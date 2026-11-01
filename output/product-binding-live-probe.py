"""只读核对真实目录、解析前后差异和实际查询 ID；禁止外部模型请求。"""
import json
import subprocess
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from data_analysis_agent.core.config import AgentSettings, read_settings
from data_analysis_agent.reviews.store import connect_review_database
from data_analysis_agent.agents.review_intent import resolve_review_request
from data_analysis_agent.agents.review_semantics import product_mentions
from data_analysis_agent.agents.reviews import run_review_agent

db = read_settings()['DATABASE_URL']
with closing(connect_review_database(db, readonly=True)) as connection, connection:
    connection.execute('BEGIN')
    readonly = connection.execute('SHOW transaction_read_only').fetchone()[0]
    products = [dict(row) for row in connection.execute(
        'SELECT product_id,title,brand FROM products WHERE source=? AND dataset_version=?',
        ('reviews_csv', 'reviews-csv'))]
    profile = connection.execute('''SELECT a.requested_model,a.base_url,a.prompt_version,COUNT(*) n
        FROM review_annotations a JOIN reviews r ON r.id=a.review_id
        WHERE r.source=? AND r.dataset_version=? AND r.product_id=? AND a.status='success'
        AND a.content_sha256=r.content_sha256 GROUP BY a.requested_model,a.base_url,a.prompt_version
        ORDER BY n DESC LIMIT 1''', ('reviews_csv', 'reviews-csv', '100013588053')).fetchone()
    bounds = connection.execute('SELECT MIN(published_at),MAX(published_at) FROM reviews WHERE source=? AND dataset_version=? AND product_id=?',
        ('reviews_csv', 'reviews-csv', '100013588053')).fetchone()
start, end = bounds[0][:10], bounds[1][:10]
baseline_end = date.fromisoformat(start) - timedelta(days=1)
scope = dict(source='reviews_csv', dataset_version='reviews-csv', product_ids=['100013588053'],
    model=profile['requested_model'], base_url=profile['base_url'], prompt_version=profile['prompt_version'],
    date_from=start, date_to=end, baseline_from=(baseline_end-timedelta(days=30)).isoformat(), baseline_to=baseline_end.isoformat())
settings = AgentSettings(model='READONLY-NO-MODEL', api_key='offline', base_url='https://offline.invalid/v1')
old = {}
exec(subprocess.check_output(['git','show','HEAD:src/data_analysis_agent/agents/review_semantics.py'], text=True, encoding='utf-8'), old)
questions = ['制冷好评主要集中在哪些方面？', '美的风酷空调的制冷好评主要集中在哪些方面？',
             '用户对于美的空调风酷的制冷好评主要集中在哪些方面？']
report = {'transaction_read_only': readonly, 'annotation_identity': {'model': scope['model'], 'prompt_version': scope['prompt_version']},
          'source': scope['source'], 'dataset_version': scope['dataset_version'], 'date_from': start, 'date_to': end, 'cases': []}
for question in questions:
    with patch('openai.OpenAI', side_effect=AssertionError('external model disabled')) as model:
        result = run_review_agent(db, question, scope=scope, settings=settings)
    assert result['status'] == 'ok', result.get('error')
    query = result['results']['r1']
    assert query['data']['scope']['product_ids'] == ['100013588053']
    assert query['parameters']['topic'] == '制冷' and query['parameters']['sentiment'] == 'positive'
    report['cases'].append({'question': question, 'before_mentions': old['product_mentions'](question, products),
        'after_mentions': product_mentions(question, products), 'query_product_ids': query['data']['scope']['product_ids'],
        'aspect': query['parameters']['aspect'], 'topic': query['parameters']['topic'], 'sentiment': query['parameters']['sentiment'],
        'validation': result['validation']['status'], 'matched_reviews': query['data']['total']})
same_series = [row['product_id'] for row in products if row['title'] and '美的' in row['title'] and '风酷' in row['title']]
try:
    resolve_review_request(questions[-1], {**scope,'product_ids':same_series}, products, settings=settings)
    raise AssertionError('multiple selected models must clarify')
except Exception as exc:
    from data_analysis_agent.agents.review_semantics import QuestionScopeError
    assert isinstance(exc, QuestionScopeError)
    report['multi_model_clarification'] = str(exc)
Path('output/product-binding-live.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
