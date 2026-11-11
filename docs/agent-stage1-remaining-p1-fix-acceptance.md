# Agent 第一阶段：剩余普通 P1 多轮业务修复验收

项目：`E:\AllProject\data-analysis-agent`。日期：2026-11-11。

**本轮四项普通 P1 离线修复 PASS；完整业务验收仍不能标记 PASS。** 原 30 组、62 轮使用同一冻结预期，结果从 **47 PASS / 5 FAIL / 10 PENDING_HUMAN** 变为 **51 PASS / 1 FAIL / 10 PENDING_HUMAN**。仅 G05-T2、G09-T2、G25-T2、G26-T2 由 FAIL 转 PASS；此前通过项无退化。唯一 FAIL 仍为明确禁止本轮修改的 **G25-T1 RAG 命题许可**。

本轮没有调用真实 PostgreSQL、收费模型或真实 Embedding，没有恢复数据库、修改 P6-E1B 授权/安全门、扩展 P6 执行范围或修改冻结 RAG/人工评审资产。

## 1. 实际复现与保全

先阅读当前源码及两份既有业务验收报告，用 v3 源码绑定重新执行原 62 轮，保存为 `.cache/agent-stage1-remaining-p1/run-01.json`。四项问题均实际复现。新增专项的首次结果为 **18 项，4 failure、5 error**；其中错误是实际拒绝/未执行路径不满足正向预期而产生的异常，并非忽略不计。证据位于 `.cache/agent-stage1-remaining-p1/before/special-tests.txt`。后续追加三个业务边界测试，最终新增专项 21 项。

原 `oracle.v1.json`、`corpus.v1.json`、`freeze-manifest.json`、旧 v3 绑定、旧报告和原失败轨迹均保持字节不变；评分代码、REVIEW 的 10 轮清单与数据替身未改。新增 [source-revision.v4.json](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-remaining-p1/source-revision.v4.json) 绑定原 freeze-manifest 及 v3 摘要，列出相对 v3 的四个业务文件变化，不覆盖旧版本。它只是源码审计清单，不是费用、数据库或恢复授权。

最终完整运行：`.cache/agent-stage1-remaining-p1/run-02.json`；评分：同目录 `assessment-run-02.json`。

- [四项修复前后完整九字段及事件](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-remaining-p1/targeted-before-after.json)。
- [全部 62 轮结果、任务 JSON 与逐节点轨迹](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-remaining-p1/after-turns.jsonl)。
- [指标与原通过项差异](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-remaining-p1/comparison-summary.json)。
- [相对本轮开始源码的精确增量](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-remaining-p1/implementation.diff)。仓库此前已有未提交修改，本报告不把它们冒认为本轮修改。

## 2. 四项根因、修改及安全边界

### G05-T2：明确更换维度没有作为同层次追问

旧 `active_request` 只处理否定数量/主题等产物，未识别“不要分析物流了，改看商品质量”。Goal 看到 quality 但默认 statistics；Router 的短维度追问语法未覆盖“改看”，最终澄清。

修改 [review_semantics.py:200](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_semantics.py:200)：新增有限的 `ASPECT_SWITCH` 词法模式，`active_request` 仅在取消旧维度、明确指定唯一新维度时提取后句，不删除原用户问题。`build_request_goal` 在唯一历史查询可确定层次时承接该层次；`_followup_intent` 将该表达作为维度替换追问。

实际 G05-T2：商品 053、themes、quality、negative；仅调用 quality 的 get_aspect_drilldown，读取 2 条质量负向评论，D=8；引文均为质量观点且可按原文位置重取，未执行物流分类。此前统计任务后“改看质量”仍统计，原文任务后仍原文；无历史或多种历史任务不猜层次，保持澄清。

独立 Goal 不调用 Router/Planner/opinion_request；Router 测试禁止读取 Goal。共享的是有限的词法切分，不是完整意图答案。完整 `question`、`RequestGoal.original_question` 和原始任务问题继续保留。

### G09-T2：日期变化误用页面全选替换全部有效条件

直接 `restore_pending_goal` 原本能恢复同会话新日期、单商品待办；问题发生在 `load_analysis_context` 最终 State 投影：只要 selection_changes 非空，就用页面全 scope（两商品）替换有效 scope，清掉合法的单商品 pending。

修改 [analysis_context.py:92](E:/AllProject/data-analysis-agent/src/data_analysis_agent/jobs/analysis_context.py:92)：有效投影范围先采用已经通过原严格检查的 pending scope，或可信历史 scope，再仅覆盖本轮真实变化的字段组（商品、当前日期、基线、as_of）。日期变化不会顺带扩大商品；真正 SKU/标注身份/日期冲突仍清除或拒绝继承。

`analysis_state_for_scope`、`pending_context`、`restore_ranking`、`restore_pending_goal` 实现均未修改，没有放宽来源或范围比较。旧日期排名仍失效；G09-T2 为 ranking_scope_mismatch、unsupported、query_executions=0，没有主题分类。新 pending 保留 053、02-15～02-28、themes/negative，completed=false。额外真实 Worker 追问“客服方面呢？”只分析新窗口的 3 条客服负面评论，没有重用旧窗口 5 条样本或旧排名。

### G25-T2：旧 RAG 的追问屏障优先于完整新统计请求

旧 `_followup_intent` 看到数量表达便尝试继承上一轮 semantic_evidence，随即要求补全检索问题，忽略本轮已完整指定物流、差评、数量。

修改 [review_intent.py:195](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_intent.py:195)：本轮同时明确统计、维度、正负方向时，旧 semantic_evidence 不再参与省略推断，交给既有普通统计 Planner；“有多少条？”“客服呢？”等未完整指定目标的 RAG 后追问仍澄清。没有扩大 RAG 许可或修改任何检索命题规则。

实际 G25-T2：statistics/logistics/negative、055，get_top_issues；按全量成功标注 D=8 得到物流负面 M=4；没有查询向量、原文检索或调用模型。G25-T1 的 query_semantics_uncovered/permitted_keys=[] 保持原 FAIL。

### G26-T2：数量追问转了层次但独立 Goal 丢掉维度

旧 Router 能从单一原文任务继承 logistics/negative，而 Goal 的 inherit_aspect 只处理短维度、情感或内容词，未覆盖“有多少条？”。最终统计可能正确，但独立目标约束不完整。

修改 [review_acceptance.py:135](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_acceptance.py:135)：有限数量引用明确改为 statistics，并只在唯一历史查询已确定 aspect 及 positive/negative 时继承目标。多维或双情感历史不猜，Router 继续澄清；原文任务方向/维度不明确也在 Router 中受控澄清。

实际 G26-T2：statistics/logistics/negative、053；get_top_issues 参数没有 page_size/top_n 展示限制，D=8、物流负面 M=3，保留完整六维统计。额外 055 场景首轮只展示 3 条原文、实际 total=4，第二轮统计得到 **4** 而非 3，证明不是只改输出标签或复用展示样本数量。

## 3. 修改文件与未修改边界

| 文件 | 函数与修改 |
|---|---|
| `src/data_analysis_agent/agents/review_semantics.py` | `ASPECT_SWITCH`、`active_request`，仅有限维度替换词法 |
| `src/data_analysis_agent/agents/review_acceptance.py` | `build_request_goal`，明确维度切换层次和数量引用目标 |
| `src/data_analysis_agent/agents/review_intent.py` | `_followup_intent`，维度替换、明确统计与 RAG 优先级、原文数量引用方向不明时澄清 |
| `src/data_analysis_agent/jobs/analysis_context.py` | `load_analysis_context`，按变化字段组合 State 最终投影范围 |
| `tests/test_agent_stage1_remaining_p1.py` | 新增独立真值、真实 Worker、工具证据和冲突边界专项 |
| `docs/evaluation/agent-stage1-remaining-p1/` | v4 源码绑定、独立运行清单、结果、摘要和增量证据 |

没有修改已有 P0/P1 专项、已有兼容测试、Planner/Tools、主题分类、共享预算、普通或 P6 Validator、State 来源校验、RAG 命题许可、P6-E1B 相关脚本/计划/授权。原 P1-01 的工具前冲突阻断实现未变。

## 4. 四项修复前后九字段对照

九字段沿用原评分口径：analysis_state 为范围投影后的输入可信状态，pending_goal 为本轮后实际可恢复状态；analysis_intent/query_plan/required_tools 比较首个已确定主计划，最终补证计划单独留痕。澄清轮没有最终计划或任务验收并不等于完成。

### G05-T2

输入：不要分析物流了，改看商品质量。

| 字段 | 修复前实际 | 修复后实际 |
|---|---|---|
| analysis_state | `{"ranking_basis":"absent","pending_goal":"absent"}` | `{"ranking_basis":"absent","pending_goal":"absent"}` |
| pending_goal（轮后） | `"absent"` | `"absent"` |
| RequestGoal | `{"original_question":"不要分析物流了，改看商品质量。","levels":["statistics"],"aspect":"quality","sentiment":"negative"}` | `{"original_question":"不要分析物流了，改看商品质量。","levels":["themes"],"aspect":"quality","sentiment":"negative"}` |
| user_intent | `{"query_type":null,"sentiment":null,"aspect":"quality","needs_clarification":true,"resolved_product_ids":["100013588053"]}` | `{"query_type":"aspect_drilldown","sentiment":"negative","aspect":"quality","needs_clarification":false,"resolved_product_ids":["100013588053"]}` |
| analysis_intent（主计划） | `"not_finalized"` | `"aspect_drilldown"` |
| query_plan | `{"blocked":true,"analysis_type":null,"aspects":[],"sentiment":null,"product_ids":["100013588053"]}` | `{"blocked":false,"analysis_type":"aspect_drilldown","aspects":["quality"],"sentiment":"negative","product_ids":["100013588053"]}` |
| required_tools | `[]` | `["get_aspect_drilldown"]` |
| task_acceptance | `null` | `{"status":"passed","complete":true}` |
| 最终状态 | `{"agent":"unsupported","worker":"succeeded"}` | `{"agent":"ok","worker":"succeeded"}` |

query_executions：0 → 2；工具执行：`[]` → `['get_analysis_context', 'get_aspect_drilldown']`。

### G09-T2

输入：具体体现在哪里？

| 字段 | 修复前实际 | 修复后实际 |
|---|---|---|
| analysis_state | `{"ranking_basis":"absent","pending_goal":"absent"}` | `{"ranking_basis":"absent","pending_goal":"absent"}` |
| pending_goal（轮后） | `"absent"` | `"awaiting_aspect"` |
| RequestGoal | `{"original_question":"具体体现在哪里？","levels":["themes"],"aspect":null,"sentiment":"negative"}` | `{"original_question":"具体体现在哪里？","levels":["themes"],"aspect":null,"sentiment":"negative"}` |
| user_intent | `{"needs_clarification":true,"resolved_product_ids":["100013588053"]}` | `{"needs_clarification":true,"resolved_product_ids":["100013588053"]}` |
| analysis_intent（主计划） | `"not_finalized"` | `"not_finalized"` |
| query_plan | `{"blocked":true,"analysis_type":null,"aspects":[],"sentiment":null,"product_ids":["100013588053"]}` | `{"blocked":true,"analysis_type":null,"aspects":[],"sentiment":null,"product_ids":["100013588053"]}` |
| required_tools | `[]` | `[]` |
| task_acceptance | `null` | `null` |
| 最终状态 | `{"agent":"unsupported","worker":"succeeded"}` | `{"agent":"unsupported","worker":"succeeded"}` |

query_executions：0 → 0；工具执行：`[]` → `[]`。

修复后 pending scope 为 053、2023-02-15～2023-02-28；ranking_basis=None，仍不执行主题，也不把待办当完成事实。

### G25-T2

输入：只统计物流差评数量。

| 字段 | 修复前实际 | 修复后实际 |
|---|---|---|
| analysis_state | `{"ranking_basis":"absent","pending_goal":"absent"}` | `{"ranking_basis":"absent","pending_goal":"absent"}` |
| pending_goal（轮后） | `"absent"` | `"absent"` |
| RequestGoal | `{"original_question":"只统计物流差评数量。","levels":["statistics"],"aspect":"logistics","sentiment":"negative"}` | `{"original_question":"只统计物流差评数量。","levels":["statistics"],"aspect":"logistics","sentiment":"negative"}` |
| user_intent | `{"query_type":null,"sentiment":"negative","aspect":"logistics","needs_clarification":true,"resolved_product_ids":["100013588055"]}` | `{"query_type":"aspect_summary","sentiment":"negative","aspect":"logistics","needs_clarification":false,"resolved_product_ids":["100013588055"]}` |
| analysis_intent（主计划） | `"not_finalized"` | `"issues"` |
| query_plan | `{"blocked":true,"analysis_type":null,"aspects":[],"sentiment":null,"product_ids":["100013588055"]}` | `{"blocked":false,"analysis_type":"issues","aspects":["logistics"],"sentiment":"negative","product_ids":["100013588055"]}` |
| required_tools | `[]` | `["get_top_issues"]` |
| task_acceptance | `null` | `{"status":"passed","complete":true}` |
| 最终状态 | `{"agent":"unsupported","worker":"succeeded"}` | `{"agent":"ok","worker":"succeeded"}` |

query_executions：0 → 2；工具执行：`[]` → `['get_analysis_context', 'get_top_issues']`。

### G26-T2

输入：有多少条？

| 字段 | 修复前实际 | 修复后实际 |
|---|---|---|
| analysis_state | `{"ranking_basis":"absent","pending_goal":"absent"}` | `{"ranking_basis":"absent","pending_goal":"absent"}` |
| pending_goal（轮后） | `"absent"` | `"absent"` |
| RequestGoal | `{"original_question":"有多少条？","levels":["statistics"],"aspect":null,"sentiment":"negative"}` | `{"original_question":"有多少条？","levels":["statistics"],"aspect":"logistics","sentiment":"negative"}` |
| user_intent | `{"query_type":"aspect_summary","sentiment":"negative","aspect":"logistics","needs_clarification":false,"resolved_product_ids":["100013588053"]}` | `{"query_type":"aspect_summary","sentiment":"negative","aspect":"logistics","needs_clarification":false,"resolved_product_ids":["100013588053"]}` |
| analysis_intent（主计划） | `"issues"` | `"issues"` |
| query_plan | `{"blocked":false,"analysis_type":"issues","aspects":["logistics"],"sentiment":"negative","product_ids":["100013588053"]}` | `{"blocked":false,"analysis_type":"issues","aspects":["logistics"],"sentiment":"negative","product_ids":["100013588053"]}` |
| required_tools | `["get_top_issues"]` | `["get_top_issues"]` |
| task_acceptance | `{"status":"passed","complete":true}` | `{"status":"passed","complete":true}` |
| 最终状态 | `{"agent":"ok","worker":"succeeded"}` | `{"agent":"ok","worker":"succeeded"}` |

query_executions：2 → 2；工具执行：`['get_analysis_context', 'get_top_issues']` → `['get_analysis_context', 'get_top_issues']`。


## 5. 完整多轮评测与历史 PASS

预期、输入、数据模式、故障注入、REVIEW 清单及评分代码均不变。自然模块分母仍为 50；工具结果为 48；两个主动故障计入轮级契约分母 52；应完成任务分母 43；整组可裁决分母 21。

| 指标 | 修复前（v3） | 修复后（v4） |
|---|---:|---:|
| State 上下文 | 49/50（98.00%） | 50/50（100.00%） |
| Intent Router | 48/50（96.00%） | 50/50（100.00%） |
| RequestGoal | 48/50（96.00%） | 50/50（100.00%） |
| Planner | 48/50（96.00%） | 50/50（100.00%） |
| 工具产物匹配 | 46/48（95.83%） | 48/48（100.00%） |
| Validator 主动故障拦截 | 2/2（100.00%） | 2/2（100.00%） |
| 轮级契约符合 | 47/52（90.38%） | 51/52（98.08%） |
| 轮级严格完成 | 39/43（90.70%） | 42/43（97.67%） |
| 整组契约符合 | 17/21（80.95%） | 20/21（95.24%） |

模块 100% 仅是这些可评分、离线合成请求上的结果，**不是实际用户语言或真实主题质量 100%**。Validator 2/2 也只表示两种主动破坏被拦截；不能外推为全部业务错误识别率。

| 组 | v3 | v4 |
|---|---|---|
| G01 | PASS | PASS |
| G02 | PASS | PASS |
| G03 | PASS | PASS |
| G04 | PASS | PASS |
| G05 | FAIL | PASS |
| G06 | PASS | PASS |
| G07 | PASS | PASS |
| G08 | PASS | PASS |
| G09 | FAIL | PASS |
| G10 | PENDING_HUMAN | PENDING_HUMAN |
| G11 | PENDING_HUMAN | PENDING_HUMAN |
| G12 | PENDING_HUMAN | PENDING_HUMAN |
| G13 | PASS | PASS |
| G14 | PASS | PASS |
| G15 | PASS | PASS |
| G16 | PASS | PASS |
| G17 | PENDING_HUMAN | PENDING_HUMAN |
| G18 | PENDING_HUMAN | PENDING_HUMAN |
| G19 | PENDING_HUMAN | PENDING_HUMAN |
| G20 | PENDING_HUMAN | PENDING_HUMAN |
| G21 | PASS | PASS |
| G22 | PASS | PASS |
| G23 | PASS | PASS |
| G24 | PENDING_HUMAN | PENDING_HUMAN |
| G25 | FAIL | FAIL |
| G26 | FAIL | PASS |
| G27 | PASS | PASS |
| G28 | PASS | PASS |
| G29 | PASS | PASS |
| G30 | PENDING_HUMAN | PENDING_HUMAN |

G13/G14/G15、G22/G23、G28 与 G01～G04 原通过路径均保持。G28 目标/计划冲突后仍是 0 次业务查询/工具/分类、无补证、无正文；G29 篡改商品来源仍拒绝发布。四个本轮原 FAIL 转 PASS，未增加原通过项失败。

## 6. 测试与执行层次

新增 21 项专项，检查真实条件而非只对标签作断言：质量观点数/原文位置；统计/原文层次的维度切换；未知层次澄清；日期 pending 严格恢复、身份/SKU/再次日期变化失效、跨会话/摘要篡改拒绝；新窗口后续只读新样本；RAG 后完整统计读取全量分母、不再检索；模糊 RAG 追问仍澄清；G25-T1 许可不扩大；数量引用单维/方向唯一性、双情感/多维拒绝；3 条展示不限制 4 条总体。

| 验证层 | 结果 |
|---|---|
| 新增剩余 P1 专项 | **21/21 PASS**，真实 Worker 和独立 Goal/Router 双侧真值检查 |
| 此前 P0/P1 专项 | **25/25 PASS**，G13～15、G22、G23、G28 不回退 |
| 聚焦两阶段专项合计 | **46/46 PASS**，0 failure/error/skip；日志 `special-02.txt` |
| 完整适用兼容回归 | **650/650 PASS**，275.551 秒，0 failure/error/skip |
| 旧测试 ID | 原 629 个全部保留，新增 21 个，无删除或放宽旧断言 |
| test_review_router_p0a | 10/10 PASS |
| test_review_acceptance_p0b | 14/14 PASS |
| test_trusted_analysis_state | 18/18 PASS |
| test_p1a_independent_validation | 11/11 PASS |
| test_p1a_f01 | 13/13 PASS |
| test_p6a_contract | 19/19 PASS |
| test_p6b_planner_snapshot | 19/19 PASS |
| test_p6c_budget_execution | 25/25 PASS |
| test_p6d_publication | 19/19 PASS |
| test_p6e1a_provider_contract | 39/39 PASS |
| 主评测 30 组 / 62 轮 | 51 PASS、1 FAIL、10 PENDING_HUMAN；旧 47 个 PASS 均保持 |
| 工具授权/澄清 | 0 次未经当时计划授权的工具执行；7 个预期澄清轮 query_executions 均为 0；G28 工具为 0 |
| 冻结输入连接边界 | 主评测 64 个协议连接替身全部关闭；55 次固定模型响应尝试，真实供应商/Embedding 调用为 0 |
| 兼容回归数据库 | 13715 次 SQLite 连接全部在本轮合成临时目录；外部连接尝试为 0 |
| 真实环境 | PostgreSQL/业务库/收费模型/真实 Embedding 为 0；PG-01～08 NOT EXECUTED，P6-E1B 继续 BLOCKED |

完整旧/新测试 ID、逐模块数量及安全连接记录见 [regression-summary.json](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-remaining-p1/regression-summary.json)；完整日志 `.cache/agent-stage1-remaining-p1/regression-01/tests.txt`。

主评测没有数据库：任务存储、目录/事务协议、analytics DTO 使用原合成替身，真实 Worker、State、Goal、Router、Planner、Tools、主题程序和 Validator 执行；P6 仍用离线 transport 和合成费用。没有为这四组新增数据替身特殊逻辑。真正 PostgreSQL 一致性与真实模型语义均不在该证明范围内。

兼容回归沿用已授权的合成 SQLite 夹具，连接只允许本轮回归目录的 synthetic-temp，不连接项目业务库；PostgreSQL 连接、网络和 PostgreSQL 程序均硬阻断。原 629 个测试 ID 保留，并加入本轮专项；没有 skip、expectedFailure、删除断言或修改原测试起点来取得通过。

## 7. 保留的 FAIL、待裁决及是否仍有错误目标完成

**唯一 FAIL：G25-T1。** 商品 055 的合成检索来源校验通过、有候选观点，但带 SKU 问题仍不能获得 reported-experience 命题许可，assessment=query_semantics_uncovered、permitted_keys=[]。真实回答仍明确不能确定；它没有 task_acceptance.complete=true。旧 RAG 外层 ok 只是检索流程结束，不等于原事实问题完成。此项未修复且未用人工评分/假证据代替。

原 10 轮 **PENDING_HUMAN** 继续保留：G10-T2、G11-T2、G12-T1/T2、G17-T2、G18-T2、G19-T2、G20-T2、G24-T2、G30-T3。它们涉及独立预期的歧义/范围定义或旧状态字段，不能因当前输出更顺畅或部分安全检查成立就自动改成 PASS。此次没有扩大评分分母。

在原可评分对话中，没有再发现错误商品、错误范围、错误普通目标却 task_acceptance.complete=true；四项修复后结果均与独立目标一致。受控澄清 G09 不完成主题分析，旧排名不恢复；原故障注入不发布成功。对待裁决轮不作“目标正确”的最终判断，尤其 G30-T3 的实际 complete=true 是否对应用户想要的层次仍待人工决定。因此不能声称全体 62 轮均已证明不存在错误目标完成，更不能推断所有未来输入都安全。

**准入结论：本轮四项普通 P1 修复 PASS，可以进入人工裁决和下一阶段业务验收准备；整个第一阶段仍受 1 FAIL 与 10 待裁决限制，不能宣布完整业务 PASS。** 本轮停止，不自动修复 RAG、裁决预期、执行真实模型、恢复数据库或启动下一阶段。

## 8. 复验与最终完整性

在仓库根目录使用项目 `.venv`，新的运行采用独立 run-NN，汇总排他写入，不覆盖旧证据：

```powershell
.\.venv\Scripts\python.exe tests/agent_stage1_multiturn_evaluation.py --source-revision docs/evaluation/agent-stage1-remaining-p1/source-revision.v4.json --output-dir .cache/agent-stage1-remaining-p1
.\.venv\Scripts\python.exe scripts/summarize_agent_stage1_evaluation.py .cache/agent-stage1-remaining-p1/run-02.json
.\.venv\Scripts\python.exe scripts/check_agent_stage1_p0p1_regression.py --source-revision docs/evaluation/agent-stage1-remaining-p1/source-revision.v4.json --targets docs/evaluation/agent-stage1-remaining-p1/regression-targets.json --output-dir .cache/agent-stage1-remaining-p1/regression-new
```

第二条对已存在的本轮评分文件会拒绝覆盖，应改用第一条新产生的 run 文件。v4 与当前代码摘要绑定；未来更改应另行审阅与保存新版本，不能改原 oracle、v3 或本次失败账本。

最终完整性复核：74 个预先保存的旧冻结文件、既有评测/源码版本证据、P6-E1B 报告/安全脚本/测试/恢复与授权材料摘要相同；普通 Agent 工具前硬阻断、State 来源校验与 RAG 许可代码相对 v3 不变；`validate_goal_plan` 与 `validate_task_completion` AST 不变。v4 校验通过。最终产物 SHA-256 见同目录 `artifact-sha256.json`。
