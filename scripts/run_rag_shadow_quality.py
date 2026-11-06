"""Fixed synthetic Q1-Q3 experiment. Default: offline preflight, never inference."""
import argparse
from collections import Counter
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
# This is a development experiment, intentionally reusing synthetic source fixtures.
for directory in (ROOT, ROOT/'src', ROOT/'tests'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from data_analysis_agent.agents import review_shadow_siliconflow as provider
from data_analysis_agent.agents import review_structured_evidence as protocol
from data_analysis_agent.agents.review_shadow_adapter import StructuredEvidenceShadow, ShadowSettings, JudgeFactory
from data_analysis_agent.core.serialization import canonical_json, content_sha256, strict_json_loads
from scripts.run_rag_shadow_boundaries import _save, _usage_problem, EXPECTED_PROMPT
from test_review_structured_shadow import verified
import test_p5b1_structured_evidence_design as synthetic

FIXTURE = ROOT/'tests/fixtures/shadow_quality_cases_v1.json'
FIXTURE_SHA256 = '5254fb26cad7c3df4476dc91ecb8c9679300dbe4fd5ed0b77fe8a65fffcce60b'
OUTPUT_DIR = ROOT/'outputs/rag-semantic-retrieval/four-state-synthetic-shadow-v1'
ESTIMATES = (7695, 7631, 7750)
STATES = ('supported', 'refuted', 'not_supported', 'unknown')
FACTORY = 'data_analysis_agent.agents.review_shadow_siliconflow:create_judge'


def load_fixed():
    raw = FIXTURE.read_bytes()
    if sha256(raw).hexdigest() != FIXTURE_SHA256:
        raise ValueError('fixed_fixture_changed')
    data = strict_json_loads(raw.decode('utf-8'))
    expected = [[f'C{i:02}' for i in range(start, start+5)] for start in (1, 6, 11)]
    if data['batches'] != expected or Counter(c['expected_status'] for c in data['cases']) != dict(
            supported=3, refuted=2, not_supported=8, unknown=2):
        raise ValueError('fixed_cases_changed')
    return data


def prepare_batch(cases):
    # Allowlist BEFORE source preparation: no expected label, reason, veto annotation or scoring data.
    inputs = [{k: c[k] for k in ('id', 'question', 'quote', 'review_text')} for c in cases]
    contract = synthetic.contract(inputs[0]['question'])
    rows = [(c['review_text'], c['quote'], int(c['id'][1:])) for c in inputs]
    contract, result, _ = verified(contract, rows)  # FakeEncoder/FakeRepository only; real P0 validation.
    request_id = 'SYNTHETIC-QUALITY-'+'-'.join(c['id'] for c in inputs)
    request = protocol.prepare_input(contract, result,
        trusted_contract_sha256=content_sha256(contract.model_dump(mode='json')), request_id=request_id)
    return contract, result, request


def prepared_batches(data):
    lookup = {c['id']: c for c in data['cases']}
    for index, ids in enumerate(data['batches']):
        cases = [lookup[cid] for cid in ids]
        yield f'Q{index+1}', cases, prepare_batch(cases)


def preflight(shadow_env):
    data = load_fixed()
    if (provider.MODEL, provider.PROMPT_VERSION, sha256(provider.SYSTEM_PROMPT.encode()).hexdigest(),
            provider.INPUT_LIMIT, provider.OUTPUT_LIMIT) != (
            'deepseek-ai/DeepSeek-V4.1-Flash', 'p5-shadow-siliconflow-instructions-v2', EXPECTED_PROMPT, 8000, 2500):
        raise ValueError('fixed_protocol_changed')
    for name, schema in protocol.schemas().items():
        saved = ROOT/'docs/schemas'/f'p5b1-{name.replace("_", "-")}.schema.json'
        if schema != strict_json_loads(saved.read_text(encoding='utf-8')):
            raise ValueError('schema_changed')
    # Configuration inspection only. Risk acceptance here never authorizes a request.
    config = provider.preflight(shadow_env, estimated_budget_risk_accepted=True)
    missing = list(config['missing'])
    batches = []
    for index, (qid, cases, (_, _, request)) in enumerate(prepared_batches(data)):
        payload = protocol.model_payload(request)
        body = provider.request_body(payload)
        estimate = provider.estimated_input_tokens(body)
        if estimate != ESTIMATES[index] or len(request.candidates) != 5:
            raise ValueError('fixed_request_changed')
        for field in ('expected_status', 'expected_reason', 'veto_text', 'target_fact', 'provenance'):
            if field in payload:
                raise ValueError('expectation_leak')
        if any(c['expected_reason'] in payload for c in cases):
            raise ValueError('expectation_leak')
        batches.append(dict(batch=qid, cases=[c['id'] for c in cases], request_id=request.request_id,
            input_sha256=content_sha256(request.model_dump(mode='json')), input_estimate=estimate,
            candidate_count=5, expected_answers_sent=False))
    if OUTPUT_DIR.exists():
        missing.append('experiment_already_claimed')
    return dict(status='blocked' if missing else 'ready_pending_explicit_authorization', missing=missing,
        batches=batches, fixture_sha256=FIXTURE_SHA256, output_dir=str(OUTPUT_DIR),
        model=provider.MODEL, prompt_version=provider.PROMPT_VERSION,
        max_model_calls=3, max_retries=0, input_limit=8000, output_limit=2500,
        outer_deadline_seconds=30, sdk_phase_timeouts=dict(read=20, connect=5, write=10, pool=5),
        conditional_peak_cost_cny='0.108', guaranteed_bill_cap=False,
        spending_authorized=False, real_llm_calls=0, real_database_calls=0, real_embedding_calls=0,
        publication_allowed=False, semantic_verified=False)


def compare(cases, request, judgments):
    """Label comparison only; semantic citation reading remains independent and pending."""
    rows = []
    matrix = {expected: {actual: 0 for actual in STATES} for expected in STATES}
    by_key = {j.key: j for j in judgments.fact_judgments} if judgments else {}
    for case, candidate in zip(cases, request.candidates):
        judgment = by_key.get(candidate.key)
        actual = judgment.status if judgment else None
        expected = case['expected_status']
        errors = []
        if actual:
            matrix[expected][actual] += 1
            if actual != expected:
                if actual == 'supported': errors.append('false_support')
                if expected == 'supported': errors.append('false_rejection')
                if 'refuted' in (actual, expected): errors.append('refutation_label_error')
                if 'unknown' in (actual, expected): errors.append('unknown_misclassification')
                if not errors: errors.append('status_mismatch')
                if case['id'] == 'C02' and actual == 'refuted': errors.append('opposite_experience_as_refutation')
                if case['id'] in ('C05', 'C13') and actual == 'supported': errors.append('hearsay_as_personal_experience')
                if case['id'] in ('C06', 'C14') and actual == 'supported': errors.append('hypothetical_as_experience')
                if case['id'] in ('C07', 'C08') and actual == 'supported': errors.append('service_role_conflation')
                if case['id'] == 'C09' and actual != 'unknown': errors.append('unresolved_negation_forced')
        rows.append(dict(case=case['id'], expected_status=expected, model_status=actual,
            status_match=actual == expected if actual else None, error_types=errors,
            execution_coverage='completed' if actual else 'N/A_no_valid_output',
            target_fact=case['target_fact'], quote=case['quote'], review_text=case['review_text'],
            expected_reason=case['expected_reason'], judgment=judgment.model_dump(mode='json') if judgment else None,
            semantic_basis_review='pending_independent_reading', model_rationale_is_not_proof=True))
    return dict(candidates=rows, confusion_matrix=matrix, semantic_verified=False, publication_allowed=False)


def run_authorized(shadow_env, *, send_authorized=False, estimated_budget_risk_accepted=False):
    if not send_authorized or not estimated_budget_risk_accepted:
        raise ValueError('explicit_send_and_fee_authorization_required')
    check = preflight(shadow_env)
    if check['missing']:
        raise ValueError('quality_preflight_incomplete')
    data = load_fixed()
    batches = list(prepared_batches(data))
    config = provider.scoped_config(shadow_env).model_copy(update={'estimated_budget_risk_accepted': True})
    # Same once-only directory claim as the existing runner, including failed/partial runs.
    OUTPUT_DIR.mkdir(parents=True, exist_ok=False)
    records = [dict(batch=qid, status='not_executed', model_calls=0) for qid, _, _ in batches]
    total = Decimal('0')
    stop = None
    comparisons = []
    record = {}
    try:
        _save(OUTPUT_DIR/'preflight.json', check)
        for record, (qid, cases, (contract, result, request)) in zip(records, batches):
            directory = OUTPUT_DIR/qid
            directory.mkdir()
            _save(directory/'model-input.json', strict_json_loads(protocol.model_payload(request)))
            _save(directory/'contract.json', contract.model_dump(mode='json'))
            _save(directory/'retrieval.json', result.to_dict())
            case_config = config.model_copy(update={'usage_file': str(directory/'provider-usage.json')})
            adapter = StructuredEvidenceShadow(settings=ShadowSettings(enabled=True, timeout_seconds=30),
                judge_factory=JudgeFactory(entrypoint=FACTORY, config_json=canonical_json(case_config.model_dump(mode='json'))))
            record.update(status='started', model_calls=None)
            run = adapter.run(contract, result, trusted_contract_sha256=content_sha256(contract.model_dump(mode='json')),
                              request_id=request.request_id)
            _save(directory/'shadow-execution.json', run.debug_record())
            if run.judgments is not None:
                _save(directory/'shadow-judgments.json', run.judgments.model_dump(mode='json'))
            comparison = compare(cases, request, run.judgments if run.outcome.execution_state == 'validated_shadow' else None)
            comparisons.extend(comparison['candidates'])
            _save(directory/'comparison.json', comparison)
            record.update(status=run.outcome.execution_state, total_ms=run.diagnostics['total_ms'])
            usage_path = directory/'provider-usage.json'
            usage = strict_json_loads(usage_path.read_text(encoding='utf-8')) if usage_path.exists() else None
            record['usage'] = usage
            record['model_calls'] = usage.get('model_calls') if usage else None
            problem = _usage_problem(usage, request.request_id, run.diagnostics['input_sha256'])
            if not problem:
                total += Decimal(usage['cost_cny_at_peak_rates'])
            if run.outcome.publication_allowed or run.outcome.semantic_verified or run.outcome.permitted_target_fact_ids:
                stop = 'shadow_isolation_violation'
            elif not run.diagnostics['reaped'] or not run.diagnostics['threads_released']:
                stop = 'worker_cleanup_failed'
            elif problem:
                stop = problem
            elif run.outcome.execution_state != 'validated_shadow':
                stop = 'shadow_validation_failed'
            elif usage['input_tokens'] > 8000 or usage['output_tokens'] > 2500 or total > Decimal('0.108'):
                stop = 'observed_budget_exceeded'
            if stop:
                break
    except (Exception, KeyboardInterrupt) as exc:
        stop = 'cancelled' if isinstance(exc, KeyboardInterrupt) else 'execution_failed'
        record.update(status=stop, exception_type=type(exc).__name__)
    finally:
        done = {row['case'] for row in comparisons}
        for _, cases, (_, _, request) in batches:
            comparisons.extend(row for row in compare(cases, request, None)['candidates'] if row['case'] not in done)
        matrix = {s: {t: 0 for t in STATES} for s in STATES}
        for row in comparisons:
            if row['model_status']:
                matrix[row['expected_status']][row['model_status']] += 1
        summary = dict(status='stopped' if stop else 'completed', stop_reason=stop, batches=records,
            candidates=comparisons, confusion_matrix=matrix, known_peak_cost_cny=str(total),
            actual_bill_known=False, publication_allowed=False, semantic_verified=False,
            semantic_basis_review='pending_independent_reading')
        _save(OUTPUT_DIR/'execution-summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('preflight', 'run'), nargs='?', default='preflight')
    parser.add_argument('--authorize-fixed-synthetic-shadow', action='store_true')
    parser.add_argument('--accept-estimated-token-risk', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'preflight':
            result = preflight(ROOT/'.env.shadow')
        else:
            result = run_authorized(ROOT/'.env.shadow', send_authorized=args.authorize_fixed_synthetic_shadow,
                estimated_budget_risk_accepted=args.accept_estimated_token_risk)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(result['status'] in ('blocked', 'stopped'))
    except Exception:
        print(json.dumps(dict(status='blocked', error='quality_configuration_or_authorization_failed', publication_allowed=False)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
