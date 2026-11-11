# Agent 第一阶段：Oracle v2 P0/P1 最小修复验收

生成时间：2026-11-11T12:51:52.946678+08:00（Asia/Taipei，UTC+08:00）。项目：`E:\AllProject\data-analysis-agent`。

**G24-T2、G30-T3 按正式人工 A 修复并通过；冻结 Oracle v2 全量结果为 61 PASS / 1 FAIL，原 59 轮 PASS 全部保持。** 唯一业务 FAIL 仍为 G25-T1，本轮未修改其预期或 RAG 命题许可。

**不能宣布兼容回归全通过：738 项中 734 通过、1 项断言失败、3 项测试错误，0 skip。** 不通过项是旧 F01/P6-B 对同一短情感句“直接承接主题”的要求，与本轮正式选 A 冲突；原断言、错误及轨迹完整保留。本轮不会为取得全绿恢复错误完成行为或改写旧断言。

## 1. 修复前复现与独立基线

先使用正式 `oracle.v2.json`、`freeze-manifest.v2.1.json`、原 v4 生产源码及原独立入口，重新运行 30 组、62 轮：**59 PASS / 3 FAIL**，首个偏离仍为 G24-T2 RequestGoal、G30-T3 RequestGoal；G25-T1 仍在命题许可阶段失败。

- [修复前原始执行](E:/AllProject/data-analysis-agent/.cache/agent-stage1-oracle-v2/run-03.json)
- [修复前原始评分](E:/AllProject/data-analysis-agent/.cache/agent-stage1-oracle-v2/assessment-03.json)
- [五轮 G24/G30 原文、历史、九字段及完整事件前后对照](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-v2-fix/final/G24-G30-nine-fields.json)

G24 历史：“100013588053有没有物流很慢的反馈？”→“客服呢？”。仅 service 维度被确认，不能确定统计、主题或具体检索命题。

G30 历史：“100013588053的差评主要集中在哪些方面？”→“具体体现在哪里？”→“那好评呢？”。前两轮同时留下已完成负面统计和未完成 negative/themes，第三轮只明确 positive，统计与主题两种承接层次竞争。

原预期、人工 A、评分器及数据替身不变。测试前保存了 843 个既有文件摘要；最终只有两处批准的业务源码发生变化。

## 2. 根因与最小代码修改

| 文件/函数 | 根因 | 最终修改 |
|---|---|---|
| [review_acceptance.py / build_request_goal](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_acceptance.py:41) | G24 无层次关键词便回退 statistics；G30 从待办/用户请求继承 themes，未考虑已完成统计仍是竞争目标 | RAG 后短维度句返回 null；在既有结构化待办或合法用户请求解析之后，若原层次 themes 与总体统计竞争且本轮只是短情感句，则返回 null |
| [review_intent.py / _followup_intent](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_intent.py:183) | pending_theme_ref 把短情感句直接导入主题；RAG 澄清文案只要求补充检索问题 | 在工具规划前抛出既有 QuestionScopeError，明确询问统计还是主题具体表现；RAG 短维度句询问统计、主题或具体检索目标 |

只修改上述两个生产文件，无新依赖、模型调用或自由规划框架。没有按 G24/G30 ID 添加分支。条件使用真实的短句语义、已有查询种类/维度和待办层次。

新规则不统一拦截所有“那好评呢？”：G02 没有待办主题，仍选择正向统计；已有唯一单维主题可继续情感切换。明确的“只统计客服差评数量”“只统计客服好评数量”优先于旧 RAG/待办；明确“那正面的具体表现呢？”仍为主题。

首轮候选已修复两个正式案例，但兼容测试暴露无 analysis_state 的旧调用方式：Goal 按用户文字继承 themes，而 Router 已要求澄清。最终将 Goal 歧义判断移到既有用户条件解析之后，使该方式也保持 Goal=null/Router 澄清。此操作只决定是否执行，不恢复任何排名事实。

首轮候选源码清单、16 项专项、62 轮执行和 737 项兼容结果仍保留；最终版本另存于 `final/`，没有覆盖首轮清单或执行账本。

最终 [source-revision.v5.json](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-v2-fix/final/source-revision.v5.json) SHA-256：`1a257a154f8db17e66b7d207ccd02ead75de68419064f176e7dd13c735c746d5`。它绑定原 v4、原 v1 源码集合、正式 v2 及 v2.1 评分合同，生产文件集合仍为 88 个，较 v4 只改变上述两个。

[独立复验入口](E:/AllProject/data-analysis-agent/scripts/run_agent_stage1_v2_fixes.py)仅适配源码修订绑定：继续校验全部原冻结文件、原 Harness、人工记录及原 v2.1 匹配代码摘要，再执行原 Harness 和原评分器。旧 v4 专用入口仍严格绑定 v4，未被修改来接受新源码。差异见 [v4→最终 v5](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-v2-fix/final/business-code.v4-v5.diff)。

## 3. 修复前后的九字段

以下 analysis_state 为输入上下文，pending_goal 为轮后合法投影；无可执行主计划时 not_finalized 仅是冻结评分合同中的审查投影，原始字段仍完整保留，不新增 API 状态。

### G24-T2

| 字段 | 修复前 | 最终修复后 |
|---|---|---|
| analysis_state | ranking/pending 均 absent | 相同，无旧统计或排名事实继承 |
| pending_goal | absent | absent |
| RequestGoal | statistics / service / null，错误冻结 | **null** |
| user_intent | service，needs_clarification=true | service，sentiment=null，needs_clarification=true；询问统计/主题/具体反馈 |
| analysis_intent | raw=null，审查 not_finalized | 相同 |
| query_plan | 无可执行主计划 | 无可执行主计划 |
| required_tools | [] | [] |
| task_acceptance | null | null，禁止 complete=true |
| final_status | Agent unsupported；Worker succeeded | 相同，表示成功回复澄清，不代表分析完成 |

有效商品始终为 053；当前窗口和标注身份不变。修复前后业务查询、Registry 调用、向量检索、分类均为 0；未将“物流慢”改写为“客服回复慢”，未发布客服事实。

### G30-T3

| 字段 | 修复前 | 最终修复后 |
|---|---|---|
| analysis_state | 有可信负面 ranking 与待办主题 | 相同，作为合法历史而非 positive 已完成事实 |
| pending_goal | 轮后 absent，原目标丢失 | **原 G30-T2 awaiting_aspect 完整保留**，completed=false |
| RequestGoal | themes / 不限维度 / positive | **null** |
| user_intent | aspect_drilldown / positive，needs_clarification=false | query_type=null，positive，needs_clarification=true |
| analysis_intent | aspect_drilldown | raw=positive_aspects（既有预解析标签），无最终计划，审查 not_finalized |
| query_plan | 可执行 positive drilldown | 无最终主计划，blocked 投影，positive 只是已知条件 |
| required_tools | get_aspect_drilldown | **[]** |
| task_acceptance | passed / complete=true | **null，不能 complete=true** |
| final_status | Agent ok；Worker succeeded | **Agent unsupported；Worker succeeded** |

最终询问：“本轮要查看好评统计，还是好评的主题具体表现？原未完成的主题目标仍保留，请明确分析层次。”有效范围保持商品 053、2023-02-01～2023-02-28 及原 source/dataset/标注身份。

原始顶层 analysis_intent 仍保留既有的 positive_aspects 预解析标签；它没有对应 Goal 或 query_plan，Router 的 query_type=null 且要求澄清，不能将该标签解释为已确认统计目标。冻结 v2.1 允许无最终计划时投影为 not_finalized；原始字段在轨迹中未删除或改写。

| 执行行为 | G30 修复前 | G30 最终修复后 |
|---|---:|---:|
| 业务查询 / Registry 调用 | 2 / 2 | **0 / 0** |
| 固定模拟主题分类尝试 | 2 | **0** |
| 观点预读 | 1 | **0** |
| 向量检索 | 0 | **0** |
| 补证 / 回答模型调用 | 未作为本次正确性依据 | **0 / 0**，evidence_completion=not_started |

未进入普通补证与事实发布流程；只生成确定性的澄清文案。必要的商品目录、事务设置读取与业务工具区分，未将这些工具前上下文读取伪报成业务查询。

## 4. 独立 Goal/Router 与待办生命周期

新增专项分别在禁用 Router/语义工具的情况下验证 Goal，在禁止调用 Goal 构建器与意图模型的情况下验证 Router；并通过真实 Worker 检查最终交互。没有仅用“两者一致”替代人工 A 的业务标准。

未修改 `analysis_context.py`、`analysis_state.py` 或其信任规则：

1. 当前澄清任务 Goal=null，不能自造一个 positive 或已完成 pending。
2. 既有 loader 沿同会话任务链继续读取原 G30-T2，经原任务完整性摘要校验恢复 negative/themes；随后核验原统计来源。
3. 输入、轮后 pending 对象逐对象相同：含原始问题、levels、negative、scope、scope_sha256、source.task_id、task_sha256、finished_at、awaiting_aspect/false。
4. 与原任务 RequestGoal、范围、保存的 analysis_integrity 摘要逐项匹配；来源不是新澄清任务。
5. 跨会话、标注身份变更、商品变更、日期变更不能恢复；篡改原任务或以错误情感重封印，仍被拒绝。

证据：[pending-lifecycle.json](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-v2-fix/final/pending-lifecycle.json)。新旧 `build_task_goal`、`validate_goal_plan`、`validate_task_completion` 的 AST 完全相同，见 [安全函数保全](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-v2-fix/final/unchanged-safety-functions.json)。Planner、Tools、P6、供应商费用与冻结 RAG 许可代码均未修改。

## 5. 专项、完整业务评测与历史通过保全

[新增专项](E:/AllProject/data-analysis-agent/tests/test_agent_stage1_v2_fixes.py)：最终 **17/17 PASS**，覆盖：独立 Goal/Router；短句同义形式；G02 正向总体统计；明确统计及主题请求优先；已完成单维主题的唯一承接；普通原文追问；原待办完整来源；跨身份/会话/范围及篡改；无结构化状态的旧用户条件方式。

完整评测使用正式 Oracle v2 与原 v2.1 评分器，不改变分子、分母、工具计数或故障注入：

| 项目 | v4 修复前复现 | 最终 v5 |
|---|---|---|
| 30组、62轮 | 59 PASS / 3 FAIL | **61 PASS / 1 FAIL** |
| 对话整体契约 | 27/30 | **29/30** |
| 原59轮PASS | 59 | **59/59 保持，无退化** |
| G24-T2、G30-T3 | FAIL / FAIL | **PASS / PASS** |
| G25-T1 | FAIL | **仍 FAIL，原预期不变** |
| G01～G04、G02正常短情感 | PASS | PASS |
| G10～G12澄清边界 | PASS | PASS |
| G17～G19部分完成、G20过期阻断 | PASS | PASS |
| G28/G29故障防护 | PASS | PASS |

| 模块 | 最终符合率 |
|---|---|
| State / Router / Goal / Planner（各项） | **60/60，100%** |
| 工具阶段及目标匹配 | **59/60，98.33%** |
| 注入错误拦截 | **2/2，100%** |
| 工具前阻断 | **10/10，100%** |
| 严格任务完整完成 | **42/43，97.67%** |
| 逐轮端到端 | **61/62，98.39%** |

自然模块排除两轮主动故障注入；总量包含全部 62 轮。澄清、partial、stale 的正确处理算契约符合，不算完整分析完成；没有通过改变分母提升成绩。

完整 [62轮前后执行与评分](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-v2-fix/final/turns.v5.jsonl)、[逐轮比较](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-v2-fix/final/turn-comparison.json)、[正式新评分](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-v2-fix/final/assessment.v5.json)、[最终原始运行](E:/AllProject/data-analysis-agent/.cache/agent-stage1-v2-fix/final/run-01.json)。

G25-T1 仍检索到合成候选但 assessment=query_semantics_uncovered、permitted_keys=[]；没有伪造命题许可或支持引用，没有 complete=true。本轮不修复此项，也不把它归因于 053 的向量覆盖缺口。

## 6. 兼容回归：保留四项真实不通过

原有 **650 个兼容测试 ID 全部保留**，加入未改动的两版评分单测（71）和新增专项（17）。最终 **738 项、734 通过、1 失败、3 错误、0 跳过**。完整结果：[regression-summary.json](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-v2-fix/final/regression-summary.json)；[原始日志](E:/AllProject/data-analysis-agent/.cache/agent-stage1-v2-fix/final/regression/tests.txt)。

四个原测试未修改：

| 测试 | 原要求 | 与本轮正式 A 的冲突 |
|---|---|---|
| test_p1a_f01.test_a_short_sentiment_keeps_themes_with_correct_empty_positive_result | 同一三轮场景 Goal=positive/themes 并执行主题工具 | A 要求 Goal=null、先澄清；旧测试解引用 null Goal 报测试错误 |
| test_p1a_f01.test_legacy_user_conditions_without_structured_state_keep_theme_goal | 去掉 analysis_state 后仍执行 positive/themes | 合法用户历史也存在统计/主题竞争；最终 Goal=null、不执行。旧断言不再成立 |
| test_p1a_f01.test_positive_theme_with_matching_evidence_completes_without_paid_model | 加入正向合成证据后，同一短句必须 ok/complete | 数据存在不等于用户选择了主题层次；本轮仍 unsupported 澄清，原断言失败 |
| test_p6b_planner_snapshot.test_explicit_other_requests_and_f01_short_sentiment_do_not_trigger_p6 | 后半部分直接 resolve 同一待办短情感句并取得主题计划 | 现在抛出既有 QuestionScopeError；旧调用未捕获，记测试错误。前半正常总体统计仍正确 |

上述 errors 是原测试期待旧计划/旧 Goal 而遇到合法 null/澄清，不是 Worker 未处理异常。原始堆栈全部保留，无 skip、expectedFailure、断言删除或测试替身特殊分支。**兼容回归结论 FAIL**；若后续要维护这些断言，需明确对齐正式人工裁决，不能在本轮自动改动。

冻结 Oracle v2 范围内没有新增 P0/P1，原59轮全部通过，两个错误目标消除；但存在四项旧合同回归冲突，因此不能笼统声称“全部兼容、无任何回归”。

## 7. 安全、保全及未验证范围

- 843 个既有文件只发现两处授权业务源码变化；oracle.v1/v2/v2.pending、所有原冻结清单、人工裁决、v2.1 评分器、旧 v3/v4、失败轨迹及 RAG 资产未改。
- 完整业务运行使用真实 Worker/State/Goal/Router/Planner/Tools/Validator，但任务存储、SQL协议和 analytics 是原内存/合成替身，分类/检索响应固定。连接替身 **64/64关闭**，禁止外部调用尝试 **0**。
- 兼容回归仅使用本轮 synthetic-temp 内的合成 SQLite 夹具；共 13707 次连接，外部尝试 **0**。真实 PostgreSQL、业务库连接/写入、收费供应商、真实 Embedding 均 **0**。
- P6-E1B 安全门、授权、恢复计划及失败证据未改；没有执行恢复、数据库程序或 PG-01～08，继续 BLOCKED。
- 未验证真实数据库集成、真实向量覆盖及真实模型语义质量；本轮结果不能外推为它们通过。

## 8. 最终结论与停止边界

| 验收层次 | 结论 |
|---|---|
| G24/G30 正式人工 A 最小修复 | **PASS**，17项专项通过，目标未确认时工具零执行 |
| 冻结 Oracle v2 全量业务 | **61 PASS / 1 FAIL**，保留 G25-T1 |
| 原59轮PASS保全 | **59/59 PASS** |
| 既有兼容回归 | **FAIL：1 failure + 3 errors**，四项旧断言与新语义冲突，未掩盖 |
| 第一阶段总体业务验收 | **仍未整体 PASS**，G25-T1 与兼容合同冲突保留 |
| 真实环境/P6-E1B | **BLOCKED**；PG-01～08 NOT EXECUTED |

完成本轮两项最小修复和独立测试后停止，不自动修改旧测试、修复 RAG、恢复数据库、调用真实模型或进入部署。

复验（新输出目录，不覆盖旧证据）：

```powershell
$env:PYTHONPATH='tests;scripts'
.\.venv\Scripts\python.exe -m unittest test_agent_stage1_v2_fixes -v
.\.venv\Scripts\python.exe scripts/run_agent_stage1_v2_fixes.py
.\.venv\Scripts\python.exe scripts/check_agent_stage1_p0p1_regression.py --source-revision docs/evaluation/agent-stage1-v2-fix/final/source-revision.v5.json --targets docs/evaluation/agent-stage1-v2-fix/regression-targets.json --output-dir .cache/agent-stage1-v2-fix/final/regression-new
```

[比较与安全汇总](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-v2-fix/final/comparison-summary.json)；[执行产物摘要](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-v2-fix/final/artifact-sha256.json)；[含本报告的最终摘要](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-v2-fix/final/final-artifact-sha256.json)。
