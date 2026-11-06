"""OFFLINE proposal only. Not imported by Judge, Worker, B2, Agent or runners.

The request must come from trusted preparation (or an explicitly frozen offline
envelope). Finding text is not a source proof or semantic/evidence validation.
"""
from hashlib import sha256


def locate_fragment(request, *, key, source_identity_sha256, reference_type, text):
    result = dict(evidence_status='unvalidated_diagnostic_only', publication_allowed=False,
                  semantic_verified=False, locations=[])
    candidates = [c for c in request.candidates if c.key == key]
    if len(candidates) != 1 or candidates[0].source_identity_sha256 != source_identity_sha256:
        return result | dict(status='identity_mismatch')
    c = candidates[0]
    if (sha256(c.review_text.encode('utf-8')).hexdigest() != c.review_text_sha256
            or not (0 <= c.quote_start < c.quote_end <= len(c.review_text))
            or c.review_text[c.quote_start:c.quote_end] != c.quote):
        return result | dict(status='source_text_invalid')
    if reference_type not in ('support_refs','refutation_refs','context_veto_refs'):
        return result | dict(status='unsupported_reference_type')
    if not isinstance(text,str) or not text:
        return result | dict(status='missing_text')
    lower, upper = ((0,len(c.review_text)) if reference_type == 'context_veto_refs'
                    else (c.quote_start,c.quote_end))
    positions = []
    start = c.review_text.find(text, lower, upper)
    while start >= 0:
        positions.append((start,start+len(text)))
        if len(positions) == 2:  # Two matches already disprove uniqueness; include overlaps.
            break
        start = c.review_text.find(text,start+1,upper)
    return result | dict(status='missing' if not positions else 'unique' if len(positions)==1 else 'ambiguous',
        candidate_key=c.key, source_identity_sha256=c.source_identity_sha256, review_id=c.review_id,
        reference_type=reference_type, locations=positions, at_least_two_matches=len(positions)==2)
