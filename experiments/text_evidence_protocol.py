"""Independent text-only output experiment. No provider, file I/O or Agent hook.

Only a new versioned output can be resolved. P0 must independently validate the
in-memory sources; unique location and B2 structure never verify semantics.
"""
from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field

from data_analysis_agent.agents import review_structured_evidence as protocol
from data_analysis_agent.core.serialization import canonical_json, content_sha256, strict_json_loads
from data_analysis_agent.core.validation import Text
from data_analysis_agent.reviews.query_contract import ContractModel, Digest
from data_analysis_agent.reviews.semantic_retrieval import SemanticRetrievalError
from experiments.text_evidence_locator import locate_fragment

OUTPUT_VERSION = 'p5b-text-evidence-output-experiment-v1'
MAX_OUTPUT_BYTES = 65536


class TextReference(ContractModel):
    key: Text
    source_identity_sha256: Digest
    text: str = Field(min_length=1, max_length=8000)


class TextFactJudgment(protocol.FactJudgment):
    support_refs: tuple[TextReference, ...] = ()
    refutation_refs: tuple[TextReference, ...] = ()
    context_veto_refs: tuple[TextReference, ...] = ()


class TextRelationJudgment(protocol.RelationJudgment):
    support_refs: tuple[TextReference, ...] = ()
    refutation_refs: tuple[TextReference, ...] = ()


class TextJudgeOutput(protocol.JudgeOutput):
    version: Literal['p5b-text-evidence-output-experiment-v1'] = Field(...)
    fact_judgments: tuple[TextFactJudgment, ...] = ()
    relation_judgments: tuple[TextRelationJudgment, ...] = ()


def output_schema():
    return TextJudgeOutput.model_json_schema() | {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        '$id': 'urn:data-analysis-agent:p5b:text-evidence-output:experiment-v1',
    }


@dataclass(frozen=True)
class OfflineTextRun:
    outcome: protocol.ShadowOutcome
    resolved_output: protocol.JudgeOutput | None = field(default=None, repr=False)
    evidence_status: Literal['offline_experiment_only'] = 'offline_experiment_only'
    failure_stage: str | None = None
    failure_details: tuple[dict, ...] = ()


def _reference(request, reference, reference_type):
    location = locate_fragment(request, key=reference.key,
        source_identity_sha256=reference.source_identity_sha256,
        reference_type=reference_type, text=reference.text)
    if location['status'] != 'unique':
        raise protocol.StructuredEvidenceError('text_reference_'+location['status'],
            dict(candidate_key=next((c.key for c in request.candidates if c.key==reference.key),None),
                 reference_type=reference_type))
    start, end = location['locations'][0]
    return reference.model_dump(mode='json') | dict(start=start, end=end)


def evaluate(contract, result, raw, *, trusted_contract_sha256, request_id):
    """Prepare through original P0/B1; locate only new text refs; audit through original B2.

    No partial resolved output escapes on failure. A frozen JSON envelope cannot
    replace a source-validated in-memory result. No historical-output repair API.
    """
    try:
        request = protocol.prepare_input(contract, result,
            trusted_contract_sha256=trusted_contract_sha256, request_id=request_id)
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError, SemanticRetrievalError) as exc:
        code = (str(exc) if isinstance(exc, protocol.StructuredEvidenceError) else
                exc.code if isinstance(exc, SemanticRetrievalError) else 'invalid_input')
        return OfflineTextRun(protocol.ShadowOutcome('input_invalid', (code,)))
    return resolve_output(request, raw)


def resolve_output(request, raw):
    """Internal: caller owns a P0-prepared request; raw cannot establish source trust."""
    stage = 'text_schema'
    raw_value = None
    try:
        protocol.require(isinstance(raw, str) and len(raw.encode('utf-8')) <= MAX_OUTPUT_BYTES,
                         'text_output_invalid_or_too_large')
        raw_value = strict_json_loads(raw)
        output = protocol.parse(raw_value, TextJudgeOutput)
        stage = 'input_binding'
        protocol.require(output.request_id == request.request_id
            and output.input_sha256 == content_sha256(request.model_dump(mode='json')), 'input_binding_mismatch')
        stage = 'matrix_binding'
        if output.execution_status == 'completed':
            expected = {(fact.id,c.key) for fact in request.contract.required_facts.items for c in request.candidates}
            actual = [(row.fact_id,row.key) for row in output.fact_judgments]
            protocol.require(set(actual) == expected and len(actual) == len(expected), 'fact_matrix_mismatch')
        stage = 'text_location'
        value = output.model_dump(mode='json')
        value['version'] = protocol.OUTPUT_VERSION
        for original, converted in zip(output.fact_judgments, value['fact_judgments']):
            for name in ('support_refs', 'refutation_refs', 'context_veto_refs'):
                converted[name] = [_reference(request, ref, name) for ref in getattr(original, name)]
        for original, converted in zip(output.relation_judgments, value['relation_judgments']):
            for name in ('support_refs', 'refutation_refs'):
                converted[name] = [_reference(request, ref, name) for ref in getattr(original, name)]
        stage = 'b1_conversion'
        resolved = protocol.parse(value, protocol.JudgeOutput)
        stage = 'b2_audit'
        details = []
        outcome = protocol.audit_output(request, canonical_json(value), failure_details=details)
        valid = outcome.execution_state == 'validated_shadow'
        if outcome.reason_codes == ('refutation_without_basis',):
            missing = next((r for r in output.fact_judgments if r.status=='refuted'
                            and not r.refutation_refs and not r.context_veto_refs),None)
            if missing is not None:
                details[0].update(candidate_key=missing.key,reference_type=None)
        return OfflineTextRun(outcome, resolved if valid else None,
            failure_stage=None if valid else stage, failure_details=tuple(details))
    except (ValueError, TypeError, AttributeError, KeyError, UnicodeError, RecursionError) as exc:
        code = str(exc) if isinstance(exc, protocol.StructuredEvidenceError) else 'invalid_text_output_schema_or_json'
        if code in ('text_reference_identity_mismatch','text_reference_source_text_invalid'):
            stage = 'source_binding'
        detail = dict(error_code=code, candidate_key=None, reference_type=None)
        if stage=='text_schema' and callable(getattr(exc,'errors',None)) and isinstance(raw_value,dict):
            loc = exc.errors(include_input=False)[0].get('loc',())
            if len(loc)>=2 and loc[0]=='fact_judgments' and isinstance(loc[1],int):
                row = raw_value.get('fact_judgments',[])[loc[1]]
                if isinstance(row,dict) and isinstance(row.get('key'),str) and row['key'] in {c.key for c in request.candidates}:
                    detail['candidate_key'] = row['key']
                if len(loc)>=3 and loc[2] in ('support_refs','refutation_refs','context_veto_refs'):
                    detail['reference_type'] = loc[2]
        if isinstance(exc,protocol.StructuredEvidenceError) and exc.detail:
            detail.update(exc.detail)
        return OfflineTextRun(protocol.ShadowOutcome('output_invalid', (code,)),
            failure_stage=stage, failure_details=(detail,))
