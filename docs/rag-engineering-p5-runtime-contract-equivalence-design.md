# P5 原查询契约到实验 Runtime 的语义等价性审计与最小适配设计

2026-11-07。**八个目标的原契约及确认依据完整保存在档案中，但现有实验草稿尚未形成完整的可执行语义表示，当前 Runtime 重解析也均不等价。** 八目标原则上可以通过“隔离的类型化目标规格 → 受检 Runtime 投影”表达；这项设计没有证明或签发任何本次可信绑定。当前可执行真实请求仍为 **0**。

本轮仅新增本报告。没有修改正式 Agent、reported_experience_v2、Runtime/B1/B2 Schema、旧契约、人工记录或任何历史实验；没有数据库、向量检索、Embedding、SiliconFlow 或其他外部 LLM 调用。27 行新四状态答案仍为空，8 个人工阅读组不可执行。`publication_allowed=false`、`semantic_verified=false`、正式许可集合为空。

## 1. 三个必须分开的结论

| 问题 | 本次结论 | 不能由此推导 |
|---|---|---|
| 原确认语义有没有留存？ | 有。八份 original_contract、问题、revision、确认事件及原理由与原会话、已发布参考一致 | 复制这些字符串已经得到可执行的等价 Runtime |
| 实验 Runtime 能否表达？ | 现有 RequiredFact/RequiredFacts/FactRelation 可承载事实、方向和关系；还需实验专用类型化条件及受检投影，八目标均为条件性可行 | 当前 build_query_plan 已覆盖这些目标，或裸 Runtime 类型本身能证明自然语言语义 |
| 当前是否取得可信范围/来源？ | 没有。旧 scope_ref 是历史运行引用；当前保存 scope/hash 是比较材料，缺本次 P0 来源签发 | SHA 相等、history_unproven 快照或人工确认能产生当前来源信任 |

旧的 `experimental_semantic_draft` 是字段副本：例如 Q09 主机/配件边界、Q05 安装日期不可替代收货日期等限制主要在原确认理由中，没有提升为执行条件。`aspect_candidates` 留在 original_contract，独立许可清单在旁字段，其他确认依据也在旁字段。**归档保留完整，不等于实验执行投影已完整。** 本报告补充这一区分，不回写旧预检或修改其状态。

确定性程序不能从任意自然语言和任意解释中自动证明逻辑等价。本设计只检查已审定的有限语义规格及其投影；自然语言到规格的解释仍须有原确认依据，并经过一次增量映射核对。它不裁决真实评论四状态。

## 2. 八目标逐项等价性矩阵

共同约束：原版本均为 `rag-query-contract-v1`、revision 1，`task_type=evidence`；顶层均为 **all_of**，没有顶层 any_of。`proposed_filters.aspect/sentiment` 全为 null；不因 aspect_candidates 或情感方向自动添加过滤。八目标 `independent_target_fact_ids=[]`；不新增子目标、统计口径、独立许可或必要事实 ID。

### 2.1 原问题、对象、事实与当前路由

| 目标 / 原始问题 | 对象 / subtopic | 必须完整保留的事实与方向 | 现行 Runtime 重解析 |
|---|---|---|---|
| Q02：有人说客服说话不耐烦、服务态度差吗？ | customer_support / attitude | f0 negative：客服不耐烦**或**态度差；这是单事实内部或关系。必须是客服角色，不能把普通回复慢、未解决问题自动当态度差 | unsupported / statistics / unsupported；对象、主题、方向 unknown；0 事实 |
| Q04：买完之后出了状况，一直没人跟进处理吗？ | after_sales / post_purchase_followup | f0 negative：购买后出现问题，且持续缺乏跟进。保留持续性；已有人处理但暂未解决，不等于无人跟进。安装问题若确有完整售后语义不被一概排除，但单纯安装慢不足 | unsupported / statistics / unsupported；对象、主题、方向 unknown；0 事实 |
| Q05：下单后很快就收到货的评价有哪些？ | delivery_service / order_to_receipt_speed | f0 positive：下单至实际收货的快速经历。安装快或由安装完成时间推定收货，不升级为直接收货证据 | unsupported / statistics / unsupported；对象、主题、方向 unknown；0 事实 |
| Q06：买家有没有抱怨迟迟收不到货？ | delivery_service / delivery_delay | f0 negative：长时间未收货**或**明显配送延误；普通尚未收货不能自动推出“迟迟”。保留到货时效，不替换成发货慢、安装等待或客服拖延 | fact_search / evidence / retrieval_only；对象、主题、方向 unknown；0 事实及规则未覆盖 uncertainty |
| Q09：用了不久就坏了或者发生故障的反馈有哪些？ | device / early_failure | f0 negative：**设备主机**实际开始使用不久后损坏或故障；不自动含遥控器等配件，不含尚未使用、收到即坏；泛质量差和无时间故障不足 | unsupported / statistics / unsupported；对象、主题、方向 unknown；0 事实 |
| Q11：炎热时开机，屋里很快就凉下来了吗？ | device / hot_weather_cooling_speed | f0 positive：同次体验中有明确炎热条件、开机后室内降温、速度快。夏季不自动等于炎热；制热、冷风或一般效果好不能补齐条件 | unsupported / statistics / unsupported；对象、主题、方向 unknown；0 事实 |
| Q12：开了很久还是不凉快，有这样的评价吗？ | device / prolonged_operation_without_cooling | f0 negative：持续运行较久后室内仍无有效降温；长时间与降温不足须共同成立，不以制热、短时尚未冷或仅风不凉代替 | unsupported / statistics / unsupported；对象、主题、方向 unknown；0 事实 |
| Q18：货到了却迟迟没人来安装，这是哪类反馈？ | installation_service / installation_after_delivery | all_of：f0 neutral 已到货；f1 negative 到货后长时间无人安装；必要 same_review(f0,f1)、before(f0,f1)。分类是原对象/主题的解释，不另添分类事实 | opinion_summary / evidence / supported；对象 review_opinion、主题为安装、方向 unknown；0 事实、0 关系 |

最后一列依次为 operation / task_type / support_status。这里的 statistics 是当前不支持路由的类型结果，不代表原问题实际上变成统计问题。Q18 的 supported 字面值同样不能证明目标已表达。

### 2.2 否定、澄清、非数值条件及确认依据

本表是契约技术摘要，不复制私有逐评论评审理由或生成新候选标签。

| 目标 | polarity_structure | uncertainty / clarification 的实际意义 | 原确认指针 / 等价设计结论 |
|---|---|---|---|
| Q02 | 无 negation、contrast | 均为空；保留客服行为内部或关系及角色边界 | E0082；可类型化，不拆 f0 为两个必需事实 |
| Q04 | f0：没人，作用域为跟进处理 | 均为空；持续性是必要自然语言条件，无固定天数 | E0085；可类型化，不能删除问题发生及持续无人跟进的合取 |
| Q05 | 无 negation、contrast | clarification 问间接安装时间是否等同直接收货；原确认已区分，不能重新作为开放解释任意选取 | E0051；可复用原裁决，需把直接性边界写入规格 |
| Q06 | f0：收不到，作用域为货物到达 | 原 uncertainty/clarification 均为空；延迟无需统一天数。否定作用在未收货分支，不把“后来已收到但明显延迟”从另一分支删掉 | E0089；可类型化内部或关系及分支否定 |
| Q09 | 无 negation、contrast | clarification 的配件、收到即坏边界已由原确认回答；不应再次擅自扩大对象 | E0036；可复用原裁决，仍需检查类型化映射完整 |
| Q11 | 无 negation、contrast | uncertainty 是保留炎热/快速且不设数值阈值的说明；原确认已给出适用边界 | E0092；作为保留条件，不因字段名叫 uncertainty 就自动丢弃或重复询问 |
| Q12 | f0：不，作用域为凉快 | 遗留 uncertainty 写有时长待确认，后续原确认已规定自然语言持续运行、无固定小时阈值 | E0095；保留旧文本及已解决依据，不能自动删除时长 |
| Q18 | f1：没人，作用域为到货后安装 | 遗留 uncertainty/clarification 涉及分类；原确认明确 evidence、两事实及双关系，不新增分类金标准 | E0064；分类解释不拆成另一个可许可目标 |

八目标 contrast_fact_ids 均为空。negative 是目标事实方向，不能把“目标具有否定表达”当作模型应返回 refuted：负面事实仍可能得到 supported。

原确认中的未解决提问和已解决说明不能混为一类。未来规格应记录 `constraint_note`、`resolved_by_existing_confirmation`、`unresolved` 三种情况及各自原指针；发现解释冲突或无法确定时保持 unresolved，不把所有非空文本机械清空以通过 B1。

### 2.3 三轴审计结论

| 目标 | 原语义归档 | 现有草稿的可执行完整性 | 等价表达可行性 | 当前可信绑定 |
|---|---|---|---|---|
| Q02 | 保留 | 未证明，内部或/角色边界未形成执行约束 | 受检规格可表达 | 无 |
| Q04 | 保留 | 未证明，持续无人跟进条件需显式保留 | 受检规格可表达 | 无 |
| Q05 | 保留 | 未证明，直接收货与安装推定边界未投影 | 受检规格可表达 | 无 |
| Q06 | 保留 | 未证明，延迟分支与否定作用域需保留 | 受检规格可表达 | 无 |
| Q09 | 保留 | 未证明，主机和使用后边界不能只留在旁注 | 受检规格可表达 | 无 |
| Q11 | 保留 | 未证明，天气/速度/同次体验条件需保留 | 受检规格可表达 | 无 |
| Q12 | 保留 | 未证明，持续运行与室内降温合取需保留 | 受检规格可表达 | 无 |
| Q18 | 保留 | 未证明，双事实、关系、分类解释需整体投影 | 现有关系类型可表达，但关系语义仍独立验证 | 无 |

没有发现必须换题才能表达的目标，也没有目标在本轮被宣布“已经等价通过”。任何实现若无法保留表中条件，该目标继续 blocked，不能替换为一般客服差、配送快、质量差或制冷好。

## 3. 根因、代码定位与证据链

| Evidence：只读依据 | Finding：阻断根因 | Path：设计上的处理 |
|---|---|---|
| [build_query_plan](../src/data_analysis_agent/agents/review_plan.py#L80)、[wants_semantic_search](../src/data_analysis_agent/agents/review_retrieval.py#L30) | 路由只识别有限表达；原确认并不是当前规则解析器的输入 | 保留正式路由；新增隔离实验来源，不修改/覆盖旧 plan |
| [build_task_contract](../src/data_analysis_agent/agents/review_task_contract.py#L115)、[v2 target](../src/data_analysis_agent/agents/review_reported_facts.py#L54) | target grammar 及事实模板有限，不能把本轮目标的对象、内部合取/析取和自然时间条件完整生成 | 为八个原确认目标设计受限类型化规格，不扩展通用解析器 |
| [QueryContract](../src/data_analysis_agent/reviews/query_contract.py#L77)、[RuntimeQueryContract](../src/data_analysis_agent/agents/review_task_contract.py#L79) | Runtime 有事实/方向/关系，却没有 polarity_structure、澄清、旧 scope_ref、独立许可清单等同名槽位 | 保留完整实验规格；向已有 Runtime 槽位做可核对投影，不能简单删字段 |
| [prepare_input](../src/data_analysis_agent/agents/review_structured_evidence.py#L122) | B1 要求 fact_search/evidence/supported、明确事实、无未解决 uncertainty/statistics、范围/问题匹配及 P0 seal | 逐项满足，失败关闭；不放宽 not_eligible 或直接调用内部 resolve_output 绕过准备 |
| prepare_input 第 125 行、[manual replay](../src/data_analysis_agent/reviews/query_contract.py#L167) | trusted_contract_sha256 由调用方提供，现有比较证明内容一致，不自动证明语义等价；手工回放也明确不证明人工语义正确 | 实验调用方只能消费等价编译器的受检对象，摘要最后计算，不收用户自报可信 SHA |
| [P0 binding](../src/data_analysis_agent/reviews/semantic_retrieval.py#L147)、[source checks](../src/data_analysis_agent/reviews/semantic_retrieval.py#L220) | 来源 seal 依赖拥有者身份及完整结果完整性；JSON、哈希或旧确认不能自行建立 | 未来由已有 P0 拥有者经真实来源核验签发；本轮没有该绑定 |
| [search](../src/data_analysis_agent/reviews/semantic_retrieval.py#L300)、[load_review_contexts](../src/data_analysis_agent/reviews/semantic_retrieval.py#L345)、[corpus reader](../src/data_analysis_agent/reviews/rag_corpus.py#L141) | 当前只有检索成功路径签发；限 ID 正文读取不含完整观点，而 corpus 读取范围过大 | 限 ID 来源接口是独立依赖，不能借正式检索/假编码器代办 |
| [audit_output](../src/data_analysis_agent/agents/review_structured_evidence.py#L234) | B2 检查矩阵、切片、见证关系；before 只要求合法证据，不能理解自然语言时间或充分性 | 结构结果与语义裁决分开；不能把结构通过当作等价或语义验证 |

[matches_saved_task_contract](../src/data_analysis_agent/agents/review_task_contract.py#L170) 只是已有 Runtime 记录的兼容比较，不是旧 QueryContract 到新 Runtime 的语义转换器；[task_matches](../src/data_analysis_agent/agents/review_task_contract.py#L198) 是正式工具/许可结果检查，也不能被调用成独立实验的信任发行者。

## 4. 最小实验适配方案

推荐未来只增加一个独立实验模块及对应合成测试/夹具，暂不增加运行命令、数据库入口或新的 LLM 输出 Schema。本文提议的模块名为 `experiments/text_evidence_runtime_contract.py`，**目前不存在、未实施**。

实验应消费“原已确认 evidence 契约”，而不是消费当前 unsupported Runtime 再改字段。对同一句原问题，可产生一个带明确来源说明的全新实验 Runtime；当前正式 Runtime 的 operation/support_status 和正式回答保持原样。实验不声称正式规则已支持该问题，也不允许把真实统计、检索辅助或因果任务改成 evidence。

### 4.1 可复用结构和拥有者

| 复用项 | 复用方式 / 边界 |
|---|---|
| QueryContract / RequiredFact / RequiredFacts / FactRelation / PolarityStructure / ScopeRef | 读取、严格解析、检查原语义和引用；不修改原模型 |
| validate_session、已发布 ReferenceRelease | 只读验证确认链、revision、原问题和契约一致；不调用发布函数、不新造确认事件 |
| RuntimeScope / RuntimeQueryContract | 最终兼容载体；保留原问题、fact ID、事实方向、all_of 和关系次序 |
| prepare_input / model_payload / audit_output | 完整复用 B1/B2 原边界，不新增旁路输入或替代校验 |
| Text Evidence 新输出契约、locate_fragment、TextEvidenceShadow | 延续精确文本→程序坐标→B2；不更换提示/输出 Schema 来掩盖契约缺失 |
| semantic_retrieval 拥有者的来源检查和签发 | 只负责真实范围/来源；不替代语义等价审计或人工金标准 |

### 4.2 实验专用类型化表示

只增加本地后端规格，不送给 LLM 作为新输出 Schema。建议一个 `ExperimentGoalSpec` 和一个仅由编译器产生的不可变 `CheckedExperimentTarget`；条件表达式作为前者的受限嵌套结构，不发展成通用规则引擎。

| 规格部分 | 必要内容 / 不变量 |
|---|---|
| origin | 原 query、question、版本/revision、原确认事件及内容摘要、原契约/确认依据的指针。摘要用于定位/防漂移，不能自证语义 |
| facts | 每个旧 fact ID 的原说明、sentiment、受限语义树；顶层 operator 原样保留，内部 and/or 不新增 B1 fact ID |
| entity / predicates | 客服、安装、配送、售后职能、设备主机等目标；未知目标拒绝。候选对象未知是后续四状态问题，不能由规格猜角色 |
| conditions | 直接收货事件、开始使用后、早期故障、炎热环境、室内降温、持续运行、同次体验等必要条件；自然语言限定不转换为任意数值阈值 |
| polarity | 原 cue/scope/fact 绑定及分支作用域；否定条件不等于 refuted 输出，不把 cue 变成必须逐字出现的词匹配 |
| relations | 精确保留 kind、fact_ids 及顺序。Q18 固定 same_review 和 before(f0,f1)，反转 before 必须失败 |
| resolutions | 原 uncertainty/clarification 全文保留在私有规格，逐项归为约束说明、原确认已解决、仍未解决，并绑定具体确认依据；不得凭空产生 resolution |
| filters / context | filters 仍为 null；aspect_candidates 仅提示元数据；全文只解释或否决 quote，不能补支持；不跨评论拼接缺失条件 |
| permission | 独立许可为空；所有必要事实和关系缺一不可；Shadow 永远不能授予正式许可 |
| scope bridge | 历史 scope_ref、原冻结范围的可追溯指针，以及未来独立获得的当前授权范围/来源观察；两种身份不混用 |

八个事实规格的核心形状：

| 目标 | 同一旧 fact 内保留的结构，不增加或减少目标 |
|---|---|
| Q02 f0 | 客服角色 AND（不耐烦 OR 明确负面沟通态度），不能改成两项皆需 |
| Q04 f0 | 购买后问题 AND 持续缺乏售后跟进；已经处理但未解决不能替代持续缺乏跟进 |
| Q05 f0 | 实际收货时效 AND 从下单起的快速评价；排除用安装日期推定直接收货 |
| Q06 f0 | 长时间未收货 OR 明显到货配送延误；保留两分支的时间语义及未收货分支的否定范围 |
| Q09 f0 | 设备主机 AND 已开始使用 AND 使用早期 AND（损坏 OR 功能故障）；排除配件/使用前损坏 |
| Q11 f0 | 明确炎热 AND 开机后室内快速降温 AND 同次体验；不从季节、冷风或全文补天气条件 |
| Q12 f0 | 连续运行较久 AND 之后室内仍无有效降温；不把风温、制热和短时运行当同一命题 |
| Q18 | f0=已到货；f1=到货后长期无人安装；保持 all_of、两方向、同评论、到货先于安装等待；不增分类事实 |

这些是待实现和核对的有限规格，不是已经通过证明的机器判定结果。尤其不能把整个目标压缩成如 cooling_poor 的 v2 fact ID，或将 Q18 简化成“安装慢”。

### 4.3 真正的等价校验与 Runtime 投影

建议以以下七步构成唯一实验适配入口，任一步失败均不产生可执行目标：

1. **验证原输入身份。** 从本次实验固定的可信本地来源配置加载原会话/发布参考，而不是从待审输入获取 expected_scope/摘要。校验确认链、原问题、query、revision、事件与原契约；未知目标、未确认、漂移或冲突立即拒绝。
2. **核对有限语义规格。** 为原事实、每个必要条件、内部 and/or、否定作用域、关系、方向、澄清与许可建立覆盖清单。每项须有原字段或原确认依据；任何未覆盖、额外条件或冲突均拒绝。指针正确不自动证明解释正确，需要一次针对八目标规格差异的人工核对；不重复审批 48 组旧评审、不生成四状态答案。
3. **进行图结构及变异校验。** 比较审定规格和待生成目标的事实图，要求 fact ID 集合、操作符、分支、角色、时间/环境条件、方向及关系边精确对应。正反向检查无遗漏、无额外必要条件；非数值限定不得变为阈值。只复制原 description 或比较两个 hash 不满足这一步。
4. **使用确定性 renderer 生成已有 Runtime 字段。** target_entity、subtopic、sentiment、RequiredFacts/relations 保留；fact.description 包含原事实及经确认的完整条件/排除替代，而非只保留短标签。Runtime 没有独立槽位的必要语义必须在描述中可见，后端仍保留类型化树。通过固定格式反向解码/字段覆盖检查确认投影完整；该闭环只证明渲染无损，不证明任意自然语言等价或模型一定理解。
5. **显式处置澄清。** 原非空文本不删除。只有对应条目确由原确认解决或属于保留条件，且已投影，才不再是新 Runtime 的 unresolved uncertainty；仍有 unresolved 则不产生 eligible Runtime。Q05/Q09/Q11/Q12/Q18 不因旧字段遗留而重复提问，但映射若仍不清楚必须阻断，不能无条件设 uncertainty=()。
6. **连接独立可信 scope。** 离线阶段只返回“可表达但未绑定”的 CheckedExperimentTarget 草稿及差异；没有可信范围时不生成可执行 Runtime。未来 P0 拥有者核验真实来源后，比较授权范围、历史范围桥接、原问题、候选集合及上下文政策，才生成新的 Runtime 与受检对象。对象中保存完整规格和投影，不能从外部 JSON 的 eligible=true 或用户提供的 SHA 恢复信任。
7. **最后派生支持状态和摘要。** 实验 support_status 由已确认 evidence 任务、全条件覆盖、无 unresolved、无统计/因果限制、完整投影及合法范围等前提派生；不是按 QID 硬编码 supported，也不是沿用 Q18 旧 supported。`trusted_contract_sha256` 由受检对象内的最终 Runtime 计算，实验调用方不接受外部“可信 SHA”。然后原样经过 B1 prepare_input、Text Evidence、B2，不直接使用内部 resolve_output 充当来源准入。

CheckedExperimentTarget 是进程内的受检返回值，可沿用拥有者/内容完整性检查思路；普通 dict 或重新序列化载入的声明不能直接充当它。它只证明实验目标编译与既定范围连接，**不取代 P0 seal，不是人工金标准，也不是正式发布许可**。不新增审批服务或历史封存体系。

现有 `Text` 上限是 **1000 字符**（[定义](../src/data_analysis_agent/core/validation.py#L9)）。renderer 必须在该限制下完整表达；超过限制则阻断，不截断必要条件。新描述增加 token，需要在未来固定输入时重新预算，不能因为 Schema 没变就沿用旧输入估算。输入≤8000、输出参数2500、候选≤5、最多8次的既定边界不扩大。

## 5. P0 可信绑定边界与依赖

### 5.1 范围、内容与语义分别验证

| 校验 | 谁负责 | 本轮能做 / 尚缺什么 |
|---|---|---|
| 原 scope_ref → 原 manifest/run/scope | 原冻结材料及只读校验 | 可追溯旧实验范围；不能证明当前数据库状态 |
| 当前 RuntimeScope 参数有效 | RuntimeScope / _scope | 可离线校验结构、单商品、UTC 日期等；传入有效参数仍不是来源信任 |
| 历史范围与新授权范围语义一致 | 实验适配边界 + 已核验范围依据 | 比较 source/dataset/product、日期、as_of、annotation model/base_url/prompt/profile；不是比较两个不同含义 scope_sha256 字符串 |
| 全文、quote、完整当前有效观点与导入身份 | 真实只读仓储 + P0 拥有者 | 仍缺最终至多20个 review_id 的真实来源重验；不能从现有快照补造 |
| P0 内存来源 seal | semantic_retrieval 拥有者 | require_source_validated_result 要求拥有者身份与完整性；目标适配器不能构造 issuer 或自行签发 |
| 目标与候选语义、关系及充分引用 | 独立人工标准及后续评估 | B1/B2 不提供自然语言蕴含证明，27 个待审事实行仍为空 |

绑定必须包含原问题的逐字身份、原 fact ID、范围、候选有序集合、观察快照、内容与标注版本。不能取一个已签发结果，修改 query/scope/evidence 再保留旧 seal；这会破坏 `_result_integrity`。旧 scope_ref 不替代 annotation profile、导入信息或本次只读事务。

当前 P0 seal 是特定观察结果的完整性证明，没有自动回查数据库的能力，也没有通用 TTL 或跨实验用途授权检查。未来实验只接受本次拥有者来源核验上下文签发的视图；旧运行的 seal 即使内容未变也不能自动带入新轮次。过期上下文、不同授权集合、快照不一致、失效来源或校验后内容变化均阻断；无法得知观察之后的数据库变化时，明确只证明观察时点，不能声称持续实时有效。

### 5.2 限 ID 来源路径是独立接口缺口

本轮不实现该路径。下一阶段应先由 P0 拥有者审查最小只读接口，例如“核验已冻结历史候选选择”，输入限最终 ID、固定 scope、原问题/选择依据与候选身份；读取完整当前有效观点，在同一只读 repeatable-read 快照内复用 `_document`、`_review_state`、`_verified_sources` 及快照检查，全部成功才签发本次内存绑定。来源变化拒绝，不自动更新金标准。

还须解决现有接口的一个表示限制：B1 接受 `SemanticResult`，其中 evidence 要有连续 rank、cosine_distance、query 和 snapshot。**单纯限 ID 读取没有产生新的向量距离或 Top-K，不能填 0.1、复制假编码结果，或把旧数据反序列化后补 seal。** 推荐的兼容设计是：

- 拥有者在独立实验接口返回一个有明确 provenance 的受检选择视图，区分“本次来源核验”和“历史候选选择/距离”。若投影到现有 SemanticResult 兼容载体，必须由拥有者新建并签发，不由实验模块写私有 proof。
- 历史 rank/mode/距离原值保存在原选择元数据并校验来源；如兼容载体确需距离，只能使用可追溯的真实历史值并明确非本次检索。不存在可用值时继续阻断，不能补假值。B1 model_payload 本就排除距离。
- 兼容载体连续 rank 只能定义为新评审视图的传输序号，必须与不可变评审索引及全部原 U/F rank 建立对应；不回写、不重新排序原 Top-K，不把新序号用于检索指标。不得把原非连续 rank 直接塞入 B1，也不得暗中改写历史 rank 以过检查。
- 原始 provenance 必须在拥有者返回视图及实验受检对象中一并绑定，不能只由不可信调用方声明。若无法在保持 B1/P0 边界的前提下清楚表达上述来源，真实执行继续 blocked；不能为了复用接口把来源核验伪装成新检索。

这是一项待审查的拥有者扩展，不是现成接口，也不承诺当前代码已支持。正式 search 行为、source_policy、P0 验证要求和 B1/B2 Schema 均保持原样。需要的源核验仍只限既有20个暂选 review_id 及其完整当前有效观点；不扫描全商品、不生成/读取向量、不调用编码器、不写数据库。模型不在数据库事务内运行。

## 6. 失败关闭、旧路由兼容与回退

| 阶段 / 失败 | 实验行为 | 正式行为 |
|---|---|---|
| 原确认身份、revision、scope_ref、目标不在八项内 | 拒绝适配，不返回 eligible 目标 | 原 build_query_plan 和澄清/不支持行为保留 |
| 漏掉对象/时长/内部或/极性/关系，添加新条件或解除独立许可 | 等价失败，不生成 Runtime；不能换简单目标 | 不影响 v2 |
| 澄清冲突或必要语义无法在1000字符等限制内投影 | 保持 unresolved/blocked | 不以 LLM 猜目标补洞 |
| scope 未绑定、跨商品/日期/profile、来源漂移/失效、假 seal | B1前或原B1拒绝，零模型发送 | 不把实验数据交给正式回答、缓存或许可集合 |
| 位置歧义、来源不一致、B1/B2失败 | 保存既定受限诊断；正式准确率N/A | 不替换 v2 引用或事实 |
| 模型超时、错误语义或usage不明 | 按原冻结停止/记录策略，不重试不补跑，不改预期 | 保留 v2 原行为；不把执行失败改成 unknown 成绩 |
| 结构和语义观察均通过 | 仅独立实验观察；三个许可保护仍关闭 | 不接同步问答、不自动正式集成 |

实验 Runtime 不进入正式 task_matches、原任务记录或 answer 决策，不能通过重新注册正式工具达到兼容。这个阶段的“回退”就是没有接入正式路径：实验失败也不触发额外检索、SQL 或新模型请求。

## 7. 合成离线验收设计

以下是**下一实施阶段的测试方案，尚未新增或执行新测试**。夹具只含自编测试问题、虚构评论/事件和明确标记的合成来源，不使用当前20条真实评论，不修改27行新金标准。合成标签属于测试 oracle，不是模型语义成绩。

建议采用参数化规格变异测试及明确时间/角色的合成事件夹具：先写完整规格，再逐项删除、反转或替换；正确规格应可得到“离线可表达”，任何缺少必要条件的变体均失败。缺少 P0 时，“离线可表达”仍不得提升为可执行。

| 测试组 | 可执行检查 / 预期 |
|---|---|
| T01 八目标完整性 | 对八规格逐一检查旧 fact ID、方向、all_of、内部 and/or、对象、条件、原依据、许可不变；renderer往返后图相同。每个必要节点至少有一个删除/替换变异，全部拒绝 |
| T02 客服对象不同/未知 | 目标 customer_support 被改成 installer 或 unknown：前者不等价、后者目标未解决。合成候选明确为安装员与候选角色未知分别保留为不同测试输入，不让编译器猜出真实标签；Mock 的候选 unknown 不得取得支持许可 |
| T03 Q02/Q06 内部或 | 单分支和另一分支均保留表达机会；把单事实内部 OR 改 AND、拆成两个必需 fact、或把未收货否定套到全部延迟分支均失败 |
| T04 Q04 跟进持续性 | 删除购买后问题或持续无人跟进节点、用“尚未修好”替换“无人跟进”均失败；不设固定天数；不把安装问题整体排除为另一主题 |
| T05 收货/安装速度 | 将 receipt_event 换成 installation_event 或用安装日期推定直接收货，等价检查失败。合成 Mock 引用即使逐字合法，若只证明安装快，仍不能被测试 oracle 判为语义充分 |
| T06 早期故障 | 主机换配件、使用后换使用前、早期条件删除、损坏/故障换泛质量评价均失败。原已经解决的配件边界不再被改成开放范围 |
| T07 制冷/制热/炎热/时长 | Q11 改制热、删除炎热、以夏季推炎热、删除速度或同次体验均失败；Q12 删除长运行、只留冷风温度、改短时未冷均失败；自行加入分钟/小时阈值同样失败 |
| T08 Q18 事实和关系 | 少 f0/f1、改方向、all_of→any_of、删 same_review、删/反转 before、添加独立许可均失败；完整同评论支持的双事实可进入结构测试，但仍无正式许可 |
| T09 Q18 见证与时间 | 两事实来自不同评论，原B2应拒绝 cross_review_relation；有 same_review、缺 before 引用应拒绝 temporal_evidence_missing；只有一个事实支持不能满足整体。文本出现顺序不等于事件先后，不能用字符下标或评论发布日期证明 before |
| T10 结构合法但语义错误 | 合成输出引用位置合法却只覆盖部分条件，或 before 引文语义反向：允许原B2给出其实际结构结果，但测试必须保留 semantic_verified=false；由合成事件 oracle 检出语义不足，不能篡改B2以声称已自动理解时序 |
| T11 澄清和来源依据 | Q05/Q09/Q11/Q12/Q18 的已解决说明保留原指针；没有真实对应确认、未解决条目被清空、未知约束被忽略、伪造reviewed=true均拒绝 |
| T12 scope 身份 | 改source/dataset/product/date/as_of/profile/annotation identity、原scope_ref冒充Runtime scope、baseline扩展或原问题变更均拒绝；相同hash形状不代表相同含义 |
| T13 缺失/失效绑定 | 普通JSON、自报SHA、错误issuer、旧运行上下文、校验后变更全文/quote/完整观点/顺序或快照均拒绝。合成拥有者夹具只用于测试；不得把Fake/Synthetic与真实文本混用 |
| T14 引用与独立许可 | support/refutation限quote，context_veto限同全文；重复/重叠、无匹配、身份或哈希漂移沿用定位失败关闭。部分 model_supported_fact_ids 不能成为独立许可，任何发布true/非空许可构造应失败 |
| T15 接口和旧功能隔离 | 正式八原句路由与本次只读结果逐项相同；已有受支持规则句、statistics、retrieval_only、unsupported、v2 assessment/回答及保存兼容行为不变；禁止正式Agent导入新实验模块 |
| T16 边界和资源 | 超过描述1000、候选5、既定输入/输出限制或请求范围即拒绝，不截断；适配失败发生在任何发送前。Mock超时/取消沿用现有Worker回收，零重试、零追加请求 |

验收必须分别报告：规格覆盖/变异拒绝、Runtime 投影、P0 输入、B1/B2、引用定位、语义测试 oracle。不能用一项“测试全过”代替这些层次，也不能把合成 Mock 判断计入真实业务准确率。

实现后应补一份独立测试文件（拟名 `tests/test_p5_runtime_contract_equivalence.py`），并复用现有的 `test_review_task_contract.py`、`test_query_contract.py`、`test_p5b1_structured_evidence_design.py`、`test_review_structured_shadow.py`、`test_shadow_text_evidence_prototype.py` 和 `test_semantic_retrieval.py` 中对应测试。涉及仓储的测试仅用明确虚构的受控夹具，不触碰真实数据库；真实服务连接、网络、检索和编码入口必须拦截。本轮不运行这些可能创建测试数据库/Worker的套件。

拟新增测试实施后可执行的命令（**当前文件不存在，不是本轮已通过的命令**）：

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_p5_runtime_contract_equivalence.py -v
```

## 8. 工作量、风险与推荐实施顺序

以下为设计估算，不是工期承诺，也不包含真实评估执行。

| 顺序 | 最小工作 | 粗估开发量 / 停止门槛 |
|---|---|---|
| 1 | 一个独立实验模块：八目标类型化规格、依据覆盖、澄清状态和差异输出 | 约0.5–1人日；不生成真实Runtime/请求，先核对八项语义映射 |
| 2 | 同模块的等价校验、无损投影及独立合成测试/夹具；保持正式路径隔离 | 约1–2人日；全部必要节点变异拒绝、旧回归通过，只得到离线可表达结论 |
| 3 | P0 拥有者限 ID 来源接口与历史选择 provenance 的兼容设计/测试 | 另约1–2人日，受接口表示风险影响；未审清楚不实现真实入口、不签发来源 |
| 4 | 对等价目标增量核对新人工旁表；冻结后再分别考虑来源读取与模型授权 | 人工时间另计；上限27个事实裁决及Q18的6个关系检查，不由Codex代填 |

首个下一实施阶段推荐只做 **1–2**，预计约1.5–3人日，交付仍纯离线，不实施数据库或模型任务入口。第3项依赖单独审查，不能把其工作隐含在“字符串适配”中。

主要风险和处理：

- **自然语言解释遗漏。** 原确认理由比短事实描述更具体；覆盖指针、变异测试和增量人工映射核对缺一不可，不能宣称形式化程序已证明任意中文含义。
- **隐性改题。** 删除天气/时长、排除合理售后场景、将内部或改成顶层多事实都会改变目标；失败就减少合格目标，不放宽问题。
- **描述投影损失与预算。** 类型化约束未进入模型可见描述，等价编译仍失败；字符/token不足时阻断，不扩预算、不删条件。
- **误把兼容载体当新检索。** 来源核验与历史排名的 provenance 是 P0 适配前提，不伪造距离/序号语义；现有接口无法表达时继续阻断。
- **把结构当语义。** 目标适配解决“问什么”，不是“模型答对没有”；refuted/unknown、充分引用和时序仍须独立标准。
- **成功原文留存缺口。** 未来真实质量调用前仍须完成前计划要求的成功原文受限留存/摘要与重解析；本设计不修改旧T组数据或顺带实现留存路径。

## 9. 下一阶段准入条件及分别授权

下一轮纯离线适配实施可开始的最小范围：仅八目标、既有候选/预算、一个隔离实验模块与合成验收；不修改正式路由、v2、B1/B2 或旧记录。只有完整语义规格及投影通过，才可将对应目标从 pending_contract_equivalence 推进到四状态人工评审准备，不能直接推进到来源/模型执行。

需要人工核对的是 **新增类型化映射是否忠实保留八个原确认目标**，以及之后的 **27 个新四状态事实裁决和Q18必要关系**。Q05收货/安装、Q09主机/配件、Q11/Q12自然条件、Q18分类解释已有原确认依据，不能当作从未回答过的问题重新全量审批；只对新规格解释冲突或遗漏增量确认。用户的映射核对不等于填好候选金标准。

进入真实来源核验前，必须先审查并测试由 P0 拥有者实现的限 ID 路径、范围桥接、历史选择 provenance 和失败关闭，再单独授权最终保留的至多20个 review_id 及其完整当前有效观点的只读字段。没有这项授权，不读取数据库；来源漂移时保持阻断，不自动改参考。

进入真实模型评估前，还必须有：可信 Runtime 与本次来源绑定、独立人工标准冻结、成功/失败原文可复核、完整预算预检及私密性检查，以及针对真实评论发送和实际费用风险的独立付费授权。仍最多8个既定请求、每组≤5、输入估算≤8000、输出2500、零重试；本轮没有产生任何可发送模型请求。

## 10. 本轮只读核验与交付边界

只读完成以下核对：

- 原 session/bundle/已发布参考三份固定字节摘要匹配；原 validate_session 通过，ReferenceRelease 可解析。八份映射的原契约、revision、事件、理由和独立许可与原会话及发布参考一致。
- 对八原问题使用保存 scope 纯离线重解析，8/8 与保存 Runtime 相同。网络、psycopg、OpenAI client、BGE及真实语义检索入口均拦截；该比较不产生可信 scope。
- review-sidecar 仍为27行、expected_status全null、human_confirmed/review_enabled全false；review-groups仍为8组、request_body=null、executable=false。未读取真实评论来制定新答案。

| 私有依据 | 本轮读取时的 SHA-256 |
|---|---|
| contract-mappings.json | `2c283ab88f913c8cdaf41d6010e978ede137081d767fe1eb6eaddc762ef65689` |
| review-sidecar.json | `0f9f5b2927b33595080a494037023c9f019ff3cabaf10d059956e489ef91703f` |
| review-groups.json | `c75a2e90218ed6aba59a98f7a0c0bf5c19120155d800082a96c0281b12554c4c` |

私有文件位于原 Git 忽略目录，不复制真实全文、候选ID/key、敏感来源字段或人工私有逐评论评审进入本报告。受保护文件使用获准的只读访问，没有修改 ACL。原确认依据只按目标技术约束概括，原记录不重发、不覆盖。

本轮未实施适配器、未新增测试、未执行拟定合成套件；只读一致性检查不是适配验收通过。开始记录的399个既有源码/脚本/测试/文档/实验文件、原私有目录11个文件及三份原人工确认材料在结束时摘要复核保持不变；仅本报告为新增交付。17处本地代码链接与行号范围、文档空白及 `git diff --check` 检查通过。

离线复核三份材料摘要可由文件拥有者运行以下只读命令，不调用服务：

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath outputs/rag-semantic-retrieval/real-evaluation-review-preflight-v1/contract-mappings.json
Get-FileHash -Algorithm SHA256 -LiteralPath outputs/rag-semantic-retrieval/real-evaluation-review-preflight-v1/review-sidecar.json
Get-FileHash -Algorithm SHA256 -LiteralPath outputs/rag-semantic-retrieval/real-evaluation-review-preflight-v1/review-groups.json
```

完成本设计后停止，不启用人工评审门、不生成实际批次、不申请笼统的数据或模型执行许可。
