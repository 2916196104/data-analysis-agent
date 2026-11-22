"""Source-bound evidence for explicit buyer reports of slow cooling."""

import re

from data_analysis_agent.agents import review_reported_facts as base


PREVIOUS_POLICY = 'reported_cooling_speed_v1'
EXPLICIT_POLICY = 'reported_cooling_speed_v2'
POLICY = 'reported_cooling_speed_v3'
POLICIES = (PREVIOUS_POLICY, EXPLICIT_POLICY, POLICY)
SLOW = 'cooling_slow'
_SPEED = r'(?:很|太|挺|比较|较|特别|有点)?慢|不快|缓慢|迟缓'
_SLOW_CLAUSE = re.compile(
    r'(?:(?:冷暖效果|使用效果|使用体验)[：:]\s*)?'
    r'(?:(?:我觉得|我感觉|实际用下来|用起来)\s*)?'
    r'(?:(?:我家的?|我买的)?(?:这台|这款)?空调)?'
    rf'(?:制冷|降温)(?:速度|效果)?(?:也|又|(?:很差|太差|差|不好)(?:也|又))?(?:{_SPEED})'
    r'(?:了|啊|呀|呢)?'
)
_WAITING = re.compile(
    r'(?:开了?|运行了|用了|吹了)(?:[1-9][0-9]*|[一二三四五六七八九十两几半]+)\s*'
    r'(?:个\s*)?(?:分钟|小时|钟头)(?:了)?[ \t]*(?:房间)?(?:都)?(?:还|仍然?)?没(?:有)?(?:凉快|变凉)(?:了)?'
)
_AMBIGUOUS_UNIT = re.compile(
    r'其他(?:品牌(?:的)?)?空调|另外(?:一个卧室|一台|一款)|(?:上一台|旧空调|老空调)'
    r'|(?:隔壁|邻居家|旁边)(?:的)?(?:那台|这台)?空调'
    r'|(?:[2-9][0-9]*|1[0-9]+|[两二三四五六七八九十几多]+)台(?:不同的)?空调'
)
_CONTEXT_QUALIFIERS = (
    r'|(?:客服|售后|师傅|商家|朋友|邻居)(?:称|提到|认为|觉得|反馈)'
    r'|(?:没(?:有)?|未)(?:实际|亲自)?(?:使用过|用过|用这台|开过机|开机)(?!多久|多长)'
    r'|仅在|只在|仅限'
)
_DOMAINS = dict(base.DOMAINS, cooling=base.DOMAINS['cooling'] + '|凉快|变凉')
_CONFLICTS = (*base.OPPOSITES, ('cooling_failure', SLOW))


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


# Older stored answers must be rechecked with the exact parser that licensed them.
# _quote_facts and _context_ok above are also the frozen v1 ranker's features.
ROOM_DROP = r'(?:室温|(?:(?:室内|房间|屋内)(?:的)?)?温度)(?:下?降)(?:速度|[得的])?(?:也)?(?:很|太|挺|比较|较|特别|有点)?慢'
_ROOM_SLOW = re.compile(ROOM_DROP + r'(?:了|啊|呀)?')
_EXPLICIT_SLOW = re.compile(
    r'(?:这台空调|这款空调|空调)?(?:制冷|降温)(?:速度)?(?:很|太|有点)?慢'
    r'(?:(?:得|的)(?:一塌糊涂|要命|离谱)|(?:而且)?效果(?:也)?(?:一般|差|不好))(?:了)?'
)
_TIME = r'(?:[1-9][0-9]{0,2}|[一二两三四五六七八九十几半]+)\s*(?:个\s*)?(?:分钟|小时|钟头)'
_COOLING_RUN = r'(?:这台空调|空调)?(?:开启制冷|开制冷|制冷)(?:模式)?(?:运行)?(?:了)?\s*' + _TIME
_STILL_WARM = r'(?:房间|室内|屋里)?(?:还|仍然?)没(?:有)?(?:凉下来|变凉|凉快)(?:了)?'
_LOW_SETTING = r'(?:空调)?(?:开|调到|设为|设到)\s*(?:1[6-9]|2[0-6])\s*(?:度|℃)'
# The separator is inside the quote. A duration and an observation from different
# sentences, or an embedded fragment of a condition, cannot create the timed fact.
_TIMED = re.compile(rf'(?:^|[，,。！!；;\n])[ \t]*{_COOLING_RUN}[ \t]*[，,]?[ \t]*{_STILL_WARM}[ \t]*(?=$|[，,。！!；;\n])')
_ACTIVE_COOLING = rf'(?:{_LOW_SETTING}|(?:开着|打开|开启)(?:空调)?制冷(?:模式)?)'
_SET_DROP = re.compile(rf'(?:^|[，,。！!；;\n])[ \t]*(?:{ROOM_DROP}了?[ \t]*[，,][ \t]*{_ACTIVE_COOLING}|'
                       rf'{_ACTIVE_COOLING}[ \t]*[，,][ \t]*{ROOM_DROP}了?)[ \t]*(?=$|[，,。！!；;\n])')
_COMPANION = re.compile(rf'(?:{_COOLING_RUN}|{_STILL_WARM}|{_ACTIVE_COOLING}|{_TIME}(?:都|还)?降不下来|'
                        rf'{_LOW_SETTING}[ \t]*{_TIME}[ \t]*(?:室温|室内温度)(?:能|才|只能)?降到[1-3][0-9](?:\.[0-9]+)?(?:度|℃)|'
                        r'制冷效果(?:很)?一般|(?:没有|不带)(?:省电|节能)模式|安装服务(?:不是很|不太|不)满意)')
_OTHER_EXPERIENCE = re.compile(r'(?:也)?不知道别人(?:花了?)?(?:[0-9]+元?)?(?:买)?的空调'
                               r'(?:是不是|是否)(?:也)?(?:是)?(?:这个|这样(?:的)?)效果[？?]?')
_V2_DOMAINS = dict(_DOMAINS, cooling=_DOMAINS['cooling'] + r'|室温|温度(?:下)?降|凉下来')
_CONDITION_CHANGE = re.compile(r'室外温度|室外气温|室外降温|自然降温|关机|没开空调|未开空调|室外机温度|'
                               r'制热模式|开(?:了|着)?制热|暖气|切换模式|换了模式|没开制冷|未开制冷|'
                               r'不算慢|算不上慢|(?:速度|耗时)(?:很)?正常|这(?:也|很|是)?正常|正常现象|'
                               r'(?:同事|朋友|邻居|父母)家的空调|从未用过|尚未使用|没(?:有)?试过|'
                               r'(?:完全|根本|一直)(?:不凉|不冷|不降温|不制冷)')


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


def _context_ok_v2(row, supported):
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
    if _CONDITION_CHANGE.search(text) or any(
            _context_fact_v2(c) in ('cooling_fast', 'cooling_failure') for _, _, c in base.clauses(text)):
        return False
    return base.context_ok(row, supported, fact_parser=_context_fact_v2, domains=_V2_DOMAINS, opposites=_CONFLICTS,
        hazards=base.CONTEXT_HAZARDS + _CONTEXT_QUALIFIERS, forward_frames=base.FORWARD_FRAMES + _CONTEXT_QUALIFIERS)


# Keep both times and both outcomes inside the same source quote.
_MINUTE_BOUNDS = {'半个小时': (30, 30), '大半个小时': (30, 60), '一个小时': (60, 60),
                  '两小时': (120, 120), '两个小时': (120, 120), '三小时': (180, 180),
                  '三个小时': (180, 180)}
_DURATION = r'(?:[1-9][0-9]{0,2}(?:分钟|小时)|' + '|'.join(_MINUTE_BOUNDS) + ')'
_PROGRESS = re.compile(
    rf'{_LOW_SETTING}\s*[，,]\s*(?:开了|运行了)\s*(?P<early>{_DURATION})\s*[，,]\s*'
    rf'(?:房间|室内|屋里)(?:没(?:有)?明显制冷效果|(?:还|仍然?)没(?:有)?凉下来)\s*[，,]\s*'
    rf'(?P<late>{_DURATION})(?:后)?(?:降温|制冷)效果(?:一般|不明显|仍不明显|依然不明显)'
)
_TRAILING_REPORT = re.compile(r'[，,]\s*(?:连)?(?:空调)?(?:维修师傅|安装师傅|售后|客服)(?:都|也)?(?:说|称)')
_PROGRESS_HAZARDS = re.compile(
    r'不是|并非|并不|不算慢|听说|据说|如果|假如|要是|可能|也许|以为|只在|仅在|仅限|'
    r'这(?:也|很|是)?正常|换了空调|换成|后来|之前|原来|广告|宣传|[？?“”「」"]'
)


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
            re.search(r'客服|售后|师傅|朋友|邻居|他人|别人', before)):
        return False
    return not any(_context_fact_v2(clause) in ('cooling_fast', 'cooling_failure')
                   for _, _, clause in base.clauses(text))


def _quote_facts_v3(quote):
    return _quote_facts_v2(quote) | ({SLOW} if reports_timed_progress(quote) else set())


def is_single_slow(question):
    request = base.target(question)
    return bool(request and request['facts'] == [SLOW] and request['relation'] is None)


def assess(question, evidence, *, policy=POLICY):
    if policy == PREVIOUS_POLICY:
        return base.assess(question, evidence, policy=policy, quote_parser=_quote_facts, context_check=_context_ok)
    if policy == POLICY and is_single_slow(question):
        return base.assess(question, evidence, policy=policy, quote_parser=_quote_facts_v3,
                           context_check=_progress_context_ok)
    if policy not in (EXPLICIT_POLICY, POLICY):
        raise ValueError('unknown cooling evidence policy')
    return base.assess(question, evidence, policy=policy, quote_parser=_quote_facts_v2, context_check=_context_ok_v2)


def validate_answer_bindings(question, assessment, evidence):
    policy = assessment['policy']
    if policy not in POLICIES:
        return False
    if policy == POLICY and is_single_slow(question):
        parser = _quote_facts_v3
    else:
        parser = _quote_facts if policy == PREVIOUS_POLICY else _quote_facts_v2
    return base.validate_answer_bindings(question, assessment, evidence, quote_parser=parser)
