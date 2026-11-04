# RAG Step 4B-2c-0c：完整观点集合只读核验与历史状态分析

日期：2026-11-04（Asia/Taipei）。**本轮完成离线预检、原归档审计、M1–M3 SQL/参数复核和既有专项/回归测试。尚未收到针对 M1–M3 的单独明确授权，因此未连接业务数据库、未采集新增元数据、未重算真实完整快照。57 条仍为 unverified，可信批次仍为 0/48。**

这不是实际元数据采集完成报告。前阶段 93/93 个观点、57/57 条评论、57/57 个原标注的行级核验结果保留；本轮再次核对其文件身份，没有重复恢复或把原命中集合冒充完整有效集合。没有运行真实 E1、判断器预测、新检索或外部模型。

## 1. 实际授权与交付范围

本轮任务第 2 节明确要求：“只有收到我针对 M1–M3 的单独明确授权后才允许实际执行。”截至本报告，没有收到该项新增确认。此前“按来源恢复报告第 4 节”读取原 93/57 的授权不扩展为 M2 其他观点或 M3 人工修订链的授权。

已执行的是本地已有产物读取、代码和 SQL 静态检查、冻结审计及合成回归。未读取项目数据库配置或连接凭据，未连接 PostgreSQL，未申请新的执行权限；没有访问 CSV、历史备份或新评论。后续即使获得 M1–M3 授权，也不自动获得历史备份、人工观点正文或正式 E1 的许可。

范围保持 `reviews_csv / reviews-csv`，商品 `100013588055`，UTC `[2021-09-11T00:00:00Z, 2022-08-27T00:00:00Z)`。原标注 profile、EmbeddingSpace、BGE-M3、1024 维、quote-utf8-v1 和 200 条索引均不变。

公开新增文件只有本报告。离线检查脚本、保护哈希、逐评论/逐列表未执行状态、测试日志位于 Git 忽略的 `outputs/rag-retrieval-quality/step4b-phase2c0c-20261104/`。没有新增公开真实数据夹具，没有修改业务代码、旧报告、冻结归档、人工评分或备注，没有暂存或提交。

## 2. 冻结身份、前阶段来源与环境预检

完整阅读了 [来源恢复报告](rag-step4b-phase2c0-source-recovery.md)、[历史快照跟进报告](rag-step4b-phase2c0-snapshot-followup.md)、[E1 准备协议](rag-step4b-phase2b-e1-readiness.md)、[Step 4A 最终质量审查](rag-retrieval-quality-final-review.md)，检查 historical_snapshot、rag_corpus、scope、e1_preparation、数据库只读连接、人工修订/删除路径、原 SQL 采集器、冻结归档和审计实现。

实际执行原归档 `audit.py --verify`，退出码 0，返回 `sealed_archive_verified`。归档 12 文件身份、载荷/相关代码摘要、原排名、评分复算及跨模式一致性通过。仍是 24 题、48 列表、222 条评分、93 个去重观点、57 条评论；60 个共享 query/key 配对冲突为 0。没有进行检索或充分性评分。

| 身份或文件 | 本轮核对值 |
|---|---|
| Git HEAD（预检） | `2964ce1da0db05aac1d9bd09334b5c3c1041962e`；开始时工作区干净 |
| run_id | `a2f4907ec8cacc6bae4b52f8d89a9ee557ae09f1f4e7a7f3c6f3803b34848f33` |
| manifest 规范 JSON SHA-256 | `a17e77d4f7c5d83c5b1c80bad318b374432f5750a9151c826f451feb52b5c3f8` |
| scope_sha256 | `167e14701623dc709a96ce8761a84f7a89c8eaba9c34f6f71025e2dc89e7f766` |
| inventory_snapshot_sha256 | `92e3c0ec0ff391e38110affb086def5e7b7a6ccd6412dea6f56cbe1133006ecd` |
| archive-manifest.json 文件 SHA-256 | `f6afd216f987314de08b089ffadd8e9e6dcc867d79a11f40dc4771b775bf7b47` |
| 原独立来源观察文件 SHA-256 | `373ee19353dd3aa89c0aa55d0c7d20031af4e1e5e9a03e89eed1dc44bcf11295` |
| 原采集回执文件 SHA-256 | `eee8bd39899fff0a0d374ad709f95ae71535ff0ecfe7d727decf9bc3fd73a973` |
| 原逐观点比较文件 SHA-256 | `1309b57d5b0157097879757033eea1b6eb4995c19451495b12ae7f2fa99270ca` |
| 原读取范围文件 SHA-256 | `71aba133248320396e476151dfe6d86270ac20f62d6b78267709f9000ae1376d` |
| M1–M3 草案文件 SHA-256 | `49511d99ca1b429793fbea7f0cd0f8b71b82ea7c63c75638ef94f6b2cdedcd8d` |
| 参数规范 JSON SHA-256 | `910ad7b686a5d3b2221721cc1ebea2f9537c971149377e5a9e675cd9997d71ae` |

以上文件哈希均与前序材料一致。另核对回执中的观察文件/采集代码摘要、run/manifest/scope/inventory 绑定和成功只读事务记录。前阶段保护清单 196 文件及其新增 3 文件，共 199 个已有文件通过初始核对；本轮扩充保护到 218 个已有文件，收尾逐项哈希不变。既有原始观察只读、未改写，没有用新摘要覆盖旧锚点。

前阶段实际数据库观察为 UTC `2026-11-04T02:15:48.466614+00:00`，事务观察标识 `1000351:1000351:`。本轮没有新的数据库观察时间、事务标识或服务端能力验证；本地预检时间不是新的数据库版本。

## 3. M1–M3 具体审查与待批准读取方案

复用上阶段私有 `metadata-read-plan.draft.json`，未修改其 SQL 或参数。57 个等长 review_id / annotation_id / content_sha256 数组逐对匹配原独立观察、冻结候选和 frozen-review-bindings；没有交叉配对、额外 ID 或内容版本替换。所有 SQL 均通过 allowed CTE 同时约束 source、dataset、商品、UTC 日期及精确 ID/内容哈希。

| 查询 | 白名单字段、用途与顺序要求 | 实际执行 / 实际返回行数 |
|---|---|---|
| M1 | 原 57 对 review/annotation：review_id、annotation_id、source/dataset/product、content_sha256、published_at、has_text、annotation_status、error_code、created_at、requested_model、base_url、prompt_version；核验原身份和状态。base_url 仅在内存重算 profile 后移除 | 未执行 / N/A；本轮实际取得 0 行 |
| M2 | 同一原 57 个 annotation 下全部模型观点：review_id、annotation_id、model_opinion_id、aspect、sentiment、quote_start/end、数据库计算的 quote UTF-8 SHA-256；不按类别或 Top-K 截断 | 未执行 / N/A；完整集合数量、新增观点数量均未知 |
| M3 | 同一原 57 个 annotation 下全部人工修订链：review_id、annotation_id、human_revision_id、previous_id、action（human_status）、created_at；保留完整链，不选 MAX/latest 充当历史状态 | 未执行 / N/A；是否存在修订及其行数未知 |

M2 对 quote 的唯一输出是 `encode(sha256(convert_to(o.quote, 'UTF8')), 'hex')`。不返回额外观点 quote 明文，不读取人工观点行/正文/评审备注、用户身份、向量、其他商品或其他评论。共同 CTE 的状态/profile 投影也在已列白名单内。

静态检查通过：精确绑定、外层投影/关联白名单、无 DDL/DML、多语句、类别截断、最新修订或猜测 as-of 逻辑。**这不代表 SQL 已在 PostgreSQL 实测**；现有 schema、权限和内置哈希函数能力仍未验证。不能将“未执行”写成成功返回 0 行。

单独获准后，最小执行顺序如下，当前均待执行：

1. 再查固定文件与批准范围。使用现有 PostgreSQL 配置及只读连接，不调用初始化、迁移或全语料入口。
2. 在一个 REPEATABLE READ READ ONLY 事务中，验证 `transaction_read_only=on`、隔离级别，设置 statement_timeout 20 秒、lock_timeout 3 秒。记录当前观察时间/事务标识，明确不是历史时点。
3. 先执行 M1，确认 57 对完整、唯一、范围/内容哈希及 profile 符合原绑定，再执行 M2、M3。M2 必须覆盖原 93 个 ID/版本，不静默剔除冲突；M3 只分析获准链元数据。
4. 权限、连接、表或 SHA-256 函数不可用即停止并保留错误回执，不安装扩展、不换成正文读取、不扩大 ID 范围。始终 rollback/close，记录 SQL/参数摘要、行数及执行结果。

原行级采集器只能用于理解事务与回执方式，**本轮未重跑它**：它的旧查询含原 57 条正文及内容哈希辅助字段，不应拿来替代本轮仅 M1–M3 的最小元数据读取。

## 4. 完整集合重建规则与当前结果

原快照摘要由 `_review_state()` 计算：`content_sha256([CORPUS_CONTRACT_VERSION, label_profile_key, state, sorted(全部有效观点版本键)])`。必须包含完整集合，opinion_count 是其长度。版本键使用内容/profile、原 annotation/修订/观点来源 ID、aspect/sentiment、区间及独立来源 quote 哈希。

state 需要明确 source/dataset/product/review、review_content_sha256、published_at、annotation_id、human_revision_id、processing_status、human_status、opinion_source、error_code 和 opinion_count。`annotation_status` 不直接等于 derived processing_status；需依原 has_text/标注选择规则处理。无人工修订的 model 集合与有修订的完整 human 快照互斥，不能拼接。

获得实际 M1–M3 后，才可依单一已观察状态组装输入，复用此前已授权保存的对应原文在内存核对区间与独立 SQL quote 哈希，并调用现有 `verify_historical_snapshot()`。不再次获取原文，不将额外切片输出成正文文件，不从切片临时生成一个值冒充数据库来源哈希。若 M3 有人工修订，模型观点只能记为原标注的完整模型集合，不能擅称该评论的当前或历史有效集合；人工观点未授权，相关重建应停止在缺少证据处。

没有通过枚举状态、选当前最新、调整 human_status 或改数量来试配冻结摘要；Embedding 缓存的 effective_snapshot_sha256 不参与替代独立计算。即使一个完整当前观察的计算值与冻结相同，既有工具也只返回 history_unproven，除非另有独立可信历史输入、回执及外部固定摘要。

| 五类结果 | 本轮实际数量 | 口径 |
|---|---|---|
| rebuilt_match | 0 个已发布 | 未重算任何真实完整快照；历史一致率 N/A，分母 0 |
| rebuilt_mismatch | 0 个已发布 | 没有已证实历史不一致，不能说已检查且漂移为 0；不一致率 N/A |
| history_unproven | 0 个完成计算后的分类 | 尚无完整计算输入；57 条均未取得完整历史证明，不将它们重复计成已执行的工具结果 |
| missing_source | 是否缺失为 N/A，未调查 | 尚未执行查询，不能把未授权当成数据缺失或报告缺失为 0 |
| unverified | 57/57 | 逐条 actual_verification_attempted=false；真实重算 0/57 |

原 57 个 annotation 的完整模型观点集合恢复为 **0/57 本轮完成、57 待核验**；M2 总数及原 93 个之外的新增数量为 N/A。当前快照观察性相等/不等数量同为 N/A，没有把缓存一致或合成结果计入。各项原行级核验成功与本表是不同层级，不能合并样本数。

## 5. 尚不能独立证明的历史状态

当前 SQL 取得的是观察时存在的原 annotation 及模型观点，不能证明冻结时相同集合未曾增删、标注状态/error_code 未改、人工修订链未被删除，以及原标注在冻结事务中确实是被选中的版本。M3 的 previous_id/action/created_at 可检查链完整性、关联、断链或分叉，但即使表中没有修订，也不构成历史“从未修订”的证明。

代码中的 `current_reviews_sql()` 按成功优先/最新 annotation ID、当前最大人工修订 ID 选择有效状态；这不是 as-of 回放。人工 confirmed 同样产生人工观点快照，不能因不是 corrected 就继续沿用 model 集合。存储代码存在关联历史删除路径；这里仅说明证明能力，未查询实际删除事件，不能据此断言已发生删除或外部历史材料不存在。

原 review.json 缺少执行事务时间/快照 ID/LSN，inventory 未保存逐评论完整 state 和键集合。归档 frozen_at 是后来的归档时间，不能作为原运行 as-of 截止时间。当前事务标识也不能恢复以前的事务视图。

若 M1–M3 仍不足，后续所需**最小额外材料**应由数据保管方先登记位置、时间/版本及授权：与原 run 可绑定的历史导出/备份，只投影同 57 条评论的内容版本、原 57 个标注的全部必要状态、完整观点元数据及适用人工修订状态；quote 仍优先只取哈希/区间。若历史有效状态确为人工来源，则先另列该确切修订的观点元数据范围，不能自动读取人工正文。还需该历史副本/审计的保留、删除完整性和原运行关联证明。

本轮未搜索或打开任何备份，也未读取其他 annotation、其他评论或全库存。不存在可明确定位的历史来源时，先保留阻塞，不扩大扫描寻找碰巧哈希匹配的组合。

## 6. 是否支持受限探索性离线实验

目前材料可支持输入映射、协议审查和合成工具验证，**不足以把实际历史 E1 或 48 个可信批次视为已具备条件**。M1–M3 尚未执行，也不能提前断言“只缺封存”。

若将来取得完整当前集合、来源校验通过且观察性摘要相符，但历史证明仍不足，可供人工考虑另立“当前观察、历史未证明”的探索性方案。需独立版本化输入、明确混合采集时点和源自冻结候选的限制、保留全部冲突，另行批准来源使用、上下文政策与实验范围。它不等于复现历史基线，不发布为正式历史 E1 质量验证，也不将 history_unproven 自动升级成可信来源。当前没有执行此实验或批准其验收门槛。

## 7. 独立来源封存准备与 48 批次条件

本轮只整理审核材料：原独立 SQL 观察/回执和其旧固定摘要、冻结归档引用、M1–M3 方案/参数摘要、保护清单、逐评论/列表未执行状态、测试与审计日志。上阶段 seal-request.draft 的历史证明、外部摘要、批准人和批准记录仍未填写。本轮新增哈希只用于防漂移，不标为外部批准。

独立保管方仍需审核以下材料并执行封存，而非由准备程序自证：

1. 实际 M1–M3 读取授权、采集回执、时间/事务观察版本、SQL/参数摘要和完整行数；保留前后两个观察时点及差异，不用后一次覆盖前次。
2. 与原运行关联的可信历史状态/完整成员证明和 57 条逐项结果；任何缺项、冲突保留，历史证明未齐不发布完整集合。
3. 历史证明完整后，才从独立来源投影恰好 93 个 SourceOpinion；额外观点只作为快照证明附件，不加入候选来源集合。没有独立字段就停止，不用待测 batch 补值。
4. 在准备程序不能自行改写的有版本、访问控制的独立位置保存材料。分别记录原文件字节摘要、SnapshotInput / SourceCollection 的项目规范 JSON 摘要，固定汇总回执及 run/manifest/scope/inventory/profile/space/builder 绑定。
5. 由授权保管方记录人员、时间、审批和变更流程，通过可信渠道提供预期来源集合/回执摘要。自产摘要、Git 忽略或普通只读标记不能代替此项。

**实际 SourceCollection：未创建；独立封存：未完成；外部固定摘要：未提供。** 没有在真实数据上调用 prepare_trusted_batch()。

48 个 query-mode 的实际结果为：准备成功 0、准备失败 0、未尝试 48，成功率 N/A（尝试分母 0）。Q17-F、Q22-F 两个空候选列表同样未准备，不能免除全局来源完整性与封存要求。私有 batch-statuses.json 保持原候选数量/列表身份，没有重排或调整 U/F、Top-K。

进一步准备全部批次仍需：93/57 行级来源完整且与后续观察一致、57 条完整历史快照证明、独立固定的来源集合/回执、可信外部预期摘要及原 scope/run 一致。准备接口本身不验证人员独立审批或历史真实性，不能倒过来用接口通过证明这些前提。正式人工充分性评审还需真实 QueryContract、上下文政策、独立评审/裁决及参考封存流程获确认；0/1/2 标签继续保留原用途。

## 8. 实际测试、命令与错误

| 实际执行 | 结果 |
|---|---|
| 原归档 audit.py --verify | 通过，退出码 0；12 文件及离线评分核验一致 |
| 本轮 offline_preflight.py | 通过，退出码 0；前序保护哈希、回执绑定、57 对数组及 M1–M3 静态白名单通过；数据库连接/查询均 0 |
| 历史快照专项 | 21/21 通过，0 失败/错误/跳过；全部合成 |
| 适用组合回归（含上述专项） | 177 项，175 通过、0 断言失败、2 错误、0 跳过；退出码 1，不报告全绿 |
| 收尾保护与隔离 | 218 个已有文件哈希未变；私有输出无跟踪文件；报告链接/隐私/空白检查通过 |

两项既有错误实际再次出现，完整保存在本轮 existing-queue-errors.log：

- `test_review_scope.ReviewScopeTests.test_run_date_filters_incremental_skip_and_scoped_progress`
- `test_review_scope.ReviewScopeTests.test_run_retry_and_cutoff_forwarding_with_real_mocked_sdk`

二者仍是临时 SQLite 进入持久标注队列后报“持久标注队列只支持 PostgreSQL”。未修改业务逻辑、跳过或记为通过。测试中的 SDK 调用为合成模拟路径；测试进程设置网络/psycopg 连接失败哨兵，未获准的数据读取不会因回归自动发生。

初次收尾检查将错误消息出现次数当作错误数量，因每段堆栈同时含源代码和异常末行而触发一次统计断言失败。已改为核对测试 ID 与异常末行，记录于 final-check-initial-error.json；未改测试、日志或业务代码，回归结果仍为两项既有错误。

本轮没有新增或修改专项测试。复用的 21 项覆盖完整一致、缺少其他观点、缩减数量、修订/标注版本变化、计数错误、状态字段缺失、快照冲突、当前观察不能冒充历史、缺外部固定摘要、来源身份/引用、空候选和顺序错误；断言实际哈希/分类/原因。组合还包含 e1_readiness、evidence_judge、query_contract、rag_corpus、retrieval_evaluation、review_scope。判断器和批次接口只在合成测试中执行。

实际命令（仓库根目录）：

```powershell
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/step4b-phase2c0c-20261104/offline_preflight.py
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/private-archive/step4a-20261104-human6/audit.py --verify
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/step4b-phase2c0c-20261104/run_checks.py
git diff --check
git status --short
```

offline_preflight 使用独占创建文件，已生成的审计产物不覆盖重跑。run_checks 的专项和回归分开记录，21 项已经包含在 177 项中，不能相加称 198 个不同测试。没有执行 PostgreSQL 集成、全仓/前端测试、新检索、向量操作、真实快照重算、真实 E1、外部模型或人工充分性标注。结构测试通过不等于真实来源完整或语义质量通过。

## 9. 结论与准确停止点

**本轮状态为“离线预检完成，M1–M3 未授权执行”，不是“完整观点集合已恢复”。** 原 93/57 行级证据未变化；本轮完整历史快照重算 0/57、未核验 57/57，新增观点数未知，可信批次 0/48，尚不具备进一步正式准备 48 批次或运行 E1 的条件。

最先需要的是用户针对第 3 节 M1–M3 的独立明确授权，并明确是否允许为现有 PostgreSQL 只读连接申请必要执行权限。授权仅用于原 57 对评论/标注及列出的元数据；不包含额外正文、人工观点/备注、备份、全库存、向量、E1 或模型推理。其后仍需根据实际结果判断历史证明是否足够，不预先保证 57 个快照能恢复或匹配。

当前已完成获准部分，到此停止。后续来源读取、历史材料授权、独立封存、人工参考与实验审批均保持待办。
