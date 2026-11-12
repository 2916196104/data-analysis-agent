# reviews_csv MVP：启动权限最小修复与两题烟雾清单

当前阶段只交付待审批方案和预检入口。**未执行 GRANT，未启动 API/Worker，未创建任务库，未提交烟雾题。** P0、P1 和所有生产模块均未修改。一般真实联调范围已获确认；ACL 变更与这两题实际执行仍遵守用户要求的分次确认。

## 1. 已核实的最小权限缺口

2026-11-12 19:05（Asia/Taipei）使用现有 `rag_audit_ro` 实际只读查询，事务为 `REPEATABLE READ READ ONLY`，结束后回滚、关闭。证据：[startup-permissions-before.json](../outputs/mvp-real-acceptance/startup-permissions-before.json)。

生产 [check_business_database()](../src/data_analysis_agent/api/app.py) 依次打开已校验的业务连接、检查工作台版本、检查监控版本，实际会读取：

| 版本表 | 读取入口 | 当前 SELECT | 当前版本 |
|---|---|---|---|
| public.review_database_schema | connect_review_database → check_review_schema → check_postgres_schema，namespace=business | 有 | 1 |
| public.review_labeling_schema | check_workbench_schema/check_monitoring_schema → check_labeling_schema | 有 | 1 |
| public.review_workbench_schema | check_workbench_schema | 有 | 1 |
| public.review_monitor_schema | check_monitoring_schema | **无** | 未读取，不能猜测 |

`public` 的 USAGE 已有；上述表都存在；只读角色对这四张表没有 INSERT/UPDATE/DELETE/TRUNCATE 权限，也非表所有者成员。角色不是超级用户，没有建库、建角色、复制、绕过 RLS 或 pg_write_all_data 成员权限。

前三张表的直接 SELECT 授权均不带转授权能力。`review_monitor_schema` 当前既无有效 SELECT，也无该角色直接 ACL，因此只增加它的 SELECT 即可解决已确认的权限缺口。不需要授予整个 schema、全部表、监控运行记录表或任务表的权限。该结论只说明最小权限缺口；该表版本是否为 1，要在授权后由生产检查实际验证。

## 2. 待单独审批的 SQL 与回滚

仅在数据库 `reviews`，由有权授权此表的管理员/表所有者执行。管理会话仅用于批准的 ACL 操作，Agent 运行账号仍为 `rag_audit_ro`。本轮没有切换到普通业务读写账号执行任何任务。

待审批文件：[agent_mvp_startup_select_grant.sql](../scripts/agent_mvp_startup_select_grant.sql)。

```sql
BEGIN;
GRANT SELECT ON TABLE public.review_monitor_schema TO rag_audit_ro;
COMMIT;
```

提交后回滚文件：[agent_mvp_startup_select_rollback.sql](../scripts/agent_mvp_startup_select_rollback.sql)。由同一授权者执行：

```sql
BEGIN;
REVOKE SELECT ON TABLE public.review_monitor_schema FROM rag_audit_ro RESTRICT;
COMMIT;
```

若授权事务尚未提交，直接 `ROLLBACK`。回滚依据是本次变更前无该直接 SELECT；若审批期间其他人修改过此表 ACL，先停止并核对，不撤销他人的独立授权。不使用 `CASCADE`、`ALL TABLES`、`WITH GRANT OPTION` 或写权限。

ACL 变更本身会持久修改 PostgreSQL 权限元数据，必须单独批准；不能描述为完全无数据库修改。它不修改评论、标注、向量或生产任务表内容。

## 3. ACL 实际完成后的生产启动预检

新增小入口 [check_agent_api_startup.py](../scripts/check_agent_api_startup.py)，固定使用现有只读身份和本机 pgpass，不读取普通业务账号密码，不执行任何授权 SQL。

```powershell
Set-Location E:\AllProject\data-analysis-agent
.\.venv\Scripts\python.exe -B scripts/check_agent_api_startup.py
if ($LASTEXITCODE -ne 0) { throw '生产启动预检失败，停止；不得提交业务任务。' }
```

默认模式检查当前权限、读取可读版本表，并调用**原生产 `check_business_database()`**。不关闭、不替代、不绕过原检查；不启动 API/Worker。结果写入 `outputs/mvp-real-acceptance/startup-check-after.json`，失败退出码为 1。

通过条件：`missing_select=[]`，四张版本表均为 1，`production_api_check=PASS`，`status=PASS`。任何其他缺失 SELECT、版本异常或连接失败都会写入结果并停止。报告包含只读事务身份、连接关闭状态、任务/模型/向量调用及写入计数。本入口发送的数据库语句仅为读取和会话/事务控制；不以试写后回滚来探测权限。

本次只执行了以下模式，结果为 FAIL（仅缺监控版本表 SELECT），尚未重跑变更后的生产检查：

```powershell
.\.venv\Scripts\python.exe -B scripts/check_agent_api_startup.py --permissions-only
```

预检入口的 3 项离线测试已通过：缺权限不能通过；元数据正常不能掩盖原生产检查失败；默认模式确实调用原检查且不启动 Worker/任务。这些只是预检脚本单元测试，不是业务 E2E。

生产数据库检查 PASS 只解除数据库启动门槛。真实 API/Worker 还须在最终烟雾确认后使用全新独立 SQLite 任务库、现有 `data/private` 目录，并成功取得 Worker 会话锁、启动和更新心跳；健康检查证明 Worker 可用后才提交问题。尚未创建本轮任务库或探测、抢占 Worker 锁。

后续启动沿用[准入报告](2026-11-12_mvp-real-system-readiness-report.md)中的直接 `server serve --task-db ...` 和 Vite 命令，不使用会自动启动 Redis 标注消费者的 `run.py`。只在服务进程设置 `LABEL_EXECUTION_MODE=local`，避免无关标注队列探测；不修改 `.env`、P6 或数据来源策略。

## 4. 烟雾顺序、输入确认与通过条件

两题都必须经真实 React → API → Worker → Agent，使用专用浏览器上下文，不拦截 API、不替换工具。范围为 `reviews_csv / reviews-csv`、页面选择 `100013588055`、当前 UTC 日期 `2021-09-11`—`2022-08-26`、基线 `2021-09-10` 单日、既有 V3.2 标注 profile（key `1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304`）。

**输入风险需要在最终烟雾授权时确认：** 离线纯解析发现，字面 `055的差评主要集中在哪些方面？` 被当作未知短 ID 拒绝。完整 ID 的等价问句可以绑定真实商品并生成 `get_top_issues` 计划。证据：[smoke-planning-only.json](../outputs/mvp-real-acceptance/smoke-planning-only.json)。这是离线解析结果，没有执行 API、Worker、SQL 统计或浏览器业务任务；不计任何烟雾 PASS，也没有修改 P0 的精确 ID 规则。

| 顺序 | 浏览器输入 | 执行要求 |
|---|---|---|
| 1：A1 | 建议最终确认使用：`商品100013588055的差评主要集中在哪些方面？` | 仅提交一次。实际 scope 必须绑定055和CSV；预期规则路由、自动 get_analysis_context、get_top_issues。核对分母、统计、工具 trace、Validator、最终回答和页面；任一失败立即停止，不执行D11 |
| 2：D11 | `商品 XYZ-ABC123 有哪些差评？` | A1通过后，在新会话中保留页面055并仅提交一次。预期受控拒绝未知商品，不执行 get_analysis_context/get_top_issues 等业务分析工具，query_executions=0，不回答055统计；未执行的Validator记不适用，不能伪记passed |

不静默把短 ID 改为完整 ID；若用户坚持 A1 原文字面输入，就按原文只提交一次，若被拒绝则记该统计目标 FAIL 并停止，不补交另一个版本。

每题记录真实 task_id、请求与 selection_scope、result.scope、analysis_state、request_goal、user_intent、query_plan/required_tools、实际工具轨迹、validation、task_acceptance、task status/result status、public_answer、截图。不存在或不适用的字段明确标注。D11 应为成功返回的受控拒答；真正 task.status=failed 仍记 FAIL，不改成边界 PASS。

## 5. 写入、调用、费用与敏感数据边界

- 业务 PostgreSQL 仅只读查询、事务控制和 Worker 会话级 advisory lock；不 INSERT/UPDATE/DELETE 评论、标注、向量或生产任务表。ACL 变更是单独审批的唯一权限元数据调整。
- 全新独立 SQLite 任务库需要初始化 task_context/tasks/worker_state、索引和版本信息，并保存本轮最多两条任务的入队、领取、进度、结果、错误及 Worker 启停/心跳；可能产生 WAL/SHM。不是“完全无数据库写入”。不消费旧导入、标注或重放任务。
- A1 完整 ID 候选的离线意图解析为 rules、issues、required_tools=[get_top_issues]，无意图 LLM。生产代码对此计划直接执行 SQL，一级维度计数回答采用模板；**预计本批外部模型调用为0**，仍必须记录实际调用、usage及回答策略，不能据预计值宣称零费用。D11在范围解析处拒绝，不应调用模型或业务工具。
- 建议烟雾进程使用 `LLM_MAX_RETRIES=0`、`ANSWER_MAX_RETRIES=0`，不改 `.env`、标注身份或P6；每题只有一次提交，无失败重试。保留现有单任务8步、12工具上限及超时；这些是执行约束，不是人民币费用保证。若出现意外真实模型调用、主题归纳或RAG路径，记录为偏离本批预期并停止后续场景，不自动扩大测试。
- 本批两题不需要 RAG 或查询向量编码。一般联调已允许必要本地查询编码，但本批不因此新增 RAG 题、生成评论向量、改动冻结向量或做同步。
- 密码与API key只从现有本机凭据/配置读取，不进入命令行、截图或公开报告；预检输出不含完整带凭据DSN。按预期两题不向模型提供评论原文；若实际出现模型调用，必须保留脱敏载荷与usage，明确外发了问题、范围、统计或片段中的哪些内容，不隐瞒、不将离线样例冒充线上响应。原始任务证据只保存到Git忽略的本机outputs。

## 6. 当前未解除的阻断

1. `public.review_monitor_schema` 的 SELECT 尚未单独批准、实际授予；其版本尚未读取。
2. ACL完成后的原生产检查尚未运行，API/Worker就绪尚未验证。
3. A1是否使用完整ID等价问句，以及两题实际执行，等待最终确认。

本轮只读权限检查：业务数据写入0、任务库写入0、ACL变更0、真实模型调用0、向量编码0、烟雾提交0。没有更改生产启动检查、P0/P1、Oracle、事实许可或P6，也没有执行其余13个场景。
