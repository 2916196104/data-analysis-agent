"""Run the frozen portfolio probes on local synthetic SQLite only."""

import argparse
from collections import Counter
from contextlib import ExitStack, closing, redirect_stdout
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from importlib.metadata import version
import json
from math import ceil
from pathlib import Path
import platform
import socket
import sqlite3
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import url2pathname

import openai
import psycopg

ROOT = Path(__file__).resolve().parents[1]
CASE_FILE = ROOT / 'tests/fixtures/agent_offline_portfolio_v1.json'
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'src')]

from test_review_dimension_content import DimensionContentTests
from test_review_positive_trend import PositiveTrendTests
from data_analysis_agent.core.config import AgentSettings
from data_analysis_agent.jobs import submission, worker
from data_analysis_agent.jobs.store import claim_task, enqueue, get_task, init_tasks, worker_started


SETTINGS = AgentSettings('portfolio-offline', 'synthetic-only', 'https://offline.invalid/v1',
                         max_retries=0, semantic_parser_enabled=False)
IDENTITY = dict(model=SETTINGS.model, base_url=SETTINGS.base_url, prompt_version='portfolio-offline-v1')
MODEL_FIELDS = ('intent_model_calls', 'planning_model_calls', 'topic_model_calls', 'answer_model_calls')


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')


def write_json(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')


def fingerprint(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def offline(stack, allowed, attempts):
    def deny(name):
        def blocked(*args, **kwargs):
            attempts.append(name)
            raise AssertionError(f'Offline boundary: {name}')
        return blocked

    connect = sqlite3.connect

    def sqlite_only(database, *args, **kwargs):
        value = str(database)
        if value.startswith('file:'):
            uri = urlsplit(value)
            path = Path(url2pathname(uri.path))
            if uri.netloc not in ('', 'localhost'):
                return deny('nonlocal SQLite')()
        else:
            path = Path(value)
        if not path.resolve().is_relative_to(allowed):
            return deny('SQLite outside fixture root')()
        return connect(database, *args, **kwargs)

    stack.enter_context(patch.object(sqlite3, 'connect', sqlite_only))
    for target, name in (
        (psycopg, 'connect'), (psycopg.Connection, 'connect'),
        (socket.socket, 'connect'), (socket.socket, 'connect_ex'), (socket, 'getaddrinfo'),
        (subprocess, 'Popen'), (openai, 'OpenAI'),
    ):
        stack.enter_context(patch.object(target, name, deny(name)))
    for target in ('data_analysis_agent.agents.reviews.create_llm',
                   'data_analysis_agent.agents.review_retrieval.create_semantic_service'):
        stack.enter_context(patch(target, deny(target)))
    for target in (worker, submission):
        stack.enter_context(patch.object(target, 'configured_agent_identity', return_value=(SETTINGS, IDENTITY)))


def fixtures(root, stack):
    result = {}
    for key, test in (('trend', PositiveTrendTests()), ('dimensions', DimensionContentTests())):
        stack.callback(test.doCleanups)
        with patch('tempfile.tempdir', str(root)):
            test.setUp()
        result[key] = (test.db, test.scope if key == 'trend' else test.scope())
    return result


def corpus(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        return {key: [dict(row) for row in db.execute(query)] for key, query in {
            'reviews': 'SELECT id, source, dataset_version, product_id, user_id, published_at, rating, text, content_sha256 FROM reviews ORDER BY id',
            'annotations': 'SELECT id, review_id, requested_model, base_url, prompt_version, status FROM review_annotations ORDER BY id',
            'opinions': 'SELECT annotation_id, aspect, sentiment, quote, quote_start, quote_end FROM review_opinions ORDER BY id',
        }.items()}


def select_fields(value, keys):
    return {key: value.get(key) for key in keys}


def score(case, task, scope, source):
    result = task.get('result') or {}
    answer = result.get('public_answer', {})
    refs = answer.get('evidence_refs', [])
    checks = {}

    def equal(name, actual, expected):
        checks[name] = dict(passed=actual == expected, actual=actual, expected=expected)

    refusal = case['category'] in ('refusal', 'empty_evidence')
    equal('task_transport_status', task['status'], 'succeeded')
    equal('agent_status', result.get('status'), 'unsupported' if refusal else 'ok')
    equal('public_status', answer.get('status'), 'unsupported' if refusal else 'ok')
    equal('model_attempts', select_fields(result, (*MODEL_FIELDS, 'total_model_calls')),
          dict.fromkeys((*MODEL_FIELDS, 'total_model_calls'), 0))
    if refusal:
        equal('no_published_claims', sum(len(answer.get(key, [])) for key in
              ('metrics', 'highlights', 'evidence_refs', 'source_result_ids')), 0)
        if case['category'] == 'refusal':
            equal('no_business_tool_query', result.get('query_executions'), 0)
            if 'refusal_text' in case:
                equal('reason_mentions_requested_target', case['refusal_text'] in answer.get('summary', ''), True)
        else:
            equal('reason_distinguishes_empty_evidence', (result.get('task_acceptance') or {}).get('evidence_status'),
                  'no_matching_opinions')
    else:
        equal('scope', select_fields(result.get('scope', {}), scope), scope)
        equal('business_validation', (result.get('validation') or {}).get('status'), 'passed')
        equal('task_acceptance', (result.get('task_acceptance') or {}).get('complete'), True)

    if 'kind' not in case:
        return checks
    ids = result.get('result_ids', [])
    selected = [(key, row) for key, row in result.get('results', {}).items()
                if row['kind'] == case['kind'] and (key in ids or case['category'] == 'empty_evidence')]
    equal('expected_result_count', len(selected), 1)
    if len(selected) != 1:
        return checks
    result_id, item = selected[0]
    data = item['data']
    for dotted, expected in case.get('fields', {}).items():
        actual = data
        for key in dotted.split('.'):
            actual = actual.get(key, '<missing>') if isinstance(actual, dict) else '<missing>'
        equal(dotted, actual, expected)
    public_metrics = [metric for group in answer.get('metrics', []) for metric in group['values']]
    if 'series' in case:
        expected = case['series']
        series = [series for row in result.get('compact_context', {}).get('statistics', [])
                  if row['source_result_id'] == result_id for series in row['trends']]
        equal('trend_dimension_period', [[row['aspect'], row['period']] for row in series],
              [[expected['aspect'], expected['period']]])
        points = [dict(bucket=point['bucket'], **point['metric']) for row in series for point in row['points']]
        for label, metrics in (('compact', points), ('public', [point for point in public_metrics if 'bucket' in point])):
            equal(label + '_points', [[row[key] for key in ('bucket', 'numerator', 'denominator', 'value')] for row in metrics],
                  expected['points'])
            equal(label + '_denominators', [row['denominator_name'] for row in metrics],
                  [expected['denominator']] * len(expected['points']))
    if 'products' in case:
        equal('counts_by_product', sorted([[row[key] for key in ('product_id', 'comparison_count',
              'comparison_denominator', 'comparison_rate')] for row in data['products']]), case['products'])
    if 'topic' in case:
        expected = case['topic']
        topic = next((row for row in data['topics'] if row['topic_id'] == expected['topic_id']), {})
        equal('topic_count_and_denominator', select_fields(topic, expected), expected)
    if 'citations' in case:
        expected = case['citations']
        equal('distinct_public_reviews', len({ref['review_id'] for ref in refs}), expected['reviews'])
        equal('one_citation_per_review', len(refs), expected['reviews'])
        quotes = []
        for ref in refs:
            row = next((row for row in source['reviews'] if row['id'] == ref['review_id']), {})
            equal(f"reference:{ref['review_id']}", bool(row and row['product_id'] == ref['product_id']
                  and row['published_at'] == ref['published_at'] and ref['source_result_id'] == result_id
                  and row['product_id'] in scope['product_ids']
                  and row['source'] == scope['source'] and row['dataset_version'] == scope['dataset_version']
                  and scope['date_from'] <= row['published_at'][:10] <= scope['date_to']), True)
            equal(f"nonempty_opinions:{ref['review_id']}", bool(ref['opinions']), True)
            for index, opinion in enumerate(ref['opinions']):
                fields = ('aspect', 'sentiment', 'quote', 'quote_start', 'quote_end', 'annotation_id')
                quote = opinion['quote']
                quotes.append(quote)
                annotation = next((entry for entry in source['annotations'] if entry['id'] == opinion['annotation_id']), {})
                equal(f"support:{ref['review_id']}:{index}", bool(row and annotation
                      and annotation['review_id'] == row['id'] and annotation['status'] == 'success'
                      and (annotation['requested_model'], annotation['base_url'], annotation['prompt_version'])
                      == (scope['model'], scope['base_url'], scope['prompt_version'])
                      and opinion['aspect'] == expected['aspect'] and opinion['sentiment'] == expected['sentiment']
                      and 0 <= opinion['quote_start'] < opinion['quote_end'] <= len(row['text'])
                      and row['text'][opinion['quote_start']:opinion['quote_end']] == quote
                      and select_fields(opinion, fields) in source['opinions']), True)
        equal('allowed_visible_quotes', sorted(set(quotes)), expected['quotes'])
    return checks


def execute(case, db, tasks, runner, parents, scope):
    request = {**scope, 'question': case['question'], 'conversation_id': 'portfolio-' + case.get('parent', case['id'])}
    if 'parent' in case:
        request['context_task_id'] = parents[case['parent']]
    params = submission.prepare_parameters('analysis', request, import_root=db.parent,
                                           env_file=db.parent / 'unused.env', business_db=db)
    submitted = enqueue(tasks, 'analysis', params)['task']
    claimed = claim_task(tasks, runner.worker_id)
    assert claimed['task_id'] == submitted['task_id']
    started = perf_counter()
    runner._execute(claimed)
    elapsed = (perf_counter() - started) * 1000
    return get_task(tasks, claimed['task_id']), elapsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--freeze', type=Path, help='Reuse a freeze manifest from an earlier run')
    args = parser.parse_args()
    manifest = json.loads(CASE_FILE.read_text(encoding='utf-8'))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source_revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    source_fingerprint = fingerprint({path.relative_to(ROOT).as_posix(): sha256(path.read_bytes()).hexdigest()
                                     for path in sorted((ROOT / 'src/data_analysis_agent').rglob('*.py'))})
    attempts, cases = [], []
    with TemporaryDirectory(prefix='agent-portfolio-') as temporary, ExitStack() as guard, ExitStack() as cleanup:
        root = Path(temporary).resolve()
        offline(guard, root, attempts)
        inputs = fixtures(root, cleanup)
        with ExitStack() as evaluation, (output / 'framework.log').open('x', encoding='utf-8') as log, redirect_stdout(log):
            offline(evaluation, root, attempts)
            frozen = {key: corpus(path) for key, (path, _) in inputs.items()}
            before = {key: sha256(path.read_bytes()).hexdigest() for key, (path, _) in inputs.items()}
            freeze = {'version': manifest['version'], 'cases_sha256': sha256(CASE_FILE.read_bytes()).hexdigest(),
                      'fixture_scopes': {key: scope for key, (_, scope) in inputs.items()},
                      'corpus_sha256': fingerprint(frozen)}
            if args.freeze and json.loads(args.freeze.read_text(encoding='utf-8')) != freeze:
                raise ValueError('Frozen questions, corpus or scope changed')
            write_json(output / 'freeze.json', freeze)
            write_json(output / 'corpus.json', frozen)
            started_at = now()
            runtime = {}
            for key, (path, _) in inputs.items():
                tasks = path.parent / 'portfolio.tasks.sqlite3'
                init_tasks(tasks, path)
                runner = worker.TaskWorker(path, tasks, import_root=path.parent, env_file=path.parent / 'unused.env')
                worker_started(tasks, runner.worker_id)
                runtime[key] = tasks, runner
            parents = {}
            for case in manifest['cases']:
                key = case['fixture']
                db, default_scope = inputs[key]
                scope = {**default_scope, 'as_of': None, **case.get('scope', {})}
                task, elapsed = execute(case, db, *runtime[key], parents, scope)
                parents[case['id']] = task['task_id']
                checks = score(case, task, scope, frozen[key])
                result = task.get('result') or {}
                cases.append(dict(id=case['id'], category=case['category'], question=case['question'],
                                  status=result.get('status'), passed=all(row['passed'] for row in checks.values()),
                                  worker_ms=round(elapsed, 3), agent_ms=result.get('performance', {}).get('total_duration_ms'),
                                  model_calls=select_fields(result, (*MODEL_FIELDS, 'total_model_calls')),
                                  query_executions=result.get('query_executions'), checks=checks))
                write_json(output / (case['id'] + '.json'), task)
            unchanged = before == {key: sha256(path.read_bytes()).hexdigest() for key, (path, _) in inputs.items()}
    latency = {}
    for key in ('worker_ms', 'agent_ms'):
        values = sorted(row[key] for row in cases if row[key] is not None)
        latency[key] = dict(samples=len(values), median=median(values), p95=values[ceil(0.95 * len(values)) - 1],
                            minimum=values[0], maximum=values[-1]) if values else None
    summary = dict(passed=sum(row['passed'] for row in cases), total=len(cases),
                   by_category={category: {'passed': sum(row['passed'] for row in cases if row['category'] == category),
                                           'total': count} for category, count in Counter(row['category'] for row in cases).items()},
                   latency=latency, model_calls={field: sum(row['model_calls'][field] or 0 for row in cases)
                                                 for field in (*MODEL_FIELDS, 'total_model_calls')})
    report = dict(started_at=started_at, finished_at=now(), revision=source_revision, source_sha256=source_fingerprint,
                  runtime={'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
                           **{package: version(package) for package in ('pydantic', 'hello-agents', 'openai')}},
                  corpus_unchanged=unchanged, blocked_attempts=attempts, real_rag='not_run', summary=summary, cases=cases)
    write_json(output / 'evaluation.json', report)
    print(json.dumps(summary, ensure_ascii=False))
    print(f'{output}: blocked attempts={len(attempts)}, corpus unchanged={unchanged}')
    return int(not unchanged or bool(attempts) or summary['passed'] != summary['total'])


if __name__ == '__main__':
    raise SystemExit(main())
