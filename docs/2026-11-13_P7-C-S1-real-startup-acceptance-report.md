# P7-C-S1 · Windows网络闸门修复后真实服务启动验收

**结论：PASS，限S1真实服务启动、健康、Worker心跳、只读锁与资源清理。** API、Worker、React各启动一次；API健康GET和React首页GET各一次，均200；Worker心跳至少两次推进；所有业务/模型/检索调用均0。日期：2026-11-13（Asia/Taipei）。本轮不执行P7-C-S2，不以S1结果判定RAG真实业务闭环通过。

## 1. 授权与原件保留

本轮依据用户“批准P7-C-S1真实服务启动复核”的新授权执行。启动前再次通过28/28离线闸门回归，并与离线交付版本逐字节比对5个运行器/guard文件，一致。运行器、生产源码及配置文件均未修改；只使用运行器原有的进程级隔离环境。

独立目录 `C:\Users\a7989\Documents\Codex\2026-11-13\mei\outputs\p7c-s1-real-20261113T102206Z`，原生运行子目录 `startup-real-20261113T102513730008Z`，新建独立SQLite任务库。没有复用旧任务库，也未修改旧P7-C BLOCKED报告、失败日志或原始运行器。原64项历史产物、13个历史任务库及705项既有源码/旧测试/文档SHA均未变；新增报告及证据包单独保存。

一次性startup-claim已保留，同一运行器不会自动重新启动。只有本次1次启动尝试，没有重试或第二次启动。

## 2. 启动前真实只读约束复核

[只读预检](../outputs/mvp-real-acceptance/p7c-s1-real-20261113T102206Z/readonly-preflight.json)实测：角色和登录角色均rag_audit_ro，库reviews；transaction_read_only和default_transaction_read_only均on；Worker命名空间即使传readonly=False也被运行器强制为True。角色非superuser/createdb/createrole/replication/bypassrls，连接上限8。6项目标业务表有SELECT、无INSERT/UPDATE/DELETE/TRUNCATE/TRIGGER权限，现有目标锁和该角色其他会话均为空。诊断连接已关闭。

预检只查询元数据，没有取得Worker锁或执行编码/检索。实际Worker锁仅在真实Worker生命周期内取得一次，后续完整释放。

## 3. 真实运行结果

| 项目 | 本轮原生结果 |
|---|---|
| API | 真实API进程PID 43788；原生Uvicorn启动完成；唯一GET /api/v1/health返回200/status=ok |
| 健康内容 | business_database=ok、worker_available=true、各任务状态计数均0；Redis消费者不启用 |
| Worker | worker_id=`b2b2328b20934ffe954225a51ca73512`，仅一个worker_state记录；原生TaskWorker启动/轮询/停止 |
| Worker锁 | 业务backend PID 3780，advisory key 8358523899158648126；role/login=rag_audit_ro，read_only/transaction_read_only均on |
| React | 真实Vite启动，唯一GET首页返回200；原运行器验证id="root"。没有打开业务页面或追加GET |
| 启动期限 | 原运行器30秒启动等待保护未触发；从claim到Worker锁取得约2.998秒 |
| 原生任务库 | 0条tasks，task_id不适用；只写必要的worker_state/heartbeat/stopped_at |

Worker生命周期：`2026-11-13T18:25:16.728+08:00` 至 `2026-11-13T18:25:27.480+08:00`（Asia/Taipei），约10.752秒。对应原生UTC心跳为：

1. `2026-11-13T10:25:16.728+00:00`
2. `2026-11-13T10:25:21.757+00:00`
3. `2026-11-13T10:25:26.771+00:00`

三个不同时间戳证明两次推进；最终stopped_at=`2026-11-13T10:25:27.480+00:00`。完整轮询样本保存在[startup-real-20261113T102513730008Z/heartbeat-samples.json](../outputs/mvp-real-acceptance/p7c-s1-real-20261113T102206Z/startup-real-20261113T102513730008Z/heartbeat-samples.json)。

API唯一HTTP访问在[api.log](../outputs/mvp-real-acceptance/p7c-s1-real-20261113T102206Z/startup-real-20261113T102513730008Z/api.log)中记录。React状态由未修改运行器的一次根GET和root检查产生，原生200保存在[startup-result.json](../outputs/mvp-real-acceptance/p7c-s1-real-20261113T102206Z/startup-real-20261113T102513730008Z/startup-result.json)；未保存原HTML副本，也没有为补录正文再发请求。本阶段未验证浏览器业务展示。

## 4. 网络、费用与零业务边界

当前S1运行器只允许数字本机回环通信以满足Windows asyncio；保留收费SDK/httpx拒绝，并阻断外部TCP/UDP/DNS和标准库HTTP请求，PG固定本机只读角色。任务、编码、向量、模型初始化/RPC等额度全部固定为0；ASGI层仅允许原生lifespan与健康GET。

启动前21项网络/零业务回归和7项授权/证据回归通过。真实运行中boundary_blocked、quota_consumed、task_enqueued、embedding_start、model_rpc_start均0；无非只读连接或向量SQL。所有受保护PG语句均为只读元数据、心跳锁保活SELECT或连接级只读设置。

| 禁止项 | 实际次数 |
|---|---:|
| AI业务任务 / Query Embedding / Top-5 | 各0 |
| 模型加载 / 模型RPC / 付费LLM | 各0 |
| Embedding同步 / PG业务写入 | 各0 |
| 自动重启 / 自动重试 | 各0 |

费用0。没有下载模型、触发Agent或更改RAG/State/Router/Planner/Validator/事实许可。

## 5. 关闭、资源清理与历史完整性

API按停止标记进入Uvicorn正常shutdown，应用lifespan调用原生Worker.stop，API退出0；Worker写入stopped_at并释放锁。React由获批运行器主动terminate，退出码1；其进程和监听已消失。没有强制结束API的降级清理或服务重启。

[独立清理复核](../outputs/mvp-real-acceptance/p7c-s1-real-20261113T102206Z/cleanup-independent.json)确认：

- 4个受保护的PG后端 `[964, 3780, 22432, 39628]` 均有原生close事件，且已从pg_stat_activity消失。
- 该只读角色无其他遗留连接、事务；目标跨角色Worker锁0。独立诊断连接在IDLE状态关闭。
- 项目Python/Node进程0，8011/5184监听0；原有PostgreSQL PID33352/5432与Redis PID26272/6379保留。
- 原生tasks为空；新增SQLite库仅含必要Worker状态。原数据库/WAL/SHM、原生JSON及一致性SQLite快照均保留。
- 705项既有源文件、64项历史产物、13个历史任务库和5个获批运行器文件摘要变化均0。

## 6. 分项结论

| 验收项 | 结论 |
|---|---|
| API启动与健康接口 | PASS |
| Worker心跳、锁和只读连接 | PASS |
| React首页响应 | PASS |
| 网络与费用边界 | PASS |
| 资源清理及历史完整性 | PASS |

仅本次S1全部条件已满足。P7-C业务RAG闭环与P7-C-S2均未执行；S2必须另行取得真实业务调用授权。

## 7. Evidence → Finding → Path

| Evidence | Finding | Path |
|---|---|---|
| E-01 pre-execution-offline测试JSON/log、readonly-preflight.json | F-01：闸门、零额度和真实只读约束在启动前满足 | 精确版本比对→28项离线复核→真实RO元数据 |
| E-02 api.log、react.log、startup-result.json | F-02：各服务启动一次，唯一健康/首页GET通过 | 真实API/Worker/React→GET健康/根页面200 |
| E-03 runtime-events.jsonl、heartbeat-samples.json、task-store-native.json | F-03：真实只读锁及两次心跳推进，零业务调用 | 原生TaskWorker→只读Worker锁→心跳推进→stopped_at |
| E-04 cleanup-independent.json、before.json | F-04：锁、连接、事务、进程、端口和历史完整性均通过 | 正常关闭→独立RO诊断/本机盘点→SHA核对 |

完整机器结果 [acceptance.json](../outputs/mvp-real-acceptance/p7c-s1-real-20261113T102206Z/acceptance.json)，全部交付摘要 integrity-manifest.json。已消费的startup-claim必须保留，不得再次运行本轮启动命令。
