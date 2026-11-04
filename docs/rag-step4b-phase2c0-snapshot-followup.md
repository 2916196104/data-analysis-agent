# RAG Step 4B-2c-0b：历史有效快照核验与可信来源封存准备

日期：2026-11-04（Asia/Taipei）。**已完成本轮获准的范围盘点、SQL 草案审查、纯离线工具与合成测试；未获新增数据读取授权，未连接业务库。57 条真实历史快照仍未核验，可信批次为 0/48，不能进入正式人工充分性评审或 E1。**

前阶段 93/93 个观点、57/57 条评论的行级核验结果保留，不重复恢复。完整历史状态和观点集合尚未取得，不能用当前最新状态、缓存快照摘要或合成测试补足。本报告同时提供待授权字段清单和人工封存交接步骤；没有宣称独立批准已完成。

## 1. 实际授权、读取范围与真实数量

本轮授权为离线代码检查、额外读取范围盘点、SQL 方案、工具和合成测试。用户要求同一 57 条评论下、原 93 个观点之外的元数据也须单独明确授权。因此没有尝试数据库连接、权限提升或新增读取，没有重跑前阶段采集脚本。

范围仍为 `reviews_csv/reviews-csv`、商品 `100013588055`、UTC `[2021-09-11T00:00:00Z,2022-08-27T00:00:00Z)`，原 profile、EmbeddingSpace、quote-utf8-v1 和 run 不变。

| 项目 | 本次实际结果 | 统计边界 |
|---|---|---|
| 新增业务数据库连接 / 真实元数据读取 | 0 / 0 行 | 没有读取其他观点、人工修订或当前新状态 |
| 前阶段行级来源 | 93 个观点、57 条评论、57 个原标注，已核验一致 | 复用已保存 SQL 观察和回执；不是本轮重新恢复 |
| 完整历史快照重建 | 0/57 | 没有在真实数据上调用新核验函数 |
| 完整重建且一致 / 完整重建但不一致 | 已确认数量均为 0；已重建分母为 0 | 一致率、不一致率均为 N/A，不能报告 100% 或 0% |
| 来源存在但历史状态不能证明 | 本轮未作逐条重建判定 | 已知全部 57 条行级来源存在，完整历史证明仍未提供；不与下一行重复计数 |
| 必要来源缺失 / 确认无法恢复 | N/A，尚未调查 | 没有证实缺失，不能说“0 条缺失”或“57 条无法恢复” |
| 未核验 | 57/57 | 私有清单逐条标记 unverified，actual_verification_attempted=false |
| 真实 SourceCollection / 独立封存 | 未创建 / 未完成 | 没有生成假定完整的来源对象 |
| 48 个真实 EvidenceBatch | 准备 0、失败 0、未尝试 48 | 包括两个空列表，均有逐列表阻塞记录 |

五类状态用于未来实际核验结果；本轮不把未审条目塞入“已重建不一致”或“来源缺失”。私有 `review-statuses.json` 保留 57 个未核验记录，`batch-statuses.json` 保留 48 个未准备记录。

## 2. 已读来源与实际交付

阅读了 [Step 4A 最终审查](rag-retrieval-quality-final-review.md)、[Step 4B 计划](rag-step4b-retrieval-strategy-plan.md)、[4B-2b](rag-step4b-phase2b-e1-readiness.md)、[4B-2c-0 来源恢复](rag-step4b-phase2c0-source-recovery.md)，以及 rag_corpus、scope、e1_preparation、evidence_judge、标注/人工修订存储、归档审计、原只读采集脚本与相关测试。

| 新增文件 | 实际职责 |
|---|---|
| [historical_snapshot.py](../src/data_analysis_agent/reviews/historical_snapshot.py) | 纯离线快照输入/输出和核验函数；不采集、封存或自动准备批次 |
| [test_historical_snapshot.py](../tests/test_historical_snapshot.py) | 21 项完全合成测试，含既有准备接口的信任边界检查 |
| 本报告 | 字段盘点、SQL 审查、历史证明阻塞、封存交接、实际数量与测试 |

私有材料在 `outputs/rag-retrieval-quality/step4b-phase2c0b-20261104/`：`metadata-read-plan.draft.json`、`frozen-review-bindings.json`、`sql-review-checks.json`、`snapshot-readiness.json`、逐评论/逐列表状态、`seal-request.draft.json`、离线草案生成脚本、测试与审计日志、保护哈希。全部受 Git 忽略，无真实公开测试夹具。

未修改既有 rag_corpus、e1_preparation、evidence_judge、检索、排序、过滤、数据库、Agent、API、前端、原评分或前序报告；没有暂存/提交。新模块没有接入任何业务调用路径。

## 3. 完整快照的全部依赖与历史证明缺口

原 `_review_state()` 使用：

```text
content_sha256([
  CORPUS_CONTRACT_VERSION,
  label_profile_key,
  state,
  sorted(全部有效观点的 stable_versioned_opinion_key)
])
```

`state` 包含下表全部字段；opinion_count 来自完整 documents 数量，而非 Top-K 命中数。即使有效集合为空，也有独立版本摘要。

| 字段 / 材料 | 既有证据及新增要求 |
|---|---|
| source、dataset_version、product_id、review_id | 原范围及 57 个精确评论绑定；不得跨商品、日期或评论替换 |
| review_content_sha256 | 前阶段已重算并通过冻结观点键绑定；完整快照仍须使用同一版本 |
| annotation_id、label_profile_key | 前阶段原 57 个标注及模型/端点/prompt 身份一致；不能替换为新标注 |
| published_at | 前阶段已核验范围及版本绑定；不猜时区 |
| processing_status | 原规则为 has_text=0→empty，未选择到标注→pending，否则取所选 annotation.status；a.status 不能直接冒充完整处理状态 |
| human_status | 无修订为 unreviewed，有修订取该历史修订 action；不能仅因当前没有返回修订就断言历史无修订 |
| human_revision_id、opinion_source | 原返回观点均为 model/null 修订；还需证明冻结时的完整有效选择状态，而非今天的 MAX(id) |
| error_code | 来自所选标注，null 也须显式保留，不遗漏或自动猜 null |
| 完整有效观点键集合 | 同评论原标注下全部有效观点；需要冻结 93 个之外的元数据，数量未知，尚未查询或计数 |
| opinion_count | 完整集合大小；不能用该评论在 48 列表里的命中数或去重命中数替代 |
| effective_snapshot_sha256 | 从独立状态和完整键集合重算，再与冻结值比较；Embedding 缓存值只作旁证 |

稳定观点键还依赖内容哈希、profile、annotation/修订/来源 ID、aspect、sentiment、字符区间和 quote 的 UTF-8 哈希。新工具以已授权保存的对应原文切片核对独立来源 quote 哈希，再按原键字段顺序计算；没有从 slice 临时填一个摘要来充当独立来源。

### 3.1 为什么当前表不等于历史快照

`current_reviews_sql()` 按匹配当前内容的标注选择成功优先/ID 最新记录，并选择当前最大的 human revision ID。人工 confirmed 也会产生一份人工观点快照，空的 corrected 快照仍是有效空集合；不能把它当模型结果继续拼接。

已检查的存储/迁移代码没有提供完整的时态有效区间或不可变事务历史证明。人工表的 previous_id/action/created_at 可辅助分析，但当前查不到某条修订不证明冻结时不存在；导入删除路径也可能移除关联历史。这里只说明代码的证据能力，**未查询数据库或备份，不能断言独立历史副本不存在**。

实际检查原 review.json 顶层：没有检索事务时间、快照 ID 或 LSN；inventory 记录只读/隔离及摘要，但没有可回放的逐评论历史 state/完整键清单。archive-manifest 的 `frozen_at=2026-11-04T00:33:41.894433+08:00` 是后来的归档封存时间，不能当原检索事务的 as-of 时间。

因此不能通过 `created_at <= frozen_at`、直接取最新修订，或枚举 human_status 直到命中哈希来制造历史证明。需要明确的历史导出/备份或可审计事件链，覆盖完整成员关系、修订状态、删除情况及原运行版本绑定。若只能取得当前完整集合，结果保留 history_unproven；即使计算值相同，也不由本工具自动升级成历史证明。

## 4. 待单独授权的额外读取范围与 SQL 审查

下列 M1–M3 均为**未授权、未执行草案**。本轮仅做静态审查；不声称 PostgreSQL 实际执行或权限验证通过。具体绑定数组来自原 57 个 review_id/annotation_id 及已验证内容哈希，保存在私有计划中，不把评论或逐条标签复制进 docs。

| 查询 | 拟新增读取字段与精确范围 | 排除项 |
|---|---|---|
| M1 原状态元数据 | 仅原 57 对 review/annotation，匹配已验证 content_sha256；读取 has_text、原 annotation_status/error_code/created_at，以及范围、内容哈希、published_at、模型/base_url/prompt 以核对原身份 | 不读取新正文、用户 ID、评分、统计数据或其他标注；base_url 只在内存重算身份后移除 |
| M2 原标注的完整模型观点元数据 | 仅上述原 57 个 annotation 下全部 review_opinions，包括原 93 个之外的行；读取 id、annotation/review 关联、aspect、sentiment、quote_start/end、由数据库对原 quote 计算的 UTF-8 SHA-256 | 不返回 quote 明文，不按 aspect/sentiment/Top-K 截断，不读向量；额外观点数量未知 |
| M3 人工修订链元数据 | 仅原 57 个 annotation 下全部 review_human_reviews 的 id、annotation_id、previous_id、action、created_at | 不读取 reviewer、note、人工观点行或任何人工观点正文；不自动以 MAX(id) 选择历史状态 |

三个查询都使用相同 allowed CTE：以参数化、等长的 review_id/annotation_id/content_hash 数组精确绑定，再同时约束 source、dataset、商品和 UTC 日期。内容版本不符导致缺行时登记错误，不越界补查。没有扫描全语料或全 200 条库存的应用入口。

M2 草案采用 PostgreSQL 内置 `sha256(bytea)` 对 `convert_to(o.quote,'UTF8')` 的结果计算哈希；只审阅了语句，未实测能力。若获准后函数不可用，应停止并报告，不安装扩展或降级为输出正文。没有默许新增片段/正文读取授权。

静态审查确认：3 个语句均有精确 ID/内容版本及数据范围约束；M2 无 Top-K/类别截断；M3 无当前最新或猜测 as-of 选择；无 DDL/DML、向量、人工备注或人工观点查询。已将草案中原标注状态明确命名为 annotation_status，避免与 derived processing_status 混淆。

未来若单独获准，应使用只读 REPEATABLE READ 事务、超时、实际只读检查、同事务记录完整结果、rollback/close，并另存回执。若需原历史备份/审计日志，应再明确来源位置、时间/版本和最小投影；M1–M3 的当前库读取授权不自动包含备份访问，更不能保证历史可重建。

## 5. 离线工具的接口、分类与能力限制

```text
verify_historical_snapshot(value, *, expected_scope, expected_review_id,
  expected_snapshot_sha256, expected_data_kind,
  expected_historical_input_sha256=None,
  expected_history_receipt_sha256=None) -> SnapshotResult
```

SnapshotInput 使用严格、冻结模型，拒绝未知字段；包含版本、synthetic/frozen_query、ScopeRef、current_observation/historical_export、历史回执引用、profile/corpus 版本、collection_complete、显式 state、原文及完整观点元数据。不会接收缓存 effective_snapshot_sha256 来代替计算输入。

输出含 execution_status、classification、review_id、规范输入哈希、重算快照哈希、观点数和固定 reason_codes。仅输出审计元数据，不输出原文。

| 分类 | 规则 |
|---|---|
| rebuilt_match | 状态/观点/完整性检查通过，具有外部固定的历史输入和回执摘要，重算值与冻结摘要一致 |
| rebuilt_mismatch | 同样完成历史输入验证，但重算值与冻结摘要不同；保留冲突，不修复来源 |
| history_unproven | 能计算观察值，但仅为当前观察或缺少任一独立固定历史摘要；相同哈希不自动放行 |
| missing_source | 明确没有来源输入，或声明观点数与提供集合不一致；数量错误另报 validation_failed |
| unverified | 集合完整性未建立，或字段、范围、数据种类、固定摘要、区间、来源身份等校验失败 |

实现复用原 `_review_state()` 计算最终摘要，合成测试以原 `_document()` 和 `_review_state()` 独立构造对照，验证版本键字段顺序相同。重复来源、非法修订身份、切片/哈希不符、缺状态字段及固定摘要冲突都不会变成正常的“历史不一致”或 E1 insufficient。

expected_scope、冻结摘要和外部历史固定摘要必须来自独立可信流程，不得从待测输入反推。代码校验摘要/声明的一致性，不能证明历史导出的真实性、人员独立性、collection_complete 声明是否真实，或数据库中是否有未导出的/已删除记录。这些要由下一节的历史材料和独立复核证明。

本轮没有对真实 57 条构造猜测 state，也没有运行该函数生成真实分类。没有修改已有 prepare_trusted_batch()；它本身不建立历史证明或外部审批，新模块也不自动调用它。

## 6. 可信来源封存交接清单（全部待执行 / 待批准）

复用前阶段实际采集观察 `2026-11-04T02:15:48.466614+00:00`、事务观察标识 `1000351:1000351:` 及原采集回执。该时间属于当前观察，不是冻结时的历史读取点。已有文件字节摘要已再次检查：

| 既有材料 | 文件 SHA-256 |
|---|---|
| attempt2-current-source-observation.json | `373ee19353dd3aa89c0aa55d0c7d20031af4e1e5e9a03e89eed1dc44bcf11295` |
| attempt2-collection-receipt.json | `eee8bd39899fff0a0d374ad709f95ae71535ff0ecfe7d727decf9bc3fd73a973` |
| 原 Step 4A archive-manifest.json | `f6afd216f987314de08b089ffadd8e9e6dcc867d79a11f40dc4771b775bf7b47` |

这些是既有审计锚点，不能代替尚缺的完整历史来源和外部固定批准。`seal-request.draft.json` 中外部来源摘要、外部回执摘要、审批人与记录均为空，没有 approved=true。

授权人员或独立流程应依次执行：

1. **确认权限与职责。** 指定可读私有来源的采集角色、具有独立复核职责的保管人员/流程及批准记录位置；不由本程序代填身份。未经新增授权，不执行 M1–M3、读取备份或其他来源。
2. **确认历史依据。** 固定获准来源的准确历史版本、与原运行的关联、完整成员和删除/修订证据。记录实际采集时间、数据库观察版本、授权、SQL/参数摘要与行数；当前时间不伪装成历史时间。
3. **复核 57 条快照。** 使用已固定历史输入和回执逐条核验，所有必需状态、完整集合及计数有独立依据；缺失、冲突和未知不得剔除。保存每条结果及规则/代码摘要。57 条未齐就停止完整可信集合发布。
4. **准备来源记录。** 在历史证明完整后，按已有 SourceOpinion 字段从独立 SQL 观察及已验证历史来源投影 93 个记录；所有共同字段保持原值。额外观点元数据仅进入历史证明附件，不加入 SourceCollection.records（该集合必须恰为原 93 个 key）。不得复制待测 batch 或用归档值填补独立来源缺项。
5. **编制封存清单。** 对象包括原归档引用、既有观察/回执/日志、新历史导出及回执、57 条核验材料、SourceCollection 与汇总采集回执、代码版本和授权记录。绑定原 run_id、manifest/scope/inventory、profile/space/builder；保留原始文件，不覆盖旧结果。
6. **分别计算两类摘要。** 原始导出、日志和回执使用文件字节 SHA-256；SourceCollection 使用已有模型校验后 `model_dump(mode='json')` 的项目 content_sha256 规范摘要；SnapshotInput 同样使用模型规范摘要。不得互换换行敏感的文件摘要与规范 JSON 摘要。汇总回执自身不循环包含自身摘要。
7. **独立封存并固定。** 保管方在可控的只读/不可覆盖存储或有版本与访问控制的登记系统中复核并保留对象，记录人员、时间、权限和变更流程。将外部批准摘要保存于准备进程不能自行改写的独立批准记录；不从同一候选目录临时读取摘要自证。Git 忽略、Windows 普通只读标记或自产哈希均不等于该步骤完成。
8. **复核后交接。** 保管方通过可信外部渠道提供 SourceCollection 规范摘要和采集回执文件摘要。准备操作者只读取得材料，逐文件复核原始/规范摘要、授权范围与 57 条结果；任一变化须新版本复审，不能回改旧批准。未获实际批准不得标记 completed。

本轮仅生成交接清单，没有创建真实 SourceCollection、封存副本或外部批准记录。

## 7. 48 批次与人工充分性评审的进入条件

现状是准备 0/48、未尝试 48，包括 Q17-F、Q22-F 两个空列表。48 条私有状态记录均带历史证明不完整、独立封存及外部摘要缺失的原因；不能用“空列表不需要来源”绕过全局门槛。

只有原 93/57 来源完整、57 条历史快照证明完整、来源集合/回执独立固定、预期摘要来自可信外部流程、scope/run 完全一致后，才可逐一调用已有 prepare_trusted_batch()。原 query、U/F、rank、Top-K、评分和备注保持不变；每个列表独立留结果，不以部分成功代替全集。

进入正式人工充分性评审还需固定并人工确认真实 QueryContract、required/independent facts、上下文政策、评审/分歧裁决与参考封存流程；候选必须是已确认来源版本。E0/E1 协议和质量门槛仍待人工确认；0/1/2 不转换为充分性金标准。完成评审准备不自动批准 E1 或模型处理评论。

## 8. 实际测试、回归与保护证据

| 实际检查 | 结果 |
|---|---|
| 新专项 | 21/21 通过；最终版本已包含在以下组合回归中 |
| 原准备/人工协议、判断器、QueryContract | 29/29、38/38、32/32 通过，均为合成测试 |
| 原语料、离线评价 | 21/21、25/25 通过 |
| 原范围测试 | 9 通过、2 项既有错误，未跳过或修改 |
| 组合回归 | 177 项：175 通过、0 断言失败、2 错误、0 跳过；不报告全绿 |
| SQL 草案 | 3 个语句的静态约束检查通过；真实执行/能力验证未做 |
| 归档审计 | 原 audit.py --verify 通过，12 个文件及评分复算一致 |
| 保护 / 隔离 | 开始记录的 196 个已有文件收尾哈希不变，含原采集与前序材料；私有目录无 Git 跟踪文件 |

新测试覆盖完整一致、缺少其他观点、缩减数量伪装完整、修订/标注变化、计数错误、缺状态字段、快照冲突、重复来源、非法原文区间/哈希、当前状态不能冒充历史、缺外部摘要、SourceCollection 身份冲突、空列表、原排名和来源引用错误。断言实际分类、原因、计数及摘要，并用网络、数据库、语料读取和 E1 调用失败哨兵验证独立函数不执行它们。

两项既有错误仍是 `test_run_date_filters_incremental_skip_and_scoped_progress`、`test_run_retry_and_cutoff_forwarding_with_real_mocked_sdk`，临时 SQLite 进入持久标注队列后报“持久标注队列只支持 PostgreSQL”。本轮再次实际出现，保存在 `existing-queue-errors.log`，没有修改旧逻辑或把错误计为通过。测试日志中模型调用计数属于原合成测试的模拟 SDK 路径，不是实际外部模型推理。

本轮主要实际命令如下；私有草案生成脚本使用独占创建，只执行一次，后续可直接审阅已生成计划，不覆盖目录重跑。

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_historical_snapshot.py -v
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/step4b-phase2c0b-20261104/plan_snapshot_followup.py
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/private-archive/step4a-20261104-human6/audit.py --verify
git diff --check
git status --short
```

组合回归通过 unittest loader 加载以下模块，最终版本精确结果在 `regression-result-final.json` 和 `targeted-regression-final.log`；初版日志也保留：

```python
import sys, unittest
sys.path.insert(0, 'tests')
names = ['test_historical_snapshot', 'test_e1_readiness', 'test_evidence_judge',
         'test_query_contract', 'test_rag_corpus', 'test_retrieval_evaluation', 'test_review_scope']
suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(n) for n in names)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
```

没有执行真实快照重建、数据库/备份查询、真实 SourceCollection 或批次准备、E1、外部 LLM、向量操作、前端或全仓测试。初次定位命令的 Windows 通配路径报错已改用目录搜索解决，不涉及数据访问。合成测试通过不等于历史来源真实完整或 E1 质量验收通过。

## 9. 证据、结论与停止点

E01：原归档及本次 archive-audit.log、前阶段观察/回执固定文件摘要。E02：SQL 草案、sql-review-checks.json、57/48 未执行状态。E03：新专项与组合日志、既有错误单列。E04：保护哈希及最终文档/隐私检查。

F01（E01/E02）：现有行级证据可复用，但完整历史状态、成员集合及原运行时间证明仍缺，真实结果保持未核验。F02（E02/E03）：可在合成输入上检验原快照规则与信任边界，不能自动建立历史或外部批准。F03（E01/E04）：冻结来源、评分与既有业务保持原状。

后续路径：用户审阅具体 M1–M3 范围并另行授权 → 按批准范围只读采集且保留当前/历史区别 → 获得独立历史证明与外部封存 → 完整 48 列表准备 → 再确认人工评审条件。若历史证明仍不足，停在不可证明结论，不自动扩大读取或修复数据。

**本轮已完成当前获准的离线部分，到此停止。新增元数据读取与独立封存尚未获准或完成；不进入真实 E1、模型推理或 Agent 集成。**
