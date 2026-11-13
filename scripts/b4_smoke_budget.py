"""One B4 smoke process: reserve before HTTP, never refund, fail closed without a provider bound.

No paid execution is authorized by importing this test runner. The upstream byte
bound is only an offline reference: SiliconFlow serialization/billing binding is
not verified. There is deliberately no flag or JSON field that enables it live.
"""
from contextlib import ExitStack
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import threading
from time import perf_counter
from unittest.mock import patch

import httpx
from openai import OpenAI

MODEL = 'Pro/deepseek-ai/DeepSeek-V3.2'
BASE_URL = 'https://api.siliconflow.cn/v1'
QUESTION = '商品100013588055的物流差评具体体现在哪里？'
OUTPUTS = {'topic_discovery': 1200, 'topic_classification': 3000,
           'answer_draft': 360, 'answer_support_review': 280}
INPUT_PRICE, OUTPUT_PRICE, LIMIT = Decimal('0.000004'), Decimal('0.000006'), Decimal('1')


class BudgetBlocked(RuntimeError):
    pass


def upstream_byte_candidate(payload):
    """Upper bound for the inspected *upstream* two-message chat encoding only.

    Official V3.2: identity normalization, ByteLevel without prefix insertion,
    BPE merges only. Each token consumes >=1 input byte; added special tokens
    contract byte spans. Its byte-only post-processor adds no token IDs.
    Role delimiters/BOS/non-thinking assistant prefix are included literally.
    This is NOT proof of SiliconFlow's served template or billable prompt.
    """
    messages = payload['messages']
    if (len(messages) != 2 or [m.get('role') for m in messages] != ['system', 'user']
            or any(set(m) != {'role', 'content'} or type(m['content']) is not str for m in messages)):
        raise BudgetBlocked('unsupported_messages')
    encoded = '<｜begin▁of▁sentence｜>' + messages[0]['content']
    encoded += '<｜User｜>' + messages[1]['content'] + '<｜Assistant｜></think>'
    return len(encoded.encode('utf-8'))


class B4Budget:
    def __init__(self, ledger, *, mock_transport=None, clock=perf_counter):
        if mock_transport is not None and type(mock_transport) is not httpx.MockTransport:
            raise TypeError('Only an explicit MockTransport can use offline counting.')
        self.ledger = Path(ledger)
        self.mock_transport, self.clock = mock_transport, clock
        self.lock = threading.RLock()
        self.rows, self.reserved, self.halted = [], Decimal(0), None
        self.task_id, self.started, self.phase_started = None, None, {}
        self.clients, self.patches = set(), None
        self.pid = os.getpid()
        # Never reset an old run's reservations, even after a process restart.
        with self.ledger.open('x', encoding='utf-8') as handle:
            json.dump(self.snapshot(), handle)

    def snapshot(self):
        with self.lock:
            return dict(task_id=self.task_id, requests=len(self.rows),
                reserved_cny=str(self.reserved), halted=self.halted,
                real_ready=False, input_bound_status='offline_reference_only' if self.mock_transport else 'unproven',
                records=deepcopy(self.rows))

    def _save(self):
        temporary = self.ledger.with_suffix(self.ledger.suffix + '.tmp')
        with temporary.open('w', encoding='utf-8') as handle:
            json.dump(self.snapshot(), handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.ledger)

    def require_real_ready(self):
        raise BudgetBlocked('provider_input_token_and_billable_template_bound_unproven')

    def begin_task(self, task_id):
        with self.lock:
            if self.task_id is not None or not task_id:
                raise BudgetBlocked('business_task_limit')
            self.task_id, self.started = task_id, self.clock()
            self._save()

    def _reserve(self, request):
        with self.lock:
            if os.getpid() != self.pid or self.started is None:
                raise BudgetBlocked('wrong_process_or_no_active_task')
            if self.halted:
                raise BudgetBlocked(self.halted)
            if len(self.rows) >= 6:
                raise BudgetBlocked('total_request_limit')
            if request.method != 'POST' or str(request.url) != BASE_URL + '/chat/completions':
                raise BudgetBlocked('unapproved_endpoint')
            body = json.loads(request.content)
            allowed = {'model', 'messages', 'temperature', 'max_tokens', 'response_format', 'enable_thinking'}
            if (set(body) - allowed or body.get('model') != MODEL or body.get('enable_thinking') is not False
                    or body.get('response_format') != {'type': 'json_object'}):
                raise BudgetBlocked('unapproved_request_or_thinking')
            candidate = upstream_byte_candidate(body)  # Checks the final messages, including embedded schema.
            stage = json.loads(body['messages'][1]['content']).get('stage')
            if stage not in OUTPUTS or type(body.get('max_tokens')) is not int or body['max_tokens'] != OUTPUTS[stage]:
                raise BudgetBlocked('unapproved_stage_or_output_limit')
            phase = 'topic' if stage.startswith('topic_') else 'answer'
            if sum(r['phase'] == phase for r in self.rows) >= (4 if phase == 'topic' else 2):
                raise BudgetBlocked(phase + '_request_limit')
            if sum(r['stage'] == stage for r in self.rows) >= (3 if stage == 'topic_classification' else 1):
                raise BudgetBlocked('stage_request_limit')
            digest = sha256(request.content).hexdigest()
            if any(r['request_sha256'] == digest for r in self.rows) or request.headers.get('x-stainless-retry-count', '0') != '0':
                raise BudgetBlocked('retry_not_authorized')
            if phase == 'topic' and 'answer' in self.phase_started:
                raise BudgetBlocked('phase_order')
            now = self.clock()
            start = self.phase_started.get(phase, now)
            if not math.isfinite(now) or now < self.started or now - self.started >= 180 or now - start >= (120 if phase == 'topic' else 30):
                raise BudgetBlocked('deadline_exceeded')
            timeout = request.extensions.get('timeout', {})
            remaining = min(180 - (now - self.started), (120 if phase == 'topic' else 30) - (now - start))
            cap = min(30 if phase == 'topic' else 15, remaining)
            if set(timeout) != {'connect', 'read', 'write', 'pool'} or any(
                    type(t) not in (int, float) or not math.isfinite(t) or not 0 < t <= cap for t in timeout.values()):
                raise BudgetBlocked('unbounded_request_timeout')
            if self.mock_transport is None:
                # No arbitrary byte multiplier, template allowance, or approval flag is proof.
                self.require_real_ready()
            upper = candidate  # Valid only for this script's explicit offline fixture contract.
            reserve = upper * INPUT_PRICE + body['max_tokens'] * OUTPUT_PRICE
            if self.reserved + reserve > LIMIT:
                raise BudgetBlocked('cost_limit')
            row = dict(attempt=len(self.rows) + 1, stage=stage, phase=phase, started=now,
                timeout_seconds=cap, request_sha256=digest, request_bytes=len(request.content),
                input_tokens_upper=upper, input_bound='OFFLINE_FIXTURE_NOT_PROVIDER_PROOF',
                output_tokens_upper=body['max_tokens'], reserved_cny=str(reserve),
                status='reserved', usage=None, usage_cost_upper_cny=None)
            self.rows.append(row)
            self.reserved += reserve
            self.phase_started.setdefault(phase, now)
            self._save()  # Durable reservation BEFORE delegation to HTTP transport.
            return row

    def _send(self, original, client, request, **kwargs):
        if client not in self.clients:
            raise BudgetBlocked('unregistered_http_client')
        row = self._reserve(request)
        try:
            request.extensions['_b4_reservation'] = (self, row['attempt'])
            # A redirect would be another HTTP attempt without a new reservation.
            response = original(client, request, **{**kwargs, 'follow_redirects': False})
            response.read()
            with self.lock:
                row['elapsed_seconds'] = self.clock() - row['started']
                row['http_status'] = response.status_code
                if response.status_code != 200:
                    self.halted = 'provider_http_error'
                else:
                    data = response.json()
                    row['usage'] = deepcopy(data.get('usage'))
                    usage = row['usage'] or {}
                    inputs, outputs = usage.get('prompt_tokens'), usage.get('completion_tokens')
                    details = usage.get('completion_tokens_details') or {}
                    input_details = usage.get('prompt_tokens_details') or {}
                    if (data.get('model') != MODEL or any(type(n) is not int or n < 0 for n in (inputs, outputs))
                            or usage.get('total_tokens') != inputs + outputs):
                        self.halted = 'identity_or_usage_unverified'
                    elif (details.get('reasoning_tokens') != 0 or any(
                            c.get('message', {}).get('reasoning_content') for c in data.get('choices', []))):
                        self.halted = 'reasoning_not_verified_zero'
                    elif inputs > row['input_tokens_upper'] or outputs > row['output_tokens_upper']:
                        self.halted = 'usage_exceeded_bound'
                    elif (set(usage) - {'prompt_tokens', 'completion_tokens', 'total_tokens', 'completion_tokens_details',
                            'prompt_tokens_details', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens'}
                            or set(details) - {'reasoning_tokens'} or set(input_details) - {'cached_tokens'}):
                        self.halted = 'unknown_billable_category'
                    else:
                        # Ignore cache discounts even in the post-response estimate.
                        row['usage_cost_upper_cny'] = str(inputs * INPUT_PRICE + outputs * OUTPUT_PRICE)
                if row['elapsed_seconds'] >= row['timeout_seconds']:
                    self.halted = 'request_deadline_exceeded'
                row['status'] = 'received' if not self.halted else 'failed'
                self._save()
                if self.halted and response.status_code == 200:
                    raise BudgetBlocked(self.halted)
            return response  # Original response; no fake completion, usage or business result.
        except Exception as exc:
            with self.lock:
                self.halted = self.halted or 'provider_request_failed'
                row.update(status='failed', exception_type=type(exc).__name__)
                try:
                    self._save()
                except Exception as audit_error:
                    exc.add_note('Budget journal failed: ' + type(audit_error).__name__)
            raise  # Preserve the original transport/business exception.

    def install(self):
        if self.patches is not None or getattr(httpx.Client.send, '_b4_budget_guard', False):
            raise BudgetBlocked('guard_already_installed')
        original_init, original_send = OpenAI.__init__, httpx.Client.send
        original_transport = httpx.HTTPTransport.handle_request
        budget = self
        def initialize(client, *args, **kwargs):
            if (args or kwargs.get('max_retries') != 0 or str(kwargs.get('base_url', '')).rstrip('/') != BASE_URL
                    or kwargs.get('http_client') is not None):
                raise BudgetBlocked('unapproved_sdk_options_or_retries')
            if budget.mock_transport is not None:
                kwargs['http_client'] = httpx.Client(transport=budget.mock_transport)
            original_init(client, **kwargs)
            budget.clients.add(client._client)
        def send(client, request, **kwargs):
            return budget._send(original_send, client, request, **kwargs)
        async def deny_async(*args, **kwargs):
            raise BudgetBlocked('unplanned_async_transport')
        def transport(transport, request):
            reservation = request.extensions.get('_b4_reservation')
            if budget.mock_transport is not None or not reservation or reservation[0] is not budget:
                raise BudgetBlocked('unguarded_network_transport')
            return original_transport(transport, request)
        send._b4_budget_guard = True
        from data_analysis_agent.jobs.worker import TaskWorker
        from data_analysis_agent.agents import review_topics
        original_task = TaskWorker._execute
        original_topics = review_topics.analyze_aspect_topics
        original_topic_call = review_topics._call
        original_checked_batch = review_topics._checked_batch
        def fail_closed(original, *args, **kwargs):
            try:
                return original(*args, **kwargs)
            except Exception as exc:
                with budget.lock:
                    budget.halted = budget.halted or 'topic_call_failed'
                    try:
                        budget._save()
                    except Exception as audit_error:
                        exc.add_note('Budget journal failed: ' + type(audit_error).__name__)
                raise
        def topic_call(*args, **kwargs):
            return fail_closed(original_topic_call, *args, **kwargs)
        def checked_batch(*args, **kwargs):
            return fail_closed(original_checked_batch, *args, **kwargs)
        def topics(extracted=None, **kwargs):
            if kwargs.get('shared_budget') is not None or extracted is None:
                raise BudgetBlocked('unapproved_topic_path')
            opinions = [o for item in extracted['items'] for o in item['opinions']]
            unique = {(o['aspect'], o['quote']) for o in opinions}
            if (extracted.get('aspect') != 'logistics' or extracted.get('sentiment') != 'negative'
                    or len(opinions) > 200 or len(unique) > 60 or review_topics.BATCH_SIZE != 20):
                raise BudgetBlocked('more_than_three_batches_or_wrong_topic_scope')
            return original_topics(extracted, **kwargs)
        def task(worker, value):
            p = value['parameters']
            required = dict(source='reviews_csv', dataset_version='reviews-csv', product_ids=['100013588055'],
                date_from='2021-09-11', date_to='2022-08-26', question=QUESTION)
            if any(p.get(k) != v for k, v in required.items()):
                raise BudgetBlocked('unapproved_business_task')
            budget.require_real_ready() if budget.mock_transport is None else None
            budget.begin_task(value['task_id'])
            return original_task(worker, value)
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(OpenAI, '__init__', initialize))
        self.patches.enter_context(patch.object(httpx.Client, 'send', send))
        self.patches.enter_context(patch.object(httpx.AsyncClient, 'send', deny_async))
        self.patches.enter_context(patch.object(httpx.HTTPTransport, 'handle_request', transport))
        self.patches.enter_context(patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', deny_async))
        self.patches.enter_context(patch.object(TaskWorker, '_execute', task))
        self.patches.enter_context(patch.object(review_topics, 'analyze_aspect_topics', topics))
        self.patches.enter_context(patch.object(review_topics, '_call', topic_call))
        self.patches.enter_context(patch.object(review_topics, '_checked_batch', checked_batch))
        return self

    def close(self):
        if self.patches is not None:
            self.patches.close()
            self.patches = None
