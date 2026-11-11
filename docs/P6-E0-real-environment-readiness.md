# P6-E0 真实环境验收准入预检

日期：2026-11-09，Asia/Taipei。依据当前源码及 [P6-A](P6-A-contract-and-selection-acceptance.md)、[P6-B](P6-B-planner-and-snapshot-acceptance.md)、[P6-C](P6-C-budget-and-execution-acceptance.md)、[P6-D](P6-D-publication-and-ui-acceptance.md)、[实施准备](P6-implementation-readiness.md) 报告。

**进入小规模真实环境验证：BLOCKED。** 本阶段的审计、独立离线测试和验收准备已完成；发现的真实调用契约缺口未修复，独立 PostgreSQL 并发验证和真实主题质量均未通过。不能用 P6-A～D 自动化通过率代替这两项结论。

本轮未调用真实收费模型或 Embedding，未连接、读取或写入生产数据库，未启动生产服务或部署。只读取本地源码、脱敏配置元数据、既有冻结文件及官方公开文档；数据库故障注入全部使用临时合成 SQLite，SDK 验证全部使用进程内 `httpx.MockTransport`。当前 P6 三个开关保持关闭，未写入任何真实费用授权。

## 1. 当前架构与真实执行链

| 环节 | 当前实现与边界 | 真实环境尚需确认 |
| --- | --- | --- |
| 提交 → Worker | `jobs/submission.py` 绑定执行模型身份、会话和 context_task_id；`TaskWorker._execute()` 从任务库加载受控历史 | 专用验收任务库、旧 Worker 重载、唯一 Worker 锁和心跳；不能读取生产任务作为本次运行输入 |
| Worker → Agent → P6RequestGoal | `reviews.py:159` 起进入只读事务；显式单维/数量/原文/正向等优先；`freeze_p6_goal()` 复核服务端原始排名，先冻结用户目标 | 商品、UTC 窗口、source/dataset、标注模型/base_url/prompt_version、执行身份、同会话及封存原始排名都须真实匹配 |
| Planner → 冻结输入 | `build_p6_query_plan()` 编译原目标；`validate_p6_plan()` 独立重建；`prepare_p6_analysis()` 在同一个连接与事务中读取当前六维统计和 k 个维度观点 | PostgreSQL 实际隔离、SQL 性能、来源表完整性；k≤3，1+k 是逻辑准备查询数，不是 SQL 语句总数 |
| 冻结输入 → 分类 | 退出事务并关闭连接后，`execute_p6_prepared()` 严格按 required_aspects 执行；每维 `get_input(aspect)` 重验摘要；旧工具分发硬屏障未删除 | 内存与任务退出风险、真实 provider timeout；不支持把历史诊断 JSON 当作冻结对象跨进程恢复 |
| P6SharedBudget → 主题模型 | 每次发现/复核前预留 token 上界费用；每维≤2、整轮≤6 次，失败计入；原 `review_topics` wire/解析/分类器复用 | tokenizer、消息协议开销、供应商价格/类别、usage、可靠中止和账单核对 |
| 逐维 → 父验收 | 复用 `validate_p6_completion()`、P0-B 子验收及 `validate_topic_result()`；重算计数、范围、情感、位置、来源；必做集合不缩减 | 自动结构/来源验收不能证明模型主题语义正确；须独立人工评审 |
| P6Publication → API | `publish_p6_execution()` 发布前复验完整父目标和全部子维度，仅投影 publishable_aspects；`/public/tasks/{id}` 复核保存时任务封存和 DTO 摘要 | 首次发布使用运行时对象；历史读取依赖服务端任务库完整性，SHA-256 不是签名或数据库特权写入防护 |
| API → 前端 | `task-api.ts` 必取 P6 公开复验对象；`ChatMessage`、`P6Results` 做运行时检查；复制/导出共用确定性 answer_text | 真实验收实例的 HTTP/CORS/轮询与身份绑定；本轮未重构页面，不重复声称进行了新浏览器联调 |

调用顺序固定为：可信历史任务 → 最终范围 → 不可变 P6RequestGoal → 确定性计划 → 独立计划检查 → 同事务当前统计/观点预读 → 关闭数据库连接 → 共享预算串行分类 → 逐维与父级验收 → 独立发布复验 → API/前端。不得把排名过期后重新选前三名作为恢复方式。

第一轮六维排名、P6 第二轮意图、Planner 和 P6 回答均可走确定性路径，额外意图/规划/回答模型为 0。**这个保证只适用于 P6 确定性路径**：旧单维主题默认分类、可选语义解析和回答生成仍有各自模型调用，不能用 P6 开关或 P6 费用对象代表整个 Agent 已获得收费授权。

## 2. 当前配置、环境和数据依赖

配置来源是 `read_settings('.env')`，进程环境变量优先。本轮仅输出允许展示的字段；未输出 API key、数据库用户、密码或完整 DSN。配置存在不代表服务在线、账户可用或数据仍与历史报告相同。

| 项目 | 本次实际观察 | 准入状态 |
| --- | --- | --- |
| 执行供应商 / 模型 | `https://api.siliconflow.cn/v1`；`Pro/deepseek-ai/DeepSeek-V3.2`；密钥字段存在，未验证有效性 | 未发请求，真实可用性未验证 |
| P6 开关 | preparation=false、execution=false、publication=false | 默认安全关闭，保持原值 |
| 费用授权工厂 | `make_execution_budget()` 仅返回无授权 `P6SharedBudget()` | fail-closed PASS；没有可用于真实运行的 profile |
| 主题 SDK | openai 1.109.1、httpx 0.28.1；psycopg 3.3.6 | 本地安装版本已核对；未自动升级 |
| API / 模型结构库 | FastAPI 0.156.6、Pydantic 2.13.5 | 后端离线回归验证 |
| 配置超时 / 重试 | LLM timeout=30 秒，旧通用 max_retries=1；P6 单次仍取 min(配置,20,剩余时间)，SDK max_retries=0 | 不应把旧配置重试次数误认为 P6 会重试 |
| tokenizer | venv 有 tiktoken，无 transformers/tokenizers；没有配置 P6 tokenizer、chat template 或上界 profile | BLOCKED；包已安装不证明可用于此模型 |
| 业务库 | `.env` 的 DATABASE_URL 指向 loopback PostgreSQL 的 `reviews`；本轮视为现有业务/生产目标，未连接 | 不能替代独立测试库 |
| 独立测试库 | TEST_POSTGRES_ADMIN_URL、TEST_P6_POSTGRES_URL、P6_TEST_DATABASE_URL 均未配置 | PostgreSQL 集成 BLOCKED；后两者只是本次检查的可能名称，当前代码未实现读取它们 |
| 任务库 | CLI `--task-db` 为实际入口；PostgreSQL 默认任务表与业务表同库，SQLite 默认旁置 tasks 文件 | 不能只增加一个未被代码读取的 TASK_DATABASE_URL 就声称完成隔离 |
| Worker | 串行线程，独立心跳，PostgreSQL advisory session lock；stop 使用 join 等待 | 未连接现有 Worker/健康接口；运行状态未验证，不自动启动或停止服务 |
| Redis | 当前 LABEL_EXECUTION_MODE=redis | 属于标注队列和健康依赖；P6 消费已有成功标注不需要新标注/Embedding，不应为 P6 自动启动写入 Worker |
| 真实来源 | 评论、导入文件 SHA/行号、对应成功标注及最新人工修订、绑定 scope、受封存的历史排名任务 | 需来自获准的独立快照/测试库；本轮没有在线核验当前业务数据 |

真实环境的最小服务集合是专用 PostgreSQL 测试库与角色、隔离任务库、一个使用已固定代码/配置的 Worker、本机 API 和前端、获准的供应商连接。P6 本身不依赖 RAG 向量库、BGE-M3、Embedding 服务或新导入/标注作业。若当前通用启动入口检查 Redis，它是运行环境前置项，不能被误计为 P6 算法依赖或据此自动读写生产。

## 3. PostgreSQL 实际事务边界与风险

`connect_review_database()` 调用唯一连接工厂并检查 schema。PostgreSQL 构造器以 autocommit=true、connect_timeout=10、search_path=public、timezone=UTC 连接，设置 default_transaction_read_only=on。构造/schema 检查发生在 Agent 设置局部 SQL timeout 之前。

`PostgresConnection.execute('BEGIN')` 实际发出 `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY`。Agent 紧接着将当前事务 statement_timeout 限为服务器原值与 30000ms 中更小者；随后商品目录、当前排名和 k 个维度预读都使用同一连接。`prepare_p6_analysis()` 还实际查询 `SHOW transaction_isolation` 与 `SHOW transaction_read_only`，不是只相信 readonly 参数。

`analytics._snapshot()` 收到现有连接时只校验并复用，不另开快照。PostgreSQL REPEATABLE READ 的可见性固定在首个获取快照的语句，而不等于 Python 记录的 read_started_at；本链中事务内 set_config 的 SELECT/目录查询可能早于排名读取。应将“同一快照”与“读取排名那一刻的最新数据”区分。[PostgreSQL SET TRANSACTION](https://www.postgresql.org/docs/current/sql-set-transaction.html)

P6 通过受控异常退出 with，正常准备分支也可能执行只读 rollback，再由 closing 关闭连接，之后才进入分类。冻结对象仅含不可变 JSON 和摘要；关闭连接后 `validate()/get_input()` 不做数据库回补。

| 风险 | 当前处理与剩余缺口 |
| --- | --- |
| 隔离实证 | 现有 PostgreSQL 协议测试使用 MagicMock；真实并发测试只有 SQLite WAL，不能视为 PostgreSQL PASS |
| 新鲜度粒度 | 当前只比较 scope、D 和完整六维 M；任一计数改变即 stale_ranking，零模型调用；不是所有原文/人工修订变化都能触发过期 |
| 同计数修改 | 本轮真实临时 SQLite 人工修订将“客服回复缓慢”改取“回复缓慢”，D/六维不变，新准备仍通过且采用新观点，旧冻结对象不变；这是已复现的版本边界，不是历史正文完全复现 |
| 连接与锁 | connect_timeout=10 不是所有 SQL/整个任务截止；schema 检查、Worker 锁连接及任务库操作没有 P6 统一 wall-clock 截止 |
| SQL 资源 | 单语句≤30 秒不等于总 SQL≤30 秒；没有 P6 专用总预读截止、lock_timeout、idle_in_transaction_session_timeout；还需数据库角色级较严格限制和实际测试 |
| 清理 | Python 上下文对正常/异常路径 rollback/close；本轮 mock 注入验证成功，但网络断开、服务端取消、rollback 再失败等真实资源回收未测 |
| 长事务与内存 | 分类已在连接释放后进行，降低长事务风险；候选有界，原文总长度/封存 JSON/公开正文仍需真实数据压力测量，不能把模型 body≤16000 当作整包内存上限 |
| 审计标识 | snapshot_audit.logical_transaction_id 是应用 UUID，不是 PostgreSQL transaction/snapshot 证明；真实测试须另外记录 backend pid、事务隔离和实际快照标识 |

如果“任何数据变化必须刷新排名”包含计数保持不变的正文或人工修订变化，现实现不满足这一更强契约；在真实准入前必须明确采用计数新鲜度，或另行版本化增加数据/修订指纹。不能改写旧可信排名摘要来伪造历史具备该能力。

### 独立 PostgreSQL 并发验证计划与当前结果

**当前结果：BLOCKED，真实 PostgreSQL 测试执行 0 次、连接 0 次。** 未运行带 skip 的可选 PostgreSQL 套件并将其计为通过，也没有因缺测试库改连 reviews。

后续环境需由负责人提供专用测试实例和显式允许写入的测试数据库/命名空间。创建器角色与运行角色分离；业务 reader 只读，任务 writer 仅写专用任务表，并发 writer 只可修改合成夹具。设置固定允许的数据库身份、非生产校验、唯一 `p6_e0_test_` 运行标记和清理清单；确认 PostgreSQL 版本、TLS、角色、超时、时区。现有 `tests/test_postgres.py` 会按 TEST_POSTGRES_ADMIN_URL 创建/删除唯一 agent_test 数据库，因此不得把其管理员地址指向生产集群来代替隔离环境。

| 测试 | 精确调度与断言 | 退出条件 |
| --- | --- | --- |
| PG-01 协议与只读 | 通过真实工厂 R 连接，BEGIN，检查 readonly=on、isolation=repeatable read、UTC；尝试修改专用夹具表必须被拒绝；rollback/close | 隔离或权限不符立即停止，不调用模型 |
| PG-02 统计→观点间新增 | 初始六维排名 D=6、M=5/4/4/2/1/0；R 在 get_top_issues 返回后通过 barrier 暂停；W 在另一连接原子插入带唯一来源/行号的合成评论及合法标注后 commit；继续 R 的三个观点预读 | R 的 D、六维、items、opinions 都必须为 W 提交前版本；W 必须已提交，不能依赖 sleep 猜并发 |
| PG-03 维度间修订 | R 第一维预读完成后暂停；W 通过真实人工修订入口改第二维情感/观点并 commit；继续第二、第三维；记录每次连接 backend pid 与事务快照 | 三维应使用同一旧快照，不能混入 W 的新修订；任何连接变化或 M/来源混合失败 |
| PG-04 新请求发现过期 | 关闭 R 后以同一旧排名发起新 P6 请求；W 的新增/情感改动使 D 或任一 M 改变 | stale_ranking；只允许当前统计，维度预读/模型为 0；原 required_aspects 不重选；刷新排名后才可形成新目标 |
| PG-05 同计数内容变化 | W 修改正文或人工引用而保持 D/六维；对照旧/新 prepared SHA 和 revision/source | 当前预计不触发 stale；单独报告这一能力缺口；若验收要求任意变化都 stale，必须先补版本化指纹，不能改预期为通过 |
| PG-06 释放后冻结 | 保存 R prepared 摘要，关闭全部 R 连接；W 再次修改数据；禁用重新连接和 analytics 入口，直接校验并以固定供应商消费 frozen input | 输入内容和摘要不变；无懒加载、无新查询；改本地副本不能改原对象 |
| PG-07 timeout/错误清理 | 专用连接注入 statement timeout、锁等待、writer rollback、断连；检查 R 不返回 ready，不启动模型；从另一监测连接检查 pg_stat_activity | 无遗留 idle in transaction，无读写权限扩大，关闭/回滚可追溯；缺监测权限也记未验证 |
| PG-08 真实 Worker 全链 | 专用业务/任务库走 API 提交、可信排名、P6；分类仍固定响应，读取公开 DTO，再用前端展示 | 父子状态及封存一致；schema/来源/范围问题硬失败；不得隐式接到生产默认库 |

PG-02/03 的 wrapper 仅用于测试调度，必须调用原始 analytics，不能返回手写统计。每轮记录 server_version、角色权限、数据库身份、backend pid、transaction snapshot、barrier 事件及 commit 时点；所有 SQL、表创建、并发写和清理只发生在获准测试数据库。测试完成先关闭连接，再按已记录身份清理，不使用不受限的数据库删除命令。

## 4. 真实模型、价格、usage 与授权审计

本轮读取官方公开资料；Exa 检索额度已用尽，按只读网页后备工具核对官方站点，未调用供应商 `/chat/completions`、账户或 token 计算 API。

| 项目 | 当前来源与结论 |
| --- | --- |
| 公开价格依据 | SiliconFlow 官方 2026-06-25 公告称该 Pro 模型自 2026-06-30 调整为输入 ¥4/百万 token、输出 ¥6/百万 token、缓存命中 ¥0.4/百万 token；公告同时要求以平台实时价格为准。它是可追溯公告，**不是当前账户实时结算价或费用授权**。[官方公告](https://docs.siliconflow.cn/docs/release-notes/overview) |
| 当前账户价格 | 未访问控制台/账单；折扣、缓存、税费、额外收费、失败/取消请求计费和模型别名结算未确认，保持未知，不能装载真实 profile |
| 输入上界 | `P6CostAuthorization.token_upper_bound(prompt,body)` 仅接收最终 system prompt 和 wire body；可靠 profile 必须额外计入消息封套、schema、模板和所有计费输入，不可用 body 字节或旧估算倍率替代真实证明 |
| 输出/推理 | 本模型现路径发送 enable_thinking=false；官方将 max_tokens 与思考预算区分，不能对未知模型假定 1200/3000 覆盖额外思考。必须验证禁用生效、reasoning_tokens=0 或明确计费合同。[推理说明](https://docs.siliconflow.cn/docs/userguide/capabilities/reasoning) |
| usage 结构 | 官方示例有 prompt_tokens、completion_tokens、total_tokens、prompt_tokens_details.cached_tokens、prompt_cache_hit/miss_tokens、completion_tokens_details.reasoning_tokens；当前账本只保留前两项的归一化计数，未保留全部原 usage/model/request id。[Chat API](https://docs.siliconflow.cn/docs/api/chat-completions-post) |
| 当前费用预留 | `upper*input_price + max_output_tokens*output_price`，累计不释放；失败、超时、usage 缺失仍保留；缺失不记零费用。费用是每个 P6 budget 实例上限，不是跨任务/重试/整个验收批次上限 |
| 实际计费类别 | 代码仅允许 input/output，且只检查人工约定 billable_extra_tokens/billable_categories；这些字段不是当前官方 usage 标准，不能靠字段缺失断言没有额外计费 |
| 身份和 trace | 当前把 grouping_model 填为 settings.model，未核对 response.model 是否属于授权模型/允许的已核实别名；未把 provider response id、trace id、fingerprint 纳入费用审计凭据 |
| 错误处理 | SDK max_retries=0；401/402/403 停止后续预算；503、普通429、JSON错误/截断使当前维失败，不自动补调。官方把402列为欠费、429列为限流；其他兼容端点的 quota 错误需按结构明确区分，不能把所有429都当欠费。[错误说明](https://docs.siliconflow.cn/docs/userguide/faqs/error-code) |

SDK float timeout 经 HTTPX 是连接/读块/写块/连接池等待的超时，不是可靠的请求 wall-clock 总截止。P6 在响应返回/解析后拒绝迟到结果，但同步请求未返回时，120 秒分类预算不能抢占它；远端任务是否取消、是否继续收费也未证明。不能通过线程 join 超时声称底层模型已经终止。[HTTPX timeout](https://www.python-httpx.org/advanced/timeouts/)

### 独立真实 SDK 离线探针

本轮直接调用安装的 OpenAI SDK，通过 MockTransport 捕获实际 JSON/extra_body、返回标准 HTTP/usage/模型字段；socket 和 psycopg 入口同时禁止。测试价格为 TEST 合成单价，不是官方人民币报价，不发送真实密钥。

**13 项：9 PASS、4 FAIL、0 errors/skips。失败断言原样保留，未改成 expectedFailure 或跳过。**

| 编号 | 探针结果 | 真实准入含义 |
| --- | --- | --- |
| E0-F01 | 原生缓存 usage 有80个缓存 token、20个未缓存输入时，未提供缓存单价，账本仍 actual_cost_complete=true | 按最高输入价估算可用于保守预留，但不能把估算标为精确账单；需要原 usage 与类别计价/未知状态 |
| E0-F02 | 传入未授权 response.model，仍接受响应 | 必须核对响应模型及已核实别名/版本的费用身份，拒绝未知重定向；不是机械要求所有合法别名字符串完全相等 |
| E0-F03 | 请求禁用思考，但返回非零 reasoning_tokens 时仍接受 | 必须明确输出总计是否含推理、额外类别是否存在及上界；本路径禁用后出现推理应停止并记未知账单 |
| E0-F04 | 模拟 HTTP429 且 error.code=insufficient_quota，没有设置 halted_reason | 这是兼容性故障注入，不证明 SiliconFlow 当前这样返回；真实 provider 错误契约须规范化，明确额度失败停止全部后续付费尝试 |

通过项验证：默认无授权时0次调用；SDK实际发送 enable_thinking=false、max_tokens=1200、max_retries=0并关闭客户端；503一次请求不重试且不释放费用；402停止后续调用；21秒迟到响应拒绝；PostgreSQL连接协议/异常rollback-close；schema失败关闭；同计数修订仍不触发stale的真实行为；以及“两次模拟模型共同语义误判”仍可能通过结构发布的能力边界。最后两项是对当前边界的实证，不是业务质量通过。

### 真实收费执行的最小授权清单

以下清单全部满足且形成可复核记录前，真实执行保持禁止；本报告不是授权记录。

1. 明确准许的供应商、精确模型/允许别名、base_url、调用时间窗、执行人/批准记录、账户/专用凭据引用及请求总数；密钥不得进入报告或前端。
2. 获准发送的独立数据快照、SKU、日期、标注身份、来源摘要、最大原文范围；授权外内容不能发送。人工答案、评分理由和测试集用途不能进入模型提示。
3. 当日可追溯价格证据、币种、税费/折扣规则；输入命中/未命中、输出/推理、额外类别、失败/超时/取消收费说明；不能把公开历史公告当账户最终账单。
4. 可靠且固定版本的模型 tokenizer/chat template 或供应商确认的保守 token 上界；覆盖最终提示、wire/schema和消息开销；上界函数与模型/配置绑定，有离线反例测试。不存在即 fail-closed。
5. 每请求、每P6轮、整个验收批次的金额和次数上限；重试/重跑算新费用，批次余额先预留；禁止自动充值、自动重试、从实际低usage释放预算追加调用。
6. 真实 `P6CostAuthorization`：authorized、allow_real、精确模型/端点、币种、Decimal上限/单价、完整类别、token_upper_bound_id与可信函数；由服务端装载并校验，不接受客户端参数。当前工厂尚未实现此装载。
7. provider响应身份、usage原字段、缓存/推理/额外收费、request/trace id、失败留账与账单对账规则；完成E0-F01～04的修复和独立反例复验。
8. 验证20秒请求截止与供应商取消/未取消情形的处理；不能可靠取消时停止该验收轮后续请求，隔离迟到响应并保守计费，不宣称硬取消成功。安全关闭不会自动消除远端费用。
9. 先通过专用PostgreSQL+固定供应商的无费全链，再分别开放明确授权的小批真实模型运行；准备、执行、发布开关分开控制，先做不对用户发布的人工评审隔离运行。

## 5. 真实主题质量评估集与评审方法

### 已有真实材料，及不能借用的结论

本轮只读核验了冻结文件的实际存在、摘要和原文位置，没有重跑检索/Embedding/数据库查询，也没有修改人工标签。

| 资产 | 本次核验 | 可用于 P6 的范围 |
| --- | --- | --- |
| Step4A冻结真实评论 | `private-archive/step4a-20261104-human6/review.json`，48个query-mode、222个排名条目，去重93观点/57评论，222处quote与保存全文位置均一致 | 仅现有保存文本的离线种子材料；不是当前业务库或全量随机样本 |
| 原人工相关性标签 | human_confirmed=true；24问题，14 answerable/10 no_known_relevant；222个0/1/2分，96/60/66 | 查询相关性参考，不能直接当主题名称/归属的金标准 |
| 负向种子 | 去重负向59观点：客服20、效果18、质量8、物流7、价格5、包装1；另有正向33、unknown1 | 可设计负向主题、正负混淆、跨维同评及证据不足用例；包装仅1例，不足以报告稳定质量 |
| B2已发布人工证据参考 | `e1a-b2-publication-20261105/release-v1/reference-release.json`；48份，sufficient19/insufficient29；一名评审者 | 可用于quote支持、上下文否决、对象/极性歧义测试，不等同独立双盲主题金标准 |
| 来源与历史可信度 | 两组同为 reviews_csv/reviews-csv、SKU100013588055、UTC2021-09-11至2022-08-26；B2 history_unproven、independent_seal=false | 原限制原样保留；不得外推到SKU100013588053、当前数据或完整主题召回率 |

这批57条评论/93观点是检索Top-K并集，不是P6“最近100条→20表达/200观点”样本的等价物；不得把检索排名变成可信六维排名，也不能由它构造真实 D/M。当前没有读取可用于新鲜可信排名的完整真实测试库。因此真实统计全链和主题质量是两套相关但独立的评估层。

三个关键文件字节SHA-256已与历史报告核对：review.json `c6ca491983c23b736c43d2ac93118ec9733af36a813ac68aaec9ec2a375eb2b2`；labels.human.json `c5a6946005e5a0b5b6f2bd63342e67d3df3f149d380c9b885440dd54dcaa7225`；B2 release `3119110db89e0c9afc88f90865e73b907f6529b4045be359001ce682f2586d26`。详细限制见[最终质量审查](rag-retrieval-quality-final-review.md)和[B2发布报告](rag-step4b-e1a-b2-reference-release.md)。

### 独立评估集设计

采用新的 P6 专用评估版本，原资产只读、原结论不变。每条新记录至少绑定：来源资产字节摘要、review/opinion键、商品/日期/标注身份、全文摘要、quote及Unicode位置、aspect/sentiment、开发/留出分组、人工主题名与定义、可接受同义名、必须覆盖问题及重要程度、归属关系、支持/反例片段、样本限制、评审记录。这里只定义结构和评审流程，不伪造新的人工确认、评审人或已完成打分。

分三层：

1. **工程及对账层**：在独立库用来源完整的真实数据副本，先固定供应商响应，核对 D/M/S/N、原文、同评论跨维度、快照、状态、费用台账、复制导出。此层100%通过只能证明工程链和对账。
2. **已曝光真实种子层**：从现有59条负向观点及正向/unknown对照建立新主题标注。以完整review_id分组，跨维观点和同评原文不得拆到训练/留出两侧；保留原人工结论仅作旁证。该层材料已被既往开发接触，必须标为探索/校准，不能宣称独立未见集。
3. **未见业务留出层**：由负责人从获准的独立数据副本固定新范围，覆盖目标SKU053和其他SKU、长短评论、否定/撤回、安装vs在线客服、同评多维、并列排名、稀疏维度、20表达上限、S<M等。先锁定清单/摘要和人工基准，再取得有次数/金额限制的模型授权；不能为填足样本去生产库补读。当前未提供，BLOCKED。

两名具业务理解的评审者分别在**不知道模型输出**时标注样本的主题/重要问题与支持片段，允许真实同义主题及多标签归属；第三方裁决分歧，原判断及理由保留。模型运行与人工答案存储隔离，禁止把人工标签、期望主题、支持理由或示范答案混入发现/归属提示。报告评审人数、分歧率及裁决数；没有实际第二评审人时保持单评限制，不编造独立性。

模型结果需分阶段评审：先判断原始发现/归属语义，再判断程序公开的每维前三主题，避免只审展示子集掩盖漏检。遗漏指标的分母必须由人工预先枚举本轮冻结样本内明显问题，不把样本外总体未知问题算作已召回；样本覆盖与模型遗漏分开记录。

### 指标、分母与准入门槛

| 指标 | 定义与评审方法 | 当前真实结果 |
| --- | --- | --- |
| 工程执行成功率 | 有足够证据且无故障的真实请求中，协议完整、两级验收通过且按约束终止的请求数/该类请求数；故障场景另算“预期受控状态符合率” | 未运行，N/A；不能计合成测试数为真实请求数 |
| 主题名称准确率 | 人工确认准确且非上级维度泛称的候选主题数/全部待审候选主题数；同时报告公开主题子集 | 未评分，N/A |
| 主题定义忠实率 | 无新增对象/情感/条件、可由原样本支持的定义数/待审定义数 | 未评分，N/A |
| 观点归属精确率/召回率 | 人工接受的观点-主题边/模型给出的边；模型正确覆盖的人工边/人工应有的边；多标签单独计边 | 未评分，N/A；不能把同一表达展开后的大量重复观点当独立语义样本 |
| 重要问题召回率 | 冻结样本内被模型覆盖的人工预先定义重要问题数/该样本人工重要问题数 | 未评分，N/A；样本外总体召回未知 |
| 证据支持率 | 经人工确认quote实际支持具体主题名/定义的公开引用数/公开引用数；source/位置合法率另外报告 | 人工语义N/A；位置正确不能代替支持正确 |
| 计数正确率 | 独立按(review_id,aspect,topic)去重的N、观点数、S、M、D、比例与公开产物完全一致的项/待审项 | 真实N/A；合成对账已测试 |
| 样本覆盖率 | 每维S/M、纳入观点/全量观点；归属覆盖 classified/selected 另列 | 不设想当然的全量代表性；真实M/D未读取 |
| 跨维污染率 | 被错误归到另一业务维度/主题的人工判错边数/归属边数；同评多维合法支持不能当污染 | 未评分，N/A |
| 多维回答完整率 | 人工确认全部必做维度及其重要问题、引用、分母、限制均完整的正常请求/正常多维请求；partial不计完整成功 | 未评分，N/A；另外记录成功子维比例和原因 |

所有指标同时报告样本量、分子/分母、逐维结果、宏平均与微平均；比例附Wilson 95%区间，标明review级聚类/重复表达，不将同评论多主题当独立用户。空分母记N/A，不记100%。开发集/旧曝光集与未见集分开，包装1例等稀疏层明确不足。

建议先冻结的质量门槛草案：来源/计数/位置/必做集合/状态/费用权限等硬规则100%；主题名和定义准确率及归属精确率≥95%、人工证据支持率≥98%、样本内重要问题召回率≥90%、无故障多维回答完整率≥95%；任一伪造来源、未获支持的公开事实或跨商品污染立即停止发布。**数值是待负责人确认的评估方案，不是既有承诺或本轮达标结论**；还须规定逐层样本量及置信区间要求，不能凭小样本点估计批准。

本轮故障注入让发现和归属两个模拟响应同时把真实“客服回复缓慢/降温速度慢/配送延误”等观点称为“安装师傅迟到”。程序仍可complete并生成模型候选DTO，因为来源/计数/位置均合法。这明确表明两个模型阶段不是独立人工语义证明。真实试验输出必须在隔离验收页面或私有产物中人工复核后评估，不直接用于生产发布；本报告不把不支持主题改判为正确。

## 6. 真实业务场景验收矩阵

费用记号：经核实授权的输入上界为 U、每token保守单价为 p_in/p_out。发现预留 `B_d=U_d*p_in+1200*p_out`；复核预留 `B_c=U_c*p_in+3000*p_out`；`B_k=Σ(B_d,a+B_c,a)`，k≤3；实际启动集合J的保守上界为 `ΣB_j≤本轮授权C`。若额外类别未知，费用上界**未知、真实调用禁止**。失败/usage缺失不释放预留。

只把官方公告作为待确认的候选费率时，三维输出预留项可计算为12600×6/百万=¥0.0756，另加全部输入上界×4/百万；输入上界及账户结算未确认，因此**没有可批准的本轮人民币总上界**。所有场景目前都不获真实费用授权。

| 场景 | 预期状态 / 模型次数 | 费用上界 | 检查点与失败退出 |
| --- | --- | --- | --- |
| A 六维统计→多维追问 | 排名ok/succeeded、0模型；P6正常k维complete/ok/succeeded，主题2k次≤6，P6意图/规划/回答0次 | 排名0；P6≤B_k且≤C | 同会话身份、完整六维、最终范围、排序与k、同快照D/M、逐维S/N/引用、公开前三；目标/来源异常立即error、无主题 |
| B 已有单维主题 | 继续旧单维路径；正常ok/succeeded；主题调用=1次发现+ceil(唯一表达数/20)次复核，负向原上限80表达时≤5；回答可能再0～2次，语义解析是否调用另计 | **不受P6预算授权覆盖**；须另设包住全路径的批次授权和逐调用费用上界，缺失则真实B场景BLOCKED | 不能为了“≤2次”改变旧语义或套用P6假设；确认单维工具/negative/范围、原回答证据校验；超批准次数/时间立即停止，无隐式重试 |
| C 排名过期→刷新统计 | D/任一M变：旧追问unsupported/succeeded、complete=false、准备stale_ranking；模型0；显式刷新排名后新目标才可执行 | 过期检测/统计0；刷新后≤B_k | 当前统计后不得预读维度或分类，不自动替换前三；同计数修订单独按PG-05处理，不能声称已检测任意变化 |
| D 第一成功、第二失败、第三成功 | 对可继续的非全局/非鉴权/非超预算失败：partial/partial，complete=false；第二发现失败共5次，第二复核失败共6次 | 实际≤2组B_d+B_c加失败阶段预留，总≤B_3、≤C | 完整required三维保留，第二无公开主题、第一/第三各独立通过；若全局来源异常则改硬失败，不能partial绕过；额度/总截止停止第三次付费 |
| E 全部主题证据不足 | 正确完成发现/归属但无支持：insufficient_evidence→unsupported/succeeded、complete=false；k维最多2k次，现非空输入正常路径为2k | ≤B_k、≤C | 无空主题卡片、无编造结论；区分模型返回错误/JSON失败与正确的无证据，不把失败混成证据不足 |
| F 超时与预算不足 | 首维请求迟到：当前维failed，后维是否可继续受共享预算和可靠终止条件控制；全部预留不足：0次、skipped_budget/error/failed；一维完成后不足：partial/partial；401/402/403应仅1次后停止 | 已启动请求全额保留≤C；供应商未可靠取消时实际结算仍需对账，不能宣称费用为0 | 无迟到发布；每维≤2/整轮≤6；不得SDK重试；quota兼容映射、20秒总耗时/取消及隐藏推理未通过前真实F BLOCKED |
| G 同评支持两个维度 | 两维均独立支持则正常complete/ok/succeeded（其他必做维也必须完成），2k次 | ≤B_k、≤C | 各自opinion/topic/result来源和片段保留；不按review_id全局去掉第二维证据；D/M/S/N分别去重，禁止跨维相加为用户数 |
| H 合理主题但无原文支持 | 假引用/错位置：当前维failed，其他有效可partial；若source/范围破坏则全局error/failed。**原文合法但语义不支持时，当前代码可能仍complete/ok**，独立人工质量判FAIL，隔离结果不得发布为质量通过 | 模型阶段≤2k、≤B_k/C；人工评审0模型；不追加“修正”模型请求 | 分开测结构无支持与语义无支持；自动Validator通过不覆盖人工否决。先建立语义质量隔离/评审门，不放宽来源断言或改为统计制造通过 |

场景D的局部普通失败与F的权限/额度/总截止必须分开；不能为了得到第三维成功，在应停止计费时继续请求。每次运行保留原始请求身份、冻结输入摘要、尝试顺序、费用预留、usage原字段、实际耗时、父子验收、公开DTO及人工评审结论；测试集答案与模型输入隔离。任何重跑需独立记录和剩余批次授权，不用同一个“每轮上限”无限重试。

## 7. 必须修改/补齐的代码与回退

本轮只审计和记录，不在E0实施下表修复。无须重写Planner、分类器或改变P0-B/P1-A信任机制。

| 优先项 | 实际涉及位置 | 修改原因 / 验证要求 |
| --- | --- | --- |
| 真实费用profile装载 | `review_budget.make_execution_budget`、受控服务端配置/授权入口 | 当前所有开关全开也不会真实调用；需要明确授权记录、过期/币种/精确模型、每轮及整批上限、可靠token上界，不可硬编码真实价格或接受前端授权 |
| E0-F01～03 | `P6SharedBudget.request`、`review_topics._call`及provider响应适配 | 保留原usage/model/trace；校验模型别名；规范化缓存/推理等真实类别，精确费用未知就保持未知，保守预留不能冒充实际账单 |
| E0-F04 | 同一provider错误适配及budget halted逻辑 | 按明确错误合同区分限流与quota；显式权限/额度失败停止后续；覆盖真实SDK MockTransport反例，不删除本轮失败断言 |
| token证明 | 受控授权profile函数及版本/消息投影测试 | 验证真正使用的tokenizer/chat template、system+wire/schema+封套上界；没有可靠方案则继续禁止真实调用 |
| 可靠截止与退出 | `_call`传输层、执行协调器、Worker.stop | 需要请求总截止/取消或明确远端未终止后停止后续的合同及实测；使用受控进程或传输取消时也须验证远端费用，不用线程超时伪装取消 |
| PostgreSQL测试与资源上限 | 新独立P6 PostgreSQL测试；连接工厂/Agent局部timeout | 实施PG-01～08；schema前操作、总预读预算、锁/idle超时与断连资源释放先以专用角色配置验证；确有缺口再最小修改工厂，保留原SQL口径 |
| 数据新鲜度契约 | `check_current_ranking`、新版本来源/修订指纹设计（若要求任意内容变化过期） | 现行D/六维相等不证明文本相等；先定契约再增版本，不能修改冻结旧排名摘要使其看似满足新能力 |
| 评估运行隔离与批次记账 | 新离线/小批验收入口、评估清单/人工结果存储 | 真实预测前先固定输入/费用批次，不让已实现P6-D自动发布未经质量评审的试验输出；保留旧资产、原结论和双评记录 |
| 单维对照授权 | 旧单维分析外围验收运行入口 | 场景B没有P6共享预算；真实测试必须有完整调用计费边界，不借P6开关绕过默认拒绝 |

回退：保持三个P6开关为0、真实费用工厂无授权；可继续既有统计/明确单维等路径，但任何旧路径收费仍须单独授权。出现来源/范围/计数破坏全局硬失败；局部普通错误只发布逐维独立通过子集；全部无证据/预算跳过保持既有外层状态。关闭发布并不取消正在供应商执行的请求，已产生费用不释放。进程重启不从prepared诊断恢复执行，须新请求、重新校验排名/范围并取得新预算，不自动续跑收费阶段。

## 8. 本轮独立测试、证据及结论

| 检查 | 当前结果 | 证据 |
| --- | --- | --- |
| P6-A/B/C/D及P0-A/P0-B/P1-A/F01定向复验 | 149/149 PASS，0 failure/error/skip | `.cache/p6e0/regression-summary.json`、`regression-tests.txt`；socket/psycopg封锁 |
| 原有全部适用功能回归 | **556/556 PASS**，225.913秒，0 failure/error/skip | `.cache/p6e0/functional-summary.json`；沿用P6-D的556项清单，不删旧断言 |
| E0独立SDK/准入反例 | 13项，9 PASS / 4 FAIL，0 error/skip | `.cache/p6e0/independent_preflight.py`、`independent-tests.txt`、`independent-summary.json` |
| 前端真实Worker产物契约测试 | 31/31 PASS | `.cache/p6e0/frontend-p6d.txt`；未改前端源码和旧测试 |
| PostgreSQL真实并发 | BLOCKED，0执行/0连接 | 独立测试库配置缺失；未将mock/SQLite结果计为PostgreSQL通过 |
| 真模型与主题质量 | BLOCKED / N/A；收费请求0、Embedding0 | 本地MockTransport及合成响应不计真实准确率 |
| 冻结资产与既有实现保护 | 入场540个受保护文件摘要全部未变、无删除；原标签、原B2结论、来源文本、源码和旧测试保持原样 | `.cache/p6e0/before-hashes.json`、`quality-assets-summary.json`、`final-protection-check.json` |
| 历史13个私有导入失败 | 独立历史状态保留，不清理，不计为新的功能通过项 | 本轮不改边界检查或相关实现 |

证据链：

- **E-01 → F-01 → P-01**：脱敏环境元数据和源码默认工厂 → 无独立测试库、无真实费用授权/token上界 → 先准备独立环境和服务端授权，不能执行真实调用。
- **E-02 → F-02 → P-02**：连接工厂/Agent/preparation源码及mock/SQLite故障注入 → 同事务设计成立，PostgreSQL实证与全量内容新鲜度有边界 → 执行PG-01～08，单列同计数内容变化契约。
- **E-03 → F-03 → P-03**：真实安装SDK+MockTransport的4个失败断言 → 计费/模型/错误合同未满足真实准入 → 最小provider适配、响应和账本修复后保持反例重跑。
- **E-04 → F-04 → P-04**：冻结真实资产摘要、222位置检查、两模拟模型共同错误主题实证 → 结构成功不等于语义达标，旧检索人工标签不是P6金标准 → 新版本双评/未见集、先隔离预测再质量决策。

重现仅限本轮已有离线入口（项目根目录PowerShell）：

```powershell
$env:PYTHONPATH='.;src;tests'
.\.venv\Scripts\python.exe .cache/p6e0/independent_preflight.py
.\.venv\Scripts\python.exe .cache/p6e0/run_checks.py functional
npm --prefix frontend test -- --maxWorkers=2 --minWorkers=1 src/features/agent/p6-publication.test.tsx
```

第一条命令当前应非零退出并报告4项准入失败，不能为了绿色结果删改断言。脚本/脱敏证据在被忽略的 `.cache/p6e0/`，本轮Git交付仅新增本报告。未更新生产配置、业务源码、冻结RAG或人工评审资产。

**最终准入：BLOCKED。** 离线工程链可继续复验；真实PostgreSQL环境、token/价格/费用授权、4项provider契约缺口、可靠截止/资源边界以及独立真实主题评审未完成。全部关闭条件解除且获得明确的小规模真实执行授权前，不进入真实供应商、生产数据库或生产部署。
