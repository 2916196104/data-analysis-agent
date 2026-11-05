# RAG Step 4B：E1验收前置复核

日期：2026-11-05，Asia/Taipei。

**人工裁决已完成并通过本轮离线一致性校验：24/24份共享契约、48/48个CASE、4/4组同输入配对，145条事件及最终完成事件E0145均有效。无须重做人工裁决。正式可信E1仍阻塞：57条快照均为history_unproven，独立来源封存与外部固定摘要尚缺；可信批次0/48，未尝试48。本轮不运行真实E1。**

## 1. 范围与执行顺序

先向用户说明当前进度、历史来源及封存阻塞和最小离线方案，再检查项目已有4B-2b、2c-0来源恢复、快照后续、M1–M3执行报告、探索输入与人工裁决报告，以及e1_review_protocol、e1_preparation、historical_snapshot、human_adjudication、exploratory_e1、exploratory_e1_metrics和原归档审计。

只使用原归档、已获准保存的来源观察/回执/核验结果、探索输入及最新裁决。没有连接数据库、重复M1–M3、搜索历史备份或其他私有来源，没有读取新评论、向量、CSV、凭据或模型输出。

保持reviews_csv / reviews-csv、商品100013588055、UTC 2021-09-11至2022-08-26、BGE-M3/1024维、quote-utf8-v1和200条索引边界。既有暂存与未暂存改动保留，本轮没有修改业务代码、协议、旧报告、原评分或旧私有材料。

## 2. 最新人工裁决的实际结果

输入为 `outputs/rag-retrieval-quality/baseline-v1/e1-human-adjudication-session (3).json`，已原样备份到本轮私有目录；SHA-256：

`b5e722b632e6639b6ebcedbe883edd0ca4f2699e5460ae8cf2b75c6c719b171b`

绑定的bundle规范摘要仍为 `f0ee9e6d2a0bcaa16baf17118d97fe7da9843098b919a6335160dd2729ef7b5a`。最终完成事件E0145的记录时间为UTC `2026-11-05T07:14:08.651Z`，即台北15:14:08.651。这是原页面记录，不是本轮重新审批或独立可信时间认证。

| 检查 | 实际结果 |
|---|---:|
| 完整事件链、顺序、内容/声明摘要及前事件绑定 | 145/145有效 |
| 活动契约、选择方案、确认理由、版本及待修改草稿检查 | 24/24有效 |
| CASE契约版本、事实、关系、许可/缺失集合、引用身份与区间、本人确认 | 48/48有效 |
| 同输入配对一致性 | 4/4，包括Q05/Q07的实际统一裁决记录 |
| 最终完成事件及全部当前确认指针 | 有效 |
| 当前逻辑冲突、过期确认、缺失条目 | 均为0 |
| 人工参考状态分布 | sufficient 19、limited 0、insufficient 29，合计48 |

这是对现成人工结论及来源映射的结构复核，不是重新判断222条相关性，也不保证人工语义判断绝对正确。继续保留单人复核及曾接触相关性评分的声明，不伪造第二评审人或新批准时间。

原有CLI退出0并生成48条 `references.pending.json`，完整补齐来源身份，绑定活动契约、原确认事件、来源和上下文摘要。原始会话未改动。

需要区分：`ready_for_formal_reference_freeze=true`表示已具备进入参考发布/冻结准备的资格，**不是E1来源就绪标志**。该CLI按既有设计仍输出human_confirmed=false、formal_confirmation_record=null、usable_for_evaluation=false的待发布包装。本轮保留这些值。后续可复用现有真实确认记录进行授权范围内的正式格式转换和封存，不要求再次逐题逐CASE审批，也不能只翻转布尔字段冒充正式发布。

## 3. 冻结身份与行级来源复用

原 `audit.py --verify` 本轮实际返回sealed_archive_verified、12个文件通过；原排名、评分复算及相关性一致性审计仍有效。

| 身份 | 值 |
|---|---|
| 原run_id | `a2f4907ec8cacc6bae4b52f8d89a9ee557ae09f1f4e7a7f3c6f3803b34848f33` |
| manifest规范摘要 | `a17e77d4f7c5d83c5b1c80bad318b374432f5750a9151c826f451feb52b5c3f8` |
| scope摘要 | `167e14701623dc709a96ce8761a84f7a89c8eaba9c34f6f71025e2dc89e7f766` |
| inventory快照引用 | `92e3c0ec0ff391e38110affb086def5e7b7a6ccd6412dea6f56cbe1133006ecd` |
| 原归档清单文件摘要 | `f6afd216f987314de08b089ffadd8e9e6dcc867d79a11f40dc4771b775bf7b47` |

复核既有注册中的本地完整性锚点、M1–M3材料清单、行级比较及回执引用。下列是旧采集材料的文件字节摘要，本轮核对相同，**不是新取得的外部可信pin**：

| 已有材料 | SHA-256 |
|---|---|
| 行级独立观察 | `373ee19353dd3aa89c0aa55d0c7d20031af4e1e5e9a03e89eed1dc44bcf11295` |
| 行级采集回执 | `eee8bd39899fff0a0d374ad709f95ae71535ff0ecfe7d727decf9bc3fd73a973` |
| M1–M3观察 | `6f6806c5d458192bfc38f8101e6196e86bf94989f6e1828701b6c85bd1e080a5` |
| M1–M3回执 | `4881beff9da10f0ee5981718c862b2f4ed87c69c8e30181a38d6885b1e6b19bd` |
| 57条观察性快照结果 | `f7a1f7af30bd6cde687c9ead6ea26bb8cf0deca4920960fa7aa3d068f30700b7` |

既有行级结果仍为观点93/93、评论57/57、原标注57/57，缺失/版本差异0。93条逐观点比较记录的全部已登记检查均通过，覆盖原222条排名引用。本轮复用结果并核对完整性，没有重新恢复或读取数据库。

原两次采集观察时间分别为UTC `2026-11-04T02:15:48.466614+00:00` 与 `06:46:15.772789+00:00`。二者均为只读repeatable read事务并rollback，但即使事务观察字符串相同，也不能视为一个历史不可变快照。

## 4. 历史快照及独立封存阻塞

M1–M3已完成的当前观察为57/138/0行；原57个annotation的完整模型观点集合覆盖57/57条评论，含原93个观点和额外45个元数据。额外45个没有加入候选或SourceCollection。

| 分层结果 | 数量 |
|---|---:|
| 当前指定集合的观察性快照重算相同 | 57/57 |
| rebuilt_match / rebuilt_mismatch | 0 / 0 |
| history_unproven | 57 |
| missing_source / unverified（已有观察核验层） | 0 / 0 |
| 具有独立历史证明 | 0/57 |
| 正式历史匹配率 | N/A，已证明历史输入分母0 |

仍无法证明原运行时的有效annotation选择、processing_status/human_status/opinion_source/human_revision_id/error_code、完整成员集合及中间增删/修订的历史有效性。当前M3为0行不能证明历史上没有修订；当前重算一致也不能自动升级为rebuilt_match。

既有seal-request仍为pending，独立保管方批准记录、SourceCollection规范外部摘要和回执外部摘要均为空。本轮可用材料没有完整历史导出/历史回执或独立封存证明。本地文件哈希只证明本地完整性，不替代历史性、采集独立性或封存批准。

`historical_snapshot.verify_historical_snapshot()`明确要求historical_export及独立固定历史输入/回执摘要，才能发布历史重建结果。`prepare_trusted_batch()`校验外部固定来源、回执、范围、完整键集合及排序，但它本身不能证明保管方独立性和历史真实性；因此不能用“接口接受了自产哈希”反向证明前提满足。

## 5. 最小额外读取范围与授权边界

**建议新增当前数据库查询：0次、0行。** 不重复M1–M3；它们的当前集合任务已经完成，重跑无法产生缺失的历史证明。

若坚持正式历史可信E1，先由来源保管方提供可定位的历史材料登记及其与原run的关联。没有明确材料位置、历史版本和权限，不扫描备份或扩大SQL。

| 缺口 | 最小所需材料 / 字段 | 授权边界 |
|---|---|---|
| 原检索历史时点 | 原run绑定的事务/不可变导出身份、实际执行时点，或有审计依据的快照/提交版本引用 | 只读对应运行与版本登记，不把归档时间当检索时点 |
| 历史有效状态与完整集合 | 同一57个review/content/profile绑定的历史annotation选择记录、状态/error_code、published_at、human_revision/source、完整有效观点ID/区间/输入哈希、opinion_count及有效期/增删证据 | 不读额外quote明文或新评论；复用已存原文及已核验元数据 |
| 历史修订与删除 | 对应annotation的revision ID、previous_id、action、created_at、有效/删除或提交版本登记 | 不读人工观点正文、备注或人员信息；当前空链不能替代历史无修订证明 |
| 选择记录不足时 | 仅这57条评论、同内容及profile下、原时点参与有效选择的候选annotation ID/status/created_at/有效期元数据 | 这可能超出原57个annotation白名单，必须单独列范围并获授权，不能直接查询 |
| 独立封存 | 独立采集记录、来源版本、回执、原run/manifest/scope/inventory绑定、只读版本化保管与复核记录、由可信外部流程提供的来源规范摘要和回执摘要 | 来源记录恰为93个候选key；完整集合证明附件可含已有138条元数据，不扩充检索 |

若保管方无法提供原运行历史关联，即使另获读取当前更多表的权限，也不能解决历史可信性；应停止正式历史路线，不为获得通过结果不断扩大读取。

原CSV完整字节复核此前未执行；当前接口要求可信来源登记及file_sha256/line_number绑定，并不自动要求本轮打开整个CSV。若另有正式政策要求全文件字节证明，须明确具体文件/行和核验范围后另行授权。全inventory、向量和其他商品也不在本轮必要新增范围内。

## 6. 全部48个列表及其他E1前提

已检查已有48份探索输入的模型、来源集合摘要、batch/context摘要与bundle一致；逐候选与冻结命中共有字段和顺序一致，222条全保留，93个来源键精确覆盖。两个空列表为Q17-filtered、Q22-filtered。

| 项目 | 本轮结果 |
|---|---:|
| 已有探索输入身份校验 | 48/48 |
| 原候选身份、排名、上下文绑定 | 222/222 |
| 正式SourceCollection创建 | 0 |
| prepare_trusted_batch真实调用 | 0 |
| 严格可信批次通过 / 失败 / 未尝试 | 0 / 0 / 48 |

每个列表已另存严格准备阻塞状态。空列表同样需要全局来源及封存前提，不先算作两个通过。本轮检查已有探索输入不等于重新准备可信批次。

| 其他前提 | 当前状态 |
|---|---|
| 查询契约、独立目标、人工事实/关系/引用及最终完成记录 | 已确认并通过一致性审计，不再重复审批 |
| 上下文 | 已有本人声明为quote_full_review_veto / 1，48个上下文摘要一致；支持仅限quote，完整原文只解释/否决 |
| 人工参考发布与冻结 | 内容和真实记录已齐，已生成48份待发布包装；未代填正式批准、未独立封存。后续转换复用现有事件 |
| 实验来源政策 | 当前仅为exploratory_current_observation_history_unproven、independent_seal=false，不能用于历史可信结论 |
| 预测运行登记 | 既有登记prediction_execution_authorized=false；本轮仅条件式前置核验，未启动预测 |
| 输出审计与评价 | 既有汇总器依赖已独立审计的引用/事实/关系映射；真实预测输出审计执行器、exact-span外引用未覆盖规则及版本化运行交接仍需落实 |
| 正式数值门槛 | 未批准，不擅自设定。缺门槛阻止正式质量通过宣称，不把它等同于人工裁决未完成 |
| 泛化 | 原24题为已见诊断集，不是独立泛化验证集 |

后续探索实验也须在预测前固定参考、活动契约、来源/上下文/规则版本和评价口径，运行时隔离人工标签。不得根据预测回改参考，不得用0/1/2相关性分数驱动判断；规则不能识别语义时单列覆盖不足，不能全部拒答后只报告安全性。

当前真实预测数0，故无支持回答许可率、过度拒答率、三状态一致率、引用有效率等E1指标均未计算；不能报0%或100%。未来保留全部48行的应评、有效、失败/缺输出分母和同query的F-U配对，不把N/A置0。

## 7. 实际命令、测试与隔离

```powershell
.venv/Scripts/python.exe -B -m data_analysis_agent.reviews.human_adjudication --bundle outputs/rag-retrieval-quality/q15-live-validation-fix-20261105/bundle.json --session 'outputs/rag-retrieval-quality/e1-acceptance-preflight-20261105/session.original.json' --expected-bundle-sha256 f0ee9e6d2a0bcaa16baf17118d97fe7da9843098b919a6335160dd2729ef7b5a --output outputs/rag-retrieval-quality/e1-acceptance-preflight-20261105/adjudication-validation
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/private-archive/step4a-20261104-human6/audit.py --verify
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/e1-acceptance-preflight-20261105/check_existing_materials.py
```

以上均退出0。新输出独占创建，不覆盖旧记录。

本轮实际执行test_human_adjudication、test_e1_readiness、test_historical_snapshot、test_exploratory_e1：**91/91通过，无失败、错误或跳过**，均为合成协议测试。测试进程禁用真实网络和psycopg连接；真实材料检查另以哨兵禁止判断器、数据库和严格准备调用。合成通过不等于真实历史来源或语义质量通过。

两项既有SQLite/PostgreSQL队列错误继续保留：test_review_scope下的test_run_date_filters_incremental_skip_and_scoped_progress、test_run_retry_and_cutoff_forwarding_with_real_mocked_sdk；本轮未重跑、未计通过，也未修业务队列逻辑。

私有结果目录为 `outputs/rag-retrieval-quality/e1-acceptance-preflight-20261105/`，含原导出备份/回执、48份待发布参考、契约绑定、48列表阻塞、来源锚点校验、acceptance-summary、测试日志和前后文件保护清单。公开仅新增本报告；没有私有评论或逐条标签进入Git。

## 8. 最终结论与停止点

**人工裁决验收通过，正式可信E1仍然阻塞。现有数据只能支持明确标记history_unproven的非可信探索路线；这不等于本轮已批准或已执行探索性预测。**

下一步先由保管方确认是否存在可定位、绑定原运行的历史材料及独立封存流程。若有，按第5节最小范围另行授权只读；若无，停止追求原历史可信重现，保留旧基线，另行固定探索运行和参考发布安排。无须重做人工作业或重复M1–M3。

本轮到此停止，没有生成判断器预测、正式质量通过结论或48/48可信批次结果。
