# Agent 第一阶段正式交付与封存

交付日期：2026-11-11（Asia/Taipei）。项目：`E:\AllProject\data-analysis-agent`。

**封存结论：第一阶段固定合成数据集上的离线业务契约验收 PASS，正式停在 source-revision.v6。真实模型语义质量、当前真实向量覆盖和真实 PostgreSQL 集成不在此结论内；P6-E1B 继续 BLOCKED。** 本文归档既有结果，不新增验收阶段、不扩充测试集、不重新运行模型、数据库或整套回归。

## 1. 最终依据和结果口径

最终结果以 [G25 最终验收](agent-stage1-g25-rag-final-acceptance.md)及其冻结产物为准。早期报告中的 FAIL、PENDING 和兼容错误保留为历史，不能覆盖、删除或与最终结果混算。

| 项目 | 已有冻结结果 | 原始证据 |
|---|---|---|
| Oracle v2 业务契约 | 30 组、62 轮，**62 PASS / 0 FAIL / 0 PENDING** | [正式评分](evaluation/agent-stage1-g25/oracle-v2-assessment.json)、[逐轮调用链](evaluation/agent-stage1-g25/oracle-v2-turns.jsonl) |
| RAG 回归 | **143/143 PASS**，0 failure/error/skip | [RAG 结果及测试 ID](evaluation/agent-stage1-g25/rag-regression.json) |
| 适用后端兼容回归 | **762/762 PASS**，0 failure/error/skip；原 743 个 ID 保留 | [兼容结果及测试 ID](evaluation/agent-stage1-g25/full-regression.json) |
| 自然输入模块匹配 | State、Router、RequestGoal、Planner、工具阶段/目标分别 60/60 | [最终汇总](evaluation/agent-stage1-g25/summary.json) |
| 工具前阻断 / 注入错误拦截 | 10/10、2/2 | 同上；仅指固定边界，不是全部错误检出率 |
| 严格完整任务 / 整组对话契约 | 43/43、30/30 | 同上；澄清、partial、stale 的契约通过不等于完整分析完成 |

143 项 RAG 与 762 项兼容存在覆盖重合，不能相加为 905 个独立业务样本。62 轮也不是 62 个真实评论问题。最终 Oracle 评分记录时间为 2026-11-11 13:44:43（UTC+08:00），绑定原运行 `.cache/agent-stage1-g25/after/run-02.json`；不是本文重新执行的结果。

原运行使用真实 Worker、业务规则和校验代码，但任务存储、目录/事务协议、analytics 数据、检索编码/仓储、主题响应及费用账本使用合成替身或固定响应。兼容测试的 13,960 次 SQLite 连接限于合成临时目录；Oracle 的 64 个协议连接替身均已关闭。最终汇总记载真实 PostgreSQL、收费模型和真实 Embedding 调用均为 0。

## 2. 冻结身份与人工裁决

以下均为**文件字节 SHA-256**，不是运行授权或独立来源保管证明。

| 封存对象 | 路径 | SHA-256 |
|---|---|---|
| 最终生产源码绑定 | [source-revision.v6.json](evaluation/agent-stage1-g25/source-revision.v6.json) | `3e4ff014bb6b2f81e40cf8196093c16e2a7cf9e173485eec2a24f82135410fd1` |
| v5 父版本 | [source-revision.v5.json](evaluation/agent-stage1-v2-fix/final/source-revision.v5.json) | `1a257a154f8db17e66b7d207ccd02ead75de68419064f176e7dd13c735c746d5` |
| 正式 Oracle v2 | [oracle.v2.json](../tests/fixtures/agent_stage1/oracle.v2.json) | `81306475371fd769b61f8132cf97dbc8dcdc82975a67a0982018575c08bed198` |
| 十项正式人工裁决 | [agent-stage1-human-adjudication.v1.json](evaluation/agent-stage1-human-adjudication.v1.json) | `97b38e01d204adfa2b3d2177a4bc1f7ee6d6026b9523242d6fe98ae825b12643` |
| 合成语料 | [corpus.v1.json](../tests/fixtures/agent_stage1/corpus.v1.json) | `ffc3b996e855417cc259cfa148e49c903f7e82398e7065656282a491bc2b3775` |
| 原始冻结清单 | [freeze-manifest.json](../tests/fixtures/agent_stage1/freeze-manifest.json) | `34d84f0be984ec86db93ffe3e644e20daafb37fe4621c3930b6c248c939fa356` |
| Oracle v2 冻结清单 | [freeze-manifest.v2.json](../tests/fixtures/agent_stage1/freeze-manifest.v2.json) | `c11073affadd71a1c860406d58806f3932e9b4b1d4094baadc6dc90d29125eb8` |
| v2.1 评分器冻结清单 | [freeze-manifest.v2.1.json](../tests/fixtures/agent_stage1/freeze-manifest.v2.1.json) | `dc9310c681c2054a2db1db75eb9355f087d1a04e83f7172b49443fcfa055af4d` |

v6 绑定 88 个生产文件；G25 修复相对 v5 仅改变四个获准文件。原 v1、v2.pending、历史评分器、失败轨迹、v3/v4/v5 和人工候选工作表均继续保留。v2.1 是已冻结的独立评分合同/实现版本，不能因本文封存而再次改写。

第一阶段十项正式选择全部为 A：G10-T2、G11-T2、G12-T1/T2、G17-T2、G18-T2、G19-T2、G20-T2、G24-T2、G30-T3。它们确认跨身份/会话澄清、合法用户条件保留、三维 partial、stale 阻断和歧义追问先澄清。记录依据是当时用户明确的“10轮全部选 A”；没有虚构真实姓名、签名或历史批准时间。该裁决只授权业务语义与评测预期，不授权数据库恢复、费用或部署。

第一阶段 Oracle 裁决与 RAG 历史 24 题/48 列表的探索性人工参考属于两套资产，后者不能替代本表 Oracle，也不是当前模型输出。

## 3. 已通过的离线业务能力

| 能力 | 已证明的行为 | 证明边界 |
|---|---|---|
| 商品、日期和会话范围 | 显式条件、页面变化及可信上下文按规则合并；未知/冲突商品受控拒绝 | 固定目录和合成数据；不是任意实体识别 |
| 失败后的承接 | 可继承经核验的用户条件；失败统计、排名及主题不升级为事实 | 用户条件与成功分析状态分离 |
| 统计、原文、主题层次 | 明确更换维度、数量追问、否定旧请求后改问新产物按独立预期执行 | 未覆盖的语言仍可能澄清或不支持 |
| 多轮歧义 | 统计与未完成主题竞争时先澄清，原待办保留，业务工具/分类为零 | 不把好评方向已知当作层次已确认 |
| 目标与计划一致性 | 独立 Goal 与计划冲突时工具前阻断；结果篡改拒绝发布 | 既有故障注入和专项边界 |
| 普通正向/负向主题 | 明确请求可执行发现、归属复核、来源及完成校验；无证据时不完成 | 固定模型响应下的工程正确性；不是模型主题质量 |
| 多维负面主题 P6 | 冻结原多维目标；准备、共享预算、执行、发布有独立校验；失败子维度保留 partial | 离线 transport/合成费用；真实 PostgreSQL 路径未验收 |
| 有限 RAG 事实回答 | 候选相关性、来源有效性、逐事实支持和必要关系分层；仅许可引用可发布 | 受现有命题语法和事实白名单约束 |

## 4. 已修复 P0/P1 缺陷及追溯

| 缺陷 | 原风险与最终行为 | 历史依据 |
|---|---|---|
| P0-01，G13/G14/G15 | 失败后丢失显式商品，回退到页面多商品；现在保留合法用户条件并重新核验目录/范围，不继承失败事实 | [P0/P1 最小修复](agent-stage1-p0-p1-minimal-fix-acceptance.md) |
| P0-02，G22/G23 | 被否定的“数量/主题”关键词控制新请求；现在识别明确生效子句，原问题完整保留 | 同上 |
| P1-01，G28 | 目标/计划已冲突仍查工具和补证；现在先阻断，最终 Validator 仍保留 | 同上 |
| G05-T2 | 更换质量维度没有保留已确认的主题层次；现在同层次切换，层次不明仍澄清 | [剩余 P1 修复](agent-stage1-remaining-p1-fix-acceptance.md) |
| G09-T2 | 仅日期变化却扩大商品范围、清掉合法待办；现在按变化字段合并，旧排名失效，合法新范围待办可恢复 | 同上 |
| G25-T2 / G26-T2 | 旧 RAG 屏障吞掉完整新统计；数量追问 Goal 丢维度；现在明确统计优先、唯一历史维度/方向才承接，全量分母不取展示条数 | 同上 |
| G24-T2，P1 | “客服呢”被提前冻结层次；现在只保留明确维度并澄清，无工具执行 | [Oracle v2 P0/P1 修复](agent-stage1-oracle-v2-p0-p1-fix-acceptance.md) |
| G30-T3，P0，含待办丢失 P1 | “那好评呢”面对统计/主题竞争仍自动执行且 complete=true；现在先澄清并逐对象保留原 negative/themes 待办 | 同上 |
| G25-T1，P1 | 已绑定 SKU 前缀阻碍原有限命题解析；现在分离符合严格条件的冗余前缀，来源/上下文/关系仍独立决定许可 | [G25 最终验收](agent-stage1-g25-rag-final-acceptance.md) |

兼容合同历史上的四项冲突按正式人工 A 对齐，原 ID 保留，并补足明确正向统计/主题的合法分支，见[旧测试合同对齐](agent-stage1-legacy-contract-alignment.md)。这是有记录的测试合同变更，不能描述为所有旧断言从未改变。G25 的测试版本绑定和已修缺陷断言适配也有独立差异。最终固定集已无 FAIL/PENDING；不据此宣称仓库或未来所有输入不存在 P0/P1。

## 5. State、Router、Goal、Planner、Tools、Validator 的实际关系

这些是当前代码中的职责和调用，并非六个独立 LLM Agent。普通链路由 [Worker](../src/data_analysis_agent/jobs/worker.py)调用 [run_review_agent](../src/data_analysis_agent/agents/reviews.py)驱动。

| 顺序/分支 | 实际调用与职责 | 关键约束 |
|---|---|---|
| 1. State | `load_analysis_context` 恢复同会话用户条件、经完整性/身份/范围检查的成功状态、排名及 pending | 原始条件不等于成功事实；旧身份/旧范围不贡献排名 |
| 2. 目录与范围入口 | 校验 source/dataset 和所选商品目录，冻结合法商品集合 | 此处可能已有目录 SQL；“工具前零执行”不等于从未打开连接或读取目录 |
| 3. 独立 RequestGoal | `build_request_goal(question, conversation_context)` **先于 Router/普通 Planner**解析业务产物及已确认条件 | 不读取 Router 的分类答案；部分/null Goal 不构成执行授权 |
| 4. Router + Planner | `resolve_review_request` 绑定范围、解析意图并编译 `ReviewQueryPlan`；Planner 固定 requirements/required_tools/任务契约 | 解析和计划在调用中协作，不是先执行工具再猜目标 |
| 5. 目标/计划门 | `build_task_goal` 将独立请求与已绑定范围组装；`validate_goal_plan` 复核 | unsupported 或冲突先退出，早于工具 context、registry 和自动 overview |
| 6. Tools | `create_review_registry`，先 `get_analysis_context`，再执行计划允许的统计、原文、主题或 `search_semantic_evidence` | 确定请求直接执行；其他分支由模型选择受限工具，仍受计划和预算检查 |
| 7. Validator | `validate_report` 校验产物、指标口径、来源、范围、引用及检索许可；普通 `validate_task_completion` 再按目标验收 | 数值自洽不自动证明目标正确；两道检查均保留 |
| 8. 回答与持久化 | 依据已校验结果压缩、生成受约束回答并保存状态；历史公开路径重验 | Worker succeeded / Agent ok 表示流程状态，不能代替 complete 或事实许可 |
| P6 独立分支 | `freeze_p6_goal` → `build_p6_query_plan` / `validate_p6_plan` → `prepare_p6_analysis` → 按启用条件执行 → 多维完成/发布校验 | 冻结可信排名和完整三维目标；准备不等于完成，partial 不升级为完整成功 |

RAG 的 RequestGoal / 普通 `task_acceptance` 可以为 null，使用自身 RuntimeQueryContract、事实 assessment 和引用绑定验收；不能为了统一仪表盘强塞 `complete=true`。统计、主题与事实任务的完成依据不同。

## 6. RAG 命题、来源及事实许可

1. **原问题保留。** 原始问题、检索输入及 `SemanticQuery.question` 不因 G25 修复改写。`RuntimeQueryContract` 绑定完整范围/摘要；内部 `evidence_question` 只按现有日期一致性和已独立绑定的单商品规则处理冗余前缀。
2. **范围先可信。** SKU 必须来自服务端目录/范围绑定；只在问题开头完整匹配单商品 ID、日期一致且余句符合原完整命题语法时分离。文本含 ID、自报 scope、多商品、冲突日期或删否定/数量/比较条件不能签发许可。
3. **检索仅产候选。** 语义服务返回后，生产者检查当前有效来源和一致快照；消费者再检查 source/dataset/product、review/annotation、profile、观点版本键、全文/quote 摘要、字符区间、排序和合法过滤。保存的有效标志不替代复核。
4. **逐事实及关系独立决定。** `reported_experience_v2` 沿用有限语法、事实模式、有限蕴含边和全文上下文否决。相似度、情感或维度匹配不能替代明确自述；不同评论分别支持两事实不能满足 `same_review`。
5. **只发布获许可绑定。** `permitted_keys`、事实/评论/引用区间及 quote 摘要须相互一致；回答只能表述本次检索内的买家自述，不能外推总体比例、因果或客观性能。旧结果重新公开仍重验原问题、范围、命题和引用。

G25 的四条**合成**候选修复前后完全相同，最终仅合成评论 #101 的 [10,14) 获 `delivery_slow` 许可，其余三条仍被上下文规则拒绝。这是规则/绑定链证明，不是真实商品 055 的四条买家评论。`review_reported_facts.py` 整个文件、RuntimeQueryContract 结构及事实许可标准没有因 G25 扩大，见[保全证明](evaluation/agent-stage1-g25/permission-policy-preservation.json)。旧 v5 失败快照没有追溯升级。

生产来源拥有者的当前来源核验与持久化消费者复核也不是独立历史封存认证。本地旧真实快照不能伪装为本轮生产者签发的当前可信来源。

## 7. 尚未验收的真实能力及 P6-E1B

历史上已有受限真实 RAG 运行及探索人工材料，见[第二阶段准备审计](agent-stage2-real-business-evaluation-readiness.md)。这些历史个案不能升级为 v6 的独立真实业务验收；本文也不将“本次未验证”写成“项目从未运行过真实组件”。

| 未验证范围 | 仍缺什么 |
|---|---|
| 真实模型 | 原始评论上的独立语义评分、主题发现/归属质量、错误事实许可、供应商实际取消/计费边界 |
| 真实向量 | 当前 055/053 有效版本关联、召回覆盖和排序质量；053 已知缺口未解决；055 历史 200 条索引不代表全商品覆盖 |
| 真实数据库 | 当前快照、角色/只读权限、事务一致性、并发、故障和资源清理的真实验证 |
| 历史来源 | 本地真实评论可追溯，但历史有效状态和独立封存仍有限制；不等于当前数据库 P0 准入 |
| 产品交付 | 前端实机、真实端到端运行、生产容量、部署及运维验收 |

P6-E1B 以[最终真实执行报告第 12 节](P6-E1B-final-real-execution-acceptance.md)的最终状态为准，而非只引用早期 CIM 错误：

- TCP 原生契约仍 BLOCKED：真实 `state=0` 不能认定为 LISTEN；真实原生结果 **5 PASS / 2 FAIL** 保留，没有通过改断言解除。
- OS/进程身份关系尚无充分可信通过条件；管理员启动日志缺失已证实，唯一失败根因仍 UNDETERMINED。手动只读材料是 PREPARED / NOT APPROVED / NOT EXECUTED。
- 恢复计划/策略绑定一致仅为材料检查；**正式恢复授权 NOT APPROVED**，不得把旧批准或 `PENDING_BINDINGS_VALID` 当新执行授权。
- 部分集群初始化完整性仍不能视为成功，存档/新实例事实不能预填。**PG-01～PG-08 NOT EXECUTED**，PG-07 失败必须阻断 PG-08 的原安全门保持。

因此继续搁置恢复。离线通过、人工业务裁决、真实评论文件存在均不能解除这些独立阻断。本轮没有重试 OS 查询、连接 PostgreSQL 或触碰恢复授权。

## 8. 可展示功能与对外表述边界

| 可以展示并如实说明 | 不可宣称 |
|---|---|
| 固定合成场景中的统计/原文/主题调用链和明确的计数口径 | 真实商品分析准确率 100%、真实模型已通过 |
| 商品/日期切换、合法条件继承、歧义澄清及待办保留 | 任意自然语言、多轮问题均可理解并自动完成 |
| 目标冲突工具前阻断、来源篡改拒绝、partial/stale 状态 | Validator 能识别一切业务错误或系统已经生产可用 |
| 有限 RAG 自述事实的来源、引用和关系许可演示，标明合成输入 | Top-K 代表全量、相似命中即事实、可推断因果或总体投诉率 |
| 本地历史真实评论供人工阅读及候选评测准备，标明历史/单人限制 | 合成人工 Oracle 结果等于真实数据验证；旧人工标注等于本轮模型输出 |
| 历史受限真实运行报告作为历史案例 | v6 当前真实数据库/向量/完整端到端验收通过、053 覆盖已修复 |

## 9. 本次收口检查与停止点

既有收口材料保留；本次独立文件复核再次确认：v6 的 **88 个生产文件摘要全部匹配**；G25 最终清单的 **39 个产物全部存在且摘要一致**，其中包含正式 Oracle、人工裁决、评分绑定及历史执行证据。引用的回归分数均来自既有冻结结果，**本次测试重跑数和 Agent 执行数均为 0**。

本轮不修改 Agent 业务源码、已冻结 Oracle、人工审查资产、事实许可、P6-E1B 安全门和授权。不运行整套测试/前端构建/浏览器验收；没有界面或业务逻辑变更。第二阶段仅形成[可行性与候选问题报告](agent-stage2-real-business-evaluation-readiness.md)，不自动开始预测、数据库、向量或下一阶段评测。

本次封存核验记录：[stage1-closure-audit.json](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-closeout-20261111/stage1-closure-audit.json)。它只记录文件一致性和已有结果，不是新一轮业务成绩或数据库/模型授权。两份原有交付文档已先保存在本轮私有审计目录，再补充本次复核情况；旧测试、评分、失败和裁决记录均不覆盖。

第二阶段材料阅读还发现一项当前访问限制：B2 的 `release-v1/` 私有目录本次访问被拒绝，不能将旧报告中“曾核对15个文件”重新描述为当前核验通过。未重试、提权或修改 ACL；这不影响已有第一阶段合成 Oracle 的冻结结果，但进一步限制历史人工参考的可直接复用范围。可以保留其历史发布报告作为线索，待访问和来源门槛满足后再单独核验。
