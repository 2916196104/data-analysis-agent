"""Exact buyer quotes about outstanding delivery and installation after receipt."""

import re

from data_analysis_agent.agents import review_reported_facts as base


POLICY = 'reported_delivery_timing_v1'
NOT_RECEIVED = 'delivery_not_received'
INSTALL_PENDING = 'installer_wait_after_receipt'

_NOW = r'(?:截至(?:评价|评论)时|到现在|现在|目前|至今)'
_GOODS = r'(?:货|商品|包裹|快递)'
_BUYER = r'(?:我|我们)'
_NOT_RECEIVED = (
    rf'(?:{_NOW}(?:{_BUYER})?|{_BUYER}(?:{_NOW})?)?(?:还|仍然?|一直|迟迟)?(?:没(?:有)?|未)(?:收到|拿到){_GOODS}'
    rf'|{_GOODS}(?:{_NOW})?(?:还|仍然?|一直|迟迟)?(?:没(?:有)?到(?:货)?|未送达|没送到|未收到)'
)
_SIGNED_WITHOUT_RECEIPT = r'没收(?:到)?货(?:却|就)?被签收了'
_INSTALL_SLOW = r'安装(?:很慢|太慢|慢|不及时)'
_ARRIVAL = (
    rf'(?:{_NOW})?(?:(?:我|我们)?(?:已经|已)?(?:收货|收到货|收到商品)(?:了)?'
    rf'|{_GOODS}(?:已经|已)?(?:到了|收到了)|到货(?:了)?)'
)
_ELAPSED = r'(?:过了|过|已过|已经)?(?:[1-9][0-9]*|[一二三四五六七八九十两几]+)(?:天|日|小时|周|星期)(?:了)?'
_WAIT_INSTALL = (
    rf'(?:{_NOW})?(?:'
    r'(?:都|还|仍|仍然|一直|也|却)?(?:无人|没人|没有人|没有师傅)(?:来|上门)?安装'
    r'|(?:还在|仍在|一直在)(?:等待|等)(?:安装|(?:安装)?师傅上门(?:安装)?)'
    r'|(?:还|仍然?|一直)(?:没(?:有)?|未)(?:等到(?:安装)?师傅上门|上门安装)'
    r'|(?:都|还|仍|一直)?不过来安装)'
)
_PENDING_AFTER_ARRIVAL = rf'{_ARRIVAL}(?:后)?(?:{_ELAPSED})?(?:[，,]|但|却)?(?:{_WAIT_INSTALL})'

_QUOTE_PATTERNS = {
    NOT_RECEIVED: re.compile(rf'(?:{_SIGNED_WITHOUT_RECEIPT}[，,]\s*)?(?:{_NOT_RECEIVED})(?:了|啊|呀|呢)?'),
    INSTALL_PENDING: re.compile(rf'(?:{_INSTALL_SLOW}[，,]\s*)?{_PENDING_AFTER_ARRIVAL}'),
}
_OTHER_ORDER = r'退货|退回|退件|返修|寄回|换货|补发|补寄|重发|退款|上一单|上次|另一(?:单|件)|其他订单|邻居'
_DOMAINS = {
    'delivery': r'物流|配送|到货|送货|快递|收货|收到|送达|签收|拿到',
    'installer': r'安装|师傅|上门|来装|装好|已装|收货|收到货|到货',
}
_HAZARDS = base.CONTEXT_HAZARDS.replace('|现在|', '|')
_FORWARD_FRAMES = base.FORWARD_FRAMES.replace('|现在|', '|')


def target(question):
    question = re.sub(r'^(?:买家|用户)(?=有没有|是否有|有无|是否存在)', '', question.strip())
    body = base.question_body(question)
    if body is None:
        report = re.fullmatch(r'(?:有没有|是否有|有无|是否存在)(?:买家|用户)?(?:抱怨|反映|反馈说|提到|说)'
                              r'(.+?)[？?。！!]*', question)
        if not report:
            return None
        body = report[1]
    clause = re.sub(r'^(?:(?:买家|用户)?(?:抱怨|反映|反馈说|提到|说))', '', body)
    if re.fullmatch(rf'(?:{_NOT_RECEIVED})|(?:一直|迟迟)(?:收不到|没收到){_GOODS}', clause):
        fact = NOT_RECEIVED
    elif (_QUOTE_PATTERNS[INSTALL_PENDING].fullmatch(clause)
          or re.fullmatch(r'(?:安装师傅|师傅|安装)?(?:上门(?:安装)?|来安装)(?:比较|很|太|较)?慢', clause)
          or re.fullmatch(rf'{_ARRIVAL}(?:后)?(?:[，,]|但|却)?(?:还|仍然?)(?:未|没(?:有)?)安装', clause)):
        fact = INSTALL_PENDING
    else:
        return None
    return {'facts': [fact], 'relation': None, 'text': body, 'clauses': [clause], 'operator': 'all_of'}


def quote_facts(quote):
    text = quote.strip().rstrip('。！!')
    return {key for key, pattern in _QUOTE_PATTERNS.items() if pattern.fullmatch(text)}


def _context_fact(clause):
    text = clause.strip().rstrip('。！!')
    text = re.sub(rf'^(?:{base.CONNECTIVES})\s*', '', text)
    facts = quote_facts(text)
    if facts:
        return next(iter(facts))
    if re.fullmatch(rf'{_ARRIVAL}(?:后)?', text):
        return 'delivery_received'
    # These clauses can accompany a supported quote, but cannot supply a timing fact.
    if re.fullmatch(rf'{_SIGNED_WITHOUT_RECEIPT}|{_INSTALL_SLOW}|(?:{_WAIT_INSTALL})', text):
        return 'timing_context'
    return base.fact(clause)


def _context_ok(row, facts):
    if re.search(_OTHER_ORDER, row['review_text']):
        return False
    return base.context_ok(row, facts, fact_parser=_context_fact, domains=_DOMAINS,
                           opposites=((NOT_RECEIVED, 'delivery_received'), (NOT_RECEIVED, 'delivery_fast')),
                           hazards=_HAZARDS, forward_frames=_FORWARD_FRAMES)


def assess(question, evidence):
    return base.assess(question, evidence, policy=POLICY, target_parser=target, quote_parser=quote_facts,
                       context_check=_context_ok)


def validate_answer_bindings(question, assessment, evidence):
    return base.validate_answer_bindings(question, assessment, evidence, target_parser=target, quote_parser=quote_facts)
