"""将口语表达归一为业务意图；范围与统计仍由计划和只读工具校验。"""

import re
from datetime import date, timedelta


def resolve_question_scope(question, scope, products):
    """明确商品 ID/完整名称和绝对日期覆盖页面；历史引用本身不提供权限。"""
    if question.startswith('执行本轮已确认的只读查询'):
        return scope
    resolved = {**scope, 'product_ids': list(scope['product_ids'])}
    matches = [row['product_id'] for row in products if re.search(r'(?<![A-Za-z0-9_])' + re.escape(row['product_id']) + r'(?![A-Za-z0-9_])', question)
               or (row['title'] and row['title'] in question)]
    if matches:
        resolved['product_ids'] = matches
    dates = re.findall(r'\d{4}-\d{2}-\d{2}', question)
    if len(dates) == 2 and not re.search(r'对比|比较|相比|基线', question):
        start, end = (date.fromisoformat(value) for value in dates)
        if start > end:
            raise ValueError('本轮指定的开始日期必须早于结束日期。')
        resolved.update(date_from=start.isoformat(), date_to=end.isoformat())
        if resolved['baseline_to'] >= resolved['date_from']:
            previous_end = start - timedelta(days=1)
            resolved.update(baseline_to=previous_end.isoformat(), baseline_from=(previous_end - (end - start)).isoformat())
    return resolved


def normalize_question(question):
    if re.search(r'集中|关注|评价|效果|问题|好评|差评|维度', question):
        return re.sub(r"哪些地方|什么地方", "哪些方面", question)
    return question


def opinion_request(question):
    """识别维度内部关注点及中性使用体验，不把未指定情感当作歧义。"""
    text = normalize_question(question)
    topic = next((word for word in ("制冷", "静音", "安装") if word in text), None)
    aspect = "effectiveness" if re.search(r"使用效果|用起来|制冷", text) else (
        "quality" if "静音" in text else "service" if "安装" in text else None)
    if aspect is None:
        aspect = next((key for word, key in {'质量': 'quality', '效果': 'effectiveness', '包装': 'packaging',
                      '物流': 'logistics', '客服': 'service', '服务': 'service', '价格': 'price'}.items() if word in text), None)
    detail = bool(re.search(r"里面|细分|关注|提及最多|哪些问题|哪些方面|主要集中", text))
    experience = bool(re.search(r"用起来.*(?:怎么样|如何)|效果.*(?:怎么样|如何)", text))
    return {"text": text, "topic": topic, "aspect": aspect,
            "breakdown": bool(topic or experience or (aspect and detail))}
