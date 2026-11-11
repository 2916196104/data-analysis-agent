# P6-E1B 独立 PostgreSQL 事务一致性与并发验收

日期：2026-11-10，Asia/Taipei。依据 [P6-E0](P6-E0-real-environment-readiness.md)、[P6-E1A](P6-E1A-provider-contract-fix-acceptance.md)、[P6-B](P6-B-planner-and-snapshot-acceptance.md)、[P6-C](P6-C-budget-and-execution-acceptance.md)、[P6-D](P6-D-publication-and-ui-acceptance.md) 和本次实际源码。

**P6-E1B：BLOCKED。缺少明确授权的独立测试数据库、分角色连接配置及授权文件，PG-01～PG-08 均未执行，不能标记PASS。** 本轮已完成环境配置预检、安全入口、八项真实PostgreSQL测试代码准备及离线回归；没有以SQLite或PostgreSQL Mock替代真实并发证据。

测试数据库连接、生产数据库操作、真实收费模型和真实Embedding调用均为0。没有创建、迁移、删除或重置数据库/schema，没有启动或操作现有业务服务。本轮只新增测试准备和报告，未修改既有业务源码、费用授权、Planner、分类语义、冻结RAG或人工评审结论。

## 1. 当前环境与准入证据

只读取配置键的存在性；不调用健康API、psql、pg_isready、数据库摘要、迁移或初始化入口，不输出DSN、主机、端口、数据库用户名、密码或其他连接信息。

| 检查 | 本次观察 | 结论 |
| --- | --- | --- |
| 既有业务库配置 | 存在DATABASE_URL | 不用于本轮测试，不连接 |
| 通用独立测试入口 | TEST_POSTGRES_ADMIN_URL未配置 | 既有可选集成套件不能代表本轮已有可用环境 |
| E1B显式授权文件 | P6_E1B_AUTHORIZATION_FILE未配置 | 没有本轮目标、角色和合成写入范围授权 |
| 分角色测试DSN | creator、task_creator、reader、writer、task配置缺失 | 无法证明角色隔离或数据库实际身份 |
| 独立库实际版本/身份/权限 | 未查询 | 未验证，不编造版本、backend pid、快照或权限证据 |
| 仓库缓存中的历史测试集群 | 存在旧测试目录 | 不是本轮授权或当前运行状态证明，未连接、启动或复用 |
| 新入口预检 | 返回BLOCKED / isolated_environment_or_authorization_missing，退出2 | 在任何数据库连接前阻断；实际PG用例数0 |

新入口**不读取 `.env` 的默认数据库作为后备**。本轮的配置存在性审计可以读取项目配置，但真实集成入口只读取明确的E1B进程变量，避免通用启动配置把测试导向现有业务库。

证据：`.cache/p6e1b/environment-preflight.json`、`preflight-run.txt`、`postgresql-result.json`。其中没有测试连接信息或凭据。

## 2. 新增文件与执行边界

| 文件 | 内容与当前验证级别 |
| --- | --- |
| `tests/p6e1b_environment.py` | 独立授权/目标安全门：仅指定变量、唯一运行范围、身份及角色摘要、有效期、明确合成写权限；无SQL与网络调用；已离线验证 |
| `tests/p6e1b_postgresql_integration.py` | 八项真实PostgreSQL场景、实时身份/权限前置检查、实际连接跟踪、Event屏障、合法合成写入与修订、固定供应商、后台Worker/API读取；语法/入口已检查，**数据库部分未实跑** |
| `tests/test_p6e1b_safety.py` | 8项纯安全门测试；禁止socket/psycopg调用，验证缺环境、错误目标、过期授权、角色/摘要失配及无授权的--run入口；不模拟PG事务结果 |
| 本报告 | 真实环境阻塞、逐项未执行结果、后续执行要求与风险 |

没有修改连接工厂、事务实现、P6准备/执行/发布、E1A供应商安全修复或旧测试。没有新增依赖、迁移器、数据库自动创建器或自动DROP实现。

真实集成文件不以test_命名，避免常规离线发现时意外连接数据库；通过显式入口执行。没有使用skip/expectedFailure把缺环境包装成集成成功。即使本地授权结构完整，只做预检也最多返回READY_FOR_LIVE_IDENTITY_CHECK且退出2，不能代表实时身份检查或任何PG用例通过。

## 3. 独立库、schema、角色与授权契约

后续须由负责人明确提供并授权**本轮唯一业务测试库与独立任务测试库**，以及四类不同角色：创建者、业务只读角色、并发写入角色、任务写入角色。创建者在两个专用库各需一个明确DSN；因此入口需要五个变量：

- P6_E1B_CREATOR_DSN、P6_E1B_TASK_CREATOR_DSN。
- P6_E1B_READER_DSN、P6_E1B_WRITER_DSN、P6_E1B_TASK_DSN。
- 另需P6_E1B_AUTHORIZATION_FILE，内容不包含密码或DSN。

以上只是变量名称，当前没有填入真实连接或凭据。新安全门要求32位十六进制run_id，专用库名与该run_id及business/tasks角色用途精确对应，两个库不同；不接受既有业务库名、其他运行标记或把同一库当作两个用途。各连接的无凭据数据库身份摘要和登录角色摘要均须与授权文件固定值相同，四类角色必须不同；连接目标覆盖参数和任意schema切换参数会被拒绝。

授权文件版本为p6-e1b-authorization-v1，包含run_id、purpose=synthetic-p6-postgresql-integration、authorized=true、synthetic_writes_authorized=true、明确批准人、带时区有效期、identity_sha256/role_sha256及allow_database_or_schema_drop=false。不能由测试程序自造批准人或把自身算出的摘要视为用户授权；必须由负责人提供/确认。摘要是绑定信息，不是身份签名。实时检查还拒绝这四类角色互相拥有成员关系，不能只凭用户名不同就忽略SET ROLE可能扩大权限。

授权环境在运行前须预建项目业务/标注/人工修订所需表和独立任务表，并在**两个库**中设置只读运行标记表 `public.p6_e1b_test_scope`：run_id、purpose、authorization_sha256必须唯一且匹配本轮批准文件。runner不初始化数据库、不建角色、不授予权限、不迁移真实数据；不自动创建或修改此标记。

| 角色 | 实时前置检查与最小边界 | 当前证据 |
| --- | --- | --- |
| 创建者 | 仅本轮两个专用库的schema/对象所有者；不能superuser、CREATEDB、CREATEROLE、replication或bypassRLS；本runner只将其用于身份/权限和资源监测 | 无实际配置，未验证 |
| 业务reader | SELECT业务/来源/观点/修订表；不能INSERT/UPDATE/DELETE/TRUNCATE、不能CREATE schema对象、不能改运行标记或使用序列分配写入ID；不能CONNECT任务库 | 未验证 |
| 并发writer | 在本轮唯一业务库对合成夹具执行必要SELECT/INSERT/UPDATE及序列USAGE；不能CREATE对象、改标记或CONNECT任务库 | 未验证 |
| 任务writer | 只在独立任务库读写任务/进度/心跳及相关序列；不能CREATE对象、改标记或CONNECT业务库 | 未验证 |

schema使用项目工厂固定的public，但它位于本轮唯一的专用数据库中，与生产public不属于同一数据库。不能只在现有业务库换个schema来声称完成隔离。运行时还检查其他数据集/未结束任务，发现外来范围即停止；不得为了满足入口自行清空。

启动前要求角色级statement_timeout、lock_timeout和idle_in_transaction_session_timeout均为有限正值且不超过5秒，供测试和失败清理有界；建议为正常准备留出足够idle时间，故障用例再局部缩短。设置和核实这些值属于获准独立环境准备，不在本轮自动修改数据库角色。

所有合成数据的dataset_version带本次run_id和用例标记；同一专用库内不覆盖其他数据集。默认**只清理连接和临时本地文件，保留合成数据库记录作证据**，没有自动删库/删schema/删表/删除他人数据的路径。后续如需清理数据，须另行确认精确目标与授权，不把“测试结束”解释为删除许可。

## 4. 真实连接与只读事务审计

当前连接工厂的实际代码路径未改变：

1. `connect_review_database()`调用统一`connect_database()`并校验业务schema。
2. PostgreSQL连接以autocommit=true、connect_timeout=10、search_path=public、timezone=UTC建立；readonly连接设置default_transaction_read_only=on。
3. `execute('BEGIN')`实际转换为`BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY`。
4. Agent在事务开始后设置statement_timeout为服务器原值与30000ms中的较小值；商品目录、当前六维统计及多维观点共用此连接。
5. `prepare_p6_analysis()`实际检查SHOW transaction_isolation与SHOW transaction_read_only；analytics接收已有连接时复用事务，不开另一个快照。
6. 准备结束经上下文rollback/commit及closing关闭连接，之后才进入冻结输入分类。

以上是源码证据，**不是本轮真实PostgreSQL执行证据**。连接建立/schema校验早于Agent局部timeout；单条SQL超时不等于所有预读SQL、排队、网络等待、分类或整个Worker任务的硬时间上限。Worker.stop当前使用join，也不能据此声称正在执行的SQL或远端模型已可靠取消。

测试代码准备记录真正的pg_backend_pid、txid_current_snapshot、transaction_isolation、transaction_read_only和UTC，并将当前R事务中的统计及各维预读快照逐项比较。不会把应用层snapshot_audit.logical_transaction_id的UUID冒充PG快照身份。当前这些真实字段均无值，不能展示虚构快照。

## 5. PG-01～PG-08：实际结果与准备的断言

| 项目 | 当前实际结果 | 已准备的真实测试方法与断言 |
| --- | --- | --- |
| PG-01 事务协议 | **NOT EXECUTED / BLOCKED** | 真实reader连接BEGIN，核实RR/READ ONLY/UTC；对本次合成行的UPDATE被拒绝；rollback后不在事务、关闭连接并查服务端会话释放 |
| PG-02 统计后并发新增 | **NOT EXECUTED / BLOCKED** | R读取排名并记录快照后到Event屏障；W用另一真实连接在一个事务中写入有文件SHA/行号的新合成来源、评论和合法标注，commit后才释放R；原快照D=6、目标M=5/4/4，新的事务可见D=7 |
| PG-03 维度间合法修订 | **NOT EXECUTED / BLOCKED** | R完成service预读后屏障暂停；W用既有人工修订入口提交完整观点集合，移除其中一个effectiveness标注；R剩余维度仍读原负面4条，事务释放后新查询读3条；三维pid/快照完全一致 |
| PG-04 计数变化过期 | **NOT EXECUTED / BLOCKED** | 合成新增使D或任一M改变；真实Worker返回stale_ranking；模型0次、目标预读0次、当前统计逻辑查询1次、required_aspects不重选；显式刷新可信排名后才可重新准备/分析 |
| PG-05 同计数内容修订 | **NOT EXECUTED / BLOCKED** | 合法人工引用由“客服回复缓慢”改为原文中的“回复缓慢”，保留完整观点集合；D/六维相同，旧排名预计仍能prepare；新/旧观点及封存摘要不同，明确计数新鲜度边界 |
| PG-06 事务外冻结输入 | **NOT EXECUTED / BLOCKED** | R关闭后W继续修改；旧prepared仍校验且内容不变；封锁重新连接和analytics读取入口后，固定响应分类只能消费get_input；损坏输入拒绝，不允许缓存未命中回库 |
| PG-07 超时/异常/中止 | **NOT EXECUTED / BLOCKED** | 真实statement_timeout（预期SQLSTATE57014）、lock_timeout（55P03）、idle_in_transaction会话终止、SQL错误、rollback、连接断开；准备SQL超时后分类0次；中途Worker停止后分类必须0次；检测客户端及服务端遗留连接 |
| PG-08 Worker/任务库/API | **NOT EXECUTED / BLOCKED** | 真实后台TaskWorker、专用任务表、可信排名、P6预读、固定供应商6次、逐维/父验收、任务完整性封存和公开DTO；调用真实公开API路由处理器，从真实PG任务库重读并与保存DTO比较 |

本轮没有SQL执行日志、并发提交时间、真实权限结果或backend pid可供验收。测试文件内的SQL是**待执行模板**，不能放入“已执行证据”冒充实际结果。PG-08准备验证的是真实公开路由处理器和PG任务读取；HTTP传输和前端浏览器联调也未在本轮执行。

## 6. 并发时序及SQL证据采集设计

PG-02/03使用两个`threading.Event`表示R到达屏障和W已提交。W仅在R发出ready后开始变更；R必须等待committed信号，并检查writer线程已结束且没有异常，然后继续原事务。没有依赖sleep猜测先后，也没有让Mock返回手写统计。

时序固定为：

```text
R: BEGIN RR READ ONLY → 当前统计 / 第一维预读 → 记录真实pid与snapshot → ready
W: 等待ready → 合成数据写入 / 合法人工修订 → COMMIT → committed
R: 等待committed → 原连接读取后续维度 → 再记录pid与snapshot → 关闭事务和连接
N: 新事务读取当前数据 → 证实W的变更已经可见
```

W新增采用现有normalize_review、validate_opinions和append_annotation_on_connection；来源文件仅为本次生成的合成JSONL，保留其实际SHA和行号。标注追加与评论插入由同一个W事务提交，不通过LLM生成标注。人工修订提交完整观点列表，不能将某维筛选后的子列表误作整条评论全部观点。

待采集的最小SQL证据包括：

- BEGIN的实际隔离/readonly设置、SHOW timezone。
- `SELECT pg_backend_pid(), txid_current_snapshot()::text, current_setting(...)`的R结果。
- 真实analytics读取及W提交事件的顺序、当前D/全部六维M、每维total及观点版本。
- 新事务可见计数/修订与旧冻结输入的对比。
- `pg_stat_activity`中仅限本轮application_name及当前专用库的连接/事务计数。
- timeout的真实SQLSTATE、rollback后连接状态及最终释放结果。

证据输出只允许run_id、授权/身份摘要、逻辑角色名、版本、权限布尔值、SQL模板、快照/pid及本次合成计数；不写入真实连接信息、用户名、密码或DSN。安全结果器不把原始异常message或完整traceback写入公开结果JSON。

## 7. 新鲜度与冻结输入的实际能力边界

`check_current_ranking()`当前核对完整scope、successful_reviews分母D和全部六维负面去重计数M。任一计数变化都应阻断，不能重新选前三名。它不比较所有评论正文、标注内容或人工修订版本；PG-05准备的预期是**允许同计数修订后的新准备**，而不是人为要求不存在的内容版本能力。

这一边界在E0仅用临时SQLite作过独立观察；本轮还没有PostgreSQL实证，因此不能把旧观察冒充PG-05通过。若业务要求“正文/修订变化即历史排名过期”，后续需要另行版本化的数据/修订指纹及目标绑定；不得补写旧排名摘要或修改冻结来源，使其看似早已具有此能力。

P6PreparedAnalysis的内容/摘要检查与排名新鲜度是两项不同保证：新请求允许使用计数相同的当前合法观点，不代表已封存对象能被数据库后续变化修改。PG-06将通过真正关闭连接后继续写入来验证后者，并在分类阶段封锁任何新连接，而非只检查最终SQL计数。

## 8. 异常、中止及资源释放风险

PG-07使用本轮专用角色和事务局部GUC：statement_timeout与lock_timeout各缩短至50ms，idle_in_transaction_session_timeout缩短至100ms。SQL中的pg_sleep只用于触发服务端语句超时，**不用于PG-02/03的并发排序**；idle超时通过真实pg_stat_activity条件轮询观测，不以客户端等待结束推断会话已经终止。

资源检查分两层：先在测试清理前断言应用创建的连接已经关闭，不能用测试最终close掩盖应用泄漏；再对业务/任务两个专用库按本轮application_name检查服务端遗留会话。发生断言失败仍执行兜底close，防止测试自身留连接，但不把兜底清理算作应用正常释放通过。

**待实测风险：Worker中途停止可能没有及时传递到P6协调器。** 当前Worker在进度回调检查stop_requested，prepare_p6_analysis和execute_p6_prepared没有明显消费该停止标志；若停止发生在当前统计/多维预读中，可能直到分类后上报完成进度才被Worker识别。本轮没有通过真实PG执行来确认发生次数，不将它改判为已验证缺陷或通过。

因此PG-07准备了严格故障注入：当前统计返回时置stop_requested，最终任务须受控失败且固定分类响应调用数必须为0。不能仅断言任务最终failed就声称中止安全，也不能把停止时点移到Worker最初入口来回避这个边界。未来若该断言失败，应先记录FAIL并最小修复取消传递，再完整复验；本轮不在缺少实际环境时修改P6-C/D业务契约或放宽断言。

连接工厂的单语句timeout、RR事务和上下文close也不证明整个任务具有硬deadline。网络断开、rollback自身失败、线程清理、会话终止和角色实际权限都需真实验证；测试代码尚未实跑，夹具自身的运行正确性也未获PostgreSQL实证。

## 9. 实际执行与回归结果

| 本轮检查 | 结果 | 是否属于PG集成通过 |
| --- | --- | --- |
| 本地环境存在性预检 | BLOCKED，未配置专用环境及授权 | 否 |
| 无授权真实入口安全门 | 返回BLOCKED并退出2；cases_executed=0 | 否 |
| 新安全门单元测试 | 8/8 PASS，封锁socket/psycopg且断言未调用 | 否，只证明入口保护 |
| PG-01～08代码 | 8项定义及语法检查完成，未连接运行 | 否 |
| 原P6/E1A适用离线功能回归 + 安全门 | **603/603 PASS**（原595+新8），203.705秒，0 failure/error/skip | 否，不替代PG结果 |
| 真实收费模型 / Embedding | 0 / 0 | 只证明本轮未越界 |
| 生产数据库操作 / PostgreSQL测试连接 | 0 / 0 | 未连接不等于事务验收通过 |
| 历史13个私有导入问题 | 原样保留，未清理/放宽 | 不计为本轮新PASS |

本轮安全门验证包括：不回退DATABASE_URL或通用adminURL；所有角色/授权变量必需；本地摘要完整不等于真实身份已经验证；既有业务库及其他run目标即使重算摘要也拒绝；过期/未批准/允许DROP的授权拒绝；角色/摘要失配拒绝；schema/host/dbname/service覆盖拒绝；无授权的--run在连接前退出并不泄漏凭据。

在项目根目录执行的离线命令：

```powershell
$env:PYTHONPATH='.;src;tests'
.\.venv\Scripts\python.exe -m unittest test_p6e1b_safety -v
.\.venv\Scripts\python.exe tests/p6e1b_postgresql_integration.py
.\.venv\Scripts\python.exe .cache/p6e1b/run_checks.py functional
```

第二条当前应退出2并产生BLOCKED。**不应为得到绿色退出而设置伪造授权或使用业务库。** 后续只有负责人提供独立环境并完成实时身份/权限/标记准备，才能在同一显式入口增加--run；本轮没有运行带有效数据库授权的命令。

证据目录 `.cache/p6e1b/` 包含environment-preflight、preflight-run、postgresql-result、safety-tests、functional-summary/functional-tests以及入场/最终文件保护清单。入场2253个受保护文件的摘要全部未变，旧测试和业务源码无本轮修改；测试连接凭据不写入任何验收报告。原有E1A供应商身份、缓存/推理费用、quota停止与默认无授权机制由完整离线回归继续保护。

## 10. 后续解除阻塞的条件与本轮结论

需要补齐的条件：负责人明确批准本轮唯一数据库和合成写入范围；提供两个隔离库、四类角色和五个显式角色连接；预建schema和只读运行标记；核实权限、序列及超时；实际执行PG-01～08并保存同快照、并发提交、过期阻断、中止和释放证据；必要修复后原离线回归继续通过。只有全部真实项目通过才能将P6-E1B标为PASS。

当前真实token上界、实时价格、真实费用授权、账户账单、可靠供应商取消和主题语义质量仍属于后续独立阻塞项。E1A工程修复保持有效，但没有授予真实收费许可；本轮固定响应设计也不改变这一点。

**最终结论：BLOCKED，按用户指定的环境缺失分支完成测试设计、代码准备和离线保护验证后停止。** 未自动连接业务库、创建/删除测试环境、运行收费模型或实施生产部署。
