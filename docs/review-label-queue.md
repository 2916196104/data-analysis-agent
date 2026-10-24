# 持久评论标注：PostgreSQL 与 Redis Streams

新增 `review-label queue` 流程按固定大小的数据块持续处理评论。原 CLI/API 可通过 `LABEL_EXECUTION_MODE=redis` 使用同一队列；评论内容、有效标注及观点仍使用原表和原校验函数。业务库和真实模型均未用于本次验证。

模型调用采用至少一次执行：**模型已经响应、结果还未提交时进程崩溃，恢复可能再次调用模型并产生费用**。成功结果由 PostgreSQL 唯一索引保证不会重复保存，无法承诺外部模型请求严格执行一次。

## 职责与选择依据

| 存储 | 保存内容 | 恢复依据 |
|---|---|---|
| PostgreSQL 原有表 | 评论、标注尝试、成功结果、观点、人工复核 | 最终业务依据 |
| PostgreSQL 新增表 | 运行范围、评论级任务、租约、发件箱、请求指标、错误与心跳 | 队列丢失后重建工作 |
| Redis Streams | 只包含 `task_id` 的消息、消费者组和 pending 消息 | 分发、确认、超时消息接管 |
| Redis Lua 限流键 | 同一配额范围的滑动窗口、token 预留、并发租约、429 冷却 | 所有标注 Worker 共享协调 |

选择 Streams 是因为当前需要消费者组分发、显式确认和未确认消息接管，且无需新增大型任务框架。`XREADGROUP` 领取新消息，`XAUTOCLAIM` 接管超时消息，提交后 `XACK` 并 `XDEL`。最低 Redis 6.2，实际协议测试使用 7.0.15。[Redis 官方 XAUTOCLAIM 文档](https://redis.io/docs/latest/commands/xautoclaim/)说明了其接管语义及版本要求。

Redis 不保存评论正文，不作为成功/失败状态的最终依据。任务身份包含评论 ID、正文 SHA-256、原记录指纹、模型、服务地址和提示词版本；正文指纹单独持久化，重启后仍能发现建任务后的正文变化。数据库任务和发件箱同事务创建；派发器先 XADD 再标记发件箱。两者之间崩溃可产生重复消息，数据库租约和成功唯一索引负责消重。

## 先核实的旧流程

- `run_labeling` 原来一次最多取 1000 条，每请求 1–8 条，单进程最多 4 个请求并发；结果列表常驻内存，不能一次持续处理几十万条。
- 首轮 SDK 重试为 0；恢复轮使用 `LLM_MAX_RETRIES`。旧 `model_calls` 只计 SDK 方法调用，未独立记录 SDK 内部重试。
- 成功记录按评论 ID、`content_sha256`、模型、服务地址和提示词版本增量跳过。该指纹来自规范原始记录，包含正文；保存时也再次核对正文及指纹。
- 批量结果按 review_id 对齐，并逐条精确定位 quote；结构、ID 和 quote 失败递归二分，网络错误留给恢复轮。
- 原 `TaskWorker` 是导入/标注/分析等粗粒度任务的单 Worker 执行器，持有业务库级锁，不能直接扩展为多个标注执行者。

新流程复用 `label_batch_once`、`review_batches` 和 `append_annotation_on_connection`。新增 `label_tasks` 是评论级调度记录，成功标注仍只写 `review_annotations/review_opinions`。旧 `tasks` 表继续管理页面任务；Redis 模式下旧入口只作提交和进度协调。

## Windows 启动

在项目根目录执行。先启用 Docker Desktop 的 Linux 容器，然后启动一个只映射到本机的 Redis：

```powershell
docker run -d --name review-label-redis -p 127.0.0.1:6379:6379 -v review-label-redis-data:/data redis:7 redis-server --appendonly yes --maxmemory-policy noeviction
docker exec review-label-redis redis-cli ping
.\.venv\Scripts\python.exe -m pip install -e ".[test-queue]"
```

已有容器后用 `docker start review-label-redis`；停止用 `docker stop review-label-redis`。本次验收实际使用 WSL 中临时解压的 Redis，未启动或修改业务 Redis；Docker 步骤为操作示例，未在本机运行成功的 Docker 引擎上验证。

保留本地 `.env` 的 `DATABASE_URL` 和模型配置，仅增加 `.env.example` 中的 `LABEL_*` 项。RPM、TPM 必须填写账户真实配额。`LABEL_QUOTA_SCOPE` 是普通名称，例如专用于评论标注的账户别名，不放 API key。共享账户的所有 Worker 必须连接同一个 Redis DB，使用相同 scope 和配额配置；配置不一致会拒绝请求。

| 参数 | 初始值 | 含义 |
|---|---|---|
| `LABEL_DB_CHUNK_SIZE` | 100 | 一次从 Redis 接收、从 PostgreSQL 领取的最大评论数；上限 1000 |
| `LABEL_BATCH_SIZE` | 4 | 一次模型请求评论数；上限 8，并遵守原正文长度限制 |
| `LABEL_MAX_CONCURRENCY` | 2 | 同一 quota scope 所有 Worker 合计请求数 |
| `LABEL_RPM` / `LABEL_TPM` | 必填 | 账户允许分配给本流程的请求/token 配额 |
| `LABEL_MAX_CALLS_PER_REVIEW` | 6 | 一次人工提交预算内，单评论最多参与几次请求，含拆分、恢复和崩溃不确定尝试 |
| `LABEL_MAX_RETRIES` | 2 | 网络/429 首次失败后最多自动恢复两次 |
| `LABEL_BACKOFF_SECONDS` | 10 | 指数退避基数；自动退避最大 3600 秒，服务端更长 Retry-After 仍优先 |
| `LABEL_LEASE_SECONDS` | 120 | 超时接管与补发间隔；必须超过请求超时至少 10 秒 |

若 `LLM_TIMEOUT_SECONDS` 较大，要相应增大租约；未显式配置时默认至少为 `2 × timeout + 30` 秒。

先备份数据库，再执行一次增量建表：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.labeling queue init --db env:DATABASE_URL
```

此命令只新增九张 `label_*` 表及索引，不重建或迁移原评论和标注表。数据库必须已经完成原有项目初始化；已有业务库不要重复执行 SQLite→PostgreSQL 迁移。

创建本次范围，输出中保留 `run_id`：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.labeling queue submit --db env:DATABASE_URL --limit 10000
```

将 `--limit` 改为 `100000`、`300000`，或省略以处理所选范围全部评论。可加 `--product-id`、`--source`、`--dataset-version`、`--date-from`、`--date-to`、`--as-of`；日期筛选沿用原 UTC 规则。创建时固定最大评论 ID，后续新导入记录不无限延长本次运行。建任务通过主键游标分块，不使用大 OFFSET，不把所有正文读入内存。`submit --chunk-size` 控制建任务块，独立于 Worker 领取块和模型 batch。

先运行一个 Worker 完成首轮验证：

```powershell
$runId = Read-Host '粘贴 submit 输出的 run_id'
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.labeling queue worker --db env:DATABASE_URL --run-id $runId
```

多个 PowerShell 窗口执行相同 Worker 命令即可竞争消费，建议从两个 Worker 开始。每个 Worker 同时执行一个请求，总并发仍由 Redis 的 2 个共享名额限制。带 `--run-id` 的 Worker 会补齐尚未完成的建任务阶段，并在该运行完成、部分最终失败或认证暂停时退出；它也可以消费同队列其他已提交工作。独占该范围时使用一个专用队列。省略 `--run-id` 时常驻消费，Ctrl+C 停止；`--max-seconds 600` 可设置停止期限，当前请求完成后退出。

Worker 自带发件箱补发。需要独立派发进程时：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.labeling queue dispatch --db env:DATABASE_URL
```

队列消息软上限为 10000 条，多个派发者可能短暂超过若干领取块；未投递任务保留在 PostgreSQL。ACK 后删除 Stream 条目，不裁剪未完成消息。

## 现有页面与 CLI 接入

将 `.env` 的 `LABEL_EXECUTION_MODE` 改为 `redis`，再按原方式启动项目：

```powershell
.\.venv\Scripts\python.exe run.py
```

另开终端常驻运行 `queue worker --db env:DATABASE_URL`。原页面标注和 `review-label run` 将只创建/观察持久运行，调用统一由专用 Worker 负责；模型 batch 和并发使用 Worker 的 `LABEL_*` 配置。页面原有单次 1000 条上限保留，大范围用 `queue submit`。

原协调入口默认等待最多 3600 秒，可用 `LABEL_COORDINATOR_TIMEOUT_SECONDS` 调整；超时返回部分状态和 run_id，后台持久工作仍可继续。停止页面后台任务不会删除持久运行。`LABEL_EXECUTION_MODE=local` 保留原小批量行为，**不要在同一模型账户同时混用 local 标注和共享配额 Worker**。销售/问答等其他模型调用不在本标注限流器内，配额应按账户实际分配。

## 中断恢复与失败处理

- Worker 在调用前再次核查成功记录、正文、指纹、暂停状态和租约，预先记录本次调用预算。模型请求期间不持有数据库事务锁。
- 模型输出仍按 review_id 拆分，quote 必须为连续原文；结果、观点、任务终态或恢复发件箱同一事务提交，之后才 ACK。数据库提交异常不确认消息。
- Worker 心跳每至多 5 秒续租；更新跳过正在提交的行。租约令牌用于阻止旧 Worker 覆盖接管者。正常停止会释放未完成工作；硬中断等租约超时，由其他 Worker 接管。
- timeout、connection_error、server_error、rate_limit 延迟恢复。属于同一运行的首次正常评论先完成，之后才恢复失败；不会因一个网络错误触发拆分树。
- 429 尊重秒/毫秒/HTTP 日期形式的 Retry-After，且建立所有 Worker 共享冷却时间。认证错误在 PostgreSQL 暂停整个模型身份；已在途请求可以收尾。
- 输出格式、review_id、quote 校验失败二分，并受每评论调用预算限制。参数/API 错误拆到单条仍失败则进入人工状态，不无限重试。最终失败保留错误码和历史请求。
- Redis 暂时不可用会使 Worker 以基础设施错误停止，未提交任务仍在 PostgreSQL。恢复 Redis 后重启 Worker；自动补发间隔为租约时长，也可立即修复发件箱。
- Lua 原子核对 Redis `run_id`，包括 Redis 重启后恢复了旧配置键的情况；冷启动等待 `max(60 秒, 请求租约)` 后才接纳新调用，避免立刻重用完整额度。正常启动空 Redis 同样等待，ACL 需要允许 `INFO server`。

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.labeling queue repair --db env:DATABASE_URL
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.labeling queue stats --db env:DATABASE_URL --run-id $runId
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.labeling queue failures --db env:DATABASE_URL --run-id $runId --limit 100
```

`failures` 用 `--after-id` 继续读取下一页。检查并修正配置后，用输出中的 profile_id 解除认证暂停；manual/failed 任务需显式重新提交，历史记录保留，重新获得一次有界预算：

```powershell
$profileId = Read-Host '粘贴 profile_id'
$taskId = Read-Host '粘贴失败 task_id'
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.labeling queue resume --db env:DATABASE_URL --profile-id $profileId
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.labeling queue resubmit --db env:DATABASE_URL --task-id $taskId
```

`queue submit --selection transient` 兼容原标注历史，只选择最近失败为可恢复网络错误的记录；已在新队列耗尽预算的任务仍须人工提交。`--selection failed` 是人工显式重新开放失败预算，仍跳过成功记录。原导入删除会拒绝仍有活动队列任务的评论，最终任务可随导入删除，保留请求聚合审计。

## 指标口径

`queue stats --run-id` 返回评论条数和运行总耗时、平均每分钟成功数、最近 60 秒成功条数、终态失败数、请求错误分布、请求平均/P95、token、重复请求数和父批次拆分数。省略 run_id 返回 PostgreSQL 待处理/处理中条数、发件箱行数、最近 100 个 Worker 心跳；Redis pending/lag 可用 `XINFO GROUPS`、`XPENDING` 查询，消息条数可能因补发而超过评论条数。

请求级原始记录在 `label_requests`，参与评论映射在 `label_request_tasks`：

| 指标 | 含义 |
|---|---|
| `successful_reviews/failed_reviews` | 评论终态数量，恢复中的评论另列 retry |
| `model_requests` | 已持久预留的 SDK 调用尝试次数；崩溃导致是否实际发出未知的尝试也保留 |
| `uncertain_requests` | 调用开始记录已保存，但未保存结束记录；不能假定未计费 |
| `average_request_ms/p95_request_ms` | 直接使用整次模型请求耗时，含失败；不使用按评论均分后的 elapsed_ms |
| `input_tokens/output_tokens` | 服务端返回 usage 的已知合计；缺失时保持未知并计入 requests_without_usage |
| `repeated_requests` | 有评论第二次及以后参与请求的次数，包含拆分和自动恢复；人工重新提交重开预算，原请求历史不删除 |
| `batch_splits` | 因校验/批量拒绝而触发二分的父请求数；任务 split_count 是该评论参与拆分的次数 |
| `sdk_max_retries` | 新队列始终为 0；重试全部显式调度，每次请求有独立记录 |

TPM 在派发前按系统提示词和正文的 UTF-8 字节数、结构余量 1024、输出上限 4096 保守预留，返回 usage 后对账；失败或缺失 usage 时保留预留额度直到窗口结束。它是本地预算控制，不是供应商 tokenizer 的精确预测；过大单次预留进入人工状态，可减小 batch。相同 scope 的所有 Worker 共用同一滑动 60 秒窗口和并发租约。

## 复现验收和基准

安装 PostgreSQL 客户端/服务端程序后，启动一个专用测试 Redis。验收脚本创建临时 PostgreSQL 集群和唯一测试库，只绑定 127.0.0.1，最终停止并删除测试库及数据目录；它不读取 `.env`，不调用真实模型。真实 Redis 测试使用随机键名并只删除自己创建的键，不执行 FLUSHDB。

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test-queue]"
# 不设 TEST_REDIS_URL 时队列协议使用 fakeredis；仍使用真实临时 PostgreSQL。
.\.venv\Scripts\python.exe scripts/queue_acceptance.py --stage 1
.\.venv\Scripts\python.exe scripts/queue_acceptance.py --stage 2
# 使用专用测试 Redis 的隔离 DB，不要填业务 Redis 地址。
$env:TEST_REDIS_URL = 'redis://127.0.0.1:6379/15'
.\.venv\Scripts\python.exe scripts/queue_acceptance.py --all-backend
.\.venv\Scripts\python.exe scripts/queue_acceptance.py --benchmark 10000
.\.venv\Scripts\python.exe scripts/queue_acceptance.py --benchmark 100000 --workers 2
.\.venv\Scripts\python.exe scripts/queue_acceptance.py --benchmark 300000 --workers 2
```

PostgreSQL 非默认安装位置加 `--pg-bin`。基准固定 batch=4、领取块=100、总并发=2，比较单/双 Worker，模拟响应延迟为 2ms；模拟 quota 设置足够高以观察本机处理链路，不代表真实服务额度。默认 fsync 开启，RSS 采样覆盖基准 Python 进程，包含多个 Worker 线程，不含 PostgreSQL 与 Redis 服务进程。输出到 `outputs/queue-validation/`，包含 JSON、逐阶段测试日志及 PostgreSQL 日志。实际结果另见 [本次验收记录](review-label-queue-validation.md)。

## 回退

先停止所有专用 Worker、派发器和页面提交入口，将 `LABEL_EXECUTION_MODE` 改回 `local`。保留新表即可回退应用入口；已提交的标注照常可供原工作台查询。不要同时运行 local 标注与旧队列消费者。

若要删除新增调度表，先用 PostgreSQL 备份工具导出 `label_*` 审计数据，再在所选业务库执行 [label-queue-down.sql](sql/label-queue-down.sql)。脚本在事务内按依赖逆序只删除新增表，不使用 CASCADE，不删除原始评论、标注、观点或旧任务表。Redis 在所有消费者停止后可清理专用队列及 quota scope 键；保留无消费者的旧消息不会影响 local 入口。不要对共用 Redis 执行 FLUSHDB/FLUSHALL。

回退 SQL 的实际演练以验收记录为准；真实业务库迁移、真实供应商配额/响应质量和实际计费未在模拟基准中验证。真实试跑前可以先按 `ceil(N/4)` 估计无失败首轮请求数，再按请求级 token 记录和供应商当前单价估算费用，不能从模拟 token 推算费用。
