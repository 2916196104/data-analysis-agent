# RAG Step 4B-2c-0c：M1–M3 元数据只读核验执行结果

日期：2026-11-04（Asia/Taipei）。**本次明确授权的三项查询已成功执行。原 57 个 annotation 的完整模型观点集合已恢复，共 138 个观点元数据，其中原冻结观点 93 个、额外发现 45 个；当前可见人工修订链返回 0 行。57 条观察性快照均重算并与冻结摘要一致，但缺乏独立历史状态证明，正式分类全部为 history_unproven。**

原[离线预检报告](rag-step4b-phase2c0c-metadata-verification.md)及其材料保留原样，不事后改写为已执行。没有运行真实 E1、判断器预测、新检索、外部模型、向量同步或 Agent 集成；可信批次仍为 0/48。

## 1. 授权、范围与文件保护

本次用户明确授权执行原预检报告第 3 节 M1、M2、M3，并允许为限定只读连接申请必要执行权限。本次权限申请获准后执行成功；没有再次要求用户确认已授范围，也没有绕过权限控制。

精确读取范围为 `reviews_csv / reviews-csv`、商品 `100013588055`、UTC `[2021-09-11T00:00:00Z, 2022-08-27T00:00:00Z)`，原 57 对 review_id/annotation_id 及对应 content_sha256。使用原已审查 SQL 与参数，无查询扩张：

- M1：原评论与标注的状态、版本、范围及 profile 身份；base_url 仅在内存计算 profile 后移除。
- M2：原 57 个 annotation 下全部模型观点 ID、关联、aspect/sentiment、字符区间及数据库计算的 quote SHA-256，不返回 quote 明文。
- M3：同一范围的人工修订链 ID、previous_id、action、created_at 等已审字段；不读取人工观点、正文、评审备注或人员身份。

没有读取其他商品/评论、新正文、CSV、历史备份、其他私有目录、向量或全量库存，没有数据库写入。原 200 条索引保持原状；额外发现的 45 条是来源元数据，不是新写入观点、扩容索引或新增检索候选。

公开仅新增本报告。实际采集、回执、逐评论快照结果、观点版本键、审计与测试材料位于 Git 忽略的 `outputs/rag-retrieval-quality/step4b-phase2c0c-execution-20261104/`。未修改任何已有业务代码、测试、旧报告、原评分或冻结归档，没有暂存/提交。

## 2. 执行前核验及冻结身份

执行前实际运行原归档 `audit.py --verify`，退出码 0，返回 `sealed_archive_verified`，12 个文件、原排名、评分复算与一致性检查通过。24 题、48 个列表、222 条评分和原 U/F 条件保持不变。

前阶段 218 个保护文件及原预检报告摘要均相同；本轮将保护范围扩展为 234 个已有文件，收尾逐项摘要不变。原 SQL 草案、参数、观察和回执未覆盖；原预检材料仍记录当时未授权的事实。

| 身份 / 锚点 | 实际核对值 |
|---|---|
| 执行前 Git HEAD | `58ae98fb987a5b7c90b622624f31119aa0081449`，工作区干净 |
| 原 run_id | `a2f4907ec8cacc6bae4b52f8d89a9ee557ae09f1f4e7a7f3c6f3803b34848f33` |
| manifest 规范 SHA-256 | `a17e77d4f7c5d83c5b1c80bad318b374432f5750a9151c826f451feb52b5c3f8` |
| scope_sha256 | `167e14701623dc709a96ce8761a84f7a89c8eaba9c34f6f71025e2dc89e7f766` |
| inventory_snapshot_sha256 | `92e3c0ec0ff391e38110affb086def5e7b7a6ccd6412dea6f56cbe1133006ecd` |
| 原归档清单文件 SHA-256 | `f6afd216f987314de08b089ffadd8e9e6dcc867d79a11f40dc4771b775bf7b47` |
| 前阶段来源观察文件 SHA-256 | `373ee19353dd3aa89c0aa55d0c7d20031af4e1e5e9a03e89eed1dc44bcf11295` |
| 前阶段采集回执文件 SHA-256 | `eee8bd39899fff0a0d374ad709f95ae71535ff0ecfe7d727decf9bc3fd73a973` |
| 原预检报告文件 SHA-256 | `1d41c296ddf2f8f98ea616e20afc3c0118e64ad4275900d7cc49c3d0f396810d` |
| 已审 SQL 草案文件 SHA-256 | `49511d99ca1b429793fbea7f0cd0f8b71b82ea7c63c75638ef94f6b2cdedcd8d` |
| 参数规范 JSON SHA-256 | `910ad7b686a5d3b2221721cc1ebea2f9537c971149377e5a9e675cd9997d71ae` |

逐条 SQL 字符串及参数摘要与已审清单相同。使用项目现有配置；不打印连接串、密码或端点。采集使用原生 psycopg 执行已审命名参数 SQL，因为业务连接桥接层会转义百分号占位符；未改变 SQL、项目连接配置或桥接逻辑。

## 3. 实际数据库事务与查询结果

| 事务项目 | 实际结果 |
|---|---|
| 采集开始 / 结束 | UTC `2026-11-04T06:46:15.635306+00:00` / `06:46:15.805311+00:00` |
| 数据库观察时间 | UTC `2026-11-04T06:46:15.772789+00:00`，台北时间 14:46:15 |
| 事务观察标识 | `1000351:1000351:`，服务端版本 18.6 |
| 隔离 / 只读 | 显式 `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY`，实际 SHOW 确认为 repeatable read / on |
| 超时 | statement_timeout 20 秒、lock_timeout 3 秒 |
| 结束 | ROLLBACK 成功，连接已关闭；未提交业务写入 |
| 数据查询 / 错误 | M1–M3 共 3 个，全部完成；查询或连接错误 0 |

事务设置、SHOW 和获取观察时间/事务标识的会话检查不计入 3 个业务 SELECT。事务观察标识恰与前阶段相同，不代表原检索运行的历史事务相同，也不作为不可变历史身份。

| 查询 | 实际返回 | 核验结果 |
|---|---|---|
| M1 原状态元数据 | 57 行 / 57 对原 review-annotation | ID、content_sha256、范围、published_at 和 profile 均符合原绑定；原 annotation_status 全为 success，has_text 全为 1 |
| M2 完整模型观点元数据 | 138 行，覆盖 57/57 条评论及原 57 个 annotation | 全量获取该精确 annotation 范围，无 Top-K、维度或情感截断；没有重复 model_opinion_id 或稳定版本键 |
| M3 人工修订链元数据 | 0 行 | 当前观察中这 57 个原 annotation 均无可见修订；不是历史“从未修订”的证明 |

M1 与前阶段独立观察比较，原标注状态、error_code、created_at、requested_model、prompt_version 及已验证内容/时间/profile 没有差异：变化评论 0/57。所有 error_code 为原来源中的 null，未猜测或填充状态。没有查询其他 annotation 来判断全库当前最新标注选择。

## 4. 完整模型观点集合及版本检查

完整模型观点集合恢复 **57/57**，共 **138** 条；原检索的 **93/93** 个观点均覆盖且版本字段一致，额外发现 **45** 条元数据。分母为指定原 annotation 的当前可见完整模型集合，不是全数据集或完整历史有效语料。

| 每评论模型观点数 | 评论数 | 观点数 |
|---|---|---|
| 1 | 14 | 14 |
| 2 | 24 | 48 |
| 3 | 9 | 27 |
| 4 | 6 | 24 |
| 5 | 1 | 5 |
| 6 | 2 | 12 |
| 8 | 1 | 8 |
| 合计 | 57 | 138 |

观点数量来自 M2 无截断的完整结果，不额外执行 COUNT 或库存查询。逐评论 opinion_count 与其集合长度一致；冻结摘要对照提供另一个一致性约束，不把检索命中数当作完整计数。

138/138 条观点的源 ID/annotation、aspect/sentiment、字符区间和源 quote SHA-256 均通过校验。quote 哈希独立由数据库计算，核验时只在内存使用前阶段已授权保存的对应原文切片，不再次读取正文，不落盘额外 quote 明文。稳定观点键按现有 `_document()` 字段顺序重算；原 93 个键均与冻结候选一致，额外 45 个仅进入快照证明附件，不追加到原候选或 SourceCollection。

另一个离线审计不调用 historical_snapshot / `_review_state()`，独立按规范 JSON 公式重算 138 个键、检查 138 个区间摘要及 57 个快照，结果一致。所有本轮新增来源观察和分析文件均不含新 quote 明文或原文副本。

## 5. 观察性快照与五类正式结果

基于 M1 实际 has_text=1 / annotation_status=success、M3 实际无可见修订及 M2 完整集合，按固定原 annotation 构造唯一观察性 state：processing_status=success、human_status=unreviewed、human_revision_id=null、opinion_source=model，其他字段直接来自来源。没有枚举状态、选择最新修订或调整字段试配摘要。

使用现有 `verify_historical_snapshot()`，输入 basis=current_observation、data_kind=frozen_query；外部历史输入摘要和历史回执摘要均未提供，没有把本轮自产哈希作为外部参数。原文只复用前阶段独立观察。函数对 **57/57** 完成校验及重算，观察性摘要 **57/57 相同、0/57 不同**，执行验证失败 0。

这些 state 表示“指定原 annotation 的完整模型集合及本次可见状态”。它们不是通过全库 current_reviews_sql 选择出的当前最新有效语料，也不证明原运行时的标注选择和历史修订状态。

| 分类 | 实际数量 / 57 | 解释 |
|---|---|---|
| rebuilt_match | 0 | 不发布正式历史重建一致；缺少独立历史依据和外部固定摘要 |
| rebuilt_mismatch | 0 | 没有正式历史不一致结论；不能把观察性比较当作历史质量验收 |
| history_unproven | 57 | 函数实际执行的分类；当前集合可重算且摘要相同，但历史状态未独立证明 |
| missing_source | 0 | 本次适用快照核验没有缺少必需行级/集合来源；不代表未授权历史材料存在或完整 |
| unverified | 0 | 57 条均已完成适用的观察性验证，没有沿用前阶段未执行状态 |

观察性相同率 57/57=100%，只能描述本次指定集合对照。具有独立历史证明的快照为 **0/57**；正式历史匹配率为 **N/A（已证明历史输入分母 0）**，不能写成历史验收 100%。93 个观点行级成功、57 个观察性成功和 57 个历史未证明是不同层级，不相加统计。

## 6. 人工修订与尚缺的历史证明

M3 实际 0 行、涉及有可见修订的评论 0/57；不存在可供本次检查的 previous_id 链，断链检查返回空，不等于曾经存在的历史链已获完整证明。未读取人工观点或备注。

独立历史证明仍缺以下内容：

1. 原 Step 4A 检索事务的明确时间/快照身份，或与原 run 绑定的不可变历史导出。原 review/inventory 未保存可回放的逐评论完整 state/键清单；后来的归档时间不能替代执行时点。
2. 原 57 个 annotation 的状态、error_code、完整成员集合在冻结时的有效性，以及中间没有未留存增删/修订的证明。当前返回内容与摘要一致是有力的观察性对照，但本阶段规则不将它升级为独立历史来源。
3. 当时原 annotation 被选中、人工修订的存在/删除情况及适用状态的可信记录。未读取其他标注、历史审计或备份，不能断言这些材料不存在；当前 M3 为空也不证明历史为空。
4. 独立保管方对来源/回执的封存及外部固定摘要。本轮仍是受控采集和本地审计，不代替人员或外部流程批准。

因此，不再建议为同一问题反复执行相同 M1–M3；这些查询的当前观察任务已经完成。剩余是历史证据和批准政策问题，不是再跑一次统计就能解决。

## 7. 后续授权与替代路线

**本次 M1–M3 范围内不再需要授权或继续查询，当前任务完成后停止。** 若坚持正式历史重现，先由保管方明确是否有与原运行绑定的历史导出/审计，再单独授权其具体位置、时间/版本和最小投影：仍限这 57 条评论/原标注的状态、完整观点 ID/区间/哈希及适用修订元数据；不自动授权正文、全库存或备份搜索。没有明确历史材料时，不继续扩大读取。

另一条可供人工决定的路线是：保留旧基线，另建独立版本的“当前观察、历史未证明”探索性离线输入。现有 93/57 行级来源和本次 138 条元数据已足以形成该路线的审核材料，需明确旧原文与本次元数据的采集时点、内容版本绑定、局限及使用批准，再由独立保管方封存当前观察。它不能宣称历史基线已复现，不沿用正式历史 trusted_batch 已通过的名义，也不能据此外推全量数据。

建议由人工选择“补充可定位的历史证据”或“接受另立当前观察探索方案”，而不是继续重复准备。若选择后者，还需另行批准来源证明要求的变更、真实 QueryContract、上下文政策、独立人工充分性参考和实验规则；不能由程序自动放宽 `history_unproven` 门槛。本轮没有实施替代实验，也没有设置或批准质量阈值。

## 8. 独立封存与 48 个可信批次

来源采集及回执已完成，仍未创建正式 SourceCollection、未独立封存、未获得可信外部预期摘要。已有 `prepare_trusted_batch()` 不用于反向认证本程序自建来源，真实调用次数为 0。

48 列表仍为：准备 0、失败 0、未尝试 48，包含 Q17-F/Q22-F 两个空列表。原候选次序、数量、Top-K、过滤及评分均未改变。私有 batch-statuses.json 为每个列表记录 history_unproven 和封存/外部摘要缺失，不能把空列表记成先行通过。

| 本轮材料 | 文件字节 SHA-256 |
|---|---|
| metadata-observation.json | `6f6806c5d458192bfc38f8101e6196e86bf94989f6e1828701b6c85bd1e080a5` |
| collection-receipt.json | `4881beff9da10f0ee5981718c862b2f4ed87c69c8e30181a38d6885b1e6b19bd` |
| collect_metadata.py | `f816805aac8968933ca856eed174b42345f4cc6847576bd6863b5d07a5213cc9` |
| snapshot-results.json | `f7a1f7af30bd6cde687c9ead6ea26bb8cf0deca4920960fa7aa3d068f30700b7` |
| verification-summary.final.json | `c88edf6440cf0814f805418706e0926f1d43d92ee52cb985b5acffc247f75f93` |
| independent-audit.json | `cbb3d508bcc325c558856885180af0e13a026b6a5c1b0607e709f15081ee8dbc` |

这些是留给独立保管方复核的材料摘要，不是已获外部批准的可信参数。封存对象还应包含授权、原归档/旧来源引用、SQL/参数绑定、逐观点版本元数据、代码与测试日志，并明确文件字节摘要和项目规范 JSON 摘要的区别。没有填写审批人或伪造 approved 标志。

## 9. 实际测试、命令与遗留错误

| 实际执行 | 结果 |
|---|---|
| 原归档审计与采集前哈希检查 | 通过；12 归档文件、原观察/回执及已审 SQL/参数一致 |
| 真实 M1–M3 | 57 / 138 / 0 行，退出码 0；只读事务与 rollback/close 已核实 |
| verify_metadata.py | 57 次适用快照验证；138 个键/区间及原 93 个身份通过；未调用数据库、判断器或批次准备 |
| audit_observation.py | 独立公式复算 138 个键、138 个区间摘要、57 个快照通过，退出码 0 |
| 历史快照合成专项 | 21/21 通过，无失败/错误/跳过 |
| 适用组合回归，含上述专项 | 177 项：175 通过、0 断言失败、2 错误、0 跳过，退出码 1 |
| 文件保护与隔离 | 234 个既有文件摘要不变；旧预检报告保留；私有输出忽略、无新增跟踪项；新报告隐私/链接/空白检查通过 |

本次首版离线汇总将 defaultdict 检查过的 57 个键误统计为“有可见人工修订的评论数”，但原 SQL 回执和 M3 0 行从未变化。独立审计已按真实非空行的 review_id 集合纠正为 **0**，最终采用 verification-summary.final.json；初版 summary 和分析脚本保留，未为修正统计重连数据库，也未改快照结果、来源或历史评分。

两项原有队列错误本轮实际重现并单列 existing-queue-errors.log：

- `test_review_scope.ReviewScopeTests.test_run_date_filters_incremental_skip_and_scoped_progress`
- `test_review_scope.ReviewScopeTests.test_run_retry_and_cutoff_forwarding_with_real_mocked_sdk`

原因仍为临时 SQLite 路径进入仅支持 PostgreSQL 的持久标注队列；没有改动业务逻辑、跳过或计为通过。回归进程禁止网络和 psycopg 连接，SDK 路径为合成模拟，测试不新增真实读取或模型推理。21 项专项已经包含于 177 项，不相加称不同测试数。

主要实际命令：

```powershell
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/private-archive/step4a-20261104-human6/audit.py --verify
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/step4b-phase2c0c-execution-20261104/collect_metadata.py
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/step4b-phase2c0c-execution-20261104/verify_metadata.py
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/step4b-phase2c0c-execution-20261104/audit_observation.py
git diff --check
git status --short
```

采集命令在本次获准的执行权限下运行一次；失败或已存在产物均不覆盖重跑。回归通过 importlib 加载原私有 run_checks.py，将 OUT 指向本轮目录，分别执行 test_historical_snapshot，以及它与 e1_readiness/evidence_judge/query_contract/rag_corpus/retrieval_evaluation/review_scope 的组合，实际日志和计数保存在本轮目录。

未运行全仓或前端检查、额外 PostgreSQL 查询、新检索、向量同步、真实 E1 或外部模型。合成测试通过不是历史证明或语义质量验收；真实来源观察结果由独立 SQL 和实际摘要对照支持。

## 10. 最终结论与停止点

**限定元数据核验已完成：57/57 个原标注的完整模型集合恢复，138 条元数据含额外 45 条，M3 当前修订 0 行；57/57 观察性摘要匹配，正式历史状态均 history_unproven。**

历史独立证明和外部封存仍未完成，不能宣称 48 个可信批次或真实 E1 就绪。后续由人工决定补充可定位历史证据还是批准独立的当前观察探索路线；不重复相同准备，不自动扩大访问。本轮到此停止。
