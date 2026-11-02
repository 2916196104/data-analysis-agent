"""隔离 SQLite 中实际执行语料读取与人工修订，不调用外部模型。"""

from contextlib import closing
from dataclasses import asdict
from hashlib import sha256
import json
import sqlite3
import unittest
from unittest.mock import patch

from test_review_workbench import IDENTITY, WorkbenchFixture, opinion

from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.reviews.analytics import get_evidence, get_trend
from data_analysis_agent.reviews.human_review import save_human_review
from data_analysis_agent.reviews.label_store import query_review_opinions, save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions
from data_analysis_agent.reviews.rag_corpus import RagCorpusIntegrityError, read_effective_corpus
from data_analysis_agent.reviews.scope import current_reviews_sql, review_scope
from data_analysis_agent.reviews.store import connect_review_database, import_amazon_jsonl


class RagCorpusTests(WorkbenchFixture, unittest.TestCase):
    def options(self, **changes):
        return {**IDENTITY, "source": "amazon_reviews_2023", "dataset_version": "amazon-reviews-2023",
                "date_from": "2023-01-01", "date_to": "2023-01-06", **changes}

    def corpus(self, db=None, **changes):
        return read_effective_corpus(self.db if db is None else db, **{**self.options(), **changes})

    def review_input(self, rid):
        with closing(connect_review_database(self.db, readonly=True)) as connection:
            return dict(connection.execute("SELECT id,text,content_sha256 FROM reviews WHERE id=?", (rid,)).fetchone())

    def label(self, rid, values, identity=None):
        review = self.review_input(rid)
        return save_annotation(self.db, review, **(identity or IDENTITY), status="success",
                               opinions=validate_opinions(json.dumps({"opinions": values}), review["text"]))

    def assert_sql_count(self, corpus, **changes):
        options = {**self.options(), **changes}
        where, filters, _ = review_scope(**{key: options[key] for key in (
            "source", "dataset_version", "date_from", "date_to", "product_ids", "as_of"
        ) if key in options}, alias="c")
        identity = tuple(options[key] for key in ("model", "base_url", "prompt_version"))
        with closing(connect_review_database(self.db, readonly=True)) as connection:
            cte, parameters = current_reviews_sql(connection, identity)
            count = connection.execute(cte + "SELECT COUNT(*) FROM current c "
                "JOIN effective_opinions o ON o.review_id=c.id WHERE " + where,
                (*parameters, *filters)).fetchone()[0]
        self.assertEqual(len(corpus.documents), count)
        self.assertEqual(sum(row.opinion_count for row in corpus.reviews), count)

    def test_multiple_opinions_keep_real_source_ids_dimensions_and_sentiments(self):
        corpus = self.corpus()
        self.assert_sql_count(corpus)
        docs = [row for row in corpus.documents if row.review_id == 1]
        self.assertEqual(len(docs), 3)
        self.assertCountEqual([(row.aspect, row.sentiment) for row in docs], [
            ("effectiveness", "positive"), ("packaging", "negative"), ("service", "negative")])
        with closing(connect_review_database(self.db, readonly=True)) as connection:
            for row in corpus.documents:
                original = connection.execute("SELECT * FROM review_opinions WHERE id=?", (row.model_opinion_id,)).fetchone()
                self.assertEqual(original["annotation_id"], row.annotation_id)
                self.assertEqual(original["quote"], row.quote)
                self.assertEqual(self.review_input(row.review_id)["text"][row.quote_start:row.quote_end], row.quote)
                self.assertEqual((row.opinion_source, row.human_opinion_id, row.human_revision_id), ("model", None, None))
                self.assertEqual((row.requested_model, row.base_url, row.prompt_version), tuple(IDENTITY.values()))
                self.assertEqual(row.embedding_input, row.quote)
                self.assertEqual(row.evidence_ref, f"sha256:{row.file_sha256}#line={row.line_number}")
                self.assertTrue({"user_id", "review_text", "file_path", "raw_json"}.isdisjoint(asdict(row)))

    def test_model_endpoint_and_prompt_configs_never_mix(self):
        baseline = self.corpus()
        for field, value in (("model", "SIMULATED-B"), ("base_url", "https://other.invalid/v1"),
                             ("prompt_version", "prompt-B")):
            with self.subTest(field=field):
                identity = {**IDENTITY, field: value}
                aid = self.label(1, [opinion()], identity)
                alternate = self.corpus(**identity)
                self.assertEqual(len(alternate.documents), 1)
                self.assertEqual(alternate.documents[0].annotation_id, aid)
                self.assertNotEqual(baseline.label_profile_key, alternate.label_profile_key)
                self.assertNotEqual(baseline.documents[0].stable_versioned_opinion_key,
                                    alternate.documents[0].stable_versioned_opinion_key)
                self.assert_sql_count(alternate, **identity)
        self.assertEqual(self.corpus(), baseline)

    def test_success_wins_even_when_a_later_failure_exists(self):
        aid = self.label(3, [])
        # 专门构造成功之后仍残留失败尝试的隔离数据，验证不是 MAX(annotation_id)。
        with closing(connect_review_database(self.db)) as connection, connection:
            connection.execute("INSERT INTO review_annotations "
                "(review_id,content_sha256,requested_model,base_url,prompt_version,status,error_code,elapsed_ms,created_at) "
                "VALUES (?,?,?,?,?,'failed','model_timeout',0,'2023-02-01')",
                (3, self.review_input(3)["content_sha256"], *IDENTITY.values()))
        result = self.corpus()
        state = next(row for row in result.reviews if row.review_id == 3)
        self.assertEqual((state.processing_status, state.annotation_id, state.opinion_count), ("success", aid, 0))
        self.assertIsNone(state.error_code)
        self.assert_sql_count(result)

    def test_successful_empty_failed_pending_and_empty_body_are_distinct(self):
        self.label(5, [])
        result = self.corpus()
        states = {row.review_id: row for row in result.reviews}
        self.assertEqual((states[5].processing_status, states[5].opinion_source, states[5].opinion_count), ("success", "model", 0))
        self.assertEqual(states[3].processing_status, "failed")
        self.assertEqual(states[4].processing_status, "empty")
        pending = self.corpus(model="UNLABELED")
        self.assertEqual({row.processing_status for row in pending.reviews}, {"pending", "empty"})
        self.assertEqual(pending.documents, ())
        self.assertIsNotNone(states[5].annotation_id)
        self.assertIsNone(states[5].human_revision_id)
        self.assert_sql_count(result)

    def test_human_confirmation_uses_human_rows_and_preserves_text_hashes(self):
        original = self.corpus()
        before = self.model_snapshot()
        saved = save_human_review(self.db, 1, annotation_id=self.annotations[1], expected_revision_id=None,
                                  action="confirmed", reviewer="隔离测试")
        result = self.corpus()
        old = {row.quote: row for row in original.documents if row.review_id == 1}
        new = [row for row in result.documents if row.review_id == 1]
        with closing(connect_review_database(self.db, readonly=True)) as connection:
            for row in new:
                self.assertEqual((row.opinion_source, row.model_opinion_id, row.human_revision_id),
                                 ("human", None, saved["revision_id"]))
                parent = connection.execute("SELECT human_review_id FROM review_human_opinions WHERE id=?",
                                            (row.human_opinion_id,)).fetchone()[0]
                self.assertEqual(parent, saved["revision_id"])
                self.assertEqual(row.embedding_input_sha256, old[row.quote].embedding_input_sha256)
                self.assertNotEqual(row.stable_versioned_opinion_key, old[row.quote].stable_versioned_opinion_key)
        self.assertEqual(self.model_snapshot(), before)
        self.assert_sql_count(result)

    def test_human_text_correction_and_latest_revision_replace_complete_set(self):
        initial = self.corpus()
        first = self.save(expected_revision_id=None, opinions=[opinion(quote="好用")])
        previous = self.corpus()
        second = self.save(expected_revision_id=first["revision_id"], opinions=[opinion("service", "negative", "客服回复慢")])
        result = self.corpus()
        docs = [row for row in result.documents if row.review_id == 1]
        self.assertEqual(len(docs), 1)
        self.assertEqual((docs[0].quote, docs[0].human_revision_id), ("客服回复慢", second["revision_id"]))
        old = next(row for row in previous.documents if row.review_id == 1)
        self.assertNotEqual(docs[0].embedding_input_sha256, old.embedding_input_sha256)
        self.assertNotEqual(docs[0].stable_versioned_opinion_key, old.stable_versioned_opinion_key)
        self.assertNotEqual(initial.reviews[0].effective_snapshot_sha256, result.reviews[0].effective_snapshot_sha256)
        self.assert_sql_count(result)

    def test_human_dimension_and_sentiment_changes_only_invalidate_version(self):
        original = next(row for row in self.corpus().documents if row.review_id == 1 and row.quote == "好用")
        revision = None
        previous = original
        for aspect, sentiment in (("quality", "negative"), ("effectiveness", "neutral"), ("price", "unknown")):
            with self.subTest(aspect=aspect, sentiment=sentiment):
                saved = self.save(expected_revision_id=revision, opinions=[opinion(aspect, sentiment, "好用")])
                revision = saved["revision_id"]
                result = self.corpus()
                row = next(row for row in result.documents if row.review_id == 1)
                self.assertEqual((row.aspect, row.sentiment), (aspect, sentiment))
                self.assertEqual(row.embedding_input_sha256, original.embedding_input_sha256)
                self.assertNotEqual(row.stable_versioned_opinion_key, previous.stable_versioned_opinion_key)
                previous = row
                self.assert_sql_count(result)

    def test_human_empty_snapshot_is_visible_and_never_falls_back(self):
        first = self.save(expected_revision_id=None, opinions=[])
        empty = self.corpus()
        state = empty.reviews[0]
        self.assertEqual((state.processing_status, state.human_status, state.opinion_source, state.opinion_count),
                         ("success", "corrected", "human", 0))
        self.assertEqual(state.human_revision_id, first["revision_id"])
        self.assertFalse(any(row.review_id == 1 for row in empty.documents))
        second = self.save(expected_revision_id=first["revision_id"], opinions=[])
        current = self.corpus()
        self.assertEqual(current.documents, empty.documents)
        self.assertNotEqual(current.reviews[0].effective_snapshot_sha256, state.effective_snapshot_sha256)
        self.assertEqual(current.reviews[0].human_revision_id, second["revision_id"])
        self.assert_sql_count(current)

    def test_content_hash_change_excludes_old_model_and_human_versions(self):
        self.save(expected_revision_id=None, opinions=[opinion()])
        before = self.corpus()
        with closing(connect_review_database(self.db)) as connection, connection:
            connection.execute("UPDATE reviews SET content_sha256='isolated-new-content' WHERE id=1")
        pending = self.corpus()
        state = pending.reviews[0]
        self.assertEqual((state.processing_status, state.annotation_id, state.human_revision_id), ("pending", None, None))
        self.assertFalse(any(row.review_id == 1 for row in pending.documents))
        new_annotation = self.label(1, [opinion()])
        after = self.corpus()
        row = next(row for row in after.documents if row.review_id == 1)
        self.assertEqual((row.annotation_id, row.opinion_source, row.review_content_sha256),
                         (new_annotation, "model", "isolated-new-content"))
        self.assertNotEqual(row.stable_versioned_opinion_key, before.documents[0].stable_versioned_opinion_key)
        self.assert_sql_count(after)

    def test_products_sources_datasets_and_utc_dates_are_isolated(self):
        other = self.root / "other.jsonl"
        other.write_text(json.dumps(self.records[0]), encoding="utf-8")
        import_amazon_jsonl(self.db, reviews_path=other, dataset_version="other-version")
        self.label(7, [opinion()])
        csv_file = self.root / "other.csv"
        csv_file.write_text("商品ID,评论内容,评论时间\nP1,好用,2023-01-01\n", encoding="utf-8")
        import_amazon_jsonl(self.db, reviews_path=csv_file, dataset_version="amazon-reviews-2023")
        self.label(8, [opinion()])
        for changes, ids in (({"product_ids": [" P1 ", "P1"]}, {1, 3, 4}),
                             ({"dataset_version": "other-version"}, {7}),
                             ({"source": "reviews_csv"}, {8}),
                             ({"date_from": "2023-01-02", "date_to": "2023-01-02"}, {2, 6}),
                             ({"as_of": "2023-01-02T20:00:00+08:00"}, {1, 6})):
            with self.subTest(changes=changes):
                result = self.corpus(**changes)
                self.assertEqual({row.review_id for row in result.reviews}, ids)
                self.assertLessEqual({row.review_id for row in result.documents}, ids)
                self.assert_sql_count(result, **changes)
        with self.assertRaisesRegex(ReviewDataError, "不属于当前数据集"):
            self.corpus(dataset_version="other-version", product_ids=["P2"])

    def test_unicode_whitespace_offsets_and_raw_utf8_hashes_are_preserved(self):
        quote = " 🎉好用\r\né e\u0301\t"
        record = {**self.records[0], "user_id": "unicode-fixture", "text": "前缀" + quote + "后缀"}
        file = self.root / "unicode.jsonl"
        file.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        import_amazon_jsonl(self.db, reviews_path=file)
        self.label(7, [opinion(quote=quote)])
        row = next(row for row in self.corpus().documents if row.review_id == 7)
        self.assertEqual((row.quote, row.embedding_input, row.quote_start, row.quote_end), (quote, quote, 2, 2 + len(quote)))
        self.assertEqual(row.opinion_text_sha256, sha256(quote.encode("utf-8")).hexdigest())
        self.assertEqual(row.embedding_input_sha256, row.opinion_text_sha256)
        self.assertNotEqual(row.embedding_input_sha256, sha256(quote.strip().encode("utf-8")).hexdigest())
        self.assertEqual(row.text_builder_version, "quote-utf8-v1")

    def test_repeat_export_order_subset_and_readonly_file_are_stable(self):
        before = self.db.read_bytes()
        first, second = self.corpus(), self.corpus()
        self.assertEqual(first, second)
        subset = self.corpus(product_ids=["P1"])
        self.assertEqual(subset.documents, tuple(row for row in first.documents if row.product_id == "P1"))
        self.assertEqual(subset.reviews, tuple(row for row in first.reviews if row.product_id == "P1"))
        self.assertEqual(len({row.stable_versioned_opinion_key for row in first.documents}), len(first.documents))
        self.assertEqual(self.db.read_bytes(), before)

    def test_hash_contract_has_fixed_golden_values(self):
        result = self.corpus()
        row = result.documents[0]
        self.assertEqual(row.quote, "好用")
        self.assertEqual(row.opinion_text_sha256, "c5c9ba1747a15ad4c6cacdc9cd0bb94e94bd31b1cf3c8b73b93938bf8d219bb6")
        self.assertEqual(result.label_profile_key, "6b27e4e29ab093c0930d55e17d92bcd83deac9c1452c4ad60b4f70b44818d9fd")
        self.assertEqual(row.stable_versioned_opinion_key,
                         "rag-opinion-v1:8b09a1d4b00a98fa0398229260c79622d57105b21752779eba9dc5937e3b47b2")

    def test_in_place_metadata_changes_are_detected_even_if_ids_are_unchanged(self):
        before = self.corpus()
        with closing(connect_review_database(self.db)) as connection, connection:
            connection.execute("UPDATE review_opinions SET aspect='quality',sentiment='negative' WHERE id=1")
        after = self.corpus()
        old, new = before.documents[0], after.documents[0]
        self.assertEqual(new.model_opinion_id, old.model_opinion_id)
        self.assertEqual(new.embedding_input_sha256, old.embedding_input_sha256)
        self.assertNotEqual(new.stable_versioned_opinion_key, old.stable_versioned_opinion_key)
        self.assertNotEqual(after.reviews[0].effective_snapshot_sha256, before.reviews[0].effective_snapshot_sha256)

    def test_invalid_model_spans_fail_whole_export_without_leaking_quote(self):
        with closing(connect_review_database(self.db)) as connection, connection:
            connection.execute("UPDATE review_opinions SET quote='isolated-private-invalid' WHERE id=1")
        with self.assertRaises(RagCorpusIntegrityError) as caught:
            self.corpus()
        self.assertNotIn("isolated-private-invalid", str(caught.exception))

    def test_invalid_human_spans_do_not_fall_back_or_silently_drop(self):
        self.save(expected_revision_id=None, opinions=[opinion()])
        with closing(connect_review_database(self.db)) as connection, connection:
            connection.execute("UPDATE review_human_opinions SET quote_end=999 WHERE human_review_id=1")
        with self.assertRaises(RagCorpusIntegrityError):
            self.corpus()

    def test_default_cte_and_public_api_shapes_remain_unchanged(self):
        with closing(connect_review_database(self.db, readonly=True)) as connection:
            default, parameters = current_reviews_sql(connection, tuple(IDENTITY.values()))
            columns = list(connection.execute(default + "SELECT * FROM effective_opinions", parameters).fetchone().keys())
            self.assertEqual(columns, ["review_id", "aspect", "sentiment", "quote", "quote_start", "quote_end"])
        before = query_review_opinions(self.db, **IDENTITY)
        trend = get_trend(self.db, **self.options())
        self.corpus()
        self.assertEqual(query_review_opinions(self.db, **IDENTITY), before)
        self.assertEqual(get_trend(self.db, **self.options()), trend)
        public_opinion = next(item["opinions"][0] for item in before["items"] if item["opinions"])
        self.assertEqual(set(public_opinion), set(columns) - {"review_id"})
        evidence = get_evidence(self.db, **self.options(), sentiment=None)
        self.assertEqual(evidence["total_opinions"], len(self.corpus().documents))

    def test_shared_transaction_is_required_and_not_committed(self):
        with closing(connect_review_database(self.db, readonly=True)) as connection:
            with self.assertRaisesRegex(ReviewDataError, "必须已有事务"):
                self.corpus(connection)
            connection.execute("BEGIN")
            result = self.corpus(connection)
            self.assertTrue(connection.in_transaction)
            self.assertEqual(result, self.corpus())
            connection.rollback()

    def test_shared_snapshot_does_not_mix_a_concurrent_human_revision(self):
        with closing(connect_review_database(self.db)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
        with closing(connect_review_database(self.db, readonly=True)) as reader:
            reader.execute("BEGIN")
            first = self.corpus(reader)
            self.save(expected_revision_id=None, opinions=[])
            self.assertEqual(self.corpus(reader), first)
            current = self.corpus()
            self.assertFalse(any(row.review_id == 1 for row in current.documents))
            self.assertNotEqual(current.reviews[0].effective_snapshot_sha256, first.reviews[0].effective_snapshot_sha256)
            reader.rollback()

    def test_missing_human_tables_work_and_partial_schema_fails(self):
        before = self.corpus()
        with closing(connect_review_database(self.db)) as connection, connection:
            for table in ("review_human_opinions", "review_human_reviews", "review_workbench_schema"):
                connection.execute("DROP TABLE " + table)
        self.assertEqual(self.corpus(), before)
        with closing(connect_review_database(self.db)) as connection, connection:
            connection.execute("CREATE TABLE review_workbench_schema(version INTEGER)")
        with self.assertRaisesRegex(ReviewDataError, "不完整"):
            self.corpus()

    def test_invalid_scopes_fail_before_opening_database(self):
        for changes in ({"source": None}, {"dataset_version": " "}, {"model": ""},
                        {"base_url": None}, {"prompt_version": " "}, {"date_from": None},
                        {"date_to": "2023-02-30"}, {"date_from": "2023-02-01"},
                        {"product_ids": [""]}, {"as_of": "invalid"}):
            with self.subTest(changes=changes), patch("data_analysis_agent.reviews.rag_corpus._snapshot") as snapshot:
                with self.assertRaises(ReviewDataError):
                    self.corpus(**changes)
                snapshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
