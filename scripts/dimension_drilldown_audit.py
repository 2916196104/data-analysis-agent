"""只读核查真实任务及原始范围；禁止模型调用和数据库写入。"""
import argparse
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch

from data_analysis_agent.core.config import read_settings, AgentSettings
from data_analysis_agent.jobs.store import connect_task_database, get_task
from data_analysis_agent.jobs.analysis_context import load_analysis_context
from data_analysis_agent.agents.review_intent import resolve_review_request
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.reviews.parameters import BaselineScope


def public(value):
    if isinstance(value, dict):
        return {key: public(item) for key, item in value.items() if key not in ('base_url', 'model_identity', 'agent_model_identity')}
    if isinstance(value, list):
        return [public(item) for item in value]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    values = read_settings()
    db = values['DATABASE_URL']
    with closing(connect_task_database(db, readonly=True)) as connection:
        rows = connection.execute("SELECT task_id FROM tasks WHERE kind='analysis' AND parameters_json LIKE ? ORDER BY created_at DESC LIMIT 40",
                                  ('%100013588053%',)).fetchall()
    tasks = [get_task(db, row['task_id']) for row in rows]
    target = next((task for task in tasks if task['parameters'].get('question') in ('客服方面有什么问题？', '客服方面有什么问题')), None)
    if target is None:
        raise RuntimeError('未找到待核查的真实第二轮任务；未执行查询。')
    related = {target['task_id'], target['parameters'].get('context_task_id')}
    records = []
    for task in (task for task in tasks if task['task_id'] in related):
        params, result = task['parameters'], task.get('result') or {}
        records.append({'task_id': task['task_id'], 'status': task['status'], 'created_at': task['created_at'],
            'parameters': params, **{key: result.get(key) for key in ('question', 'user_intent', 'intent_resolution',
                'analysis_intent', 'query_plan', 'trace', 'evidence_assessment', 'evidence_completion', 'result_ids', 'public_answer')}})
    report = {'historical_tasks': records, 'external_model_calls': 0}
    if target:
        params = target['parameters']
        scope = BaselineScope.model_validate({key: value for key, value in params.items() if key in BaselineScope.model_fields}).model_dump()
        settings = replace(AgentSettings.from_values(values), semantic_parser_enabled=False)
        context = load_analysis_context(db, params)
        # 禁止规划、主题和回答任何一个入口外发真实文本。
        with patch('openai.OpenAI', side_effect=AssertionError('No external model authorization')), \
             patch('data_analysis_agent.agents.reviews.create_llm', side_effect=AssertionError('No external model authorization')):
            if args.execute:
                result = run_review_agent(db, params['question'], scope=scope, settings=settings, conversation_context=context)
                report['readonly_replay'] = result
            else:
                from data_analysis_agent.reviews.store import connect_review_database
                with closing(connect_review_database(db, readonly=True)) as connection:
                    products = [dict(row) for row in connection.execute('SELECT product_id,title FROM products WHERE source=? AND dataset_version=?', (scope['source'], scope['dataset_version'])).fetchall()]
                state = {}
                resolved, plan, _ = resolve_review_request(params['question'], scope, products, settings=settings, context=context, intent_state=state)
                report['resolved_plan'] = {'scope': resolved, 'plan': plan.to_dict(), 'intent': state}
        report['source_task_id'] = target['task_id']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(public(report), ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'source_task_id': report.get('source_task_id'),
        'tasks': [{'task_id': row['task_id'], 'question': row['parameters'].get('question'), 'analysis_type': row['analysis_intent']} for row in records],
        'replay_status': report.get('readonly_replay', {}).get('status')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
