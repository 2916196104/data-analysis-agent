"""P5-B2 production-side SHADOW-ONLY evidence contract.

No final answer permission or semantic proof. The P5-B1 test prototype and frozen
JSON Schemas stay independent; no imports from tests and no provider access here.
"""
from dataclasses import asdict, dataclass
from copy import deepcopy
from hashlib import sha256
from typing import Literal, Protocol

from pydantic import Field

from data_analysis_agent.agents.review_task_contract import (
    RuntimeQueryContract, evidence_question, evidence_scope_matches, task_limit,
)
from data_analysis_agent.core.serialization import canonical_json, content_sha256, strict_json_loads
from data_analysis_agent.core.validation import Text
from data_analysis_agent.reviews.query_contract import ContractModel, Digest, FactId
from data_analysis_agent.reviews.semantic_retrieval import SemanticResult, _scope, _verified_sources

INPUT_VERSION = 'p5b-evidence-input-draft-v1'
OUTPUT_VERSION = 'p5b-evidence-output-draft-v1'
Status = Literal['supported', 'refuted', 'not_supported', 'unknown']


class Candidate(ContractModel):
    rank: int = Field(ge=1)
    key: Text
    source_identity_sha256: Digest
    review_id: int = Field(gt=0)
    annotation_id: int = Field(gt=0)
    product_id: Text
    published_at: Text
    quote: str = Field(min_length=1, max_length=8000)
    quote_start: int = Field(ge=0)
    quote_end: int = Field(gt=0)
    review_text: str = Field(min_length=1, max_length=40000)
    review_text_sha256: Digest
    effective_snapshot_sha256: Digest
    review_content_sha256: Digest
    label_profile_key: Digest
    file_sha256: Digest
    line_number: int = Field(gt=0)


class JudgeInput(ContractModel):
    version: Literal['p5b-evidence-input-draft-v1'] = INPUT_VERSION
    request_id: Text
    contract: RuntimeQueryContract
    contract_sha256: Digest
    retrieval_sha256: Digest
    source_policy: Literal['current_effective_readonly_v1'] = 'current_effective_readonly_v1'
    context_policy: Literal['quote_full_review_veto'] = 'quote_full_review_veto'
    context_version: Literal['1'] = '1'
    candidates: tuple[Candidate, ...] = Field(max_length=5)


class Span(ContractModel):
    key: Text
    source_identity_sha256: Digest
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=8000)


class FactJudgment(ContractModel):
    fact_id: FactId
    key: Text
    status: Status
    support_refs: tuple[Span, ...] = ()
    refutation_refs: tuple[Span, ...] = ()
    context_veto_refs: tuple[Span, ...] = ()
    reason_code: Literal['direct_report', 'explicit_refutation', 'context_veto',
                         'no_direct_support', 'semantics_unknown', 'conflicting_evidence']
    rationale: Text


class Witness(ContractModel):
    fact_id: FactId
    key: Text


class RelationJudgment(ContractModel):
    relation_id: Text  # relation_0 etc. are backend indices, not model-created goals.
    status: Status
    witnesses: tuple[Witness, ...] = ()
    support_refs: tuple[Span, ...] = ()
    refutation_refs: tuple[Span, ...] = ()
    reason_code: Literal['relation_verified', 'relation_unknown', 'conflicting_evidence']
    rationale: Text


class JudgeOutput(ContractModel):
    version: Literal['p5b-evidence-output-draft-v1'] = OUTPUT_VERSION
    request_id: Text
    input_sha256: Digest
    execution_status: Literal['completed', 'failed']
    fact_judgments: tuple[FactJudgment, ...] = ()
    relation_judgments: tuple[RelationJudgment, ...] = ()


class MockJudgePort(Protocol):
    def judge(self, payload_json: str) -> str: ...


class PrototypeError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise PrototypeError(code)


def parse(value, model):
    raw = strict_json_loads(value) if isinstance(value, str) else value
    return model.model_validate_json(canonical_json(raw))


def prepare_input(contract, result: SemanticResult, *, trusted_contract_sha256, trusted_sources, request_id):
    """Mock backend holds the expected contract independently of the judge response.

    trusted_sources here are independently owned fixture repository records, not
    records reconstructed from the candidate. This hash is a request binding, not a seal.
    """
    contract = parse(contract.model_dump(mode='json'), RuntimeQueryContract)
    require(content_sha256(contract.model_dump(mode='json')) == trusted_contract_sha256, 'target_binding_mismatch')
    require(contract.scope_sha256 == content_sha256(contract.scope.model_dump(mode='json')), 'scope_digest_mismatch')
    require(contract.evidence_question == evidence_question(contract.original_question), 'target_text_mismatch')
    require(evidence_scope_matches(contract.original_question, contract.scope.model_dump()), 'date_binding_mismatch')
    require(contract.operation == 'fact_search' and contract.task_type == 'evidence'
            and contract.support_status == 'supported' and not contract.unsupported_reason
            and not contract.uncertainty and not contract.statistics and not task_limit(contract.original_question), 'not_eligible')
    ids = [f.id for f in contract.required_facts.items]
    require(0 < len(ids) <= 8 and len(ids) == len(set(ids)), 'unresolved_target')
    require(contract.target_entity != 'unknown' and contract.subtopic != 'unknown', 'unresolved_target')
    require(all(f.description.strip() and f.description.strip() != f.id for f in contract.required_facts.items),
            'unresolved_fact_description')
    for relation in contract.required_facts.relations:
        require(len(relation.fact_ids) == len(set(relation.fact_ids)) and set(relation.fact_ids) <= set(ids), 'invalid_relation')
        require(relation.kind != 'before' or len(relation.fact_ids) == 2, 'invalid_relation')
    expected_scope = _scope(contract.scope.model_dump(mode='json', exclude={'baseline_from', 'baseline_to'}))
    require(result.scope == expected_scope and result.query.question == contract.original_question, 'retrieval_target_mismatch')
    require((result.source_policy, result.context_policy, result.context_version, result.text_builder_version) ==
            ('current_effective_readonly_v1', 'quote_full_review_veto', '1', 'quote-utf8-v1'), 'source_policy_mismatch')
    from data_analysis_agent.reviews.embedding_store import SPACE_ID
    require(result.embedding_space_id == SPACE_ID, 'embedding_space_mismatch')
    require(result.snapshot.read_only and result.snapshot.isolation_level in ('repeatable read', 'serializable'), 'snapshot_invalid')
    keys = tuple(row.key for row in result.evidence)
    require(len(keys) == len(set(keys)) and len(keys) <= result.query.top_k, 'candidate_list_invalid')
    documents = _verified_sources(trusted_sources, keys, expected_scope)
    candidates = []
    for rank, row in enumerate(result.evidence, 1):
        doc, source = documents[row.key]
        require(row.rank == rank and row.document == doc and row.review_state == source.state
                and row.review_text == source.review_text, 'candidate_source_mismatch')
        text_hash = sha256(source.review_text.encode('utf-8')).hexdigest()
        require(row.review_text_sha256 == text_hash, 'context_hash_mismatch')
        require((result.query.aspect is None or doc.aspect == result.query.aspect)
                and (result.query.sentiment is None or doc.sentiment == result.query.sentiment), 'filter_mismatch')
        identity = content_sha256([asdict(doc), asdict(source.state), text_hash])
        fields = {name:getattr(doc, name) for name in ('review_id','annotation_id','product_id','published_at',
            'quote','quote_start','quote_end','review_content_sha256','label_profile_key','file_sha256','line_number')}
        candidates.append(Candidate(**fields, rank=rank, key=row.key, source_identity_sha256=identity,
            review_text=source.review_text, review_text_sha256=text_hash,
            effective_snapshot_sha256=source.state.effective_snapshot_sha256))
    return JudgeInput(request_id=request_id, contract=contract, contract_sha256=trusted_contract_sha256,
                      retrieval_sha256=content_sha256(result.to_dict()), candidates=tuple(candidates))


def model_payload(request):
    """Whitelist projection; do not send labeling endpoints, distances or v2 decisions."""
    value = request.model_dump(mode='json', exclude={'contract': {'scope': {'base_url'}}})
    value['input_sha256'] = content_sha256(request.model_dump(mode='json'))
    require(len(canonical_json(value).encode('utf-8')) <= 128000, 'context_budget_exceeded')
    return canonical_json(value)


def schemas():
    """Backend envelope and redacted model projection share the same goal definitions."""
    backend = JudgeInput.model_json_schema()
    wire = deepcopy(backend)
    scope = wire['$defs']['RuntimeScope']
    scope['properties'].pop('base_url')
    scope['required'].remove('base_url')
    wire['properties']['input_sha256'] = deepcopy(wire['properties']['contract_sha256'])
    wire['required'].append('input_sha256')
    values = dict(backend_input=backend, model_input=wire, model_output=JudgeOutput.model_json_schema())
    for name,value in values.items():
        value['$schema']='https://json-schema.org/draft/2020-12/schema'
        value['$id']=f'urn:data-analysis-agent:p5b1:{name}:draft-v1'
    return values


@dataclass(frozen=True)
class ShadowOutcome:
    execution_state: str
    reason_codes: tuple[str, ...]
    model_supported_fact_ids: tuple[str, ...] = ()
    missing_required_fact_ids: tuple[str, ...] = ()
    unestablished_fact_ids: tuple[str, ...] = ()
    unmet_relation_ids: tuple[str, ...] = ()
    structural_goal_satisfied: bool = False
    semantic_verified: Literal[False] = False
    publication_allowed: Literal[False] = False
    permitted_target_fact_ids: tuple = ()


def check_span(span, candidate, *, context=False):
    require(span.key == candidate.key and span.source_identity_sha256 == candidate.source_identity_sha256, 'citation_identity_mismatch')
    lower, upper = (0, len(candidate.review_text)) if context else (candidate.quote_start, candidate.quote_end)
    require(lower <= span.start < span.end <= upper
            and candidate.review_text[span.start:span.end] == span.text, 'citation_span_mismatch')


def audit_output(request, raw):
    """Structural prototype only; it deliberately never returns a production licence."""
    try:
        output = parse(raw, JudgeOutput)
        require(output.request_id == request.request_id and output.input_sha256 == content_sha256(request.model_dump(mode='json')), 'input_binding_mismatch')
        if output.execution_status == 'failed':
            require(not output.fact_judgments and not output.relation_judgments, 'failed_output_has_judgments')
            return ShadowOutcome('model_failed', ('model_declared_failed',))
        ids = tuple(f.id for f in request.contract.required_facts.items)
        candidates = {c.key:c for c in request.candidates}
        expected = {(fid,key) for fid in ids for key in candidates}
        judgments = {(j.fact_id,j.key):j for j in output.fact_judgments}
        require(set(judgments) == expected and len(judgments) == len(output.fact_judgments), 'fact_matrix_mismatch')
        for judgment in judgments.values():
            candidate = candidates[judgment.key]
            for refs, context in ((judgment.support_refs,False),(judgment.refutation_refs,False),(judgment.context_veto_refs,True)):
                require(len({canonical_json(r.model_dump()) for r in refs}) == len(refs), 'duplicate_citation')
                for ref in refs:check_span(ref,candidate,context=context)
            if judgment.status == 'supported':
                require(judgment.support_refs and not judgment.refutation_refs and not judgment.context_veto_refs
                        and judgment.reason_code == 'direct_report', 'support_without_valid_basis')
            elif judgment.status == 'refuted':
                require(not judgment.support_refs and (judgment.refutation_refs or judgment.context_veto_refs)
                        and judgment.reason_code in ('explicit_refutation','context_veto','conflicting_evidence'), 'refutation_without_basis')
            else:
                require(not judgment.support_refs and not judgment.refutation_refs
                        and judgment.reason_code in ('no_direct_support','semantics_unknown','context_veto','conflicting_evidence'), 'unknown_cannot_support')
        vetoed = {(j.fact_id,candidates[j.key].review_id) for j in judgments.values() if j.status == 'refuted' or j.context_veto_refs}
        supported = {(j.fact_id,j.key) for j in judgments.values() if j.status == 'supported'
                     and (j.fact_id,candidates[j.key].review_id) not in vetoed}
        relations = {f'relation_{i}':r for i,r in enumerate(request.contract.required_facts.relations)}
        results = {r.relation_id:r for r in output.relation_judgments}
        require(set(results) == set(relations) and len(results) == len(output.relation_judgments), 'relation_set_mismatch')
        for rid, relation in relations.items():
            result = results[rid]
            require(len({w.fact_id for w in result.witnesses}) == len(result.witnesses)
                    and all(w.fact_id in relation.fact_ids and w.key in candidates for w in result.witnesses), 'relation_witness_mismatch')
            for ref in (*result.support_refs, *result.refutation_refs):
                require(ref.key in {w.key for w in result.witnesses}, 'relation_citation_mismatch')
                check_span(ref,candidates[ref.key])
            if result.status != 'supported':
                require(not result.support_refs, 'unresolved_relation_has_support')
                require(result.status != 'refuted' or result.refutation_refs, 'relation_refutation_without_basis')
                continue
            require(not result.refutation_refs, 'conflicting_relation_support')
            witnesses = {w.fact_id:w.key for w in result.witnesses}
            require(set(witnesses) == set(relation.fact_ids) and len(witnesses) == len(result.witnesses)
                    and all((fid,key) in supported for fid,key in witnesses.items()), 'relation_witness_mismatch')
            require(len({candidates[key].review_id for key in witnesses.values()}) == 1, 'cross_review_relation')
            require(result.reason_code == 'relation_verified', 'relation_reason_mismatch')
            require(relation.kind != 'before' or result.support_refs, 'temporal_evidence_missing')
        covered = tuple(fid for fid in ids if any(f == fid for f,_ in supported))
        unestablished = tuple(fid for fid in ids if fid not in covered)
        core = not unestablished if request.contract.required_facts.operator == 'all_of' else bool(covered)
        missing = () if core else unestablished
        relation_ok = all(r.status == 'supported' for r in results.values())
        codes = ('semantic_review_required',) if core and relation_ok else ('unmet_target_or_relation',)
        return ShadowOutcome('validated_shadow', codes, model_supported_fact_ids=covered,
            missing_required_fact_ids=missing, unestablished_fact_ids=unestablished,
            unmet_relation_ids=tuple(rid for rid,r in results.items() if r.status!='supported'),
            structural_goal_satisfied=core and relation_ok)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
        return ShadowOutcome('output_invalid', (str(exc) if isinstance(exc,PrototypeError) else 'invalid_output_schema_or_json',))


def run_mock_shadow(request, judge: MockJudgePort):
    """Mock timeout signal only: real deadline/cancellation belongs to P5-B2."""
    if not request.candidates:
        return ShadowOutcome('skipped_empty', ('no_candidates_not_population_absence',),
            missing_required_fact_ids=tuple(f.id for f in request.contract.required_facts.items),
            unestablished_fact_ids=tuple(f.id for f in request.contract.required_facts.items))
    try:
        raw = judge.judge(model_payload(request))
    except TimeoutError:
        return ShadowOutcome('model_failed', ('timeout',))
    except PrototypeError as exc:
        return ShadowOutcome('input_invalid', (str(exc),))
    except Exception:
        return ShadowOutcome('model_failed', ('adapter_error',))
    return audit_output(request, raw)
