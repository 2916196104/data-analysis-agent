"""真实 PostgreSQL 的本地事实目录与简单回答校验；强制禁止外部模型调用。"""
import argparse
from contextlib import closing
import json
from pathlib import Path
from unittest.mock import patch

from data_analysis_agent.core.config import read_settings, AgentSettings
from data_analysis_agent.reviews.store import connect_review_database
from data_analysis_agent.reviews.analytics import get_positive_aspects
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_validation import validate_report
from data_analysis_agent.agents.review_compression import build_analysis_artifacts, CompactAnalysisContext
from data_analysis_agent.agents.review_insights import fact_catalog
from data_analysis_agent.agents.review_answers import NaturalLanguageAnswerGenerator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); values = read_settings(); settings = AgentSettings.from_values(values)
    with closing(connect_review_database(values['DATABASE_URL'], readonly=True)) as connection, connection:
        connection.execute('BEGIN')
        row = connection.execute('''SELECT r.source,r.dataset_version,a.requested_model,a.base_url,a.prompt_version,
            MIN(r.published_at) date_from,MAX(r.published_at) date_to,COUNT(*) n FROM reviews r
            JOIN review_annotations a ON a.review_id=r.id AND a.content_sha256=r.content_sha256
            WHERE r.product_id=? AND a.status='success' GROUP BY r.source,r.dataset_version,a.requested_model,a.base_url,a.prompt_version
            ORDER BY n DESC LIMIT 1''', ('100013588053',)).fetchone()
        if row is None:
            raise ValueError('没有可用的已标注商品。')
        scope = dict(model=row['requested_model'],base_url=row['base_url'],prompt_version=row['prompt_version'],
            source=row['source'],dataset_version=row['dataset_version'],product_ids=['100013588053'],
            date_from=row['date_from'][:10],date_to=row['date_to'][:10])
        data = get_positive_aspects(connection, **scope); backend = type(connection).__name__
    question = '美的空调好评主要集中在哪些方面？'
    scope.update(baseline_from='2000-01-01', baseline_to='2000-01-02')
    plan = build_query_plan(question,scope)
    results = {'r1':dict(result_id='r1',kind='positive_aspects',parameters={},data=data)}
    validation = validate_report(results,['r1'],expected_scope=scope,query_plan=plan,sentiment_intent='positive')
    if validation['status'] != 'passed':
        raise ValueError('真实库本地统计校验未通过。')
    full = dict(status='ok',question=question,scope=scope,results=results,result_ids=['r1'],validation=validation)
    artifacts = build_analysis_artifacts(full,plan)
    catalog,_ = fact_catalog(CompactAnalysisContext.model_validate(artifacts['compact_context']))
    with patch('openai.OpenAI', side_effect=AssertionError('真实数据校验禁止外部请求')) as client:
        answer,diag = NaturalLanguageAnswerGenerator().generate(question,artifacts['compact_context'],settings=settings,validated=True)
    client.assert_not_called()
    report = dict(database_backend=backend,validation=validation,model_requests=0,fact_count=len(catalog),answer=answer,
        source=scope['source'],dataset_version=scope['dataset_version'],product_ids=scope['product_ids'],date_from=scope['date_from'],date_to=scope['date_to'])
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'database_backend':backend,'validation':validation['status'],'model_requests':0,'fact_count':len(catalog),'summary':answer['summary']},ensure_ascii=False))


if __name__ == '__main__':
    main()
