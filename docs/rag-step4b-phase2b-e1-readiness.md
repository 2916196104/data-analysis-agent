# RAG Step 4B-2b：E1 输入准备与人工充分性评审协议

日期：2026-11-04（Asia/Taipei）。**本阶段完成准备工具、协议模型和合成验证；当前不能进入真实 E1，等待人工审核后停止。**

现有冻结输入仍为 24 题、48 个 query-mode、222 个排名条目；未改写原相关性评分或备注。冻结产物缺少判断器所需的逐观点内容版本哈希，真实来源恢复尚未获本轮授权；人工查询契约、充分性参考、上下文政策与质量门槛也未确认。因此本报告不是 E1 结果或语义质量验收结论。

## 1. 阅读与实际交付

阅读了 [Step 4A 最终审查](rag-retrieval-quality-final-review.md)、[Step 4B 计划](rag-step4b-retrieval-strategy-plan.md)、[4B-0](rag-step4b-phase0-readiness.md)、[4B-1a](rag-step4b-phase1a-contract.md)、[4B-2a 判断器报告](rag-step4b-phase2a-evidence-judge.md)，检查 QueryContract、EvidenceBatch、来源版本键、离线评分、冻结 manifest 和原归档审计机制。

| 新增文件 | 内容 |
|---|---|
| [e1_preparation.py](../src/data_analysis_agent/reviews/e1_preparation.py) | 库式离线准备工具；用冻结列表和外部固定哈希约束的独立来源集合构建 trusted_batch，不运行判断器 |
| [e1_review_protocol.py](../src/data_analysis_agent/reviews/e1_review_protocol.py) | 人工参考结构、确认/裁决校验及 A/B 人工评审视图；不计算真实实验指标 |
| [test_e1_readiness.py](../tests/test_e1_readiness.py) | 29 项完全合成测试 |
| 本报告 | 字段差异、来源与授权方案、人工协议、上下文设计、指标和审批项 |

没有修改任何已有源文件、测试、配置、判断器行为、检索/排序、Embedding、数据库、Agent、API 或前端。本轮私有材料在 `outputs/rag-retrieval-quality/step4b-phase2b-20261104/`。没有真实 SourceCollection、PreparedBatch、人工参考答案或 E1 预测产物。

## 2. 冻结输入与判断器字段差异

实际只盘点既有归档的字段名、数量、身份和哈希，不导出评论正文或逐条标签。222 个 hit 的共同字段有 19 个，其中 cosine_distance 不属于判断器输入，故 **18 个可直接用于 EvidenceCandidate**。该模型共需 28 个字段，另外 10 个不在 hit 行内；不能把“父级可推导的期望值”混同于“独立核验的逐行来源”。

| 目标字段 | 冻结来源 / 可获得性 | 准备时的证明要求 |
|---|---|---|
| rank、key、review_id、annotation_id | hit 直接保存 | 原 run_id 及有序列表绑定，不能换序、增删或跨 query/mode 借用 |
| opinion_source、model_opinion_id、human_opinion_id、human_revision_id | hit 直接保存，null 也必须原样保留 | 与独立来源的互斥身份、修订版本一致 |
| aspect、sentiment | hit 直接保存 | 仅为原标注元数据，不转为语义支持结论 |
| quote、quote_start、quote_end、review_text | hit 直接保存 | quote 等于原文字符切片；原文/片段与独立来源一致，不 trim、改写或补上下文 |
| embedding_input_sha256 | hit 直接保存 | UTF-8 quote 哈希及独立来源均相符；不是 review_content_sha256 |
| effective_snapshot_sha256 | hit 直接保存 | 必须与对应历史评论的有效观点快照核验，不以当前已漂移快照替代 |
| evidence_ref、evidence_valid | hit 直接保存 | ref 与独立文件哈希/行号一致；冻结 true 只证明保存运行的校验状态，不证明当前在线新鲜性 |
| query_id、mode | 父级 search 直接保存 | 给每行赋所属列表身份，不能从其他列表选择“更好”的候选 |
| source、dataset_version | manifest.scope 与 inventory.scope | 获得授权范围期望值；逐行还须与来源记录相等 |
| product_id | 当前冻结 scope 仅含一个商品 `100013588055` | 可确定范围期望值，但不能把它当独立逐行来源证明；多商品情况不能用此推法 |
| label_profile_key、text_builder_version | manifest/inventory | 与独立观点身份、原范围、空间版本一起验证 |
| file_sha256、line_number | 可由 evidence_ref 的固定格式确定性解析 | 解析不是文件真实性证明；须核对可信 import_runs/原文件登记或历史来源导出，不凭解析值自证 |
| review_content_sha256 | **不在冻结 hit、summary 的逐行数据中，不能从归档独立恢复** | 从相同历史版本的可信 reviews/语料契约或已封存来源记录取得；没有就阻塞，不能猜或反推稳定键 |
| published_at（准备工具额外需要） | 不在现有 hit 中，EvidenceCandidate 本身不要求 | 从独立来源取得带时区的时间，按冻结 UTC 日期校验；不猜缺失时区 |
| EvidenceBatch.version、scope_ref | 新格式固定版本；run 的 run_id/manifest_sha256/scope_sha256 | 新格式版本不改原 manifest；规范 JSON 哈希和原文件字节 SHA-256 分开记录 |
| cosine_distance | 原 hit 直接保存，**不输入判断器** | 留在原基线中，不转成相关性、充分性概率或拒答阈值 |

当前真正无法仅靠冻结产物恢复的关键字段是 review_content_sha256。现有 `reviews/store.py` 对导入记录/内容数据计算版本摘要，并非简单对 review_text 取 SHA-256；quote 哈希、文件哈希、稳定观点键及库存摘要都不能作为它的替代。published_at 同样没有逐 hit 保存，日期范围不能反推出每条实际时间。

这不等于断言数据库或历史导出中永远没有该字段：本轮没有读取它们。若原历史内容/标注版本已被覆盖、关联导入登记丢失且无可信历史副本，就应将该来源标记为无法恢复；不能用新的有效观点伪装成旧运行。不能删除无法恢复项后仍宣称完成原 48 列表的完整 E1。

## 3. 可信来源构建方案及授权边界

### 3.1 已实现的纯离线准备接口

```text
prepare_trusted_batch(frozen_run, source_collection, *, expected_scope,
    expected_source_collection_sha256, expected_receipt_sha256,
    expected_data_kind, query_id, mode) -> PreparedBatch
```

SourceCollection 使用 `e1-source-collection-v1`，包含 data_kind（synthetic/frozen_query）、basis=independent_source_snapshot、collector_id、collection_receipt_sha256、scope_ref、inventory_snapshot_sha256 和独立记录。SourceOpinion 复用既有 EvidenceCandidate 的来源字段定义，去掉 query_id/mode/rank，并额外要求 published_at；没有用自由字典绕过类型与缺失字段校验。

工具只做以下确定性工作：

1. 从调用方**另行固定**的来源集合摘要和采集回执摘要核对输入；合成数据不能静默改标为 frozen_query。
2. 重算原 run_binding，核对可信 expected_scope、manifest 哈希、scope 哈希、库存身份/快照，以及全部 query×两模式的完整性和唯一性。
3. 要求来源键集合恰好覆盖该冻结运行出现的观点键，不接收额外未返回观点、不用部分来源集合补出一份看似完整的结果。本轮真实盘点对应去重 93 个观点、57 条评论，准备工具尚未对这些真实来源运行。
4. 按原 rank 查找相同 key 的独立来源记录；核对范围、profile、builder 和 UTC 日期，所有共有字段必须精确相等。缺 review_content_sha256 或任何必需来源字段就失败，不从原文补造。
5. 一路按独立来源投影 trusted_batch；另一路以原 hit 共有字段覆盖投影，使用现有纯来源校验机制比对两路，并校验字符区间、输入哈希、稳定版本键、文件引用及同评论版本一致性。不调用 judge_evidence 或 score_run。
6. 返回 `e1-prepared-batch-v1` 的批次及其规范 JSON 哈希、来源集合哈希、采集回执引用。状态 `validated_against_pinned_sources` 只表示与外部固定来源相符，不表示来源权限、独立性或语义质量自动获批。

每次返回只覆盖选定的 query-mode。未来真实准备必须为全部 48 列表分别验证，包括两个空列表；不能凭一个批次成功判断全集就绪。准备工具不执行数据库查询、下载、文件恢复或写入归档，也不生成真实人工参考。

### 3.2 如何保证独立，而不是复制候选自证

拟议的数据流是：独立采集器按冻结键集合核验历史来源 → 单独封存来源集合与采集回执 → 由授权人员固定外部摘要 → 准备工具关联原冻结排名 → 生成只读 trusted_batch；待测输入再走独立投影/校验路径。采集器不读取判断器输出，来源集合不包含 query/rank 或分数，不能以“待测 batch 的副本”充当采集结果。

仅填写 collector_id/basis 或重新计算一个哈希不能证明操作独立。代码拒绝 candidate_copy 类型及摘要不符，但无法证明人员没有撒谎、没有把候选反向包装成来源。**外部摘要及回执的真实性、采集授权、采集时间、使用的历史版本与人员职责仍须人工/运维审计保证**，不能从待校验集合临时取出哈希再作为可信参数传回。

原归档清单文件摘要仍为 `f6afd216f987314de08b089ffadd8e9e6dcc867d79a11f40dc4771b775bf7b47`；原 run_id 为 `a2f4907ec8cacc6bae4b52f8d89a9ee557ae09f1f4e7a7f3c6f3803b34848f33`。新采集回执须引用这些身份及实际来源，不更写旧封存文件。

### 3.3 真实恢复所需操作：本轮未执行

| 待批准操作 | 最小授权边界与失败处理 |
|---|---|
| 查询可信历史来源导出是否存在 | 只访问明确授权的文件/来源登记；确认它是否绑定同一历史运行和快照，不自行搜索其他私有评论目录 |
| 只读核验 93 个返回观点及 57 条关联评论 | 限 `reviews_csv/reviews-csv`、商品 `100013588055`、UTC `2021-09-11` 至 `2022-08-26`、既有 profile/space、原 key 集合；只获取缺失身份、时间、必要原文与哈希，不读取新商品或未索引 891 条评论内容 |
| 证明上述评论的 effective_snapshot | 可能需要同评论的完整有效观点键集合，不能只拿一个命中就声称快照相同；所需额外元数据范围须先列明，不能自动扩大原文授权 |
| 核对 import_runs / 导入文件登记 | 只访问相关 file_sha256、line_number、导入身份；如需重新读取原文件验证内容，单独列授权文件及范围，不在本轮打开 |
| 重算整个库存 fingerprint | 现有 inventory/read_effective_corpus 会读取整个获批范围的有效语料和 200 条存储向量，不能假定“只核对 93 个 key”就等于这种授权；若确需重算，另行明确更宽的只读范围。本轮未调用 |

任何历史版本/哈希/快照不一致都应停止受影响准备，不更新数据库、不重新同步、不替换为当前版本。本轮只记录这些操作和阻塞项，没有连接业务库或执行来源恢复。

## 4. 人工充分性参考协议

协议模型为 `e1-human-reference-v1`，origin 必须为 independent_human_reference；judge_output_seen 必须为 false。这里的 confirmed 仅描述人工记录的确认状态，**不代表 E1 协议或质量门槛获批**。没有从原 0/1/2 标签或判断器状态生成 reference_state 的代码。

### 4.1 每个 query-mode 的独立记录

| 字段 | 要求 |
|---|---|
| review_id、revision、parent_reference_sha256 | 记录和修订链；有更正另存版本，不覆盖原标签或旧人工参考 |
| scope_ref、query_id、mode、batch_sha256 | 绑定原范围及当前列表；同 query 的 U/F 分开评审，不能借另一模式的证据补足当前模式 |
| contract_version、contract_sha256、contract_confirmed | 绑定已由人工确认的 QueryContract；忠实于真实问题，不为了适配 15 个规则句式而改写目标事实 |
| required_fact_ids、independent_target_fact_ids | 必需事实来自契约；独立子问题要由人工明确确认，复合核心的一个前提不自动成为独立答案 |
| reference_state | sufficient/limited/insufficient；草稿可为 null，不能将未审记为 insufficient |
| supported_target_fact_ids、missing_required_fact_ids | 显式记许可目标及缺失；与逐事实支持、all_of/any_of 及必要关系一致；原子条件直接支持不一定等于核心可回答 |
| fact_findings | 每个事实标 direct/partial/not_supported/unknown、人工理由和 FactEvidence 来源映射；不使用 0/1/2 自动换算 |
| relation_findings | 为 relation_0 等契约关系及 contrast 分别标 supported/not_supported/unknown、理由和当前候选 evidence_keys |
| context_policy、context_version、context_input_sha256 | 绑定本次实际可见视图及版本；A/B 不能共用一个已确认参考而不另审 |
| context_observations | 只说明当前候选中的上下文否决证据；B 可引用对应原文区间，但不能把 quote 外事实登记为支持 |
| review_status、human_confirmed | draft/confirmed/adjudicated；未确认或未知未解决的记录不能供评价 |
| reviewer_decisions、disagreements、adjudication | 保存独立评审人结论和许可事实、分歧字段、裁决人及理由；有分歧而无匹配裁决不得发布为已确认 |

人工参考的事实引用逐项绑定当前 batch 的 key/rank/query/mode/review/annotation/快照/输入哈希/ref，必须落在 quote 区间内；重复引用、跨列表引用、许可未直接支持的事实、未审关系等都会报错。same_review 关系不能用不同评论来源声明共现。

已确认记录要求所有事实/关系完成审阅、查询契约已确认、至少有实际人工确认记录，并解决分歧。sufficient 记录全部直接可许可目标；limited 仅记录已直接支持且可独立许可的目标，不能把 partial 升级为 direct；insufficient 不包含许可目标，但可保留已经直接匹配的复合前提，表明不足发生在哪个条件/关系。不能把所有记录统一改成 insufficient 掩盖已确认的可答事实。

这些是格式、来源和逻辑一致性检查，**不证明人工的语义判断正确**。记录中声明“未看输出”也不是机器能证明的盲审，需要流程隔离。模型没有要求必须使用某个未获批准的评审人数；下述双人评审安排只是待确认建议。

### 4.2 独立标注与封存顺序（待批准）

1. 先固定真实查询契约、目标/独立子问题、context_policy 及候选列表，不展示判断器规则输出、方案名、距离或分数。
2. 建议由两名人员独立评审，先完成所有所问事实和必要关系，再给状态；分歧留原始记录，由裁决人给出处置及理由。评审人数与第三方裁决安排需人工确认。
3. 原相关性标签仅可在独立初评封存后作为辅助对照；若辅助信息导致人工更正，应另建 revision，重新确认并在实验前固定最终版本。不能直接把 2 分变 sufficient、1 分变 limited、0 分变 insufficient。
4. 在任何待测输出生成前封存人工参考集合、引用映射、评审名单/记录及摘要，独立保存采集/确认/封存时间；未来实验登记引用其固定摘要，记录配置、代码版本和尝试次数。
5. 评审/运行角色分离，参考标签不给运行时判断器。后续若发现参考错误，另版裁决并重新声明比较边界，不根据有利的预测结果回改旧参考。

本轮只验证合成记录，不为真实 48 列表创建状态、标签或已确认模板。

## 5. 上下文政策 A/B 对照：均待批准

| 事项 | A：quote_only / version 1 | B：quote_full_review_veto / version 1 |
|---|---|---|
| 人工/语义判断可见内容 | quote 和可信来源元数据 | quote、对应完整原文及元数据 |
| 支持事实来源 | 只限当前候选 quote 内 | 同样只限 quote 内，原文只能否决/限定解释，不能借入额外支持事实 |
| 适用性 | 隔离短观点本身的信息量；可能看不到被截掉的否定、角色或时间限定 | 检查被截掉的否定/条件、转述等；读取更多内容，可能增加规则覆盖不足引发的拒答 |
| 数据与权限 | 来源核验层仍可能需要已授权原文，但语义/人工视图不能暴露它 | 必须另获完整原文查看/处理授权，并绑定相同历史版本 |
| 当前代码支持 | review_context_view 可生成去掉 review_text 的人工视图；**没有 quote-only 判断器版本** | 当前 2a 判断器读取完整原文作否决，但仍未通过真实质量验证 |
| 人工参考 | 使用 A 可见信息独立确认 | 使用 B 可见信息独立确认；不能直接复用 A 状态 |

现有 EvidenceCandidate 要求 review_text，判断器还用完整原文检查原子边界。因此不能用 `review_text=quote`、重写 offset/内容哈希或剥掉原文后调用原判断器来冒充 A；这会改变来源或程序行为。A 的真正判断器若未来要开发，必须是单独获准、单独版本化的上下文变体。本轮没有修改 2a。

人工视图函数仅做数据投影和 context_input_sha256 绑定，不运行 A/B 判断器或比较真实结果。公平对照必须固定 query、原列表/顺序、契约、来源和判断规则的其他部分，仅改变上下文变量；独立人工参考应先封存。需避免同一评审者看完 B 原文后再凭记忆评 A，可考虑独立评审组或受控顺序，但具体安排待确认。不能同时改意图、过滤、候选数和排序后把差异归因于上下文。

## 6. E1 评价设计、分母及错误规则

本节仅为设计，**没有执行或计算真实 E1 指标**。原 24 题、48 列表、222 条相关性记录和原 U/F 指标完整保留，E1 不改变其分母，也不将 14 个 answerable 问题等同于充分性金标准。

固定一个 context_policy/版本和候选配置后，记 H 为人工参考、查询契约和来源均有效且已确认的 query-mode；V 为 H 中执行完成且输出结构有效的记录。每行必须报告 H、V、应评 48、执行失败、缺预测、未审、来源无效等计数。U 为 V 中人工不许可任何目标事实的列表；S 为 V 中人工许可至少一个目标事实的列表；A 为 V 中判断器输出 sufficient 或 limited 的列表。复合前提匹配不自动进入人工许可集合。

| 指标 | 分子 / 分母与约束 |
|---|---|
| 无支持场景回答许可率 | U 中输出 sufficient 或 limited 的列表数 / U 的列表数；limited 同样算许可，不能隐藏 |
| 全部许可中的越界比例（补充） | A 中许可人工未支持的目标、缺必要关系、借外部事实或充分性过度声明的列表数 / A 的列表数 |
| 过度拒答率 | S 中输出 insufficient 的列表数 / S 的列表数；仅能支持独立子问题的人工 limited 也属于可答，必须计入 |
| 三状态一致率 | V 中与人工 reference_state 相同的列表数 / V 数；同时给 3×3 混淆矩阵和每个人工状态的有效分母/召回情况 |
| 许可目标事实的证据支持率 | 将许可去重为 (query_id,mode,fact_id)，统计目标本身及其引用/必要关系均获独立人工支持的许可事实数 / 已完成该支持审查的许可事实数；另报全部许可事实总数、映射未覆盖/未评数 |
| 来源引用有效率 | 经独立可信批次、身份、哈希、范围和 quote 字符区间校验的去重引用数 / 输出的全部去重引用数；没有引用时为 N/A，不能写 100% |
| 人工/执行覆盖 | 已确认有效参考数 / 48；完成且合法输出数 / 应执行列表数；validation_failed、execution_failed、缺失预测和未审分别计数，不计作正确拒答 |
| U/F 配对 | 仅同 query、同 context_policy/版本、同契约和来源条件，两模式对应指标都可判断时算 F−U；逐项记录配对数与排除原因 |

支持率不能只看“引用格式合法”或“fact_id 在人工集合中”。人工必须先确认事实与候选/区间的支持映射以及必要关系；引用语义未被预先覆盖时，保持未评，不自动算支持或不支持，也不根据预测反向生成金标准。应在实验前明确映射是否穷举、区间兼容/包含规则及未覆盖情况的处理；未评引用较多时不能据已评子集宣称整体质量通过。

错误和缺失不悄悄从主结论消失：条件指标以 V 计算时，同时显示 H→V 的减少原因及在各人工状态中的错误分布；覆盖不完整不得宣称完整验收。零分母和未评均为 N/A，不填 0。来源/范围错误的预测不纳入“正确 insufficient”；记录违规并按待审批停止政策处理。

分组按冻结 query 的 aspect/sentiment、人工确认的 task_type、对象/主题、否定/混合/多条件/独立子问题类别分别列 H/V/许可数/人工状态数及指标分母。类别可重叠，不相加冒充独立样本；同 query 两模式也不是两个独立自然语言问题。不能把 filtered 的人工预设条件当自动意图识别能力。

### 6.1 区分覆盖不足与证据不足

人工参考先由真实可见证据建立，不受 2a 的 15 个句式约束。之后才记录预测侧的 unrecognized_target_fact、unrecognized_clause_or_context、negation_scope_unknown 等覆盖诊断。

- 人工确认可回答而规则输出 insufficient 且不识别目标/上下文：报告覆盖不足造成的过度拒答，不改成人工 insufficient，也不从全体指标中删除。
- 人工确认核心证据不足、规则也 insufficient：可报告状态一致，但不能由此断言规则理解了语义；若仍有未识别原因，单列“状态一致但覆盖未知”。
- 来源非法/运行异常：是验证或执行失败，不能解释为“证据确实不充分”。

必须同时报告风险和可用性：全部输出 insufficient 可能使无支持场景许可数为 0，但只要存在人工可答列表，其过度拒答率就会升至全部拒答水平；三状态混淆矩阵、许可覆盖和空分母 N/A 会暴露这种退化，不能据单一风险指标宣布提升。

## 7. 待人工确认的质量建议与审批项

| 事项 | 建议与当前状态 |
|---|---|
| 无支持许可比例 | 建议以降低无支持/越界许可为首要目标，先由业务方确认最大可接受上限及严重性分级；**未设置正式数值** |
| 过度拒答 | 与风险指标一起设置可接受上限、各关键类别最低样本量和代价权衡；不能靠全部拒答放行；待确认 |
| limited 范围 | 只许可直接支持的独立目标，明确缺失条件；复合核心组成部分不得擅自拆成答案；真实 QueryContract/独立声明待人工确认 |
| 来源/范围违规 | 建议任一此类事件停止受影响运行及结果发布，保留证据与原因；结构校验已实现，正式实验停止/发布政策仍待审批 |
| 标注与裁决 | 建议双人独立初评、保留原结论、分歧裁决、版本和封存时间；人员、工作量、裁决流程与盲审措施待确认 |
| 参考完整性 | 确认所有事实、必要关系、可许可目标及候选/区间映射覆盖；未审不当否定；是否允许部分参考实验及其报告方式需明确批准 |
| 上下文 | A/B 的数据授权、实际可见字段、规则/策略版本及独立参考分别批准；不以本轮合成测试选择更优方案 |
| 独立验证 | 另外准备未用于规则/提示选择的问题，先固定方案再揭示参考；数量和分层配额待人工确定，不宣称 24 条已见诊断题上的泛化 |

本轮未创建“approved=true”配置、正式数值门槛或真实已确认参考。模型允许校验人工 confirmed 记录的结构，不代表程序有权批准真实实验。上述待审批事项不因测试通过而自动转为已批准。

## 8. 合成测试与回归

| 检查 | 实际结果 |
|---|---|
| 新准备/协议专项 | 29/29 通过；最终版本也包含在组合回归中 |
| 原判断器 / QueryContract | 38/38、32/32 通过，未改行为 |
| 原意图 / 语料 / 离线评分 | 19/19、21/21、25/25 通过 |
| 原范围测试 | 9 通过、2 项既有错误，未跳过或改成通过 |
| 组合合计 | 175 项：173 通过、0 断言失败、2 错误、0 跳过；新专项已包含其中 |
| 冻结与改动边界 | 开始记录的 185 个已有文件收尾哈希相同；原归档 12 文件保持不变，新材料只进入本轮忽略目录 |

专项验证了独立摘要/回执约束、缺失字段拒绝、UTC 时间、源字段冲突、重复/额外来源、U/F 身份、空列表、草稿与确认、人工分歧和裁决、事实引用边界、A/B 视图隔离及哈希、all_of/any_of 和复合核心/独立目标区别。测试设置哨兵，证明准备/人工协议流程没有调用判断器、score_run、数据库或网络。没有用真实评论或原评分生成公开夹具。

首版专项有 1 项 JSON 测试辅助函数错误，修复辅助函数后 29 项通过；日志均保留。两项旧错误为 `test_run_date_filters_incremental_skip_and_scoped_progress`、`test_run_retry_and_cutoff_forwarding_with_real_mocked_sdk`，仍为临时 SQLite 进入仅支持 PostgreSQL 的持久标注队列；单独保存在 `existing-queue-errors.log`，未修改原业务逻辑。

实际执行的专项命令：

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_e1_readiness.py -v
git diff --check
git status --short
```

组合回归由以下 unittest loader 在仓库根目录执行，完整输出保存在本轮 `targeted-regression.log`；两项错误保持原始失败记录：

```python
import sys, unittest
sys.path.insert(0, 'tests')
names = ['test_e1_readiness', 'test_evidence_judge', 'test_query_contract',
         'test_review_scope', 'test_review_intent', 'test_rag_corpus', 'test_retrieval_evaluation']
suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(n) for n in names)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
```

未运行全仓、前端、真实数据库检索、来源恢复、向量同步、外部模型、上下文消融或真实 E1。原审计机制和字段清单核对不等于 E1 推理或质量评分。

## 9. 明确结论与停止点

**不能进入真实 E1。** 已具备可用合成数据验证的准备/评审结构，但缺少获准恢复并独立封存的真实来源集合、真实人工查询契约与充分性参考、上下文政策、实验配置和正式质量门槛。A 还缺单独获准的判断器上下文变体，B 也未获真实质量验收。

证据链：E01=冻结字段盘点与保护哈希；E02=新专项/组合回归日志；E03=单列旧队列错误；E04=最终语法、隐私、文件边界及归档哈希检查。F01（E01/E02）：离线结构逻辑可验证，真实来源仍有恢复阻塞；F02（E02/E04）：人工记录的一致性校验不是语义金标准生成或质量验收；F03（E03）：既有队列错误仍保留。后续路径 P01：人工审核本报告 → 明确授权来源与评审操作 → 另行决定下一阶段，当前停止，不自动进入 4B-2c 或 Agent 集成。
