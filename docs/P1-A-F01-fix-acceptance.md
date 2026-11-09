# P1-A-F01 修复与独立复验

日期：2026-11-09。范围：仅 F01；依据为 `P1-A-independent-validation.md`、`P6-implementation-readiness.md`、`2026-11-09_P1-A-trusted-analysis-state-acceptance.md` 和当前工作区源码。

**结论：F01 / P1-A 离线独立复验 PASS。原 450 项回归、11 项独立验收及 13 项 F01 测试共 474 个不同测试全部通过，0 failure / error / skip。历史私有接口静态审计仍有 13 个失败子项，单独保留。P6 未实施。**

入场工作区已有大量未提交的 P0/P1-A 实现，其中 `_followup_intent()` 已包含将 `short_sentiment` 纳入待完成主题承接的一行修补，另有 9 项 F01 测试草稿；入场直接运行这 9 项及独立 11 项为 20/20 通过。本轮保留该修补，明确其判定变量与契约，扩展测试并完成旧判定复现及独立复验。不能将下面的“旧判定失败”描述为入场源码仍然失败。

## 1. 修改前复现与证据

数据使用独立验收自建的业务 SQLite 和任务 SQLite。沿用真实 enqueue → claim_task → `TaskWorker._execute()` → finish_task → get_task → load_analysis_context 路径，只在临时库使用合成评论；模型身份读取沿用独立夹具的受控替身。禁止网络、PostgreSQL 和真实主题模型入口。

在测试进程内将 `_followup_intent()` 的待完成主题判定恢复为报告中的旧条件 `short_aspect and pending_query_type == 'aspect_drilldown'`，其他工作区逻辑保持当前版本；没有回滚磁盘源码，也没有修改原始失败断言。因此这是旧判定的受控复现，并非对完整历史提交的检出。

旧判定下完整三轮 Worker 对话复现为：

1. `100013588053的差评主要集中在哪些方面？`：succeeded / ok。
2. `具体体现在哪里？`：succeeded / unsupported，等待指定维度。
3. `那好评呢？`：failed / error，business_error。

同时直接执行未改动的 `test_explicit_short_positive_switch_after_pending_goal_is_accepted`，得到 **1 failure、0 error**，失败仍是 `task['status'] == 'succeeded'` 的原始断言：实际 failed。

| 证据 | 来源 / 复跑 | 观察 |
| --- | --- | --- |
| E-01 | `.cache/p1a-f01/original-worker.json`；`probe.py original` | 原始三轮状态、完整计划与验收记录；第三轮错误执行 `get_positive_aspects` |
| E-02 | `.cache/p1a-f01/original-f01-assertion.txt`；同上 | 原始 F01 断言仍能识别旧判定失败 |
| E-03 | `.cache/p1a-f01/final-worker.json`；`probe.py final` | 正确计划为正向主题下钻，plan_check=true；空正向证据不冒充完成 |
| E-04 | `.cache/p1a-f01/targeted-summary.json`、`regression-summary.json` | 指定专项 66/66；原回归 450/450 |

E-01 SHA-256：`09dd2b3ad20afe126a7dca5372e15d481f1ea95e5eda798915bc5b958d323469`。E-03 SHA-256：`66c640a7f36128aaec276aa6d201f096d5fd0e7ab566446a8c3e6957c318a6ec`。这些是本地证据文件的摘要，不替代任务库来源认证。

完整 JSON 同时记录每轮 input_analysis_state、analysis_state、pending_goal、保存后重新恢复的 restored_after_round、RequestGoal、user_intent、analysis_intent、query_plan、required_tools、task_plan_check、task_acceptance、查询/模型次数及最终 Worker/Agent 状态。

## 2. 每轮状态与修改前后对比

首轮有效范围是商品 `100013588053`，当前窗口 `2023-02-01` 至 `2023-02-28`，基线 `2023-01-01` 至 `2023-01-31`。身份和数据集保持独立夹具定义。成功标注分母 D=6，六维负面去重计数及顺序为 service=5、effectiveness=4、logistics=4、quality=2、price=1、packaging=0。两次对照的排名、分母和完整范围逐项相同。

以下为原始 JSON 字段的摘要；`null` 表示该阶段没有生成字段，不能自行视作验收通过。

| 字段 | 首轮：负面排名 | 第二轮：具体体现 | 第三轮：旧判定 | 第三轮：修补后 |
| --- | --- | --- | --- | --- |
| analysis_state.ranking_basis | 结果当轮无历史输入；保存后可恢复六维排名 | 首轮可信排名 | 首轮可信排名 | 首轮可信排名 |
| analysis_state.pending_goal | null | null；保存后可恢复 awaiting_aspect | 第二轮冻结的负向主题目标 | 同一负向历史目标 |
| RequestGoal.levels / sentiment / aspect | statistics / negative / null | themes / negative / null | themes / positive / null | themes / positive / null |
| user_intent.query_type / sentiment | aspect_summary / negative | aspect_drilldown / negative | aspect_summary / positive | aspect_drilldown / positive |
| user_intent.needs_clarification | false | true | false | false |
| analysis_intent | issues | null | positive_aspects | aspect_drilldown |
| query_plan.analysis_type / sentiment | issues / negative | null | positive_aspects / positive | aspect_drilldown / positive |
| required_tools | get_top_issues | 空，无工具计划 | get_positive_aspects | get_aspect_drilldown |
| task_plan_check.passed | true | null | false | true |
| task_acceptance.status / complete | passed / true | null | failed / false | insufficient_evidence / false |
| query_executions | 2 | 0 | 2 | 2 |
| total_model_calls | 0 | 0 | 0 | 0 |
| Worker / Agent 最终状态 | succeeded / ok | succeeded / unsupported | failed / error | succeeded / unsupported |

第二轮在编译计划前抛出受控澄清，所以没有 query_plan、task_goal 或 task_acceptance；澄清不被记为主题任务完成。第三轮的历史 pending_goal 仍是第二轮保存的 negative 快照，当前请求的新目标则是 positive；二者不是同一个字段，也不会改写旧任务的来源或摘要。

第三轮关键任务 JSON 对比（仅列用于对比的字段，完整字典见 E-01/E-03）：

```json
{
  "before": {
    "request_goal": {"levels": ["themes"], "sentiment": "positive", "aspect": null},
    "user_intent": {"query_type": "aspect_summary", "sentiment": "positive"},
    "analysis_intent": "positive_aspects",
    "query_plan": {
      "analysis_type": "positive_aspects",
      "required_tools": ["get_positive_aspects"],
      "requirements": [{"tool": "get_positive_aspects", "parameters": {"product_ids": ["100013588053"]}}]
    },
    "task_plan_check": {"passed": false},
    "task_acceptance": {"status": "failed", "complete": false, "plan_check": {"passed": false}},
    "worker_status": "failed",
    "status": "error"
  },
  "after": {
    "request_goal": {"levels": ["themes"], "sentiment": "positive", "aspect": null},
    "user_intent": {"query_type": "aspect_drilldown", "sentiment": "positive"},
    "analysis_intent": "aspect_drilldown",
    "query_plan": {
      "analysis_type": "aspect_drilldown",
      "required_tools": ["get_aspect_drilldown"],
      "requirements": [{"tool": "get_aspect_drilldown", "parameters": {"product_ids": ["100013588053"], "aspect": null, "sentiment": "positive"}}]
    },
    "task_plan_check": {"passed": true},
    "task_acceptance": {"status": "insufficient_evidence", "complete": false, "plan_check": {"passed": true}},
    "worker_status": "succeeded",
    "status": "unsupported"
  }
}
```

本夹具中 A 没有正向观点，所以修补后正确结果为证据不足，未调用主题模型。另一个测试只向临时库补充正向客服证据，以固定的主题发现/归属响应替换模型入口，验证相同追问可达到 ok、task_acceptance.complete=true；这证明流程及校验可完成，不证明真实模型的语义质量。

## 3. 根因、调用链和最小修复

F01（功能正确性，证据 E-01/E-02/E-03，置信度高）：RequestGoal 与 Router 各自使用历史目标。前者的 REFERENCE 已识别短情感表达并继承 themes，后者旧判定只对 short_aspect 使用 pending_query_type，于是短情感追问落回上一条成功查询 issues，生成 positive_aspects 统计计划。情感本身已正确切换，分歧发生在产物层次。

调用路径：Worker 加载同会话上下文 → pending_context 提供冻结主题目标 → build_request_goal 独立冻结本轮 themes/positive → `_user_conditions()` 保留 pending_query_type → `_followup_intent()` 编译追问 → `_intent_plan()` / Planner 生成精确工具计划 → task_plan_check → 受计划授权的工具执行 → validate_task_completion 独立验收。

最小修复是在 `_followup_intent()` 将 short_aspect 和 short_sentiment 一并纳入待完成主题承接，保留显式情感优先级。`_intent_plan()` 已有单商品正向主题能力；负向缺维度仍须澄清，因此不需要新增主题能力或改 Planner。验收目标、工具白名单、Validator 和统计口径均未调整。

相对独立失败报告的旧判定，核心变化为：

```python
pending_theme_ref = (short_aspect or short_sentiment) and conditions.get('pending_query_type') == 'aspect_drilldown'
theme_ref = semantic['theme_summary'] or pending_theme_ref
```

相对本轮入场源码，仅将已有修补的表达式提取为上述局部变量并补充契约注释；没有再引入另一套目标解析器。

## 4. 请求优先级与歧义契约

| 情形 | 契约与结果 |
| --- | --- |
| 本轮完整明确的新意图 | 本轮产物层次优先。明确好评维度/总体统计采用 statistics + positive；明确数量采用 statistics；原文请求采用 evidence。不得强制继承 themes |
| short_aspect | 没有新的产物要求时，继续唯一待完成目标并补维度；“客服方面呢？”保留 themes + negative |
| short_sentiment | 同范围且待完成主题唯一时，继承 themes，仅替换情感；“那好评呢？”采用 themes + positive |
| 明确正向具体表现 | “那正面的具体表现呢？”本身就是 themes + positive，不降成总体统计 |
| 不能唯一选择历史查询 | 在 `_followup_intent()` 中抛 QuestionScopeError，needs_clarification=true；无工具执行，query_executions=0、total_model_calls=0、trace/results 为空，历史 pending 保持受控未完成状态 |
| 显式修改商品/日期 | 已有范围绑定与投影清除不兼容 pending/ranking；页面选择变化时 pending_context 禁止从旧文字重建被清除的目标；本轮明确请求按新范围处理 |
| 跨会话 | 加载器拒绝恢复父任务目标和排名；不得由 context_task_id 跨会话恢复 |
| 更新请求替换旧目标后再短追问 | 使用最新已验证统计请求；旧 themes 不得再次出现 |

这里的歧义澄清沿用已有“多个可适用历史查询”的判定。对于题设的唯一主题待完成目标，没有发现需要另加业务层次猜测的依据，因此继续主题分析。明确总体统计和完整维度统计不会被纳入这个短情感引用分支。

`pending_context()` 仍只消费服务端受控加载器的上下文；它不是新的认证入口。`build_request_goal()` 不读取本轮 Router/Planner 的结果，仍能拒绝故意错误的计划。

## 5. 修改文件与测试覆盖

本轮相对入场快照修改两个已有文件，新增本报告：

| 文件 / 函数 | 本轮变化 |
| --- | --- |
| `src/data_analysis_agent/agents/review_intent.py::_followup_intent` | 保留已存在的 short_sentiment 修补，提取 pending_theme_ref 并说明优先级 |
| `tests/test_p1a_f01.py::F01WorkerTests` | 9 项草稿扩展为 13 项；增加总体统计变体、连续追问、文本范围变化、短情感跨会话、无结构化状态兼容、非空正向证据完成路径 |
| `docs/P1-A-F01-fix-acceptance.md` | 本报告 |

另在忽略目录 `.cache/p1a-f01/` 保存复现工具、入场快照、原始/最终 JSON、测试日志和摘要。不修改三份输入报告。

审查但不改动：`_user_conditions()`、`_intent_plan()`、`review_acceptance.build_request_goal()`、`validate_goal_plan()`、`validate_task_completion()`、`jobs.analysis_state.pending_context()`、状态恢复/投影、任务存储和 Worker。既有未提交前端、Python 及 P6 方案均保持入场内容。

| 要求 | 测试及断言 |
| --- | --- |
| A 负面排名→待指定维度→好评 | 保留 themes/positive；唯一主题工具；无证据时 insufficient_evidence 而非 error 或 complete |
| B 待补负面主题→客服方面 | 单维度 negative 主题下钻；固定分类响应下业务验收通过 |
| C 待补主题→正面具体表现 | themes/positive，与计划一致 |
| D 明确好评维度统计 | 维度统计、总体好评、整体好评统计三个表达均覆盖旧 themes |
| E 有多少条 | 显式计数成为 statistics，使用原负向统计工具 |
| F 商品或日期变化 | 页面选择变化和问题文本内变化均清除旧 pending/ranking |
| G 跨会话 | 具体表现请求澄清且零查询；短情感请求不继承旧主题 |
| H 故意冲突 | 注入统计 Planner 后 RequestGoal 仍为 themes；task_plan_check=false，Validator 拒绝完成，外层任务 failed |
| 歧义 | 注入多查询历史；澄清前零查询、零模型、无 trace/results，pending 仍未完成 |
| 连续追问 / 兼容 / 非空正向 | 完整新请求覆盖后的下一轮、仅旧 users/validated 的上下文、固定正向证据完成路径 |

所有 F01 用例的公共授权断言检查 trace 工具白名单、每个结果的工具参数是否符合实际计划；并拦截实际 ReviewToolContext.execute 逐次执行 plan.check_call，汇总未经计划授权的调用，要求为空。历史前两轮也执行公共授权断言。澄清统一断言 query_executions=0、total_model_calls=0、trace/results 为空。

H 的统计工具由故意注入的错误计划授权，但不满足独立主题目标，仍被最终 Validator 拒绝；不把“工具符合计划”等同于“目标验收通过”。原始独立 F01 测试文件完全未改，未增加 expectedFailure、skip 或宽松断言。

## 6. 独立复验结果

| 检查 | 结果 |
| --- | --- |
| 旧判定执行原始 F01 断言 | 1 failure / 0 error，证明原始断言仍有效 |
| test_p1a_independent_validation | 11/11 PASS |
| test_trusted_analysis_state | 18/18 PASS |
| test_review_router_p0a | 10/10 PASS |
| test_review_acceptance_p0b | 14/14 PASS |
| test_p1a_f01 | 13/13 PASS |
| 上述指定专项合并 | 66/66，34.600 秒，0 failure / error / skip |
| 原 450 项适用回归清单 | 450/450，123.989 秒，0 failure / error / skip |
| 去重后的功能测试 | 474/474；450 已包含原状态、Router、Acceptance 专项，不重复计数 |
| 私有接口静态审计 | 1 个方法，13 个既有失败子项，0 error / skip；独立 FAIL |
| 修改文件语法编译、git diff --check | PASS |

450 项目标列表逐项取自既有 `.cache/p1a/final-summary.json`，保存在本轮 regression-summary.json；没有通过缩短清单减少测试。所有功能运行禁止 socket 与 psycopg 连接，未连接生产库或收费服务。主题测试使用固定响应，检索回归沿用本地替身。

首次自定义脚本运行遗漏仓库根目录的 Python 导入路径，导致部分模块无法导入 scripts；该次日志独立保存为 `initial-runner-import-error.txt` 和 `initial-regression-import-error*`。补齐根目录后完整重跑，最终通过数字仅来自最终运行，不把导入失败算作跳过或通过。

13 个静态失败模块与原报告相同：agents/reviews、review_retrieval、review_shadow_siliconflow、review_structured_evidence，以及 reviews/e1a_b3_runtime、e1a_b3_worker、e1a_prediction、e1_preparation、embedding_store、exploratory_e1、historical_snapshot、rag_corpus、semantic_retrieval。本轮没有改动这些模块，未清理或放宽静态规则。入场文件哈希对照也确认原始独立测试、可信状态、验收器及这些模块未变。

复跑（在仓库根目录 PowerShell 执行）：

```powershell
$env:PYTHONPATH='.;src;tests'
.venv/Scripts/python.exe -m unittest test_p1a_independent_validation test_trusted_analysis_state test_review_router_p0a test_review_acceptance_p0b test_p1a_f01 -v
.venv/Scripts/python.exe .cache/p1a-f01/run_checks.py regression
.venv/Scripts/python.exe .cache/p1a-f01/probe.py original
.venv/Scripts/python.exe .cache/p1a-f01/probe.py final
.venv/Scripts/python.exe -m unittest test_dependency_boundaries.DependencyBoundaryTests.test_implementation_uses_public_interfaces_and_one_connection_factory -v
.venv/Scripts/python.exe -m compileall -q src/data_analysis_agent/agents/review_intent.py tests/test_p1a_f01.py
git diff --check
```

其中 original 是预期失败对照；static 审计预期继续报告 13 个历史子项，均不得写成全仓库全绿。

## 7. P6 前置条件与停止点

**F01 阻塞项已解除，P6 准备报告第 1 项“修复 F01，独立用例及旧 450 项通过”已满足。不能据此宣称 P6 所有前置条件均已满足。**

进入下一阶段前仍需按 P6 准备报告冻结 P6 模式目标、选择规则和 partial 发布契约。本轮没有确认这一项已完成；当前样本新鲜度检查、同快照预读、完整目标集合验收、共享预算、逐维与父级验收及 P6 UI 仍属于后续分阶段工作。收费验证还需要另行明确供应商、额度、单价和质量样本授权。

未进行真实收费 LLM、真实 Embedding、生产数据库修改、生产部署、前端重构或冻结 RAG 资产修改。未验证真实 PostgreSQL 并发、旧 Windows 异步链路、生产负载或真实主题模型质量；本报告的 PASS 限于上述离线功能与可信状态契约。本阶段完成后停止。
