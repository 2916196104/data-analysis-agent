"""P0 scope regression: in-memory catalog, mocked Agent boundary, no database or model."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import MagicMock, patch

from test_agent import SETTINGS
from data_analysis_agent.agents.review_intent import resolve_review_request
from data_analysis_agent.agents.review_semantics import (
    QuestionScopeError, match_product_mentions, product_mentions, resolve_question_scope,
)
from data_analysis_agent.agents.reviews import run_review_agent


PRODUCTS = [dict(product_id='100013588055', title='和风清风空调', brand='和风'),
            dict(product_id='100013588053', title='晨光明月空调', brand='晨光')]
SCOPE = dict(source='reviews_csv', dataset_version='offline-p0', product_ids=['100013588055'],
             model='offline', base_url='https://offline.invalid/v1', prompt_version='offline',
             date_from='2026-10-01', date_to='2026-10-31',
             baseline_from='2026-09-01', baseline_to='2026-09-30', as_of=None)
UNKNOWN_QUESTIONS = (
    '商品 XYZ-ABC123 有哪些差评？',
    '商品ID：XYZ-ABC123 有哪些差评？',
    '产品 XYZ123 有哪些差评？',
    'SKU=ABC-123-X 的差评有哪些？',
    '型号 KFR-35G 有哪些差评？',
    '商品 B0ZZZZZZZZ 有哪些差评？',
    '100013588055 商品 XYZ-ABC123 有哪些差评？',
    '商品 100013588055 和 XYZ-ABC123 有哪些差评？',
    '对比商品 100013588055 与 XYZ-ABC123 的差评',
    '商品 100013588055 和 XYZ-ABC123 有哪些优缺点？',
    '和风清风空调和商品 XYZ-ABC123 有哪些差评？',
    '商品 XYZ-ABC123 有没有送货慢的反馈？',
    '100013588055和XYZ-ABC123有哪些差评？',
    'XYZ-ABC123与100013588055分别有哪些差评？',
    '100013588055和XYZ-ABC123有哪些优缺点？',
    '和风清风空调和XYZ-ABC123有哪些差评？',
    'XYZ-ABC123与和风清风空调有哪些差评？',
    '商品“100013588055”和“XYZ-ABC123”有哪些差评？',
    '商品 XYZ-ABC123 参数有哪些差评？',
)
NON_ID_QUESTIONS = (
    '这款商品有哪些差评？', '那好评呢？',
    '商品使用 API 和 JSON 有哪些差评？',
    '商品参数 top_k=10、page_size=20 的差评统计',
    '商品 2026-10-12 的差评有哪些？',
    '商品 20261012 的差评有哪些？',
    '商品 v2.0 的差评有哪些？', '商品 v2 的差评有哪些？',
    '商品版本 2.0.1 的差评有哪些？',
    '商品 HTTP2 协议有哪些差评？', '商品 UTF-8 编码有哪些差评？',
)


def agent_at_boundary(question, products=PRODUCTS, context=None):
    """Only BEGIN/catalog reads are simulated; stop before constructing business tools."""
    connection = MagicMock()
    connection.__enter__.return_value = connection

    def execute(sql, parameters=()):
        if sql == 'BEGIN':
            return MagicMock()
        if sql == 'SELECT product_id,title,brand FROM products WHERE source=? AND dataset_version=?':
            return MagicMock(fetchall=lambda: deepcopy(products))
        raise AssertionError(f'Business SQL forbidden: {sql}')

    connection.execute.side_effect = execute
    with patch('data_analysis_agent.agents.reviews.connect_review_database', return_value=connection), \
         patch('data_analysis_agent.agents.review_tools.ReviewToolContext',
               side_effect=QuestionScopeError('offline execution boundary')) as boundary, \
         patch('data_analysis_agent.agents.review_tools.create_review_registry') as registry:
        result = run_review_agent('unused-offline-db', question, scope=deepcopy(SCOPE),
            settings=replace(SETTINGS, semantic_parser_enabled=True), conversation_context=context)
    registry.assert_not_called()
    return result, boundary.called


def trace_question(question):
    """Record extraction, matching, binding, Router plan and mocked Agent boundary separately."""
    trace = {'question': question, 'mode': 'offline_mock_catalog', 'real_database_execution': False}

    def record(name, operation):
        try:
            value = operation()
        except QuestionScopeError as exc:
            trace[name] = {'error': type(exc).__name__, 'message': str(exc)}
            return None
        trace[name] = value
        return value

    mentions = record('product_mentions', lambda: product_mentions(question, PRODUCTS))
    record('match_product_mentions', lambda: match_product_mentions(mentions, PRODUCTS,
        selected_ids=SCOPE['product_ids']))
    record('resolve_question_scope', lambda: resolve_question_scope(question, deepcopy(SCOPE), PRODUCTS))

    def resolve():
        bound, plan, direct = resolve_review_request(question, deepcopy(SCOPE), PRODUCTS, settings=SETTINGS)
        return {'scope': bound, 'query_plan': plan.to_dict(), 'semantic_direct': direct}

    resolved = record('resolve_review_request', resolve)
    result, reached = agent_at_boundary(question)
    trace.update(final_scope=resolved['scope'] if resolved else None,
                 query_plan=resolved['query_plan'] if resolved else None,
                 execution_boundary_reached=reached, business_tool_executions=result['query_executions'],
                 agent_status=result['status'], agent_answer=result.get('answer'),
                 agent_selection_scope=result.get('scope'), agent_query_plan=result.get('query_plan'))
    return trace


class UnknownProductIdTests(unittest.TestCase):
    def setUp(self):
        for target in ('sqlite3.connect', 'psycopg.connect', 'socket.socket.connect', 'openai.OpenAI',
                       'data_analysis_agent.agents.reviews.create_llm',
                       'data_analysis_agent.agents.review_intent._llm_intent',
                       'data_analysis_agent.agents.review_retrieval.create_semantic_service'):
            self.enterContext(patch(target, side_effect=AssertionError(f'Offline only: {target}')))

    def test_a_unknown_id_is_extracted_matched_and_blocked_by_scope(self):
        question = UNKNOWN_QUESTIONS[0]
        self.assertEqual(product_mentions(question, PRODUCTS), ['XYZ-ABC123'])
        with self.assertRaisesRegex(QuestionScopeError, 'XYZ-ABC123'):
            match_product_mentions(product_mentions(question, PRODUCTS), PRODUCTS)
        with self.assertRaisesRegex(QuestionScopeError, 'XYZ-ABC123'):
            resolve_question_scope(question, deepcopy(SCOPE), PRODUCTS)

    def test_explicit_identifier_labels_and_delimiters(self):
        for label in ('商品', '商品ID', '商品 ID', '商品id', '产品', '产品ID', 'SKU', 'sku', '型号'):
            for gap in (' ', '：', ': ', '=', '为', '是', '“'):
                with self.subTest(label=label, gap=gap):
                    self.assertEqual(product_mentions(f'{label}{gap}XYZ-ABC123”有哪些差评？', PRODUCTS),
                                     ['XYZ-ABC123'])

    def test_unknown_targets_block_router_including_comparison_balanced_and_rag(self):
        for question in UNKNOWN_QUESTIONS:
            with self.subTest(question=question), self.assertRaises(QuestionScopeError):
                resolve_review_request(question, deepcopy(SCOPE), PRODUCTS, settings=SETTINGS)

    def test_unknown_targets_block_agent_before_tools_and_models(self):
        for question in UNKNOWN_QUESTIONS:
            with self.subTest(question=question):
                result, reached = agent_at_boundary(question)
                self.assertFalse(reached)
                self.assertEqual(result['status'], 'unsupported')
                self.assertTrue(result['user_intent']['needs_clarification'])
                self.assertNotIn('query_plan', result)
                self.assertEqual(result['query_executions'], 0)
                self.assertEqual(result['total_model_calls'], 0)
                self.assertEqual(result['results'], {})

    def test_b_valid_other_id_requires_range_switch(self):
        question = '商品 100013588053 有哪些差评？'
        self.assertEqual(match_product_mentions(product_mentions(question, PRODUCTS), PRODUCTS), ['100013588053'])
        with self.assertRaisesRegex(QuestionScopeError, '确认切换'):
            resolve_review_request(question, deepcopy(SCOPE), PRODUCTS, settings=SETTINGS)
        result, reached = agent_at_boundary(question)
        self.assertFalse(reached)
        self.assertIn('确认切换', result['answer'])
        bound, plan, _ = resolve_review_request(question, {**SCOPE, 'product_ids': ['100013588053']},
                                               PRODUCTS, settings=SETTINGS)
        self.assertEqual(bound['product_ids'], ['100013588053'])
        self.assertEqual(plan.required_tools, ['get_top_issues'])

    def test_c_generic_product_inherits_055(self):
        question = '这款商品有哪些差评？'
        self.assertEqual(product_mentions(question, PRODUCTS), [])
        self.assertEqual(resolve_question_scope(question, deepcopy(SCOPE), PRODUCTS), SCOPE)
        bound, plan, _ = resolve_review_request(question, deepcopy(SCOPE), PRODUCTS, settings=SETTINGS)
        self.assertEqual(bound, SCOPE)
        self.assertEqual(plan.required_tools, ['get_top_issues'])
        result, reached = agent_at_boundary(question)
        self.assertTrue(reached)
        self.assertEqual(result['query_plan']['required_tools'], ['get_top_issues'])

    def test_d_sentiment_followup_inherits_product_and_previous_aspect(self):
        context = {'validated': {'task_id': 'prior', 'scope': deepcopy(SCOPE), 'analysis_type': 'issues',
            'sentiment': 'negative', 'queries': [{'kind': 'issues', 'parameters': {'aspect': 'logistics',
                'product_ids': ['100013588055'], 'sentiment': 'negative'}}]}}
        bound, plan, _ = resolve_review_request('那好评呢？', deepcopy(SCOPE), PRODUCTS, settings=SETTINGS,
                                               context=context)
        self.assertEqual(bound, SCOPE)
        self.assertEqual(plan.sentiment, 'positive')
        self.assertEqual(plan.required_tools, ['get_positive_aspects'])
        self.assertEqual(plan.requirements[0]['parameters']['aspect'], 'logistics')
        _, reached = agent_at_boundary('那好评呢？', context=context)
        self.assertTrue(reached)

    def test_e_unknown_asin_and_numeric_id_never_match_titles(self):
        for identifier in ('B0ZZZZZZZZ', '999999999999', 'XYZ-ABC123', 'ABC123'):
            products = [dict(product_id='100013588055', title=f'{identifier} 测试空调', brand='测试')]
            with self.subTest(identifier=identifier):
                with self.assertRaises(QuestionScopeError):
                    match_product_mentions([identifier], products)
                with self.assertRaises(QuestionScopeError):
                    resolve_review_request(f'商品 {identifier} 有哪些差评？', deepcopy(SCOPE), products,
                                           settings=SETTINGS)

    def test_g_terms_parameters_dates_versions_are_not_identifiers(self):
        for question in NON_ID_QUESTIONS:
            with self.subTest(question=question):
                self.assertEqual(product_mentions(question, PRODUCTS), [])
                self.assertEqual(resolve_question_scope(question, deepcopy(SCOPE), PRODUCTS), SCOPE)

    def test_g_router_keeps_regular_english_and_date_version_queries(self):
        for question in ('商品使用 API 有哪些差评？', '商品参数 top_k=10 有哪些差评？',
                         '2026-10-12 的差评有哪些？', '商品 v2.0 有哪些差评？'):
            with self.subTest(question=question):
                bound, plan, _ = resolve_review_request(question, deepcopy(SCOPE), PRODUCTS, settings=SETTINGS)
                self.assertEqual(bound, SCOPE)
                self.assertEqual(plan.required_tools, ['get_top_issues'])

    def test_existing_numeric_asin_and_alphanumeric_ids_match_exactly(self):
        for identifier in ('100013588055', 'B012345678', 'ABC123', 'ABC-123-X', 'P1'):
            products = [dict(product_id=identifier, title='和风清风空调', brand='和风')]
            scope = {**SCOPE, 'product_ids': [identifier]}
            with self.subTest(identifier=identifier):
                bound, plan, _ = resolve_review_request(f'商品 {identifier} 有哪些差评？', scope, products,
                                                       settings=SETTINGS)
                self.assertEqual(bound['product_ids'], [identifier])
                self.assertEqual(plan.required_tools, ['get_top_issues'])

    def test_existing_product_titles_aliases_and_comparison_still_work(self):
        for question, expected in (('和风清风空调有哪些差评？', ['100013588055']),
                                   ('和风空调有哪些差评？', ['100013588055']),
                                   ('比较和风清风空调与晨光明月空调的差评', ['100013588055', '100013588053']),
                                   ('比较商品 100013588055 和 100013588053 的差评', ['100013588055', '100013588053'])):
            with self.subTest(question=question):
                bound, plan, _ = resolve_review_request(question, deepcopy(SCOPE), PRODUCTS, settings=SETTINGS)
                self.assertEqual(bound['product_ids'], expected)
                self.assertEqual(plan.required_tools, ['compare_products'] if len(expected) == 2 else ['get_top_issues'])

    def test_identifier_with_numeric_tail_does_not_emit_partial_numeric_id(self):
        for identifier in ('B012345678', 'ABC-123456789', 'ABC123456789'):
            products = [dict(product_id=identifier, title='测试商品', brand='测试')]
            with self.subTest(identifier=identifier):
                self.assertEqual(product_mentions(f'商品 {identifier} 有哪些差评？', products), [identifier])

    def test_known_id_is_not_a_prefix_match_for_another_identifier(self):
        with self.assertRaises(QuestionScopeError):
            resolve_review_request('商品 100013588055-ABC123 有哪些差评？', deepcopy(SCOPE), PRODUCTS,
                                   settings=SETTINGS)

    def test_case_changes_are_not_fuzzy_identifier_matches(self):
        products = [dict(product_id='ABC123', title='ABC123 测试商品', brand='测试')]
        with self.assertRaises(QuestionScopeError):
            resolve_review_request('商品 abc123 有哪些差评？', {**SCOPE, 'product_ids': ['ABC123']}, products,
                                   settings=SETTINGS)

    def test_unknown_id_overrides_previous_validated_context(self):
        context = {'validated': {'scope': deepcopy(SCOPE), 'analysis_type': 'issues',
            'queries': [{'kind': 'issues', 'parameters': {'aspect': 'logistics'}}]}}
        result, reached = agent_at_boundary(UNKNOWN_QUESTIONS[0], context=context)
        self.assertFalse(reached)
        self.assertEqual(result['status'], 'unsupported')
        self.assertNotIn('query_plan', result)

    def test_exact_complex_catalog_title_keeps_its_model_text(self):
        products = [dict(product_id='100013588055', title='和风 KFR-35G/W（新风与净化）空调', brand='和风')]
        bound, plan, _ = resolve_review_request('和风 KFR-35G/W（新风与净化）空调有哪些差评？',
            deepcopy(SCOPE), products, settings=SETTINGS)
        self.assertEqual(bound, SCOPE)
        self.assertEqual(plan.required_tools, ['get_top_issues'])

    def test_confirmed_query_original_question_cannot_bypass_scope_checks(self):
        for original in (UNKNOWN_QUESTIONS[0], '商品 100013588053 有哪些差评？'):
            for escaped in (True, False):
                question = '执行本轮已确认的只读查询\n' + json.dumps(
                    {'tool': 'get_top_issues', 'parameters': {}, 'question': original}, ensure_ascii=escaped)
                with self.subTest(original=original, escaped=escaped):
                    with self.assertRaises(QuestionScopeError):
                        resolve_review_request(question, deepcopy(SCOPE), PRODUCTS, settings=SETTINGS)
                    result, reached = agent_at_boundary(question)
                    self.assertFalse(reached)
                    self.assertEqual(result['status'], 'unsupported')
                    self.assertEqual(result['query_executions'], 0)

    def test_valid_confirmed_query_keeps_original_plan_and_parameters(self):
        payload = {'tool': 'get_top_issues', 'parameters': {'aspect': 'logistics'},
                   'question': '商品 100013588055 有哪些差评？'}
        question = '执行本轮已确认的只读查询\n' + json.dumps(payload)
        bound, plan, _ = resolve_review_request(question, deepcopy(SCOPE), PRODUCTS, settings=SETTINGS)
        self.assertEqual(bound, SCOPE)
        self.assertEqual(plan.requirements, [{'tool': payload['tool'], 'parameters': payload['parameters']}])
        self.assertEqual(plan.answer_question, payload['question'])

    def test_code_shaped_exact_title_and_brand_remain_valid_names(self):
        for question, title in (('Phone15有哪些差评？', 'Phone15'), ('3M有哪些差评？', '3M Phone15')):
            products = [dict(product_id='100013588055', title=title, brand='3M')]
            with self.subTest(question=question):
                bound, plan, _ = resolve_review_request(question, deepcopy(SCOPE), products, settings=SETTINGS)
                self.assertEqual(bound, SCOPE)
                self.assertEqual(plan.required_tools, ['get_top_issues'])

    def test_explicit_id_cannot_match_even_an_exact_code_shaped_title(self):
        products = [dict(product_id='100013588055', title='XYZ-ABC123', brand='测试')]
        for question in ('商品 XYZ-ABC123 有哪些差评？', '商品 XYZ-ABC123 有哪些优缺点？',
                         '商品 XYZ-ABC123 有没有送货慢的反馈？'):
            with self.subTest(question=question):
                with self.assertRaises(QuestionScopeError):
                    resolve_question_scope(question, deepcopy(SCOPE), products)
                with self.assertRaises(QuestionScopeError):
                    resolve_review_request(question, deepcopy(SCOPE), products, settings=SETTINGS)
                result, reached = agent_at_boundary(question, products=products)
                self.assertFalse(reached)
                self.assertEqual(result['query_executions'], 0)

    def test_bare_asin_and_numeric_id_cannot_be_reinterpreted_as_exact_titles(self):
        for identifier in ('B0ZZZZZZZZ', '999999999999'):
            products = [dict(product_id='100013588055', title=identifier, brand='测试')]
            with self.subTest(identifier=identifier):
                with self.assertRaises(QuestionScopeError):
                    match_product_mentions([identifier], products)
                with self.assertRaises(QuestionScopeError):
                    resolve_review_request(f'{identifier}有哪些差评？', deepcopy(SCOPE), products, settings=SETTINGS)


if __name__ == '__main__':
    unittest.main()
