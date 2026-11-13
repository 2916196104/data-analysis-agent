# P7-C-S2 单次真实 RAG 浏览器验收报告

结论：**BLOCKED**。唯一一次真实 Chrome 提交在验收运行器的 scope 闸门中失败，API 返回 HTTP 500，业务任务未创建。根因属于本轮验收运行器与原生 API 的契约不一致，不能据此判定生产 RAG 业务失败或成功。

本轮未修复后重试，未重新提交、编码或检索。S1 的历史 PASS 和此前 BLOCKED 记录均保持原样。本报告不继承历史业务 PASS。

## 固定输入与身份

- 问题：商品100013588055有没有送货慢的反馈？
- source / dataset_version：`reviews_csv` / `reviews-csv`
- 商品：`100013588055`
- 当前窗口：`2021-09-11` 至 `2022-08-26`，UTC。
- 原生表单必需基线：`2021-09-10` 至 `2021-09-10`；仅保留 API 原有参数，未执行基线分析。
- 标注 profile：27；`Pro/deepseek-ai/DeepSeek-V3.2`；`review-opinions-v1-e61de599683cba6703c2f61e85125d0be4627c2763440486aa717e4bc884036e`。它作为已有标注身份传输，未调用该付费模型。
- Git HEAD：`a83962601c28fffe0146c8fd8e4e990de303098b`。
- 独立运行目录：`p7c-s2-real-20261113T103342Z`；全新 claim 与 SQLite 任务库。

原生证据：[唯一 Chrome 请求体](chrome-submitted-body.json)、[准备阶段浏览器记录](prepare-browser.json)、[授权](authorization.json)。

## 分项判定

| 项目 | 判定 | 本轮事实 |
| --- | --- | --- |
| React / API / Worker 业务链路 | BLOCKED | Chrome POST 1 次；API 500；未入队 |
| 本地编码与 Top-5 检索 | BLOCKED | 编码 0，检索 0，模型未加载 |
| 事实许可与来源 | BLOCKED | 无候选或许可结果，不代表检索为空 |
| Validator | BLOCKED | 未执行，无原生结果 |
| 最终回答及 React 展示 | BLOCKED | 无 public_answer；页面显示提交未确认 |
| 费用与调用次数 | PASS | 所有付费、同步、PG 业务写入、业务重试均为 0 |
| 资源清理 | PASS | 锁、连接、事务、进程、端口释放；历史未变 |

## 执行前检查及其遗漏

独立 S2 闸门测试 **16/16**，原有 API / Worker 离线集成 **11/11**，前端专用测试 **5/5**。这些是合成离线测试，不是本轮真实 RAG 业务证据。真实只读预检核验了 `rag_audit_ro`、`reviews`、事务只读、无写表权限、固定地址和连接上限；8 个本地模型缓存文件 SHA-256 与既有基线一致，未加载模型、未自动下载。

S2 使用独立正业务额度，不调用 S1 零额度 install；Windows asyncio 的本机回环边界函数逐字复用已通过 S1 的版本。调用前原子闸门覆盖 HTTP 提交、任务入队、Worker、编码、Top-5、模型进程及 RPC；外部 HTTP、收费 SDK、错误 PostgreSQL 地址、写入和同步在离线测试中仍被阻断。

**预检遗漏**：闸门测试手工构造了含 `timezone: UTC` 的任务参数。原生 API 测试与闸门测试分开运行，没有联测实际 React → API 参数序列化 → 闸门这一接口，因此这些测试通过未能证明提交兼容。这是验收运行器的问题，报告保留该失败，不将测试通过等同于真实验收通过。

证据：[闸门测试](gate-preflight.log)、[API/Worker 测试](offline-integration/s2-api-worker-preflight.json)、[前端测试](offline-frontend/frontend-specialized-S2.json)、[真实只读与缓存预检](preflight.json)。

## 唯一一次真实提交与失败路径

- 实际浏览器：已安装 Google Chrome `157.0.8103.79`，headless；真实 React、API、嵌入式 Worker，未 Mock 或截获替换业务请求。
- 提交尝试时间：`2026-11-13T10:47:36.096Z`。
- 原生请求 conversation_id：`cceae975-561f-434f-afd1-4ba4272cb4e8`。
- task_id：**null / 未创建**；最终原生任务状态：**不存在**，不是某个任务的 failed 状态。
- 拒绝时间：`2026-11-13T10:47:36.364544+00:00`；操作：`scope`；异常：`p7c_forbidden_scope`。
- API 原生日志：`POST /api/v1/tasks` → HTTP **500**。
- Chrome 原生网络记录：`net::ERR_FAILED`，status `-1`；500 响应的 CORS 边界导致浏览器没有可读业务响应。
- 服务停止完成：`2026-11-13T10:47:37.122599+00:00`，提交后约 **1.027 秒**。
- 独立观察上限 900 秒，截止 ` 2026-11-13T11:02:36.096000+00:00`；没有等到上限后重试。

失败调用栈为原生 `api/routes/tasks.py:35` → 验收 `p7c_real_guard.enqueue` → `check_scope` → `deny('scope')`，在原生 enqueue 和任务额度消耗之前失败。

保留请求体经原生 `AnalysisParameters` 离线校验成功；商品、来源、日期及基线均匹配。唯一 scope 差异是原生任务参数没有 `timezone` 字段，而运行器要求该字段等于 UTC。原生纯函数 `review_scope` 从同一参数派生 `timezone: UTC`。这只解释契约差异，**没有执行本轮 RuntimeQueryContract 或 Agent**，也没有修改检查标准。

证据：[API 原生日志及栈](api.log)、[原生运行事件](runtime-events.jsonl)、[原生 Chrome POST](chrome-native-task-post.json)、[离线契约诊断](scope-contract-diagnosis.json)。

## RAG 业务证据边界

Router、Planner、`search_semantic_evidence`、本地 BGE-M3、Top-5、来源认证、事实许可、原生 Validator、模板回答均**未执行**。配置维度是 1024、目标 Top-K 是 5；本轮实际维度、模型加载时间、编码时间、候选、逐条拒绝原因、review_id、annotation_id、原文区间和 public_answer 全部记为 **null / 未执行**，不填造结果，不伪造旧式 task_acceptance。

原生任务库 `tasks` 表为 0 行。空任务库不等于空检索结果；本轮不能证明不存在送货慢反馈，也不能产生总体比例或全量评论结论。此前 055 的有限向量覆盖未补全，本轮未重新测量覆盖率。

证据：[原生任务导出](task-store-native.json)、[一致性 SQLite 快照](tasks.snapshot.sqlite3)、[未执行业务字段说明](business-evidence-not-run.json)。

## Chrome 实际展示

准备阶段截图显示正确商品和日期。失败原生帧显示固定问题及“问题尚未确认提交 / 无法连接后端”的错误状态。页面的手动重试按钮**未点击**。没有回答或引用，因此无法提供成功回答/引用截图，也不能核对 public_answer 与 React 的一致性。

![准备阶段真实 Chrome](prepare.png)

![唯一提交失败后的真实 Chrome 原生帧](execute-failure-native-frame.jpeg)

失败图为 Playwright 原生 Chrome screencast 的未编辑末帧，捕获于 `2026-11-13T10:47:44.674000+00:00`，不是重建页面。原始完整帧序列、network、console 和 trace 已保留。浏览器观察脚本没有对 requestfailed 立即退出，服务停止后仍进行了允许的状态/健康 GET 轮询；未再次 POST。保存原始 trace 后，于 `2026-11-13T10:52:39.1025169Z` 终止经身份核验的本轮专属驱动树，提交后约 **302.904 秒**，仍在 900 秒内。没有完成标准 trace zip 或脚本最终摘要；原始未完成 trace 单独归档，不冒充成功 trace。

证据：[浏览器独立观察](browser-abort-observation.json)、[原生 console](chrome-native-console.json)、[浏览器清理日志](browser-forced-cleanup.log)、[准备阶段 trace](prepare-browser-trace.zip)、[原始失败 trace](execute-aborted-raw-trace.zip)。

## 调用次数、费用与只读边界

| 调用 | 允许上限 | 实际 |
| --- | ---: | ---: |
| 真实 Chrome 业务 POST | 1 | **1** |
| 成功新建业务任务 | 1 | 0 |
| Worker Agent 业务执行 | 1 | 0 |
| Query Embedding | 1 | 0 |
| PostgreSQL Top-5 | 1 | 0 |
| 模型加载 / 进程 / RPC | 各 1 | 各 0 |
| 付费 LLM / Embedding 同步 / PG 业务写入 | 各 0 | 各 0 |
| 任务自动重试 / 重新编码 / 重新检索 | 各 0 | 各 0 |

本轮付费费用 **0**。HTTP 提交额度在进入原生 API 前消耗；scope 拒绝发生在入队之前。唯一 Chrome 提交额度已经使用，不因无 task_id 而再次提交。SQL 原生日志共有 316 条（只读元数据/范围读取和只读连接设置），向量 SQL 0 条。所有记录的业务连接为只读；真实 Worker 锁角色 `rag_audit_ro`、事务 `on`，锁 backend PID `39484`。

证据：[闸门原生计数](gate-state.json)、[SQL 原生事件](native-sql-events.jsonl)、[真实 Worker 锁身份](services-ready.json)、[连接与心跳样本](connection-samples.jsonl)。

## 资源清理与完整性

API / Worker / React 各启动 1 次，无自动重启。Worker 心跳推进 29 次并原生记录 stopped_at；API 正常关闭（exit 0），React 被监督器控制终止（exit 1）。本轮 19 个 guard 记录的 PostgreSQL 连接全部闭合，独立观察连接也已关闭；最终只读核查无残留角色会话、归属连接、Worker 锁或事务，核查连接以 IDLE 状态关闭。

本轮 Python / Node / 专属 Chrome 进程及 8011 / 5184 监听均为 0。原有 PostgreSQL 5432（PID 33352）和 Redis 6379（PID 26272）保留。原生 SQLite 主文件/WAL/SHM 已先行备份，一致性快照 `integrity_check=ok`。

清理审计初次断言错误地要求 `task_context` 也为空；实际仅含原生任务库允许的数据库绑定行 `business_path=postgresql://127.0.0.1:5432/reviews`。**原始 failed 审计 JSON 和断言日志保留不改**；另行离线 reconciliation 区分数据库绑定与业务任务，基于同一原生证据确认资源清理 PASS，没有修改任务、数据库或历史记录来改变业务结论。

完整性核对：原有源码/测试/前端/文档 706 文件、历史验收产物 112 文件、历史任务库 15 个均 SHA-256 未变；复制的 S1 网络闸门原文件未变。未修改生产代码、配置、事实许可或 Validator。

证据：[原始清理审计](cleanup-independent.json)、[原始审计断言日志](cleanup-audit.log)、[独立清理复核](cleanup-reconciliation.json)、[历史基线](before.json)、[本轮归档完整性清单](integrity-manifest.json)。

## 后续边界

本轮停止于验收运行器契约错误，业务闭环仍为 **BLOCKED**。后续需要单独修正并联测原生任务序列化与 UTC 契约绑定，再另获新的单次真实业务授权。本轮不实施修复后重试，不开展 P7-D、B4、向量补全或无关重构。
