"""Synthetic-only review drafts. Test decisions never confirm a human mapping."""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

import test_p5_runtime_contract_equivalence as synthetic
from experiments import text_evidence_runtime_contract as compiler
from scripts import prepare_p5_typed_mapping_review as review
from data_analysis_agent.core.serialization import content_sha256, canonical_json


def origins_with_notes(suffix=''):
    records=synthetic.synthetic_records()
    for qid,record in records.items():
        record['rationale']=''.join(f'🧪合成依据{qid}第{i}句{suffix}。' for i in range(9))
    return compiler._make_origins(records,synthetic.SCOPE,kind='synthetic_fixture')


def opinions(material):
    for target in material['targets']:
        for row in target['conditions']:
            yield row['human_opinion']
        yield target['structural_review']['human_opinion']


class TypedMappingReviewTests(unittest.TestCase):
    def setUp(self):
        guards=ExitStack();self.addCleanup(guards.close)
        for name in ('socket.socket.connect','socket.create_connection','psycopg.connect','sqlite3.connect',
            'httpx.HTTPTransport.handle_request','httpx.AsyncHTTPTransport.handle_async_request','openai.OpenAI',
            'data_analysis_agent.reviews.embedding.LocalBgeM3Embedder',
            'data_analysis_agent.reviews.semantic_retrieval.SemanticRetrievalService.search'):
            guards.enter_context(patch(name,side_effect=AssertionError('offline_review_only')))
        self.origins=origins_with_notes()
        self.material=review.build_material(self.origins)

    def test_complete_37_conditions_eight_structures_and_empty_opinions(self):
        self.assertEqual([len(t['conditions']) for t in self.material['targets']],[4,3,3,4,8,6,4,5])
        self.assertEqual(len(list(opinions(self.material))),45)
        self.assertTrue(all(o==dict(decision=None,comment=None) for o in opinions(self.material)))
        result=review.check_review(self.origins,self.material)
        self.assertEqual(result['filled_opinions'],0)
        self.assertEqual(set(result['target_states'].values()),{'pending_per_item_human_review'})
        self.assertFalse(result['mapping_human_confirmed'])
        self.assertEqual(result['four_state_review_admitted_targets'],[])

    def test_every_item_has_exact_unicode_source_slice_and_original_fact(self):
        data=compiler._origin_data(self.origins)
        for target in self.material['targets']:
            self.assertEqual(target['original_contract_revision'],1)
            for row in target['conditions']:
                self.assertTrue(row['tree_pointer'].startswith('/facts/'))
                self.assertTrue(row['potential_semantic_risk'])
                self.assertTrue(row['new_explicit_content'])
                self.assertTrue(row['original_basis'])
                for basis in row['original_basis']:
                    field=(data['records'][target['query_id']]['rationale'] if basis['sentence_index'] is not None
                           else row['original_fact']['description'])
                    self.assertEqual(field[basis['start']:basis['end']],basis['excerpt'])
                    self.assertEqual(content_sha256(field),basis['field_sha256'])
                    self.assertEqual(basis['coordinate_unit'],'unicode_code_point_in_json_field')

    def test_internal_or_negation_and_q18_relations_visible(self):
        target=self.material['targets'][0]
        impatient=next(r for r in target['conditions'] if r['predicate']=='impatient')
        self.assertEqual([a['operator'] for a in impatient['ancestors']],['and','or'])
        q06=next(t for t in self.material['targets'] if t['query_id']=='Q06')
        delay=next(r for r in q06['conditions'] if r['predicate']=='clear_delivery_delay')
        nonreceipt=next(r for r in q06['conditions'] if r['predicate']=='prolonged_nonreceipt')
        self.assertFalse(delay['applicable_negations']);self.assertTrue(nonreceipt['applicable_negations'])
        q18=self.material['targets'][-1]
        self.assertEqual([(f['id'],f['sentiment']) for f in q18['original_facts']],[('f0','neutral'),('f1','negative')])
        self.assertEqual([r['kind'] for r in q18['relations']],['same_review','before'])
        self.assertIn('before',q18['structural_review']['focus'])

    def test_all_accept_still_cannot_issue_owner_confirmation_or_permission(self):
        for opinion in opinions(self.material):opinion['decision']='accept'
        result=review.check_review(self.origins,self.material)
        self.assertEqual(result['filled_opinions'],45)
        self.assertEqual(set(result['target_states'].values()),{'awaiting_explicit_owner_confirmation'})
        for name in ('mapping_human_confirmed','p0_binding_present','publication_allowed','semantic_verified'):
            self.assertFalse(result[name])
        self.assertEqual(result['incremental_human_confirmation'],'not_issued')
        self.assertEqual(result['executable_requests'],0)
        self.assertEqual(result['four_state_review_admitted_targets'],[])

    def test_revision_or_rejection_blocks_target_without_author_attribution(self):
        row=self.material['targets'][0]['conditions'][0]
        row['human_opinion']=dict(decision='needs_revision',comment='SYNTHETIC proposed change')
        result=review.check_review(self.origins,self.material)
        self.assertEqual(result['target_states']['Q02'],'semantic_difference_requires_revision')
        self.assertEqual(result['opinion_actor'],'unattributed_file_draft')
        self.assertFalse(result['mapping_human_confirmed'])

    def test_revision_requires_explanation_and_unsupported_opinion_keys_rejected(self):
        for opinion in (dict(decision='needs_revision',comment=None),dict(decision='reject',comment=' '),
                        dict(decision='accept',comment=None,actor='owner'),dict(decision=True,comment=None)):
            value=deepcopy(self.material);value['targets'][0]['conditions'][0]['human_opinion']=opinion
            with self.assertRaises(review.ReviewError):review.check_review(self.origins,value)

    def test_fake_confirmed_flags_and_fabricated_final_confirmation_rejected(self):
        for name,value in [('mapping_human_confirmed',True),('human_final_confirmation',{'actor':'owner','confirmed':True}),
                           ('semantic_verified',True),('publication_allowed',True),('permitted_target_fact_ids',['f0'])]:
            edited=deepcopy(self.material);edited[name]=value
            with self.assertRaises(review.ReviewError):review.check_review(self.origins,edited)

    def test_recomputed_self_hash_does_not_hide_changed_conditions(self):
        edited=deepcopy(self.material)
        edited['targets'][0]['conditions'][0]['meaning']='changed goal'
        edited['material_sha256']=review.material_digest(edited)
        with self.assertRaisesRegex(review.ReviewError,'stale_or_changed_review_material'):
            review.check_review(self.origins,edited)

    def test_source_drift_invalidates_all_accepted_draft(self):
        for opinion in opinions(self.material):opinion['decision']='accept'
        new_origins=origins_with_notes('changed')
        with self.assertRaisesRegex(review.ReviewError,'stale_or_changed_review_material'):
            review.check_review(new_origins,self.material)

    def test_same_predicate_graph_but_changed_rendered_meaning_invalidates_review(self):
        changed=dict(compiler.LABELS);changed['customer_support']+='（合成变更）'
        with patch.object(compiler,'LABELS',changed):
            with self.assertRaisesRegex(review.ReviewError,'stale_or_changed_review_material'):
                review.check_review(self.origins,self.material)

    def test_material_version_original_revision_and_code_drift_rejected(self):
        for key,value in [('version','other'),('material_revision',2)]:
            edited=deepcopy(self.material);edited[key]=value
            with self.assertRaises(review.ReviewError):review.check_review(self.origins,edited)
        edited=deepcopy(self.material);edited['targets'][0]['original_contract_revision']=2
        with self.assertRaises(review.ReviewError):review.check_review(self.origins,edited)
        with patch.object(review,'code_fingerprints',return_value={'changed.py':'0'*64}):
            with self.assertRaises(review.ReviewError):review.check_review(self.origins,self.material)

    def test_original_basis_or_tree_change_rejected(self):
        for field,value in [('start',99),('excerpt','invented'),('json_pointer','/invented')]:
            edited=deepcopy(self.material);edited['targets'][0]['conditions'][0]['original_basis'][0][field]=value
            with self.assertRaises(review.ReviewError):review.check_review(self.origins,edited)
        edited=deepcopy(self.material);edited['targets'][0]['conditions'].pop()
        with self.assertRaises(review.ReviewError):review.check_review(self.origins,edited)

    def test_every_opinion_and_comment_bound_to_final_proposal_digest(self):
        before=review.check_review(self.origins,self.material)
        for opinion in opinions(self.material):opinion['decision']='accept'
        accepted=review.check_review(self.origins,self.material)
        self.assertNotEqual(before['opinions_sha256'],accepted['opinions_sha256'])
        self.assertEqual(before['material_sha256'],accepted['material_sha256'])
        list(opinions(self.material))[-1]['comment']='SYNTHETIC additional note'
        after=review.check_review(self.origins,self.material)
        self.assertNotEqual(accepted['opinions_sha256'],after['opinions_sha256'])
        self.assertEqual(len(after['per_item_opinions']),45)

    def test_markdown_contains_all_items_and_no_preselected_human_opinion(self):
        md=review.render_markdown(self.material)
        for target in self.material['targets']:
            for row in target['conditions']:self.assertIn('### '+row['condition_id'],md)
        self.assertEqual(md.count('### Q'),37)
        self.assertIn('Unicode字符串',md)
        self.assertIn('全部accept仍须本人',md)
        self.assertLess(len(md.encode()),review.MAX_BYTES)
        self.assertLess(len(canonical_json(self.material).encode()),review.MAX_BYTES)

    def test_old_runtime_gate_and_formal_paths_remain_isolated(self):
        target=compiler.compile_target(self.origins,compiler.propose_spec(self.origins,'Q02'))
        self.assertFalse(target.report()['mapping_human_confirmed'])
        with self.assertRaises(compiler.EquivalenceError):compiler.require_executable(target)
        for path in Path('src').rglob('*.py'):
            self.assertNotIn('prepare_p5_typed_mapping_review',path.read_text(encoding='utf-8'))
        self.assertFalse(hasattr(review,'confirm'))
        self.assertFalse(hasattr(review,'issue_confirmation'))


if __name__=='__main__':
    unittest.main()
