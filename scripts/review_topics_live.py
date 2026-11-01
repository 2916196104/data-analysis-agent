"""指定商品的真实只读二级主题验收；凭据不写入产物。"""
import argparse
from contextlib import closing
import json
from pathlib import Path
from time import perf_counter

from data_analysis_agent.core.config import read_settings, AgentSettings
from data_analysis_agent.core.database import PostgresConnection
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.reviews.analytics import get_aspect_opinions
from data_analysis_agent.reviews.store import connect_review_database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--extract-only', action='store_true', help='只读提取与引用校验，不调用模型')
    parser.add_argument('--product', default='100013588053')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.live and not args.extract_only:
        parser.error('请指定 --extract-only 或经授权的 --live。')
    values = read_settings(); settings = AgentSettings.from_values(values)
    db = values['DATABASE_URL']
    args.output.mkdir(parents=True, exist_ok=True)
    with closing(connect_review_database(db, readonly=True)) as connection, connection:
        connection.execute('BEGIN')
        if isinstance(connection, PostgresConnection):
            connection.execute("SELECT set_config('statement_timeout','30000',true)")
        row = connection.execute('''SELECT r.source,r.dataset_version,a.requested_model,a.base_url,a.prompt_version,
            MIN(r.published_at) date_from,MAX(r.published_at) date_to,COUNT(*) n
            FROM reviews r JOIN review_annotations a ON a.review_id=r.id AND a.content_sha256=r.content_sha256
            WHERE r.product_id=? AND a.status='success'
            GROUP BY r.source,r.dataset_version,a.requested_model,a.base_url,a.prompt_version ORDER BY n DESC LIMIT 1''', (args.product,)).fetchone()
        if row is None:
            raise ValueError('指定商品没有可用的成功标注。')
        scope = dict(model=row['requested_model'], base_url=row['base_url'], prompt_version=row['prompt_version'],
            source=row['source'], dataset_version=row['dataset_version'], product_ids=[args.product],
            date_from=row['date_from'][:10], date_to=row['date_to'][:10], baseline_from='2000-01-01', baseline_to='2000-01-02')
        backend = type(connection).__name__
    start = perf_counter()
    if args.extract_only:
        current = {key: value for key, value in scope.items() if not key.startswith('baseline_')}
        extracted = get_aspect_opinions(db, **current, aspect='effectiveness', sentiment='positive')
        checked = 0
        for item in extracted['items']:
            assert item['product_id'] == args.product and item['evidence_ref'] == f"sha256:{item['file_sha256']}#line={item['line_number']}"
            for opinion in item['opinions']:
                assert opinion['aspect'] == 'effectiveness' and opinion['sentiment'] == 'positive'
                assert item['text'][opinion['quote_start']:opinion['quote_end']] == opinion['quote']
                checked += 1
        report = dict(database_backend=backend, product_id=args.product, total_reviews=extracted['total'],
            total_opinions=extracted['total_opinions'], selected_reviews=len(extracted['items']), checked_opinions=checked,
            source=scope['source'], dataset_version=scope['dataset_version'], date_from=scope['date_from'], date_to=scope['date_to'],
            annotation_profile_ref='profile:'+content_sha256([scope[key] for key in ('model','base_url','prompt_version')])[:16],
            real_model_used=False, model_requests=0, status='passed', elapsed_seconds=round(perf_counter()-start,3))
        (args.output/'extraction.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False)); return
    result = run_review_agent(db, '使用效果具体好在哪里？', scope=scope, settings=settings,
        conversation_context={'user': {'task_id': 'readonly-parent', 'question': '好评主要集中在哪些方面？'},
            'users': [{'task_id': 'readonly-parent', 'question': '好评主要集中在哪些方面？'}]})
    topics = next((item['data'] for item in result['results'].values() if item['kind'] == 'aspect_drilldown'), None)
    report = {'database_backend': backend, 'real_model': settings.model, 'real_model_used': bool(result.get('topic_model_calls')),
        'product_id': args.product, 'source': scope['source'], 'dataset_version': scope['dataset_version'],
        'date_from': scope['date_from'], 'date_to': scope['date_to'], 'status': result['status'],
        'validation': result.get('validation'), 'topic_model_calls': result.get('topic_model_calls'),
        'elapsed_seconds': round(perf_counter() - start, 3), 'public_answer': result['public_answer'],
        'topics': topics, 'error': result.get('error')}
    # 没有数据库连接 URL、密钥或服务器文件路径；只保存本次真实只读快照。
    (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('database_backend', 'real_model', 'status', 'topic_model_calls', 'elapsed_seconds')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
