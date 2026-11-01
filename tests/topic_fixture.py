"""离线主题模型夹具；仅用于验证真实工具、来源及计算，不用于生产归并。"""
from contextlib import contextmanager
from unittest.mock import patch

from data_analysis_agent.agents.review_topics import TopicCatalog, TopicBatch


def exact_topic_response(settings, prompt, payload, schema, timeout):
    """旧回归以精确表述提供受控语义响应，新语义案例另有预设同义分组。"""
    if schema is TopicCatalog:
        return TopicCatalog(themes=[{'key': f't{index + 1}', 'name': f'观点主题{index + 1}',
            'definition': row['quote'], 'support_ids': [row['opinion_id']]}
            for index, row in enumerate(payload['opinions'][:8])])
    return TopicBatch(assignments=[{'opinion_id': row['opinion_id'],
        'state': 'assigned' if any(theme['definition'] == row['quote'] for theme in payload['themes']) else 'unclassified',
        'matches': [{'key': theme['key'], 'start': 0, 'end': len(row['quote'])} for theme in payload['themes'] if theme['definition'] == row['quote']]}
        for row in payload['opinions']])


@contextmanager
def topic_model(response=exact_topic_response):
    with patch('data_analysis_agent.agents.review_topics._call', side_effect=response) as model:
        yield model
