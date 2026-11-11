"""Run all original independent matcher assertions plus partial-Goal representation counterexamples."""
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import test_agent_stage1_v2_scoring as base
import agent_stage1_v2_1_scoring as corrected
import agent_stage1_v2_scoring as first


class CorrectedMatcherTests(base.MatcherTests):
    def setUp(self):self.enterContext(patch.object(base,'score',corrected))

    def test_known_direction_can_live_in_partial_goal_without_repeated_intent_slot(self):
        t,e,c=base.fixture()
        t['task']['result']['request_goal']['sentiment']='negative'
        e['expected_request_goal']['sentiment']='negative'
        e['expected_query_plan']['sentiment']='negative';c['known_constraints']['sentiment']='negative'
        self.assertFalse(first.assess(t,e,c)['checks']['user_intent'])
        self.assertEqual(corrected.assess(t,e,c)['status'],'PASS')

    def test_explicit_conflicting_intent_is_not_overridden_by_goal(self):
        t,e,c=base.fixture();t['task']['result']['request_goal']['sentiment']='negative'
        e['expected_request_goal']['sentiment']='negative';e['expected_query_plan']['sentiment']='negative'
        c['known_constraints']['sentiment']='negative'
        t['task']['result']['user_intent']['sentiment']='positive'
        self.assertFalse(corrected.assess(t,e,c)['checks']['user_intent'])

    def test_unfrozen_goal_cannot_supply_missing_known_direction(self):
        t,e,c=base.fixture();t['task']['result']['request_goal']=None;e['expected_request_goal']=None
        c['known_constraints']['sentiment']='positive'
        self.assertFalse(corrected.assess(t,e,c)['checks']['user_intent'])
