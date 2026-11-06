"""SiliconFlow shadow port. Real calls fail closed pending model-specific evidence.

MockTransport exercises the actual SDK without opening a network connection. It is
an injected test port, never a switch available through dotenv or factory config.
"""
from decimal import Decimal
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import ssl
from time import monotonic
from typing import Literal

from dotenv import dotenv_values
import httpx
from openai import OpenAI
from pydantic import Field

from data_analysis_agent.core.serialization import canonical_json, strict_json_loads
from data_analysis_agent.reviews.query_contract import ContractModel
from data_analysis_agent.agents.review_shadow_openai import (
    SYSTEM_PROMPT, strict_output_schema, ShadowProviderError, _LimitedStream,
)

MODEL = 'deepseek-ai/DeepSeek-V4.1-Flash'
BASE_URL = 'https://api.siliconflow.cn/v1'
DOCUMENTED_VERSION = 'DeepSeek-V4.1-Flash-1017'
INPUT_LIMIT, OUTPUT_LIMIT = 8000, 2500
INPUT_PRICE, OUTPUT_PRICE, CACHE_PRICE = Decimal('2'), Decimal('8'), Decimal('0.04')
PROMPT_VERSION = 'p5-shadow-siliconflow-instructions-v1'


def compatibility_blockers():
    # 2026-11-06 public docs confirm API-level json_schema, not this model's
    # strict Schema subset, tokenizer/template or non-thinking budget guarantee.
    return ('model_strict_schema_unverified', 'native_tokenizer_binding_unverified',
            'model_non_thinking_budget_unverified')


class SiliconFlowShadowConfig(ContractModel):
    model: Literal['deepseek-ai/DeepSeek-V4.1-Flash']
    base_url: Literal['https://api.siliconflow.cn/v1']
    env_file: str = Field(min_length=1, repr=False)
    env_sha256: str = Field(pattern=r'^[0-9a-f]{64}$', repr=False)
    usage_file: str | None = None


def _settings(env_file, expected_sha256=None):
    try:
        raw = Path(env_file).read_bytes()
        digest = sha256(raw).hexdigest()
        if expected_sha256 and digest != expected_sha256:
            raise ShadowProviderError('shadow_configuration_changed')
        # No os.environ merge, interpolation, .env/LLM_* or SDK key fallback.
        from io import StringIO
        values = dotenv_values(stream=StringIO(raw.decode('utf-8-sig')), interpolate=False)
        names = ('SHADOW_MODEL_ID', 'SHADOW_BASE_URL', 'SHADOW_API_KEY')
        if any(not (values.get(k) or '').strip() for k in names):
            raise ShadowProviderError('missing_shadow_settings')
        if (values['SHADOW_MODEL_ID'].strip(), values['SHADOW_BASE_URL'].strip()) != (MODEL, BASE_URL):
            raise ShadowProviderError('invalid_shadow_model_or_endpoint')
        return values, digest
    except (OSError, UnicodeError):
        raise ShadowProviderError('shadow_configuration_unreadable') from None


def scoped_config(env_file):
    _, digest = _settings(env_file)
    # Only a file reference and its identity cross the worker control pipe.
    return SiliconFlowShadowConfig(model=MODEL, base_url=BASE_URL,
        env_file=str(Path(env_file).resolve()), env_sha256=digest)


def preflight(env_file):
    missing = []
    try:
        scoped_config(env_file)
        configured = True
    except ShadowProviderError as exc:
        missing.append(str(exc)); configured = False
    blockers = list(compatibility_blockers())
    return dict(provider='siliconflow', model=MODEL, base_url=BASE_URL,
        endpoint=BASE_URL+'/chat/completions', documented_service_version=DOCUMENTED_VERSION,
        service_version_observed=False, immutable_model_snapshot=False,
        status='blocked_compatibility' if configured else 'configuration_incomplete',
        shadow_key_configured=configured, primary_key_reused=False,
        strict_schema='api_documented_model_unverified', json_object_fallback=False,
        offline_tokenizer_ready=False, tokenizer='unverified_no_openai_tokenizer_fallback',
        enable_thinking=False, thinking_budget_guaranteed=False,
        input_token_budget_per_request=INPUT_LIMIT, output_token_limit_per_request=OUTPUT_LIMIT,
        max_model_calls=3, total_input_budget=24000, total_output_limit=7500,
        price_checked_on='2026-11-06', currency='CNY',
        price_cny_per_million=dict(peak=dict(input='2',output='8',cached_input='0.04'),
                                   off_peak=dict(input='1',output='4',cached_input='0.02')),
        off_peak_beijing_time='02:00 <= local_time < 08:00',
        conditional_per_call_cny='0.036', conditional_three_calls_cny='0.108',
        conditional_off_peak_per_call_cny='0.018', conditional_off_peak_three_calls_cny='0.054',
        guaranteed_max_cost_cny=None, spending_authorized=False,
        budget_condition='8000 input and 2500 total generated tokens incl reasoning; no cache discount assumed',
        sdk_timeout_seconds=20, outer_deadline_seconds=30, max_retries=0,
        missing=missing+blockers, real_database_calls=0, real_embedding_calls=0, real_llm_calls=0,
        account_access_checked=False, publication_allowed=False, semantic_verified=False)


def request_body(payload_json):
    payload = strict_json_loads(payload_json)
    return dict(model=MODEL, max_tokens=OUTPUT_LIMIT, enable_thinking=False,
        temperature=0, n=1, stream=False,
        messages=[dict(role='system',content=SYSTEM_PROMPT),
                  dict(role='user',content=canonical_json(payload))],
        response_format=dict(type='json_schema',json_schema=dict(
            name='p5b_evidence_output',strict=True,schema=strict_output_schema())))


class _BoundedTransport(httpx.BaseTransport):
    def __init__(self, inner): self.inner = inner
    def handle_request(self, request):
        if (request.method,request.url.scheme,request.url.host,request.url.path) != (
                'POST','https','api.siliconflow.cn','/v1/chat/completions'):
            raise ShadowProviderError('unexpected_provider_request')
        response = self.inner.handle_request(request)
        if response.headers.get('content-encoding','identity') != 'identity':
            response.close(); raise ShadowProviderError('unexpected_content_encoding')
        if response.is_stream_consumed and len(response.content)>131072:
            response.close(); raise ShadowProviderError('http_response_too_large')
        response.stream = _LimitedStream(response.stream)
        return response
    def close(self): self.inner.close()


class SiliconFlowShadowJudge:
    input_limit = INPUT_LIMIT
    output_limit = OUTPUT_LIMIT

    def _check_readiness(self, transport, encoding):
        if type(transport) is not httpx.MockTransport and compatibility_blockers():
            raise ShadowProviderError('siliconflow_compatibility_unverified')
        if encoding is None:
            raise ShadowProviderError('native_tokenizer_binding_unverified')

    def _request_body(self, payload_json):
        return request_body(payload_json)

    def _input_estimate(self, body):
        return len(self.encoding.encode(canonical_json(body)))+1024

    def __init__(self, config: SiliconFlowShadowConfig, *, transport=None, encoding=None):
        # The public factory has no test/bypass flags. Until the blockers are
        # resolved, it cannot construct a live HTTP client, even with a valid key.
        self._check_readiness(transport, encoding)
        values, _ = _settings(config.env_file, config.env_sha256)
        self.config, self.encoding = config, encoding
        self.used = False
        self.metrics = dict(provider='siliconflow', state='not_called', model=MODEL, base_url=BASE_URL,
            documented_service_version=DOCUMENTED_VERSION, prompt_version=PROMPT_VERSION,
            model_calls=0, usage_known=False, currency='CNY', price_checked_on='2026-11-06',
            semantic_verified=False, publication_allowed=False)
        if config.usage_file and Path(config.usage_file).exists():
            raise ShadowProviderError('usage_file_already_exists')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); context.load_default_certs()
        inner = transport or httpx.HTTPTransport(verify=context,trust_env=False,retries=0)
        timeout = httpx.Timeout(20,connect=5,write=10,pool=5)
        http_client = httpx.Client(transport=_BoundedTransport(inner),trust_env=False,
                                  follow_redirects=False,timeout=timeout)
        self.client = OpenAI(api_key=values['SHADOW_API_KEY'].strip(),base_url=BASE_URL,
            max_retries=0,timeout=timeout,http_client=http_client,
            default_headers={'Accept-Encoding':'identity'})

    def judge(self, payload_json):
        if self.used: raise ShadowProviderError('single_attempt_only')
        self.used = True; started = monotonic()
        try:
            body = self._request_body(payload_json); incoming = strict_json_loads(payload_json)
            self.metrics.update(request_id=incoming['request_id'],input_sha256=incoming['input_sha256'])
            # Test encoders are injected only with MockTransport. No o200k/byte
            # estimate is presented as this model's native tokenizer.
            budget = self._input_estimate(body)
            self.metrics['preflight_input_token_estimate'] = budget
            if budget>self.input_limit: raise ShadowProviderError('input_token_budget_exceeded')
            thinking = body.pop('enable_thinking')
            self.metrics.update(state='request_started',model_calls=1,
                request_started_at_utc=datetime.now(timezone.utc).isoformat())
            response = self.client.chat.completions.create(**body,extra_body={'enable_thinking':thinking})
            # Safe diagnostics survive usage/format/non-thinking failures too.
            import re
            reported_model=response.model
            self.metrics.update(http_status=200,
                response_model=reported_model if isinstance(reported_model,str) and
                re.fullmatch(r'[A-Za-z0-9_./:-]{1,120}',reported_model) else 'invalid_model_identity')
            if len(response.choices)==1:
                choice=response.choices[0];message=choice.message
                reasoning_content=getattr(message,'reasoning_content',None)
                self.metrics.update(finish_reason=choice.finish_reason if choice.finish_reason in (
                    'stop','length','content_filter','tool_calls','function_call') else 'unknown',
                    reasoning_content_field_present=hasattr(message,'reasoning_content'),
                    reasoning_content_present=bool(reasoning_content),
                    reasoning_content_bytes=len(reasoning_content.encode('utf-8')) if isinstance(reasoning_content,str) else None)
            usage = response.usage
            if usage is None: raise ShadowProviderError('usage_missing')
            pin,pout,total = usage.prompt_tokens,usage.completion_tokens,usage.total_tokens
            if any(type(v) is not int or v<0 for v in (pin,pout,total)) or total!=pin+pout:
                raise ShadowProviderError('usage_invalid')
            details = getattr(usage,'prompt_tokens_details',None)
            cached = getattr(details,'cached_tokens',None)
            hit = getattr(usage,'prompt_cache_hit_tokens',None)
            miss = getattr(usage,'prompt_cache_miss_tokens',None)
            if cached is None: cached = hit
            if cached is not None and (type(cached) is not int or not 0<=cached<=pin):
                raise ShadowProviderError('usage_invalid')
            if hit is not None and (type(hit) is not int or hit!=cached):
                raise ShadowProviderError('cache_usage_conflict')
            if miss is not None and (type(miss) is not int or cached is None or miss!=pin-cached):
                raise ShadowProviderError('cache_usage_conflict')
            reasoning = getattr(getattr(usage,'completion_tokens_details',None),'reasoning_tokens',None)
            if reasoning is not None and (type(reasoning) is not int or not 0<=reasoning<=pout):
                raise ShadowProviderError('usage_invalid')
            self.metrics.update(usage_known=True,input_tokens=pin,output_tokens=pout,total_tokens=total,
                cached_input_tokens=cached,reasoning_tokens=reasoning)
            # This is a conservative peak-rate estimate, NOT an exact bill or
            # a claim that cancellation/unknown reasoning cannot incur charges.
            cost=(Decimal(pin-(cached or 0))*INPUT_PRICE+Decimal(cached or 0)*CACHE_PRICE+
                  Decimal(pout)*OUTPUT_PRICE)/1000000
            self.metrics['cost_cny_at_peak_rates'] = str(cost)
            if response.model not in (MODEL,'deepseek-ai/'+DOCUMENTED_VERSION):
                raise ShadowProviderError('response_model_mismatch')
            self.metrics.update(response_model=response.model)
            if pin>self.input_limit or pout>self.output_limit: raise ShadowProviderError('reported_token_budget_exceeded')
            if len(response.choices)!=1: raise ShadowProviderError('unexpected_choice_count')
            choice=response.choices[0]; message=choice.message
            if choice.finish_reason!='stop': raise ShadowProviderError('incomplete_completion')
            if getattr(message,'refusal',None): raise ShadowProviderError('provider_refusal')
            if message.tool_calls or message.function_call: raise ShadowProviderError('unexpected_tool_call')
            if getattr(message,'reasoning_content',None) or reasoning:
                raise ShadowProviderError('unexpected_reasoning_in_non_thinking_mode')
            content=message.content
            if not isinstance(content,str) or not content.strip(): raise ShadowProviderError('empty_completion')
            if len(content.encode('utf-8'))>65536: raise ShadowProviderError('completion_too_large')
            strict_json_loads(content)
            self.metrics['state']='response_received'
            return content  # B1/B2 still perform the full source/fact/relation audit.
        except Exception as exc:
            self.metrics['state']=str(exc) if isinstance(exc,ShadowProviderError) else type(exc).__name__
            status=getattr(exc,'status_code',None)
            if type(status) is int and 100<=status<=599:self.metrics['http_status']=status
            raise ShadowProviderError('shadow_provider_failed') from None
        finally:
            self.metrics['provider_elapsed_ms']=(monotonic()-started)*1000
            self.metrics['completed_at_utc']=datetime.now(timezone.utc).isoformat()
            if self.config.usage_file:
                with Path(self.config.usage_file).open('x',encoding='utf-8') as out:
                    out.write(canonical_json(self.metrics))

    def close(self): self.client.close()


def create_judge(**config):
    return SiliconFlowShadowJudge(SiliconFlowShadowConfig.model_validate_json(canonical_json(config)))
