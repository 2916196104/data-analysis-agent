# Agent 第一阶段：P0/P1 业务缺陷最小修复验收

项目：`E:\AllProject\data-analysis-agent`。执行跨 2026-11-10～11，交付日期：2026-11-11。

本轮只修复 P0-01、P0-02、P1-01。**三项修复离线验收 PASS；完整多轮业务验收仍未通过。** 同一 30 组、62 轮评测由 42 PASS / 10 FAIL / 10 PENDING_HUMAN，变为 **47 PASS / 5 FAIL / 10 PENDING_HUMAN**。G13/G14/G15-T2、G22/G23-T2 按原冻结预期转为 PASS；G28-T2 在继续拒绝错误发布的同时，实现了工具前阻断。

原通过轮没有退化。剩余失败与待裁决项不属于本次已授权的三项修复，未自动修改。真实 PostgreSQL、收费供应商、真实 Embedding 调用为零；P6-E1B 仍 BLOCKED，没有执行恢复或 PG-01～08。

## 1. 原始证据、复现与预期保全

先阅读当前源码及 [原独立业务验收报告](E:/AllProject/data-analysis-agent/docs/agent-stage1-multiturn-business-evaluation.md)，再用原入口执行完整 62 轮，保存为 `.cache/agent-stage1/run-07.json`。该轮重现了三项问题，没有使用旧报告替代实际复现。原 run-06、原报告、原评分及证据均未覆盖。

首次新增专项测试执行 **21 项，15 个失败、1 个错误**（包含两个方向的 subTest 失败），证据保存在 `.cache/agent-stage1-p0p1/before/special-tests.txt`；随后修复。另补充了失败后显式日期承接/日期冲突三项边界及“已确认查询”前缀不能绕过阻断的反例，最终专项为 25 项。

严格保留三个原冻结文件，SHA-256 与原始字节一致：

| 文件 | SHA-256 |
|---|---|
| [oracle.v1.json](E:/AllProject/data-analysis-agent/tests/fixtures/agent_stage1/oracle.v1.json) | `08b9af5a94f8d86ba44d355f7d72d1482885c357d7d1429564850375af441e42` |
| [corpus.v1.json](E:/AllProject/data-analysis-agent/tests/fixtures/agent_stage1/corpus.v1.json) | `ffc3b996e855417cc259cfa148e49c903f7e82398e7065656282a491bc2b3775` |
| [freeze-manifest.json](E:/AllProject/data-analysis-agent/tests/fixtures/agent_stage1/freeze-manifest.json) | 见本轮 [source-revision.v3.json](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-p0p1/source-revision.v3.json) 中 baseline_manifest_sha256；原文件没有回写 |

旧 freeze-manifest 同时绑定修复前代码，因此不能直接改其中代码摘要来伪造“源码未变”。本轮新增独立源码修订绑定：允许且仅允许下述五个业务文件改变，其余原业务源文件仍逐一核验；原 oracle、corpus 和裁决/评分规则不变。v1、v2 绑定及中间修复运行也保留，最终运行使用 v3。此绑定只是离线评测的代码版本清单，**不是数据库、费用或恢复授权**。

最终完整运行：`.cache/agent-stage1-p0p1/run-03.json`；评分：`.cache/agent-stage1-p0p1/assessment-run-03.json`。

可直接审阅的交付证据：

- [五个业务文件及两份兼容测试的本轮增量 implementation.final.diff](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-p0p1/implementation.final.diff)，相对任务开始时的真实文件，而非相对包含历史未提交修改的 HEAD。
- [六个目标轮修复前后及事件轨迹](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-p0p1/targeted-before-after.final.json)。
- [完整 62 轮修复后原始任务与轨迹](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-p0p1/after-turns.final.jsonl)。
- [指标、原通过项差异及待裁决清单](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-p0p1/comparison-summary.final.json)。

## 2. 根因与最小修改

### P0-01：失败后的商品条件被成功事实门槛吞掉

`load_analysis_context` 已允许失败任务提供原始用户请求，但旧 `_inherit_scope` 只有在 `context.validated` 存在时才继承商品。G13/G14/G15 首轮 unsupported/error，没有可信分析结果，导致下一轮回退到页面默认选择的两款商品；数值 Validator 只能校验已经错误确认的范围，仍会 complete=true。

修改：

- [jobs/analysis_context.py:11](E:/AllProject/data-analysis-agent/src/data_analysis_agent/jobs/analysis_context.py:11) 的 `load_analysis_context`：将页面选择变更识别移到成功事实判定之外；恢复用户条件前验证历史参数、原选择、当前选择和标注身份。这样失败之后的页面 SKU/日期变化也能优先生效。会话、封存摘要、四轮上限与原成功事实验收保持。
- [review_intent.py:257](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_intent.py:257) 的 `_inherit_scope`：没有可信分析结果时，仍可从通过上下文边界的用户原文解析显式商品；再用当前商品目录和选择范围验证。未知或冲突商品拒绝，不静默替换成页面商品。显式日期仅从用户原文重新解析、按当前有效范围复核，不读失败 result.scope；本轮文字或页面明确变化优先。

没有把失败结果包装成 validated，没有使用失败统计、排名、主题或结果中的 scope 作为事实。`jobs/analysis_state.py`、`restore_ranking`、`restore_pending_goal` 及其信任门未修改。G13～15-T2 的 ranking_basis 和 validated 仍为空，统计对象却正确保持 053。

### P0-02：被否定的关键词仍参与目标识别

旧独立目标和 Router 分别识别关键词，但都没有充分区分“被否定的旧产物”与“本轮肯定的新请求”。G22 的“数量”吞掉主题目标，G23 的“归纳主题”吞掉明确统计目标；两层一致不代表业务正确。

修改：

- [review_semantics.py:200](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_semantics.py:200) 新增小型纯函数 `active_request`，只处理明确的否定产物前缀、标点分隔和肯定请求标记，例如“不是问数量，**我想知道…**”“不要归纳主题了，**只统计…**”。没有唯一明确后句时原样返回，不统一优先主题或统计，也不把旧维度切换 G05 顺带改成成功。
- `normalize_question` / `opinion_request` 使用生效子句；对“具体在抱怨什么”的明确主题表达增加规则。实际 Planner 继续沿用现有编译路径，没有重写 Planner。
- [review_acceptance.py:41](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_acceptance.py:41) 的 `build_request_goal` 独立从生效文本生成产物，仍不调用 opinion_request、Router 或 Planner。只共享子句提取这一词法步骤，未共享完整意图答案。

独立测试分别按文字真值断言 RequestGoal 和 Router；并禁止 Goal 调用 Router 分类函数、禁止 Router 读取 Goal 的分类结果。两个方向都覆盖，同时保留普通统计、主题、原文三种能力的原有优先级。`question`、`RequestGoal.original_question`、任务问题和 Runtime Contract 原始问题继续保存用户完整原文；没有删除审计内容，也没有调用模型代替规则修复。

### P1-01：已知目标冲突后仍进入工具与补证

普通 Agent 旧实现仅保存 task_plan_check，未在 false 时退出；G28 即使独立 Goal=themes、主计划=statistics，仍先查统计、再补查观点，最后才拒绝发布。

在 [reviews.py:252](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/reviews.py:252) 增加普通路径硬阻断：合法主计划与独立 Goal 不一致时立即抛受控业务错误，早于 ReviewToolContext、工具注册及自动 overview。明确 unsupported 的旧受控退出仍保留。task_plan_check 原因、task_goal、失败 task_acceptance 和稳定错误均保存。

P6 分支原有的 validate_p6_plan 工具前检查未改。普通 `validate_task_completion` 和数值/来源 Validator 的实现未改，仍作为第二道发布防线；阻断轮不再为了“运行 Validator”而执行不必要工具。独立测试直接向最终验收器提供冲突目标/计划，继续被拒绝；G29 的来源篡改仍被拒绝。合法补证流程保留，由既有回归验证。

## 3. 修改文件

| 文件 | 本轮变化 |
|---|---|
| `src/data_analysis_agent/jobs/analysis_context.py` | 失败轮选择变化及用户条件边界校验 |
| `src/data_analysis_agent/agents/review_intent.py` | `_inherit_scope` 区分用户条件与可信结果；目录/范围冲突拒绝 |
| `src/data_analysis_agent/agents/review_semantics.py` | 明确纠正子句提取与“具体在抱怨什么”表达 |
| `src/data_analysis_agent/agents/review_acceptance.py` | 独立 Goal 使用生效子句，保留原问题 |
| `src/data_analysis_agent/agents/reviews.py` | 普通主计划冲突时在工具前退出 |
| `tests/test_agent_stage1_p0_p1_fixes.py` | 新增真实 Worker、独立 Goal/Router、范围冲突和双层阻断专项 |
| `tests/test_review_dimension_content.py`、`tests/test_review_evidence_loop.py` | 冲突主计划场景加强为零查询；合法补证夹具明确统计与解读目的并增加主计划 passed 断言，保留补证/预算/引用检查 |
| 原评测入口和汇总/检查脚本 | 只新增显式源码修订绑定、独立输出目录、排他写入；数据替身及业务评分判定未特判目标组 |
| `scripts/check_agent_stage1_p0p1_regression.py` | 运行原适用业务回归；SQLite 仅允许本轮隔离合成临时目录，禁止 PostgreSQL/网络及 PostgreSQL 程序 |
| 新报告及 `docs/evaluation/agent-stage1-p0p1/` | 修复后证据、完整指标与代码绑定 |

没有修改 Tools、Planner、主题分类器、共享费用契约、P6 发布规则、冻结 RAG/人工评审资产或 P6-E1B 安全门/授权文件；没有清理 13 项历史私有接口导入问题。

## 4. 六个目标轮的九字段修复前后对照

这里直接比较修复前实际执行与修复后实际执行；两边输入、冻结预期、数据模式和故障注入完全相同。State 用可信排名/待办是否存在表示，完整来源摘要见 JSON。query_plan 同时区分首个主计划与最终补证计划。

### G13-T2

输入：只统计客服差评有多少条。

| 字段 | 修复前实际 | 修复后实际 |
|---|---|---|
| analysis_state | `{"ranking_basis":"absent","pending_goal":"absent"}` | `{"ranking_basis":"absent","pending_goal":"absent"}` |
| pending_goal | `"absent"` | `"absent"` |
| RequestGoal | `{"original_question":"只统计客服差评有多少条。","levels":["statistics"],"aspect":"service","sentiment":"negative"}` | `{"original_question":"只统计客服差评有多少条。","levels":["statistics"],"aspect":"service","sentiment":"negative"}` |
| user_intent | `{"query_type":"aspect_summary","sentiment":"negative","aspect":"service","needs_clarification":false,"resolved_product_ids":["100013588053","100013588055"]}` | `{"query_type":"aspect_summary","sentiment":"negative","aspect":"service","needs_clarification":false,"resolved_product_ids":["100013588053"]}` |
| analysis_intent（首个确定主计划） | `"issues"` | `"issues"` |
| query_plan | `{"blocked":false,"analysis_type":"issues","aspects":["service"],"sentiment":"negative","product_ids":["100013588053","100013588055"]}` | `{"blocked":false,"analysis_type":"issues","aspects":["service"],"sentiment":"negative","product_ids":["100013588053"]}` |
| required_tools（主计划） | `["get_top_issues"]` | `["get_top_issues"]` |
| task_acceptance | `{"status":"passed","complete":true}` | `{"status":"passed","complete":true}` |
| 最终 Agent / Worker 状态 | `{"agent":"ok","worker":"succeeded"}` | `{"agent":"ok","worker":"succeeded"}` |

业务查询计数：2 → 2；实际工具：`['get_analysis_context', 'get_top_issues']` → `['get_analysis_context', 'get_top_issues']`。

### G14-T2

输入：物流差评有多少条？

| 字段 | 修复前实际 | 修复后实际 |
|---|---|---|
| analysis_state | `{"ranking_basis":"absent","pending_goal":"absent"}` | `{"ranking_basis":"absent","pending_goal":"absent"}` |
| pending_goal | `"absent"` | `"absent"` |
| RequestGoal | `{"original_question":"物流差评有多少条？","levels":["statistics"],"aspect":"logistics","sentiment":"negative"}` | `{"original_question":"物流差评有多少条？","levels":["statistics"],"aspect":"logistics","sentiment":"negative"}` |
| user_intent | `{"query_type":"aspect_summary","sentiment":"negative","aspect":"logistics","needs_clarification":false,"resolved_product_ids":["100013588053","100013588055"]}` | `{"query_type":"aspect_summary","sentiment":"negative","aspect":"logistics","needs_clarification":false,"resolved_product_ids":["100013588053"]}` |
| analysis_intent（首个确定主计划） | `"issues"` | `"issues"` |
| query_plan | `{"blocked":false,"analysis_type":"issues","aspects":["logistics"],"sentiment":"negative","product_ids":["100013588053","100013588055"]}` | `{"blocked":false,"analysis_type":"issues","aspects":["logistics"],"sentiment":"negative","product_ids":["100013588053"]}` |
| required_tools（主计划） | `["get_top_issues"]` | `["get_top_issues"]` |
| task_acceptance | `{"status":"passed","complete":true}` | `{"status":"passed","complete":true}` |
| 最终 Agent / Worker 状态 | `{"agent":"ok","worker":"succeeded"}` | `{"agent":"ok","worker":"succeeded"}` |

业务查询计数：2 → 2；实际工具：`['get_analysis_context', 'get_top_issues']` → `['get_analysis_context', 'get_top_issues']`。

### G15-T2

输入：不用主题分析，只告诉我物流差评多少条。

| 字段 | 修复前实际 | 修复后实际 |
|---|---|---|
| analysis_state | `{"ranking_basis":"absent","pending_goal":"absent"}` | `{"ranking_basis":"absent","pending_goal":"absent"}` |
| pending_goal | `"absent"` | `"absent"` |
| RequestGoal | `{"original_question":"不用主题分析，只告诉我物流差评多少条。","levels":["statistics"],"aspect":"logistics","sentiment":"negative"}` | `{"original_question":"不用主题分析，只告诉我物流差评多少条。","levels":["statistics"],"aspect":"logistics","sentiment":"negative"}` |
| user_intent | `{"query_type":"aspect_summary","sentiment":"negative","aspect":"logistics","needs_clarification":false,"resolved_product_ids":["100013588053","100013588055"]}` | `{"query_type":"aspect_summary","sentiment":"negative","aspect":"logistics","needs_clarification":false,"resolved_product_ids":["100013588053"]}` |
| analysis_intent（首个确定主计划） | `"issues"` | `"issues"` |
| query_plan | `{"blocked":false,"analysis_type":"issues","aspects":["logistics"],"sentiment":"negative","product_ids":["100013588053","100013588055"]}` | `{"blocked":false,"analysis_type":"issues","aspects":["logistics"],"sentiment":"negative","product_ids":["100013588053"]}` |
| required_tools（主计划） | `["get_top_issues"]` | `["get_top_issues"]` |
| task_acceptance | `{"status":"passed","complete":true}` | `{"status":"passed","complete":true}` |
| 最终 Agent / Worker 状态 | `{"agent":"ok","worker":"succeeded"}` | `{"agent":"ok","worker":"succeeded"}` |

业务查询计数：2 → 2；实际工具：`['get_analysis_context', 'get_top_issues']` → `['get_analysis_context', 'get_top_issues']`。

### G22-T2

输入：不是问数量，我想知道物流差评具体在抱怨什么。

| 字段 | 修复前实际 | 修复后实际 |
|---|---|---|
| analysis_state | `{"ranking_basis":"absent","pending_goal":"absent"}` | `{"ranking_basis":"absent","pending_goal":"absent"}` |
| pending_goal | `"absent"` | `"absent"` |
| RequestGoal | `{"original_question":"不是问数量，我想知道物流差评具体在抱怨什么。","levels":["statistics"],"aspect":"logistics","sentiment":"negative"}` | `{"original_question":"不是问数量，我想知道物流差评具体在抱怨什么。","levels":["themes"],"aspect":"logistics","sentiment":"negative"}` |
| user_intent | `{"query_type":"aspect_summary","sentiment":"negative","aspect":"logistics","needs_clarification":false,"resolved_product_ids":["100013588053"]}` | `{"query_type":"aspect_drilldown","sentiment":"negative","aspect":"logistics","needs_clarification":false,"resolved_product_ids":["100013588053"]}` |
| analysis_intent（首个确定主计划） | `"issues"` | `"aspect_drilldown"` |
| query_plan | `{"blocked":false,"analysis_type":"issues","aspects":["logistics"],"sentiment":"negative","product_ids":["100013588053"]}` | `{"blocked":false,"analysis_type":"aspect_drilldown","aspects":["logistics"],"sentiment":"negative","product_ids":["100013588053"]}` |
| required_tools（主计划） | `["get_top_issues"]` | `["get_aspect_drilldown"]` |
| task_acceptance | `{"status":"passed","complete":true}` | `{"status":"passed","complete":true}` |
| 最终 Agent / Worker 状态 | `{"agent":"ok","worker":"succeeded"}` | `{"agent":"ok","worker":"succeeded"}` |

业务查询计数：3 → 2；实际工具：`['get_analysis_context', 'get_top_issues', 'get_opinion_breakdown']` → `['get_analysis_context', 'get_aspect_drilldown']`。

修复前最终补证计划为 composite（统计 + 原始观点分组），仍不满足 themes；修复后真实执行主题发现与归属复核两次固定响应，完成 themes。

### G23-T2

输入：不要归纳主题了，只统计客服差评数量。

| 字段 | 修复前实际 | 修复后实际 |
|---|---|---|
| analysis_state | `{"ranking_basis":"absent","pending_goal":"absent"}` | `{"ranking_basis":"absent","pending_goal":"absent"}` |
| pending_goal | `"absent"` | `"absent"` |
| RequestGoal | `{"original_question":"不要归纳主题了，只统计客服差评数量。","levels":["themes"],"aspect":"service","sentiment":"negative"}` | `{"original_question":"不要归纳主题了，只统计客服差评数量。","levels":["statistics"],"aspect":"service","sentiment":"negative"}` |
| user_intent | `{"query_type":"aspect_drilldown","sentiment":"negative","aspect":"service","needs_clarification":false,"resolved_product_ids":["100013588053"]}` | `{"query_type":"aspect_summary","sentiment":"negative","aspect":"service","needs_clarification":false,"resolved_product_ids":["100013588053"]}` |
| analysis_intent（首个确定主计划） | `"aspect_drilldown"` | `"issues"` |
| query_plan | `{"blocked":false,"analysis_type":"aspect_drilldown","aspects":["service"],"sentiment":"negative","product_ids":["100013588053"]}` | `{"blocked":false,"analysis_type":"issues","aspects":["service"],"sentiment":"negative","product_ids":["100013588053"]}` |
| required_tools（主计划） | `["get_aspect_drilldown"]` | `["get_top_issues"]` |
| task_acceptance | `{"status":"passed","complete":true}` | `{"status":"passed","complete":true}` |
| 最终 Agent / Worker 状态 | `{"agent":"ok","worker":"succeeded"}` | `{"agent":"ok","worker":"succeeded"}` |

业务查询计数：2 → 2；实际工具：`['get_analysis_context', 'get_aspect_drilldown']` → `['get_analysis_context', 'get_top_issues']`。

### G28-T2

输入：物流差评主要体现在哪里？

| 字段 | 修复前实际 | 修复后实际 |
|---|---|---|
| analysis_state | `{"ranking_basis":"present","pending_goal":"absent"}` | `{"ranking_basis":"present","pending_goal":"absent"}` |
| pending_goal | `"absent"` | `"absent"` |
| RequestGoal | `{"original_question":"物流差评主要体现在哪里？","levels":["themes"],"aspect":"logistics","sentiment":"negative"}` | `{"original_question":"物流差评主要体现在哪里？","levels":["themes"],"aspect":"logistics","sentiment":"negative"}` |
| user_intent | `{"query_type":"aspect_drilldown","sentiment":"negative","aspect":"logistics","needs_clarification":false,"resolved_product_ids":["100013588053"]}` | `{"query_type":"aspect_drilldown","sentiment":"negative","aspect":"logistics","needs_clarification":false,"resolved_product_ids":["100013588053"]}` |
| analysis_intent（首个确定主计划） | `"issues"` | `"issues"` |
| query_plan | `{"blocked":false,"analysis_type":"issues","aspects":["logistics"],"sentiment":"negative","product_ids":["100013588053"]}` | `{"blocked":false,"analysis_type":"issues","aspects":["logistics"],"sentiment":"negative","product_ids":["100013588053"]}` |
| required_tools（主计划） | `["get_top_issues"]` | `["get_top_issues"]` |
| task_acceptance | `{"status":"failed","complete":false}` | `{"status":"failed","complete":false}` |
| 最终 Agent / Worker 状态 | `{"agent":"error","worker":"failed"}` | `{"agent":"error","worker":"failed"}` |

业务查询计数：3 → 0；实际工具：`['get_analysis_context', 'get_top_issues', 'get_opinion_breakdown']` → `[]`。

原故障注入未改：注入统计主计划仍然与主题 Goal 冲突；主计划 required_tools 的不匹配正是预期反例，验收按禁止执行和禁止发布检查。


## 5. G28 的执行时机与查询边界

| 检查 | 修复前 | 修复后 |
|---|---|---|
| 独立 Goal | themes/logistics/negative | 相同，未降低目标 |
| 注入的主计划 | statistics/get_top_issues | 相同故障注入，未移除原反例 |
| task_plan_check | false，themes 能力不满足 | false，原因保留 |
| 冲突后的工具 | overview → get_top_issues → get_opinion_breakdown | **0 次** |
| query_executions | 3（含上下文统计和补证） | **0** |
| 分类/回答模型 | 0 | **0** |
| 补证 | 已进入、扩展计划 | not_started |
| 最终状态 | error / Worker failed，最后才删正文 | error / Worker failed，工具前退出，无正文 |

修复后仍有计划校验前必要的目录读取、只读事务/GUC 设置，用于确认合法商品范围。这不是 get_top_issues、主题分类或补证，不能误称为“完全没有读取数据库目录”，也不计为计划失败后业务查询。主评测中的这些读取为协议替身；无真实数据库。

## 6. 全部 30 组与指标变化

评分代码的原 10 项 REVIEW 保留，分母没有为了提高比例改变：自然模块 50 轮，工具产物 48 轮，端到端 52 轮，预期应完成任务 43 轮，完整可裁决会话 21 组。G28 原来已因最终拒绝发布计防护 PASS，本次没有把它额外算成一个“FAIL 转 PASS”；提前阻断用专项断言独立衡量。

| 指标 | 修复前 | 修复后 | 分母 |
|---|---:|---:|---:|
| State 上下文继承 | 46/50（92.00%） | 49/50（98.00%） | 不变 |
| Intent Router | 43/50（86.00%） | 48/50（96.00%） | 不变 |
| RequestGoal 一致性 | 46/50（92.00%） | 48/50（96.00%） | 不变 |
| Planner | 43/50（86.00%） | 48/50（96.00%） | 不变 |
| 工具产物与目标匹配 | 41/48（85.42%） | 46/48（95.83%） | 不变 |
| Validator 主动故障拦截 | 2/2（100.00%） | 2/2（100.00%） | 不变 |
| 轮级端到端契约 | 42/52（80.77%） | 47/52（90.38%） | 不变 |
| 轮级严格完成 | 34/43（79.07%） | 39/43（90.70%） | 不变 |
| 组级端到端契约 | 12/21（57.14%） | 17/21（80.95%） | 不变 |

| 组 | 修复前 | 修复后 |
|---|---|---|
| G01 | PASS | PASS |
| G02 | PASS | PASS |
| G03 | PASS | PASS |
| G04 | PASS | PASS |
| G05 | FAIL | FAIL |
| G06 | PASS | PASS |
| G07 | PASS | PASS |
| G08 | PASS | PASS |
| G09 | FAIL | FAIL |
| G10 | PENDING_HUMAN | PENDING_HUMAN |
| G11 | PENDING_HUMAN | PENDING_HUMAN |
| G12 | PENDING_HUMAN | PENDING_HUMAN |
| G13 | FAIL | PASS |
| G14 | FAIL | PASS |
| G15 | FAIL | PASS |
| G16 | PASS | PASS |
| G17 | PENDING_HUMAN | PENDING_HUMAN |
| G18 | PENDING_HUMAN | PENDING_HUMAN |
| G19 | PENDING_HUMAN | PENDING_HUMAN |
| G20 | PENDING_HUMAN | PENDING_HUMAN |
| G21 | PASS | PASS |
| G22 | FAIL | PASS |
| G23 | FAIL | PASS |
| G24 | PENDING_HUMAN | PENDING_HUMAN |
| G25 | FAIL | FAIL |
| G26 | FAIL | FAIL |
| G27 | PASS | PASS |
| G28 | PASS | PASS |
| G29 | PASS | PASS |
| G30 | PENDING_HUMAN | PENDING_HUMAN |

## 7. 专项与既有兼容回归

主评测始终使用原纯内存任务/数据边界、固定供应商响应；没有为 G13/G14/G15/G22/G23/G28 增加数据替身特判来生成结果。

25 项新增专项覆盖：三种失败后 SKU 承接；不继承失败事实；当前文字 SKU、页面 SKU、当前日期与页面日期优先；失败轮显式日期的保留/越界拒绝；跨会话/标签身份失效；无效保存范围；未知商品冲突；独立 Goal 与 Router 两方向真值；不统一能力优先级；真实 Worker 两方向执行；G28 零工具/零补证；确认查询前缀同样不能绕过；最终 Validator 仍拒绝；G01～04 和来源篡改防线保持。

兼容回归复用原 450 项业务目标，增加 P1-A/F01、P6-A/B/C/D、E1A SDK MockTransport 以及评测基础设施/本轮专项。沿用其**临时合成 SQLite 夹具**，连接仅允许本轮回归目录的 `synthetic-temp`，不访问现有业务库；所有 PostgreSQL 连接、外部网络及 PostgreSQL 程序硬阻断。本轮并未将 SQLite 当作 PostgreSQL 集成证明。

首次完整兼容运行 `regression-01` 为 **628 项、6 failure、2 error、0 skip**，原日志和摘要完整保留。其问题没有隐去：一项只是原“确认切换”的提示契约需保持；其余是旧补证测试从“内容目标 + 统计主计划”这一已知冲突出发，原来要求进入补证。按本轮新要求，不能为了旧阶段期望而放行 false 计划检查。

因此对两份兼容测试作了必要且显式的更新：

1. 原“主题不能只用统计完成”的反例仍是相同主题/统计冲突，仍要求 error 和 complete=false；将阶段检查从“补证 completed”加强为 not_started、task_plan_check=false、query_executions=0。另增确认查询前缀的同类零工具反例，防止按前缀绕过安全门。
2. 原合法补证测试的初始请求明确为“客服/包装差评有多少条，具体如何解读？”，与已确认统计主计划一致；额外要求主计划检查 passed。旧补证次数、原文支持、预算耗尽、工具失败、引用损坏、样本连续性和无外发断言继续保留。删除了旧 helper 将一个不一致问句静默转成统计查询的特判。
3. 原两商品比较、趋势子窗口等本就合法的补证测试未改输入或断言。聚焦兼容复验为 **58/58 PASS**。原冻结 30 组的任何输入、预期或评分规则都没有改。

这是测试起点与新安全契约的对齐，不是把一个 false 检查改成 true、删除故障用例或放宽 Validator。不能把最终兼容运行表述为“628 项原输入、原时机断言逐字未变”。

| 验证 | 最终结果 |
|---|---|
| 原始固定 30 组 / 62 轮 | 47 PASS、5 FAIL、10 PENDING_HUMAN；原 42 个 PASS 无退化 |
| 新增专项 | **25/25 PASS**，含单 SKU 的 D=8、客服 M=5/物流 M=3 或 0 的实际数量断言，排除仅修改 scope 标签而继续合并统计 |
| 聚焦兼容验证 | **58/58 PASS**（补证、内容、商品绑定与本轮专项） |
| 最终完整适用回归 | **629/629 PASS**，290.550 秒，0 failure/error/skip |
| 原 450 项业务目标 | 全部包含于最终回归；两份补证测试的夹具/阶段检查按上文对齐，未声称原样逐字不变 |
| test_trusted_analysis_state | 18/18 PASS |
| test_p1a_independent_validation | 11/11 PASS |
| test_p1a_f01 | 13/13 PASS |
| test_p6a_contract | 19/19 PASS |
| test_p6b_planner_snapshot | 19/19 PASS |
| test_p6c_budget_execution | 25/25 PASS |
| test_p6d_publication | 19/19 PASS |
| test_p6e1a_provider_contract | 39/39 PASS |
| test_agent_stage1_evaluation | 9/9 PASS |
| 旧测试 ID | 首次 628 个全部保留，另增 1 个确认查询前缀反例；没有删除测试或使用 skip/expectedFailure |
| 主评测隔离 | 0 数据库连接/写入，64 个协议连接替身全部关闭，7 个预期澄清轮的 query_executions 均为 0 |
| 回归数据隔离 | 13715 次连接全部限定于独立合成临时目录；既有业务库、真实 PostgreSQL 连接为 0 |
| 模型边界 | 真实收费模型和真实 Embedding 为 0；主评测 53 次固定响应尝试，外部依赖构造拦截从旧 G22 的 1 次降为 0 次 |
| PG-01～08、数据库恢复、生产部署 | NOT EXECUTED；P6-E1B BLOCKED 不变 |

完整最终测试 ID、逐模块数量和零外部连接尝试记录见 [regression-summary.final.json](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-p0p1/regression-summary.final.json)；完整日志 `.cache/agent-stage1-p0p1/regression-02/tests.txt`。最终加强单商品实际计数断言后，另独立重跑专项 25/25，通过记录在 `special-tests-final.txt`；该断言加强没有业务代码变化，未将重复测试计入 629。

## 8. 原 10 轮待人工裁决、剩余问题与准入结论

以下原 10 轮仍是 **PENDING_HUMAN**，没有改成 PASS，也没有修改原预期：

- G10-T2、G11-T2、G12-T1、G12-T2：无可信历史/身份/唯一层次的预期存在语义或范围约束问题。
- G17-T2、G18-T2、G19-T2：原 oracle 的 partial 状态字段与已冻结业务状态契约不一致。
- G20-T2：原 not_completed 验收状态不是既有状态枚举。
- G24-T2：RAG 后“客服呢？”的层次与 Goal 预期需裁决。
- G30-T3：待完成主题后的“那好评呢？”业务层次待裁决。

范围修复使 G12-T2 的实际商品由页面两商品改为用户此前明确的 053；这是 P0-01 通用条件规则的结果，原模糊预期与评分标记仍保留。没有据此裁决该轮。其余待裁决轮的状态映射及关键安全约束继续记录，不以模型或真实数据库回填标准。

剩余 5 个 FAIL 与修复前相同，不在本轮扩展修复：

| 轮次 | 未修复原因 |
|---|---|
| G05-T2 | 更换维度时主题层次承接仍不足，保持原 FAIL |
| G09-T2 | 日期选择变化后的 pending_goal 投影仍丢失，保持原 FAIL |
| G25-T1 | 带 SKU 的 RAG 命题许可不足；用户明确禁止本轮修复，冻结 RAG 不动 |
| G25-T2 | RAG 后明确统计请求被旧追问屏障挡住，保持原 FAIL |
| G26-T2 | 数量追问的独立 RequestGoal 仍丢 aspect，保持原 FAIL |

没有观察到原通过项新增 P0/P1 回归；这只覆盖本轮离线用例及列明的回归，不能证明未测试输入绝无缺陷。真实主题语义质量、实时数据覆盖、真实 PostgreSQL 事务一致性、供应商账单/取消等仍未验证。

**进入下一阶段的结论：可以进入后续业务验收及人工预期裁决；不能据此宣布完整 Agent 业务验收 PASS，更不允许进入真实 PostgreSQL 恢复、收费模型或生产部署。** 本轮三项修复完成后停止，没有自动实施其余 P1 或新建基础设施阶段。

## 9. 复验命令与证据保护

在仓库根目录执行；源码修订清单固定为 v3，后续代码变化必须重新审阅并另建版本，不能改原 oracle/manifest：

```powershell
.\.venv\Scripts\python.exe tests/agent_stage1_multiturn_evaluation.py --source-revision docs/evaluation/agent-stage1-p0p1/source-revision.v3.json --output-dir .cache/agent-stage1-p0p1
.\.venv\Scripts\python.exe scripts/summarize_agent_stage1_evaluation.py .cache/agent-stage1-p0p1/run-03.json
.\.venv\Scripts\python.exe scripts/check_agent_stage1_p0p1_regression.py --source-revision docs/evaluation/agent-stage1-p0p1/source-revision.v3.json --targets docs/evaluation/agent-stage1-p0p1/regression-targets.v1.json --output-dir .cache/agent-stage1-p0p1/regression-new
```

新运行自动分配独立 run-NN；汇总和回归输出采用排他创建，已有结果不会覆盖。第二条对本轮既有评分文件再次执行会安全拒绝覆盖，应对第一条新产生的 run 文件汇总。不要把本轮源码修订清单理解为旧数据库恢复计划获得批准。

最终完整性复核：49 个预先保存的冻结文件、旧评测证据、P6-E1B 报告/安全脚本/授权与恢复材料摘要一致；独立 `validate_goal_plan`、`validate_task_completion` 函数 AST 与修复前相同。新增产物摘要见同目录 `artifact-sha256.final.json`。
