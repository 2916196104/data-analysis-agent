# P0-A · 商品评论 Agent 意图识别与工具路由修复验收

日期：2026-11-09。范围：Intent Router、共享语义识别、Planner 及离线测试。

## 1. 结论

**路由修复已实现，A–J 全部通过；整阶段按“适用现有测试全部通过”的严格门槛，暂不能标记为完全通过。**

- 新增 `tests/test_review_router_p0a.py`：10 个测试方法全部通过，包含 A–J 语义、计划、路由三组矩阵，以及 A–J 的真实 Agent 隔离执行。
- 最终适用离线回归：**418 个测试，417 通过，1 失败，0 error，0 skip**。唯一失败在修改前也能复现，详见第 6 节；未删除、放宽或跳过该断言。
- A、B 及定义的物流主题同义表达均调用 `get_aspect_drilldown`；F/G/I 保持统计，H 保持原文。J 使用主题工具已有的去重评论数，不再触发数据质量查询。
- 意图解析、规划与回答生成在新增 A–J 集成测试中均为 0 次模型调用；主题分类的 2 次调用均由固定响应模拟。没有声称真实主题分类也无需模型。
- 没有进入 P0-B / P6，没有修改 Validator、持久化、分类器、数据库、人工标注或冻结 RAG 契约。

## 2. 修改前复现

先新增三个 A–J 矩阵测试，再修改业务代码。原始运行结果：

```text
Ran 3 tests in 0.044s
FAILED (failures=13)
```

13 是 unittest 的失败子用例数：语义矩阵 B/C/D/E/J 共 5 项；Planner 矩阵 B/C/D/J 共 4 项；Router 矩阵 B/C/D/J 共 4 项。

之后用 Git index 中的修改前源码在独立 Python 进程内重新加载三个模块，复核同一矩阵及既有空观点失败；没有替换或回滚工作区源码。结果为 4 个测试方法、14 个失败子用例，其中 13 个为上述矩阵，另 1 个为既有失败。

修改前后计划如下。`query_type` 为 Router 的业务类型；每行均保持 negative 情感。维度除 I 为空、E/G 为 service 外，均为 logistics。

| 案例 | 原问题 | 修改前 analysis_type / 工具 | 修改后 query_type / analysis_type / 工具 |
| --- | --- | --- | --- |
| A | 物流差评主要体现在哪里？ | aspect_drilldown / get_aspect_drilldown | aspect_drilldown / aspect_drilldown / get_aspect_drilldown |
| B | 物流差评主要是在抱怨哪些事情？ | issues / get_top_issues | aspect_drilldown / aspect_drilldown / get_aspect_drilldown |
| C | 物流差评都在吐槽些什么？ | opinion_breakdown / get_opinion_breakdown | aspect_drilldown / aspect_drilldown / get_aspect_drilldown |
| D | 大家对物流有哪些不满意的地方？ | issues / get_top_issues | aspect_drilldown / aspect_drilldown / get_aspect_drilldown |
| E | 客服差评具体涉及哪些问题？ | aspect_drilldown / get_aspect_drilldown | aspect_drilldown / aspect_drilldown / get_aspect_drilldown |
| F | 物流差评有多少条？ | issues / get_top_issues | aspect_summary / issues / get_top_issues |
| G | 客服差评占比是多少？ | issues / get_top_issues | aspect_summary / issues / get_top_issues |
| H | 给我三条物流差评原文。 | evidence / get_evidence | evidence / evidence / get_evidence |
| I | 差评主要集中在哪些维度？ | issues / get_top_issues | aspect_summary / issues / get_top_issues |
| J | 物流差评主要有哪些问题，各自涉及多少评论？ | composite / get_data_quality + get_top_issues | aspect_drilldown / aspect_drilldown / get_aspect_drilldown |

E 的工具原本正确，但 `theme_summary=False, content_detail=False, breakdown=True`，依赖单维度负面下钻的兜底规则；本轮将其语义明确为主题请求。C 原本为 `content_detail=True, theme_summary=False`，因此实际进入原始文本分组。证据：`test_a_to_j_semantics`、`test_a_to_j_planner`、`test_a_to_j_router`。

## 3. 根因与调用链

1. `review_semantics.py:opinion_request()` 原有主题词主要覆盖“体现、表现、归纳、总结”，未覆盖 B/D；C 只命中 content_detail。“哪些问题”的宽泛 detail 规则还让 E/J 的行为依赖其他启发式。
2. `review_plan.py:_build_query_plan()` 将 content_detail 与主题归纳分开：C 的内容标记使其进入 `get_opinion_breakdown`；B/D 则落到负向统计。J 中“多少评论”还命中 quality 规则，生成数据质量与统计的合法复合计划。
3. `review_intent.py:_resolve_review_request()` 原本只要规则计划没有 unsupported_reason 就提前返回。工具合法不代表完成用户内容目标，因此不会再进行受限语义补全。
4. `reviews.py:run_review_agent()` 先调用 resolve_review_request，将 `plan.analysis_type` 写入 analysis_intent，再调用 `build_task_goal()`。原文问题或主题请求一旦前面被误判为统计，后续验收也可能沿用错误目标。
5. 只读检查 `review_acceptance.py:build_task_goal()`：目标依赖 opinion_request 的标记及 plan.analysis_type。它与 Router 共享语义信号，不能作为独立识别用户目标的证明。本轮不修改该文件。

## 4. 修改文件与最小修复

| 文件 / 函数 | 本轮修改与原因 |
| --- | --- |
| src/data_analysis_agent/agents/review_semantics.py · opinion_request | 在既有返回结构中补充具体抱怨的枚举表达，并将其标记为 theme_summary/content_detail/breakdown；排除明确一级维度和原始观点文本分组。“主要有哪些问题”仅在已给出维度时提升为二级主题，避免破坏整体商品问题统计。 |
| src/data_analysis_agent/agents/review_plan.py · _build_query_plan | 主题内评论计数不再误触发数据质量；缺情感时明确要求好评/差评；明确拒绝多个维度和主题与趋势/对比/质量的混合请求。沿用原有 get_aspect_drilldown、工具参数和查询契约。 |
| src/data_analysis_agent/agents/review_intent.py · _resolve_review_request | 明确内容请求与纯统计计划发生冲突时，按现有 ReviewIntent/_intent_plan 重新编译；不额外调用模型。多维度/多能力主题请求避开单维度追问槽位，交由 Planner 明确拒绝。 |
| tests/test_review_router_p0a.py | 独立新增语义、计划、Router 和 Agent 执行回归，不修改原有断言。 |
| 本报告 | 独立记录复现、修复、验证及未通过项。 |

`ReviewIntent`、`ReviewQueryPlan` 无新增平行类型；`_intent_plan()`、`_followup_intent()`、`build_query_plan()` 和 `run_review_agent()` 的原有能力继续复用。后四者并非都需要修改源码：新标记通过既有调用关系进入主题路径，集成测试验证真实执行。

复用的语义边界：

| 业务目标 | 识别与能力 |
| --- | --- |
| statistics | 数量/占比/一级维度统计，get_top_issues 或 get_positive_aspects |
| opinion_content | 原始观点内容与文本分组，保留 get_opinion_breakdown |
| themes | 具体表现、明确维度中的抱怨枚举，get_aspect_drilldown |
| evidence | 明确原文与引用，保留 get_evidence；RAG 的专用检索入口不变 |

## 5. 新增测试证据

| 测试方法 | 对应结论 |
| --- | --- |
| test_a_to_j_semantics | A–J 的 aspect、theme_summary、content_detail、examples、breakdown 正确 |
| test_a_to_j_planner | A–J 的 analysis_type、sentiment、aspect、required_tools 和完整 scope 正确；内容请求不降级统计 |
| test_a_to_j_router | 启用语义解析配置时仍为规则路由；query_type、情感、维度正确；OpenAI 与意图模型均禁止调用 |
| test_equivalent_theme_phrasings_have_identical_requirements | “具体表现是什么、主要抱怨哪些事情、具体有哪些问题、集中在哪些环节”和 A/B/C/D/J 具有相同业务能力与参数 |
| test_raw_opinion_grouping_and_metric_manifestation_keep_existing_tools | 原始观点分组、数量表现、占比表现、一级维度及“这个商品主要有哪些问题”保留原能力 |
| test_followup_themes_inherit_scope_aspect_and_sentiment | 从已有物流差评统计追问具体抱怨时，沿用商品、时间、数据集、标注身份、物流和负向情感；J 不被 count_ref 降级 |
| test_content_precheck_repairs_legal_but_wrong_statistics_plan | 模拟规则返回参数合法的纯统计计划；主题请求修复为主题工具，原始观点请求修复为分组工具 |
| test_out_of_scope_mixed_theme_requests_are_explicitly_unsupported | 无历史及有单维度历史时，多个维度、主题＋趋势、主题＋覆盖率均明确不支持，无工具要求 |
| test_missing_theme_sentiment_asks_for_direction | 无上下文且情感不确定时明确澄清，不编造正负情感 |
| test_agent_executes_a_to_j_without_intent_or_planning_model | A–J 经真实 run_review_agent 执行；使用临时合成 SQLite 和模拟主题响应；状态、工具、分析类型、范围、去重评论数及调用计数正确；临时业务库哈希不变 |

J 的主题评论数来自 `review_topics.py` 已有 `len(set(review_ids))` 及去重来源，新增集成测试断言每主题 review_count=2、有 review_ids。该结果仍遵守工具原有候选范围、覆盖率与分类状态；不把采样主题计数宣称为全库精确总量。

## 6. 回归结果与未通过项

最终适用集：30 个模块/测试类目标，418 个测试，417 通过、1 失败。覆盖维度内容、正向总结、路由、Planner、上下文恢复、商品绑定、工具统计、证据补全、任务完成、结构化答案、语义检索、运行查询契约、RAG MVP 及 P5 契约等价回归。全程禁止 socket 网络连接和 psycopg 连接；数据库均为隔离 SQLite 或 mock，未启动 Embedding。

唯一最终失败：

```text
test_assistant_semantics.AssistantSemanticsTests.
  test_empty_topic_and_empty_baseline_report_data_limits
AssertionError: 'error' != 'ok'
business_error: 本轮任务目标、范围或所需查询未通过验收，未发布分析结论。
```

该用例查询“它的静音效果怎么样？”，要求空观点结果 status=ok；修改前源码重跑同一断言也失败。它属于既有空证据/业务验收行为，本轮保留失败，不改 Validator、不放宽测试。故不能宣称“适用现有测试全部通过”。

其他运行记录：

- 核心路由/内容/正向主题集曾运行 77 个测试并全部通过；最后又补回整体问题统计断言，纳入上述最终 418 集。
- 最终单独执行新文件及原有整体负面模板用例：11/11 通过，其中新文件为 10/10。
- 探索性扩大到 530 个测试的第一次运行出现 11 failures / 39 errors。包含过严 socket 禁用导致 Windows 本地事件循环失败、开发中 scope 结构断言失配，以及旧 SQLite 标注队列用例要求 PostgreSQL 等问题；不能当作最终通过率。新 scope 断言已按实际查询快照契约修正后重跑通过。未删除或放宽任何原有断言。
- 放行本机事件循环后，旧 `test_review_agent` 在首个异步编排用例持续无结果，已中止，未声称通过。旧标注队列、范围标注及 HTTP/异步用例不计入最终适用离线集；影子供应商协议测试的探索性通过也不冒充真实供应商验证。
- Python 语法编译检查与 `git diff --check` 通过。

复跑核心命令（PowerShell）：

```powershell
$env:PYTHONPATH='src;tests'
.venv/Scripts/python.exe -m unittest test_review_router_p0a -v
.venv/Scripts/python.exe -m unittest test_review_dimension_content test_review_intent test_review_plan test_review_positive_summary test_review_topics -v
.venv/Scripts/python.exe -m unittest test_assistant_semantics.AssistantSemanticsTests.test_empty_topic_and_empty_baseline_report_data_limits -v
```

原始本机检查记录位于 `.cache/p0a/`：`baseline-tests.txt`、`before-routes.json`、`after-routes.json`、`final-new-tests.txt`、`applicable-modules.json`、`applicable-regression.txt`、`applicable-summary.json`。它们是临时检查记录；本报告保留正式结论及关键证据。

## 7. 模型、资产与范围核验

- 新 Router 测试开启 semantic_parser_enabled，并直接禁止 `_llm_intent` 和 OpenAI；A–J 及已定义同义表达无需模型解析。原来受限解析/澄清机制未移除，既有 ReviewIntentTests 继续通过。
- 新 Agent 集成测试分别断言 intent_resolution.model_calls=0、model_calls=0、answer_model_calls=0。F/G/H/I 的 topic_model_calls=0；A/B/C/D/E/J 为固定 mock 的 2 次主题调用。
- 仅三份运行时代码与一个新测试文件发生本轮代码修改，另新增本报告。已有暂存的前端、上下文修复、reviews.py、测试及 P6 文档保持原样。
- 运行回归前对排除上述三份运行时代码的 1609 个既有受跟踪文件做 SHA-256 快照，回归后逐一比对：0 个变化。快照开始于本轮业务修复后、回归前，不冒充任务开始时的全库快照；本轮实际 diff 与只读/临时库测试共同约束资产边界。
- `review_acceptance.py`、State、review_topics 分类器、RAG 检索实现和冻结查询契约源码均未改动。没有生产数据库、人工标注或 Embedding 操作。

## 8. 剩余歧义与下一阶段建议

- 未实现 P6：多个负向维度的主题请求要求拆开；没有维度的负向主题请求仍需补维度，没有情感时需明确好评/差评。
- 保留既有原始观点表达与二级主题边界：“说了什么/观点内容”可进入文本分组；新增的具体抱怨枚举进入主题。未证明所有任意口语、否定纠正和复杂混合句都已覆盖。
- “主题＋数量”J 已验证；“主题＋趋势/对比/质量”明确拒绝。主题与指定原文条数等其他复杂混合目标未新增联合编排，不属于完整支持范围，应拆分查询。
- 本轮扩大 opinion_request 覆盖自然会影响 build_task_goal 的共享语义信号，但没有解决 Router 与 Validator 共用错误目标的问题。P0-B 应独立提取用户目标，注入合法却错误的统计/文本计划验证其确实被拒绝，并单独确认空观点/证据不足的状态契约。

本阶段停止于 P0-A。由于保留的既有失败和未完成的旧异步环境检查，最终状态为：**路由目标已验证，严格整阶段验收未完全通过**。
