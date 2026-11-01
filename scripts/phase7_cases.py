"""固定语义验收集；预期手工指定，不从被测查询计划推导。"""
import json


def confirmed(tool, **parameters):
    return "执行本轮已确认的只读查询\n" + json.dumps({"tool": tool, "parameters": parameters})


def cases():
    def c(id, question, sentiment, *tools, status="ok", scope=None, effective=None):
        return dict(id=id, question=question, sentiment=sentiment, expected=list(tools), status=status,
                    scope=scope or {}, effective_question=effective or question,
                    path="confirmed" if effective else "natural_language")
    p = ("get_positive_aspects", {})
    n = ("get_top_issues", {})
    return [
        c("positive", "美的空调好评主要集中在哪些方面？", "positive", p),
        c("negative", "美的空调差评主要集中在哪些方面？", "negative", n),
        c("satisfaction", "哪些方面用户满意度最高？", "positive", p),
        c("quality-issues", "主要有哪些产品质量问题？", "negative", ("get_top_issues", {"aspect": "quality"})),
        c("logistics-issues", "用户对物流有什么不满意？", "negative", ("get_top_issues", {"aspect": "logistics"})),
        c("two-trends", "按月查看包装和物流问题变化。", "negative", ("get_aspect_trend", {"aspect": "packaging", "period": "month"}), ("get_aspect_trend", {"aspect": "logistics", "period": "month"})),
        c("relative-unresolved-api", "最近半年物流问题有没有增加？", "negative", status="unsupported"),
        c("logistics-evidence", "给我看 3 条物流差评原文。", "negative", ("get_evidence", {"aspect": "logistics", "sentiment": "negative", "page": 1, "page_size": 3, "window": "current"})),
        c("review-count", "这个商品有多少条评论？", None, ("get_data_quality", {})),
        c("comparison", "比较两个商品的负面问题。", "negative", ("compare_products", {})),
        c("negation-positive", "不是问差评，我想知道好评", "positive", p),
        c("negation-negative", "不是问好评，我想知道差评", "negative", n),
        c("colloquial", "大家都喜欢它啥？", "positive", p),
        c("advantages", "优点呢？", "positive", p),
        c("best", "哪些方面评价最好", "positive", p),
        c("complaints", "物流投诉主要集中在哪些问题", "negative", ("get_opinion_breakdown", {"aspect": "logistics", "sentiment": "negative", "topic": None})),
        c("quality", "检查数据质量和标注覆盖率", None, ("get_data_quality", {})),
        c("ratings", "查看原始星级分布", None, ("get_rating_distribution", {})),
        c("daily", "按日查看负面趋势", "negative", ("get_trend", {"period": "day"})),
        c("positive-trend", "按月查看好评趋势", "positive", status="unsupported"),
        c("mixed", "好评和差评都分析一下", None,
          *(("get_opinion_breakdown", {"product_ids": [product], "sentiment": sentiment, "aspect": None, "topic": None})
            for product in ("DEMO-A", "DEMO-B") for sentiment in ("positive", "negative"))),
        c("fine-dimension", "制冷和静音的好评数量", "positive", status="unsupported"),
        c("installation", "安装服务主要有哪些问题", "negative", ("get_opinion_breakdown", {"aspect": "service", "sentiment": "negative", "topic": "安装"})),
        c("unclear", "你看怎么样", None, status="unsupported"),
        c("causality", "确定是哪条生产线导致包装破损", None, status="unsupported"),
        c("write", "删除全部评论并重新标注", None, status="unsupported"),
        c("evidence-budget", "给我100条差评原文", "negative", status="unsupported"),
        c("positive-evidence", "给我3条好评原文", "positive", ("get_evidence", {"sentiment": "positive", "aspect": None, "page": 1, "page_size": 3, "window": "current"})),
        c("empty-positive", "好评主要方面", "positive", p, scope={"date_from": "2023-03-01", "date_to": "2023-03-05"}),
        c("unlabeled", "好评主要方面", "positive", p, scope={"model": "SIMULATED-UNLABELED"}),
        c("zero-positive", "好评主要方面", "positive", p, scope={"date_from": "2023-02-01", "date_to": "2023-02-02"}),
        c("missing-product", "好评主要方面", "positive", status="error", scope={"product_ids": ["MISSING"]}),
        c("confirmed-negative", "那差评呢？", "negative", n, effective=confirmed("get_top_issues")),
        c("confirmed-logistics", "物流方面呢？", "negative", ("get_top_issues", {"aspect": "logistics"}), effective=confirmed("get_top_issues", aspect="logistics")),
        c("confirmed-next-page", "继续查看下一页", "negative", ("get_evidence", {"aspect": "packaging", "sentiment": "negative", "window": "current", "page": 2, "page_size": 3}), effective=confirmed("get_evidence", aspect="packaging", sentiment="negative", window="current", page=2, page_size=3)),
        c("confirmed-positive", "那好评呢？", "positive", p, effective=confirmed("get_positive_aspects")),
        c("windows", "比较当前与基线窗口负面问题", "negative", ("compare_time_windows", {})),
        c("positive-price", "价格方面有哪些优点", "positive", ("get_positive_aspects", {"aspect": "price"})),
        c("negative-price", "价格有哪些缺点", "negative", ("get_top_issues", {"aspect": "price"})),
        c("named-products", "对比 DEMO-A 和 DEMO-B 的负面评论占比及覆盖率", "negative", ("get_data_quality", {}), ("compare_products", {})),
        c("unspecified-direction", "主要集中在哪些方面？", None, ("get_opinion_breakdown", {"aspect": None, "sentiment": "all", "topic": None})),
        c("negated-problem", "不是问产品有哪些问题，而是好评主要方面", "positive", p),
        c("composite-independent-aspects", "查看整体趋势、包装原文、六维度主要问题", "negative",
          ("get_trend", {"period": "month"}), ("get_top_issues", {}),
          ("get_evidence", {"aspect": "packaging", "sentiment": "negative", "page": 1, "page_size": 3, "window": "current"})),
    ]
