"""P6 供应商传输契约：只检查身份、计量和错误，不解释主题语义。"""
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
import re

from openai import APIConnectionError, APITimeoutError

CONTRACT_VERSION = 'review-p6-provider-contract-v1'


@dataclass(frozen=True)
class P6ProviderContract:
    model: str
    base_url: str
    source_ref: str
    source_sha256: str
    version: str = CONTRACT_VERSION
    response_model_aliases: tuple[str, ...] = ()
    cached_input_price: Decimal | None = None
    completion_tokens_include_reasoning: bool | None = None

    def validate(self, policy):
        price = self.cached_input_price
        if (self.version != CONTRACT_VERSION or (self.model, self.base_url) != (policy.model, policy.base_url)
                or not self.source_ref or not re.fullmatch(r'[a-f0-9]{64}', self.source_sha256)
                or type(self.response_model_aliases) is not tuple
                or any(type(alias) is not str or not alias.strip() for alias in self.response_model_aliases)
                or len(set(self.response_model_aliases)) != len(self.response_model_aliases)
                or (price is not None and (type(price) is not Decimal or not price.is_finite()
                    or not 0 <= price <= policy.input_price))
                or (self.completion_tokens_include_reasoning is not None
                    and type(self.completion_tokens_include_reasoning) is not bool)):
            raise ValueError('供应商身份或价格合同未核实。')


def _identifier(value):
    # 不记录供应商 message/body/URL，避免错误回显密钥或评论正文。
    return value if type(value) is str and re.fullmatch(r'[\w./:-]{1,200}', value) else None


def parse_provider_response(raw):
    response = raw.parse()
    # SDK model_dump 会补入缺失字段；账单原件应来自收到的 JSON，不把 SDK 默认值当供应商报告。
    payload = raw.http_response.json()
    object.__setattr__(response, '_p6_raw_usage', deepcopy(payload.get('usage')))
    object.__setattr__(response, '_p6_transport_audit', {
        'http_status': raw.status_code,
        'request_id': _identifier(raw.headers.get('x-request-id')),
        'trace_id': _identifier(raw.headers.get('x-siliconcloud-trace-id'))})
    return response


def provider_error_audit(exc, *, offline=False):
    """唯一的传输错误分类入口；未知错误停止，普通限流不冒充欠费。"""
    status = getattr(exc, 'status_code', None)
    body = getattr(exc, 'body', None)
    error = body.get('error', body) if isinstance(body, dict) else {}
    error = error if isinstance(error, dict) else {}
    value = getattr(exc, 'code', None)
    value = error.get('code') if value is None else value
    code = value if type(value) is int else _identifier(value)
    category = _identifier(getattr(exc, 'type', None)) or _identifier(error.get('type'))
    response = getattr(exc, 'response', None)
    headers = getattr(response, 'headers', {})
    halt = None
    if status in (401, 403):
        kind, halt = 'authorization', f'provider_{status}'
    elif status == 402:
        kind, halt = 'insufficient_quota', 'provider_402'
    elif code == 'insufficient_quota' or category == 'insufficient_quota':
        kind, halt = 'insufficient_quota', 'provider_insufficient_quota'
    elif status == 429 and (code in (None, 'rate_limit_exceeded', 'rate_limit')
                           and category in (None, 'rate_limit_exceeded', 'rate_limit')):
        kind = 'rate_limit'
    elif status in (500, 502, 503, 504):
        kind = 'provider_unavailable'
    elif isinstance(exc, (TimeoutError, APITimeoutError)):
        kind = 'timeout'
    elif isinstance(exc, APIConnectionError):
        kind = 'connection_error'
    elif offline and status is None:
        kind = 'offline_fixture_error'
    else:
        kind, halt = 'unknown', 'provider_error_unknown'
    return {'http_status': status, 'provider_error_code': code, 'provider_error_type': category,
        'provider_error_class': kind, 'provider_halt_reason': halt,
        'request_id': _identifier(getattr(exc, 'request_id', None)) or _identifier(headers.get('x-request-id')),
        'trace_id': _identifier(headers.get('x-siliconcloud-trace-id'))}


def inspect_provider_response(response, policy, *, upper, max_tokens, offline_model=None):
    """原 usage 原样保留；只有完整且可定价的计量才返回精确费用。"""
    usage = getattr(response, '_p6_raw_usage', getattr(response, 'usage', None))
    if hasattr(usage, 'model_dump'):
        usage = usage.model_dump(mode='json')
    model = getattr(response, 'model', None)
    # 仅兼容显式 P6OfflineTransport 的旧固定响应；真实 SDK 缺失身份绝不补造。
    identity_source = 'response'
    if model is None and offline_model is not None:
        model, identity_source = offline_model, 'offline_transport_request'
    contract = policy.provider_contract
    row = {'raw_usage': deepcopy(usage), 'response_model': getattr(response, 'model', None),
        'response_id': _identifier(getattr(response, 'id', None)),
        'request_id': _identifier(getattr(response, '_request_id', None)), 'trace_id': None,
        'identity_source': identity_source, 'identity_verified': False,
        'provider_contract_version': contract.version if contract else None,
        'provider_contract_source': contract.source_ref if contract else None,
        'provider_contract_sha256': contract.source_sha256 if contract else None,
        'actual_cost': None, 'cost_status': 'unknown', 'cost_reason': 'usage_missing',
        'stop_after_response_reason': None,
        'usage': None, 'usage_status': 'missing', 'billing_usage': None, 'provider_halt_reason': None}
    row.update(getattr(response, '_p6_transport_audit', {}))
    aliases = contract.response_model_aliases if contract else ()
    if type(model) is not str or model not in (policy.model, *aliases):
        row.update(cost_reason='response_model_unverified', provider_halt_reason='response_model_unverified')
        return row
    row['identity_verified'] = True
    if not isinstance(usage, dict):
        return row
    integer = lambda n: type(n) is int and n >= 0
    inputs, outputs, total = (usage.get(key) for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
    valid = integer(inputs) and integer(outputs)
    row.update(usage_status='reported' if valid else 'invalid', cost_reason='usage_incomplete')
    if valid:
        row['usage'] = {'input_tokens': inputs, 'output_tokens': outputs}
    input_details, output_details = usage.get('prompt_tokens_details'), usage.get('completion_tokens_details')
    if any(value is not None and not isinstance(value, dict) for value in (input_details, output_details)):
        row.update(cost_reason='usage_invalid', provider_halt_reason='usage_contract_invalid')
        return row
    input_details, output_details = input_details or {}, output_details or {}
    cached = input_details.get('cached_tokens')
    hits, misses = usage.get('prompt_cache_hit_tokens'), usage.get('prompt_cache_miss_tokens')
    reasoning = output_details.get('reasoning_tokens')
    reported = [n for n in (cached, hits, misses, reasoning, total) if n is not None]
    if (any(not integer(n) for n in (inputs, outputs) if n is not None)
            or any(not integer(n) for n in reported)
            or (valid and total is not None and total != inputs + outputs)
            or (cached is not None and hits is not None and cached != hits)
            or (valid and any(n > inputs for n in (cached, hits, misses) if n is not None))
            or (valid and reasoning is not None and reasoning > outputs)):
        row.update(cost_reason='usage_inconsistent', provider_halt_reason='usage_contract_invalid')
        return row
    if cached is None:
        cached = hits
    if cached is None and misses is not None and integer(inputs):
        cached = inputs - misses
    if cached is not None and misses is not None and integer(inputs) and cached + misses != inputs:
        row.update(cost_reason='cache_usage_inconsistent', provider_halt_reason='usage_contract_invalid')
        return row
    row['billing_usage'] = {'prompt_tokens': inputs, 'cached_tokens': cached, 'completion_tokens': outputs,
        'reasoning_tokens': reasoning, 'total_tokens': total,
        'uncached_tokens': inputs - (cached or 0) if integer(inputs) else None,
        'completion_tokens_include_reasoning': contract.completion_tokens_include_reasoning if contract else None}
    # 缺 token 字段时也不能忽略明确的额外类别或上界破坏。
    known_top = {'prompt_tokens', 'completion_tokens', 'total_tokens', 'prompt_tokens_details',
        'completion_tokens_details', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens',
        'billable_extra_tokens', 'billable_categories'}
    unknown = set(usage) - known_top
    unknown |= set(input_details) - {'cached_tokens', 'audio_tokens'}
    unknown |= set(output_details) - {'reasoning_tokens', 'audio_tokens', 'accepted_prediction_tokens', 'rejected_prediction_tokens'}
    extras = [input_details.get('audio_tokens'), *(output_details.get(k) for k in
        ('audio_tokens', 'accepted_prediction_tokens', 'rejected_prediction_tokens'))]
    if (unknown or usage.get('billable_extra_tokens', 0) not in (None, 0)
            or usage.get('billable_categories', ['input', 'output']) != ['input', 'output']
            or any(n is not None and (not integer(n) or n != 0) for n in extras)
            or (integer(inputs) and inputs > upper) or (integer(outputs) and outputs > max_tokens)):
        row.update(cost_reason='unsupported_billing_or_bound', provider_halt_reason='usage_exceeded_authorized_bound')
        return row
    if reasoning:
        row.update(cost_reason='unexpected_reasoning_tokens', provider_halt_reason='unexpected_reasoning_tokens')
        return row
    if cached and (contract is None or contract.cached_input_price is None):
        row['cost_reason'] = 'cached_input_price_unknown'
        row['stop_after_response_reason'] = 'cached_input_price_unknown'
        return row
    if not valid:
        return row
    cost = (inputs - (cached or 0)) * policy.input_price + outputs * policy.output_price
    if cached:
        cost += cached * contract.cached_input_price
    row.update(actual_cost=str(cost), cost_status='exact', cost_reason='contract_priced_usage')
    return row
