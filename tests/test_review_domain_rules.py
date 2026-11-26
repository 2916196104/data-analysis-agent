"""版本化领域规则配置加载、强校验与通用多 Query 扩展回归测试。"""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from data_analysis_agent.agents.review_domain_rules import (
    DEFAULT_RULES_FILENAME,
    SCHEMA_VERSION,
    DomainRulesBundle,
    domain_rules,
    load_domain_rules,
)
from data_analysis_agent.agents.review_retrieval import (
    QUERY_EXPANSION_VERSION,
    SLOW_QUERY_VARIANTS,
    query_variants_for_fact_request,
    retrieval_query_variants,
)
from data_analysis_agent.core.config import AgentSettings
from data_analysis_agent.core.serialization import content_sha256

RULES_PATH = (
    Path(__file__).resolve().parents[1]
    / 'src/data_analysis_agent/agents/rules'
    / DEFAULT_RULES_FILENAME
)


class DomainRulesTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads(RULES_PATH.read_text(encoding='utf-8'))

    def _write_and_load(self, payload):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'rules.json'
            target.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            return load_domain_rules(target)

    def test_default_rules_load_with_version_and_stable_digest(self):
        bundle = domain_rules()
        self.assertEqual(bundle.schema_version, SCHEMA_VERSION)
        self.assertEqual(bundle.rule_version, '2026.11-v1')
        self.assertEqual(bundle.rules_sha256, content_sha256(self.raw))
        self.assertEqual(QUERY_EXPANSION_VERSION, 'query-expansion-v1')
        self.assertEqual(
            SLOW_QUERY_VARIANTS,
            ('有没有制冷速度迟缓的反馈？', '有没有空调开了好一会儿才凉快的评论？'),
        )
        self.assertIs(domain_rules(), bundle)

    def test_rejects_duplicate_json_keys_and_extra_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / 'dup.json'
            duplicate.write_text('{"schema_version":"review-domain-rules-v1","schema_version":"x"}', encoding='utf-8')
            with self.assertRaises(ValueError):
                load_domain_rules(duplicate)

        extra = deepcopy(self.raw)
        extra['unexpected_section'] = {}
        with self.assertRaises(ValidationError):
            self._write_and_load(extra)

    def test_rejects_invalid_regex_dangling_references_and_bad_bounds(self):
        bad_regex = deepcopy(self.raw)
        bad_regex['reported_facts']['patterns']['cooling_slow'] = '[unclosed'
        with self.assertRaises(ValidationError):
            self._write_and_load(bad_regex)

        bad_edge = deepcopy(self.raw)
        bad_edge['reported_facts']['entailment_edges']['cooling_failure'] = ['nonexistent_fact']
        with self.assertRaises(ValidationError):
            self._write_and_load(bad_edge)

        bad_opposite = deepcopy(self.raw)
        bad_opposite['reported_facts']['opposites'].append(['cooling_good', 'missing_fact'])
        with self.assertRaises(ValidationError):
            self._write_and_load(bad_opposite)

        bad_bounds = deepcopy(self.raw)
        bad_bounds['cooling_speed']['minute_bounds']['半个小时'] = [60, 30]
        with self.assertRaises(ValidationError):
            self._write_and_load(bad_bounds)

        bad_expansion = deepcopy(self.raw)
        bad_expansion['retrieval_query_expansion']['rules'][0]['fact_id'] = 'unknown_fact'
        with self.assertRaises(ValidationError):
            self._write_and_load(bad_expansion)

        bad_policy = deepcopy(self.raw)
        bad_policy['retrieval_query_expansion']['rules'][0]['fact_policy'] = 'unknown_policy_v99'
        with self.assertRaises(ValidationError):
            self._write_and_load(bad_policy)

    def test_generic_multi_query_expansion_works_for_non_cooling_domains_and_defaults_off(self):
        custom = deepcopy(self.raw)
        custom['retrieval_query_expansion']['rules'].append({
            'id': 'delivery_slow_v3',
            'fact_policy': 'reported_experience_v3',
            'fact_id': 'delivery_slow',
            'single_fact_only': True,
            'variants': [
                '有没有快递配送迟缓的反馈？',
                '有没有到货时间太久的评论？',
            ],
        })
        bundle = DomainRulesBundle.model_validate({**custom, 'rules_sha256': content_sha256(custom)})

        variants = query_variants_for_fact_request(
            '有没有物流很慢的反馈？',
            '有没有物流很慢的反馈？',
            'reported_experience_v3',
            rules=bundle,
        )
        self.assertEqual(variants, ('有没有快递配送迟缓的反馈？', '有没有到货时间太久的评论？'))

        # 复合关系不触发 single_fact_only 规则
        self.assertEqual(
            query_variants_for_fact_request(
                '有没有同一条评论既说物流很慢又说包装破损的反馈？',
                '有没有同一条评论既说物流很慢又说包装破损的反馈？',
                'reported_experience_v3',
                rules=bundle,
            ),
            (),
        )

        # 默认配置下 rag_multi_query 关闭，不产生额外 Query 编码开销
        default_settings = AgentSettings.from_values({
            'LLM_MODEL_ID': 'test',
            'LLM_API_KEY': 'test-key',
            'LLM_BASE_URL': 'https://offline.invalid/v1',
        })
        self.assertFalse(default_settings.rag_multi_query)
        scope = {'product_ids': ['P1'], 'date_from': '2023-01-01', 'date_to': '2023-01-31'}
        self.assertEqual(
            retrieval_query_variants('有没有制冷很慢的反馈？', scope, default_settings, rules=bundle),
            (),
        )
        enabled = replace(default_settings, rag_multi_query=True)
        self.assertEqual(
            retrieval_query_variants('有没有物流很慢的反馈？', scope, enabled, rules=bundle),
            ('有没有快递配送迟缓的反馈？', '有没有到货时间太久的评论？'),
        )


if __name__ == '__main__':
    unittest.main()
