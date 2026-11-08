"""从已校验的压缩上下文提取回答事实；数字和可解释命题都由代码绑定。"""

import re
from calendar import monthrange
from datetime import date

from pydantic import Field

from data_analysis_agent.agents.review_results import AnalysisModel
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.opinions import ASPECTS


class AnswerFact(AnalysisModel):
    finding_ref: str
    kind: str
    scope_ref: str
    source_result_ids: list[str]
    aspect_key: str | None = None
    sentiment: str | None = None
    metrics: list[dict] = Field(default_factory=list)
    statements: list[str]
    detail: str


def percent(value):
    return "暂无占比" if value is None else f"{value * 100:.1f}%"


def metric_map(rows):
    return {row["name"]: row for row in rows}


def ratio_text(metric):
    if metric["value"] is None:
        return "暂无占比（没有可用分母）"
    names = {"positive_review_count": "含正向观点评论", "successful_reviews": "成功标注评论", "rated_reviews": "有效星级评论"}
    return f"{percent(metric['value'])}（{metric['numerator']}/{metric['denominator']} 条{names.get(metric['denominator_name'], '评论')}）"


def selected_aspects(question):
    # 仅在已有六维内筛选回答，不改变阶段 2 查询计划或产生细分维度。
    question = question.replace("数据质量", "")
    return {key for key, label in ASPECTS.items() if label in question or key in question
            or (key == "service" and ("客服" in question or "服务" in question))
            or (key == 'quality' and '质量' in question)
            or (key == "effectiveness" and "效果" in question)}


def confirmed_aspects(context):
    return {row['filters']['aspect'] for row in context.statistics
            if row.get('filters', {}).get('aspect') in ASPECTS}


def build_answer_facts(context, *, detailed=False):
    scopes = {row["scope_ref"]: row for row in context.scope_summary}
    samples = {row["sample_ref"]: row for row in context.sample_summary}
    facts = []
    comparison = any(row["analysis_type"] == "comparison" for row in context.statistics)
    advice = bool(re.search(r"建议|改进|改善|优化", context.question))

    def add(stat, kind, detail, statements, *, aspect=None, metrics=(), finding_ref=None):
        identity = [stat["source_result_id"], stat["scope_ref"], stat.get("group_id"), kind, aspect]
        if advice and aspect:
            statements = [*statements, f"建议优先复核{ASPECTS[aspect]}相关原文，再结合业务记录排查。"]
        facts.append(AnswerFact(finding_ref=finding_ref or stat.get("finding_ref") or "finding:" + content_sha256(identity)[:20], kind=kind,
            scope_ref=stat["scope_ref"], source_result_ids=[stat["source_result_id"]], aspect_key=aspect,
            sentiment=stat.get("sentiment", stat.get("filters", {}).get("sentiment")), metrics=list(metrics), statements=statements, detail=detail))

    for stat in context.statistics:
        scope = scopes[stat["scope_ref"]]
        sample = samples.get(stat.get("sample_ref"), {})
        kind = stat["analysis_type"]
        if comparison and kind == "issues":
            continue  # 比较商品时，额外汇总问题不替代逐商品结论。
        prefix = f"商品 {stat['group_id']}：" if kind == "comparison" else ""
        if kind in ("positive_aspects", "issues", "comparison"):
            if stat["group_id"] == "baseline":
                continue
            positive = stat.get("sentiment") == "positive"
            sentiment_name = "正面" if positive else "负面"
            count_name = "positive_review_count" if positive else "negative_review_count"
            rows = stat['aspects']
            mentioned = [row for row in rows if metric_map(row["metrics"])[count_name]["value"] > 0]
            for row in mentioned[:(3 if detailed or not comparison else 1)]:
                metrics = metric_map(row["metrics"])
                label = row["aspect_label"]
                rate = metrics["positive_rate" if positive else "negative_rate"]
                detail = f"{prefix}{label}：{metrics[count_name]['value']} 条，{ratio_text(rate)}。"
                if detailed and positive:
                    detail += f"占成功标注评论 {ratio_text(metrics['positive_success_rate'])}。"
                statement = f"{label}是已有{sentiment_name}提及的评价维度。"
                alternatives = [statement, f"现有样本包含{label}相关的{sentiment_name}评价。"]
                add(stat, "aspect", detail, alternatives, aspect=row["aspect"], metrics=row["metrics"], finding_ref=row.get("finding_ref"))
            if not mentioned:
                if not sample.get("successful_reviews"):
                    detail = prefix + "当前范围暂无成功标注评论，无法判断评价维度分布。"
                else:
                    detail = prefix + f"成功标注评论中暂无匹配的有效{sentiment_name}观点，无法给出维度排名。"
                add(stat, "empty", detail, ["现有数据不足以给出维度排名。"], metrics=stat["metrics"])
        elif kind in ("trend", "aspect_trend"):
            for series in stat["trends"]:
                aspect = series["aspect"]
                finding = next(row for row in context.key_findings if row["type"] == "trend"
                    and row["source_result_id"] == stat["source_result_id"] and row["scope_ref"] == stat["scope_ref"]
                    and row["aspect"] == aspect)
                points = {point["bucket"]: point for point in series["points"]}
                valid = finding.get("valid_bucket_count", sum(point["metric"]["value"] is not None for point in points.values()))
                sufficient = finding.get("sufficient_bucket_count", 0)
                limited = valid < 3 or sufficient < valid * 0.8 or bool(finding["empty_buckets"])
                label = ASPECTS[aspect] if aspect else "整体负面评价"
                directions = {"up": "呈上升变化", "down": "呈下降变化", "flat": "保持平稳", "fluctuating": "有升有降、呈现波动",
                              "insufficient_data": "暂无法判断变化"}
                statement = (f"{label}可比较的标注样本不足，暂不能判断稳定趋势。" if limited else
                             f"{label}在已有标注样本中的负面占比{directions[finding['direction']]}。")
                detail = ""
                metrics = []
                if valid:
                    peak = points[finding["peak_bucket"]]
                    metrics.append({"bucket": finding["peak_bucket"], **peak["metric"], "sample_size": peak["sample_size"]})
                    detail = f"{label}已标注样本的占比峰值在 {finding['peak_bucket']}，为 {ratio_text(peak['metric'])}。"
                    recent = finding.get("recent_adjacent_change")
                    if recent:
                        detail += f"最近可比较的相邻时段 {recent['from']} 至 {recent['to']} 变化 {recent['change_pp']:+.1f} 个百分点。"
                        metrics.append({"name": "recent_change_pp", "value": recent["change_pp"], "unit": "percentage_points",
                                        "from": recent["from"], "to": recent["to"],
                                        "from_metric": points[recent["from"]]["metric"], "to_metric": points[recent["to"]]["metric"]})
                    metrics.append({"name": "first_last_change_pp", "value": finding["first_last_change_pp"],
                                    "unit": "percentage_points", "from": finding.get("first_valid_bucket"), "to": finding.get("last_valid_bucket")})
                    largest = finding.get("largest_adjacent_change")
                    if largest:
                        metrics.append({"name": "largest_adjacent_change_pp", "value": largest["change_pp"],
                                        "unit": "percentage_points", "from": largest["from"], "to": largest["to"]})
                        if detailed and largest != recent:
                            detail += f"最大相邻变化为 {largest['from']} 至 {largest['to']} 的 {largest['change_pp']:+.1f} 个百分点。"
                else:
                    detail = f"{label}各时间桶均无成功标注，不能把空数据当作零负面占比。"
                add(stat, "trend", detail, [statement], aspect=aspect, metrics=metrics,
                    finding_ref=finding.get("finding_ref"))
        elif kind == "quality":
            detail = (f"当前范围共 {sample['review_count']} 条评论，{sample['eligible_text_reviews']} 条有有效正文，"
                      f"成功标注 {sample['successful_reviews']} 条，覆盖率 {percent(sample['labeling_coverage'])}；"
                      f"失败 {sample['failed_reviews']} 条，待处理 {sample['unprocessed_reviews']} 条。")
            add(stat, "quality", detail, ["数据质量应结合有效正文、成功标注与覆盖率判断。"], metrics=stat["metrics"])
        elif kind == "window_comparison":
            metrics = metric_map(stat["metrics"])
            label = "当前窗口" if stat["group_id"] == "current" else "基线窗口"
            detail = f"{label} {scope['date_from']} 至 {scope['date_to']}：负面占比 {ratio_text(metrics['negative_rate'])}。"
            add(stat, "window", detail, ["两个窗口应分别核对样本量和覆盖率，不宜仅比较评论数量。"], metrics=stat["metrics"])
        elif kind == "ratings":
            stars = [row for row in stat["metrics"] if row["name"].startswith("star_")]
            detail = f"{stat['group_id']} 的有效星级分布：" + "；".join(
                f"{row['name'][-1]} 星 {row['numerator']} 条（{percent(row['value'])}）" for row in stars) + "。"
            add(stat, "rating", detail, ["星级分布独立于观点情感标注。"], metrics=stat["metrics"])
        elif kind == 'aspect_drilldown':
            filters = stat['filters']
            for topic in filters['topics']:
                basis = '该维度纳入样本评论' if filters['population_mode'] == 'sample' else '该维度该情感的匹配评论'
                detail = (f"{ASPECTS[topic['aspect']]} · {topic['name']}：{topic['definition']}。"
                          f"{topic['review_count']} 条去重评论、{topic['opinion_count']} 条观点，"
                          f"占{basis} {percent(topic['review_rate'])}（分母 {topic['review_denominator']} 条）。"
                          f"代表原文：“{topic['quote']}”（评论 #{topic['review_id']}）。")
                add(stat, 'topic', detail, ['以下主题由模型发现并独立复核观点归属，未经人工确认。'], aspect=topic['aspect'],
                    metrics=[{key: topic[key] for key in ('review_count', 'opinion_count', 'review_rate', 'review_denominator')}],
                    finding_ref='finding:' + content_sha256([stat['source_result_id'], topic['topic_id']])[:20])
            if not filters['topics']:
                add(stat, 'empty', '当前没有获得真实观点及归属复核共同支持的二级主题；不据此编造主题排名。', ['当前没有可发布的二级主题。'])
        elif kind == 'opinion_breakdown':
            groups = stat['filters']['groups']
            balanced = context.analysis_type == "pros_cons"
            prefix = ""
            if balanced:
                product = scope["product_ids"][0]
                title = scope.get("product_titles", {}).get(product)
                if len(scope['product_ids']) > 1:
                    label = f"{scope.get('product_group_label', '商品组')}（{len(scope['product_ids'])} 款商品）"
                else:
                    label = f"{title}（{product}）" if title else f"商品 {product}"
                direction = "优点（正面评价）" if stat["filters"]["sentiment"] == "positive" else "缺点（负面评价）"
                prefix = f"{label}的{direction}："
            for index, row in enumerate(groups[:(2 if balanced else 3)]):
                emotion = {'positive': '正面', 'negative': '负面', 'neutral': '中性'}[row['sentiment']]
                denominator = f"该商品含{emotion}观点的评论" if balanced else "本次匹配评论"
                detail = (prefix + f"观点原文“{row['quote']}”：{row['review_count']} 条评论、{row['opinion_count']} 条观点，"
                          f"占{denominator} {percent(row['review_rate'])}（分母 {stat['filters']['total']} 条），{emotion}；"
                          + (f"原文依据：评论 #{row['review_id']}。" if balanced else f"评论依据：评论 #{row['review_id']}。"))
                add(stat, 'opinion_group', detail, ['以下按已有观点原文及情感归组，不能替代标准细分标签。'],
                    metrics=[{key: row[key] for key in ('review_count', 'opinion_count', 'review_rate')}],
                    finding_ref='finding:' + content_sha256([stat['source_result_id'], index, row['quote'], row['sentiment']])[:20])
            if not groups:
                add(stat, 'empty', prefix + '当前范围没有匹配的有效观点文本；请检查标注覆盖率或补充标注。',
                    ['现有数据无法支持该主题的细分统计。'])
        elif kind == "evidence":
            for ref in context.evidence:
                if ref.source_result_id != stat["source_result_id"]:
                    continue
                detail = "；".join(f"{ASPECTS[q.aspect]}相关原文：“{q.quote}”{'（片段）' if q.truncated else ''}" for q in ref.opinions)
                add(stat, "evidence", detail, ["以下原文仅为匹配样本，不能单独确认根因。"],
                    finding_ref="finding:" + content_sha256([ref.source_result_id, ref.review_id])[:20])
            if not any(ref.source_result_id == stat["source_result_id"] for ref in context.evidence):
                add(stat, "empty", "当前筛选条件没有匹配原文；没有证据不等于没有问题。", ["当前条件未找到匹配原文。"])
    # 普通回答优先主要发现；显式维度/商品组分别保留，较多分组提示详情。
    limit = 15 if detailed or context.analysis_type in ('pros_cons', 'aspect_drilldown') else max(5, sum(fact.kind == "trend" for fact in facts))
    return facts[:limit], len(facts) > limit


def answer_caveats(context, facts):
    codes = {item["code"] for item in context.caveats}
    messages = []
    for stat in context.statistics:
        if stat['analysis_type'] == 'aspect_drilldown':
            filters, coverage = stat['filters'], stat['filters']['coverage']
            messages.append(f"模型候选主题未经人工确认，未穷尽全部主题；同一评论可进入多个主题，数量不能相加。"
                f"未归类 {coverage['unclassified_opinions']} 条（含模糊、矛盾及未处理观点）。")
            if filters.get('aspect') is None:
                resolved = {topic['aspect'] for topic in filters['topics']}
                messages.append('整体好评按维度归并，各主题占比以该维度实际纳入的评论为分母。'
                    + ('尚未形成可靠主题的维度：' + '、'.join(label for key, label in ASPECTS.items() if key not in resolved) + '；不据此推断没有好评。' if len(resolved) < len(ASPECTS) else ''))
            basis = '非随机样本评论' if filters['population_mode'] == 'sample' else '匹配评论'
            messages.append(f"本次二级主题分析纳入 {coverage['selected_reviews']} 条{basis}、{coverage['selected_opinions']} 条观点"
                + ('，样本量较少' if coverage['selected_reviews'] < 20 else '') + '，主题归并结果不能直接代表更广泛的消费者评价。')
            if filters['population_mode'] == 'sample':
                messages.append(f"这是受预算限制的非随机样本探索，总体有 {filters['total']} 条匹配评论、{filters['total_opinions']} 条观点；样本占比不能视为总体真实比例。")
            if filters['analysis_status'] == 'partial':
                messages.append('部分归属批次失败，仅保留已通过来源与计算校验的事实，尚未形成完整分析结果。')
    if any(row['analysis_type'] == 'opinion_breakdown' for row in context.statistics):
        messages.append('现有标注只有六个上级维度；以上按观点原文和情感精确归组，同义表述未合并，不是完整细分主题排名。同一评论可有多条观点并进入多个组，评论数与观点数分别统计。')
    if "no_successful_annotations" in codes:
        messages.append("部分范围暂无成功标注，无法判断其正负面占比。")
    if "no_positive_mentions" in codes and "no_successful_annotations" not in codes:
        messages.append("当前成功标注中没有有效正向观点，不能据此编造好评排名。")
    points = [point for stat in context.statistics for series in stat.get('trends', []) for point in series['points']]
    if 'no_successful_annotations' not in codes:
        if ('small_sample' in codes or any(point['sample_size']['successful_reviews'] < 20 for point in points)) and not any(stat['analysis_type'] == 'aspect_drilldown' for stat in context.statistics):
            messages.append('部分样本量较少，结论仅描述已标注样本。')
        if 'insufficient_coverage' in codes or any((point['sample_size']['labeling_coverage'] or 0) < 0.8 for point in points):
            messages.append('部分范围或时间段标注覆盖率不足，结论仅描述已标注样本。')
    if "missing_time_buckets" in codes:
        messages.append("存在无成功标注的时段，空数据不代表负面占比为零。")
    if sum(fact.kind == "aspect" for fact in facts) > 1:
        messages.append("同一条评论可涉及多个维度，各维度数量不能相加作为总评论数。")
    if any(fact.kind == "trend" for fact in facts):
        scopes = {row['scope_ref']:row for row in context.scope_summary}
        partial = False
        for stat in context.statistics:
            if not any(series['period'] == 'month' for series in stat.get('trends', [])): continue
            scope = scopes[stat['scope_ref']]
            if scope.get('date_from') and scope.get('date_to'):
                start, end = date.fromisoformat(scope['date_from']), date.fromisoformat(scope['date_to'])
                partial |= start.day != 1 or end.day != monthrange(end.year,end.month)[1]
        if partial:
            messages.append('首末月的查询区间未覆盖完整自然月，不宜直接按整月比较。')
        messages.append('尚未核验各时段内的数据采集完整性；变化不能直接解释为已证实的原因。')
    if re.search(r"原因|为什么|导致|建议|改进|改善|优化", context.question):
        messages.append("评论统计不能确认因果；建议属于待核查方向，需要结合原文及业务记录验证。")
    return messages


def summary_options(context, facts):
    from data_analysis_agent.agents.review_semantics import opinion_request
    content_detail = opinion_request(context.question)['content_detail']
    if context.analysis_type == 'aspect_drilldown':
        return [topic_summary(context, [row for stat in context.statistics for row in stat.get('filters', {}).get('topics', [])])]
    if context.analysis_type == "pros_cons":
        return ['以下按商品分别列出优点和缺点，每个方向展示最多两种常见原文表述，仅代表当前数据集的已标注样本。']
    if any(fact.kind == 'opinion_group' for fact in facts):
        stats = [stat for stat in context.statistics if stat['analysis_type'] == 'opinion_breakdown']
        groups = [group for stat in stats for group in stat['filters'].get('groups', [])[:3]]
        if len(stats) == 1 and (content_detail or (stats[0]['filters'].get('aspect') and stats[0]['filters'].get('sentiment') in ('positive', 'negative'))):
            stat = stats[0]
            aspect = stat['filters'].get('aspect')
            sentiment = stat['filters'].get('sentiment')
            scope = next((row for row in context.scope_summary if row['scope_ref'] == stat['scope_ref']), {})
            count = len(scope.get('product_ids', []))
            selected = '当前所选全部商品'
            if count == 1:
                selected = '当前所选商品'
            elif count > 1:
                selected = f'当前所选 {count} 款商品'
            focus = f'买家对{ASPECTS[aspect]}的{"好评" if sentiment == "positive" else "差评"}' if aspect and sentiment in ('positive', 'negative') else '评价'
            quotes = '、'.join('“' + row['quote'][:80] + ('…' if len(row['quote']) > 80 else '') + '”' for row in groups[:3])
            return [f'{selected}的已标注评论中，{focus}提到{quotes}。']
        return ['已检索当前范围的观点文本，以下列出出现较多的原文表述及情感。']
    if facts and all(fact.kind == "empty" for fact in facts):
        requested = confirmed_aspects(context)
        successful = any(row.get('successful_reviews', 0) for row in context.sample_summary)
        if requested and successful and context.sentiment in ('positive', 'negative'):
            labels = '、'.join(ASPECTS[aspect] for aspect in requested)
            direction = '好评' if context.sentiment == 'positive' else '差评'
            return [f'当前范围的成功标注评论中，暂未检出{labels}相关{direction}，因此无法总结具体评价内容。']
        return ["当前数据不足以给出可靠的分析结论。"]
    if not facts:
        return ["当前数据不足以给出可靠的分析结论。"]
    if content_detail and all(fact.kind in ('aspect', 'empty') for fact in facts):
        return ['当前只有维度数量与占比，尚未取得对应观点内容，不能判断具体问题或体验。']
    if len(facts) == 1 and facts[0].kind == 'aspect' and confirmed_aspects(context):
        fact = facts[0]
        metric = next(row for row in fact.metrics if row['name'] == ('positive_rate' if fact.sentiment == 'positive' else 'negative_rate'))
        denominator = '含正向观点评论' if fact.sentiment == 'positive' else '成功标注评论'
        direction = '正向' if fact.sentiment == 'positive' else '负面'
        return [f"本次 {metric['denominator']} 条{denominator}中，有 {metric['numerator']} 条包含{ASPECTS[fact.aspect_key]}相关{direction}观点，占 {percent(metric['value'])}。"]
    if all(fact.kind == "aspect" and fact.sentiment == "positive" for fact in facts):
        return [_aspect_ranking_summary(facts, positive=True)]
    if any(fact.kind == "trend" for fact in facts):
        return ["".join(fact.statements[0] for fact in facts if fact.kind == "trend"),
                "趋势需要结合各时段的样本量和覆盖率分别判断。"]
    if any(row["analysis_type"] == "comparison" for row in context.statistics):
        return ["各商品的主要负面问题如下，占比均以各自成功标注评论为分母。"]
    if all(fact.kind == "aspect" for fact in facts):
        return [_aspect_ranking_summary(facts, positive=False)]
    return ["以下是当前范围内已核验的分析结果。"]


def topic_summary(context, topics):
    """只组织已通过归属复核的主题；整体总结先覆盖每个有依据的维度。"""
    if not topics:
        return '当前数据或归属复核不足，尚无可发布的具体评价总结。'
    sampled = any(stat.get('filters', {}).get('population_mode') == 'sample' for stat in context.statistics)
    basis = '本次纳入的非随机样本中' if sampled else '本次匹配的已标注评论中'
    direction = '好评' if context.sentiment == 'positive' else '差评' if context.sentiment == 'negative' else '评价'
    groups = {}
    for topic in topics:
        names = groups.setdefault(topic['aspect'], [])
        if topic['name'] not in names:
            names.append(topic['name'])
    points = '；'.join(ASPECTS[aspect] + '方面主要体现在' + '、'.join(names[:3]) for aspect, names in groups.items())
    return f'{basis}，{direction}的具体内容如下：{points}。这些是依据真实观点归并的候选主题。'


def _aspect_ranking_summary(facts, *, positive):
    metric_name = 'positive_rate' if positive else 'negative_rate'
    direction = '好评' if positive else '差评'
    denominator_name = '含正向观点评论' if positive else '成功标注评论'
    rows = [(fact, metric_map(fact.metrics)[metric_name]) for fact in facts]
    first, metric = rows[0]
    leader = '提及最多' if len(rows) == 1 or metric['numerator'] > rows[1][1]['numerator'] else '提及较多'
    lead = (f'当前已标注评论中，{ASPECTS[first.aspect_key]}是{direction}{leader}的维度：'
            f'{metric["denominator"]} 条{denominator_name}中有 {metric["numerator"]} 条提到该维度，占 {percent(metric["value"])}。')
    if len(rows) > 1:
        others = '、'.join(f'{ASPECTS[fact.aspect_key]} {row["numerator"]} 条（{percent(row["value"])}）'
                          for fact, row in rows[1:3])
        lead += f'其他较多的维度是{others}。'
    return lead + '这些数字说明评价涉及哪些维度，具体内容仍需查看对应观点。'
