# P0-B · 独立任务目标验收修复报告

日期：2026-11-09。前置：[P0-A 验收](2026-11-09_P0-A-intent-router-acceptance.md)。

## 结论

**P0-B PASS（本阶段离线验收范围）。**

- 原始请求在 Router / Planner 调用前形成不可变 `RequestGoal`。业务目标不再由 `plan.analysis_type` 或 Router 的 `opinion_request()` 推导。
- 独立检查请求与计划能力、请求与实际产物；继续调用现有 Validator 验证数字、分母、范围、引用位置和主题来源。
- 合法却错误的统计计划不能完成主题/原文请求；缺少统计数据、错误维度、错误情感和篡改引用均被拒绝。
- 解决空观点遗留问题：修正“静音效果”的维度绑定，并按现有业务契约返回证据不足；没有将 error 简单改成 ok。
- 最终整组运行 **431/431**；随后新增一个边界用例，专项运行 **25/25**。按测试 ID 去重覆盖当前适用集 **432/432 个不同测试**：418 个既有测试（含 P0-A 10 项）与 P0-B 14 项；没有未覆盖 ID，0 failure / error / skip。
- 未修改 State 持久化、P6、RAG 冻结契约、生产数据库或人工标注。未调用收费模型或执行 Embedding。

## 1. 审计与遗留失败复现

先阅读 P0-A 报告及 review_semantics、review_intent、review_plan、review_acceptance、reviews、review_evidence_loop 和原有任务级测试，再修改业务代码。

修改前执行原测试：

```text
test_assistant_semantics.AssistantSemanticsTests.
  test_empty_topic_and_empty_baseline_report_data_limits
Ran 1 test
FAILED (failures=1)
```

原始输入：“它的静音效果怎么样？”；隔离合成数据，商品 `100013588053`，当前窗口 `2021-06-01—2021-06-30`。

| 项目 | 修改前 | 修改后 |
| --- | --- | --- |
| 请求目标 | 由计划辅助推导为 evidence，aspect=quality | 编译前固定 opinion_content，aspect=quality，topic=静音 |
| 工具 | get_opinion_breakdown | get_opinion_breakdown |
| 工具维度 / 主题 / 情感 | effectiveness / 静音 / all | quality / 静音 / all |
| 匹配总数、groups、items | 0、空、空 | 0、空、空 |
| 数字、范围与引用校验 | passed（0 条引用，没有虚构引用） | passed |
| 请求与计划能力 | 没有独立检查 | passed |
| 观点筛选检查 | failed：quality 与 effectiveness 冲突 | passed |
| 原文引用存在性 | false | false，按正确空结果处理 |
| 业务验收 | failed | insufficient_evidence；complete=false |
| 最终外层状态 | error / business_error | unsupported，明确证据不足，无成功 highlights |

具体原因不是“空列表天然导致 error”：`opinion_request()` 把静音绑定 quality；`_build_query_plan()` 的一级维度词表又因“效果”预先得到 effectiveness，覆盖了语义结果。业务验收的“观点筛选”因此成为硬失败。即使没有这个维度错误，内容任务无证据也应进入证据不足，而非完成。

### 旧测试断言修订依据

已有确认契约的代码证据：

1. `review_acceptance.validate_task_completion()` 原本已定义正确空主题/空引用为 `insufficient_evidence`、`complete=false`。
2. `reviews.run_review_agent()` 已将该业务状态映射为外层 `unsupported`，不发布成功内容。
3. `test_review_task_completion.ReviewTaskCompletionTests.test_no_evidence_and_unclassified_are_not_complete` 已要求无证据与无可归类主题都为 insufficient_evidence、未完成、无 highlights。

因此只修订 `test_empty_topic_and_empty_baseline_report_data_limits` 中冲突的“空观点必须 ok 且有 highlights”断言，替换为更具体的维度、数字校验、计划能力、insufficient_evidence、complete=false 和空 highlights 断言。该测试后半段“上一时间段对比”的 ok 与 baseline review_count=1 断言原样保留。没有删除用例或改成宽泛的状态集合。

本机原始快照见 `.cache/p0b/empty-before.json`、`empty-after.json`、`legacy-before.txt`。

## 2. 独立请求目标契约

`review_acceptance.RequestGoal` 是仅本轮使用的 frozen dataclass，不是持久化 State。字段：

| 字段 | 来源与含义 |
| --- | --- |
| original_question | 原始用户请求；已确认查询封装带 question 时读取内层真实问题 |
| levels | 用户要求的产物：statistics、opinion_content、themes、evidence；不是计划类型 |
| aspect / sentiment / topic | 原问题明确约束，或已授权历史中唯一且适用的省略条件 |
| statistic_families | 原问题要求的汇总、趋势、数据质量、星级或比较能力，用于发现遗漏 |
| source | user_request / confirmed_request；后者来自用户已确认的工具请求，不来自编译结果 |

例：“物流差评主要是在抱怨哪些事情？”固定为：

```json
{
  "original_question": "物流差评主要是在抱怨哪些事情？",
  "levels": ["themes"],
  "aspect": "logistics",
  "sentiment": "negative",
  "topic": null,
  "statistic_families": [],
  "source": "user_request"
}
```

实际顺序：

1. `build_request_goal(question, conversation_context)` 在调用 `resolve_review_request()` 之前解析并冻结目标。
2. Router / Planner 独立编译计划并完成原有范围绑定。
3. `build_task_goal()` 只把确认范围附加到已经固定的目标，形成版本 `review-request-goal-v1`。兼容保留 plan 参数，但不读取它来生成目标。
4. `task_plan_check` 记录初始计划能力与目标的一致性。原有一次本地证据补查仍可运行，不能改写目标。
5. 发布前 `validate_task_completion()` 检查最终计划、实际结果、范围和可信证据；`task_acceptance.plan_check` 是最终计划检查。

独立性证据：`test_goal_is_frozen_and_unchanged_by_different_compiled_plans` 验证目标不可赋值，且更换主题、统计、原文三个编译计划后任务目标完全相同；`test_request_snapshot_exists_before_injected_wrong_router_plan` 在替换 Router 的回调中断言冻结目标已经存在；`test_shared_router_semantic_failure_cannot_downgrade_theme_goal` 模拟共享语义标记全部丢失，新目标提取不调用该函数，仍要求 themes。

语义边界只用于声明产物与约束，不生成工具调用、SQL 或新计划；没有新增并行 Intent Router。情感词法和既有 RAG 专用入口判定继续复用，独立的是任务目标层，而不是声称所有词法工具也完全重写。

历史恢复仅使用经现有上下文加载器约束的历史查询条件，以及最近至多四条用户原始问题，按时间恢复明确的目标、维度、情感与主题。失败结果中的数字、引文或状态不会成为完成证明。本轮明确要求覆盖历史要求。已有失败轮次恢复、原文基线与页大小、细分主题继承和商品比较测试均通过。

## 3. 计划与实际结果分别验收

| 请求产物 | 计划所需能力 | 实际所需结果 |
| --- | --- | --- |
| themes | get_aspect_drilldown，保留目标筛选条件 | aspect_drilldown，完整分类、可支持主题、去重来源、可信引用 |
| opinion_content | get_opinion_breakdown 或 get_aspect_drilldown | 对应观点产物及可信引用；纯统计不满足 |
| evidence | get_evidence | evidence 原文结果；issues 不满足 |
| statistics | 对应统计工具及原始统计能力族 | 对应统计产物与必要统计字段；纯原文/观点不满足 |

最终还执行 `plan.satisfied(selected)`，并复用 `review_validation.validate_report()`、`expected_result_scopes()` 复核数字、分母、范围、主题来源与 quote_start/quote_end。该复核也适用于独立调用业务验收函数的故障注入，不能只靠非空 evidence_ref 证明引用可信。

已知内容补查保持现有隐私边界：自动补查仅精确观点分组，不能假装补完语义主题。强制注入统计计划时，初始计划检查为 false；即使之后补到了原始观点，仍不能通过 themes 任务验收。

## 4. 故障注入与边界测试

新增 `tests/test_review_acceptance_p0b.py` 共 14 个测试方法：

| 测试 | 计划检查 | 业务完成结果 |
| --- | --- | --- |
| shared_router_semantic_failure_cannot_downgrade_theme_goal | 错误统计计划 false | failed；themes 不降级 |
| request_snapshot_exists_before_injected_wrong_router_plan | Router 前已有目标，注入合法统计计划后 false | Agent 外层 error，complete=false，无成功答案 |
| evidence_goal_rejects_statistics_plan_and_actual_issues | 错误统计计划 false；正确原文计划 true | 两种实际只有 issues 的情况均 failed |
| statistics_goal_requires_actual_statistics_even_if_plan_is_satisfied | 内容计划 false；正确统计计划 true | 纯内容或删除 issues 字段均 failed |
| correct_themes_and_statistics_pass_both_gates | true | 正确主题与统计均 passed、complete=true |
| empty_themes_are_insufficient_not_failed_classification | true | 零匹配为 insufficient_evidence，非分类故障 |
| partial_classification_cannot_hide_behind_missing_citations | true | partial 分类加空证据仍 failed，不借证据不足掩盖未完成 |
| completed_classification_with_no_supported_topic_is_insufficient | true | total>0、分类完成但无主题：insufficient_evidence |
| tool_execution_failure_has_no_successful_business_acceptance | true | 工具超时注入后 error / execution_failed / 未完成 |
| existing_quote_location_validator_still_rejects_corruption | true | 篡改 quote_start 后 failed，原位置校验仍生效 |
| valid_zero_statistics_can_pass_without_evidence | true | 合法零统计 passed，统计无需凭空要求引文 |
| plan_sentiment_cannot_override_explicit_user_sentiment | false | 正向请求配负向计划 failed |
| statistics_aspect_cannot_be_replaced_with_another_dimension | false | 物流请求配客服统计 failed |
| goal_is_frozen_and_unchanged_by_different_compiled_plans | 单独验证不可变性及计划无权修改目标 | 同一原请求生成同一目标 |

方法全名均带 `test_` 前缀。所有测试使用合成 SQLite、固定分类响应或模拟工具，不使用付费模型。A–J 在原 `test_review_router_p0a.py` 中继续检查工具路由、情感、范围、任务完成状态与模型调用计数，该文件未修改。

## 5. 无证据与故障状态契约

| 场景 | task_acceptance.status / complete | evidence_status | 外层状态与发布行为 |
| --- | --- | --- | --- |
| 正确内容/原文/主题查询，total=0 | insufficient_evidence / false | no_matching_opinions | unsupported，说明证据不足，无成功 highlights |
| total>0，分类完成但无可支持主题 | insufficient_evidence / false | no_supported_evidence | unsupported，不编造主题结论 |
| partial 或 failed 分类结果 | failed / false | analysis_incomplete | 已有 partial 保留 partial；失败则 error；不发布完整结论 |
| 工具执行异常、超时或未形成可验证结果 | failed / false | execution_failed（异常出口） | error，保留原错误与诊断 |
| 有结果但计划错、统计缺失或引用被篡改 | failed / false | 附带证据诊断，不能覆盖硬失败 | error，禁止成功答案 |
| 正确统计查到 0 条 | passed / true | not_requested | ok，可报告已核验的零匹配统计，不能推断现实无问题 |
| 正确主题且证据可信、批次完整 | passed / true | available | ok；仍明确模型候选主题未人工确认 |

原来“分类批次完整”检查可能在 missing=true 时被一并豁免。本轮将 partial/failed 分类保持为硬失败；合法 insufficient_data 空批次则允许进入证据不足。`reviews.py` 也会对 partial 结果生成具体的任务验收检查，不再仅留下无检查明细的默认失败。

没有新增顶层状态枚举，复用现有 ok / unsupported / partial / error；业务完成性以 task_acceptance 明确表达。

## 6. 文件变更

| 文件 | 本轮改动 |
| --- | --- |
| src/data_analysis_agent/agents/review_acceptance.py | 不可变请求目标、独立目标提取、计划能力检查、实际产物与统计完整性、复用原 Validator、证据与分类状态区分 |
| src/data_analysis_agent/agents/reviews.py | Router 前冻结请求；绑定独立目标；记录初始计划检查；对正常/partial 结果执行业务验收；异常出口保留计划检查与失败状态 |
| src/data_analysis_agent/agents/review_plan.py | 最小修正“静音效果”词义冲突，使 quality/静音 不再被裸“效果”误替换为 effectiveness |
| tests/test_assistant_semantics.py | 修订一个与现有契约冲突的空观点断言，增加精确状态与维度检查，其余断言保留 |
| tests/test_review_acceptance_p0b.py | 新增 14 项离线故障注入及状态契约测试 |
| 本报告 | 独立记录审计证据、实现、测试与边界 |

`review_semantics.py`、`review_intent.py`、review_validation 原有规则、State 持久化、主题分类器及 RAG 契约本轮均未修改。保留了任务开始时已有的 P0-A、前端及上下文相关修改。

## 7. 测试执行与资产核验

| 检查 | 结果 |
| --- | --- |
| 修改前遗留失败 | 1 个用例复现失败，完整原始快照已记录 |
| 第一版新增故障注入 | 8 个测试方法：1 failure、7 errors；真实共享目标降级被暴露，其他错误主要为尚未实现的新检查字段/接口 |
| 第一轮扩大回归 | 426 项，6 失败、0 error；暴露独立历史恢复不足，随后修复，未放宽原断言 |
| 多轮/绑定/业务验收专项 | 77/77 通过 |
| 最终适用整组 | 431/431，0 failure、0 error、0 skip |
| 最后补充专项 | 25/25，包括 P0-B 最终 14 项、P0-A 10 项及遗留用例 |
| 按测试 ID 去重 | 覆盖当前 432 个不同测试，uncovered=[] |
| Python 编译、git diff --check | 通过 |

最终整组包含 P0-A 选择的全部 418 项适用离线测试及当时 13 项 P0-B 测试；之后只新增了“分类完成但无可支持主题”第 14 项测试，没有改动业务代码。该项随最后 25 项专项通过，故不把 432 误称为一次完整运行的数量。

全部最终整组运行显式禁止 socket 连接和 psycopg 连接，SDK 使用既有 mock，未连接生产数据库、调用付费模型或运行 Embedding。P0-A 已记录的旧 Windows 异步、真实 PostgreSQL/HTTP 环境限制没有在本轮被声称已解决；PASS 范围为这里列出的适用离线集。

任务开始时对 1614 个既有文件记录 SHA-256（含 P0-A 新报告和新测试）。完成后仅上述四个既有代码/测试文件发生变化，没有额外受跟踪资产变化；两个新增文件为本轮测试与本报告。P0-A 测试和报告、冻结 RAG 资产及用户原有修改均保持原样。

关键复跑命令（PowerShell）：

```powershell
$env:PYTHONPATH='src;tests'
.venv/Scripts/python.exe -m unittest test_review_acceptance_p0b test_review_router_p0a -v
.venv/Scripts/python.exe -m unittest test_assistant_semantics.AssistantSemanticsTests.test_empty_topic_and_empty_baseline_report_data_limits -v
.venv/Scripts/python.exe -m unittest test_review_intent test_review_product_binding test_review_task_completion -v
```

完整适用集名单与日志在本机 `.cache/p0b/final-summary.json`、`final-regression.txt`、`final-targeted.txt`、`coverage.json`；修改前记录与目标/结果快照也在同目录。该目录为临时诊断记录，本报告保留正式结论。

## 8. 未覆盖风险与停止边界

- 独立请求目标仍为受限的确定性规则，不声称任意新口语、否定纠正、复杂混合目标均被完整识别；目标层与 Router 的词义覆盖可能仍存在同时遗漏的场景，需要继续补充相互独立的冻结请求样例。
- RAG、双向优缺点沿用原有专用契约与验证路径，本阶段不替换其任务目标模型；冻结契约未变。
- 未新增“主题＋指定原文条数”等联合编排，也没有实现 P6 多维度下钻。本轮通过结论针对已定义产物和故障注入边界。
- 对范围的解析与授权仍复用现有解析层；本阶段防止目标被计划降低，不是重新实现一套商品/日期授权系统。
- 主题来源与精确引用可验证，并不等于模型主题语义已经人工确认。通过状态继续保留 model_candidates_unreviewed 提示。

完成后停止于 P0-B，不自动进入下一阶段。
