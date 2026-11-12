# reviews_csv MVP 两题真实烟雾结果：启动就绪失败，停止提交

执行日期：2026-11-12（Asia/Taipei）。运行标识：`smoke-20261112T112628Z`。**本轮结果 BLOCKED：A1、D11 都未提交，不能计为业务 PASS 或业务任务 FAIL。**

已读取用户实际完成的 [生产启动检查 PASS 记录](../outputs/mvp-real-acceptance/startup-check-after.json)，没有重复修改 ACL。随后确实创建全新独立 SQLite 任务库、调用现有 `server serve` 启动真实 API 和内置 Worker。应用启动完成、Worker 已取得 PostgreSQL 会话锁并更新心跳；但真实 `/api/v1/health` 返回 503，触发用户要求的停止条件，未启动 React/浏览器或提交任一业务题。

## 1. 实际启动与阻断证据

| 项目 | 结果 | 本轮真实记录 |
|---|---|---|
| 原生产版本检查 | 用户已执行 PASS | 四张版本表均为1，missing_select=[]；本轮无 ACL 变更 |
| 全新任务库 | PASS | 创建前不存在；`outputs/mvp-real-acceptance/smoke-20261112T112628Z/tasks.sqlite3`；初始任务0，结束任务仍为0 |
| API进程 | 已启动 | PID 9860；Uvicorn日志明确 Application startup complete，监听127.0.0.1:8011 |
| Worker会话锁 | PASS | worker_id=`ae47729d79c34b0e855bf0273ab56188`，PostgreSQL backend_pid=21780；真实 pg_try_advisory_lock 成功 |
| Worker账号及连接 | PASS | current_user/session_user 均为rag_audit_ro，数据库reviews，default_transaction_read_only=on |
| Worker心跳 | 已更新 | started_at=2026-11-12T11:29:11.537Z，heartbeat_at更新至11:32:17.309Z（本地19:32:17） |
| 健康接口 | **FAIL** | HTTP503，error.code=database_error，message=数据库暂不可用，请稍后重试；诊断读取同样返回503 |
| 账号连接配额 | **阻断** | rag_audit_ro.rolconnlimit=1；数据库datconnlimit=-1。首条只读连接成功、同时建立第二条连接失败 |
| 原生错误 | 真实连接错误 | `致命错误: 由角色"rag_audit_ro"发起的连接太多了`；psycopg连接失败未提供SQLSTATE，不补造错误码 |
| React/浏览器 | 未启动 | 按启动失败即停止的条件，没有继续页面业务测试 |

证据链：

1. [WorkerLock.acquire()](../src/data_analysis_agent/jobs/worker.py)持有一条 PostgreSQL 连接，直到 Worker 停止才释放。
2. [health()](../src/data_analysis_agent/api/app.py)会再调用 connect_review_database() 打开独立业务连接。
3. 角色连接上限1，唯一配额已被 Worker 常驻锁占用，后续独立连接被拒绝；实际健康请求返回503。停止服务后对同账号执行两条并发只读连接，原生错误明确为连接过多。

原 `check_business_database()`是顺序打开、关闭连接的检查，未同时持有 Worker 锁，因此它的 PASS 不代表完整服务的并发读取已就绪。这与已修复的监控版本表 SELECT 缺失是两个独立问题。

## 2. 两题逐项结果

计划范围保持：reviews_csv / reviews-csv / 100013588055，UTC当前窗口2021-09-11—2022-08-26，既有V3.2标注Profile。本轮未生成业务任务的实际执行scope，不能把计划范围冒称已执行范围。

| 必需记录 | A1：商品100013588055的差评主要集中在哪些方面？ | D11：商品 XYZ-ABC123 有哪些差评？ |
|---|---|---|
| task_id / 最终任务状态 | 无 / NOT_SUBMITTED | 无 / NOT_SUBMITTED |
| 实际商品与数据源 | 无业务任务；计划055/CSV | 无业务任务；计划页面055作对照、问题目标为未知ID |
| RequestGoal、意图 | 未运行 | 未运行 |
| Planner工具计划 | 未运行，不沿用离线结果作为真实计划 | 未运行 |
| 实际工具轨迹 | 空；无get_top_issues业务调用 | 空；不能据此宣称未知ID真实拒绝已通过 |
| Validator / task_acceptance | NOT_RUN / NOT_RUN | NOT_RUN / NOT_RUN |
| 最终回答 | 无 | 无 |
| 模型调用 / usage / 费用 | 0；无供应商usage响应；本轮无新增模型API请求费用 | 0；无供应商usage响应；本轮无新增模型API请求费用 |
| 浏览器截图 | 无，尚未启动浏览器 | 无，尚未启动浏览器 |
| 验收状态 | **BLOCKED：启动就绪失败** | **BLOCKED：A1未通过，不允许执行** |

既未发送两题，也未重试失败题目；没有将预检、离线解析、Mock或服务日志冒充真实业务E2E通过。

## 3. 本轮写入、保护与清理

- 独立 SQLite 实际写入：任务库schema/绑定信息、队列索引和版本、Worker启动及心跳。任务表行数0，未写生产PostgreSQL任务表。任务库保留供复核，未覆盖或删除旧库。
- 所有生产业务连接固定rag_audit_ro；本轮进程内透传记录将Worker锁连接也保持只读。SQL与工具记录直接调用原生产实现并保留其真实返回，没有Mock API、假数据库、假工具结果或替换Validator。
- 本批预计模型和RAG路径均为0，因此进程内边界保护会在意外HTTP模型传输、RAG创建或业务写SQL执行前拒绝并记录。实际boundary_blocked事件为0；模型、Embedding、业务分析工具及真实任务执行均为0。
- PostgreSQL业务数据、评论/标注/向量、角色/ACL均未修改；未改P0/P1、P6、Oracle、事实许可或生产源码。日志只记录SQL模板和参数摘要，不记录凭据和原文参数。
- 已停止本轮服务。实际验证8011不再接受连接，本轮advisory lock剩余0。
- 终端Ctrl+C中断返回退出码1，没有完成正常shutdown日志及worker_state.stopped_at落盘，字段仍为null；不声称优雅停机。已通过端口和pg_locks独立确认服务监听关闭、会话锁释放；没有手工改写该状态来美化结果。

## 4. 最小后续处理

需要单独确认现有只读角色的**有限并发连接额度**；不是增加业务写权限或扩大SELECT范围。本轮未执行ALTER ROLE、未重启重试。

只允许两条连接可以覆盖Worker常驻锁+单个读取，但前端 [App.load()](../frontend/src/App.tsx)会并发请求options、quality、health，叠加Worker锁已有至少4条连接的正常需求。因此不能只凭单连接预检PASS放行，也不能直接把额度2视为完整React路径已满足。下一步额度需明确确认，再重新进行启动就绪验证；成功后仍按A1→D11的原顺序，每题一次。

到此停止，不启动其余业务验收，不把本轮新任务库作为下一轮可覆盖的临时库。

## 5. 本机证据

- [机器可读验收结果](../outputs/mvp-real-acceptance/smoke-20261112T112628Z/acceptance.json)
- [全新任务库创建记录](../outputs/mvp-real-acceptance/smoke-20261112T112628Z/task-db-created.json)
- [真实服务器日志](../outputs/mvp-real-acceptance/smoke-20261112T112628Z/server.log)
- [真实SQL、会话锁与工具透传事件](../outputs/mvp-real-acceptance/smoke-20261112T112628Z/runtime-events.jsonl)
- [健康接口503正文](../outputs/mvp-real-acceptance/smoke-20261112T112628Z/health-failure.json)
- [只读连接额度诊断](../outputs/mvp-real-acceptance/smoke-20261112T112628Z/startup-failure-diagnosis.json)
- [端口/锁释放及原生并发连接错误](../outputs/mvp-real-acceptance/smoke-20261112T112628Z/cleanup-and-native-error.json)
- [任务数0、Worker持久状态和调用计数](../outputs/mvp-real-acceptance/smoke-20261112T112628Z/stopped-state.json)
