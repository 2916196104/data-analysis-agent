"""完全合成的显式陈述、来源和关系测试；不读取真实评测或人工标签。"""

from copy import deepcopy
from dataclasses import asdict
import json
import unittest
from unittest.mock import patch

from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.evidence_judge import (
    INPUT_VERSION, EvidenceBatch, SourceValidationError, judge_evidence, validate_judge_result,
)
from data_analysis_agent.reviews.query_contract import ScopeRef
from data_analysis_agent.reviews.rag_corpus import _document


SCOPE = ScopeRef(run_id=content_sha256('judge-synthetic-run'), manifest_sha256=content_sha256('judge-synthetic-questions'),
                 scope_sha256=content_sha256('judge-synthetic-scope'))


def contract(descriptions=('客服回复及时',), *, sentiments=None, **changes):
    value = dict(version='rag-query-contract-v1', task_type='evidence', sentiment_intent='positive',
        target_entity='customer_support', subtopic='response_time', scope_ref=SCOPE.model_dump(mode='json'),
        aspect_candidates=[dict(aspect='service', rationale='合成查询明确对象')], proposed_filters={},
        required_facts=dict(operator='all_of', items=[dict(id=f'f{i}', description=text,
            sentiment=(sentiments[i] if sentiments else None)) for i, text in enumerate(descriptions)], relations=[]))
    value.update(changes)
    return value


def batch(quotes=(), *, review_ids=None, originals=None, query_id='SYNTH-JUDGE-Z', mode='unfiltered'):
    candidates=[]
    for index, quote in enumerate(quotes):
        rid = review_ids[index] if review_ids else index + 1
        original = originals[index] if originals else quote
        row = dict(source='SYNTHETIC',dataset_version='synthetic-v1',product_id='SYNTHETIC-DEVICE-Z',
            review_id=rid,annotation_id=100+rid,model_opinion_id=500+index,human_opinion_id=None,
            human_revision_id=None,opinion_source='model',processing_status='success',
            quote=quote,quote_start=original.index(quote),quote_end=original.index(quote)+len(quote),review_text=original,
            aspect='service',sentiment='positive',review_content_sha256=content_sha256(original),
            published_at='2031-01-01T00:00:00Z',import_run_id=1,line_number=rid,
            file_sha256=content_sha256(['synthetic-file',rid]))
        doc = _document(row,('synthetic-model','http://offline.invalid','synthetic-prompt'),
                        content_sha256('synthetic-profile'))
        fields = asdict(doc)
        names = ('review_id','annotation_id','source','dataset_version','product_id','label_profile_key',
            'opinion_source','model_opinion_id','human_opinion_id','human_revision_id','aspect','sentiment',
            'quote','quote_start','quote_end','review_content_sha256','embedding_input_sha256','file_sha256',
            'line_number','evidence_ref','text_builder_version')
        candidates.append(dict(**{k:fields[k] for k in names},query_id=query_id,mode=mode,rank=index+1,
            key=doc.stable_versioned_opinion_key,review_text=original,evidence_valid=True,
            effective_snapshot_sha256=content_sha256(['synthetic-snapshot',rid,original])))
    return dict(version=INPUT_VERSION,scope_ref=SCOPE.model_dump(mode='json'),query_id=query_id,mode=mode,candidates=candidates)


class EvidenceJudgeTests(unittest.TestCase):
    def judge(self, c=None, b=None, *, trusted=None, independent=()):
        c = contract() if c is None else c
        b = batch(['客服回复及时']) if b is None else b
        result = judge_evidence(c,b,expected_scope=SCOPE,trusted_batch=b if trusted is None else trusted,
                                independent_target_fact_ids=independent)
        if result.execution_status=='completed':
            self.assert_refs(result,c,b)
        else:
            self.assertIsNone(result.status)
            self.assertFalse(result.supported_target_fact_ids)
            self.assertFalse(result.evidence_refs)
        return result

    def assert_refs(self,result,c,b):
        if isinstance(b,str):b=json.loads(b)
        facts={f['id']:f for f in c['required_facts']['items']}
        by_key={h['key']:h for h in b['candidates']}
        self.assertEqual(set(r.fact_id for r in result.evidence_refs),set(result.supported_target_fact_ids))
        self.assertEqual(set(r.fact_id for r in result.condition_evidence_refs),set(result.matched_required_fact_ids))
        for ref in (*result.evidence_refs,*result.condition_evidence_refs):
            self.assertIn(ref.key,by_key)
            hit=by_key[ref.key]
            self.assertEqual((ref.query_id,ref.mode,ref.rank),(b['query_id'],b['mode'],hit['rank']))
            self.assertEqual(ref.review_id,hit['review_id'])
            self.assertEqual(ref.annotation_id,hit['annotation_id'])
            self.assertEqual(ref.effective_snapshot_sha256,hit['effective_snapshot_sha256'])
            self.assertEqual(ref.embedding_input_sha256,hit['embedding_input_sha256'])
            self.assertEqual(ref.evidence_ref,hit['evidence_ref'])
            self.assertGreaterEqual(ref.quote_start,hit['quote_start'])
            self.assertLessEqual(ref.quote_end,hit['quote_end'])
            self.assertEqual(hit['review_text'][ref.quote_start:ref.quote_end],facts[ref.fact_id]['description'].removesuffix('。'))
        self.assertIn('unknown_is_not_proof_of_absence',result.limitations)
        self.assertIn('candidate_evidence_only_not_population_statistics',result.limitations)

    def test_sufficient_direct_fact_and_exact_source_reference(self):
        r=self.judge()
        self.assertEqual((r.execution_status,r.status),('completed','sufficient'))
        self.assertEqual(r.supported_target_fact_ids,('f0',))
        self.assertFalse(r.missing_required_fact_ids)
        self.assertTrue(r.input_sha256)

    def test_missing_compound_core_part_does_not_become_limited(self):
        c=contract(('设备已到货','安装人员仍未上门'),sentiments=['neutral','negative'],
                   target_entity='multiple',subtopic='multiple',sentiment_intent='negative')
        r=self.judge(c,batch(['设备已到货']))
        self.assertEqual(r.status,'insufficient')
        self.assertEqual(r.matched_required_fact_ids,('f0',))
        self.assertEqual(r.missing_required_fact_ids,('f1',))
        self.assertFalse(r.evidence_refs)
        self.assertEqual(r.condition_evidence_refs[0].fact_id,'f0')

    def test_independent_subquestion_can_be_licensed_as_limited(self):
        c=contract(('设备制冷良好','设备制热良好'),target_entity='device',subtopic='multiple')
        r=self.judge(c,batch(['设备制冷良好']),independent=('f0','f1'))
        self.assertEqual(r.status,'limited')
        self.assertEqual(r.supported_target_fact_ids,('f0',))
        self.assertEqual(r.missing_required_fact_ids,('f1',))
        self.assertEqual(len(r.evidence_refs),1)

    def test_irrelevant_close_topic_or_opposite_sentiment_has_no_support(self):
        for text in ('物流人员配送及时','安装人员上门及时','客服回复不及时','完全无关的合成陈述'):
            r=self.judge(b=batch([text]))
            self.assertEqual(r.status,'insufficient')
            self.assertEqual(r.missing_required_fact_ids,('f0',))
            self.assertFalse(r.matched_required_fact_ids)

    def test_declared_entity_topic_and_sentiment_must_match_the_explicit_fact(self):
        for changes,reason in [(dict(target_entity='installer'),'target_entity_not_confirmed'),
                               (dict(subtopic='heating'),'subtopic_not_confirmed'),
                               (dict(sentiment_intent='negative'),'sentiment_not_confirmed')]:
            r=self.judge(contract(**changes))
            self.assertEqual(r.status,'insufficient');self.assertIn(reason,r.reason_codes)

    def test_subtopic_does_not_match_heating_to_cooling(self):
        c=contract(('设备制冷良好',),target_entity='device',subtopic='cooling')
        r=self.judge(c,batch(['设备制热良好']))
        self.assertEqual(r.status,'insufficient');self.assertFalse(r.evidence_refs)

    def test_lexical_overlap_hypothetical_reported_and_unknown_context_stays_unknown(self):
        for quote in ('如果客服回复及时就好了','据说客服回复及时','客服并非回复及时','“客服回复及时”是一个例句',
                      '客服回复及时，但是','客服回复及时。后续情况未知'):
            r=self.judge(b=batch([quote]))
            self.assertEqual(r.status,'insufficient')
            self.assertIn('unrecognized_clause_or_context',r.reason_codes)

    def test_quote_cannot_drop_a_negation_in_the_original_context(self):
        r=self.judge(b=batch(['客服回复及时'],originals=['并非客服回复及时']))
        self.assertEqual(r.status,'insufficient');self.assertFalse(r.evidence_refs)

    def test_original_outside_quote_is_not_borrowed_as_evidence(self):
        b=batch(['设备制冷良好'],originals=['设备制冷良好；客服回复及时'])
        r=self.judge(b=b)
        self.assertEqual(r.status,'insufficient');self.assertFalse(r.matched_required_fact_ids)

    def test_explicit_negation_scope_without_naive_polarity_flip(self):
        c=contract(('客服没有拖延回复',))
        c['polarity_structure']=dict(negations=[dict(fact_id='f0',cue='没有',scope='拖延回复')])
        r=self.judge(c,batch(['客服没有拖延回复']))
        self.assertEqual(r.status,'sufficient');self.assertEqual(r.supported_target_fact_ids,('f0',))
        c['polarity_structure']['negations'][0]['scope']='其他未知作用范围'
        r=self.judge(c,batch(['客服没有拖延回复']))
        self.assertEqual(r.status,'insufficient');self.assertIn('negation_scope_unknown',r.reason_codes)

    def test_any_of_needs_one_alternative_and_does_not_mark_others_mandatory(self):
        c=contract(('设备制冷良好','设备制热良好'),target_entity='device',subtopic='multiple')
        c['required_facts']['operator']='any_of'
        r=self.judge(c,batch(['设备制热良好']))
        self.assertEqual(r.status,'sufficient');self.assertEqual(r.supported_target_fact_ids,('f1',))
        self.assertFalse(r.missing_required_fact_ids)

    def test_all_of_direct_facts_supported_together(self):
        c=contract(('设备制冷良好','设备制热良好'),target_entity='device',subtopic='multiple')
        r=self.judge(c,batch(['设备制冷良好；设备制热良好']))
        self.assertEqual(r.status,'sufficient');self.assertEqual(len(r.evidence_refs),2)

    def before_contract(self):
        c=contract(('设备已到货','安装人员仍未上门'),sentiments=['neutral','negative'],
                   target_entity='multiple',subtopic='multiple',sentiment_intent='negative')
        c['required_facts']['relations']=[dict(kind='before',fact_ids=['f0','f1'])]
        return c

    def test_before_requires_explicit_connector_and_one_coherent_quote(self):
        r=self.judge(self.before_contract(),batch(['设备已到货，然后安装人员仍未上门']))
        self.assertEqual(r.status,'sufficient');self.assertFalse(r.missing_relation_ids)
        self.assertLess(r.evidence_refs[0].quote_end,r.evidence_refs[1].quote_start)

    def test_narrative_order_separate_quotes_and_reverse_order_do_not_prove_before(self):
        cases=[batch(['设备已到货；安装人员仍未上门']),
               batch(['安装人员仍未上门，然后设备已到货']),
               batch(['设备已到货','安装人员仍未上门'],review_ids=[1,1],
                     originals=['设备已到货；安装人员仍未上门']*2)]
        for b in cases:
            r=self.judge(self.before_contract(),b,independent=('f0','f1'))
            self.assertEqual(r.status,'insufficient');self.assertEqual(r.matched_required_fact_ids,('f0','f1'))
            self.assertTrue(r.missing_relation_ids);self.assertFalse(r.supported_target_fact_ids)

    def mixed_contract(self):
        c=contract(('设备制冷良好','设备噪声很大'),sentiments=['positive','negative'],
                   sentiment_intent='mixed',target_entity='device',subtopic='multiple')
        c['required_facts']['relations']=[dict(kind='same_review',fact_ids=['f0','f1'])]
        return c

    def test_same_review_mixed_and_explicit_contrast(self):
        c=self.mixed_contract();c['polarity_structure']=dict(contrast_fact_ids=['f0','f1'])
        r=self.judge(c,batch(['设备制冷良好，但是设备噪声很大']))
        self.assertEqual(r.status,'sufficient');self.assertEqual(len({x.review_id for x in r.evidence_refs}),1)
        r=self.judge(c,batch(['设备制冷良好；设备噪声很大']))
        self.assertEqual(r.status,'insufficient');self.assertIn('contrast',r.missing_relation_ids)

    def test_cross_review_positive_negative_splicing_is_rejected(self):
        r=self.judge(self.mixed_contract(),batch(['设备制冷良好','设备噪声很大']))
        self.assertEqual(r.status,'insufficient');self.assertTrue(r.missing_relation_ids)
        self.assertFalse(r.evidence_refs)

    def test_same_review_separate_opinions_can_support_cooccurrence(self):
        b=batch(['设备制冷良好','设备噪声很大'],review_ids=[8,8],originals=['设备制冷良好；设备噪声很大']*2)
        r=self.judge(self.mixed_contract(),b)
        self.assertEqual(r.status,'sufficient');self.assertEqual({x.review_id for x in r.evidence_refs},{8})

    def test_shared_relations_cannot_mix_incompatible_witnesses(self):
        c=contract(('设备已到货','安装人员仍未上门','设备短期内发生故障'),sentiments=['neutral','negative','negative'],
                   target_entity='multiple',subtopic='multiple',sentiment_intent='negative')
        c['required_facts']['relations']=[dict(kind='before',fact_ids=['f0','f1']),dict(kind='before',fact_ids=['f1','f2'])]
        r=self.judge(c,batch(['设备已到货，然后安装人员仍未上门','安装人员仍未上门，然后设备短期内发生故障']))
        self.assertEqual(r.status,'insufficient');self.assertEqual(len(r.matched_required_fact_ids),3)
        self.assertTrue(r.missing_relation_ids);self.assertFalse(r.evidence_refs)

    def test_joint_witness_can_use_later_full_quote_without_losing_it_to_deduplication(self):
        original='设备已到货，然后安装人员仍未上门'
        b=batch(['设备已到货','安装人员仍未上门',original],review_ids=[4,4,4],originals=[original]*3)
        r=self.judge(self.before_contract(),b)
        self.assertEqual(r.status,'sufficient');self.assertEqual({ref.rank for ref in r.evidence_refs},{3})

    def test_duplicates_never_create_extra_licensed_facts(self):
        r=self.judge(b=batch(['客服回复及时','客服回复及时'],review_ids=[1,1]))
        self.assertEqual(r.status,'sufficient');self.assertEqual(len(r.evidence_refs),1)
        b=batch(['客服回复及时']);b['candidates']*=2;b['candidates'][1]=deepcopy(b['candidates'][1]);b['candidates'][1]['rank']=2
        r=self.judge(b=b)
        self.assertEqual(r.execution_status,'validation_failed');self.assertIn('duplicate_candidate_key',r.reason_codes)

    def test_empty_candidates_return_insufficient_with_absence_limitation(self):
        r=self.judge(b=batch())
        self.assertEqual(r.status,'insufficient');self.assertEqual(r.missing_required_fact_ids,('f0',))
        self.assertIn('no_candidates_not_proof_of_absence',r.reason_codes)

    def test_noise_cannot_imply_sleep_and_early_fault_cannot_imply_durability(self):
        for description,quote,topic in [('噪声影响睡眠','设备噪声很大','sleep_impact'),
                                       ('设备长期不耐用','设备短期内发生故障','long_term_durability')]:
            r=self.judge(contract((description,),target_entity='device',subtopic=topic,sentiment_intent='negative'),batch([quote]))
            self.assertEqual(r.status,'insufficient');self.assertFalse(r.evidence_refs)

    def test_statistics_clarify_and_uncertain_queries_do_not_gain_sufficient(self):
        for changes in (dict(task_type='statistics'),dict(uncertainty=['合成：对象待确认']),
                        dict(task_type='clarify',clarification=['合成：统计还是举例？'])):
            r=self.judge(contract(**changes),independent=('f0',))
            self.assertEqual(r.status,'insufficient');self.assertFalse(r.evidence_refs)
        r=self.judge(contract(task_type='mixed'),independent=('f0',))
        self.assertEqual(r.status,'limited');self.assertIn('statistics_require_separate_evaluation',r.reason_codes)

    def test_stale_source_is_a_validation_failure_not_normal_insufficient(self):
        b=batch(['客服回复及时']);b['candidates'][0]['evidence_valid']=False
        r=self.judge(b=b)
        self.assertEqual(r.execution_status,'validation_failed');self.assertIn('invalid_or_stale_source',r.reason_codes)

    def test_source_span_hash_reference_and_stable_key_are_checked_even_against_bad_registry(self):
        mutations=[('source_span_mismatch',lambda h:h.update(quote_start=1)),
            ('source_quote_hash_mismatch',lambda h:h.update(embedding_input_sha256='0'*64)),
            ('source_ref_mismatch',lambda h:h.update(evidence_ref='sha256:wrong#line=1')),
            ('stable_source_key_mismatch',lambda h:h.update(key='rag-opinion-v1:'+'0'*64)),
            ('invalid_opinion_identity',lambda h:h.update(human_opinion_id=4))]
        for code,mutate in mutations:
            b=batch(['客服回复及时']);mutate(b['candidates'][0]);r=self.judge(b=b)
            self.assertEqual(r.execution_status,'validation_failed');self.assertIn(code,r.reason_codes)

    def test_input_scope_query_mode_and_rank_mismatches_fail(self):
        mutations=[lambda b:b['scope_ref'].update(scope_sha256='0'*64),
            lambda b:b['candidates'][0].update(query_id='another-query'),
            lambda b:b['candidates'][0].update(mode='filtered'),lambda b:b['candidates'][0].update(rank=2)]
        for mutate in mutations:
            b=batch(['客服回复及时']);mutate(b)
            self.assertEqual(self.judge(b=b).execution_status,'validation_failed')
        c=contract();c['scope_ref']['run_id']='0'*64
        self.assertEqual(self.judge(c).execution_status,'validation_failed')

    def test_current_ordered_list_cannot_be_tampered_dropped_or_borrowed(self):
        trusted=batch(['客服回复及时'])
        for mutate in [lambda b:b.update(candidates=[]),lambda b:b.update(query_id='another'),
                       lambda b:b['candidates'][0].update(effective_snapshot_sha256='0'*64),
                       lambda b:b['candidates'][0].update(annotation_id=999),
                       lambda b:b['candidates'][0].update(product_id='OTHER-SYNTHETIC')]:
            b=deepcopy(trusted);mutate(b)
            r=self.judge(b=b,trusted=trusted)
            self.assertEqual(r.execution_status,'validation_failed');self.assertIn('candidate_list_identity_mismatch',r.reason_codes)

    def test_conflicting_original_for_same_review_fails(self):
        b=batch(['客服回复及时','客服回复不及时'],review_ids=[1,1])
        r=self.judge(b=b)
        self.assertEqual(r.execution_status,'validation_failed');self.assertIn('conflicting_review_identity',r.reason_codes)

    def test_source_metadata_sentiment_does_not_replace_textual_fact_support(self):
        b=batch(['客服回复不及时'])  # fixture metadata is positive, actual clause negative
        r=self.judge(b=b)
        self.assertEqual(r.status,'insufficient')
        c=contract(('客服回复不及时',),sentiment_intent='negative')
        self.assertEqual(self.judge(c,b).status,'sufficient')

    def test_explicit_fact_polarity_cannot_override_opposite_query_polarity(self):
        r=self.judge(contract(sentiments=['positive'],sentiment_intent='negative'))
        self.assertEqual(r.status,'insufficient');self.assertIn('query_fact_polarity_conflict',r.reason_codes)

    def test_no_distance_or_grade_runtime_fields_are_accepted(self):
        for key,value in [('grade',2),('cosine_distance',0.00001),('reference_state','sufficient')]:
            b=batch(['客服回复及时']);b['candidates'][0][key]=value
            self.assertEqual(self.judge(b=b).execution_status,'validation_failed')

    def test_unknown_required_proposition_never_uses_substring_matching(self):
        r=self.judge(contract(('客服回复及时并解决了所有问题',)))
        self.assertEqual(r.status,'insufficient');self.assertIn('unrecognized_target_fact',r.reason_codes)

    def test_invalid_inputs_and_independent_target_declarations_fail_clearly(self):
        for b in ('{"version":1,"version":2}',[],True,{'version':'wrong'}):
            self.assertEqual(self.judge(b=b,trusted=batch()).execution_status,'validation_failed')
        for ids in [('not_in_contract',),('f0','f0'),('f0',1)]:
            self.assertEqual(self.judge(independent=ids).execution_status,'validation_failed')

    def test_execution_failures_do_not_look_like_semantic_insufficiency(self):
        with patch('data_analysis_agent.reviews.evidence_judge._clauses',side_effect=RuntimeError('PRIVATE-ERROR')):
            r=self.judge()
        self.assertEqual(r.execution_status,'execution_failed');self.assertNotIn('PRIVATE-ERROR',r.model_dump_json())

    def test_relation_budget_exhaustion_is_execution_failure(self):
        def excessive_combinations(*pools):
            return iter([tuple(pool[0] for pool in pools)]*10001)
        with patch('data_analysis_agent.reviews.evidence_judge.product',side_effect=excessive_combinations):
            r=self.judge(self.before_contract(),batch(['设备已到货；安装人员仍未上门']))
        self.assertEqual(r.execution_status,'execution_failed');self.assertFalse(r.evidence_refs)

    def test_output_validation_rejects_forged_permissions_hashes_and_external_refs(self):
        c=contract();b=batch(['客服回复及时']);r=self.judge(c,b)
        self.assertEqual(validate_judge_result(r,c,b,expected_scope=SCOPE,trusted_batch=b),r)
        mutations=[lambda v:v.update(input_sha256='0'*64),lambda v:v.update(status='limited'),
                   lambda v:v['evidence_refs'][0].update(key='external-key'),
                   lambda v:v['evidence_refs'][0].update(quote_end=1),lambda v:v.update(supported_target_fact_ids=[])]
        for mutate in mutations:
            v=r.model_dump(mode='json');mutate(v)
            with self.assertRaises(SourceValidationError):validate_judge_result(v,c,b,expected_scope=SCOPE,trusted_batch=b)

    def test_serialization_input_detachment_and_no_io(self):
        c=contract();b=batch(['客服回复及时']);before=deepcopy(b)
        with patch('socket.socket.connect',side_effect=AssertionError('no network')), \
             patch('sqlite3.connect',side_effect=AssertionError('no DB')),patch('psycopg.connect',side_effect=AssertionError('no DB')):
            r=self.judge(c,b)
        self.assertEqual(b,before)
        self.assertEqual(self.judge(c,json.dumps(b),trusted=before),r)
        self.assertEqual(validate_judge_result(r.model_dump_json(),c,b,expected_scope=SCOPE,trusted_batch=b),r)
        self.assertEqual(EvidenceBatch.model_validate_json(json.dumps(b)).query_id,b['query_id'])

    def test_arbitrary_query_identity_does_not_select_a_fixed_answer(self):
        for qid in ('SYNTH-NEW-A','SYNTH-NEW-B'):
            r=self.judge(b=batch(['客服回复及时'],query_id=qid,mode='filtered'))
            self.assertEqual(r.status,'sufficient');self.assertEqual(r.evidence_refs[0].query_id,qid)


if __name__=='__main__':unittest.main()
