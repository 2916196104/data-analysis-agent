"""Versioned full-snapshot topics, bounds and source tampering; no external models."""
from copy import deepcopy
from contextlib import closing
import csv
import unittest
from unittest.mock import patch

import test_review_dimension_content as fixture
from data_analysis_agent.agents import review_evidence_loop, review_topic_statistics
from data_analysis_agent.agents.review_topic_statistics import build_statistics,validate_statistics,topic_request
from data_analysis_agent.agents.review_answers import read_public_answer
from data_analysis_agent.agents.review_validation import validate_report
from data_analysis_agent.reviews.analytics import get_evidence, get_topic_corpus
from data_analysis_agent.reviews.human_review import save_human_review
from data_analysis_agent.reviews.label_store import append_annotation_on_connection
from data_analysis_agent.reviews.store import connect_review_database, import_amazon_jsonl


class TopicStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.DimensionContentTests('test_scope_binding_and_simple_ranking_are_deterministic')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.scope={key:value for key,value in self.f.scope().items() if not key.startswith('baseline_')}

    def corpus(self):return get_topic_corpus(self.f.db,**self.scope)

    def assert_same_evidence_as_pages(self, corpus, scope, sentiment):
        page = get_evidence(self.f.db, **scope, sentiment=sentiment, page_size=100)
        items = list(page['items'])
        for page_number in range(2, page['pages'] + 1):
            items.extend(get_evidence(self.f.db, **scope, sentiment=sentiment, page_size=100, page=page_number)['items'])
        for item in items:
            item.pop('file_path')
        self.assertEqual((corpus['scope'], corpus['total'], corpus['total_opinions'], corpus['items']),
                         (page['scope'], page['total'], page['total_opinions'], items))

    def test_native_full_topic_query_and_history_do_not_call_models(self):
        r=self.f.run_local('按细分主题统计差评')
        self.assertEqual(r['status'],'ok',r.get('error'))
        self.assertEqual(r['validation']['status'],'passed')
        self.assertEqual(r['total_model_calls'],0)
        d=r['results']['r1']['data']
        self.assertTrue(validate_statistics(d))
        self.assertEqual(d['coverage']['processed_opinions'],12)
        self.assertEqual(read_public_answer({'status':'succeeded','result':r}),r['public_answer'])
        self.assertIn('未判定',str(r['public_answer']))

    def test_synonyms_merge_and_counts_deduplicate_with_unknown_upper_bounds(self):
        corpus=self.corpus()
        items=corpus['items']
        # Replace synthetic opinions with explicit cooling equivalents, keeping source spans.
        for item,text in zip(items,['制冷不足；制冷不够给力','降温不给力；未知体验']):
            item['text']=text;item['opinions']=[]
            for quote in text.split('；'):
                start=text.index(quote)
                item['opinions'].append(dict(aspect='effectiveness',sentiment='negative',quote=quote,
                                            quote_start=start,quote_end=start+len(quote)))
        corpus['total_opinions']=4
        data=build_statistics(corpus,topic='cooling_poor')
        row=data['topics'][0]
        self.assertEqual((row['review_count'],row['opinion_count']),(2,3))
        self.assertEqual(row['rate'],.5)
        self.assertEqual(data['coverage']['unresolved_opinions'],1)
        self.assertEqual(data['coverage']['classification_coverage'],.75)
        self.assertTrue(validate_statistics(data))

    def test_unlabelled_and_unresolved_are_never_silently_negative(self):
        corpus=self.corpus()
        data=build_statistics(corpus,topic='cooling_poor')
        self.assertEqual(data['topics'][0]['review_count'],0)
        self.assertEqual(data['topics'][0]['rate_state'],'lower_bound')
        self.assertGreater(data['topics'][0]['upper_rate'],0)
        corpus['summary']['review_count']+=1
        data=build_statistics(corpus,topic='cooling_poor')
        self.assertEqual(data['coverage']['unlabelled_reviews'],1)
        self.assertGreater(data['topics'][0]['upper_rate'],0)

    def test_version_counts_coverage_spans_and_scope_tampering_rejected(self):
        data=build_statistics(self.corpus())
        for mutate in (lambda d:d.update(version='wrong'),lambda d:d['coverage'].update(classified_opinions=999),
                       lambda d:d['topics'][0].update(review_count=999),
                       lambda d:d['corpus']['items'][0]['opinions'][0].update(quote_start=999),
                       lambda d:d['corpus']['items'][0].update(product_id='outside')):
            forged=deepcopy(data);mutate(forged)
            self.assertFalse(validate_statistics(forged))

    def test_topic_validation_reuses_only_an_exact_payload_with_the_same_rules(self):
        data = build_statistics(self.corpus())
        verified = set()
        with patch.object(review_topic_statistics, 'build_statistics', wraps=build_statistics) as rebuild:
            self.assertTrue(validate_statistics(data, verified_payloads=verified))
            self.assertTrue(validate_statistics(deepcopy(data), verified_payloads=verified))
            self.assertEqual(rebuild.call_count, 1)
            with patch.object(review_topic_statistics, 'RULE_DIGEST', 'different-rule-digest'):
                self.assertFalse(validate_statistics(data, verified_payloads=verified))
            self.assertEqual(rebuild.call_count, 2)
            self.assertEqual(len(verified), 1)

    def test_task_rejects_changed_theme_or_evidence_after_initial_validation(self):
        complete = review_evidence_loop.complete_evidence
        for field, mutate in (
            ('topic_count', lambda row: row['data']['topics'][0].update(review_count=999)),
            ('evidence_ref', lambda row: row['data']['items'][0].update(evidence_ref='incorrect-source')),
            ('query_denominator', lambda row: row['parameters'].update(denominator='successful_reviews')),
        ):
            def change_after_validation(db, result, plan, context, *args):
                plan = complete(db, result, plan, context, *args)
                self.assertEqual(result['validation']['status'], 'passed')
                mutate(context.results['r1'])
                return plan

            with self.subTest(field=field), patch.object(
                review_evidence_loop, 'complete_evidence', side_effect=change_after_validation,
            ):
                result = self.f.run_local('按细分主题统计差评')
                self.assertEqual(result['status'], 'error')
                self.assertFalse(result['task_acceptance']['complete'])
                checks = {row['name']: row['passed'] for row in result['task_acceptance']['checks']}
                self.assertFalse(checks['统计与引用校验'])
                self.assertFalse(result['public_answer']['evidence_refs'])
                self.assertEqual(result['public_analysis']['status'], 'failed')

    def test_history_revalidates_topic_and_source_even_after_success(self):
        result = self.f.run_local('按细分主题统计差评')
        self.assertEqual(result['status'], 'ok')
        result['results']['r1']['data']['items'][0]['opinions'][0]['quote_end'] += 1
        answer = read_public_answer({'status': 'succeeded', 'result': result})
        self.assertEqual(answer['status'], 'failed')
        self.assertFalse(answer['evidence_refs'])

    def test_narrow_failure_and_intact_packaging_are_not_broadened_to_parent_themes(self):
        corpus=self.corpus()
        for item,text in zip(corpus['items'],['制冷不足','完全不制冷']):
            item['text']=text
            item['opinions']=[dict(aspect='effectiveness',sentiment='negative',quote=text,quote_start=0,quote_end=len(text))]
        corpus['total_opinions']=2
        self.assertEqual(build_statistics(corpus,topic='cooling_poor')['topics'][0]['review_count'],2)
        self.assertEqual(build_statistics(corpus,topic='cooling_failure')['topics'][0]['review_count'],1)
        self.assertEqual(topic_request('完全不制冷的反馈占全部评论多少？')['topic'],'cooling_failure')
        self.assertIsNone(topic_request('客服不耐烦的反馈占全部评论多少？'))
        from data_analysis_agent.agents.review_topic_statistics import fact
        self.assertEqual(fact('但是包装很结实'),'packaging_sturdy')
        self.assertEqual(fact('包装完好'),'packaging_good')

    def test_structured_topic_followups_keep_version_scope_and_denominator(self):
        first=self.f.run_local('按细分主题统计物流差评')
        self.assertEqual(first['status'],'ok',first.get('error'))
        context={'turns_read':1,'user':{'question':first['question']},'users':[{'question':first['question']}],
            'validated':{'scope':first['scope'],'sentiment':'negative','analysis_type':'topic_statistics',
                         'queries':[{'kind':'topic_statistics','parameters':first['results']['r1']['parameters']}]}}
        for question,aspect,sentiment in [('那好评呢','logistics','positive'),('那质量呢','quality','negative')]:
            follow=self.f.run_local(question,context=context)
            self.assertEqual(follow['status'],'ok',follow.get('error'))
            data=follow['results']['r1']['data']
            self.assertEqual((data['aspect'],data['sentiment'],data['denominator']),(aspect,sentiment,'review_count'))
            self.assertEqual(data['version'],first['results']['r1']['data']['version'])
            self.assertEqual(follow['scope'],first['scope'])

    def test_ranking_attaches_full_statistics_not_just_two_examples(self):
        r=self.f.run_local('差评主要集中在哪里？')
        self.assertEqual(r['status'],'ok',r.get('error'))
        d=r['results']['r1']['data']['topic_statistics']
        self.assertEqual(d['coverage']['processed_opinions'],12)
        self.assertIn('固定版本规则',str(r['public_answer']))

    def test_explicit_topic_rate_uses_all_reviews_and_cannot_become_causal(self):
        r=self.f.run_local('制冷不足的反馈占全部评论多少？')
        self.assertEqual(r['status'],'ok',r.get('error'))
        data=r['results']['r1']['data']
        self.assertEqual(data['topics'][0]['denominator'],4)
        self.assertIn('至少',str(r['public_answer']))
        self.assertIsNone(topic_request('为什么制冷不足，主要抱怨什么原因？'))
        self.assertIsNone(topic_request('按月查看细分主题趋势'))
        self.assertIsNone(topic_request('查看细分主题和数据质量'))

    def test_full_sql_corpus_matches_all_pages_beyond_200_reviews(self):
        source = self.f.root / 'topic-many.csv'
        with source.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(('product_id', 'user_id', 'published_at', 'text'))
            writer.writerows(('DIM-C', f'synthetic-{index}', '2023-02-11', '制冷不足。包装破损')
                             for index in range(205))
        import_amazon_jsonl(self.f.db, reviews_path=source, dataset_version='reviews-csv')
        identity = {key: self.scope[key] for key in ('model', 'base_url', 'prompt_version')}
        opinions = [dict(aspect='packaging', sentiment='negative', quote='包装破损', quote_start=5, quote_end=9),
                    dict(aspect='effectiveness', sentiment='negative', quote='制冷不足', quote_start=0, quote_end=4)]
        with closing(connect_review_database(self.f.db)) as connection, connection:
            connection.execute('BEGIN')
            rows = connection.execute("SELECT id, text, content_sha256 FROM reviews WHERE product_id='DIM-C'").fetchall()
            for row in rows:
                append_annotation_on_connection(connection, row, **identity, status='success', opinions=opinions)

        scope = {**self.scope, 'product_ids': ['DIM-C']}
        statements = []
        with closing(connect_review_database(self.f.db, readonly=True)) as connection, connection:
            connection.execute('BEGIN')
            connection.set_trace_callback(statements.append)
            scanned = get_topic_corpus(connection, **scope)
        data_reads = [statement for statement in statements if statement.lstrip().startswith('WITH attempts AS')]
        self.assertEqual(len(data_reads), 2)
        self.assertTrue(all('OFFSET' not in statement.upper().split() for statement in data_reads))
        self.assert_same_evidence_as_pages(scanned, scope, 'negative')
        self.assertEqual((scanned['total'], scanned['total_opinions']), (205, 410))
        self.assertEqual([item['review_id'] for item in scanned['items']], sorted((row['id'] for row in rows), reverse=True))
        self.assertEqual([opinion['quote'] for opinion in scanned['items'][0]['opinions']], ['制冷不足', '包装破损'])
        data = build_statistics(scanned, topic='cooling_poor')
        self.assertEqual(data['coverage']['processed_opinions'], 410)
        self.assertEqual((data['topics'][0]['review_count'], data['topics'][0]['rate_state']), (205, 'exact'))

    def test_latest_human_revision_replaces_model_and_empty_revision_does_not_fall_back(self):
        original, removed = self.corpus()['items']
        revision = save_human_review(self.f.db, original['review_id'], annotation_id=original['annotation_id'],
                                     expected_revision_id=None, action='corrected', reviewer='SIMULATED',
                                     opinions=[dict(aspect='service', sentiment='positive', quote='客服回复慢')])
        latest = save_human_review(self.f.db, original['review_id'], annotation_id=original['annotation_id'],
                                   expected_revision_id=revision['revision_id'], action='corrected', reviewer='SIMULATED',
                                   opinions=[dict(aspect='service', sentiment='negative', quote='客服回复慢')])
        save_human_review(self.f.db, removed['review_id'], annotation_id=removed['annotation_id'],
                          expected_revision_id=None, action='corrected', reviewer='SIMULATED', opinions=[])

        negative = self.corpus()
        self.assert_same_evidence_as_pages(negative, self.scope, 'negative')
        self.assertEqual((negative['total'], negative['total_opinions']), (1, 1))
        self.assertEqual(negative['summary']['successful_reviews'], 4)
        item = negative['items'][0]
        self.assertEqual((item['review_id'], item['revision_id'], item['opinion_source']),
                         (original['review_id'], latest['revision_id'], 'human'))
        self.assertEqual(item['evidence_ref'], original['evidence_ref'])
        opinion = item['opinions'][0]
        self.assertEqual(item['text'][opinion['quote_start']:opinion['quote_end']], '客服回复慢')
        positive = get_topic_corpus(self.f.db, **self.scope, sentiment='positive')
        self.assert_same_evidence_as_pages(positive, self.scope, 'positive')
        self.assertEqual((positive['total'], positive['total_opinions']), (2, 12))

    def test_empty_window_positive_filter_and_explicit_denominator(self):
        empty=self.f.run_local('按细分主题统计差评',scope=self.f.scope(date_from='2030-01-01',date_to='2030-02-01'))
        self.assertEqual(empty['status'],'ok',empty.get('error'))
        self.assertIsNone(empty['results']['r1']['data']['topics'][0]['rate'])
        positive=self.f.run_local('按细分主题统计好评，以成功标注评论为分母')
        self.assertEqual(positive['status'],'ok',positive.get('error'))
        data=positive['results']['r1']['data']
        self.assertEqual(data['sentiment'],'positive')
        self.assertTrue(all(row['denominator_name']=='successful_reviews' for row in data['topics']))


if __name__=='__main__':unittest.main()
