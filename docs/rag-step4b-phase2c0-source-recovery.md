# RAG Step 4B-2c-0：历史来源只读核验与 E1 数据准备

日期：2026-11-04（Asia/Taipei）。**最终状态：已授权完成限定只读核验，部分恢复。93 个观点的行级版本及 57 条评论的内容版本均验证一致；完整历史有效快照与独立封存尚未完成，不能进入真实 E1。**

用户已明确授权按本报告第 4 节连接项目现有 PostgreSQL，并允许为该连接申请必要执行权限。本次成功执行 5 个限定范围的数据查询，取得 57 条评论、57 个原标注、93 个原观点、93 条 Embedding 来源元数据及 1 条导入登记。未发现已核验字段的缺失或版本差异；没有将当前数据库自动视为完整历史快照。

没有建立 SourceCollection 或可信 EvidenceBatch，没有调用 prepare_trusted_batch()、判断器或模型。前次受阻报告和失败回执已独立保留；本报告按新增授权与真实读取结果更新。

## 1. 实际结果与统计口径

| 对象 / 检查 | 实际结果 | 解释 |
|---|---|---|
| 冻结输入身份 | 24 题、48 个 query-mode、222 个排名条目 | 离线检查现有归档，不是重新检索 |
| 去重目标 | 93 个观点、57 条评论、57 个原 annotation_id | 93 个观点均为 model 来源；没有读取范围外观点 |
| 行级来源恢复成功 | 观点 93/93；评论内容版本 57/57；原标注 57/57 | 从数据库独立取值，重算摘要和版本键后与冻结身份绑定，非候选副本自证 |
| 行级恢复失败 / 缺失 / 字段差异 | 0/93 个观点；0/57 条评论 | 仅限本次读取和下表已核验字段，不包括未重建的完整历史快照 |
| 当前可见但连行级版本也未证实 | 0/93；0/57 | 全部目标已完成行级版本验证，仍不能等同完整历史有效性证明 |
| 完整历史来源链的部分恢复项 | 93/93 个观点，关联 57/57 条评论 | 行级验证已完成；完整有效状态、独立封存及外部固定来源摘要仍待落实 |
| 已独立重算的历史有效观点快照 | 0/57；57/57 尚未重建 | 未读取其他有效观点键集合，不报告“0 个快照漂移” |
| 缓存快照值与冻结值一致 | 93/93 条元数据，覆盖 57/57 条评论 | 仅为缓存旁证，不是完整快照独立重算 |
| 已准备的可信 EvidenceBatch | 0/48；实际准备尝试 0 | 48 个均未准备，不能算通过；包括 Q17-F、Q22-F 两个空列表 |
| 独立数据库采集 / 操作回执 | 已完成，带事务观察时间和快照标识 | 数据来自独立 SQL 来源；采集时点是当前观察时间，不伪称历史备份版本 |
| 独立封存 / 外部固定来源摘要 | 未完成 | 本轮自产审计哈希不作为外部可信参数 |

分层结论：独立恢复并验证的行级来源为 93 个观点及 57 条评论；仅能确认可见、无法确认行级版本的为 0；已核验字段缺失或漂移的为 0。**完整历史有效状态尚未证实的仍为全部 93/57**，它与行级版本成功是不同层级，不能相加当作不同样本。其他观点的快照元数据、原导入文件及额外历史副本仍未获本次第 4 节授权，不预先读取。

全部 222 个原排名条目的来源共有字段已对照数据库观察记录复核一致，含跨 query/mode 重复引用；原顺序与范围均未改变。这是来源字段比较，不是可信批次准备或充分性评分。所有原候选、U/F 条件、Top-5、评分、备注保持原状。

## 2. 已阅读与离线基线核对

完整阅读并核对 [Step 4A 最终审查](rag-retrieval-quality-final-review.md)、[Step 4B 计划](rag-step4b-retrieval-strategy-plan.md)、[4B-0](rag-step4b-phase0-readiness.md)、[4B-1a](rag-step4b-phase1a-contract.md)、[4B-2a](rag-step4b-phase2a-evidence-judge.md)、[4B-2b](rag-step4b-phase2b-e1-readiness.md)。检查了 QueryContract、EvidenceBatch、e1_preparation、rag_corpus、retrieval_evaluation、serialization、scope、导入/标注表结构、Embedding 来源表及只读数据库连接实现。

初次预检及本次获明确授权后的收尾均实际运行原归档 `audit.py --verify`，返回 `sealed_archive_verified`。12 个文件的归档身份、载荷哈希与当前相关代码检查通过；原评分复算、保存排名与跨模式评分一致性通过。归档审计检查已保存运行，数据库来源另由第 3、4 节的实际采集与比较核验；没有重新运行检索。

| 冻结身份 | 核对值 |
|---|---|
| 数据集 / 商品 | `reviews_csv / reviews-csv`；`100013588055` |
| UTC 日期 | `[2021-09-11T00:00:00Z, 2022-08-27T00:00:00Z)`，包含 2022-08-26 |
| run_id | `a2f4907ec8cacc6bae4b52f8d89a9ee557ae09f1f4e7a7f3c6f3803b34848f33` |
| manifest 规范 JSON SHA-256 | `a17e77d4f7c5d83c5b1c80bad318b374432f5750a9151c826f451feb52b5c3f8` |
| scope_sha256 | `167e14701623dc709a96ce8761a84f7a89c8eaba9c34f6f71025e2dc89e7f766` |
| inventory_snapshot_sha256 | `92e3c0ec0ff391e38110affb086def5e7b7a6ccd6412dea6f56cbe1133006ecd` |
| 归档清单文件 SHA-256 | `f6afd216f987314de08b089ffadd8e9e6dcc867d79a11f40dc4771b775bf7b47` |
| review.json 文件 SHA-256 | `c6ca491983c23b736c43d2ac93118ec9733af36a813ac68aaec9ec2a375eb2b2` |
| labels.human.json 文件 SHA-256 | `c5a6946005e5a0b5b6f2bd63342e67d3df3f149d380c9b885440dd54dcaa7225` |
| metrics.json 文件 SHA-256 | `477f3798bf906c4550029ecb1fd93249ecbbd21bd7c26847233c9a7b46df3749` |
| label_profile_key | `1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304` |
| 原标注身份 | `Pro/deepseek-ai/DeepSeek-V3.2`；prompt `review-opinions-v1-e61de599683cba6703c2f61e85125d0be4627c2763440486aa717e4bc884036e` |
| EmbeddingSpace | `embedding-space-v1:11e6754662dbd13fc56c6e624bc9416c1c2766f962787267fd4a57b1fa8d622e` |
| 模型 / 文本版本 | `BAAI/bge-m3`，revision `5617a9f61b028005a4858fdac845db406aefb181`，1024 维；`quote-utf8-v1` |
| 冻结索引 | 200 条，原有效观点 1091 条；没有重新清点在线库存、读取向量或扩大索引 |

离线评分审计仍对应 222 条评分、60 个共享 query/key 跨模式评分对且冲突为 0。没有修改人工评分、旧备注或其解释；这不是充分性人工参考。Q01/Q09 备注解释、Q13 过滤遗漏和索引覆盖限制继续沿用前序报告。

## 3. 授权、实际采集与历史错误

本次用户明确确认：“我明确授权按报告第4节方案，连接项目现有 PostgreSQL，只读核验冻结的93个观点、57条评论及关联版本字段，允许为该连接申请必要的执行权限。”本次权限申请后成功执行原限定采集方案；授权只覆盖第 4 节，未扩展到其他观点、完整快照、原文件或 E1。

| 本次实际采集 | 结果 |
|---|---|
| 采集时间 | UTC `2026-11-04T02:15:48.332008+00:00` 至 `02:15:48.497007+00:00`；台北时间约 10:15:48 |
| 数据库观察快照 | `2026-11-04T02:15:48.466614+00:00`，事务快照标识 `1000351:1000351:`；是本次观察版本，非历史备份时间 |
| 只读 / 隔离 | 实际检查 transaction_read_only=`on`、transaction_isolation=`repeatable read`；结束执行 rollback/close |
| reviews / review_annotations | 57 / 57 行；均为白名单内原 ID |
| review_opinions / Embedding 来源元数据 | 93 / 93 行；精确限制原观点 ID/key、原 profile/space/builder；未读取向量列 |
| import_runs | 1 行，仅关联导入登记 |
| 业务查询 | 5 个限定 SELECT；无范围外观点、业务写入、提交、服务启动、同步、推理或 E1 |
| 执行 / 比较错误 | 本次均为 0；采集及后续纯离线复核均退出码 0 |

下列是**明确授权之前的历史失败**，原文件保留，不能混入本次成功结果或删除：

| 实际动作 | 结果 |
|---|---|
| 普通执行环境调用私有只读采集脚本 | 退出码 1；DatabaseError；连接失败；没有进入业务 SELECT 或取得事务快照 |
| 同一目标连接诊断 | psycopg OperationalError，SQLSTATE 为空；后续诊断识别到 permission denied、10013 / 0x0000271d |
| 在允许本地连接的环境申请重试 | 当时自动审批拒绝，进程未启动；当时没有 attempt2 来源产物 |
| 拒绝原因 | 提升权限后将读取私有评论；虽然只读且范围有限，但任务要求用户明确授权，未见用户实际确认 |
| 拒绝后的处理 | 停止数据库连接与来源读取；只完成离线审计、方案及本报告 |

前次发生 1 次采集连接尝试和 2 次同目标连接诊断，均未成功建立连接；另有 1 次重试申请被拒绝、未执行。此后等待用户明确授权，本次才成功执行 `--attempt2`。此前系统权限错误不是来源缺失，本次已解决连接与授权阻塞。

前次 `execution-status.json` 及失败回执保留当时状态；本次 `attempt2-collection-receipt.json` 使用 `explicit_user_confirmation_20261104_report_section_4_only`，由 `authorized-resume-status.json` 记录新增授权后的最终状态。原失败回执中的助手授权解释不被事后改写成用户确认。

## 4. 已获明确授权并实际执行的只读方案

以下方案已在项目现有 PostgreSQL 中成功执行。本次只连接项目现有配置，不打印连接串、密码或端点，不自动迁移/初始化数据库。

所有正文读取都同时受固定 source、dataset_version、商品、UTC 日期、57 个精确 review_id 限制；标注和观点还受原 57 个 annotation_id、93 个 model_opinion_id 限制。完整键白名单保存于私有 `requested-scope.json`，文件 SHA-256 为 `71aba133248320396e476151dfe6d86270ac20f62d6b78267709f9000ae1376d`；它是访问范围清单，**不是独立来源集合或外部可信来源摘要**。

| 来源 | 实际读取的最小字段与用途 |
|---|---|
| reviews，仅 57 条 | source/dataset/product/id、content_sha256、published_at、text、import_run_id、line_number；并在内存使用 title/rating/asin/user_id 重算原内容摘要，随后丢弃这些辅助字段，不留存或公开用户身份 |
| review_annotations，仅原 57 个 ID | id/review_id/content_sha256、requested_model/base_url/prompt_version、status/error_code/created_at；重算 label_profile_key 后不输出 base_url |
| review_opinions，仅原 93 个 ID | id/annotation_id、aspect/sentiment、quote、quote_start/end；不查询同评论其他观点 |
| review_opinion_embeddings，仅原 93 个 key、原 profile/space/builder | 只取身份、内容/输入/快照哈希、published_at、观点修订身份、区间、失效和同步时间等来源元数据；**不读取 embedding 向量列** |
| import_runs，仅上述评论关联的导入 ID | id/source/dataset_version/kind/file_sha256、started_at/completed_at；不打开原导入文件 |

连接使用只读模式，开启 REPEATABLE READ READ ONLY，实际检查 transaction_read_only=on 和隔离级别，设置语句超时 20 秒、锁等待超时 3 秒，最后 rollback/close。没有修改业务表。

实际比较路径：从数据库独立取得值 → 重算 reviews 内容摘要 → 核对 annotation 的内容哈希和原 ID → 重算 label_profile_key → 以数据库实际观点字段调用现有 `_document()` 重算稳定版本键 → 对照冻结 key、原文、quote、Python 字符区间、输入 UTF-8 哈希和文件引用。另用不连接数据库、不导入判断器的离线脚本独立重算稳定键，覆盖全部 222 个排名条目。没有用冻结 quote 填补来源，没有把缓存快照值当作独立重算结果。

`review_content_sha256` 是规范化导入记录的摘要，包含正文之外的字段，不是简单的正文 SHA-256。上述辅助字段仅用于这 57 条记录的版本重算。若不允许读取其中某项，必须将重算标记为未完成；不能改用 quote 哈希替代。

### 4.1 字段级实际结果与版本差异

| 字段 / 身份 | 实际结果 | 证明范围 |
|---|---|---|
| review_content_sha256 | 57/57 重算与 reviews 保存值相同，并与 annotation、Embedding 来源记录相符 | 再以重算的观点版本键匹配冻结 key，绑定原内容版本；不是对 quote 或正文单独取哈希替代 |
| review_id / annotation_id | 57/57 关联精确一致，原标注均 success | 未换用当前最新标注；未查询其他 annotation 代替缺项 |
| opinion key / 修订身份 | 93/93 重算键相符；model_opinion_id 对应，human_opinion_id/human_revision_id 均与冻结 null 相符 | 证明原返回观点版本，不能证明冻结时完整有效修订集合已恢复 |
| review_text / quote / 字符区间 | 57/57 原文与归档一致；93/93 quote、区间及切片一致 | 未 trim、拼接、规范化或补写文本 |
| embedding_input_sha256 | 93/93 实际 quote 的 UTF-8 SHA-256 相符 | 未编码或读取向量 |
| published_at | 57/57 带时区且在授权 UTC 区间，93/93 缓存时间与对应评论相同 | 归档未逐 hit 保存时间明文；由规范化内容摘要与冻结版本键间接绑定，不宣称进行了不存在的冻结时间逐值对比 |
| aspect / sentiment | 93/93 与原观点和缓存相同 | 只核对标注身份，不判定相关性或充分性 |
| label_profile_key | 57/57 从原标注模型、base_url、prompt 重算匹配；93/93 缓存身份相符 | 原标注身份保持，未调用标注模型 |
| text_builder_version / EmbeddingSpace | 93/93 来源元数据匹配原 quote-utf8-v1 与固定空间 | 不代表重新核验了 200 条全库存或实际向量值 |
| file_sha256 / line_number / import_runs | 93/93 evidence_ref 与独立关联导入登记及行号一致；导入登记 1/1 | 原文件字节未重新读取；不冒充已独立验证 CSV 文件 |
| effective_snapshot_sha256 | 93/93 缓存值与冻结值相同，关联 57 条评论；独立完整重算为 0/57 | 缓存旁证；不能证明历史有效观点完整性或当前最新有效状态 |
| invalidated_at | 93/93 保存为 null | 只说明缓存标记；未据此推断不存在后续人工修订或来源变化 |

**已核验字段没有缺失或版本差异：观点 0/93、评论 0/57。** 合法的 human 来源字段 null 不算缺失。对于尚未恢复的完整快照、未读取的历史状态和原文件字节，差异结论保持“未验证”，不填 0。没有查询范围外记录、删除不一致候选或自动修复数据。

## 5. 需额外列明的快照与历史来源操作

**现有 read_effective_corpus() / inventory 不能直接用于首次核验。** 它们会读取更宽的当前有效语料；当前选择逻辑也不能证明历史有效版本。冻结 key 的逐行恢复与完整 effective_snapshot / inventory 证明是不同层级。

| 可能追加的操作 | 所需最小额外范围 / 元数据 | 本轮状态 |
|---|---|---|
| 重算 57 条评论的完整有效观点快照 | 仍限这 57 条评论、原标注/profile；需要每条完整有效观点键集合及 opinion_count，包括未进入冻结 93 个 key 的观点 | 未查询、未计数，额外观点数量未知；先申请元数据授权 |
| 重建快照 review state | 原 review_content_sha256、published_at、annotation_id、human_revision_id、processing_status、human_status、opinion_source、error_code；有效状态必须有历史版本依据 | 不能以当前“最新”标注/人工修订代替冻结时状态 |
| 缺少已保存稳定键时重算其他观点键 | 同评论其他有效观点的来源 ID/修订 ID、aspect/sentiment、字符区间及原 quote 哈希；若未保存哈希，需要另行批准读取相应片段或用已批准原文的精确区间计算 | 不预读其他 quote；不新增评论正文范围 |
| 验证原导入文件真实性 | 目前冻结 evidence_ref 指向 1 个文件哈希；需先通过可信登记确定准确文件/副本和指定行，定义文件全字节摘要验证范围 | 没有访问文件路径或原 CSV；import_runs 登记相符也不等于已重新验证原文件字节 |
| 访问独立历史备份/快照 | 指定副本、历史时间和版本登记，只提取相同 93/57；登记访问人与来源摘要 | 未搜索或读取其他私有目录/备份；需明确位置和授权 |
| 独立重算全 inventory 摘要 | 将涉及 93/57 之外的冻结 200 条及更宽有效状态；须另列精确元数据范围，证明是否还需向量摘要 | 本轮不执行；不能用现有 200/1091 数字冒充在线核验 |

`_review_state()` 的摘要包含完整 state 和排序后的全部有效观点键。仅原返回观点集合不足以证明其值；Embedding 表保存的 effective_snapshot_sha256 即使与归档相同，也只是缓存旁证，不能证明当前来源仍完整，更不能单凭它恢复历史快照。

## 6. SourceCollection、独立封存与 48 批次条件

**本次已取得独立数据库观察记录及成功采集回执，但没有完成独立封存。** 采集器只从冻结运行取得定位白名单和比较目标，来源字段来自独立 SQL 读取；观察文件不含 query/mode/rank 或人工分数，未从待测 EvidenceBatch 复制数据。回执记录授权、采集时间、只读事务/观察快照、脚本摘要、5 个查询模板及参数摘要/行数，并绑定原 run、manifest、scope、inventory 和归档清单。

当前数据库观察证明行级版本与冻结承诺相符，不能把本次事务快照时间当作原 Step 4A 的完整历史快照版本。数据只保存在本地忽略目录，尚未由另一人员/独立流程封存或固定外部摘要。

| 本次审计文件 | 文件字节 SHA-256，仅用于审计，非外部可信输入参数 |
|---|---|
| attempt2-current-source-observation.json | `373ee19353dd3aa89c0aa55d0c7d20031af4e1e5e9a03e89eed1dc44bcf11295` |
| attempt2-collection-receipt.json | `eee8bd39899fff0a0d374ad709f95ae71535ff0ecfe7d727decf9bc3fd73a973` |
| attempt2-source-comparison.json | `1309b57d5b0157097879757033eea1b6eb4995c19451495b12ae7f2fa99270ca` |
| 本次采集脚本 | `3d3e04343b8df20b8a5babb5031ea32488b1508676ca6b4b12b817ae5b82140b` |

没有把这些本轮自产摘要再次传给 prepare_trusted_batch() 作为外部固定参数；没有创建真实 SourceCollection。回执、摘要与文件都不是 WORM 封存、数字签名或独立批准。

剩余条件与状态：

1. 原 93/57 行级采集、身份、区间和文件登记比较已完成；原文件字节复核未执行。若要求文件字节级证明，应另外明确授权文件和核验方式。
2. 完整历史有效观点快照及 review state 尚未重建，额外元数据范围见第 5 节；不能只用返回观点或缓存快照补足，也不自动扩大读取范围。
3. 将完整来源集合与采集回执单独封存，由授权人员/独立流程固定外部 SHA-256，并绑定原 run/manifest/scope/inventory 与归档清单。仅设置 collector_id、把文件放入 outputs、或本脚本自产摘要均不等于完成独立封存。
4. 只有前提全部满足后，才允许构建真实 `SourceCollection(data_kind='frozen_query')`，然后按原 query/mode/rank 分别调用既有 prepare_trusted_batch()，保留全部 48 条结果和错误，包括两个空列表。

当前准备接口要求来源键集合精确覆盖整个冻结运行的 93 个键，校验外部来源集合/回执摘要、run_binding、manifest、inventory、U/F 条件和原顺序。空列表也受同一完整来源及外部固定摘要前提约束；不能因没有候选便先记为 2 个通过。接口本身不会证明外部封存或人的独立性，不能用它反过来认证一个自建临时来源集合。

48 个列表的未准备状态及阻塞原因逐一记录在私有 `batch-readiness-blockers.json`。没有调用该接口、构造真实 EvidenceBatch、生成充分性预测或人工参考状态；两个空列表也没有例外记作通过。

## 7. 文件、实际检查与未执行项目

公开交付仅更新本报告。私有工作目录为 `outputs/rag-retrieval-quality/step4b-phase2c0-20261104/`，保留前次范围白名单、失败回执及受阻报告副本；新增本次 attempt2 来源观察、成功回执、逐观点比较、纯离线验证脚本与结果、48 列表阻塞清单、授权后状态和最终保护检查。原采集脚本仅更新授权依据，读取边界不变。全部私有材料受既有 `outputs/` 规则忽略，不进入 Git。

前序 2b 的 4 个未提交文件保留原状：报告、e1_preparation.py、e1_review_protocol.py、test_e1_readiness.py。没有修改已有业务代码、API、Agent、数据库、评分或历史归档，没有暂存/提交。

| 本轮实际检查 | 结果 |
|---|---|
| 归档 audit.py --verify | 通过，12 文件，原封存摘要一致；含原评分与一致性离线复核 |
| 冻结范围去重与身份检查 | 24/48/222、93/57、57 annotation_id；run/manifest 绑定一致；93 个来源均为 model |
| 实际只读采集 | 真实 PostgreSQL 执行成功，5 个限定数据查询，返回 57/57/93/93/1 行；只读事务实际检查并回滚 |
| 来源逐项比较 | 93/93 个观点通过采集脚本的全部行级检查；57/57 内容摘要及原文匹配；缺失/差异为 0 |
| 纯离线独立复核 | 按精确 ID 验证完整性、重复、UTC 范围、脚本/观察文件哈希，再独立重算版本键，复核 222/222 排名引用；通过，不调用数据库或判断器 |
| 48 列表准备 | 0/48，未尝试；逐列表阻塞原因已登记；不能算准备通过 |
| 保护与 Git 边界 | 原 189 个既有文件收尾逐项哈希一致；12 个归档文件及前次失败回执不变；私有目录无跟踪项 |
| 空白 / 文档隐私 | git diff --check 及新增报告检查通过；没有复制真实原文、逐条评分、用户标识或私有评审备注 |

本次明确授权后实际主要命令如下，均成功完成；`--attempt2` 在本次获准执行，未覆盖前次失败记录。采集、离线来源复核和归档审计均退出码 0。

```powershell
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/private-archive/step4a-20261104-human6/audit.py --verify
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/step4b-phase2c0-20261104/recover_sources.py --attempt2
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/step4b-phase2c0-20261104/verify_authorized_observation.py
git diff --check
git status --short
```

历史错误继续保留：前次普通采集连接失败、两次连接诊断、一次被拒绝且未启动的重试，以及一次初始离线统计命令括号错误（SyntaxError）。本次没有这些错误；旧记录没有改写或算成行级恢复失败。

未新增/运行合成质量测试来替代真实来源检查；未运行判断器、真实 E1、外部模型、向量同步、真实检索、前端或全仓回归。此前两项 `test_run_date_filters_incremental_skip_and_scoped_progress`、`test_run_retry_and_cutoff_forwarding_with_real_mocked_sdk` 的 SQLite/PostgreSQL 队列错误继续保留，**本轮未重跑、不算通过**。

## 8. 明确结论与停止边界

**结论：本次获准的只读核验完成，状态为“部分恢复”。93/93 个观点的行级版本和 57/57 条评论的内容版本与冻结身份一致，已核验字段缺失/漂移为 0；完整历史有效快照尚未恢复，独立封存与外部固定摘要未落实，可信批次为 0/48，仍不满足 E1 可信输入准备条件。**

连接与首次读取授权阻塞已解除。剩余阻塞为：第 5 节的额外完整快照元数据授权及历史状态依据；第 6 节的独立封存/外部固定摘要流程；若要求原文件字节级证明，还需另行授权具体文件核验。不能读取其他观点补齐、用当前最新状态替代历史状态、用自产摘要自证或自动生成 SourceCollection。

真实 E1 另需前序 2b 所列人工查询契约、独立充分性参考、上下文政策和实验质量门槛确认。本次没有批准任何门槛或生成预测/参考答案，没有业务写入、模型推理、向量操作或 Agent 集成。完成本报告更新后停止，不自动追加元数据读取或进入 E1。
