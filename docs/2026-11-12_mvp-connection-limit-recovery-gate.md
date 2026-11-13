# reviews_csv MVP：连接额度恢复执行门槛

2026-11-12 19:49（Asia/Taipei）只读复核。**当前仍为 CONNECTION LIMIT 1，尚未进入服务启动、并发读取或两题业务测试。** 没有执行 ALTER ROLE，也没有更改表权限或使用业务读写账号。

## 本次实际检查

证据：[connection-limit-gate-20261112T114921Z.json](../outputs/mvp-real-acceptance/connection-limit-gate-20261112T114921Z.json)。

| 项目 | 本次实际结果 |
|---|---|
| 账号、库、只读状态 | rag_audit_ro / reviews / read_only=on |
| 实际 rolconnlimit | **1**，不是计划值8 |
| 本库会话 | 检查时仅本次探针1条，其他会话0；探针已关闭 |
| 本库该角色advisory lock | 0 |
| 业务运行连接峰值 | 未测；仅观察到一条检查连接，不能冒称真实服务峰值 |
| API/Worker | 本轮未启动，未重新验证就绪 |
| React options/quality/health并发 | BLOCKED，未执行 |
| A1 | BLOCKED，NOT_SUBMITTED，无task_id/工具/Validator/回答/截图 |
| D11 | BLOCKED，NOT_SUBMITTED；A1未通过，不执行 |
| 数据库变更及调用 | 角色变更0、ACL变更0、业务数据写入0、模型/Embedding调用0 |
| 新任务库 | 未创建 |

原任务库 `outputs/mvp-real-acceptance/smoke-20261112T112628Z/tasks.sqlite3` 保留。只读校验前后SHA-256一致：`62edf2863e7a7f2778573cbc640f0773884ded641d70ce965d894847cde64183`。没有打开它执行SQL、覆盖、清理或复用；上轮Worker持久状态仍按原记录保留。

## 待用户单独批准、管理员手动实施

[临时调整SQL](../scripts/agent_mvp_connection_limit_8.sql)：

```sql
BEGIN;
ALTER ROLE rag_audit_ro CONNECTION LIMIT 8;
COMMIT;
```

这只修改本机PostgreSQL集群中该角色的连接上限，影响该角色在该集群的连接；不增加SELECT、INSERT、UPDATE、DELETE、schema或其他授权。管理员负责实施；Codex不自动执行此文件。若尚未提交，可直接ROLLBACK。

[恢复SQL](../scripts/agent_mvp_connection_limit_restore_1.sql)：

```sql
BEGIN;
ALTER ROLE rag_audit_ro CONNECTION LIMIT 1;
COMMIT;
```

恢复前应停止本轮API、Worker、React和连接观察进程；只读检查该角色在整个集群的剩余会话及advisory lock，再关闭检查连接。连接数0、锁0且无其他使用方仍需并发读取时，才可由管理员恢复1。降低上限不会主动终止已有会话，不能用执行恢复SQL替代清理。本次上限未变，**无需执行恢复SQL**。

## 管理员确认调整完成后的执行顺序

1. 只读核实实际rolconnlimit=8，复查生产启动检查。额度不符或检查FAIL就停止，不进入任务提交。
2. 新建唯一目录及全新独立SQLite任务库；文件已存在立即拒绝，不覆盖旧库或消费旧任务。
3. 使用现有server serve入口、rag_audit_ro和内置Worker；确认真实会话锁、心跳及health HTTP200。
4. 启动真实React和专用浏览器；验证实际options、quality、health并发请求均成功，并记录该角色的连接使用。峰值必须来自实际记录，区分观察连接和业务连接，注明采样精度，不把配额8称为峰值8。
5. 通过全部就绪条件后，从真实页面提交A1一次：`商品100013588055的差评主要集中在哪些方面？`。固定reviews_csv/reviews-csv、商品100013588055、UTC 2021-09-11—2022-08-26及既有Profile，保存真实任务/工具/校验/回答/页面证据；失败立即停止。
6. 仅A1 PASS后新建会话，页面保留055，提交D11一次：`商品 XYZ-ABC123 有哪些差评？`。核对未知ID受控拒绝、query_executions=0、无055业务统计结论。不得重试失败题或运行其余13题。
7. 无论通过或失败都停止本轮服务和观察连接，保存任务库、日志与截图，验证会话/锁释放，报告恢复1是否具备条件；不自动恢复角色属性。

本批仍预计规则解析、SQL工具、模板回答，模型和RAG编码预计0；若出现计划外模型调用、RAG编码或业务写入要求，停止并如实记录。独立SQLite初始化、任务进度/结果和Worker心跳是允许的实际任务库写入，不描述为完全无数据库写入。不修改P0/P1、P6、Agent核心、Oracle或事实许可。

当前门槛未满足。等待用户对临时1→8的明确批准，以及管理员实际完成变更的确认，再恢复以上顺序。
