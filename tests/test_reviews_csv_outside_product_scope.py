"""Explicit external identifiers must never fall back to a selected local SKU."""
import unittest
from data_analysis_agent.agents.review_semantics import QuestionScopeError, resolve_question_scope


class OutsideProductTests(unittest.TestCase):
    def setUp(self):
        self.products = [dict(product_id='100013588055', title='测试空调', brand='测试')]
        self.scope = dict(source='reviews_csv', dataset_version='reviews-csv', product_ids=['100013588055'],
                          date_from='2021-09-11', date_to='2022-08-26')

    def test_external_identifier_requires_clarification(self):
        for question in ('B019GBG0IE有没有送货慢的反馈？', '商品B019GBG0IE有没有物流慢的反馈？',
                         '100013588055和B019GBG0IE的差评如何？'):
            with self.subTest(question=question), self.assertRaises(QuestionScopeError):
                resolve_question_scope(question, self.scope, self.products)

    def test_catalog_identifier_in_other_source_is_still_supported(self):
        product = dict(product_id='B019GBG0IE', title='其他商品', brand='其他')
        scope = {**self.scope, 'source':'amazon_reviews_2023', 'dataset_version':'amazon-reviews-2023',
                 'product_ids':['B019GBG0IE']}
        self.assertEqual(resolve_question_scope('B019GBG0IE有没有送货慢的反馈？', scope, [product])['product_ids'],
                         ['B019GBG0IE'])

    def test_external_identifier_in_local_title_is_not_an_id_match(self):
        local = [dict(product_id='100013588055', title='B019GBG0IE 测试空调', brand='测试')]
        with self.assertRaises(QuestionScopeError):
            resolve_question_scope('B019GBG0IE有没有送货慢的反馈？', self.scope, local)

    def test_regular_local_product_is_unchanged(self):
        scope = resolve_question_scope('100013588055有没有送货慢的反馈？', self.scope, self.products)
        self.assertEqual((scope['source'], scope['dataset_version'], scope['product_ids']),
                         ('reviews_csv', 'reviews-csv', ['100013588055']))
