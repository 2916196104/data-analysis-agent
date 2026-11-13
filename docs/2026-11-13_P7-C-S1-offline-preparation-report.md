# P7-C-S1 · Windows网络闸门兼容性修复：离线准备报告

**离线准备PASS，S1整体验收BLOCKED于新的真实启动授权。** 本轮严格按“当前只允许继续离线准备；未获得新的真实启动授权前，不得启动服务”执行。API、Worker、React均未启动；真实业务任务、Query Embedding、Top-5、付费模型、向量同步、PG连接及业务写入均为0。报告日期：2026-11-13（Asia/Taipei）。

真实API/Worker/React尚未执行，不能将其填为PASS或FAIL；本报告记NOT_RUN/null，待授权实测后再落PASS/FAIL。本轮不执行P7-C-S2。

## 1. 原始记录与修改范围

基于最新P7-C BLOCKED：父进程网络audit只允许127.0.0.1:5432，误拦Windows asyncio创建socketpair所用的本机临时端口，API尚未进入lifespan即失败。旧失败报告、日志、执行过的guard、修正候选和独立空任务库全部保留；本轮对其 **30个文件**建立前后SHA对照，变化0。

原样复用 `guard-candidate/p7c_real_guard.py` 至本目录 `guard/p7c_real_guard.py`，SHA256：`8669b60b7b951920d7e39bda39e46892341d125eea2c565ee31394f2aeedc34e`。复制件与原候选逐字节一致。本轮限制放在独立 `guard/s1_guard.py`，没有覆盖旧guard或BLOCKED记录。

仓库HEAD `a83962601c28fffe0146c8fd8e4e990de303098b`；原 **704项**源码/旧测试/文档摘要变化0。未修改生产RAG、State、Router、Planner、Validator或事实许可；未改数据库角色、权限、代理、防火墙或模型配置。仓库只新增本报告与独立证据包。

## 2. 兼容性与边界

- 允许数字地址127.0.0.1与::1上的合法本机端口，供Windows asyncio临时socketpair通信；不新增外部目的地址。拒绝localhost主机名、伪装域名、非回环IP及非法端口，避免DNS名字扩大目的地范围。
- 保留候选的收费SDK和httpx拒绝，补齐标准库HTTP请求阻断，包括经本机代理发送的外部HTTP；拒绝非回环TCP/UDP和外部DNS查询。
- PostgreSQL角色/数据库/地址/端口固定，拒绝非本机hostaddr、连接关键字覆盖和service覆盖。Worker命名空间强制readonly，raw connect选项也强制default_transaction_read_only=on。这里用合成PG连接核验调用参数，**未连接真实PG**，不据此宣称当前角色权限实测通过。
- 业务写入和readonly关闭在公共execute入口提前拒绝，再沿用候选的底层SQL防线；向量同步/迁移在入口拒绝，不等待SQL写入才阻断。
- S1将全部任务/编码/检索/模型RPC额度固定为0，重复和并发调用均拒绝。即使误放execution-armed文件仍不能消费额度；真实模型构造、health/RPC、同步、向量检索及Worker任务执行也显式拒绝。
- 专用ASGI包装只转发原生lifespan和 `GET /api/v1/health`，其余HTTP/任务写路由和WebSocket拒绝。该包装的单元测试使用合成ASGI callable，没有创建真实API、Worker或测试服务器。

这些都是运行器进程中的限制，不修改生产实现或业务验收标准。

## 3. 本轮离线结果

| 检查 | 结果 |
|---|---|
| Windows真实Proactor事件循环、跨线程唤醒与关闭 | PASS，loop=ProactorEventLoop，线程和循环均关闭 |
| Windows socketpair本机字节传输与句柄释放 | PASS |
| 网络/SDK/HTTP/PG地址/只读/写入/同步/零额度/重复并发边界 | 与上述IPC合计21/21 PASS |
| 新S1授权、旧P7-C授权拒绝、启动次数/零业务额度、辅助入口与默认CLI | 7/7 PASS |
| 交付目录独立复现 | PASS，21项与最终7项均从交付包运行 |
| 原失败文件和原生产/测试/文档完整性 | PASS，0变化 |
| 真实API/Worker/React | NOT_RUN，未获得新的启动授权 |

总计 **28/28离线测试通过**。真实PG使用明确的进程内合成连接替身，SDK/HTTP均在真正发送前拒绝；没有启动TestClient生命周期、API/Worker/React服务，也没有加载模型。实际网络行为仅为Windows asyncio/socketpair所需的本机IPC。

首次新增测试为18/21：3个错误均因合成FakeRaw缺少transaction_status字段。已补齐该字段，并把S1写入拒绝提前到公共execute入口，避免产生无用BEGIN。`offline-initial.log/json`与 `iterations/` 中的初版源码保留，未改写旧失败。

证据：`bundle-offline-final.json/log`、`windows-asyncio.json`、`startup-authorization-delivery.json/log`、`offline-resource-inventory.json`、`integrity-check.json`。

## 4. 待新的启动复核授权

`startup-authorization.json`仍为approved=false。主入口与API辅助入口均会检查**新P7-C-S1授权**，旧P7-C业务授权不能复用。默认执行 `startup_runner.py`只打印离线状态，不启动任何进程。当前没有startup-claim、startup-real目录或本轮真实任务库。

已准备的受限方案：

1. 新授权明确API/Worker/React各启动最多1次；任务、编码、Top-5、收费、同步、PG写入和自动重试均0。通过验证后才创建一次性claim和新的独立startup-real目录/SQLite库；不复用旧目录。
2. 复用真实生产API与嵌入式TaskWorker、现有React/Vite；LABEL_EXECUTION_MODE=local，DB固定rag_audit_ro。模型相关入口全部拒绝，无模型下载或加载。
3. 启动等候上限30秒；进程退出或超时即失败，不重启。只发一次健康GET和一次React根页面GET；不打开业务聊天或提交接口。
4. 记录原生worker_state，等待两次5秒心跳推进；记录真实Worker锁会话身份及transaction_read_only=on，不创建任务。
5. 正常停止API生命周期/Worker，关闭React；检查原生stopped_at、空任务库、PG会话/事务/跨角色锁和8011/5184监听。失败保留日志并清理，只处理本轮创建的进程。

该执行器只完成离线授权与证据判据验证，**真实启动、心跳、锁释放与清理路径尚未执行**，不能预先判PASS。

## 5. 分项验收记录

| 项目 | 当前结果 | 证明范围 |
|---|---|---|
| Windows asyncio事件循环 | PASS | 独立真实Windows Proactor事件循环、socketpair和跨线程唤醒，均关闭 |
| API启动与健康接口 | NOT_RUN | 未获得新的S1真实启动授权 |
| Worker生命周期及锁 | NOT_RUN | 未启动真实Worker，未连接真实PostgreSQL或取得锁 |
| React启动 | NOT_RUN | 未获得新的S1真实启动授权 |
| 网络与费用边界 | PASS | 离线入口回归21/21及授权回归7/7；不代表真实启动已通过 |
| 资源清理 | PASS | 本轮离线循环、线程、socket均释放，子进程结束；真实服务清理待授权后验证 |

真实三项NOT_RUN/null遵循当前授权限制，不把“未执行”伪记为失败，也不以旧PASS补齐。

## 6. 清理与复现

离线循环、socketpair、线程均已释放，测试进程已退出。本地只读盘点未发现项目Python/Node进程或8011/5184监听；原有PostgreSQL/Redis保留。因本轮没有真实PG连接或Worker锁，未执行PG会话/锁查询，不能将这项离线清理当成真实服务清理PASS。

仅复现离线测试：

```powershell
$bundle = 'C:\Users\a7989\Documents\Codex\2026-11-13\mei\outputs\p7c-s1-offline-20261113T095658Z'
$python = 'E:\AllProject\data-analysis-agent\.venv\Scripts\python.exe'
& $python -B (Join-Path $bundle 'offline_tests.py') ('offline-recheck-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'))
& $python -B (Join-Path $bundle 'startup_plan_tests.py') ('authorization-recheck-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'))
& $python -B (Join-Path $bundle 'startup_runner.py')
```

最后一条默认命令只输出OFFLINE_PREPARED_NO_SERVICES_STARTED。不得在尚无新授权时修改approved状态或启动服务。

## 7. Evidence → Finding → Path

| Evidence | Finding | Path |
|---|---|---|
| E-01 baseline.json + integrity-check.json | F-01：原失败30文件、原候选及生产文件完整保留 | 读取最新BLOCKED→候选原样复制→SHA前后核对 |
| E-02 windows-asyncio.json + bundle-offline-final.json/log | F-02：本机IPC兼容且所列边界保持拒绝 | S1适配→真实Proactor/socketpair→合成副作用拒绝回归 |
| E-03 startup-authorization-delivery.json/log + startup-authorization.json | F-03：当前与旧授权均不能启动S1服务 | 主入口/辅助入口检查新S1授权→拒绝于进程创建之前 |
| E-04 offline-resource-inventory.json | F-04：本轮只做离线准备，没有服务或业务调用遗留 | 离线进程退出→本地进程/端口盘点，未查询真实PG |

机器结果 `acceptance.offline.json`；交付摘要 `integrity-manifest.json`。整体结论保持 **BLOCKED（等待新S1启动授权）**，真实启动复核完成前不改变此结论。
