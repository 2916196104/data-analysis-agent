# reviews_csv MVP：列权限修复后的真实启动结果

运行标识：`smoke-20261112T132444Z`，日期2026-11-12（Asia/Taipei）。**权限与数据库预检PASS，真实Worker启动FAIL：目标会话锁已被其他角色持有。按“任一失败立即停止”的要求，A1/D11均未提交。**

## 1. 实际通过的前置条件

| 检查 | 真实结果 |
|---|---|
| 连接身份 | current_user=session_user=rag_audit_ro；reviews；read_only=on |
| 连接额度 | rolconnlimit=8，本轮未改角色 |
| import_errors列权限 | id=true；raw_line=false；reason=false；table_select=false |
| 原样COUNT(*) | 成功，返回0 |
| 原生产数据库启动预检 | status=PASS，production_api_check=PASS，missing_select=[] |
| 新独立SQLite | 新路径tasks.sqlite3创建成功；未覆盖以前两个库，SHA-256均未变 |

启动前另有一条既存的rag_audit_ro / DataGrip 2026.2.4空闲连接（PostgreSQL PID2392），未擅自关闭，已计入观察范围。

证据：[运行与权限配置](../outputs/mvp-real-acceptance/smoke-20261112T132444Z/run-config.json)、[原生产预检](../outputs/mvp-real-acceptance/smoke-20261112T132444Z/production-startup-preflight.json)、[任务库创建](../outputs/mvp-real-acceptance/smoke-20261112T132444Z/task-db-created.json)。

## 2. 真实启动失败及持锁者

通过现有server serve入口启动真实API进程PID4276，lifespan调用生产TaskWorker.start()时失败：

```text
TaskConflict: 该业务库已有后台 worker；请停止旧 worker，或用 serve --no-worker 连接它。
Application startup failed. Exiting.
```

生产Worker调用pg_try_advisory_lock的SQL本身执行成功，但返回false；TaskConflict分支与跨角色持锁记录交叉确认了这一点。未产生Worker启动记录或心跳，没有开始处理任务。

| 目标锁字段 | 本轮实际值 |
|---|---|
| 数据库 | reviews |
| lock key | 8358523899158648126 |
| classid / objid / objsubid | 1946120499 / 1878447422 / 1 |
| 持锁PostgreSQL会话PID | **8768** |
| 持锁角色 | **review_agent** |
| 锁模式/状态 | ExclusiveLock / granted=true |

该PID是PostgreSQL后端会话PID，不能当作已确认的Python应用进程PID直接终止。rag_audit_ro看到的外部客户端详细字段为空，尚不能确定它是旧Worker进程还是手动获取锁的会话；本轮没有猜测进程来源、强行释放锁或终止会话。

**预检遗漏说明：** 启动前的existing_advisory_locks=0只核对rag_audit_ro自身，漏掉了其他角色持有同一个目标锁，不能等同于整个业务库的Worker锁空闲。这处核对范围不足在本轮实际启动中暴露，生产锁检查正确阻止了双Worker。后续恢复前应使用跨角色、限定数据库及目标key的只读查询，而不是再增加表权限或绕开锁。

证据：[生产启动错误日志](../outputs/mvp-real-acceptance/smoke-20261112T132444Z/server.log)、[真实SQL透传事件](../outputs/mvp-real-acceptance/smoke-20261112T132444Z/runtime-events.jsonl)、[跨角色持锁核对](../outputs/mvp-real-acceptance/smoke-20261112T132444Z/worker-lock-conflict.json)、[只读定位SQL](../outputs/mvp-real-acceptance/smoke-20261112T132444Z/check-worker-lock.sql)。

## 3. 两题业务结果

| 必需记录 | A1：商品100013588055的差评主要集中在哪些方面？ | D11：商品 XYZ-ABC123 有哪些差评？ |
|---|---|---|
| task_id / 任务状态 | 无 / NOT_SUBMITTED | 无 / NOT_SUBMITTED |
| 实际商品/数据源scope | 无业务执行scope；计划055 / reviews_csv / reviews-csv | 无业务执行scope；计划页面055对照未知ID |
| RequestGoal / 意图 | 未运行 | 未运行 |
| Planner / required_tools | 未运行 | 未运行 |
| 真实Agent工具 | 0，未执行get_top_issues | 0，未知ID拒答尚未实际验收 |
| Validator / task_acceptance | NOT_RUN / NOT_RUN | NOT_RUN / NOT_RUN |
| 最终回答 / 截图 | 无；React/浏览器未启动 | 无 |
| 模型调用 / usage / 费用 | 0；无usage响应，无新增模型API请求费用 | 0；无usage响应，无新增模型API请求费用 |
| 判定 | **BLOCKED：Worker启动失败** | **BLOCKED：A1未PASS，不得提交** |

health/options/quality本轮未作HTTP就绪验证，不能沿用上轮HTTP结果标成本轮PASS。没有提交问题、没有重试业务题，也没有执行其他验收场景。

## 4. 连接、写入与清理

连接观察按50ms间隔采样2852次，角色会话采样峰值2（含既存DataGrip与观察连接）；本轮服务成功连接的生命周期峰值1。短暂启动连接可能不落在采样点，未进入首屏并发或业务负载，因此不把采样值冒充完整MVP连接压力验证。未发生额度不足。

- 本轮所有业务连接均为rag_audit_ro只读连接；没有PostgreSQL业务、评论、标注、向量或生产任务表写入，没有GRANT/ALTER ROLE。
- 新SQLite实际仅写入schema、绑定、索引及版本；tasks行数0、worker_state行数0。该新库保留，不删除、覆盖或作为已成功任务库使用。
- 没有模型请求、RAG编码或Embedding同步；未改P0/P1/P6、Agent核心、Validator或事实许可。进程内记录只透传原生产调用，没有Mock API/工具或假结果。
- 失败的API进程已退出，观察进程正常结束；本轮创建的PostgreSQL后端连接均不再存在。8011、5184均未监听，React和专用浏览器从未启动。
- **外部会话仍保留：** review_agent的PID8768仍持有目标Worker锁；用户既存DataGrip PID2392仍在。本轮没有清除这些非本轮资源，因此不能宣称整个数据库的所有会话和锁已释放。

证据：[结束后的连接、锁、端口及旧库校验](../outputs/mvp-real-acceptance/smoke-20261112T132444Z/cleanup.json)、[连接观察摘要](../outputs/mvp-real-acceptance/smoke-20261112T132444Z/connection-observer-summary.json)、[机器可读验收表](../outputs/mvp-real-acceptance/smoke-20261112T132444Z/acceptance.json)。

## 5. 最小下一步与演示判断

当前没有证据表明还需增加数据库读取权限；唯一实际启动阻断是上述外部会话锁。请使用已有管理员或会话所有者身份只读确认PID8768对应的客户端，再从原启动终端/IDE正常停止对应Worker；若是手动取锁会话，由其所有者释放/关闭。无需给rag_audit_ro增加监控权限，不建议直接终止未知业务会话。

恢复前检查目标锁在**所有角色**中已无持有者，再使用新任务库启动、核对真实首屏三个200，仍按A1→D11各一次执行。本轮遵守失败即停止，没有自行清锁后重启。

**尚不能进入后续核心MVP验收或宣称核心业务演示通过。** 先解除外部锁占用，完成实际A1统计及D11拒答，之后才决定后续场景。
