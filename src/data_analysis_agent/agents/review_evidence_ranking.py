"""Order already permitted citations by question cues, preserving each review as a group."""

import re

from data_analysis_agent.agents.review_cooling_speed_evidence import ROOM_DROP
from data_analysis_agent.agents.review_reported_facts import DOMAINS


CITATION_RERANK_POLICY = 'review_cues_v1'
TOPICS = {
    'cooling': (DOMAINS['cooling'], DOMAINS['cooling'] + '|凉快|凉爽|变凉|有点凉|' + ROOM_DROP),
    'heating': (DOMAINS['heating'], DOMAINS['heating'] + '|暖和'),
    'delivery': (DOMAINS['delivery'] + '|发货|收.{0,3}到货', DOMAINS['delivery'] + '|发货|收.{0,3}到货|送达|签收'),
    'return': ('退货|退回|寄回', '退货|退回|寄回|退件|取件|退运'),
    'installation': ('安装|上门', '安装|上门'),
    'noise': (DOMAINS['noise'], DOMAINS['noise'] + '|静音|很吵'),
    'packaging': ('包装|纸箱|外箱', '包装|纸箱|外箱|包裹'),
}
CUES = {
    'delay': ('慢|不快|迟迟|不到货', '慢|不快|迟迟|迟缓|不及时|拖|才|等了|没收到|未收到|没送到'),
    'not_received': ('收不到货|不到货', '收不到|没收到|未收到|没送到|未送到|还没到'),
    'quick': ('(?<!不)快', '很快|挺快|迅速|及时|当天|次日'),
    'working_well': ('(?:制冷|降温)(?:效果)?好', '很好|效果好|不错|很棒|满意'),
    'loud': ('(?:噪音|噪声|声音)(?:很|比较)?大', '声音大|噪音大|噪声大|(?:声音|噪音|噪声).{0,3}大|很吵'),
    'damaged': ('破了|破损|损坏', '破了|破损|损坏|破裂|裂开'),
    'temperature': ('零下|负[一二三四五六七八九十0-9]', '零下|负[一二三四五六七八九十0-9]'),
    'duration': ('慢|不快|迟迟|小时|分钟|连续',
                 r'(?:[0-9]+|[几一二三四五六七八九十两半]+)\s*(?:个\s*)?(?:小时|钟(?:头)?|天|分钟|周)'),
}


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
