# RAG Step 4B — E1-A探索性预测准备与评估协议冻结方案

日期：2026-11-05（Asia/Taipei）。方案版本：E1-A v1，待用户确认执行版。

**本轮完成只读核查和方案落盘，未发布正式参考、未启动预测、未修改业务代码。** 现有人工裁决完整，探索输入可复用；历史可信E1的来源条件仍未满足。本方案仅适用于 `exploratory_current_observation_history_unproven`。文档中的新接口、版本和文件均是拟议设计，不是已实现能力；文档确认也不自动授予模型运行或评论外发权限。本阶段结束后停止，不进入E1-B。

## 1. 当前真实状态与证据

本轮重新读取并校验现有会话、来源登记、48份探索输入、参考包装及相关实现，未仅沿用旧报告计数。

| 项目 | 本轮只读核对结果 |
|---|---|
| 人工会话 | `e1-human-adjudication-session (3).json`，145条有效事件，最终完成事件E0145 |
| 活动共享契约、CASE、同输入配对 | 24/24、48/48、4/4通过；无待修改草稿、过期确认或结构冲突 |
| 已确认人工状态 | sufficient 19、limited 0、insufficient 29；这是人工标签分布，不是预测准确率 |
| 参考包装 | 48份与当前会话校验输出一致；仍为待发布包装，正式确认字段未被改写 |
| 探索输入 | 48组、222条排名记录、93个来源键、57条评论；与bundle的batch及上下文摘要一致 |
| 来源政策 | 57条均为history_unproven，独立来源封存未完成，历史可信批次0/48 |
| 原两次观察 | UTC 2026-11-04 02:15:48.466614与06:46:15.772789；保留跨时点限制，不合并为历史事务 |
| 当前实验登记 | prediction_execution_authorized=false；真实预测数0；质量门槛未批准 |
| 仓库状态 | 开始时工作区干净；本轮计划只新增本文档 |

固定范围仍为reviews_csv / reviews-csv、商品100013588055、UTC 2021-09-11至2022-08-26、BAAI/bge-m3、1024维、quote-utf8-v1、原200条索引。没有新检索、补读数据库、M1–M3重跑、备份访问或向量重建。

### 1.1 可复核的身份锚点

| 对象 | SHA-256 / 身份 |
|---|---|
| 人工会话文件字节摘要 | `b5e722b632e6639b6ebcedbe883edd0ca4f2699e5460ae8cf2b75c6c719b171b` |
| bundle规范摘要 | `f0ee9e6d2a0bcaa16baf17118d97fe7da9843098b919a6335160dd2729ef7b5a` |
| 原run_id | `a2f4907ec8cacc6bae4b52f8d89a9ee557ae09f1f4e7a7f3c6f3803b34848f33` |
| manifest规范摘要 | `a17e77d4f7c5d83c5b1c80bad318b374432f5750a9151c826f451feb52b5c3f8` |
| scope摘要 | `167e14701623dc709a96ce8761a84f7a89c8eaba9c34f6f71025e2dc89e7f766` |
| 已保存观察来源规范摘要 | `ecb19362428a75b55552d181688c6dff49a820e973b63a3274efef46276eea55` |

上述摘要用于本地身份和完整性绑定，不是新增独立来源证明。文件字节摘要与模型规范JSON摘要分别使用，不能互换。

### 1.2 实际读取的实现与材料

- [最新验收前置报告](rag-step4b-e1-acceptance-preflight.md)、[人工裁决工作台报告](rag-step4b-phase2c1c-human-adjudication.md)、[4B-2b协议](rag-step4b-phase2b-e1-readiness.md)、[探索输入报告](rag-step4b-phase2c1-exploratory-e1-review-preparation.md)。旧工作台报告的0/24、0/48是当时状态，当前以E0145为准，不覆盖历史报告。
- [e1_review_protocol.py](../src/data_analysis_agent/reviews/e1_review_protocol.py)、[e1_preparation.py](../src/data_analysis_agent/reviews/e1_preparation.py)、[human_adjudication.py](../src/data_analysis_agent/reviews/human_adjudication.py)、[exploratory_e1.py](../src/data_analysis_agent/reviews/exploratory_e1.py)、[exploratory_e1_metrics.py](../src/data_analysis_agent/reviews/exploratory_e1_metrics.py)、[historical_snapshot.py](../src/data_analysis_agent/reviews/historical_snapshot.py)、[evidence_judge.py](../src/data_analysis_agent/reviews/evidence_judge.py)。
- 私有材料使用现有 `outputs/rag-retrieval-quality/e1-acceptance-preflight-20261105/` 的待发布参考和 `step4b-phase2c1-exploratory-v1-20261104/prepared-v1/` 的inputs、observed-sources、experiment，以及原人工会话和原bundle。本轮未生成新的私有数据副本。

证据到结论的对应关系：E01=事件及参考包装复核，证明人工内容已齐；E02=输入/来源摘要复核，证明可复用本地探索输入；E03=来源政策与历史接口，证明不能升级为历史可信；E04=当前判断器及汇总器接口，证明预测适配和独立输出审计尚需开发。后续路径依次为参考发布、合成审计验证、获准小样本调试、固定配置全48组运行；不是继续重做人工作业。

## 2. 已满足、未发布、未授权与历史缺口

| 分类 | 当前材料或条件 | E1-A处理 |
|---|---|---|
| 已验证事实 | 93/57行级版本核验、48组输入身份、222条原排序、24份契约、48份裁决及E0145 | 复用，不重新评分或检索 |
| 已存在但未正式发布 | 48份references.pending、活动契约绑定、上下文摘要、本人确认记录 | 依据现有事件设计版本化转换，当前不发布 |
| 已有结构能力 | HumanReference、QueryContract、EvidenceBatch、纯来源校验、探索汇总器 | 复用结构与枚举，明确其证明边界 |
| 尚未授权的操作 | 参考发布执行、新预测运行器开发与真实调试/全量运行、外部模型处理评论 | 分别批准，不能从本文或过去只读授权推定 |
| 历史可信E1未满足 | 原run历史有效状态/完整成员依据、不可变历史来源及回执、独立封存和外部固定摘要 | 探索路线保留限制；不尝试严格可信批次准备 |
| 尚未实现 | 通用预测输出契约、参考发布器、隔离运行器、独立语义引用审计及新增指标适配 | 仅提出后续文件和验证方案 |

重复当前M1–M3不能补出历史证明，E1-A不新增元数据读取。原已有138条模型观点元数据中的额外45条只用于来源附件，不进入候选或模型上下文。

### 2.1 对现有代码能力的准确解释

`prepare_trusted_batch()`的独立来源前提保持不变，不改其参数检查，也不填入本机临时算出的“外部pin”。探索运行使用独立命名、显式history_unproven的来源政策；`source_valid`在探索指标中若被复用，仅表示通过本地观察政策检查，必须随报告附上source_policy，不能解释成历史可信。

现有规则判断器版本为 `explicit-clauses-zh-v1`，不是通用LLM。它的JudgeResult没有完整的逐事实支持等级、逐关系判断及各项理由；`validate_judge_result()`会重新执行规则并比对输出，不是通用模型结果的独立审计器。不能用“与自己重算一致”宣称语义质量通过。

当前 `_judge()`还会因contract的uncertainty/clarification或statistics/clarify任务阻断许可。已确认的Q07、Q20、Q21等仍保留这些字段，Q23仍为clarify；**人工确认契约不等于这些字段被清空**。原样保留并记录其对规则覆盖/拒答的影响，不能为适配规则删掉条件或更改人工参考。

## 3. 探索性实验边界与协议冻结对象

固定 `experiment_kind=exploratory_current_observation_history_unproven`、`history_status=history_unproven`、`independent_seal=false`、`validation_policy=local_saved_sources_cross_time_v1`，以及B策略 `quote_full_review_veto / 1`。完整评论仅解释、限定或否决quote；支持事实只能来自当前候选quote内。

全部48个query-mode列表为固定应评全集，包含Q17-filtered、Q22-filtered两个空列表。保持问题、模式、原过滤、顺序、候选数、Top-K与来源字段；不做截断、改写、补证据、自动过滤或检索优化。原距离和相关性评分不输入预测器，也不作为充分性阈值。

执行前拟冻结一份新的E1-A登记，绑定：原run/manifest/scope/inventory引用、48个batch/context摘要、24份活动契约的内容及确认序号、参考release摘要、协议版本、代码树摘要（包括未提交改动）、运行器/审计器版本、引擎和参数、调试清单、尝试/重试政策、指标定义及执行授权来源。新的登记与旧experiment.json分开，旧登记的false和历史计数不改写。

本文是v1执行协议的固定提案。用户确认后才记录真实批准依据并冻结执行版；后续改方案另建版本、列差异，不能边看全量结果边改变分母或规则。数字化质量通过门槛仍待人工确定；没有门槛时仅作描述性探索，不发布“质量验收通过”。

## 4. 参考答案发布与冻结方案

目标是从既有真实确认记录**派生新版本**，不是手改pending文件的human_confirmed，也不是再评一次48个CASE。本轮只设计以下流程。

1. **接收一次参考发布授权。** 明确使用当前(3)和E0145发布探索参考v1。批准发布与批准模型运行分开；发布回执记录真实授权来源和程序实际执行时间，不假装产生了新的人工评审时间。
2. **只读重验。** 用固定bundle校验会话链、活动契约、CASE、配对和完成事件；任何内容/身份变化均中止。原导出、pending与旧审计不覆盖。
3. **创建发布物。** 拟用外层 `e1a-reference-release-v1` 包装现有 `e1-human-reference-v1`，记录scope、CASE映射、会话文件摘要、E0145及各query/CASE事件摘要、契约/批次/context/来源摘要、source_policy、单人限制及既往评分接触声明。
4. **由事件推导正式记录。** 只有该CASE及活动契约的真实事件有效且发布动作已获授权时，在**新发布物**内生成confirmed状态及相应确认字段；reviewer_decisions从原评审人、原state/许可集合及原决定理由逐项投影，不增加评审人、不新写语义理由。不能只翻转布尔值后跳过事件复核。
5. **区分评审与裁决。** 默认使用review_status=confirmed。工作台里的同人复核/配对统一决定不自动成为另一个adjudicator；没有独立裁决依据时，不伪造adjudicated字段。原审计发现、修订轨迹和配对记录作为附件保留。
6. **协议验证。** 新记录走 `validate_human_reference(..., for_evaluation=True)` 的事实/关系/引用/确认结构校验；批次身份来自已固定探索输入并经其来源政策复核。参数名trusted_batch在此结构函数中不授予来源历史可信性，外层必须保留history_unproven声明；不调用strict准备接口冒充认证。
7. **写清单与隔离保存。** 新release以独占目录写入，分别登记文件字节摘要及模型规范摘要；保留原确认时间、实际发布时间、父版本摘要。写入失败不留下“已发布”标志。参考目录由评价/保管角色持有，预测worker没有读取权限。
8. **冻结后的更正。** 调试或预测不修改参考。若发现真实参考错误，保留v1和本轮结果，另行记录争议、人工更正及v2；不能据有利预测反推金标准。引用审计新增意见是审计记录，不是偷偷修改参考。

`judge_output_seen=false`只继承原流程声明并保留其限制，不把它包装成可证明的双人盲审。48项中无limited参考，后续limited召回率分母为0，应报N/A，不能补造真实limited案例。

## 5. 预测输入、输出与隔离协议

### 5.1 引擎与来源入口

建议将现有规则作为R0对照；如要研究更广的语义能力，再单独批准L1模型臂。R0/L1分别固定引擎标识、规则或模型修订、提示/解码参数和输出契约，不能合并为一次运行，也不默认采用任何外部供应商。

R0的严格入口不能通过把candidate_batch复制成trusted_batch来绕过。拟新增的探索入口须从已固定SQL观察记录恢复独立比较投影，先通过本地来源政策，再适配获准的纯规则内核；保留原strict入口和安全约束。实现时需用合成测试证明适配不改变规则行为。若无法安全获得逐事实/关系诊断，原始结果仍保留，该部分明确unknown/未提供；不能从“未匹配”反推not_supported。本轮不直接调用规则内核。

L1是否本地、具体模型/版本、是否允许评论外发、端点、保留政策、费用/超时/输出上限等均需后续明确。未批准前不发送任何真实评论；设计接口不意味着数据处理授权。

### 5.2 拟议输入：e1a-prediction-input-v1

| 字段组 | 内容与来源 |
|---|---|
| 运行身份 | experiment_id、protocol_version、engine_id、input_version、input_sha256；由运行器绑定，不由模型自报决定 |
| 查询身份 | query_id、question、question_sha256、scope_ref；原问题不改写 |
| 活动契约 | QueryContract、contract_sha256、contract_revision、independent_target_fact_ids；两模式同一内容版本 |
| 候选 | 复用EvidenceBatch/EvidenceCandidate的原query/mode/rank/key、review/annotation、quote及区间、已保存原文和来源身份 |
| 来源与上下文 | observed_sources_sha256、batch_sha256、context_input_sha256、B策略/版本、history_unproven政策 |

引擎只接收允许字段投影。模型文本视图以原问题、当前契约、quote、合法引用标识及对应完整原文为主；技术哈希由运行器验证。完整原文分为上下文区域，不与支持quote混为一段。字符区间使用原评论Unicode码点的半开区间 `[start,end)`，不使用UTF-16下标，不按trim后的文本重编号。

**禁止进入预测输入**：reference_state、人工fact/relation支持等级、许可/缺失答案、人工证据映射、review_notes、decision_rationale、确认理由、原0/1/2评分、cosine distance、审计修改建议、旧预测及其评价结论。活动契约的required/independent目标是查询条件，可输入；CASE实际支持结果不是查询条件。

不将含原评审和建议的bundle或整个会话交给worker。由评价侧生成独立runtime目录，只含白名单输入和配置；参考、审计与评分置于其他目录。建议采用受限账户/进程，只读runtime、仅写本次raw输出。普通目录分开只能证明逻辑隔离，不能声称实现了OS访问隔离；实现时需验证权限及禁止引用目录访问。模型无数据库、文件搜索、检索、Agent工具或自选网络能力；quote内指令按待评文本处理，不能覆盖契约和系统规则。

### 5.3 拟议输出：e1a-prediction-output-v1

| 字段 | 定义 |
|---|---|
| 版本与绑定 | output_version、input_sha256、contract_sha256、query_id、mode、engine_id/version、context_policy/version、protocol_version |
| execution_status | completed / validation_failed / execution_failed；缺输出由运行台账记录missing，不由模型伪装输出 |
| status | sufficient / limited / insufficient；仅completed可填写，失败时为null |
| fact_findings | 每个required fact恰好一项：fact_id、direct/partial/not_supported/unknown、简短rationale、FactEvidence列表；unknown表示未可靠判断，不等于证据确实不存在 |
| relation_findings | 当前relation_0等及contrast恰好一项：supported/not_supported/unknown、理由、证据键及关联事实的精确来源；复用RelationFinding并为预测侧另加来源映射包装 |
| supported_target_fact_ids | 实际许可的目标事实ID，不由排名、维度或相似度生成 |
| missing_required_fact_ids | 按现有all_of/any_of及必要关系定义，保持与人工协议一致 |
| evidence_refs | 当前列表内的FactEvidence；精确绑定fact/query/mode/rank/key/review/annotation、quote区间和来源摘要 |
| context_observations | 独立记录解释/否决的key、原文区间及理由，不能作为quote外的新支持 |
| coverage与原因 | 每事实/关系的covered/unknown/not_provided、reason_codes、limitations；只给可审计理由摘要，不要求内部思维链 |
| 原始回执 | raw_output_sha256、attempt_id、结束状态、耗时、实际模型/规则版本；由运行器记录 |

对R0保留原JudgeResult，另存投影/诊断；没有的信息明确unknown或not_provided，报告其覆盖，不能从聚合reason_codes捏造某个事实的完整判断。若需要规则追踪接口，是后续显式实现与验证的工作，不在本文宣称已具备。

空候选是正常输入，不跳过：通常只能许可空集合、报告insufficient和no_candidates_not_proof_of_absence；不能表达全量数据不存在。statistics/clarify及契约不确定项必须原样进入协议，不能自动变成可回答的产品能力/总体统计问题；未知与澄清原因单列，不擅自改参考适配规则。

## 6. 独立输出审计器设计

审计分为**结构/来源、契约逻辑、独立语义映射**三层。预测worker不能读取参考；审计器在raw结果封存后才读取发布参考。审核结论另存，不修改预测原文，也不把审计修正后的答案当原始预测成绩。

| 层级 | 检查 | 失败或未知处理 |
|---|---|---|
| 身份与来源 | input/contract/batch/context/engine摘要及版本一致；列表和连续排名固定；重复/跨query-mode来源、来源身份冲突 | validation_failed；保留原始输出，停止受影响运行/发布，追查污染，不转换insufficient |
| quote与区间 | key在当前候选；身份字段匹配；每个支持引用满足hit.start≤start<end≤hit.end；切片和输入哈希合法 | 引用非法；许可事实缺引用也必须报错，不能因引用总数0躲过审计 |
| 上下文 | context区间只限该候选对应原文；quote外只解释/否决 | 借入quote外新事实属于越界支持 |
| 事实结构 | 事实ID完整唯一；direct必须有合法quote引用；许可集合只能来自direct，unknown/partial不可许可 | 结构失败；主题相似或机器维度一致不能补足直接支持 |
| 关系结构 | expected关系完整；same_review来源身份一致；before/contrast连接的事实均有直接来源 | 不允许跨评论拼接混合情感；字符先后不证明事件先后；不能把字段齐全当关系语义成立 |
| 状态与集合 | 重算当前契约要求、独立目标和关系阻断，检查状态、许可与缺失集合 | 不为消除冲突自动更改state或字段；记录原始违规 |
| 语义与充分性 | 对象、细分主题、情感、否定范围、必要条件、时间关系、quote支持及上下文否决 | 依据先冻结的人工映射审核；无法覆盖时unassessed/coverage_unknown，不自行标为正确或错误 |
| 越界结论 | 无许可仍声称确定结论、个别评论推断比例、短期故障推长期耐用性、噪声推睡眠影响、检索空推全库不存在 | 单列unsupported_claim/context_fact_leak等风险；不以格式正确掩盖内容错误 |

### 6.1 状态及缺失集合的固定规则

按当前人工字段的含义：all_of要求全部核心事实及关系成立；any_of至少一个核心事实直接支持且必要关系成立即可。sufficient必须满足核心并许可正确直接支持集合；limited只许可已确认可独立回答、且未被未满足关系阻断的目标；insufficient许可集合为空，不能拒绝已完整满足的核心。

`missing_required_fact_ids`不是“所有未支持的备选项”。例如完全合成的any_of(g0,g1)，仅g1直接支持且无关系时，可为sufficient、许可[g1]、missing=[]，g0仍在fact_findings中标not_supported。不能因missing为空而把g0升级direct。必要关系未知时不能按已满足处理；缺失关系与缺失事实分开记录。

对预测unknown允许合法保守输出，但必须计入覆盖不足和对应的过度拒答/状态错误，不能删掉这些CASE。若字段无法满足输出契约，是结构失败，不是正常保守判断。

### 6.2 语义映射与引用“精确”的含义

字符区间合法不等于事实支持。本轮建议首版采用严格映射政策：只有模型的fact_id、key、精确 `[start,end)` 与已发布人工direct映射一致，且事实被人工许可、必要关系和上下文条件均一致，才自动记为“人工映射已覆盖的支持”。关系还须与人工关系记录和事实来源对应，不能仅比较两个fact_id。

模型引用同一quote的其他合法子区间、另一候选或其他关系来源时，先做结构校验，再记语义未评；不自动认为错误，也不认为同事实ID就正确。若需追加人工输出审计，单独授权、保留原参考和raw预测，另存审计意见与版本，不重做已完成的48项裁决，不用预测反向修金标准。

支持映射的自动核对只是已审范围内的证据追溯，不宣称原人工标注绝对正确。未评映射使主支持率保持N/A，同时报告已评子集和覆盖率。必要关系、上下文否决及任何声称的新结论都须被纳入语义审计范围。

## 7. 评价指标与统计口径

### 7.1 固定全集和分母

设N=原48个(query_id,mode)。H为参考已发布且本地观察政策校验有效的列表；V为H中预测执行完成、身份/引用/契约结构有效的列表。H+为人工许可非空的列表，H0为人工许可空的列表，V+/V0是各自与V的交集。按当前内容，完整发布时预期H+=19、H0=29；实际运行仍重算并报告，不能预填质量成绩。

报告N、H、V、各类别有效数及流失原因。来源错误、缺参考、缺输出、执行失败、结构失败、语义未评、覆盖未知分别计数；可能重叠的诊断另列，另给每行唯一终态台账，不相加伪造互斥分布。失败不得补成insufficient。零分母为N/A；缺失不是0，也不是成功。

| 指标 | 分子 / 分母 | 处理与补充 |
|---|---|---|
| 三状态一致率 | V中预测state=人工state的列表数 / \|V\| | 同时给reference×prediction的3×3混淆矩阵、各状态分母；给有效且正确数/\|H\|的全范围成功比例，避免失败被隐藏 |
| 有证据时回答许可率 | V+中预测许可非空的列表数 / \|V+\| | 只衡量是否许可，不等于许可内容正确；另报有效许可数/\|H+\|的端到端许可覆盖 |
| 过度拒答率 | V+中预测insufficient的列表数 / \|V+\| | 人工limited也属可答；另报H+未获得有效许可的比例，拆为正常拒答、缺失、失败，不能把失败都称拒答 |
| 无证据时不当回答率 | V0中预测许可非空的列表数 / \|V0\| | sufficient/limited都算许可；无证据指本列表人工无可许可目标，不表示全库无证据 |
| 引用结构有效率 | 身份、key、范围及quote区间均合法的去重支持引用数 / 可解析输出中的全部去重支持引用数 | 统计非法输出中可识别引用，不能只留下合规输出；0引用N/A；另报许可事实缺引用数、无法解析输出数。上下文引用单独统计 |
| 许可事实证据支持率 | 经独立事实/精确引用/关系/上下文映射审计得到支持的许可事实数 / 全部预测许可事实数 | 任一许可事实未评时主率N/A；另报已评支持率、已评/全部覆盖率及未评数 |
| 事实判断一致率 | 预测support标签与人工完全相同的事实数 / V内全部required fact数 | 比较direct/partial/not_supported/unknown；预测unknown计不一致且单列。缺失事实数组属于结构失败并在H事实覆盖中暴露，不从账本删除 |
| 关系判断一致率 | 预测关系标签与人工相同的关系数 / V内全部expected relation数 | 同时按same_review/before/contrast分组；无关系组N/A，不能当100% |
| 许可/缺失集合一致率 | V内相应集合精确相同的列表数 / \|V\| | 两集合分开报告；any_of按既有定义，不强制未支持备选项进入missing |
| 覆盖不足 | V内存在unknown/not_provided语义诊断的列表数 / \|V\|，并给逐事实/关系覆盖分母 | 单列由覆盖未知导致的过度拒答、状态一致但覆盖未知；规则不认识不等于证据确实不足 |

去重引用身份按(query_id,mode,fact_id,key,start,end,用途)确定，同一事实重复输出相同引用仍报重复，不靠重复增加分母。语义支持率与字符合法率分别命名；history_unproven政策有效不等于历史来源有效率100%。

非法或解析失败输出不进入常规状态率，但若raw输出可明确识别出无支持许可/确定性断言，另报原始越界尝试数及应评类别分母。不能解析的项保留未知风险，不能计为安全。当前limited参考分母0时，该类召回N/A；limited预测仍按许可、状态和事实规则正常审计。

### 7.2 filtered / unfiltered配对与分组

总体、U、F绝对指标各报分子/分母，再对同query计算F−U。配对要求相同活动契约、来源集合版本、上下文政策、引擎/配置及规则版本，原U/F各自候选可以不同，不能为配对换证据。指标两侧均有效才进入该指标的配对分母；例如无证据风险率仅在两侧均属人工无许可类别时形成该条件指标的配对。

每个指标给逐query差值、有效配对n、缺失/失败/类别不同/零引用/映射未评等排除原因。不能用两组不同分母的均值之差冒充配对。4组“相同输入一致性”是审计对照，全集U/F最多24组，不将两者混淆。

按冻结manifest的aspect/sentiment，以及活动契约的task、对象、主题、否定、混合情感、多条件和关系类型分组，各自显示N/H/V与指标分母。类别可重叠，不能相加冒充独立样本。另报全48行有效/失败情况、各类全范围成功/覆盖指标，防止全部拒答或大量失败制造表面改善。

### 7.3 对现有汇总器的复用限制

`exploratory_e1_metrics.summarize_e1()`已有状态、过度拒答、无支持许可、支持/引用覆盖及配对汇总，可复用，但它只接受先审计好的行，不会生成真实语义判断。`source_valid`须标为本地探索政策含义。

它尚无完整的有证据许可率、逐事实/关系一致率和全范围覆盖视图；现有关系结构也不足以承载全部拟议预测诊断。后续需独立适配/补充指标并保留旧版本，不能把本文新增口径宣称为当前代码已实现，也不手填AuditedCase的支持布尔值让分数通过。

## 8. 分阶段执行计划

下列为建议执行顺序，**本轮均不实施**。

| 阶段 | 内容与产物 | 放行条件 |
|---|---|---|
| A：本文审阅 | 确认探索政策、参考发布、预测协议、映射和分母方案 | 用户批准协议执行版；质量数值门槛可继续待定，但不能宣称质量通过 |
| B1：实现与合成验证 | 新结构、发布器、隔离投影、运行器、审计与指标；合成覆盖无引用direct、跨列表、哈希/区间、关系、unknown、空候选、失败、全部拒答 | 真实来源/参考零改动；确认“预测进程不能读参考”；结构和来源违规有停止机制 |
| B2：发布参考与固定登记 | 复用E0145派生探索参考v1，固定48列表、24契约及全部版本 | 已获发布授权；新release结构校验完整；执行登记单独获准 |
| B3：获准8项调试 | 固定下列清单，验证输入隔离、输出格式、精确区间、审计和失败台账 | 仅调试程序；不得改参考或据命中率挑模型/提示/阈值 |
| B4：固定配置全48项 | 调试后冻结代码、引擎、提示、解码、重试政策；对全部48列表重新运行，含调试清单项 | 全量执行授权；raw结果先封存再评价；同一配置无择优重跑 |
| B5：审计与报告 | 生成48行终态、映射覆盖、完整指标/混淆矩阵及配对结果 | 未评明确N/A；禁止把诊断集结果当泛化验证或正式可信验收 |

建议的8项调试清单仅按契约结构/输入形态选择，不按人工状态、预期成功或后续预测挑选：

| CASE | 查询 / 模式 | 调试目的 |
|---|---|---|
| CASE-004、CASE-009 | Q20 / U、F | 否定焦点纠正、单核心事实、同契约两模式 |
| CASE-012 | Q15 / F | any_of备选目标及missing集合含义 |
| CASE-023、CASE-034 | Q07 / U、F | 三事实、same_review与before联合约束 |
| CASE-040 | Q21 / F | 同评论混合情感与contrast |
| CASE-005 | Q17 / F | 空候选的合法输出和非全库缺失声明 |
| CASE-028 | Q23 / U | clarify、未知情感及规则覆盖边界 |

这是8个列表而非8个独立问题；只有Q20/Q07形成完整调试配对。Q22-filtered空列表仍在全48组内。limited等当前真实参考未覆盖的分支用完全合成用例验证，不改变真实标签补齐类别。

每个列表每个配置拟只记一个主尝试。超时、截断、解析失败保留原始回执；默认不自动重试或用修复后的答案替代首尝试。若允许重试/JSON修复，须提前冻结次数、触发和主指标选取规则，所有尝试均保留，不能择优。

调试参考已经被开发者接触，原24题也已用于诊断，因此全48结果始终是已见集探索。调试只修程序性错误；若修改语义规则、模型、提示或阈值，另立实验臂/配置并记录迭代，不将后续提升宣称为独立泛化。真正泛化需要另获授权的独立问题与标注，不自动扩大本次索引或数据范围。

## 9. 预计新增或修改文件

以下名称是建议，实施前按仓库惯例复核；当前未创建代码或私有发布/运行目录。

| 拟议文件 | 最小职责 |
|---|---|
| src/data_analysis_agent/reviews/e1a_protocol.py | 新输入/输出/发布/审计外层结构，复用QueryContract、FactEvidence和已有枚举 |
| src/data_analysis_agent/reviews/e1a_reference_release.py | 绑定原会话和E0145的确定性发布、正式结构校验及新版本清单；不重评 |
| src/data_analysis_agent/reviews/e1a_prediction.py | runtime白名单、授权检查、探索来源入口、引擎适配与每列表执行台账 |
| src/data_analysis_agent/reviews/e1a_output_audit.py | 来源/区间/状态规则检查、精确映射、unknown及独立语义审计记录 |
| src/data_analysis_agent/reviews/e1a_evaluation.py | 适配既有汇总器，补齐本文新增事实/关系及覆盖指标，输出带政策的报告 |
| scripts/evaluate_exploratory_e1.py | 显式分开的校验、发布、运行、审计、汇总入口；发布不能自动触发运行 |
| tests/test_e1a_*.py | 完全合成夹具和权限/字段泄漏、来源、协议、失败分母及全部拒答等边界测试 |
| outputs/rag-retrieval-quality/e1a-exploratory-v1/（未来私有目录） | 分离的release/runtime/raw/audit/metrics及配置回执；Git忽略，原目录不覆盖 |

优先新建薄适配，保持strict准备接口和历史模块不变。若确需抽取现有规则诊断接口或扩展现有指标，必须说明行为差异、另版并单独测试，不借本阶段设计授权修改原业务逻辑。Agent、Supervisor、LangGraph、API、前端、数据库、Embedding和检索排序均不在实施范围。

## 10. 需要明确批准的事项与本轮停止点

| 待确认事项 | 需要确认的具体范围 |
|---|---|
| 执行协议冻结 | 接受本文探索政策、B上下文、精确映射/N/A/失败和U/F分母口径及版本变更规则 |
| 参考发布 | 使用当前会话/E0145发布探索参考v1，复用实际人工确认；批准发布动作与保管方式，不重复24/48裁决 |
| 后续开发 | 第9节离线模块、合成测试、规则诊断适配边界；本次只设计，不授予实现权限 |
| 预测引擎 | R0对照是否先行、是否增加L1、具体版本/配置；如为外部模型，单独批准处理哪些已存评论及端点/保留边界 |
| 运行授权 | 8项调试与全48项运行分开确认，含预算、超时、重试、日志与停止/发布策略 |
| 输出语义审计 | 是否接受首版exact-span覆盖政策；未评引用如何保持未知，是否授权另存追加输出审计 |
| 质量验收 | 正式数值门槛仍待人工指定；可先批准描述性探索，但不能发布质量通过结论 |

这些是按阶段的权限边界，可以由用户在明确范围内一次性批准多项；不得推定过去的来源只读授权或本次文档任务批准了模型预测。历史可信E1的独立来源条件与本探索协议分开，本文不要求重跑M1–M3，也不伪造独立封存。

本轮只读核查调用了validate_session和现有输入模型，在禁止网络、数据库及真实judge调用的保护下核对现有48组绑定；没有执行真实或合成预测、发布器、运行器、审计器或指标实验，没有新增单元测试。先前报告的91项通过属于前阶段，不冒充本轮测试。两项既有SQLite/PostgreSQL队列错误本轮未重跑、未计通过。

本轮完成的是**E1-A方案v1及待批准冻结清单**。人工内容与本地输入准备已满足；参考正式发布、预测/审计适配和运行授权尚未完成。建议下一步先审阅并批准参考发布与离线实现范围，再实施E1-B；当前到此停止。
