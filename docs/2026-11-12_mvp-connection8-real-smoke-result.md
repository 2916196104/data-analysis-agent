# reviews_csv MVP：连接额度8后的真实启动与两题烟雾结果

运行标识：`smoke-20261112T120938Z`；日期2026-11-12（Asia/Taipei）。**原连接额度阻断已解除，API/Worker就绪PASS；真实浏览器首屏FAIL，因此A1、D11均BLOCKED、未提交。** 没有把启动就绪或离线规划算成业务E2E通过。

## 1. 实际额度、原预检及服务就绪

| 检查 | 真实结果 |
|---|---|
| 当前角色 | rag_audit_ro；数据库reviews；只读状态on |
| 实际rolconnlimit | **8**；由用户此前手动修改，本轮没有ALTER ROLE或GRANT |
| 启动前残留 | 其他该角色会话0，advisory lock为0 |
| 原生产预检 | scripts/check_agent_api_startup.py：status=PASS，production_api_check=PASS，missing_select=[] |
| 新独立任务库 | 新目录下tasks.sqlite3，创建前不存在，未覆盖旧库；任务数始终0 |
| 真实API | 现有server serve入口；PID29128，127.0.0.1:8011，Application startup complete |
| 真实内置Worker | worker_id=f1e49e0597d3461f8a0d191faca8fc41；实际取得PostgreSQL advisory lock，账号及锁连接均只读 |
| 心跳 | 两次真实health读取期间，从12:13:42.408Z更新到12:13:47.423Z；worker_available=true |
| health | 真实HTTP200，连接额度1造成的上一轮503已不再出现 |

证据：[运行配置及启动前核对](../outputs/mvp-real-acceptance/smoke-20261112T120938Z/run-config.json)、[原生产预检](../outputs/mvp-real-acceptance/smoke-20261112T120938Z/production-startup-preflight.json)、[真实health/心跳/锁](../outputs/mvp-real-acceptance/smoke-20261112T120938Z/startup-health.json)。

## 2. 连接使用及真实浏览器首屏

使用真实React/Vite（127.0.0.1:5184）、专用Chrome会话和真实API；没有route拦截、假响应或模拟业务工具结果。

| 首屏请求 | 实际HTTP | 结果 |
|---|---|---|
| /api/v1/options | 200 | PASS |
| /api/v1/quality | **503** | **FAIL** |
| /api/v1/health | 200 | PASS |

页面实际出现“无法完整加载工作空间 / 数据库暂不可用，请稍后重试”。页面无JavaScript异常，但不满足用户要求的首屏全部成功。React现有初始化产生两次quality读取，均503；没有点击重新连接，没有业务问题提交或失败题重试。

首屏失败前尚未通过范围编辑器配置本轮055和日期，页面仍显示CSV默认全部商品范围。现有首屏目录/质量信息和页面摘要读取不能当作A1的055统计；本轮不存在任何已提交的Agent商品scope。

连接观察每50ms采样一次，共4088次。**采样观测峰值4，包含1条明确标记的观察连接；生产连接生命周期记录峰值3，加观察连接也是4，未观察到超过额度8。** 采样值不是声称捕获了任意短时瞬时峰值；本轮没有连接额度拒绝错误。

证据：[浏览器真实请求与响应状态](../outputs/mvp-real-acceptance/smoke-20261112T120938Z/prepare-browser.json)、[真实浏览器trace](../outputs/mvp-real-acceptance/smoke-20261112T120938Z/prepare-browser-trace.zip)、[连接观察摘要](../outputs/mvp-real-acceptance/smoke-20261112T120938Z/connection-observer-summary.json)、[生命周期及任务状态](../outputs/mvp-real-acceptance/smoke-20261112T120938Z/runtime-metrics.json)。

![真实首屏失败截图](../outputs/mvp-real-acceptance/smoke-20261112T120938Z/prepare-failure.png)

## 3. 新的明确阻断点

真实SQL记录显示，quality接口在以下语句失败：

```sql
SELECT COUNT(*) FROM import_errors;
```

驱动异常为 **InsufficientPrivilege**。实际只读权限复核确认 `rag_audit_ro` 对 `public.import_errors` 无表级SELECT、无id列SELECT，也无raw_line列SELECT。

调用链为 [GET /quality](../src/data_analysis_agent/api/routes/reviews.py) → [get_quality_summary() / _quality_summary()](../src/data_analysis_agent/reviews/store.py) → import_errors计数。原生产启动检查检查版本和schema，没有覆盖此运行时质量接口的读取依赖；版本检查PASS不能替代真实首屏验收。

这是新的读取权限阻断，**不是连接数8不足、不是Agent统计或未知ID逻辑已经失败**。本轮执行边界禁止调整其他权限，因此没有执行GRANT、没有改用读写账号，也没有跳过quality、伪造计数或修改前后端来掩盖失败。需要单独确认该计数读取的最小权限处理，不能顺带获得错误原文或写权限。

证据：[真实服务器日志](../outputs/mvp-real-acceptance/smoke-20261112T120938Z/server.log)、[透传SQL与异常记录](../outputs/mvp-real-acceptance/smoke-20261112T120938Z/runtime-events.jsonl)、[权限及最终清理核对](../outputs/mvp-real-acceptance/smoke-20261112T120938Z/cleanup.json)。

## 4. 两题实际结果

| 必需记录 | A1：商品100013588055的差评主要集中在哪些方面？ | D11：商品 XYZ-ABC123 有哪些差评？ |
|---|---|---|
| task_id / 最终任务状态 | 无 / NOT_SUBMITTED | 无 / NOT_SUBMITTED |
| 实际商品、来源 | 无业务执行scope；计划055、reviews_csv/reviews-csv | 无业务执行scope；计划保留页面055对照未知ID |
| RequestGoal、user_intent | 未运行 | 未运行 |
| Planner与required_tools | 未运行，不拿历史离线计划代替 | 未运行 |
| 真实Agent工具轨迹 | 空；未执行get_top_issues | 空；未知ID受控拒答尚未真实验收 |
| Validator / task_acceptance | NOT_RUN / NOT_RUN | NOT_RUN / NOT_RUN |
| 最终回答 | 无 | 无 |
| 模型调用 / usage / 费用 | 0；无供应商usage响应；无本批新增模型API请求费用 | 0；无供应商usage响应；无本批新增模型API请求费用 |
| 浏览器回答截图 | 无业务回答；仅有首屏失败截图 | 无 |
| 判定 | **BLOCKED：首屏quality失败，按要求停止** | **BLOCKED：A1未PASS，不允许提交** |

全新SQLite任务表真实行数为0；没有POST /tasks、没有TaskWorker._execute或Agent工具执行。前端现有范围摘要GET不算Agent业务任务。未运行其余13个场景。

## 5. 数据库写入、源码及敏感信息边界

- PostgreSQL业务连接均使用rag_audit_ro，进程内透传保护将Worker锁连接也保持readonly=true。记录的业务SQL只读，业务写SQL、RAG和外部模型传输越界事件均为0。SQL保护和日志不提供假返回值；API、Worker及SQL结果都来自生产实现和真实数据库。
- PostgreSQL业务数据、评论、标注、向量、生产任务表、ACL和角色属性均未修改；没有Embedding编码/同步、模型请求或P6执行，也没有修改P0/P1/P6、核心逻辑或事实许可。
- 独立SQLite实际写入了schema、业务库绑定、索引/版本、Worker启动、心跳与正常停止状态；任务行数0。它不是完全无数据库写入。
- 原任务库SHA-256仍为62edf2863e7a7f2778573cbc640f0773884ded641d70ce965d894847cde64183，校验未变；所有旧运行记录保留，新库也保留。
- 任务证据、网络trace与截图保存在Git忽略的本机outputs；未输出密钥、密码或带凭据DSN。模型没有实际请求，因此没有评论或统计发往模型供应商。

## 6. 清理与恢复连接限制

本轮API正常完成Application shutdown、Worker停止并写入stopped_at=2026-11-12T12:16:05.705Z，服务器退出码0；连接观察进程正常退出。React/Vite与专用浏览器已关闭。

最终只读复核：8011和5184均不再接受连接；该角色在整个集群没有其他会话，advisory lock为0；检查连接随后关闭。生命周期记录也没有未关闭的服务连接。

**从本轮资源清理角度，满足由管理员恢复CONNECTION LIMIT=1的条件。** 当前实际额度仍为8；本轮未自动恢复。恢复1会再次限制现有API/Worker并发读取，若近期继续本机联调，需要保留或重新确认适当额度，不得误认为恢复1后同一真实服务链路仍可运行。

## 7. 是否具备进入完整MVP验收的条件

**尚不具备。** 连接额度问题已解除，API/Worker启动与健康检查通过；但首屏quality读取权限仍阻断，两道真实业务题都未执行，Router/Planner/Tools/Validator/最终回答的本轮业务协同尚无真实证据。

本轮到此停止，不扩大权限、不修Agent核心、不补交题目、不自动开展其他阶段。

机器可读总表：[acceptance.json](../outputs/mvp-real-acceptance/smoke-20261112T120938Z/acceptance.json)。
