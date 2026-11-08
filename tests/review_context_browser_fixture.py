"""浏览器回归专用临时库与合成分类器；不读取生产配置或调用模型。"""
import argparse
import os
from unittest.mock import patch
import uvicorn

from test_review_task_completion import ReviewTaskCompletionTests, synthetic_classifier
from data_analysis_agent.api.app import create_app
from data_analysis_agent.jobs.store import init_tasks
from data_analysis_agent.reviews.monitor_store import init_monitoring


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--origin', required=True)
    args = parser.parse_args()
    os.environ.update(LLM_MODEL_ID='SIMULATED-ENGINEERING', LLM_API_KEY='offline-not-a-real-key',
        LLM_BASE_URL='http://127.0.0.1:9/v1', LABEL_EXECUTION_MODE='local', AGENT_SEMANTIC_PARSING='rules_only')
    case = ReviewTaskCompletionTests(); case.setUp()
    tasks = case.root / 'browser-tasks.sqlite3'
    init_tasks(tasks, case.db); init_monitoring(case.db)
    env = case.root / 'offline.env'; env.write_text('', encoding='utf-8')
    metrics = {'classification_calls': 0, 'sdk_attempts': 0, 'embedding_attempts': 0}
    def classify(*args, **kwargs):
        metrics['classification_calls'] += 1
        return synthetic_classifier(*args, **kwargs)
    def blocked_model(*args, **kwargs):
        metrics['sdk_attempts'] += 1
        raise AssertionError('external models are forbidden')
    def blocked_embedding(*args, **kwargs):
        metrics['embedding_attempts'] += 1
        raise AssertionError('embeddings are forbidden')
    try:
        with patch('openai.OpenAI', side_effect=blocked_model), \
             patch('data_analysis_agent.agents.review_retrieval.create_semantic_service', side_effect=blocked_embedding), \
             patch('data_analysis_agent.agents.review_topics._call', side_effect=classify), \
             patch('data_analysis_agent.agents.review_answers.AnswerGenerationStrategy.choose', return_value='template'):
            app = create_app(case.db, tasks, import_root=case.root, env_file=env, allowed_origins=[args.origin])
            @app.get('/offline-metrics')
            def offline_metrics():
                return metrics
            uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')
    finally:
        case.doCleanups()


if __name__ == '__main__':
    main()
