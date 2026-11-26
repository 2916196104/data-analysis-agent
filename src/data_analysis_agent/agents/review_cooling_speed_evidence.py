"""Source-bound evidence for explicit buyer reports of slow cooling."""

import re

from data_analysis_agent.agents import review_reported_facts as base
from data_analysis_agent.agents.review_domain_rules import domain_rules

_CFG = domain_rules().cooling_speed
PREVIOUS_POLICY = _CFG.policies.previous
EXPLICIT_POLICY = _CFG.policies.explicit
PROGRESS_POLICY = _CFG.policies.progress
POLICY = _CFG.policies.current
KEYWORD_POLICIES = _CFG.policies.keyword_policies
POLICIES = _CFG.policies.all_policies
SLOW = _CFG.fact_id

_SLOW_CLAUSE = re.compile(_CFG.slow_clause_pattern)
_WAITING = re.compile(_CFG.waiting_pattern)
_AMBIGUOUS_UNIT = re.compile(_CFG.ambiguous_unit_pattern)
_CONTEXT_QUALIFIERS = _CFG.context_qualifiers_extra
_DOMAINS = dict(base.DOMAINS, cooling=base.DOMAINS['cooling'] + _CFG.domain_cooling_extra)
_CONFLICTS = (*base.OPPOSITES, *_CFG.extra_conflicts)

# Older stored answers must be rechecked with the exact parser that licensed them.
# _quote_facts and _context_ok below are also the frozen v1 ranker's features.
ROOM_DROP = _CFG.room_drop
_ROOM_SLOW = re.compile(ROOM_DROP + _CFG.room_slow_suffix)
_EXPLICIT_SLOW = re.compile(_CFG.explicit_slow)
_TIME = _CFG.time_pattern
_COOLING_RUN = _CFG.cooling_run_prefix + _TIME
_STILL_WARM = _CFG.still_warm
_LOW_SETTING = _CFG.low_setting
# The separator is inside the quote. A duration and an observation from different
# sentences, or an embedded fragment of a condition, cannot create the timed fact.
_TIMED = re.compile(rf'(?:^|[，,。！!；;\n])[ \t]*{_COOLING_RUN}[ \t]*[，,]?[ \t]*{_STILL_WARM}[ \t]*(?=$|[，,。！!；;\n])')
_ACTIVE_COOLING = rf'(?:{_LOW_SETTING}|{_CFG.active_cooling_extra})'
_SET_DROP = re.compile(rf'(?:^|[，,。！!；;\n])[ \t]*(?:{ROOM_DROP}了?[ \t]*[，,][ \t]*{_ACTIVE_COOLING}|'
                       rf'{_ACTIVE_COOLING}[ \t]*[，,][ \t]*{ROOM_DROP}了?)[ \t]*(?=$|[，,。！!；;\n])')
_COMPANION = re.compile(
    rf'(?:{_COOLING_RUN}|{_STILL_WARM}|{_ACTIVE_COOLING}|{_TIME}(?:都|还)?降不下来|'
    rf'{_LOW_SETTING}[ \t]*{_TIME}[ \t]*(?:室温|室内温度)(?:能|才|只能)?降到[1-3][0-9](?:\.[0-9]+)?(?:度|℃)|'
    + '|'.join(_CFG.companion_extra) + ')'
)
_OTHER_EXPERIENCE = re.compile(_CFG.other_experience)
_V2_DOMAINS = dict(_DOMAINS, cooling=_DOMAINS['cooling'] + _CFG.v2_domain_cooling_extra)
_CONDITION_CHANGE = re.compile(_CFG.condition_change)

# Keep both times and both outcomes inside the same source quote.
_MINUTE_BOUNDS = dict(_CFG.minute_bounds)
_PROGRESS = re.compile(_CFG.progress_pattern)
_TRAILING_REPORT = re.compile(_CFG.trailing_report)
_PROGRESS_HAZARDS = re.compile(_CFG.progress_hazards)
_PROGRESS_BEFORE_SPEAKERS = re.compile(_CFG.progress_before_speakers)
_JOINED_EFFECT = re.compile(_CFG.joined_effect)
_UNTESTED_HEATING = re.compile(_CFG.untested_heating)
_COOLING_SENSATION = re.compile(_CFG.cooling_sensation)


def _cooling_fact(clause):
    text = re.sub(rf'^(?:{base.CONNECTIVES})\s*', '', clause.strip())
    return SLOW if _SLOW_CLAUSE.fullmatch(text) else base.fact(clause)


def _quote_facts(quote):
    return {value for _, _, clause in base.clauses(quote) if (value := _cooling_fact(clause))}


def _context_fact(clause):
    # A timed observation may accompany an explicit speed judgment, without licensing a quote by itself.
    if _WAITING.fullmatch(clause.strip()):
        return 'cooling_wait_context'
    return _cooling_fact(clause)


def _context_ok(row, supported):
    if SLOW not in supported:
        return base.context_ok(row, supported)
    return not _AMBIGUOUS_UNIT.search(row['review_text']) and base.context_ok(
        row, supported, fact_parser=_context_fact, domains=_DOMAINS, opposites=_CONFLICTS,
        hazards=base.CONTEXT_HAZARDS + _CONTEXT_QUALIFIERS, forward_frames=base.FORWARD_FRAMES + _CONTEXT_QUALIFIERS)


def _quote_facts_v2(quote):
    values = _quote_facts(quote)
    for _, _, clause in base.clauses(quote):
        text = re.sub(rf'^(?:{base.CONNECTIVES})\s*', '', clause.strip())
        if _EXPLICIT_SLOW.fullmatch(text):
            values.add(SLOW)
    if _TIMED.search(quote) or _SET_DROP.search(quote):
        values.add(SLOW)
    return values


def _context_fact_v2(clause):
    text = re.sub(rf'^(?:{base.CONNECTIVES})\s*', '', clause.strip())
    if _EXPLICIT_SLOW.fullmatch(text) or _ROOM_SLOW.fullmatch(text) or _TIMED.fullmatch(text):
        return SLOW
    if _COMPANION.fullmatch(text) or _OTHER_EXPERIENCE.fullmatch(text):
        return 'cooling_context_only'
    return _context_fact(clause)


def _context_ok_v2(row, supported, *, fact_parser=_context_fact_v2, condition_change=_CONDITION_CHANGE.search):
    if SLOW not in supported:
        return base.context_ok(row, supported)
    text = row['review_text']
    if text[row['quote_start']:row['quote_end']] != row['quote'] or _AMBIGUOUS_UNIT.search(text):
        return False
    # A trailing question about another owner's experience cannot license the claim.
    for left, _, clause in base.clauses(text):
        if _OTHER_EXPERIENCE.fullmatch(clause.strip()) and (
                left < row['quote_end'] or not re.search(r'[。！!？?\n]', text[row['quote_end']-1:left])):
            return False
    if condition_change(text) or any(
            fact_parser(c) in ('cooling_fast', 'cooling_failure') for _, _, c in base.clauses(text)):
        return False
    return base.context_ok(row, supported, fact_parser=fact_parser, domains=_V2_DOMAINS, opposites=_CONFLICTS,
        hazards=base.CONTEXT_HAZARDS + _CONTEXT_QUALIFIERS, forward_frames=base.FORWARD_FRAMES + _CONTEXT_QUALIFIERS)


def _minute_bounds(duration):
    if duration in _MINUTE_BOUNDS:
        return _MINUTE_BOUNDS[duration]
    minutes = int(duration[:-2]) * (60 if duration.endswith('小时') else 1)
    return minutes, minutes


def reports_timed_progress(quote):
    match = _PROGRESS.fullmatch(quote.strip().rstrip('。！!'))
    return bool(match and _minute_bounds(match['early'])[1] < _minute_bounds(match['late'])[0])


def _progress_context_ok(row, supported):
    if not reports_timed_progress(row['quote']):
        return _context_ok_v2(row, supported)
    text, start, end = row['review_text'], row['quote_start'], row['quote_end']
    before, after = text[:start].rstrip(' \t'), text[end:].lstrip(' \t')
    if text[start:end] != row['quote'] or (before and before[-1] not in '。！!\n'):
        return False
    if (after and text[end-1] not in '。！!\n' and after[0] not in '。！!\n'
            and not _TRAILING_REPORT.match(after)):
        return False
    if (_PROGRESS_HAZARDS.search(text) or re.search(base.RETRACTIONS, text) or
            _AMBIGUOUS_UNIT.search(text) or _CONDITION_CHANGE.search(text) or
            _PROGRESS_BEFORE_SPEAKERS.search(before)):
        return False
    return not any(_context_fact_v2(clause) in ('cooling_fast', 'cooling_failure')
                   for _, _, clause in base.clauses(text))


def _quote_facts_v3(quote):
    return _quote_facts_v2(quote) | ({SLOW} if reports_timed_progress(quote) else set())


def _joined_slow(clause):
    match = _JOINED_EFFECT.fullmatch(clause.strip())
    return bool(match and _cooling_fact(match[1]) == SLOW and base.fact(match[2]) == 'cooling_poor')


def _quote_facts_v4(quote):
    return _quote_facts_v3(quote) | ({SLOW} if any(_joined_slow(c) for _, _, c in base.clauses(quote)) else set())


def _context_fact_v4(clause):
    text = re.sub(rf'^(?:{base.CONNECTIVES})\s*', '', clause.strip())
    if _joined_slow(text):
        return SLOW
    if _UNTESTED_HEATING.fullmatch(text) or _COOLING_SENSATION.fullmatch(text):
        return 'cooling_context_only'
    return _context_fact_v2(clause)


def _condition_change_v4(text):
    # An untested heating mode leaves a separate buyer report about cooling intact.
    heating = [(left, right) for left, right, clause in base.clauses(text) if _UNTESTED_HEATING.fullmatch(clause.strip())]
    return any(not any(left <= match.start() and match.end() <= right for left, right in heating)
               for match in _CONDITION_CHANGE.finditer(text))


def _context_ok_v4(row, supported):
    if reports_timed_progress(row['quote']):
        return _progress_context_ok(row, supported)
    return _context_ok_v2(row, supported, fact_parser=_context_fact_v4, condition_change=_condition_change_v4)


def keyword_quote_matches(quote, *, policy=POLICY):
    if policy not in KEYWORD_POLICIES:
        raise ValueError('unknown cooling keyword policy')
    parser = _quote_facts_v3 if policy == PROGRESS_POLICY else _quote_facts_v4
    return SLOW in parser(quote)


def is_single_slow(question):
    request = base.target(question)
    return bool(request and request['facts'] == [SLOW] and request['relation'] is None)


def assess(question, evidence, *, policy=POLICY):
    if policy == PREVIOUS_POLICY:
        return base.assess(question, evidence, policy=policy, quote_parser=_quote_facts, context_check=_context_ok)
    if policy == PROGRESS_POLICY and is_single_slow(question):
        return base.assess(question, evidence, policy=policy, quote_parser=_quote_facts_v3,
                           context_check=_progress_context_ok)
    if policy == POLICY and is_single_slow(question):
        return base.assess(question, evidence, policy=policy, quote_parser=_quote_facts_v4,
                           context_check=_context_ok_v4)
    if policy not in POLICIES:
        raise ValueError('unknown cooling evidence policy')
    return base.assess(question, evidence, policy=policy, quote_parser=_quote_facts_v2, context_check=_context_ok_v2)


def validate_answer_bindings(question, assessment, evidence):
    policy = assessment['policy']
    if policy not in POLICIES:
        return False
    if policy in KEYWORD_POLICIES and is_single_slow(question):
        parser = _quote_facts_v3 if policy == PROGRESS_POLICY else _quote_facts_v4
    else:
        parser = _quote_facts if policy == PREVIOUS_POLICY else _quote_facts_v2
    return base.validate_answer_bindings(question, assessment, evidence, quote_parser=parser)
