"""Allowlisted R0 worker. No release, review session, metrics or source repository."""
import sys
from pathlib import Path

from data_analysis_agent.core.serialization import canonical_json, strict_json_loads
from data_analysis_agent.reviews.e1_preparation import load, require
from data_analysis_agent.reviews.e1a_protocol import PredictionInput, digest, validate_prediction_input
from data_analysis_agent.reviews.e1a_prediction import _predict_r0_validated
from data_analysis_agent.reviews.evidence_judge import EvidenceBatch, _validate_sources
from data_analysis_agent.reviews.query_contract import ContractModel, Digest, ScopeRef
from data_analysis_agent.core.validation import Text
from typing import Literal

FIXED_CASES = (
    ('CASE-004', 'Q20', 'unfiltered'), ('CASE-009', 'Q20', 'filtered'),
    ('CASE-012', 'Q15', 'filtered'), ('CASE-023', 'Q07', 'unfiltered'),
    ('CASE-034', 'Q07', 'filtered'), ('CASE-040', 'Q21', 'filtered'),
    ('CASE-005', 'Q17', 'filtered'), ('CASE-028', 'Q23', 'unfiltered'),
)


class LaunchGrant(ContractModel):
    version: Literal['e1a-b3-launch-grant-v1'] = 'e1a-b3-launch-grant-v1'
    action: Literal['b3_eight_case_r0_debug'] = 'b3_eight_case_r0_debug'
    request_sha256: Digest
    config_sha256: Digest
    execution_permit_sha256: Digest
    authorization_basis_sha256: Digest


class WorkerRequest(ContractModel):
    version: Literal['e1a-b3-worker-request-v1'] = 'e1a-b3-worker-request-v1'
    case_id: Text
    prediction: PredictionInput
    input_sha256: Digest
    expected_scope: ScopeRef
    comparison_batch: EvidenceBatch
    comparison_sha256: Digest
    # A local controller integrity binding, never independent historical provenance.
    source_policy: Literal['local_saved_sources_cross_time_v1'] = 'local_saved_sources_cross_time_v1'
    history_status: Literal['history_unproven'] = 'history_unproven'
    independent_seal: Literal[False] = False


def execute_request(value, *, expected_request_sha256, launch_grant=None, expected_grant_sha256=None):
    r = load(value, WorkerRequest)
    require(digest(r) == expected_request_sha256, 'b3_worker_request_hash')
    p = validate_prediction_input(r.prediction, expected_scope=r.expected_scope, expected_input_sha256=r.input_sha256)
    if p.data_kind == 'frozen_query':
        b = p.prepared.candidate_batch
        require((r.case_id,b.query_id,b.mode) in FIXED_CASES, 'b3_worker_case_scope')
        require(launch_grant is not None and expected_grant_sha256 is not None, 'b3_worker_execution_grant_required')
        grant = load(launch_grant, LaunchGrant)
        require(digest(grant) == expected_grant_sha256 and grant.request_sha256 == expected_request_sha256, 'b3_worker_launch_grant_binding')
    require(digest(r.comparison_batch) == r.comparison_sha256, 'b3_comparison_hash')
    _validate_sources(p.prepared.candidate_batch, r.comparison_batch, r.expected_scope)
    return _predict_r0_validated(p)


def main():
    # No reference/session paths. Frozen queries also require a controller launch grant.
    request = strict_json_loads(Path(sys.argv[1]).read_bytes())
    grant = strict_json_loads(Path(sys.argv[3]).read_bytes()) if len(sys.argv) == 5 else None
    out = execute_request(request, expected_request_sha256=sys.argv[2], launch_grant=grant,
                          expected_grant_sha256=sys.argv[4] if len(sys.argv) == 5 else None)
    sys.stdout.buffer.write(canonical_json(out.model_dump(mode='json')).encode('utf-8'))


if __name__ == '__main__':
    main()
