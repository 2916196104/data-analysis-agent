"""P6 budget concurrency: synthetic transport only, deterministic synchronization."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from decimal import Decimal
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from data_analysis_agent.agents.review_budget import (
    P6BudgetBlocked, P6CostAuthorization, P6OfflineTransport, P6SharedBudget,
)


class BudgetConcurrencyTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'psycopg.connect', 'openai.OpenAI', 'openai.AsyncOpenAI'):
            self.enterContext(patch(target, side_effect=AssertionError('offline only')))
        self.settings = SimpleNamespace(model='offline', base_url='https://offline.invalid',
                                        timeout_seconds=20, p6_execution_enabled=True)

    def budget(self, respond, *, limit='1.00', clock=lambda: 0.0, targets=('service',),
               upper=lambda prompt, body: 1000, verify=lambda: None):
        policy = P6CostAuthorization(authorized=True, model=self.settings.model, base_url=self.settings.base_url,
            currency='TEST', amount_limit=Decimal(limit), input_price=Decimal('0.0001'),
            output_price=Decimal('0.0001'), billing_categories=('input', 'output'),
            token_upper_bound_id='synthetic', token_upper_bound=upper)
        value = P6SharedBudget(policy, offline_transport=P6OfflineTransport(respond), clock=clock)
        value.start(targets, verify, input_hashes={a:'synthetic' for a in targets})
        value.begin_aspect(targets[0])
        return value

    def request(self, budget, aspect='service'):
        return budget.request(self.settings, aspect=aspect, stage='topic_discovery', prompt='offline', body='{}',
                              max_tokens=1200, provider=lambda _: self.fail('real transport forbidden'))

    @staticmethod
    def response():
        return SimpleNamespace(usage=None, model='offline')

    def test_simultaneous_preflight_cannot_exceed_one_reservation(self):
        gate = threading.Barrier(2, timeout=0.5)
        local = threading.local()
        provider_entered, provider_release = threading.Event(), threading.Event()
        calls = []

        def clock():
            if threading.current_thread() is not threading.main_thread():
                local.count = getattr(local, 'count', 0) + 1
                if local.count == 2:
                    try: gate.wait()
                    except threading.BrokenBarrierError: pass
            return 0.0

        def respond(request):
            calls.append(request['aspect'])
            provider_entered.set()
            if not provider_release.wait(5): raise AssertionError('provider not released')
            return self.response()

        budget = self.budget(respond, limit='0.22', clock=clock)
        def invoke():
            try:
                self.request(budget)
                return 'received'
            except P6BudgetBlocked:
                return 'blocked'
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(invoke) for _ in range(2)]
            try:
                self.assertTrue(provider_entered.wait(5))
                done, _ = wait(futures, timeout=2, return_when=FIRST_COMPLETED)
            finally:
                provider_release.set()
            outcomes = [f.result(timeout=5) for f in futures]
        self.assertTrue(done, 'competing request must be rejected while provider is in flight')
        self.assertCountEqual(outcomes, ['received', 'blocked'])
        self.assertEqual(calls, ['service'])
        state = budget.snapshot()
        self.assertEqual(state['attempts'], 1)
        self.assertEqual(Decimal(state['reserved_cost']), Decimal('0.22'))

    def test_snapshot_and_same_stage_competitor_during_provider(self):
        entered, release = threading.Event(), threading.Event()
        def respond(request):
            entered.set()
            if not release.wait(5): raise AssertionError('provider not released')
            return self.response()
        budget = self.budget(respond, limit='0.22')
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.request, budget)
            try:
                self.assertTrue(entered.wait(5))
                state = pool.submit(budget.snapshot).result(timeout=3)
                self.assertEqual((state['attempts'], state['records'][0]['status']), (1, 'started'))
                self.assertEqual(Decimal(state['reserved_cost']), Decimal('0.22'))
                with self.assertRaises(P6BudgetBlocked): pool.submit(self.request, budget).result(timeout=3)
                with self.assertRaises(P6BudgetBlocked): pool.submit(budget.accept, 'service', 'topic_discovery').result(timeout=3)
                with self.assertRaises(P6BudgetBlocked): pool.submit(budget.reject, 'service', 'topic_discovery', 'other').result(timeout=3)
                self.assertEqual(budget.snapshot()['records'][0]['status'], 'started')
            finally:
                release.set()
            first.result(timeout=5)
        state['records'].clear()
        self.assertEqual(budget.snapshot()['records'][0]['status'], 'received')

    def test_other_dimension_is_blocked_during_inflight_then_allowed(self):
        entered, release = threading.Event(), threading.Event()
        def respond(request):
            if request['aspect'] == 'service':
                entered.set()
                if not release.wait(5): raise AssertionError('provider not released')
            return self.response()
        budget = self.budget(respond, limit='0.44', targets=('service', 'logistics'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.request, budget)
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(P6BudgetBlocked): pool.submit(budget.begin_aspect, 'logistics').result(timeout=3)
                with self.assertRaises(P6BudgetBlocked): pool.submit(self.request, budget, 'logistics').result(timeout=3)
            finally:
                release.set()
            first.result(timeout=5)
        budget.begin_aspect('logistics')
        self.request(budget, 'logistics')
        self.assertEqual(Decimal(budget.snapshot()['reserved_cost']), Decimal('0.44'))

    def test_timeout_and_provider_exception_release_inflight_without_refund(self):
        for error in (TimeoutError('synthetic timeout'), RuntimeError('synthetic provider error')):
            with self.subTest(error=type(error).__name__):
                def respond(request):
                    if request['aspect'] == 'service': raise error
                    return self.response()
                budget = self.budget(respond, limit='0.44', targets=('service', 'logistics'))
                with self.assertRaises(type(error)): self.request(budget)
                budget.begin_aspect('logistics')
                self.request(budget, 'logistics')
                state = budget.snapshot()
                self.assertEqual([r['status'] for r in state['records']], ['failed', 'received'])
                self.assertEqual(Decimal(state['reserved_cost']), Decimal('0.44'))

    def test_preflight_failure_does_not_reserve_or_leave_budget_busy(self):
        attempts = []
        def upper(prompt, body):
            attempts.append(True)
            if len(attempts) == 1: raise ValueError('synthetic bound error')
            return 1000
        budget = self.budget(lambda request: self.response(), limit='0.22', upper=upper)
        with self.assertRaises(P6BudgetBlocked): self.request(budget)
        self.assertEqual(budget.snapshot()['attempts'], 0)
        self.assertEqual(Decimal(budget.snapshot()['reserved_cost']), 0)
        self.request(budget)
        self.assertEqual(budget.snapshot()['attempts'], 1)

    def test_preflight_callback_cannot_reenter_budget(self):
        holder = {}
        def upper(prompt, body):
            with self.assertRaises(P6BudgetBlocked): self.request(holder['budget'])
            return 1000
        budget = self.budget(lambda request: self.response(), limit='0.22', upper=upper)
        holder['budget'] = budget
        self.request(budget)
        self.assertEqual(budget.snapshot()['attempts'], 1)

    def test_snapshot_remains_available_during_token_bound_callback(self):
        entered, release = threading.Event(), threading.Event()
        def upper(prompt, body):
            entered.set()
            if not release.wait(5): raise AssertionError('bound callback not released')
            return 1000
        budget = self.budget(lambda request: self.response(), limit='0.22', upper=upper)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.request, budget)
            try:
                self.assertTrue(entered.wait(5))
                state = pool.submit(budget.snapshot).result(timeout=1)
                self.assertEqual(state['attempts'], 0)
                self.assertEqual(Decimal(state['reserved_cost']), 0)
            finally:
                release.set()
            first.result(timeout=5)

    def test_validation_callback_can_read_snapshot_from_another_thread(self):
        holder, snapshots = {}, []
        with ThreadPoolExecutor(max_workers=1) as pool:
            def verify():
                snapshots.append(pool.submit(holder['budget'].snapshot).result(timeout=2)['attempts'])
            budget = self.budget(lambda request: self.response(), limit='0.22', verify=verify)
            holder['budget'] = budget
            self.request(budget)
            budget.accept('service', 'topic_discovery')
        self.assertEqual(snapshots, [0, 1, 1])
        self.assertEqual(budget.snapshot()['records'][0]['status'], 'accepted')

    def test_deadline_exception_releases_inflight_for_next_dimension(self):
        now = [0.0]
        def respond(request):
            if request['aspect'] == 'service': now[0] += 21
            return self.response()
        budget = self.budget(respond, limit='0.44', clock=lambda: now[0],
                             targets=('service', 'logistics'))
        with self.assertRaises(P6BudgetBlocked) as caught: self.request(budget)
        self.assertEqual(caught.exception.code, 'request_deadline_exceeded')
        budget.begin_aspect('logistics')
        self.request(budget, 'logistics')
        state = budget.snapshot()
        self.assertEqual([r['status'] for r in state['records']], ['failed', 'received'])
        self.assertEqual(Decimal(state['reserved_cost']), Decimal('0.44'))

    def test_concurrent_start_accepts_exactly_one_caller(self):
        budget = P6SharedBudget()
        gate = threading.Barrier(3, timeout=5)
        def start():
            gate.wait()
            try:
                budget.start(('service',), lambda: None, input_hashes={'service':'synthetic'})
                return 'started'
            except P6BudgetBlocked:
                return 'blocked'
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(start) for _ in range(2)]
            gate.wait()
            outcomes = [f.result(timeout=5) for f in futures]
        self.assertCountEqual(outcomes, ['started', 'blocked'])
        budget.begin_aspect('service')

    def test_snapshot_consistent_while_response_is_being_inspected(self):
        entered, release = threading.Event(), threading.Event()
        class Response:
            model = 'offline'
            @property
            def usage(self):
                entered.set()
                if not release.wait(5): raise AssertionError('inspection not released')
                return None
        budget = self.budget(lambda request: Response(), limit='0.22')
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.request, budget)
            try:
                self.assertTrue(entered.wait(5))
                state = pool.submit(budget.snapshot).result(timeout=3)
                self.assertEqual((state['attempts'], state['records'][0]['status']), (1, 'started'))
                self.assertEqual(Decimal(state['reserved_cost']), Decimal('0.22'))
            finally:
                release.set()
            first.result(timeout=5)
        self.assertEqual(budget.snapshot()['records'][0]['status'], 'received')

    def test_many_competitors_never_exceed_budget(self):
        for _ in range(5):
            self._many_competitors_round()

    def _many_competitors_round(self):
        count = 24
        start = threading.Barrier(count + 1, timeout=5)
        entered, release, rejected_all = threading.Event(), threading.Event(), threading.Event()
        rejection_lock = threading.Lock()
        rejected = []
        def respond(request):
            entered.set()
            if not release.wait(5): raise AssertionError('provider not released')
            return self.response()
        budget = self.budget(respond, limit='0.22')
        def invoke():
            start.wait()
            try:
                self.request(budget)
                return 'received'
            except P6BudgetBlocked:
                with rejection_lock:
                    rejected.append(True)
                    if len(rejected) == count - 1: rejected_all.set()
                return 'blocked'
        with ThreadPoolExecutor(max_workers=count) as pool:
            futures = [pool.submit(invoke) for _ in range(count)]
            try:
                start.wait()
                self.assertTrue(entered.wait(5))
                self.assertTrue(rejected_all.wait(5))
                state = budget.snapshot()
                self.assertGreaterEqual(Decimal(state['reserved_cost']), 0)
                self.assertLessEqual(Decimal(state['reserved_cost']), Decimal('0.22'))
                self.assertEqual(state['attempts'], 1)
            finally:
                release.set()
            outcomes = [f.result(timeout=5) for f in futures]
        self.assertEqual(outcomes.count('received'), 1)
        self.assertEqual(outcomes.count('blocked'), count - 1)


if __name__ == '__main__':
    unittest.main()
