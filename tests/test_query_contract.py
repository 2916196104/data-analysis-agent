"""4B-1a 合成契约测试；不读取真实评论、冻结问题或人工相关性标签。"""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.opinions import ASPECTS, SENTIMENTS
from data_analysis_agent.reviews.query_contract import (
    CONTRACT_VERSION, REPLAY_VERSION, ContractError, ScopeRef,
    validate_manual_replay, validate_query_contract,
)


SCOPE = ScopeRef(run_id=content_sha256('synthetic-run-z'),
                 manifest_sha256=content_sha256('synthetic-manifest-z'),
                 scope_sha256=content_sha256('synthetic-scope-z'))
QUESTION = '[合成] ZX 测试机的客服答复是否让体验者满意？'


def payload():
    return dict(version=CONTRACT_VERSION, task_type='evidence',
        aspect_candidates=[dict(aspect='service', rationale='合成输入明确指定客服对象')],
        sentiment_intent='positive', target_entity='customer_support', subtopic='response_experience',
        required_facts=dict(operator='all_of', items=[dict(id='reply', description='合成目标：客服作出答复',
                            sentiment='positive')], relations=[]),
        polarity_structure=dict(negations=[], contrast_fact_ids=[]),
        proposed_filters=dict(aspect='service', sentiment='positive'),
        uncertainty=[], clarification=[], scope_ref=SCOPE.model_dump(mode='json'))


def changed(**updates):
    value = payload()
    value.update(updates)
    return value


def replay(value=None):
    contract = validate_query_contract(value or payload(), expected_scope=SCOPE).model_dump(mode='json')
    return dict(version=REPLAY_VERSION, input_kind='developer_contract', data_kind='synthetic',
        replay_id='synthetic-replay-z', query_id='synthetic-query-z',
        query_sha256=content_sha256(QUESTION), contract_sha256=content_sha256(contract), contract=contract)


class QueryContractTests(unittest.TestCase):
    def validate(self, value):
        return validate_query_contract(value, expected_scope=SCOPE)

    def load(self, value, **updates):
        arguments = dict(expected_scope=SCOPE, expected_query_id='synthetic-query-z',
                         expected_question=QUESTION, expected_data_kind='synthetic')
        arguments.update(updates)
        return validate_manual_replay(value, **arguments)

    def test_positive_negative_and_existing_sentiment_enums(self):
        for sentiment in SENTIMENTS:
            with self.subTest(sentiment=sentiment):
                filters = {} if sentiment == 'unknown' else dict(sentiment=sentiment)
                c = self.validate(changed(sentiment_intent=sentiment, proposed_filters=filters))
                self.assertEqual(c.sentiment_intent, sentiment)

    def test_all_existing_aspects_are_reused(self):
        for aspect in ASPECTS:
            c = self.validate(changed(aspect_candidates=[dict(aspect=aspect, rationale='合成显式维度')],
                                      proposed_filters=dict(aspect=aspect)))
            self.assertEqual(c.aspect_candidates[0].aspect, aspect)

    def test_statistics_evidence_and_mixed_tasks_are_distinct(self):
        for task in ('statistics', 'evidence', 'mixed'):
            value = changed(task_type=task, proposed_filters={})
            value['required_facts']['items'][0]['description'] = {
                'statistics':'合成目标：统计客服答复数量', 'evidence':'合成目标：定位一次客服答复',
                'mixed':'合成目标：分别统计数量并定位实例'}[task]
            self.assertEqual(self.validate(value).task_type, task)

    def test_customer_delivery_installer_roles_remain_separate(self):
        for entity, aspect in [('customer_support','service'), ('delivery_staff','logistics'),
                              ('installer','service')]:
            c = self.validate(changed(target_entity=entity,
                aspect_candidates=[dict(aspect=aspect, rationale='合成对象明确')],
                proposed_filters=dict(aspect=aspect)))
            self.assertEqual(c.target_entity, entity)

    def test_cooling_and_heating_subtopics_are_not_merged(self):
        for topic in ('cooling', 'heating'):
            c = self.validate(changed(target_entity='test_device_z', subtopic=topic,
                aspect_candidates=[dict(aspect='effectiveness', rationale='合成设备功能')],
                proposed_filters=dict(aspect='effectiveness')))
            self.assertEqual(c.subtopic, topic)

    def test_explicit_unknown_topic_and_entity_are_not_guessed(self):
        value = changed(proposed_filters={})
        del value['subtopic']; del value['target_entity']
        c = self.validate(value)
        self.assertEqual((c.subtopic, c.target_entity), ('unknown', 'unknown'))

    def test_negation_keeps_target_fact_cue_and_scope_without_flipping_sentiment(self):
        for sentiment, cue, scope in [('positive','并非','合成：否定等待过久，不否定答复事实'),
                                     ('negative','未能','合成：否定所需答复，不否定联系动作')]:
            value = changed(sentiment_intent=sentiment, proposed_filters={})
            value['polarity_structure']['negations'] = [dict(fact_id='reply',cue=cue,scope=scope)]
            c = self.validate(value)
            self.assertEqual(c.polarity_structure.negations[0].scope, scope)
            self.assertEqual(c.sentiment_intent, sentiment)

    def test_multiple_facts_require_all_and_before_relation(self):
        value = changed(target_entity='installer', subtopic='installation_after_delivery', proposed_filters={})
        value['required_facts'] = dict(operator='all_of', items=[
            dict(id='arrived',description='合成条件：设备已送达'),
            dict(id='waiting',description='合成条件：送达后仍等待安装')],
            relations=[dict(kind='before',fact_ids=['arrived','waiting'])])
        c = self.validate(value)
        self.assertEqual(c.required_facts.operator, 'all_of')
        self.assertEqual(c.required_facts.relations[0].fact_ids, ('arrived','waiting'))

    def test_any_of_is_preserved(self):
        value = changed(proposed_filters={})
        value['required_facts'].update(operator='any_of', items=[dict(id='a',description='合成条件 A'),
                                                               dict(id='b',description='合成条件 B')])
        self.assertEqual(self.validate(value).required_facts.operator, 'any_of')

    def mixed_payload(self):
        return changed(sentiment_intent='mixed', proposed_filters={}, required_facts=dict(
            operator='all_of', items=[dict(id='good',description='合成正向条件',sentiment='positive'),
                                      dict(id='bad',description='合成负向条件',sentiment='negative')],
            relations=[dict(kind='same_review',fact_ids=['good','bad'])]),
            polarity_structure=dict(negations=[],contrast_fact_ids=['good','bad']))

    def test_same_review_positive_negative_cooccurrence_and_contrast(self):
        c = self.validate(self.mixed_payload())
        self.assertEqual(c.required_facts.relations[0].kind, 'same_review')
        self.assertEqual(c.polarity_structure.contrast_fact_ids, ('good','bad'))

    def test_mixed_requires_cooccurrence_and_both_polarities(self):
        for mutation in ('no_relation','same_polarity','any_of'):
            value = self.mixed_payload()
            if mutation == 'no_relation': value['required_facts']['relations'] = []
            elif mutation == 'same_polarity': value['required_facts']['items'][1]['sentiment'] = 'positive'
            else: value['required_facts']['operator'] = 'any_of'
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ContractError,'mixed_requires'):
                self.validate(value)

    def test_ambiguous_query_can_clarify_without_invented_facts_or_filters(self):
        c = self.validate(changed(task_type='clarify', aspect_candidates=[], sentiment_intent='unknown',
            target_entity='unknown', subtopic='unknown', required_facts={}, proposed_filters={},
            uncertainty=['合成：对象及任务不明确'], clarification=['合成：希望统计还是查看实例？']))
        self.assertEqual(c.required_facts.items, ())
        self.assertIsNone(c.proposed_filters.aspect)

    def test_clarify_requires_a_question(self):
        with self.assertRaisesRegex(ContractError,'clarification_required'):
            self.validate(changed(task_type='clarify', proposed_filters={}))

    def test_uncertain_inputs_cannot_carry_hard_filters(self):
        variants = [dict(uncertainty=['合成：角色未定']), dict(clarification=['合成：先确认对象？']),
            dict(subtopic='unknown'), dict(target_entity='unknown'), dict(sentiment_intent='unknown'),
            dict(aspect_candidates=[]),dict(aspect_candidates=[dict(aspect='service',rationale='合成备选一'),
                                                               dict(aspect='logistics',rationale='合成备选二')])]
        for updates in variants:
            with self.subTest(updates=updates), self.assertRaisesRegex(ContractError,'filters_forbidden'):
                self.validate(changed(**updates))
            self.validate(changed(**{**updates, 'proposed_filters':{}}))

    def test_statistics_never_carries_retrieval_filters(self):
        with self.assertRaisesRegex(ContractError,'non_evidence_filters'):
            self.validate(changed(task_type='statistics'))

    def test_contradictory_legal_filters_are_rejected(self):
        for filters, error in [(dict(aspect='price'),'filter_aspect_mismatch'),
                               (dict(sentiment='negative'),'filter_sentiment_mismatch')]:
            with self.assertRaisesRegex(ContractError,error): self.validate(changed(proposed_filters=filters))

    def test_mixed_is_not_a_sql_sentiment_even_when_other_values_are_legal(self):
        value = self.mixed_payload()
        for sentiment in ('mixed','positive','negative','all'):
            value['proposed_filters'] = dict(sentiment=sentiment)
            with self.assertRaises(ContractError): self.validate(value)
        value['proposed_filters'] = dict(aspect='service')
        self.assertEqual(self.validate(value).proposed_filters.aspect, 'service')

    def test_invalid_enums_and_filter_keys_are_rejected(self):
        bad = [changed(task_type='answer'),changed(sentiment_intent='all'),
            changed(aspect_candidates=[dict(aspect='installation',rationale='合成非法分类')]),
            changed(proposed_filters=dict(aspect='customer_service')),
            changed(proposed_filters=dict(sentiment='ambiguous')),
            changed(proposed_filters=dict(product_ids=['SYNTHETIC-PRODUCT'])),
            changed(proposed_filters=dict(top_k=10)),changed(required_facts=dict(operator='xor'))]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ContractError): self.validate(value)

    def test_scope_cannot_be_replaced_or_extended(self):
        for name in ('run_id','manifest_sha256','scope_sha256'):
            value=payload(); value['scope_ref'][name]=content_sha256('other-synthetic-identity')
            with self.assertRaisesRegex(ContractError,'scope_ref_mismatch'): self.validate(value)
        for name in ('product_ids','date_from','source'):
            value=payload(); value['scope_ref'][name]='UNTRUSTED'
            with self.assertRaises(ContractError): self.validate(value)
        with self.assertRaises(ContractError): self.validate(changed(scope_ref='unbound'))
        with self.assertRaisesRegex(ContractError,'trusted_scope_required'):
            validate_query_contract(payload(),expected_scope=SCOPE.model_dump())

    def test_invalid_copied_scope_is_revalidated(self):
        with self.assertRaises(ContractError):
            validate_query_contract(payload(),expected_scope=SCOPE.model_copy(update={'scope_sha256':123}))

    def test_models_are_deeply_immutable_and_input_mutation_is_detached(self):
        value=payload(); before=deepcopy(value); c=self.validate(value)
        self.assertEqual(value,before)
        value['scope_ref']['scope_sha256']='changed'; value['required_facts']['items'][0]['description']='changed'
        self.assertEqual(c.scope_ref,SCOPE)
        self.assertNotEqual(c.required_facts.items[0].description,'changed')
        with self.assertRaises(ValidationError): c.scope_ref.scope_sha256='f'*64
        with self.assertRaises(ValidationError): c.proposed_filters.sentiment='negative'
        with self.assertRaises(TypeError): c.required_facts.items[0]='changed'

    def test_duplicate_aspects_and_fact_ids_are_rejected(self):
        value=payload(); value['aspect_candidates']*=2
        with self.assertRaisesRegex(ContractError,'duplicate_aspect'): self.validate(value)
        value=payload(); value['required_facts']['items']*=2
        with self.assertRaisesRegex(ContractError,'duplicate_fact'): self.validate(value)

    def test_unknown_duplicate_and_incomplete_relation_references_are_rejected(self):
        for ids in (['missing','reply'],['reply','reply'],['reply']):
            value=payload(); value['required_facts']['relations']=[dict(kind='same_review',fact_ids=ids)]
            with self.assertRaises(ContractError): self.validate(value)
        value=payload(); value['polarity_structure']['negations']=[dict(fact_id='missing',cue='未',scope='合成否定范围')]
        with self.assertRaisesRegex(ContractError,'invalid_fact_references'): self.validate(value)
        value=payload(); value['polarity_structure']['contrast_fact_ids']=['reply']
        with self.assertRaises(ContractError): self.validate(value)

    def test_nonclarify_requires_facts_and_blank_text_is_invalid(self):
        with self.assertRaisesRegex(ContractError,'required_facts_missing'):
            self.validate(changed(required_facts={}))
        for key in ('target_entity','subtopic'):
            with self.assertRaises(ContractError): self.validate(changed(**{key:'   '}))
        value=payload(); value['aspect_candidates'][0]['rationale']=''
        with self.assertRaises(ContractError): self.validate(value)

    def test_json_serialization_and_default_normalization_are_deterministic(self):
        value=changed(proposed_filters={}); del value['polarity_structure']
        c=self.validate(value); dumped=c.model_dump(mode='json')
        self.assertEqual(c,self.validate(json.dumps(dumped,ensure_ascii=False)))
        self.assertEqual(content_sha256(dumped),content_sha256(self.validate(value).model_dump(mode='json')))
        self.assertEqual(dumped['polarity_structure'],dict(negations=[],contrast_fact_ids=[]))

    def test_invalid_json_duplicate_keys_nan_types_and_safe_errors(self):
        for raw in ('{"task_type":"evidence","task_type":"statistics"}', '{"value":NaN}',
                    '{','[]','null',True,payload() | {'task_type':1}):
            with self.assertRaises(ContractError): self.validate(raw)
        with self.assertRaises(ContractError) as caught: self.validate(payload() | {'PRIVATE-TEST-SECRET':'s'})
        self.assertNotIn('PRIVATE-TEST-SECRET',str(caught.exception))

    def test_manual_replay_roundtrip_and_identity(self):
        value=replay(); normalized=self.load(value)
        self.assertEqual(normalized.input_kind,'developer_contract')
        self.assertEqual(normalized,self.load(normalized.model_dump_json()))
        self.assertEqual(value,replay())

    def test_replay_cannot_confuse_synthetic_and_frozen_inputs(self):
        with self.assertRaisesRegex(ContractError,'data_kind_mismatch'):
            self.load(replay(),expected_data_kind='frozen_query')
        value=replay(); value['data_kind']='frozen_query'
        self.assertEqual(self.load(value,expected_data_kind='frozen_query').data_kind,'frozen_query')

    def test_replay_cannot_accept_predictions_or_reference_answers(self):
        for kind in ('runtime_prediction','human_reference','developer_contract_with_grades'):
            value=replay(); value['input_kind']=kind
            with self.assertRaises(ContractError): self.load(value)
        for key in ('grade','judgments','reference_state','evidence_status','answer','metrics'):
            value=replay(); value[key]=2
            with self.assertRaises(ContractError): self.load(value)
            value=replay(); value['contract'][key]=2
            with self.assertRaises(ContractError): self.load(value)

    def test_replay_rejects_query_contract_scope_and_version_drift(self):
        mutations=[lambda v:v.update(query_id='other-query'),
            lambda v:v.update(query_sha256=content_sha256('other question')),
            lambda v:v.update(contract_sha256='0'*64), lambda v:v.update(version='future-v2'),
            lambda v:v['contract'].update(version='future-v2'),
            lambda v:v['contract']['scope_ref'].update(scope_sha256='0'*64),
            lambda v:v['contract'].update(subtopic='changed-topic')]
        for mutate in mutations:
            value=replay(); mutate(value)
            with self.assertRaises(ContractError): self.load(value)

    def test_replay_requires_external_query_and_kind(self):
        for updates in (dict(expected_question=''),dict(expected_query_id=''),dict(expected_data_kind='test')):
            with self.assertRaises(ContractError): self.load(replay(),**updates)

    def test_validation_never_opens_network_or_database(self):
        with patch('socket.socket.connect',side_effect=AssertionError('no network')), \
             patch('sqlite3.connect',side_effect=AssertionError('no DB')), \
             patch('psycopg.connect',side_effect=AssertionError('no DB')):
            self.validate(payload()); self.load(replay())


if __name__ == '__main__':
    unittest.main()
