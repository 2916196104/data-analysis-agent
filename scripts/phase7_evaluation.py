"""独立 PostgreSQL 上的固定集评测；--live 才发送合成数据到当前真实供应商。"""
import argparse
from collections import Counter
from contextlib import redirect_stdout
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP
import io
import json
import os
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit
import uuid

import httpx
from openai import OpenAI
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from scripts.frontend_fixture import create_fixture
from scripts.evaluate_review_agent import SCOPE, snapshot
from scripts.phase7_cases import cases
from data_analysis_agent.agents.review_plan import TOOL_KINDS, build_query_plan
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.core.config import AgentSettings, read_settings


def rate(n, d):
    return float((Decimal(n) / d).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)) if d else None


def raw_reviews(db, scope):
    """Python 独立 oracle：不复用聚合 SQL、CTE、校验器或压缩器。"""
    with psycopg.connect(db, row_factory=dict_row) as con:
        con.execute('SET TRANSACTION READ ONLY')
        reviews = con.execute('SELECT * FROM reviews').fetchall()
        annotations = con.execute('SELECT * FROM review_annotations ORDER BY id').fetchall()
        opinions = con.execute('SELECT * FROM review_opinions').fetchall()
        revisions = con.execute('SELECT * FROM review_human_reviews ORDER BY id').fetchall()
        human = con.execute('SELECT * FROM review_human_opinions').fetchall()
    rows = []
    for r in reviews:
        if any(scope.get(k) is not None and r[k] != scope[k] for k in ('source', 'dataset_version')):
            continue
        if scope.get('product_ids') and r['product_id'] not in scope['product_ids']:
            continue
        if not scope['date_from'] <= r['published_at'][:10] <= scope['date_to']:
            continue
        if scope.get('as_of') and r['published_at'][:10] > scope['as_of'][:10]:
            continue
        matched = [a for a in annotations if a['review_id'] == r['id'] and a['content_sha256'] == r['content_sha256']
                   and (a['requested_model'], a['base_url'], a['prompt_version']) == (scope['model'], scope['base_url'], scope['prompt_version'])]
        a = max(matched, key=lambda a: (a['status'] == 'success', a['id']), default=None)
        revision = next((h for h in reversed(revisions) if a and h['annotation_id'] == a['id']), None)
        status = 'empty' if not r['has_text'] else a['status'] if a else 'pending'
        ops = ([o for o in human if o['human_review_id'] == revision['id']] if revision else
               [o for o in opinions if a and o['annotation_id'] == a['id'] and status == 'success'])
        rows.append({**r, 'status': status, 'opinions': ops, 'reviewed': bool(revision)})
    return rows


def summary(rows):
    out = {'review_count': len(rows), 'eligible_text_reviews': sum(r['has_text'] for r in rows),
           'reviewed_reviews': sum(r['reviewed'] for r in rows)}
    for status, key in [('success', 'successful_reviews'), ('failed', 'failed_reviews'), ('pending', 'unprocessed_reviews'), ('empty', 'empty_text_reviews')]:
        out[key] = sum(r['status'] == status for r in rows)
    for sentiment in ('positive', 'negative'):
        out[sentiment + '_review_count'] = sum(any(o['sentiment'] == sentiment for o in r['opinions']) for r in rows)
    out['negative_rate'] = rate(out['negative_review_count'], out['successful_reviews'])
    out['positive_rate'] = rate(out['positive_review_count'], out['successful_reviews'])
    out['labeling_coverage'] = rate(out['successful_reviews'], out['eligible_text_reviews'])
    return out


def check_statistics(db, result):
    errors = []
    def equal(actual, expected, label):
        if actual != expected:
            errors.append(f'{label}: actual={actual!r}, expected={expected!r}')
    for rid in result.get('result_ids', []):
        item = result['results'][rid]
        data, kind = item['data'], item['kind']
        scope = data['scope']
        rows = raw_reviews(db, scope)
        def check_summary(actual, selected, label):
            expected = summary(selected)
            for key, value in expected.items():
                if key in actual:
                    equal(actual[key], value, label + '.' + key)
            for aspect, counts in actual.get('aspects', {}).items():
                for sentiment in ('negative', 'positive'):
                    key = sentiment + '_review_count'
                    if key in counts:
                        count = sum(any(o['aspect'] == aspect and o['sentiment'] == sentiment for o in r['opinions']) for r in selected)
                        equal(counts[key], count, label + '.' + aspect + '.' + key)
        if 'summary' in data:
            check_summary(data['summary'], rows, rid)
        if 'current' in data:
            check_summary(data['current'], rows, rid + '.current')
            check_summary(data['baseline'], raw_reviews(db, data['baseline_scope']), rid + '.baseline')
        if kind == 'positive_aspects':
            counts = {a: sum(any(o['aspect'] == a and o['sentiment'] == 'positive' for o in r['opinions']) for r in rows)
                      for a in ('quality', 'effectiveness', 'price', 'packaging', 'logistics', 'service')}
            equal([a['aspect'] for a in data['aspects']], sorted(counts, key=lambda a: (-counts[a], a)), rid + '.ranking')
            for a in data['aspects']:
                count, totals = counts[a['aspect']], summary(rows)
                for key, value in {'positive_review_count': count, 'positive_rate_denominator': totals['positive_review_count'],
                    'positive_success_rate_denominator': totals['successful_reviews'], 'positive_rate': rate(count, totals['positive_review_count']),
                    'positive_success_rate': rate(count, totals['successful_reviews'])}.items():
                    equal(a[key], value, rid + '.' + key)
        if kind in ('issues', 'window_comparison'):
            counts = {a['aspect']: sum(any(o['aspect'] == a['aspect'] and o['sentiment'] == 'negative' for o in r['opinions']) for r in rows) for a in data['issues']}
            equal([a['aspect'] for a in data['issues']], sorted(counts, key=lambda a: (-counts[a], a)), rid + '.ranking')
            for a in data['issues']:
                equal(a['negative_review_count'], counts[a['aspect']], rid + '.count')
                equal(a['negative_rate'], rate(counts[a['aspect']], summary(rows)['successful_reviews']), rid + '.rate')
        for bucket in data.get('buckets', []):
            check_summary(bucket, [r for r in rows if r['published_at'].startswith(bucket['bucket'])], rid + '.' + bucket['bucket'])
        for product in data.get('products', []):
            check_summary(product, [r for r in rows if r['product_id'] == product['product_id']], rid + '.' + product['product_id'])
        if kind == 'evidence':
            selected = [r for r in rows if any((data['aspect'] is None or o['aspect'] == data['aspect']) and
                        (data['sentiment'] == 'all' or o['sentiment'] == data['sentiment']) for o in r['opinions'])]
            equal(data['total'], len(selected), rid + '.total')
            ids = [r['review_id'] for r in data['items']]
            equal(len(ids), len(set(ids)), rid + '.distinct')
            for item in data['items']:
                r = next((r for r in selected if r['id'] == item['review_id']), None)
                equal(r is not None, True, rid + '.authorized_review')
                if r:
                    for o in item['opinions']:
                        equal(r['text'][o['quote_start']:o['quote_end']], o['quote'], rid + '.quote')
                        equal(any(all(op[k] == o[k] for k in ('aspect', 'sentiment', 'quote')) for op in r['opinions']), True, rid + '.effective_opinion')
    return errors


def grade(case, result, db):
    checks = {'status': result.get('status') == case['status'],
              'scope': result.get('scope') == {**SCOPE, **case['scope'], 'as_of': None},
              'sentiment': result.get('sentiment_intent') == case['sentiment']}
    selected = [result['results'][rid] for rid in result.get('result_ids', [])]
    if case['status'] == 'ok':
        checks['validation'] = result.get('validation', {}).get('status') == 'passed'
        for tool, params in case['expected']:
            checks[tool] = any(r['kind'] == TOOL_KINDS[tool] and all(r['parameters'].get(k) == v for k, v in params.items()) for r in selected)
        checks['no_unrequested_analysis'] = all(r['kind'] == 'context' or r['kind'] in {TOOL_KINDS[t] for t, _ in case['expected']} for r in selected)
        checks['public_answer'] = result.get('public_answer', {}).get('status') == 'ok'
        bound = {**SCOPE, **case['scope']}
        checks['all_selected_products'] = all(set(r['data']['scope']['product_ids']) == set(bound['product_ids']) for r in selected)
        checks['dataset_and_annotation_identity'] = all(all(r['data']['scope'][k] == bound[k]
            for k in ('source', 'dataset_version', 'model', 'base_url', 'prompt_version')) for r in selected)
        if case['path'] == 'confirmed':
            checks['no_planner'] = result.get('model_calls') == 0
        errors = check_statistics(db, result)
        checks['independent_database_oracle'] = not errors
    else:
        errors = []
        checks['no_false_answer'] = result.get('public_answer', {}).get('status') != 'ok'
    return checks, errors


def offline_client(case):
    def handler(request):
        body = json.loads(request.content)
        if not body.get('tools'):
            payload = json.loads(body['messages'][-1]['content'])
            draft = {'summary': payload['summary_options'][0], 'items': [{'finding_ref': f['finding_ref'], 'aspect_key': f['aspect_key'], 'explanation': f['statements'][0]} for f in payload['findings']], 'caveats': []}
            message = {'role': 'assistant', 'content': json.dumps(draft)}
        elif any(m['role'] == 'tool' for m in body['messages']):
            ids = ['r' + str(i + 1) for i in range(len(case['expected']))]
            message = {'role': 'assistant', 'content': json.dumps({'status': 'ok', 'result_ids': ids})}
        else:
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call_' + str(i), 'type': 'function', 'function': {'name': tool, 'arguments': json.dumps(params)}} for i, (tool, params) in enumerate(case['expected'])]}
        return httpx.Response(200, json={'id': 'offline-phase7', 'object': 'chat.completion', 'created': 0,
            'model': 'offline-phase7', 'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls' if message.get('tool_calls') else 'stop'}]})
    return lambda **kwargs: OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))


class BoundedLiveTransport(httpx.BaseTransport):
    """没有 SDK 重试；只保存计数与用量，禁止保存 HTTP header 或凭据。"""
    def __init__(self, ledger):
        self.inner = None
        self.ledger = ledger
    def handle_request(self, request):
        content = request.read()
        body = json.loads(content)
        if len(self.ledger) >= 36 or len(content) > 64_000 or sum(r['input_bytes'] for r in self.ledger) + len(content) > 600_000:
            raise RuntimeError('phase7 request/input budget exceeded')
        if not 1 <= body.get('max_tokens', 0) <= 2048:
            raise RuntimeError('phase7 output budget exceeded')
        row = {'input_bytes': len(content), 'phase': 'planning' if body.get('tools') else 'answer', 'model': body['model']}
        self.ledger.append(row)
        started = perf_counter()
        try:
            if self.inner is None:
                self.inner = httpx.HTTPTransport(retries=0)
            response = self.inner.handle_request(request)
            response.read()
        except Exception as exc:
            row.update(elapsed_ms=round((perf_counter() - started) * 1000, 3), transport_error=type(exc).__name__)
            raise
        row.update(elapsed_ms=round((perf_counter() - started) * 1000, 3), http_status=response.status_code)
        if response.status_code == 200:
            payload = response.json()
            row['usage'] = payload.get('usage')
            row['finish_reason'] = payload.get('choices', [{}])[0].get('finish_reason')
        return response
    def close(self):
        if self.inner is not None:
            self.inner.close()


def main():
    # 本次验收子进程不继承系统级 TLS 调试密钥日志配置。
    os.environ.pop('SSLKEYLOGFILE', None)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    admin = os.environ['TEST_POSTGRES_ADMIN_URL']
    name = 'agent_test_phase7_eval_' + uuid.uuid4().hex
    db = urlunsplit(urlsplit(admin)._replace(path='/' + name))
    with psycopg.connect(admin, autocommit=True) as con:
        con.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(name)))
    ledger, rows = [], []
    settings = replace(AgentSettings.from_values(read_settings(Path('.env'))), max_steps=3, max_retries=0, answer_max_retries=0) if args.live else AgentSettings('offline-phase7', 'not-a-real-key', 'https://offline.invalid/v1', max_steps=3, max_retries=0)
    try:
        create_fixture(args.output / 'fixture', db)
        before = snapshot(db)
        selected = cases()[:12] if args.live else cases()
        for case in selected:
            start = perf_counter()
            client = (lambda **kwargs: OpenAI(**kwargs, http_client=httpx.Client(transport=BoundedLiveTransport(ledger)))) if args.live else offline_client(case)
            with patch('openai.OpenAI', side_effect=client), redirect_stdout(io.StringIO()):
                result = run_review_agent(db, case['effective_question'], scope={**SCOPE, **case['scope']}, settings=settings)
            checks, errors = grade(case, result, db)
            rows.append({**case, 'passed': all(checks.values()), 'checks': checks, 'oracle_errors': errors,
                         'elapsed_ms': round((perf_counter() - start) * 1000, 3), 'actual': result})
            print(case['id'], 'PASS' if all(checks.values()) else 'FAIL', flush=True)
        readonly = snapshot(db) == before
        report = {'source': 'actual supplier + isolated synthetic PostgreSQL' if args.live else 'scripted HTTP + real HelloAgents + synthetic PostgreSQL; not model understanding accuracy',
                  'model': settings.model, 'cases': rows, 'business_tables_unchanged': readonly, 'requests': ledger,
                  'limits': {'cases': 12, 'http_requests': 36, 'total_input_bytes': 600000, 'max_output_tokens_per_request': 2048, 'sdk_retries': 0} if args.live else None,
                  'actual_cost': None, 'cost_note': '供应商响应未提供可核验的实际费用；没有将 token 推算费用作为账单。',
                  'passed': sum(r['passed'] for r in rows), 'failed': sum(not r['passed'] for r in rows)}
        (args.output / 'evaluation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        assert readonly, '分析写入业务表'
    finally:
        with psycopg.connect(admin, autocommit=True) as con:
            con.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
    return int(report['failed'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
