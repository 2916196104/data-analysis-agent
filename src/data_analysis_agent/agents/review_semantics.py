"""将口语表达归一为业务意图；范围与统计仍由计划和只读工具校验。"""

import re
import unicodedata
from datetime import date, timedelta

from data_analysis_agent.agents.review_prompts import detect_sentiment_directions
from data_analysis_agent.core.errors import ReviewDataError


class QuestionScopeError(ReviewDataError):
    """商品目标不完整时要求补充，不能退回页面商品冒充完整比较。"""


def _title_matches(mention, title):
    def compact(value):
        return re.sub(r"[^\w\u4e00-\u9fff]", "", unicodedata.normalize("NFKC", value).casefold())
    mention, title = compact(mention), compact(title or "")
    if len(mention) < 2:
        return False
    if mention in title:
        return True
    # 简称只能省略品牌和品类之间的型号文字，不以模糊相似度猜商品。
    return any(title.startswith(mention[:i]) and mention[i:] in title[i:]
               for i in range(2, len(mention) - 1))


def product_mentions(question, products):
    """只提取用户明确写出的实体；不把页面默认商品当作用户指代。"""
    found = []
    for clause in re.split(r'以及|还有|与|和|及|对比|比较|[，,；;、]', question):
        explicit = [row['product_id'] for row in products if re.search(
            r'(?<![A-Za-z0-9_])' + re.escape(row['product_id']) + r'(?![A-Za-z0-9_])', clause)]
        explicit += [row['title'] for row in products if row['title'] and row['title'] in clause]
        if explicit:
            found.extend(explicit)
            continue
        brands = []
        for brand in sorted({row.get('brand') for row in products if row.get('brand')}, key=len, reverse=True):
            if brand not in {'其他', '未知'} and brand in clause:
                brands.append(brand + '空调' if brand + '空调' in clause else brand)
        head = re.split(r'好评|差评|优缺点|优点|缺点|评价|使用效果', clause, maxsplit=1)[0]
        match = re.search(r'([\w\u4e00-\u9fff]{2,30}空调)', head)
        if match:
            name = re.sub(r'^(?:请|帮我|看看|查看|分析|一下|查询|按日|按月|按天|按月份|统计|分别)+', '', match[1])
            if not re.match(r'^(?:这|那|该|所选|当前|两款)', name):
                found.append(name)
                brands = [value for value in brands if not name.startswith(value.removesuffix('空调'))]
        else:
            # “名称的好评”中的明确名称也必须匹配目录，不能只识别空调后退回页面商品。
            subject = re.match(r'^(?:请|帮我|看看|查看|分析|一下|查询|统计|分别)*(.{2,100}?)的(?:好评|差评|优缺点|优点|缺点|评价|使用效果)', clause.strip())
            if subject:
                name = subject[1].strip(' “ ”「」\"')
                generic = re.fullmatch(r'(?:这|那|该|所选|当前|上述|上轮|所有|全部)(?:两款|款|个)?(?:商品|产品|空调|机器)?|它们|两者|二者|两款商品|商品|产品|用户|消费者|买家|大家|(?:使用效果|质量|效果|包装|物流|客服|服务|价格)(?:方面)?', name)
                if not generic:
                    found.append(name)
                    brands = [value for value in brands if not name.startswith(value.removesuffix('空调'))]
        found.extend(brands)
    found.extend(value for value in re.findall(r'(?<!\d)\d{8,}(?!\d)', question) if value not in found)
    return list(dict.fromkeys(found))


def match_product_mentions(mentions, products, *, selected_ids=(), strict=False):
    """普通查询可沿用明确选中的型号；比较指代必须逐个消除型号歧义。"""
    selected, ambiguous = [], []
    for mention in mentions:
        exact = [row for row in products if mention in (row['product_id'], row['title'])]
        candidates = exact or [row for row in products if _title_matches(mention, row['title'])]
        if not candidates:
            raise QuestionScopeError(f'当前数据集未找到“{mention}”对应的商品，请提供该数据集中的商品名称或 ID。')
        within_selection = [row for row in candidates if row['product_id'] in selected_ids]
        if len(candidates) > 1 and not strict and within_selection:
            candidates = within_selection
        if len(candidates) > 1:
            ambiguous.append(f'“{mention}”可匹配 ' + '、'.join(row['product_id'] for row in candidates[:5]))
        else:
            selected.append(candidates[0]['product_id'])
    if ambiguous:
        raise QuestionScopeError('需要选择具体商品：' + '；'.join(ambiguous) + '。请分别指定商品 ID。')
    return list(dict.fromkeys(selected))


def _pros_cons_products(question, products):
    head = re.split(r"优缺点|优点|缺点|好评|差评|正面|负面", question, maxsplit=1)[0]
    mentions = []
    for part in re.split(r"以及|与|和|、|[，,]|比较|对比", head):
        part = re.sub(r"^(?:请|帮我|分别|分析|看看|查看|总结|说说|告诉我|一下)+", "", part.strip())
        if not any(_title_matches(part, row['title']) or part == row['product_id'] for row in products):
            part = re.sub(r"(?:的|各自)$", "", part).strip()
        if part and part not in {"它", "这款", "这款商品", "这款空调", "这个商品", "所选商品", "当前商品", "两款商品", "商品"}:
            mentions.append(part)
    groups = []
    for mention in mentions:
        exact = [row for row in products if mention == row['product_id'] or mention == row['title']]
        candidates = exact or [row for row in products if _title_matches(mention, row['title'])]
        if not candidates:
            raise QuestionScopeError(f'当前数据集未找到“{mention}”对应的商品，请提供该数据集中的商品 ID。')
        if len(candidates) > 1:
            brands = {row.get('brand') for row in candidates}
            brand = next(iter(brands)) if len(brands) == 1 else None
            if not brand or mention not in {brand, brand + '空调', brand + '商品', brand + '产品'}:
                ids = '、'.join(row['product_id'] for row in candidates[:5])
                raise QuestionScopeError(f'“{mention}”对应多款商品（{ids}），请指定要分析的商品 ID。')
        groups.append({'label': mention, 'product_ids': sorted(row['product_id'] for row in candidates)})
    return groups


def resolve_question_scope(question, scope, products, *, product_groups=None):
    """明确商品 ID/完整名称和绝对日期覆盖页面；历史引用本身不提供权限。"""
    if question.startswith('执行本轮已确认的只读查询'):
        return scope
    resolved = {**scope, 'product_ids': list(scope['product_ids'])}
    matches = [row['product_id'] for row in products if re.search(r'(?<![A-Za-z0-9_])' + re.escape(row['product_id']) + r'(?![A-Za-z0-9_])', question)
               or (row['title'] and row['title'] in question)]
    if detect_sentiment_directions(question) == {"positive", "negative"}:
        groups = _pros_cons_products(question, products)
        if groups:
            matches = list(dict.fromkeys(product for group in groups for product in group['product_ids']))
            if product_groups is not None:
                product_groups.extend(groups)
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
