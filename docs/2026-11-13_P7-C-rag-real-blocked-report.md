# P7-C · 已授权受限真实浏览器验收：启动阻断报告

**最终结论：BLOCKED。** 本轮已有明确授权，但真实服务启动前条件未满足，已按用户要求停止并清理，没有再次启动。任务、Query Embedding和PG Top-5各0次；没有真实task_id或Chrome截图。报告日期：2026-11-13（Asia/Taipei）。本报告不以历史PASS或上一阶段离线PASS判定本轮成功。

## 1. 授权、范围和执行记录

固定问题：**商品100013588055有没有送货慢的反馈？** 来源 `reviews_csv / reviews-csv`，商品 `100013588055`，UTC `2021-09-11`—`2022-08-26`。HEAD `a83962601c28fffe0146c8fd8e4e990de303098b`。最多新任务1、BGE-M3 Query Embedding1、只读Top-5检索1；付费LLM/同步/PG业务写入/自动重试全部0；允许独立SQLite任务和状态记录，观察上限900秒。

新建独立任务库 `tasks.sqlite3`，不复用历史库。安全闸门未武装，任务库当前0条任务，worker_state也为空，浏览器提交标记不存在。task_id、conversation_id均null，任务状态为NOT_CREATED；候选、事实许可、Validator、public_answer、浏览器正文、任务耗时和截图均NOT_RUN/null。不能把不存在的结果改填成PASS。

| 实际操作 | 次数 |
|---|---:|
| 真实任务创建 / Query Embedding / PG Top-5 | 各0 |
| 模型RPC / 付费LLM / Embedding同步 / PG业务写入 / 自动重试 | 各0 |
| 真实API/React服务启动尝试 | 各1，失败后未再次启动 |
| 元数据安全预检 | 只读角色/权限/会话/锁与生产数据库启动检查 |

生产Worker未进入生命周期；真实Chrome尚未启动。Vite曾短暂监听5184，API未就绪后随本轮监督进程停止。900秒任务观察期限未开始，不能宣称该期限的真实超时处理已经验收。

## 2. 执行前安全验证

`safety-offline-tests.json`验证一次性闸门：未武装拒绝、每类额度最多一次、16路并发只放行一次、PG连接（包括worker命名空间）强制readonly、SELECT透传、DDL/DML和readonly-off在IO前拒绝、错误角色/问题/范围/历史拒绝、重复任务拒绝、错误编码输入和收费SDK/外部HTTP拒绝。副作用均使用合成替身，真实额度0。

随后真实只读预检通过：rag_audit_ro、数据库reviews、read_only=on，非superuser/createdb/createrole/replication/bypassrls，连接上限8；6项目标表有SELECT、无写权限。实际调用原生WorkerLock.acquire获得锁，会话PID `28636` 的transaction_read_only与default_transaction_read_only均为on，随后释放并关闭。生产数据库schema检查通过，固定8项模型缓存SHA与已有预检一致，没有加载或下载模型。

这些检查没有覆盖到Windows事件循环实际创建自连接的路径，因此不能据此称API已经可启动。完整启动在下一步失败，安全条件最终未满足。

## 3. 原始启动失败与原因

启动时刻 `2026-11-13T09:23:19.668362+00:00`，停止时刻 `2026-11-13T09:23:22.374556+00:00`；API启动器PID `42384`，实际API进程PID43112；React PID `38956`。API退出码1，Vite由监督进程结束，退出码1。

失败由本轮新增运行器的网络audit hook引起：它只允许父进程连接127.0.0.1:5432。Windows Python的asyncio Proactor事件循环使用socket.socketpair，标准库实现会连接本机临时回环端口；该必要通信被误拦，抛出 `RuntimeError: p7c_forbidden_outgoing_network`。Uvicorn尚未进入生产lifespan，Worker未启动、任务闸门未开启、没有执行编码/检索。

这是本轮运行器的兼容性错误。RAG业务尚未开始，其真实业务能力本轮无法判定。

原始证据保留：`api.log`完整trace、`runtime-events.jsonl`中的boundary_blocked/outgoing_network、`supervisor-state.json`、`react.log`及执行过的原始 `guard/p7c_real_guard.py`。监督进程已在首次失败时停止并清理，未执行第二次启动或提交。

## 4. 仅离线修正，未重新执行真实链路

修正候选另存 `guard-candidate/p7c_real_guard.py`，不覆盖执行过的guard。候选允许本机回环通信以支持Windows asyncio，同时继续拒绝非回环目的地；收费SDK/外部HTTP阻断、数据库身份/只读强制、一次性编码与向量SQL闸门不变，并补充对非本机PG hostaddr的拒绝。

`candidate-offline/safety-offline-tests.json`通过原有边界测试，并新增**实际Windows asyncio事件循环创建**、本机临时端口识别、非回环目的地及PG hostaddr拒绝测试。所有实际业务任务、编码和向量检索仍为0。

候选只完成离线验证，没有重新启动API/Worker/React/Chrome，也没有将其离线PASS改写成真实PASS。后续若继续，必须以这份原始失败为基线再核对安全条件；本轮记录保持BLOCKED。

## 5. 资源与完整性复核

`cleanup.json`实测：项目Python/Node进程0，8011/5184监听0；rag_audit_ro其他会话0、目标Worker锁0，诊断连接关闭，事务随连接结束。连接上限仍为8。原有PostgreSQL PID33352/端口5432、Redis PID26272/端口6379保留。

12个历史SQLite任务库摘要未变；原699项源文件/旧测试/文档和8项模型缓存摘要均未变。独立任务库0条任务，所有额度消费事件为空；pg_sql中不存在向量距离查询，embedding_start与model_rpc_start均0。没有修改生产源码、配置、权限、事实许可、Validator或旧验收报告。

## 6. 分项结论

| 分项 | 状态 | 本轮证据 |
|---|---|---|
| API/Worker集成 | BLOCKED | API事件循环初始化被本轮网络闸门拒绝；生产Worker生命周期未开始 |
| 本地编码与检索 | BLOCKED | Query Embedding、模型RPC与PG Top-5均0次 |
| 事实许可 | BLOCKED | 无本轮真实候选，未执行事实许可 |
| Validator | BLOCKED | 未创建真实业务任务，未执行本轮Validator |
| React展示 | BLOCKED | Vite短暂启动，API未就绪即停止；未启动Chrome，无本轮回答截图 |
| 资源清理 | PASS | 本轮进程、端口、PG会话/事务、Worker锁无遗留；历史完整性未变 |

## 7. Evidence → Finding → Path

| Evidence | Finding | Path |
|---|---|---|
| E-01 safety-offline-tests.json + safety-preflight.json | F-01：额度和只读权限预检通过，但不代表完整API可启动 | 合成边界验证→真实RO元数据→原生Worker锁检查→释放 |
| E-02 api.log + runtime-events.jsonl + supervisor-state.json | F-02：新增网络闸门误拦Windows本机通信，真实启动阻断 | 生产CLI→Uvicorn→asyncio→socketpair→audit拒绝→监督进程停止 |
| E-03 candidate-offline/safety-offline-tests.json | F-03：修正候选离线通过，未作真实重跑 | 允许本机回环→真实asyncio创建→额度/只读/外部拒绝回归 |
| E-04 cleanup.json + native-tasks.json | F-04：真实额度未消耗，清理和历史完整性通过 | 空任务库/零额度→进程/端口/会话/事务/锁→历史SHA复核 |

机器结果见 `acceptance.json`，全部交付摘要见 `integrity-manifest.json`。执行过的脚本副本与未执行的浏览器脚本分别标识保留。没有真实浏览器截图，不能用历史或合成截图补齐。
