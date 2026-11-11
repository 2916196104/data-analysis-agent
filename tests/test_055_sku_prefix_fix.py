"""Synthetic scope tests; no real catalog, database, model, or retrieval."""
import unittest
from copy import deepcopy

from data_analysis_agent.agents.review_reported_facts import target
from data_analysis_agent.agents.review_task_contract import evidence_question
from data_analysis_agent.agents.review_semantics import match_product_mentions, product_mentions, QuestionScopeError


class SkuPrefixTests(unittest.TestCase):
    sku='100013588055'
    scope={'product_ids':[sku], 'date_from':'2024-01-01', 'date_to':'2024-01-31'}
    proposition='有没有送货慢的反馈？'

    def test_single_bound_sku_prefix_variants(self):
        for question in ('商品100013588055有没有送货慢的反馈？',
                         '商品 100013588055 有没有送货慢的反馈？',
                         '100013588055有没有送货慢的反馈？'):
            with self.subTest(question=question):
                snapshot=deepcopy(self.scope)
                normalized=evidence_question(question,self.scope)
                self.assertEqual(normalized,self.proposition)
                self.assertEqual(target(normalized)['facts'],['delivery_slow'])
                self.assertEqual(self.scope,snapshot)

    def test_other_valid_sku_works_without_hardcoding(self):
        scope={**self.scope,'product_ids':['987654321234']}
        self.assertEqual(evidence_question('商品987654321234有没有送货慢的反馈？',scope),self.proposition)

    def test_missing_conflicting_or_multi_scope_does_not_strip(self):
        q='商品100013588055有没有送货慢的反馈？'
        for scope in (None,{}, {'product_ids':[]}, {'product_ids':['other']},
                      {'product_ids':['100013588055','100013588053']}):
            with self.subTest(scope=scope):
                self.assertEqual(evidence_question(q,scope),q)
                self.assertIsNone(target(evidence_question(q,scope)))

    def test_unknown_sku_rejected_by_catalog_binding(self):
        products=[{'product_id':self.sku,'title':'合成商品','brand':'合成'}]
        q='商品999999999999有没有送货慢的反馈？'
        with self.assertRaises(QuestionScopeError):
            match_product_mentions(product_mentions(q,products),products,selected_ids=[self.sku])

    def test_multisku_middle_and_bad_prefix_remain_unparsed(self):
        for q in ('商品100013588055和100013588053有没有送货慢的反馈？',
                  '有没有商品100013588055送货慢的反馈？',
                  '商品1000135880559有没有送货慢的反馈？',
                  '商品100013588055-有没有送货慢的反馈？',
                  '不是商品100013588055有没有送货慢的反馈？'):
            with self.subTest(q=q):
                self.assertEqual(evidence_question(q,self.scope),q)
                self.assertIsNone(target(evidence_question(q,self.scope)))

    def test_conditions_not_silently_removed(self):
        for suffix in ('有没有并非送货慢的反馈？','有没有去年送货慢的反馈？',
                       '有没有三条送货慢的反馈？','有没有比100013588053送货慢的反馈？'):
            q='商品'+self.sku+suffix
            with self.subTest(q=q):self.assertEqual(evidence_question(q,self.scope),q)

    def test_date_prefix_only_with_matching_bound_dates(self):
        q='2024-01-01到2024-01-31 商品 100013588055 有没有送货慢的反馈？'
        self.assertEqual(evidence_question(q,self.scope),self.proposition)
        wrong='2023-01-01到2023-01-31 商品100013588055有没有送货慢的反馈？'
        self.assertEqual(evidence_question(wrong,self.scope),'商品100013588055有没有送货慢的反馈？')

    def test_original_unprefixed_proposition(self):
        self.assertEqual(evidence_question(self.proposition,self.scope),self.proposition)
        self.assertEqual(target(self.proposition)['facts'],['delivery_slow'])


if __name__=='__main__': unittest.main()
