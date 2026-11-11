"""Synthetic-only seal/score rehearsal; no real predictor or Gold access."""
import hashlib
import json
import tempfile
from pathlib import Path

import p2b1_blind_protocol as blind

BLIND_SHA256 = '8ad87b8c6dfeaf28756c82de515fb16428bf877fb2029625c1cc03f2e22c4726'
OUTCOMES = frozenset({'direct_support', 'unsupported', 'unknown', 'insufficient_evidence',
                      'explicit_opposite', 'execution_failure', 'not_executed', 'refusal'})
COMPARABLE = frozenset({'direct_support', 'unsupported', 'insufficient_evidence', 'explicit_opposite'})
SCHEMA = frozenset({'version', 'data_kind', 'case_id', 'outcome', 'citations', 'published_claims'})


def validate_blind_input(data):
    """Accept only the exact frozen bytes, then reuse the v1 no-prediction precheck."""
    if not isinstance(data, bytes) or hashlib.sha256(data).hexdigest() != BLIND_SHA256:
        raise ValueError('blind_input_hash_mismatch')
    result = blind.validate(json.loads(data))
    if result['case_id'] != 'RB03' or result['component_subquery_ready'] is not False:
        raise ValueError('unexpected_precheck')
    return {'input_valid': True, 'execution_ready': False, 'reason': 'subquery_and_os_isolation_unapproved'}


def validate_prediction(value):
    if not isinstance(value, dict) or set(value) != SCHEMA:
        raise ValueError('invalid_prediction_schema')
    if (value['version'], value['data_kind'], value['case_id']) != ('p2b2-synthetic-v1', 'synthetic', 'SYNTHETIC_EXAMPLE'):
        raise ValueError('synthetic_only')
    if value['outcome'] not in OUTCOMES or value['citations'] != [] or value['published_claims'] != []:
        raise ValueError('invalid_synthetic_result')
    return value


def seal_synthetic_prediction(value, output_root):
    """Exclusive synthetic temp output; returned hash needs an independent trusted custodian."""
    validate_prediction(value)
    root = Path(output_root).resolve(strict=True)
    if not root.is_relative_to(Path(tempfile.gettempdir()).resolve()) or not root.is_dir():
        raise ValueError('synthetic_temp_root_required')
    attempt = root / 'synthetic-sealed-attempt'
    attempt.mkdir(exist_ok=False)
    payload = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    with (attempt / 'prediction.json').open('xb') as f:
        f.write(payload)
    anchor = hashlib.sha256(payload).hexdigest()
    receipt = {'version': 'p2b2-synthetic-seal-v1', 'sha256': anchor}
    with (attempt / 'receipt.json').open('x', encoding='utf-8') as f:
        json.dump(receipt, f, sort_keys=True)
    return attempt, anchor


def score_synthetic(sealed_dir, *, independent_anchor, synthetic_reference):
    """Read only: a prediction and receipt cannot substitute for an external anchor."""
    directory = Path(sealed_dir)
    data = (directory / 'prediction.json').read_bytes()
    receipt = json.loads((directory / 'receipt.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256(data).hexdigest()
    if (not isinstance(independent_anchor, str) or digest != independent_anchor or
            receipt != {'version': 'p2b2-synthetic-seal-v1', 'sha256': digest}):
        raise ValueError('seal_or_anchor_mismatch')
    result = validate_prediction(json.loads(data))
    if (not isinstance(synthetic_reference, dict) or set(synthetic_reference) != {'data_kind', 'case_id', 'outcome'} or
            synthetic_reference['data_kind'] != 'synthetic' or synthetic_reference['case_id'] != 'SYNTHETIC_EXAMPLE' or
            synthetic_reference['outcome'] not in COMPARABLE):
        raise ValueError('synthetic_reference_required')
    outcome = result['outcome']
    return {'data_kind': 'synthetic', 'outcome': outcome,
            'fact_support_accuracy': (outcome == synthetic_reference['outcome']) if outcome in COMPARABLE else None,
            'unsupported_claim_rate': None, 'citation_precision': None,
            'refusal': outcome == 'refusal', 'execution_failure': outcome == 'execution_failure',
            'not_executed': outcome == 'not_executed'}
