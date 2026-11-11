# RB03 本地探索性事实判断诊断

日期：2026-11-11，Asia/Taipei。**本地判断器识别出 EV08 的配送慢观点；EV15 的两个原始观点均返回 unknown，尽管其完整评论中有买家直接描述物流慢。** 这是固定历史候选上的探索性结果，不是正式 Gold 评测、完整原题验收或端到端 RAG 结果。

## 执行范围和输入

只读取 P2-B1 中 RB03 盲态输入、对应的原评论 CSV、历史评论/标注观察和历史观点观察，核验了文件摘要、商品与评论身份、原文、标注关联、原始观点键、位置和切片。使用 EV08、EV15 的完整原评论和原始历史观点；没有读取 Gold Reference、人工裁决事件或人工批准 Q 引用。没有伪造当前来源认证、生产快照或检索结果。

实际调用的本地纯函数为 `target()`、`fact()`、`quote_facts()` 和一次 `assess()`；`assess()` 内部按现有实现调用上下文规则。`assess` 所需字段是原始观点键、review_id、全文、原观点、起止位置；此函数本身不要求 P0 认证对象，因此可以用已复核的历史记录进行**纯函数诊断**。其返回的 `permitted_keys` 是函数计算结果，不是生产来源认证或发布许可。

没有调用 `validate_retrieval()`、生产 Validator、Agent、Worker、检索、PostgreSQL、LLM 或 Embedding。生产 Validator 和 P0 当前来源认证均为 **NOT_TESTED**；历史来源仍是 `history_unproven`。执行时禁用了 Python socket 接口，且诊断只加载本地标准库及事实判断文件。

## 解析及实际结果

组件问题“有没有配送慢的反馈？”经 [target](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_reported_facts.py:54) 解析为目标事实 `delivery_slow`，无跨事实关系；解析符合本次组件目标。

| 候选及原始观点 | `fact()` / `quote_facts()` | `assess()` 原始判断 | 说明 |
|---|---|---|---|
| EV08，原始观点 1 | `delivery_slow` | `direct_report` / `explicit_entailment` | 该观点被识别为配送慢；同评论的安装态度没有被计作配送慢事实。 |
| EV15，原始观点 1 | `None` / 空集合 | `unknown` / `semantic_not_verified` | 到货等待表述没有完整匹配现有事实词形。 |
| EV15，原始观点 2 | `None` / 空集合 | `unknown` / `semantic_not_verified` | 物流慢表述末尾带语气词，当前完整匹配未识别。 |

以两条评论的全部三个原始观点作为一次 `assess()` 的输入，其原始总体结果为：`status=sufficient`、`reason=explicit_report_only`、`required_fact_ids=[delivery_slow]`、`supported_target_fact_ids=[delivery_slow]`、`missing_required_fact_ids=[]`。这个总体状态由 EV08 的一条观点产生；它**不表示 EV15 也通过**，不表示全体评论都已检查。

保留了完整的判断器原始结果、观点级结果及历史身份的[私有诊断记录](E:/AllProject/data-analysis-agent/outputs/agent-stage2-real-business-readiness-20261111/rb03-exploratory-fact-debug-v1/raw-diagnostic.json)，其 SHA-256 为 `a3346add30dfe88268f1f57e3335a283b64caf50a9a2082d6496caff72f438c7`。该文件在 Git 忽略目录；本报告不复制私人评论正文或精确身份。

## 识别边界、混淆与风险

[fact 及事实词表](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_reported_facts.py:10) 使用完整短句匹配。从源码看，有限词形包括“物流/配送/到货”加“慢、很慢、太慢”，以及“送货很慢”；本次真实观点实测只证明 EV08 的对应词形能识别，不能宣称所有列举词形均做了实际输入测试。

EV15 的完整评论直接包含买家对物流慢的描述，但两个原始观点均未获得当前函数的直接支持：这是**已观察到的事实识别缺口**。当前 `semantic_not_verified` 由事实识别未命中引起，不应报告成生产 Validator 拒绝；生产证据身份和最终发布资格本轮都没有验证。

本批实际被识别为 `delivery_slow` 的是 EV08 的配送观点，未观察到将安装观点识别成配送慢。该观察仅限两条评论及其三个历史观点；本轮没有用大量安装、否定、纠正或反向例子验证全部混淆风险，也没有发现或证明证据不足却被许可的真实案例。`assess()` 不自行认证来源，所以直接把它的结果当作生产许可存在绕过来源检查的风险；现有生产链仍需 [validate_retrieval](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_retrieval.py:171) 等安全检查。

unknown 表示当前规则未确认，并不等于明确反驳、事实不存在或执行错误；本次输入的历史身份和切片检查通过，未发生输入无效或执行失败。对无法唯一解释的内容仍须保持 unknown 或受控拒绝。

## 根因与最小修复建议

首个偏离位于 [fact()](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_reported_facts.py:41) 所用的 `delivery_slow` 完整词形：现有表达无法识别本次带语气词的物流慢观点，也没有覆盖到货等待时间的表达。值得针对这一具体业务缺口提出最小修复，但不能把所有“等待”“安装慢”都映射为配送慢。

还需注意 [context_ok()](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_reported_facts.py:71) 会对全文中无法识别的同一配送领域句子保守否决。**仅扩大目标观点的正则未必足以使 EV15 的完整上下文通过**；这是根据代码的待验证推断，本次原始判断停在 `semantic_not_verified`，没有测试修复后的上下文分支。建议在任何生产修改前，以合成例子覆盖语气词、到货时间、配送与安装对象、否定、转述和纠正，再保持原来源与 Validator 校验。不得用这两条历史评论作为唯一规则或降低安全门。

## 结论

值得下一步单独评估并最小修复 `fact()` 的配送表达覆盖，同时检验 `context_ok()` 的完整上下文边界；本轮没有修改生产逻辑。source-revision.v6 所列 88 个生产文件摘要仍一致，Oracle v2 摘要保持冻结值。未运行正式盲态评测，未读取 Gold 评分或产生准确率；P2-B3 的正式隔离准入状态仍不因本次探索而改变。
