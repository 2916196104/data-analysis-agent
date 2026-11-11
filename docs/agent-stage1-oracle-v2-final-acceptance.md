# Agent 第一阶段：Oracle v2 正式冻结与独立验收

日期：2026-11-11；时区：Asia/Taipei（UTC+08:00）。源码：既有 **source-revision.v4**，本轮未修改 Agent。

**Oracle v2 冻结完成；业务独立验收 FAIL：30 组、62 轮实际执行，59 PASS、3 FAIL、0 PENDING_HUMAN。** 原 51 轮 PASS 全部保持通过；G25-T1 仍 FAIL。正式选 A 后，G24-T2 与 G30-T3 暴露两项已确认的业务偏差，不能迎合源码改选 B。收费模型、真实 Embedding、真实数据库连接均为 0。P6-E1B 继续 BLOCKED，PG-01～08 NOT EXECUTED。

## 1. 正式人工裁决及一致性

本轮用户明确重申 **“10轮全部选 A”**，并列出十个用例，作为业务语义与评测预期的正式依据。未捏造其他消息编号、真实身份、签名或历史批准时间；不包含业务代码修改、数据库操作、费用授权或部署许可。

[独立裁决记录](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-human-adjudication.v1.json)生成于 **2026-11-11T12:15:40.889627+08:00**，记录版本、用户原话、权限边界、原候选文件及工作表摘要；每项完整保存选 A 的规则、理由、风险及所有未选择的 B/C。候选快照中的“待选定”文字作为历史原文保留，正式选择由独立记录的 `selected=A` 确认，不回写原工作表或 pending 草案。

| 用例 | 正式选择 A 的业务规则与理由 | 本次结果 |
|---|---|---|
| G10-T2 | 标注身份已变；旧排名及单商品事实不可恢复。只保留主题层次线索，商品/情感未唯一确认，先澄清。 | PASS |
| G11-T2 | 新会话不继承旧会话条件或排名；按当前页面合法范围澄清，不默认 negative 或单 SKU。 | PASS |
| G12-T1 | 当前原文明确 053，可以绑定商品；主题意图部分明确，情感/方向不足，先澄清。 | PASS |
| G12-T2 | 同会话继承用户明确的 053；前轮未完成不抹除合法用户条件，也不能生成排名或情感事实。 | PASS |
| G17-T2 | effectiveness 失败，其余两维独立通过；完整三维目标保留，父级 partial/false。 | PASS |
| G18-T2 | service 失败，其余两维独立通过；partial 不能写成 failed 或完整成功。 | PASS |
| G19-T2 | logistics 失败，其余两维独立通过；成功子集不替代三维原目标。 | PASS |
| G20-T2 | 当前统计与历史排名不一致；stale_ranking 在准备阶段阻断，保留计划目标，不预读、不分类、不重选。 | PASS |
| G24-T2 | RAG 后“客服呢？”只确认 service；统计、主题、事实命题均未唯一确定，Goal 暂不冻结。 | **FAIL** |
| G30-T3 | 053、positive 已知，但统计与主题两种承接层次都合理；先澄清并保留原 negative/themes 待办。 | **FAIL** |

十项 A 的候选规则未发现需要停止冻结的内部矛盾：

- G10/G11/G12 的部分 Goal 不是可执行授权。`sentiment=null`，`needs_clarification=true`，无主计划、无工具、无分类，acceptance 可缺省或显式 failed/false，不能 complete=true。
- G17/G18/G19 的子维度失败与父级 partial 并不矛盾：只有成功且证据通过的维度发布，父级 complete=false。
- G20 的“计划覆盖三维”和“准备阶段发现过期而不执行”属于不同阶段；不要求不存在的主题产物。
- G30 的 positive 是当前已知澄清条件；保留的 negative/themes 是原未完成待办，不改写成 positive 已完成。沿用现有 awaiting_aspect 历史状态，不新增 API 枚举。

## 2. Oracle v2 冻结及差异审计

正式文件：[oracle.v2.json](E:/AllProject/data-analysis-agent/tests/fixtures/agent_stage1/oracle.v2.json)。生成器只读取 v1 与人工候选，不导入 Agent、不读取本轮执行结果：[freeze_agent_stage1_oracle_v2.py](E:/AllProject/data-analysis-agent/scripts/freeze_agent_stage1_oracle_v2.py)。

| 核验项 | 实际结论 |
|---|---|
| 对话与用户输入 | 30 组、62 轮不变；逐轮输入、selection_changes、conversation_override、parent_override、data_mode、fault、kind 相同 |
| 原 52 轮非待裁决对象 | **逐对象完全相同**，包括原 51 PASS 与 G25-T1 FAIL 的独立预期 |
| 原 10 轮待裁决对象 | 仅按选 A 更新；所选 proposed_expectations 逐字段相同；增加正式裁决及范围审查契约 |
| 正式预期 | 覆盖九字段、effective_scope，以及澄清/过期/计划冲突的工具前阻断与分类次数 |
| 旧版本 | v1、corpus、原 freeze-manifest、v2.pending、source-revision.v3/v4、旧轨迹/评分/失败证据全部保留 |
| 既有文件保全 | 本轮预先保存的 **462 个文件**摘要相同；v4 的 **88 个生产源码文件**摘要相同 |

冻结摘要：

| 对象 | SHA-256 |
|---|---|
| Oracle v2 | `81306475371fd769b61f8132cf97dbc8dcdc82975a67a0982018575c08bed198` |
| 人工裁决记录 | `97b38e01d204adfa2b3d2177a4bc1f7ee6d6026b9523242d6fe98ae825b12643` |
| 初始 freeze-manifest.v2 | `c11073affadd71a1c860406d58806f3932e9b4b1d4094baadc6dc90d29125eb8` |
| 正式评分合同 v2.1 | `4b38505dbfd2b4e1750631f928eb13c020c9b510a488a4e510cd187dcd460711` |
| 补充 freeze-manifest.v2.1 | `dc9310c681c2054a2db1db75eb9355f087d1a04e83f7172b49443fcfa055af4d` |

完整差异、原 51 PASS 清单、逐项人工结果和保全检查：[diff-and-preservation-audit.json](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-oracle-v2/diff-and-preservation-audit.json)。摘要绑定：[初始冻结清单](E:/AllProject/data-analysis-agent/tests/fixtures/agent_stage1/freeze-manifest.v2.json)、[补充评分器冻结清单](E:/AllProject/data-analysis-agent/tests/fixtures/agent_stage1/freeze-manifest.v2.1.json)。

## 3. 独立评分规则、测试与执行顺序

原 [v1 评分器](E:/AllProject/data-analysis-agent/scripts/summarize_agent_stage1_evaluation.py)及其历史结果未修改。新增纯函数评分器，不按 Gxx ID 强制 PASS；规则由冻结预期与执行阶段驱动，Agent 当前输出不参与预期生成。

| 合法表示/执行阶段 | v2 匹配规则 |
|---|---|
| task_acceptance=null | 仅在已批准的缺省/显式失败澄清表示中接受；complete=true 一律拒绝 |
| 部分 Goal | 允许 themes 与未知 sentiment；不能把原始路由 hint 视为已确认用户条件 |
| 无最终计划 | 审查投影为 not_finalized；原始 analysis_intent、sentiment_intent 和 query_plan 原样保存 |
| partial | 完整 required_aspects、逐维状态、允许发布维度及计数/引用复核，父级 partial/false |
| stale_ranking | 独立检查计划覆盖和准备阶段阻断；允许一次当前统计校验，禁止观点预读、分类、主题发布 |
| 上下文读取 | 目录、事务设置等必要读取不计为业务工具；按 Registry、analytics、检索、分类事件独立核对 |
| 待办恢复 | 关联同会话、身份、有效范围及来源完整性；G30 应保留原待办 goal/scope/source，不能只自报 awaiting_aspect |
| 故障注入 | 计划篡改和结果篡改保留原始字段差异，单独检验阻断/拒绝；不伪装成自然规划准确率 |
| RAG | 沿用冻结独立命题许可，候选检索成功不能代替 permitted_keys 或事实完成 |

时间顺序及保留的评分器缺陷：

1. **12:15:40.889627+08:00**：冻结正式 Oracle、初始评分合同与代码摘要。初始独立单测 **34/34 PASS**。
2. **12:16:34.038693～12:16:36.160608+08:00**：首轮真实业务链执行，初始评分 **58 PASS / 4 FAIL**；原始文件完整保留。
3. 首轮多出 G09-T2：合同已允许澄清时的已知条件放在部分 Goal，但评分器实现错误地要求 Router 重复 sentiment。实际 Goal 已保留 negative、Router 已澄清、执行为 0；这是评分实现缺陷，不是业务修复或新增人工选择。
4. 新建版本化适配器 [agent_stage1_v2_1_scoring.py](E:/AllProject/data-analysis-agent/scripts/agent_stage1_v2_1_scoring.py)，保留初始实现和全部结果。仅当 Intent 字段缺失时，读取已单独匹配的部分 Goal；Intent 明确冲突仍拒绝。未修改 Oracle、分母或阶段规则，未写案例 ID 豁免。
5. 修正版继承原 34 项测试并增加 3 项边界：合法缺省表示、明确冲突拒绝、null Goal 不能补造条件。两版合计 **71/71 PASS**（34 原版 + 37 修正版），冻结后再次执行仍 **71/71 PASS**。
6. **12:20:47.199316+08:00**：冻结 v2.1 评分代码及原始清单引用。**12:20:54.261684～12:20:56.315119+08:00**：重新执行全量业务链，正式结果 **59 PASS / 3 FAIL**。两次执行均晚于各自绑定冻结时间。

修正版摘要：`d2da305cac79da9288313d686ac9f36fad610c43e58fdeecd06b3994eb93b5ca`；入口 [run_agent_stage1_oracle_v2_1.py](E:/AllProject/data-analysis-agent/scripts/run_agent_stage1_oracle_v2_1.py) 摘要：`39ce24f930cd9f8bb43b5db088c98e1cc7e9e5123d02dea060bfcdd707ca8beb`。

测试覆盖 null/部分 Goal、partial 发布、stale 无产物、分类/检索越权、假完成、待办来源及生命周期、篡改范围/引用/计数、ID 无关匹配等。未使用 skip、expectedFailure 或放宽既有断言。日志：[冻结前](E:/AllProject/data-analysis-agent/.cache/agent-stage1-oracle-v2/matcher-v1.1-tests-before-freeze.txt)、[冻结后](E:/AllProject/data-analysis-agent/.cache/agent-stage1-oracle-v2/matcher-tests-after-freeze.txt)。

## 4. 执行边界与证据

复用未修改的 [既有 Harness](E:/AllProject/data-analysis-agent/tests/agent_stage1_multiturn_evaluation.py:338)：真实 TaskWorker 调度、State 加载/失效、独立 Goal、Router、Planner、Tools、主题程序、Validator、P6 发布逻辑执行。评分前替换的只是正式 Oracle 引用，未修改 data double、固定模型响应、故障注入或查询记录逻辑。

任务库使用内存替身；目录与事务是 MemoryReadConnection 协议替身；analytics 使用既有合成 DTO；检索使用 FakeEncoder/FakeRepository/FakeSnapshot；分类使用固定响应及合成费用账本。`backend=postgresql`、快照 ID 等 DTO 诊断字段不是实际 PostgreSQL 运行证明。053 的向量覆盖缺口继续用原 rag_gap 夹具表现，不能归因于 Router，也不能补造检索证据。

真实连接、OpenAI 构造、真实 Embedding、网络与子进程入口有既有硬阻断。本轮禁止调用尝试 **0**；连接替身 **64/64 已关闭**；真实 PostgreSQL/业务数据库操作、收费模型、真实 Embedding 均 **0**。工具计数中的分类尝试全是固定模拟响应，不代表真实模型质量。

正式逐轮证据：

- [62 轮完整预期、审查字段与原始执行链 JSONL](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-oracle-v2/turns.v2.1.jsonl)：包含输入、前后 State、Goal、Router、原始计划、工具事件、Validator、Worker/Agent 状态和首个偏离节点。
- [正式评分](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-oracle-v2/assessment.v2.1.json)、[三项完整失败轨迹](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-oracle-v2/failures.v2.1.json)、[汇总](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-oracle-v2/summary.json)。
- [首轮原始运行](E:/AllProject/data-analysis-agent/.cache/agent-stage1-oracle-v2/run-01.json)、[首轮原始评分](E:/AllProject/data-analysis-agent/.cache/agent-stage1-oracle-v2/assessment-01.json)；[正式第二轮原始运行](E:/AllProject/data-analysis-agent/.cache/agent-stage1-oracle-v2/run-02.json)。

本轮运行 71 项评分测试与 62 轮业务评测；未宣称重新执行此前整套后端兼容回归。原 v4 源码未变且 51 个既有通过用例复验不退化，不等于真实数据库、供应商或模型语义验收通过。

## 5. 30 组、62 轮结果

| 对话组 | 逐轮结果 | 对话契约 |
|---|---|---|
| G01 | T1 PASS；T2 PASS | PASS |
| G02 | T1 PASS；T2 PASS | PASS |
| G03 | T1 PASS；T2 PASS | PASS |
| G04 | T1 PASS；T2 PASS | PASS |
| G05 | T1 PASS；T2 PASS | PASS |
| G06 | T1 PASS；T2 PASS | PASS |
| G07 | T1 PASS；T2 PASS | PASS |
| G08 | T1 PASS；T2 PASS | PASS |
| G09 | T1 PASS；T2 PASS | PASS |
| G10 | T1 PASS；T2 PASS | PASS |
| G11 | T1 PASS；T2 PASS | PASS |
| G12 | T1 PASS；T2 PASS | PASS |
| G13 | T1 PASS；T2 PASS | PASS |
| G14 | T1 PASS；T2 PASS | PASS |
| G15 | T1 PASS；T2 PASS | PASS |
| G16 | T1 PASS；T2 PASS | PASS |
| G17 | T1 PASS；T2 PASS；T3 PASS | PASS |
| G18 | T1 PASS；T2 PASS | PASS |
| G19 | T1 PASS；T2 PASS | PASS |
| G20 | T1 PASS；T2 PASS | PASS |
| G21 | T1 PASS；T2 PASS | PASS |
| G22 | T1 PASS；T2 PASS | PASS |
| G23 | T1 PASS；T2 PASS | PASS |
| G24 | T1 PASS；**T2 FAIL** | FAIL |
| G25 | **T1 FAIL**；T2 PASS | FAIL |
| G26 | T1 PASS；T2 PASS | PASS |
| G27 | T1 PASS；T2 PASS | PASS |
| G28 | T1 PASS；T2 PASS（计划冲突正确拒绝） | PASS |
| G29 | T1 PASS（篡改结果正确拒绝）；T2 PASS | PASS |
| G30 | T1 PASS；T2 PASS；**T3 FAIL** | FAIL |

G17 和 G30 各有三轮，其余各两轮，合计 62 轮；完整输入/轮序见 JSONL。

### 5.1 模块指标及 v1/v2 口径

自然输入模块统计排除两个明确注入故障的轮次，分母为 60；故障拒绝另外统计。所有 62 轮均进入总体验收，没有待人工裁决排除。

| 指标 | v1 历史 v4 | 本次正式 v2 |
|---|---|---|
| State 上下文/范围/待办 | 50/50，100% | **59/60，98.33%** |
| Intent Router | 50/50，100% | **59/60，98.33%** |
| RequestGoal 一致性 | 50/50，100% | **58/60，96.67%** |
| Planner | 50/50，100% | **59/60，98.33%** |
| 工具阶段与目标匹配 | v1 工具目标 48/48，100% | **58/60，96.67%** |
| Validator 注入错误拦截 | 2/2，100% | **2/2，100%** |
| 工具前阻断正确率 | 原报告未单列 | **9/10，90%** |
| 严格完整任务完成率 | 42/43，97.67% | **42/43，97.67%** |
| 逐轮端到端契约 | 51/52，98.08% | **59/62，95.16%** |
| 多轮对话端到端契约 | 20/21，95.24% | **27/30，90%** |

v1 排除 10 轮待裁决及其未裁决对话；v2 新增这 10 轮的正式判定（8 PASS、2 FAIL）。工具指标还增加了准备阶段阻断与独立 RAG 命题许可检查，不能把两个百分比直接解释成 Agent 质量下降或上升。严格完整完成率沿用 43 个已确认完整任务分母；澄清、partial 和过期阻断被正确处理可算“契约 PASS”，不算分析任务完整完成。G30 的错误 complete=true 已在 62 轮契约及阻断指标中记 FAIL，不作为新增完整任务成功。

原 51 轮 PASS 保留 **51/51**；没有业务代码变更造成的新回归。新增两项 FAIL 是正式 A 对此前未裁决行为的业务确认，不是修改预期以消除失败。

## 6. 三项 FAIL 的九字段与首个偏离节点

共同范围：均使用冻结的 source/dataset/标注身份和当前 2023-02-01～2023-02-28 窗口；G24/G30 为商品 053，G25 为 055。本次三项范围匹配均通过。九字段的完整对象、原始 raw 字段和工具结果见失败轨迹，不以回答文本代替比较。

| 字段 | G24-T2 | G25-T1 | G30-T3 |
|---|---|---|---|
| analysis_state（输入） | 无 ranking/pending，符合 | 无 ranking/pending，符合 | 有可信 ranking 与旧 pending，符合 |
| pending_goal（轮后） | absent，符合 | absent，符合 | 应保留旧 awaiting_aspect；实际 absent，**不符** |
| RequestGoal | 应 null；实际 statistics/service/null，**不符** | 独立 RAG 路径 null，符合 | 应 null；实际 themes/不限维度/positive，**不符** |
| user_intent | needs_clarification=true；service，符合所选字段 | evidence，符合 | 应澄清；实际 aspect_drilldown、false，**不符** |
| analysis_intent | not_finalized 投影；raw=null，符合 | semantic_evidence，符合 | 应 not_finalized；实际 aspect_drilldown，**不符** |
| query_plan | 无最终可执行计划，符合 | semantic_evidence，符合 | 应阻断；实际 positive drilldown，**不符** |
| required_tools | []，符合 | search_semantic_evidence，符合 | 应 []；实际 get_aspect_drilldown，**不符** |
| task_acceptance | null，合法澄清表示 | 原独立预期需 permitted_evidence=true；实际 false，**不符** | 应缺省/failed 且禁止 complete；实际 passed/true，**不符** |
| final_status | unsupported / Worker succeeded，符合 | ok / Worker succeeded 是检索流程终态，符合旧字段，但不能证明命题完成 | 应 unsupported / Worker succeeded；实际 ok / succeeded，**不符** |

### 6.1 G24-T2：提前冻结未确认层次（P1）

历史：“100013588053有没有物流很慢的反馈？” → 本轮：“客服呢？”。首个偏离为 **RequestGoal**。

[build_request_goal](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_acceptance.py:102)在无主题/内容/原文关键词时回退 statistics；service 仅确认维度，却已生成统计 Goal。它发生在 [Router 之前](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/reviews.py:183)。Router 随后因 [旧 semantic_evidence 省略追问](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_intent.py:207)正确提出澄清，所以本轮业务查询、Registry、向量检索和分类均 **0**，没有发布客服事实或 complete=true。

最小后续建议：对层次未确认的 RAG 短维度追问保持 Goal 未冻结；保留维度和商品条件。不得扩大检索事实许可。本轮没有实施。

### 6.2 G25-T1：RAG 命题许可仍不支持带 SKU 原句（既有 P1）

“100013588055有没有物流很慢的反馈？”。首个偏离为 **工具产物与目标匹配**，随后独立事实完成验收不通过。检索返回合成候选，但 `assessment.reason=query_semantics_uncovered`、`permitted_keys=[]`。

[target](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_reported_facts.py:54)用整句 fullmatch，不能接受前置 SKU；[evidence_question](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_task_contract.py:18)只剥离明确日期前缀，SKU 保留在命题字符串。于是路由/商品范围正确、候选存在，仍无法授予原问题命题许可。[assess](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_reported_facts.py:124)按冻结安全契约拒绝确定结论，回答明确说明当前证据不能确认。

本轮业务查询 **2**（上下文统计、语义检索），固定检索 **1**，分类 **0**；无虚构事实，普通 task_acceptance 为 null，不是 complete=true。该 FAIL 是业务能力缺口，不能将“安全拒绝”改写成原事实任务完成，也不能归因于 053 向量缺口。未来需另行授权审查范围/命题分离，不能放宽事实蕴含规则。

### 6.3 G30-T3：歧义追问被自动执行并错误完成（P0，含待办丢失 P1）

历史负面排名 → 未完成的“具体体现在哪里？”主题目标 → “那好评呢？”。首个偏离为 **RequestGoal**。

[build_request_goal 的待办承接](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_acceptance.py:117)与 [reference 层次继承](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_acceptance.py:137)把新句直接定为 themes；[pending_theme_ref](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_intent.py:229)把 short_sentiment 直接导向主题。两者一致仍不代表符合人工 A：统计/主题层次尚未唯一确定，应先澄清。

实际执行 `get_analysis_context`、`get_aspect_drilldown`，业务查询 **2**、固定模拟分类尝试 **2**，无澄清。现有 [validate_task_completion](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_acceptance.py:184)验收了被错误冻结的 themes 产物，返回 `passed/complete=true`；Agent ok、Worker succeeded，轮后旧 pending absent。回答声称“二级主题 complete”。技术产物有校验，不足以证明选中了用户确认的任务。

最小后续优先修复：在短情感追问且统计/旧待办主题存在竞争承接层次时，Goal 不冻结、Router 先问统计还是具体表现、工具前执行为 0；严格保留旧 negative/themes 未完成待办。需要分别验证独立 Goal 与 Router，不能仅比较两者相等。本轮不修复。

## 7. 已确认通过的业务能力及边界

- G01～G04 普通排名、下钻及情感追问路径保持通过；G05/G09/G25-T2/G26 的已修普通 P1、G13～15 商品条件继承、G22/G23 否定纠正、G28 计划冲突工具前阻断均保持通过。
- G10/G11/G12 共四轮无主计划，业务查询/检索/分类均 0；只依据当前合法条件保留部分 Goal，跨身份/会话排名不恢复。raw 路由诊断仍出现 aspect_drilldown/negative，已原样保存；因 Goal sentiment=null、Router 需澄清且无执行，不把该 hint 解释为用户确认的 negative，也不擦除它来取得 PASS。
- G17：service、logistics 发布，effectiveness failed；G18：effectiveness、logistics 发布，service failed；G19：service、effectiveness 发布，logistics failed。三轮均保留 service/effectiveness/logistics 完整目标，子状态及父 partial/false、Worker partial 相符。
- G20：旧 D=8，当前 D=9 且六维计数出现变化，准备结果 stale_ranking。仅当前统计校验 **1**，观点预读 **0**、分类 **0**；计划中的三维未被偷偷替换，不伪造主题工具结果。返回 unsupported、Worker succeeded 是受控阻断，不等于主题分析完成或整个服务异常。
- 注入错误的 G28-T2 与 G29-T1 均正确拒绝；2/2 说明既定反例被拦截，不是所有可能错误都已证明可拦截。

当前不能宣布“无错误目标 complete=true”：**G30-T3 已按人工 A 明确证实存在这一 P0**。也不能把自动单测通过等同于真实模型语义准确率、实际样本覆盖率或 PostgreSQL 并发一致性。

## 8. 后续最小修复优先级与停止结论

1. **P0：G30-T3**，在冻结 Goal 和工具执行前处理短情感追问的层次歧义，并保留原未完成待办；不得削弱最终 Validator。
2. **P1：G24-T2**，层次未定时不得先默认冻结 statistics，维度条件可保留。
3. **既有 P1：G25-T1**，独立评审带 SKU 的范围/命题分离；保持冻结 RAG 许可及原 FAIL，不在本阶段修改。

| 交付/准入项 | 最终状态 |
|---|---|
| 十项人工裁决记录 | 完成；全部 A，未虚构身份/历史时间/额外授权 |
| Oracle v2 冻结、旧预期/证据保全 | **PASS** |
| 独立评分器测试 | **71/71 PASS**，初版缺陷及两次运行完整保留 |
| 全量 30 组、62 轮业务执行 | 完成：**59 PASS / 3 FAIL / 0 PENDING** |
| Agent 第一阶段整体业务验收 | **FAIL**，存在 G30 错误目标完成与两项 P1 |
| P6-E1B | **BLOCKED**；PG-01～08 **NOT EXECUTED** |

本轮不修改 Agent、v1、pending 草案、旧评分与失败证据、冻结 RAG 人工资产、P6-E1B 安全门或授权。没有自动开启修复、真实模型、数据库恢复或部署阶段。已完成本轮冻结和独立评测，停止。

复验入口（产生新的 run-NN，不覆盖旧结果）：

```powershell
$env:PYTHONPATH='tests;scripts'
.\.venv\Scripts\python.exe -m unittest test_agent_stage1_v2_scoring test_agent_stage1_v2_1_scoring -v
.\.venv\Scripts\python.exe scripts/run_agent_stage1_oracle_v2_1.py
```

其入口先校验原始及补充冻结清单、v4 生产源码，再运行既有带硬阻断的离线 Harness。执行证据摘要见 [artifact-sha256.json](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-oracle-v2/artifact-sha256.json)；包含本报告、冻结后测试日志与最终核验的清单见 [final-artifact-sha256.json](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-oracle-v2/final-artifact-sha256.json)。
