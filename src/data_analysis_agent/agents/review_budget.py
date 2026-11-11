"""P6 串行预留预算；失败/usage 缺失仍保留全部费用预留。"""
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal
import math
from time import perf_counter
from typing import Callable

from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.agents.review_provider import P6ProviderContract, inspect_provider_response, provider_error_audit

STAGES = {'topic_discovery': 1200, 'topic_classification': 3000}


class P6BudgetBlocked(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class P6InputIntegrityError(ReviewDataError):
    pass


@dataclass(frozen=True)
class P6CostAuthorization:
    authorized: bool = False
    allow_real: bool = False
    model: str | None = None
    base_url: str | None = None
    currency: str | None = None
    amount_limit: Decimal | None = None
    input_price: Decimal | None = None
    output_price: Decimal | None = None
    billing_categories: tuple[str, ...] | None = None
    token_upper_bound_id: str | None = None
    token_upper_bound: Callable | None = field(default=None, repr=False)
    # 上界函数必须覆盖最终 system/wire/schema、消息封套、chat template 及所有计费输入。
    provider_contract: P6ProviderContract | None = None


@dataclass(frozen=True)
class P6OfflineTransport:
    respond: Callable = field(repr=False)


class P6SharedBudget:
    def __init__(self, authorization=None, *, offline_transport=None, clock=perf_counter):
        if authorization is not None and type(authorization) is not P6CostAuthorization:
            raise TypeError('预算授权必须由服务端提供。')
        if offline_transport is not None and type(offline_transport) is not P6OfflineTransport:
            raise TypeError('离线响应必须显式标记。')
        self._authorization = authorization
        self._offline_transport = offline_transport
        self.clock = clock
        self._started = None
        self._last_time = None
        self._targets, self._index, self._active = (), 0, None
        self._deadline = None
        self._in_flight = False
        self._reserved = Decimal(0)
        self._records = []
        self._verify = None
        self._halted_reason = None

    @property
    def authorization(self):
        return self._authorization

    @property
    def offline_transport(self):
        return self._offline_transport

    @property
    def halted_reason(self):
        return self._halted_reason

    def _now(self):
        now = self.clock()
        if not math.isfinite(now) or (self._last_time is not None and now < self._last_time):
            self._halted_reason = 'invalid_clock'
            raise P6BudgetBlocked('invalid_clock')
        self._last_time = now
        return now

    def start(self, required_aspects, verify, *, input_hashes):
        if self._started is not None or not 1 <= len(required_aspects) <= 3 or len(set(required_aspects)) != len(required_aspects):
            raise P6BudgetBlocked('invalid_or_restarted_budget')
        self._targets = tuple(required_aspects)
        self._verify = verify
        self._input_hashes = dict(input_hashes)
        if set(self._input_hashes) != set(self._targets):
            raise P6InputIntegrityError('冻结输入集合缺失。')
        self._started = self._now()

    def verify_input(self, aspect, value):
        if content_sha256(value) != self._input_hashes.get(aspect):
            raise P6InputIntegrityError('冻结维度输入摘要失配。')

    def begin_aspect(self, aspect):
        if self._in_flight or self._index >= len(self._targets) or self._targets[self._index] != aspect:
            raise P6BudgetBlocked('aspect_order_or_reentrancy')
        self._active = aspect
        self._index += 1
        self._deadline = self._now() + 40

    def check_time(self):
        now = self._now()
        if self._started is None or self._deadline is None or now >= min(self._started + 120, self._deadline):
            raise P6BudgetBlocked('deadline_exceeded')
        return now

    def ensure_authorized(self, settings):
        if not getattr(settings, 'p6_execution_enabled', False):
            raise P6BudgetBlocked('execution_not_enabled')
        if self.halted_reason:
            raise P6BudgetBlocked(self.halted_reason)
        policy = self.authorization
        if not policy or policy.authorized is not True:
            raise P6BudgetBlocked('cost_not_authorized')
        prices = (policy.amount_limit, policy.input_price, policy.output_price)
        if (any(type(p) is not Decimal or not p.is_finite() or p < 0 for p in prices)
                or not policy.currency or policy.billing_categories != ('input', 'output')
                or not policy.token_upper_bound_id or not callable(policy.token_upper_bound)
                or (policy.model, policy.base_url) != (settings.model, settings.base_url)):
            raise P6BudgetBlocked('unknown_price_token_bound_or_billing')
        if self.offline_transport is None and policy.allow_real is not True:
            raise P6BudgetBlocked('real_provider_not_authorized')
        if policy.provider_contract is not None:
            try:
                if type(policy.provider_contract) is not P6ProviderContract:
                    raise ValueError('invalid provider contract')
                policy.provider_contract.validate(policy)
            except (ValueError, TypeError, AttributeError):
                raise P6BudgetBlocked('provider_contract_unverified') from None
        self.check_time()

    def request(self, settings, *, aspect, stage, prompt, body, max_tokens, provider, options=None):
        if self._in_flight or aspect != self._active or stage not in STAGES or max_tokens != STAGES[stage]:
            raise P6BudgetBlocked('invalid_stage_or_reentrancy')
        self._verify()
        self.ensure_authorized(settings)
        records = [row for row in self._records if row['aspect'] == aspect]
        if (len(self._records) >= 6 or len(records) >= 2 or any(row['stage'] == stage for row in records)
                or (stage == 'topic_classification' and not any(
                    row['stage'] == 'topic_discovery' and row['status'] == 'accepted' for row in records))):
            raise P6BudgetBlocked('attempt_or_stage_limit')
        body_bytes = len(body.encode('utf-8'))
        if body_bytes > 16000:
            raise P6BudgetBlocked('input_body_limit')
        policy = self.authorization
        try:
            upper = policy.token_upper_bound(prompt, body)
        except Exception as exc:
            raise P6BudgetBlocked('unknown_token_bound') from exc
        if type(upper) is not int or upper <= 0:
            raise P6BudgetBlocked('unknown_token_bound')
        reserve = upper * policy.input_price + max_tokens * policy.output_price
        if self._reserved + reserve > policy.amount_limit:
            raise P6BudgetBlocked('cost_limit')
        now = self.check_time()
        timeout = min(settings.timeout_seconds, 20, self._deadline - now, self._started + 120 - now)
        if not math.isfinite(timeout) or timeout <= 0:
            raise P6BudgetBlocked('invalid_timeout')
        row = {'attempt': len(self._records) + 1, 'aspect': aspect, 'stage': stage,
            'started_at': now, 'timeout_seconds': timeout, 'max_output_tokens': max_tokens,
            'input_token_upper_bound': upper, 'body_bytes': body_bytes, 'reserved_cost': str(reserve),
            'actual_cost': None, 'usage': None, 'usage_status': 'missing', 'status': 'started', 'error_code': None}
        self._reserved += reserve
        self._records.append(row)
        self._in_flight = True
        try:
            request = {'model': settings.model, 'messages': [{'role':'system','content':prompt}, {'role':'user','content':body}],
                'temperature':0, 'response_format':{'type':'json_object'}, 'max_tokens':max_tokens,
                'timeout':timeout, 'max_retries':0, 'aspect':aspect, 'stage':stage, 'options':options or {}}
            response = (self.offline_transport.respond(request) if self.offline_transport else provider(timeout))
            row.update(inspect_provider_response(response, policy, upper=upper, max_tokens=max_tokens,
                offline_model=settings.model if self.offline_transport is not None else None))
            if row['provider_halt_reason']:
                self._halted_reason = row['provider_halt_reason']
                if self._halted_reason == 'response_model_unverified':
                    raise P6InputIntegrityError(self._halted_reason)
                raise P6BudgetBlocked(self._halted_reason)
            self._halted_reason = self.halted_reason or row['stop_after_response_reason']
            row['finished_at'] = self._now()
            if row['finished_at'] >= row['started_at'] + timeout:
                raise P6BudgetBlocked('request_deadline_exceeded')
            self.check_time()
            self._verify()
            row['status'] = 'received'
            return response
        except Exception as exc:
            if not isinstance(exc, (P6BudgetBlocked, P6InputIntegrityError)):
                audit = provider_error_audit(exc, offline=self.offline_transport is not None)
                row.update(audit, actual_cost=None, cost_status='unknown', cost_reason='provider_request_failed')
                self._halted_reason = self.halted_reason or audit['provider_halt_reason']
            row.update(status='failed', error_code=getattr(exc, 'code', None) or self.halted_reason or type(exc).__name__,
                       finished_at=self._now())
            raise
        finally:
            self._in_flight = False

    def accept(self, aspect, stage):
        self.check_time()
        self._verify()
        row = next(row for row in reversed(self._records) if row['aspect'] == aspect and row['stage'] == stage)
        if self._now() >= row['started_at'] + row['timeout_seconds']:
            raise P6BudgetBlocked('request_deadline_exceeded')
        if row['status'] != 'received':
            raise P6BudgetBlocked('response_not_pending')
        row['status'] = 'accepted'

    def reject(self, aspect, stage, code):
        for row in reversed(self._records):
            if row['aspect'] == aspect and row['stage'] == stage:
                row.update(status='failed', error_code=code)
                break

    def snapshot(self):
        reported = [Decimal(row['actual_cost']) for row in self._records if row['actual_cost'] is not None]
        complete = bool(self._records) and len(reported) == len(self._records)
        return {'attempts':len(self._records), 'reserved_cost':str(self._reserved),
            'reported_cost_subtotal':str(sum(reported, Decimal(0))),
            'actual_cost_total':str(sum(reported, Decimal(0))) if complete else None, 'actual_cost_complete':complete,
            'currency':self.authorization.currency if self.authorization else None,
            'halted_reason':self.halted_reason, 'records':deepcopy(self._records)}


def make_execution_budget(settings):
    """默认没有价格/授权/可靠 tokenizer，打开执行开关也不能取得收费许可。"""
    return P6SharedBudget()
