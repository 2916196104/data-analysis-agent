"""仅测试进程：真实 API/Worker、临时 SQLite、固定供应商响应，禁止非本机网络。"""
import argparse
import socket
from unittest.mock import patch
import uvicorn

from test_p6d_publication import PublicationFixture
from data_analysis_agent.api.app import create_app
from data_analysis_agent.reviews.monitor_store import init_monitoring
from data_analysis_agent.reviews.human_review import init_workbench


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--origin', required=True)
    args = parser.parse_args()
    original_connect = socket.socket.connect
    fixture = PublicationFixture()
    case = fixture.case
    state = {'mode': 'many', 'external_network_attempts': 0, 'sdk_attempts': 0, 'embedding_attempts': 0}

    def blocked_model(*args, **kwargs):
        state['sdk_attempts'] += 1
        raise AssertionError('P6-D forbids external models')

    def blocked_embedding(*args, **kwargs):
        state['embedding_attempts'] += 1
        raise AssertionError('P6-D forbids embeddings')

    def local_connect(sock, address):
        if not isinstance(address, tuple) or address[0] not in ('127.0.0.1', 'localhost', '::1'):
            state['external_network_attempts'] += 1
            raise AssertionError('P6-D E2E forbids external network')
        return original_connect(sock, address)

    init_monitoring(case.fixture.db)
    init_workbench(case.fixture.db)
    env = case.fixture.root / 'offline.env'
    env.write_text('', encoding='utf-8')
    try:
        with patch('socket.socket.connect', local_connect), \
             patch('openai.OpenAI', side_effect=blocked_model), \
             patch('data_analysis_agent.agents.reviews.create_llm', side_effect=blocked_model), \
             patch('data_analysis_agent.agents.review_retrieval.create_semantic_service', side_effect=blocked_embedding), \
             patch('data_analysis_agent.jobs.worker.configured_agent_identity', return_value=(fixture.settings, case.fixture.agent_identity)), \
             patch('data_analysis_agent.jobs.submission.configured_agent_identity', return_value=(fixture.settings, case.fixture.agent_identity)), \
             patch('data_analysis_agent.agents.review_budget.make_execution_budget', side_effect=lambda _: fixture.budget(state['mode'])):
            app = create_app(case.fixture.db, case.fixture.tasks, import_root=case.fixture.root,
                env_file=env, allowed_origins=[args.origin])

            @app.post('/offline-scenario/{mode}')
            def scenario(mode: str):
                if mode not in ('complete', 'many', 'fail_service', 'fail_effectiveness', 'fail_logistics', 'empty_effectiveness', 'empty', 'budget'):
                    raise ValueError('unknown offline scenario')
                state['mode'] = mode
                return state

            @app.get('/offline-metrics')
            def metrics():
                return {'external_network_attempts': state['external_network_attempts'],
                    'paid_model_calls': state['sdk_attempts'], 'embedding_calls': state['embedding_attempts'], 'database': 'temporary SQLite only',
                    'simulated_provider_calls': len(case.calls)}

            uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')
    finally: fixture.close()


if __name__ == '__main__': main()
