"""Temporary DB and real loopback HTTP checks; no model calls or business writes."""

import hashlib
import http.client
import io
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing, redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from data_analysis_agent.review_label_store import init_labeling, pending_reviews, save_annotation
from data_analysis_agent.review_labeler import validate_opinions
from data_analysis_agent.review_loader import ReviewDataError
from data_analysis_agent.review_store import init_database, import_amazon_jsonl
from data_analysis_agent.review_workbench import create_server, main
from data_analysis_agent.review_workbench_store import (
    ReviewConflict, ReviewNotFound, init_workbench, list_reviews, review_detail,
    save_human_review, workbench_options,
)

IDENTITY = {"model": "SIMULATED-model-A", "base_url": "https://offline.invalid/v1", "prompt_version": "prompt-A"}


def opinion(aspect="effectiveness", sentiment="positive", quote="好用"):
    return {"aspect": aspect, "sentiment": sentiment, "quote": quote}


class WorkbenchFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = self.root / "reviews.sqlite3"
        init_database(self.db)
        source = self.root / "reviews.jsonl"
        bodies = ["好用，包装破了，客服回复慢。", "质量很好，价格合适，物流很快。", "只有一般描述", "", "尚未使用",
                  "🎉好用\n包装破了<script>alert(1)</script>"]
        dates = ["2023-01-01T00:00:00+00:00", "2023-01-02T23:59:59.999+00:00", "2023-01-03T00:00:00+00:00",
                 "2023-01-04T00:00:00+00:00", "2023-01-05T00:00:00+00:00", "2023-01-02T12:00:00+00:00"]
        self.records = [{"parent_asin": product, "user_id": str(index), "text": text, "rating": 3,
                         "timestamp": int(datetime.fromisoformat(day).timestamp()*1000)}
                        for index, (product,text,day) in enumerate(zip(["P1","P2","P1","P1","P2","P3"],bodies,dates))]
        source.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in self.records), encoding="utf-8")
        meta = self.root / "meta.jsonl"
        meta.write_text("\n".join(json.dumps({"parent_asin":p,"title":"SIMULATED 商品 " + p}) for p in ("P1","P2","P3")), encoding="utf-8")
        import_amazon_jsonl(self.db, reviews_path=source, meta_path=meta)
        init_labeling(self.db)
        inputs = {r["id"]:r for r in pending_reviews(self.db, **IDENTITY, limit=10)}
        outputs = {1:[opinion(),opinion("packaging","negative","包装破了"),opinion("service","negative","客服回复慢")],
                   2:[opinion("quality","positive","质量很好"),opinion("price","positive","价格合适"),opinion("logistics","positive","物流很快")],
                   6:[opinion()]}
        self.annotations = {}
        for rid, values in outputs.items():
            self.annotations[rid] = save_annotation(self.db, inputs[rid], **IDENTITY, status="success",
                                                    opinions=validate_opinions(json.dumps({"opinions":values}),inputs[rid]["text"]))
        save_annotation(self.db, inputs[3], **IDENTITY, status="failed", error_code="invalid_output")
        self.profile = self.annotations[1]
        init_workbench(self.db)

    def browse(self, **kwargs):
        return list_reviews(self.db, profile_id=self.profile, **kwargs)

    def save(self, rid=1, **kwargs):
        return save_human_review(self.db, rid, annotation_id=self.annotations[rid],
                                 reviewer="测试复核员", action="corrected", **kwargs)

    def model_snapshot(self):
        with closing(sqlite3.connect(self.db)) as con:
            return [con.execute("SELECT * FROM " + table + " ORDER BY id").fetchall()
                    for table in ("reviews", "review_annotations", "review_opinions")]


class WorkbenchStoreTests(WorkbenchFixture, unittest.TestCase):
    def test_explicit_idempotent_setup_and_legacy_schemas_unchanged(self):
        other = self.root / "other.sqlite3"
        init_database(other)
        with self.assertRaisesRegex(ReviewDataError,"观点表未初始化"):
            init_workbench(other)
        init_labeling(other)
        digest = hashlib.sha256(other.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ReviewDataError,"工作台表未初始化"):
            create_server(other)
        self.assertEqual(digest,hashlib.sha256(other.read_bytes()).hexdigest())
        self.assertTrue(init_workbench(other)["created"])
        self.assertFalse(init_workbench(other)["created"])
        with closing(sqlite3.connect(other)) as con:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0],1)
            self.assertEqual(con.execute("SELECT version FROM review_labeling_schema").fetchone()[0],1)
        self.assertEqual(workbench_options(other)["profiles"],[])
        self.assertEqual(list_reviews(other)["total"],0)
        self.assertEqual(list_reviews(other)["pages"],1)

    def test_date_boundaries_stable_pagination_and_product_scope(self):
        self.assertEqual([r["id"] for r in self.browse(date_from="2023-01-02",date_to="2023-01-02")["items"]],[2,6])
        first = self.browse(page_size=2)
        self.assertEqual((first["total"],first["pages"]),(6,3))
        ids = [r["id"] for page in (1,2,3) for r in self.browse(page=page,page_size=2)["items"]]
        self.assertEqual(ids,[5,4,3,2,6,1])
        self.assertEqual(self.browse(page=999,page_size=2)["page"],3)
        product = json.dumps(["amazon_reviews_2023","amazon-reviews-2023","P1"])
        self.assertEqual(self.browse(product_key=product)["total"],3)
        source = self.root / "version.jsonl"
        source.write_text(json.dumps(self.records[0]),encoding="utf-8")
        import_amazon_jsonl(self.db,reviews_path=source,dataset_version="another-version")
        self.assertEqual(self.browse(product_key=product)["total"],3)
        self.assertEqual(self.browse()["total"],7)

    def test_dimension_and_sentiment_match_same_opinion_without_duplicates(self):
        self.assertEqual(self.browse(aspect="effectiveness",sentiment="negative")["total"],0)
        self.assertEqual([r["id"] for r in self.browse(aspect="packaging",sentiment="negative")["items"]],[1])
        self.assertEqual(self.browse(sentiment="negative")["total"],1)
        self.assertEqual(self.browse(processing_status="success")["total"],3)
        self.assertEqual(self.browse(processing_status="failed")["items"][0]["error_code"],"invalid_output")
        self.assertEqual(self.browse(processing_status="pending")["total"],1)
        self.assertEqual(self.browse(processing_status="empty")["total"],1)
        combined = self.browse(product_key=json.dumps(["amazon_reviews_2023","amazon-reviews-2023","P1"]),
                               date_from="2023-01-01",date_to="2023-01-01",aspect="packaging",sentiment="negative",
                               processing_status="success",human_status="unreviewed",page_size=1)
        self.assertEqual([r["id"] for r in combined["items"]],[1])

    def test_confirm_correction_and_empty_snapshot_preserve_model_and_history(self):
        before = self.model_snapshot()
        saved = save_human_review(self.db,1,annotation_id=self.annotations[1],expected_revision_id=None,
                                  action="confirmed",reviewer="甲")
        self.assertEqual(self.browse(human_status="confirmed")["total"],1)
        second = self.save(expected_revision_id=saved["revision_id"],opinions=[opinion("quality","negative","包装破了")],note="重分维度")
        self.assertEqual(self.browse(aspect="packaging")["total"],0)
        self.assertEqual(self.browse(aspect="quality",sentiment="negative")["total"],1)
        third = self.save(expected_revision_id=second["revision_id"],opinions=[])
        detail = review_detail(self.db,1,self.profile)
        self.assertEqual(detail["effective_opinions"],[])
        self.assertEqual(len(detail["annotation"]["opinions"]),3)
        self.assertEqual(len(detail["human_reviews"]),3)
        self.assertEqual(detail["human_reviews"][0]["previous_id"],second["revision_id"])
        self.assertEqual(detail["review"]["revision_id"],third["revision_id"])
        self.assertEqual(self.browse(aspect="quality",sentiment="negative")["total"],0)
        self.assertEqual(self.model_snapshot(),before)
        restored = save_human_review(self.db,1,annotation_id=self.annotations[1],expected_revision_id=third["revision_id"],action="confirmed",reviewer="乙")
        self.assertEqual(restored["opinion_count"],3)

    def test_reject_fabricated_invalid_and_stale_edits_atomically(self):
        before = self.model_snapshot()
        for values in ([opinion(quote="改写的引用")],[opinion(),opinion(quote="不存在")],
                       [opinion(aspect="invalid")],[{**opinion(),"quote_start":0}]):
            with self.assertRaises(ReviewDataError):
                self.save(expected_revision_id=None,opinions=values)
        for kwargs in ({"reviewer":" "},{"note":"\ud800"},{"expected_revision_id":True}):
            values = {"annotation_id":self.annotations[1],"action":"corrected","reviewer":"甲", "expected_revision_id":None,"opinions":[]}
            values.update(kwargs)
            with self.assertRaises(ReviewDataError):
                save_human_review(self.db,1,**values)
        with self.assertRaises(ReviewDataError):
            save_human_review(self.db,1,annotation_id=self.annotations[1],action="confirmed",reviewer="甲",expected_revision_id=None,opinions=[])
        self.assertEqual(review_detail(self.db,1,self.profile)["human_reviews"],[])
        self.save(expected_revision_id=None,opinions=[])
        with self.assertRaises(ReviewConflict):
            self.save(expected_revision_id=None,opinions=[])
        with self.assertRaises(ReviewNotFound):
            save_human_review(self.db,2,annotation_id=self.annotations[1],expected_revision_id=None,action="confirmed",reviewer="甲")
        with self.assertRaises(ReviewConflict):
            save_human_review(self.db,3,annotation_id=4,expected_revision_id=None,action="confirmed",reviewer="甲")
        self.assertEqual(self.model_snapshot(),before)

    def test_unicode_quote_positions_and_source_evidence(self):
        self.save(rid=6,expected_revision_id=None,opinions=[opinion(quote="🎉好用\n包装破了")])
        detail = review_detail(self.db,6,self.profile)
        p = detail["effective_opinions"][0]
        self.assertEqual(detail["review"]["text"][p["quote_start"]:p["quote_end"]],p["quote"])
        self.assertEqual(detail["review"]["line_number"],6)
        self.assertIn("#line=6",detail["review"]["evidence_ref"])
        self.assertNotIn("user_id",detail["review"])

    def test_simultaneous_submissions_commit_only_one_revision(self):
        start = threading.Barrier(2)

        def submit():
            start.wait(timeout=5)
            try:
                self.save(expected_revision_id=None,opinions=[])
                return "saved"
            except ReviewConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _:submit(),range(2)))
        self.assertCountEqual(results,["saved","conflict"])
        self.assertEqual(len(review_detail(self.db,1,self.profile)["human_reviews"]),1)

    def test_profiles_and_changed_source_content_never_reuse_human_results(self):
        self.save(expected_revision_id=None,opinions=[])
        other = {**IDENTITY,"model":"SIMULATED-model-B"}
        review = pending_reviews(self.db,**other,limit=10)[0]
        alternate = save_annotation(self.db,review,**other,status="success",opinions=[])
        self.assertEqual(workbench_options(self.db)["selected_profile_id"],alternate)
        self.assertEqual(list_reviews(self.db,profile_id=alternate,human_status="corrected")["total"],0)
        self.assertEqual(list_reviews(self.db,profile_id=alternate,processing_status="success")["total"],1)
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("UPDATE reviews SET content_sha256='new-version' WHERE id=1")
            con.commit()
        self.assertEqual(self.browse(human_status="corrected")["total"],0)
        self.assertEqual(review_detail(self.db,1,self.profile)["review"]["processing_status"],"pending")
        with self.assertRaises(ReviewConflict):
            self.save(expected_revision_id=1,opinions=[])

    def test_product_progress_counts_unique_reviews_and_skips_empty_denominator(self):
        self.save(expected_revision_id=None,opinions=[])
        products = {p["product_id"]:p for p in workbench_options(self.db,self.profile)["products"]}
        p1=products["P1"]
        self.assertEqual((p1["review_count"],p1["eligible_count"],p1["success_count"],p1["failed_count"],p1["empty_count"],p1["reviewed_count"]),(3,2,1,1,1,1))
        self.assertEqual(products["P2"]["pending_count"],1)
        self.assertEqual(products["P3"]["success_count"],1)

    def test_invalid_filters_and_missing_records(self):
        for kwargs in ({"page":True},{"page_size":101},{"date_from":"2023-02-30"},
                       {"date_to":"9999-12-31"},{"date_from":"2023-01-02","date_to":"2023-01-01"},
                       {"date_from":"20230101"},{"aspect":"quality' OR 1=1"},{"sentiment":"bad"},
                       {"processing_status":"done"},{"human_status":"done"},{"product_key":"[1,2,3]"}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ReviewDataError):
                self.browse(**kwargs)
        with self.assertRaises(ReviewNotFound):
            review_detail(self.db,999,self.profile)
        with self.assertRaises(ReviewNotFound):
            list_reviews(self.db,profile_id=999)

    def test_cli_explicit_setup_and_invalid_ports(self):
        out = io.StringIO()
        with patch.object(sys,"argv",["review-workbench","init","--db",str(self.db)]),redirect_stdout(out):
            self.assertEqual(main(),0)
        self.assertFalse(json.loads(out.getvalue())["created"])
        with patch.object(sys,"argv",["review-workbench","serve","--db",str(self.db),"--port","0"]),redirect_stdout(io.StringIO()),patch("sys.stderr",io.StringIO()):
            with self.assertRaises(SystemExit):
                main()


class WorkbenchHTTPTests(WorkbenchFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.server = create_server(self.db,0)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self,method,path,body=None,headers=None):
        with closing(http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=5)) as client:
            client.request(method,path,body=body,headers=headers or {})
            response = client.getresponse()
            return response.status,dict(response.getheaders()),response.read()

    def test_assets_filters_detail_and_no_cross_site_reads(self):
        for path in ("/","/workbench.js","/workbench.css"):
            code,headers,body = self.request("GET",path)
            self.assertEqual(code,200)
            self.assertEqual(headers["Cache-Control"],"no-store")
            self.assertIn("frame-ancestors 'none'",headers["Content-Security-Policy"])
            self.assertTrue(body)
        code,_,body = self.request("GET","/api/options")
        self.assertEqual(code,200)
        self.assertIn("csrf_token",json.loads(body))
        self.assertEqual(self.request("GET","/api/options",headers={"Host":"untrusted.example"})[0],403)
        self.assertEqual(self.request("GET","/api/reviews",headers={"Origin":"https://untrusted.example"})[0],403)
        code,_,body = self.request("GET",f"/api/reviews?profile_id={self.profile}&aspect=packaging&sentiment=negative&page_size=1")
        self.assertEqual((code,json.loads(body)["total"]),(200,1))
        code,_,body = self.request("GET",f"/api/reviews/1?profile_id={self.profile}")
        self.assertEqual(json.loads(body)["review"]["id"],1)
        self.assertEqual(self.request("GET","/api/reviews?page=1&page=2")[0],400)
        self.assertEqual(self.request("GET","/api/reviews?unexpected=1")[0],400)
        self.assertEqual(self.request("GET","/api/reviews/999")[0],404)
        self.assertEqual(self.request("GET","/../../.env")[0],404)

    def test_csrf_writes_conflicts_and_malformed_payloads(self):
        _,_,data = self.request("GET","/api/options")
        token = json.loads(data)["csrf_token"]
        path="/api/reviews/1/human-reviews"
        body=json.dumps({"annotation_id":self.annotations[1],"expected_revision_id":None,"action":"corrected", "reviewer":"甲","opinions":[]})
        headers={"Content-Type":"application/json","X-CSRF-Token":token}
        self.assertEqual(self.request("POST",path,body,{"Content-Type":"application/json"})[0],403)
        self.assertEqual(self.request("POST",path,body,{**headers,"X-CSRF-Token":"é"})[0],403)
        self.assertEqual(self.request("POST",path,body,{**headers,"Origin":"https://untrusted.example"})[0],403)
        self.assertEqual(self.request("POST",path,body,headers)[0],201)
        self.assertEqual(self.request("POST",path,body,headers)[0],409)
        self.assertEqual(self.request("POST",path,"{}",headers)[0],400)
        self.assertEqual(self.request("POST",path,'{"a":1,"a":2}',headers)[0],400)
        self.assertEqual(self.request("POST",path,"not json",headers)[0],400)
        self.assertEqual(self.request("POST",path,body,{**headers,"Content-Type":"text/plain"})[0],400)
        self.assertEqual(self.request("POST",path,body,{**headers,"Content-Length":"200001"})[0],400)
        with patch("data_analysis_agent.review_workbench.workbench_options",side_effect=sqlite3.OperationalError("private info")):
            code,_,response=self.request("GET","/api/options")
        self.assertEqual(code,503)
        self.assertNotIn(b"private info",response)


if __name__ == "__main__":
    unittest.main()
