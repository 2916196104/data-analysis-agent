# P7-C-S2-F01 · 验收闸门 UTC 契约修复

最终状态：**PASS**，仅针对本次验收运行器契约修复、离线联测与独立 Review。历史 P7-C-S2 仍为 **BLOCKED**。本轮未启动服务或真实业务，也未自动开始新的 S2。

## 根因与当前源码核验

基于当前仓库 `a83962601c28fffe0146c8fd8e4e990de303098b`，重新读取了下列实际代码，并以原始 Chrome POST 及 API 栈交叉核验；没有只依赖历史报告。

| 位置（相对仓库根） | 当前真实契约 |
| --- | --- |
| `api/routes/tasks.py:33`（`src/data_analysis_agent/` 下） | TaskInput 校验后 `model_dump()`，再 `prepare_parameters()`；35 行才进入 enqueue |
| `jobs/parameters.py:46`、`reviews/parameters.py:63` | AnalysisParameters 继承 BaselineScope / Scope；没有 timezone；Input 禁止额外字段 |
| `reviews/scope.py:25`、`:96` | review_scope 校验原生筛选并派生 `timezone=UTC`；结束日期换为次日 UTC 开区间 |
| 原始 `guard/p7c_real_guard.py:59` | 直接要求所有 SCOPE 键存在于任务参数，误将派生 timezone 当作必填输入 |
| 原始 `p7c_real_guard.enqueue` | 顶层和 selection_scope 都会进入上述检查，失败发生在入队额度/原生 enqueue 之前 |
| `frontend/src/features/agent/followup.ts:24` | copyScope 只复制原生字段，不含 timezone |
| `useAgentChat.ts:119`、`shared/api/task-api.ts:30`、`http-client.ts:26` | 原生 hook → api.submit → JSON.stringify；不会添加 timezone |

原始请求的源、版本、商品、日期和标注身份正确。新捕获的当前 React JSON 与原 Chrome POST 仅 conversation_id 不同；没有补造 timezone。把新捕获请求经过原生 API 序列化后的参数交给**未改原守卫**，纯离线仍复现 `p7c_forbidden_scope`。同一参数在新守卫中通过。

调用路径（Evidence → Finding → Path）：原生 Chrome 请求 / 当前 React 捕获 → TaskInput / AnalysisParameters → prepare_parameters → 旧 check_scope 读取缺失 timezone → scope 拒绝 → 原生 HTTP 500。新 check_scope 在该位置复用生产 review_scope 派生 UTC；生产 API 和业务路径不改。

证据：[当前 React 请求](react-serialized-post.json)、[原生 API 实际序列化参数](offline-final/contract-native-serialized.json)、[旧守卫离线复现](original-guard-reproduction/result.json)、[源文件及历史完整性](safety-integrity.json)。历史证据仍位于 `../p7c-s2-real-20261113T103342Z/`。

## 最小修复与 Diff

新候选放在仓库 `scripts/p7c_s2_guard/`，没有覆盖历史运行目录的 guard。

1. `p7c_real_guard.py:60-68`：将原生字段交给生产 review_scope 派生 UTC；继续严格比较原始商品列表、来源、数据集和起止日期；校验派生 UTC，已有 timezone 元数据若存在也必须为 UTC。
2. 原有基线日期、as_of 约束保留。enqueue 仍分别检查执行参数和 selection_scope；没有移动配额或原生入队调用。
3. `p7c_real_guard.py:16-17,70`：锁定原生请求中已有的 model / base_url / prompt_version。旧 scope 检查缺少这项固定身份约束，新增检查使三个字段的不一致明确被拒绝；不是放宽身份许可。

固定范围仍为 reviews_csv / reviews-csv、唯一商品 100013588055、2021-09-11 至 2022-08-26 UTC；基线仍为 2021-09-10 单日，as_of 必须为空，问题、首次会话、幂等键及历史引用约束保留。UTC 日期边界为 `[2021-09-11T00:00:00.000+00:00, 2022-08-27T00:00:00.000+00:00)`。

AST 对比证明 base guard 只有 `check_scope` 与新增 `IDENTITY` 常量变化；网络、只读、调用次数、Worker、模型、工具限制无其他逻辑变更。S1 / S2 两个叠加 guard 与历史原件逐字节相同。没有修改生产 Router、Planner、Tools、Validator、事实许可、模板或配置。

- [精简 guard Diff](guard-fix.diff)
- [guard 与测试完整逻辑 Diff](code.diff)
- [候选源文件](candidate/p7c_real_guard.py)、[S2 叠加闸门](candidate/s2_guard.py)、[离线复现说明](candidate/README.md)
- [新增 React 测试](frontend-changes/p7c-s2-guard-contract.test.tsx)、[原生 Chrome 夹具](frontend-changes/p7c-s2-native-post.json)

## 契约联测与回归

前端新测试直接运行现有 useAgentChat / copyScope / api.submit / request / JSON.stringify，仅替换 fetch 传输，保存当前真实序列化体。Python 将**这个捕获体**直接送入真实 ASGI 路由、TaskInput 和 prepare_parameters，再进入候选闸门与原生 SQLite enqueue。临时合成任务仅为 queued，从未执行 Worker。合法输入没有添加任何生产 API 不会产生的字段。

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 当前 React 请求序列化新联测 | 1/1 PASS | [JSON](react-contract-tests.json) |
| 原生 API / scope 契约及边界 | 37/37 PASS（21 新联测 + 16 原边界测试） | [JSON](offline-final/contract-tests.json)、[日志](offline-final/contract-tests.log) |
| 原有后端关键回归 | 134/134 PASS | [JSON](regression/f01-regression.json) |
| 原有前端 P7-C / 聊天 / 追问回归 | 83/83 PASS | [JSON](frontend-regression.json) |
| TypeScript | `tsc --noEmit` exit 0 | [日志](typescript.log) |
| 独立 Review 的纯离线检查 | 39/39 PASS | [独立 JSON](independent-review.json) |

后端回归模块为 `test_review_rag_p7c, test_review_task_contract, test_query_contract, test_review_trend_template, test_review_followup_p7b, test_review_mvp_scope, test_api_routes, test_review_answers`。本轮不把这些定向回归宣称为整个仓库全套测试通过，也不重跑历史真实验收任务。

要求覆盖结果：

- 合法原生 API 参数、顶层和 selection_scope 均不含 timezone，可通过并创建一个临时合成 queued 记录。
- 错商品、空商品、多商品、起止日变化、基线变化、as_of、错误来源/版本均拒绝。
- model、base_url、prompt_version 任一变化，或 selection_scope 的商品、日期、身份漂移均拒绝。
- 固定问题变化、历史 context_task_id 仍拒绝；UTC 派生结果被故障注入改为非 UTC 时拒绝。
- 付费 SDK / 外部 HTTP、同步、写 SQL、只读覆盖、错误 PG 地址、重复额度与非固定 Top-5 仍被离线边界测试阻断；Windows asyncio 事件循环可正常创建。
- 每个原生 API 拒绝案例断言临时 tasks 表 0 行、原生 enqueue 哨兵未触发、submission 配额 0、Worker / 模型 / 检索计数 0。

首轮新增测试的原始失败也保留：[attempt-01 日志](attempt-01/contract-tests.log)、[结果](attempt-01/contract-tests.json)。其问题是新测试未显式关闭 SQLite 句柄，导致 Windows 清理报错，以及备用离线审计先拦截导致预期错误前缀不一致；只修了测试中的 closing() 和备用审计注册顺序，未改运行器安全策略。修后在全新离线临时目录复测 37/37。没有真实业务重试，也没有改写原 P7-C-S2 失败。

## 安全边界与独立 Review

独立审查使用无主代理实现历史的独立上下文，重新检查当前生产源码、原始请求、候选与测试，并运行 39 项纯内存复核。结论：**未发现阻断问题**；UTC 根因已解决，scope 与标注身份未放宽。原始独立报告保留未改：[Markdown](independent-review.md)、[JSON](independent-review.json)。

审查指出并保留两项非阻断边界：

1. `tasks.py:33-35 → jobs/submission.py:56-58 → agents/review_mvp_scope.py:21-26` 会先进行商品目录只读回读，再到 enqueue。联测的 business_db=None 不执行该回读。因此“副作用前拒绝”指任务创建、Worker、编码、Top-5、付费和写入，**不证明先于全部只读元数据连接**。独立报告 O1 给出了纯内存复现步骤；F01 不改生产调用顺序。
2. React 新联测覆盖固定首次会话序列化，不验证 DOM 点击、CORS、历史恢复、运行环境或真实服务。源码位置、证据及复现说明见独立报告 O2。

本轮真实服务启动、Chrome POST、业务任务、PG 连接、Query Embedding、Top-5、付费调用、同步和 PostgreSQL 业务写入**全部 0**。测试中的 SQLite 任务、向量、RPC、数据库适配器均是明确标识的离线合成数据，不冒充真实 RAG 证据。

完整性：原有源码/脚本/测试/文档 790 文件、历史验收产物 365 文件、历史任务库 18 个及 .env 哈希未变；S1/S2 历史状态不变。没有服务/模型进程或 8011/5184 监听。本轮未连接真实数据库，因此没有新的数据库会话或锁需要释放。首轮离线临时 SQLite 残留文件仅作为失败证据保留，无活动句柄。

## 下一次真实 S2 准入建议

F01 的 UTC 缺陷已在离线层解除，可以作为下一次真实 S2 的候选；它不是新的真实授权，也不能沿用上次已消耗的 Chrome 提交额度。

下一次执行应另获明确授权；使用全新运行目录、SQLite、claim 和额度文件；将本次已审查 guard 三文件复制到新目录，在任何旧模块导入前核对实际 import 路径与 SHA-256，不能只向 sys.path 追加新目录而仍复用旧模块。重新核验 Windows 网络边界、rag_audit_ro / 事务只读 / Worker 锁、禁止下载与付费和调用前额度。

提交前再将当次 React 参数经过同一原生序列化联测。保持单次提交/编码/Top-5、900 秒独立观察和失败即停、零重试规则。此前浏览器观察器对 requestfailed 没有及时退出；下一执行方案还应明确该失败分支如何有界退出及清理，F01 没有修改浏览器控制流。

本阶段已停止于离线交付，没有启动新的真实验收，没有实施 P7-D、B4 或向量补全。
