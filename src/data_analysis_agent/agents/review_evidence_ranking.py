"""Order already permitted citations by question cues, preserving each review as a group."""

import re

from data_analysis_agent.agents.review_domain_rules import domain_rules

_RULES = domain_rules()
CITATION_RERANK_POLICY = _RULES.evidence_rerank.policy
TOPICS = _RULES.resolved_rerank_topics()
CUES = dict(_RULES.evidence_rerank.cues)


def evidence_features(question):
    topics = {name: re.compile(quote) for name, (query, quote) in TOPICS.items() if re.search(query, question)}
    cues = {name: re.compile(quote) for name, (query, quote) in CUES.items() if re.search(query, question)}
    return topics, cues


def rerank_citations(question, references):
    groups = {}
    for ref in references:
        groups.setdefault(ref.review_id, []).append(ref)
    merged = [refs[0].model_copy(update={'opinions': [quote for ref in refs for quote in ref.opinions]})
              for refs in groups.values()]
    topics, cues = evidence_features(question)

    def score(ref):
        quotes = [opinion.quote for opinion in ref.opinions]
        topic_count = sum(any(pattern.search(quote) for quote in quotes) for pattern in topics.values())
        cue_count = sum(any(pattern.search(quote) for quote in quotes) for pattern in cues.values()) if topic_count else 0
        return cue_count, topic_count

    return sorted(merged, key=score, reverse=True)
