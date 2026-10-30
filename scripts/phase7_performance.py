"""9600 条合成 PostgreSQL 评论的串行查询测量；供应商延迟单独评测。"""
import argparse
from contextlib import redirect_stdout
import io
import json
import math
import os
from pathlib import Path
import platform
from statistics import median
from time import perf_counter
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit
import uuid

import psycopg
from psycopg import sql

from scripts.frontend_fixture import create_fixture
from scripts.evaluate_review_agent import SCOPE, business_probe
from scripts.phase7_cases import confirmed
from scripts.phase7_evaluation import offline_client
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.core.config import AgentSettings, read_settings
from data_analysis_agent.reviews import analytics


def distribution(values):
    assert len(values) >= 50, 'P95 需要至少 50 个样本'
    return {'samples': len(values), 'p50_ms': round(median(values), 3),
            'p95_ms': round(sorted(values)[math.ceil(len(values) * .95) - 1], 3), 'raw_ms': values}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    admin = os.environ['TEST_POSTGRES_ADMIN_URL']
    name = 'agent_test_phase7_perf_' + uuid.uuid4().hex
    db = urlunsplit(urlsplit(admin)._replace(path='/' + name))
    with psycopg.connect(admin, autocommit=True) as con:
        con.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(name)))
    measured = {}
    try:
        create_fixture(args.output / 'fixture', db)
        # 复制本次合成夹具而非业务数据。100 个副本分散到 24 个月，保持正文与观点对应。
        with psycopg.connect(db) as con:
            con.execute("""INSERT INTO reviews(source,dataset_version,source_record_id,product_id,asin,user_id,title,text,has_text,rating,published_at,fetched_at,raw_json,content_sha256,import_run_id,line_number)
              SELECT source,dataset_version,source_record_id||'-scale-'||g,product_id,asin,user_id||'-scale-'||g,title,text,has_text,rating,
              to_char(published_at::timestamptz + ((g % 24)||' months')::interval,'YYYY-MM-DD\"T\"HH24:MI:SS.MS')||'+00:00',fetched_at,raw_json,content_sha256,import_run_id,line_number
              FROM reviews CROSS JOIN generate_series(1,99) g""")
            con.execute("""INSERT INTO review_annotations(review_id,content_sha256,requested_model,base_url,prompt_version,model_version,status,elapsed_ms,created_at)
              SELECT r.id,r.content_sha256,a.requested_model,a.base_url,a.prompt_version,a.model_version,a.status,0,a.created_at
              FROM reviews r JOIN reviews original ON original.source_record_id=split_part(r.source_record_id,'-scale-',1)
              JOIN review_annotations a ON a.review_id=original.id WHERE r.source_record_id LIKE '%%-scale-%%'""")
            con.execute("""INSERT INTO review_opinions(annotation_id,aspect,sentiment,quote,quote_start,quote_end)
              SELECT a.id,o.aspect,o.sentiment,o.quote,o.quote_start,o.quote_end FROM review_annotations a
              JOIN reviews r ON r.id=a.review_id JOIN reviews original ON original.source_record_id=split_part(r.source_record_id,'-scale-',1)
              JOIN review_annotations old ON old.review_id=original.id JOIN review_opinions o ON o.annotation_id=old.id
              WHERE r.source_record_id LIKE '%%-scale-%%'""")
            con.execute('ANALYZE')
            count = con.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]
            assert count == 9600, count
        scope = {**SCOPE, 'date_from': '2023-01-01', 'date_to': '2024-12-31', 'baseline_from': '2022-01-01', 'baseline_to': '2022-12-31'}
        current = {k: v for k, v in scope.items() if not k.startswith('baseline_')}
        sql_scenarios = [('positive', analytics.get_positive_aspects, {**current, 'product_ids': ['DEMO-A']}),
            ('negative', analytics.get_top_issues, {**scope, 'product_ids': ['DEMO-A']}),
            ('two_monthly_aspects', analytics.get_aspect_trend, {**current, 'period': 'month'}),
            ('comparison', analytics.compare_products, current),
            ('evidence_page_1', analytics.get_evidence, {**current, 'aspect': 'packaging', 'sentiment': 'negative', 'page': 1, 'page_size': 3}),
            ('evidence_page_2', analytics.get_evidence, {**current, 'aspect': 'packaging', 'sentiment': 'negative', 'page': 2, 'page_size': 3})]
        for label, function, params in sql_scenarios:
            values = []
            for _ in range(50):
                start = perf_counter(); function(db, **params); values.append(round((perf_counter() - start) * 1000, 3))
            measured['sql_' + label] = distribution(values)
            print('sql', label, measured['sql_' + label]['p50_ms'], flush=True)
        settings = AgentSettings('offline-phase7', 'not-a-real-key', 'https://offline.invalid/v1', max_steps=3, max_retries=0)
        tasks = [('positive', confirmed('get_positive_aspects'), []), ('negative', confirmed('get_top_issues'), []),
                 ('logistics_followup', confirmed('get_top_issues', aspect='logistics'), []),
                 ('two_trends', '按月查看包装和物流问题变化', [('get_aspect_trend', {'aspect': 'packaging', 'period': 'month'}), ('get_aspect_trend', {'aspect': 'logistics', 'period': 'month'})]),
                 ('comparison', confirmed('compare_products'), []),
                 ('evidence_page', confirmed('get_evidence', aspect='packaging', sentiment='negative', page=2, page_size=3, window='current'), [])]
        for label, question, expected in tasks:
            runs = []
            for _ in range(50):
                with patch('openai.OpenAI', side_effect=offline_client({'expected': expected})), redirect_stdout(io.StringIO()):
                    result = run_review_agent(db, question, scope=scope, settings=settings)
                assert result['status'] == 'ok', result.get('error')
                runs.append({**result['performance'], **{k: result[k] for k in ('model_calls', 'query_executions', 'cache_hits', 'answer_model_calls', 'model_tool_attempts')}})
            measured['task_' + label] = {**distribution([r['total_duration_ms'] for r in runs]), 'runs': runs,
                'note': '真实 PostgreSQL + 模拟模型；不包含 API 排队、浏览器或供应商延迟'}
            print('task', label, measured['task_' + label]['p50_ms'], flush=True)
        result = {'source': 'isolated synthetic PostgreSQL, sequential warm-cache measurements', 'reviews': count, 'products': 2, 'months': 24,
                  'environment': {'os': platform.platform(), 'python': platform.python_version(), 'cpu_threads': os.cpu_count()},
                  'measurements': measured, 'baseline': '没有同环境改造前基线；不宣称相对加速或并发容量'}
        values = read_settings(Path('.env'))
        if values.get('DATABASE_URL'):
            result['business_readonly'] = business_probe(values['DATABASE_URL'])
        (args.output / 'performance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        with psycopg.connect(admin, autocommit=True) as con:
            con.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


if __name__ == '__main__':
    main()
