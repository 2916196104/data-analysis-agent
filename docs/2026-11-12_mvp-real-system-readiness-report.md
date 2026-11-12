# reviews_csv MVP 真实联调准入与执行清单

检查时间：2026-11-12 18:30—18:33（Asia/Taipei）。**当前状态：BLOCKED，等待真实联调授权，并存在只读账号启动权限阻断。12 个业务问题均未执行。**

本轮只完成源码入口检查、主机侧 PostgreSQL 只读环境预检和本地参数校验。没有启动 API、Worker 或浏览器业务验收，没有提交任务、调用语言模型或编码查询向量。业务库写入与任务库写入均为 0。历史 055 单题 Agent PASS 仍是历史证据，不能替代本轮 Worker/API/浏览器验收。

## 1. 当前真实环境与证据

| 项目 | 实际结果 | 证据与限制 |
|---|---|---|
| PostgreSQL 连接 | PASS | 主机侧连接 127.0.0.1:5432 / reviews，服务版本 18.6；受限执行上下文连接失败不能作为主机数据库宕机证据 |
| 现有只读账号 | rag_audit_ro 可登录 | libpq 使用本机 pgpass；报告和命令不含密码。事务为 repeatable read / read only；连接已回滚并关闭 |
| 已检查权限 | public 现有表无 INSERT/UPDATE/DELETE/TRUNCATE 权限、无表所有者成员身份 | 非超级用户，无建库、建角色、复制、绕过 RLS 权限，不属于 pg_write_all_data；不以配置里有用户名替代权限检查 |
| 必要业务读取 | products、reviews、标注、观点、人工修订、来源和向量表具 SELECT 权限 | 此次只读取 053/055 目录与首末日期；未重新审计观点、向量覆盖或实际证据质量 |
| 053/055 来源 | reviews_csv / reviews-csv | 两款真实商品目录均存在；没有切换来源 |
| 实际日期 | 两款首条均为 2021-09-11，末条均为 2022-08-26（UTC） | 后续统一使用此当前窗口；不扩大为整个数据库盘点 |
| 标注身份 | V3.2 / 当前 review-opinions prompt | 本地配置派生 profile key 为 1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304，与历史 055 记录一致；不是本次重新验证所有标注质量 |
| Agent 引擎身份 | review-agent-v9 | 与标注 prompt 是不同身份；不得用 Agent prompt 替换标注 profile |
| 本地查询编码环境 | 配置的 Python 与模型缓存路径存在 | 未加载模型、未生成查询向量；路径存在不等于本次 RAG 执行成功 |
| P6 | preparation/execution/publication 均为 false | 仅读取当前配置，不修改 |
| 普通启动方式 | 不适合此次受限验收 | run.py 使用 .env 的 review_agent，并可能启动 Redis 标注消费者；PostgreSQL 默认任务库与业务库同库 |
| API 启动检查 | **FAIL** | 真实调用生产 check_business_database() 失败；rag_audit_ro 缺少 public.review_monitor_schema 的 SELECT 权限。API 启动会无条件查询此版本表 |

当前记录：

- [环境与实际 ACL](../outputs/mvp-real-acceptance/readiness.json)
- [生产 API 启动检查](../outputs/mvp-real-acceptance/api-readiness.json)
- [计划范围及两类身份](../outputs/mvp-real-acceptance/planned-scope.json)
- [只读环境检查脚本](../outputs/mvp-real-acceptance/check_environment.py)

最小启动阻断的证据链：实际 ACL 显示 `review_monitor_schema.can_select=false` → [API 启动检查](../src/data_analysis_agent/api/app.py)调用 [check_monitoring_schema()](../src/data_analysis_agent/reviews/monitor_store.py) → 查询该表版本失败。未修改 ACL、创建角色或改用普通读写账号，也没有绕过此检查。

## 2. 授权后采用的执行范围

- 业务来源固定 `reviews_csv / reviews-csv`；统计商品只使用 `100013588053`、`100013588055`。
- 当前窗口固定 `2021-09-11` 至 `2022-08-26`（UTC）。基线 `2021-09-10` 单日仅用于满足现有 BaselineScope；本轮没有窗口对比题，不据此推断基线统计。
- 标注 profile 通过真实 `/options` 确认并由前端提交；API 冻结独立的 Agent 模型身份；Worker 再验来源及目录。
- A、C、D 独立会话；B5—B8 是同一连续会话。所有问题由真实 React 页面提交至真实 API，不拦截 API、不使用假任务或假工具结果。
- 每题串行提交一次；不自动人工重提、扩大 Top-K、补数据或变更问题来追求通过。当前 SDK 配置允许内部重试 1 次，最终回答重试 0；实际调用数和费用边界需在授权时接受，不能把一次任务说成一次模型请求。
- 可能产生真实语言模型调用：当前语义解析启用；规划、主题归纳和回答是否调用以实际路径为准。单任务配置为 max_steps=8、max_tool_calls=12，LLM 超时 30 秒、回答超时 15 秒；它们不等于统一费用上限。
- 不改业务表、标注、冻结向量、P6、Oracle、Gold Reference 或事实许可。不存在可许可证据时明确说明能力或检索限制，不证明全库不存在该事实。

### 必须单独确认的 Embedding 边界

“不生成或同步 Embedding”按严格字面执行时，C9/C10 不能运行真实向量检索：现有生产路径需要对本轮问题做临时查询向量编码。

若允许这两题真实 RAG，需要明确授权**仅使用现有本地模型，在内存中编码查询向量**；不新增评论向量、不修改数据库中的 053/055 向量、不同步、不扩大索引。本次未作此项编码。若不允许，C9/C10 保持 BLOCKED，不能替换成 Mock 或冒称 RAG PASS。

## 3. 必要的最小写入

拟使用全新独立 SQLite 任务库 `outputs/mvp-real-acceptance/tasks-20261112.sqlite3`，不使用生产 PostgreSQL 里的任务表。

| 位置 | 必要写入 | 不涉及的写入 |
|---|---|---|
| 独立 SQLite 任务库 | 初始化 task_context、tasks、worker_state 与队列索引及版本；INSERT 新任务；领取/心跳/进度/结果/错误/终止状态 UPDATE；Worker 启停及中断恢复信息 | 不写生产 tasks、业务评论、标注或向量表；不执行 import/label/replay/固定写入型分析任务 |
| SQLite 辅助文件 | WAL/SHM 或 journal | 不把这些文件描述为无数据库写入 |
| 本地文件 | 本轮日志、脱敏任务快照、截图、验收表 | 原文证据保留在本机私有输出，不公开凭据 |
| 浏览器 | 专用测试上下文的 localStorage 会话与草稿 | 不覆盖用户现有浏览器会话 |
| PostgreSQL 业务库 | SELECT、只读事务、会话配置；Worker 会话级 advisory lock | 不持久写表、不改数据/结构/权限；会话锁不是评论数据写入 |

当前实际写入仅为本地预检脚本、JSON 和本报告，尚未创建任务数据库。

## 4. 准确启动命令（待授权，且先解除权限阻断）

已核对当前 CLI 的 `serve --help`、任务库初始化函数及前端 API 配置。以下命令尚未执行。**现有 rag_audit_ro 缺少版本表 SELECT，直接启动仍会失败。需要提供已有合适权限的只读身份，或另行决定最小权限适配；本轮不自行授予权限。**

不要运行 `server.py init`：该入口还会初始化业务表，不符合本次边界。只调用 `init_tasks()` 初始化新的隔离任务库。

终端 1：初始化独立任务库，然后启动真实 API 与内置真实 TaskWorker：

```powershell
Set-Location E:\AllProject\data-analysis-agent
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$env:MVP_READONLY_DB = 'postgresql://rag_audit_ro@127.0.0.1:5432/reviews?hostaddr=127.0.0.1'
# 仅进程级关闭无关的 Redis 标注健康探测；不改 .env 或 P6。
$env:LABEL_EXECUTION_MODE = 'local'
if (Test-Path -LiteralPath 'outputs/mvp-real-acceptance/tasks-20261112.sqlite3') {
    throw '本轮任务库已存在，停止以避免消费旧任务；请先核对执行记录。'
}
@'
import os
from data_analysis_agent.jobs.store import init_tasks
print(init_tasks('outputs/mvp-real-acceptance/tasks-20261112.sqlite3', os.environ['MVP_READONLY_DB']))
'@ | .\.venv\Scripts\python.exe -B -
if ($LASTEXITCODE -ne 0) { throw '隔离任务库初始化失败，停止。' }
.\.venv\Scripts\python.exe -B -m data_analysis_agent.cli.server serve --db env:MVP_READONLY_DB --task-db outputs/mvp-real-acceptance/tasks-20261112.sqlite3 --env-file .env --import-root data/private --host 127.0.0.1 --port 8011 --allow-origin http://127.0.0.1:5184
```

终端 2：

```powershell
Set-Location E:\AllProject\data-analysis-agent\frontend
$env:VITE_API_BASE_URL = 'http://127.0.0.1:8011/api/v1'
npm run dev -- --port 5184 --strictPort
```

浏览器入口：`http://127.0.0.1:5184/assistant`，选择“评论分析”。预检时 8011/5184 未接受 TCP 连接，正式启动仍以实际绑定结果为准。不启动第二个 Worker、Redis 标注 Worker、Embedding 同步进程或旧一次性 RAG 脚本。

## 5. 场景表：本轮尚未执行

| 编号 | 页面商品 / 问题 | 会话 | 当前结果 |
|---|---|---|---|
| A1 | 055：055的差评主要集中在哪些方面？ | 独立 | BLOCKED：待授权及 API 权限解除 |
| A2 | 055：055的好评主要集中在哪些方面？ | 独立 | BLOCKED |
| A3 | 053：053的差评主要集中在哪些方面？ | 独立 | BLOCKED |
| A4 | 055：055的物流负面评价按月有什么变化？ | 独立 | BLOCKED |
| B5 | 055：055的差评主要集中在哪些方面？ | B 起始 | BLOCKED |
| B6 | 具体体现在哪里？ | 连续 B | BLOCKED；允许真实受控澄清 |
| B7 | 物流方面呢？ | 连续 B | BLOCKED |
| B8 | 那好评呢？ | 连续 B | BLOCKED |
| C9 | 055：055有没有送货慢的反馈？ | 独立 | BLOCKED：另需确认临时查询向量编码 |
| C10 | 055：055有没有客服回复慢的反馈？ | 独立 | BLOCKED：同上；事实许可不放宽 |
| D11 | 当前055：商品 XYZ-ABC123 有哪些差评？ | 独立 | BLOCKED；需真实验证不继承055 |
| D12 | 当前055：商品 B019GBG0IE 有哪些差评？ | 独立 | BLOCKED；需真实验证不跨来源、不继承055 |
| 边界13 | 055的物流好评按月有什么变化？ | 独立 | BLOCKED；预期现有能力受控不支持 |
| 边界14 | 055，2021-09-10单日空样本窗口，询问差评方面；基线改为2021-09-09 | 独立 | BLOCKED；验证真实无样本时不生成结论，不冒充全库无证据 |
| 边界15 | 工具失败处理 | 独立 | BLOCKED；需另行限定实际数据库超时等可逆会话级故障方式，不使用假工具结果；未制造故障 |

每题保存实际 task_id、请求/selection_scope、result.scope、analysis_state、request_goal（无则如实记录 null）、user_intent、query_plan/required_tools、真实工具 trace/状态、validation、task_acceptance（不适用则注明）、task status/result status/public_answer、引用核验与浏览器截图。B 链同时保留 conversation_id/context_task_id、恢复后的 State 与每轮独立快照。

任务 failed 必须记 FAIL。对明确不支持目标的成功受控拒答，可记录边界 PASS；工具故障任务仍记录 FAIL，失败后不发布假回答的保护行为另列，不将失败任务洗成业务 PASS。

## 6. 本阶段结论与确认事项

- 已确认数据库只读连接、两款商品目录、日期和本地配置；未确认本轮完整模块协同与浏览器展示。
- 真正阻断：现有只读账号无法通过 API 启动检查；尚无本轮真实任务写入/模型调用授权；RAG 查询编码与 Embedding 禁令需明确区分。
- 尚不能给出本轮真实业务演示放行结论。API 启动检查 FAIL，所有业务场景 BLOCKED；不是最终业务验收 PASS。
- 需要确认：允许独立任务库写入及必要真实模型调用的任务范围；是否允许 C9/C10 的临时查询向量编码；只读 API 启动权限采用何种明确获准的解决方式。工具故障方式单独记录，未经确认不制造故障。

到此停止，等待上述确认；不重新实施 P0/P1，不修改生产业务代码，不自动进入下一阶段。
