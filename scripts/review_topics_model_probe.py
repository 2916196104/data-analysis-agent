"""仅向配置模型发送隔离合成观点，验证主题归并；绝不连接真实数据库。"""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys

from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.core.config import AgentSettings, read_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # 复用验收夹具创建临时 SQLite，真实评论库和 DATABASE_URL 均不传入执行器。
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
    from test_review_topics import ReviewTopicTests
    fixture = ReviewTopicTests(); fixture.setUp()
    try:
        with redirect_stdout(io.StringIO()):
            result = run_review_agent(fixture.db, '使用效果的好评主要集中在哪些地方？', scope=fixture.scope(),
                                      settings=AgentSettings.from_values(read_settings()))
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        data = next((row['data'] for row in result['results'].values() if row['kind']=='aspect_drilldown'), {})
        print(json.dumps(dict(source='SYNTHETIC_SQLITE_ONLY',status=result['status'],validation=result.get('validation',{}).get('status'),
            elapsed_seconds=result['elapsed_seconds'],topic_model_calls=result['topic_model_calls'],
            topics=[{key:row[key] for key in ('name','review_count','opinion_count')} for row in data.get('topics',[])],
            coverage=data.get('coverage'),failures=data.get('failures')),ensure_ascii=False))
    finally:
        fixture.tearDown()


if __name__ == '__main__':
    main()
