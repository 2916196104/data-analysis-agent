# P6-D 多维度结果发布与前端集成验收

日期：2026-11-09。范围：已通过独立验收的 P6 结果发布、确定性回答、聊天展示、复制和 Markdown 导出。依据当前 P6-A/B/C 源码、三个阶段验收报告及 P6 实施准备报告。入场存在未提交的 P6-B/C 改动，全部保留。

## 1. 结论

**P6-D：PASS（离线工程验收）。真实供应商、真实 PostgreSQL 和生产准入未验收，未启动。**

完整结果和部分结果分别保持 `ok/succeeded`、`partial/partial`。只有全部必做维度完成时，`task_acceptance.complete=true`。证据不足、预算跳过和全局硬失败不发布主题。三维各自最多三个主题，每个公开主题有一条独立核验的真实支持引用，第三维不受旧全局列表上限影响。

服务端准备、执行、发布三个开关均默认关闭；发布开关不授予模型费用权限。真实收费 LLM、Embedding、生产数据库及部署操作均为零。本阶段结束，不自动进入真实环境验收。

## 2. 修改前行为与审计

入场 P6-C 已通过 537 项适用离线测试。`execute_p6_prepared()` 返回带摘要的 `P6ExecutedAnalysis`，内部已经完成逐维及父级验收；`run_review_agent()` 退出只读事务后执行分类，但不发布主题，内部 complete 对外仍为 unsupported，预算失败为 error。`diagnostics()` 固定 `public_release_enabled=false`，旧默认行为由 P6-C 原断言及本轮关闭发布开关的真实 Worker 测试继续验证。

实际链路为 `review_preparation → review_execution → reviews → jobs.worker → task API → task-api.ts → features/agent/ChatMessage.tsx → AgentResults / report-export`。仓库实际聊天文件位于 `features/agent`。

审计确认：旧压缩器 `build_analysis_artifacts()` 只接受完整通过的结果；事实生成器和压缩器存在跨主题的全局截断，不能直接消费父 partial。旧聊天门禁仅显示 succeeded 且无 error 的 ok/unsupported，partial 会隐藏正文与导出。Worker 接受通过 validation 的 aspect_drilldown partial，但另有顶层 `complete=false` 也映射为 partial 的规则。

本轮保留旧压缩器、事实生成器、分类器、Planner、预算管理、工具硬阻断及 Worker 状态机。新增 P6 专用公开 DTO，父 partial 不进入旧压缩器，不添加顶层 `complete=false`。新 DTO 的发布验证与完整任务验收分开：`validation=passed` 表示允许公开的维度及元数据已通过发布复验，不表示父任务完整完成。

## 3. 发布入口、信任边界与数据契约

新入口 `publish_p6_execution(executed, prepared, settings, **authorization)`：

1. 必须是服务端本次运行的精确 `P6ExecutedAnalysis`、`P6PreparedAnalysis` 类型；客户端字典、诊断 JSON、自报 complete 不可进入。
2. 必须同时开启准备、执行、发布三个服务端开关。
3. `revalidate_for_publication()` 重验执行摘要、冻结输入摘要、原目标、完整有序必做集合、准备对象绑定、身份、会话、原始排名任务、完整范围及当前排名。
4. 每份存在的子结果，包括失败维度留下的内部结果，都与对应冻结 `summary/aspect/sentiment/total/total_opinions/opinions/items` 逐字段比较；检查精确工具参数、result_id 和模型身份。
5. 复用 `validate_topic_result()` 从观点归属重算去重计数、主题和覆盖率；重新执行 `validate_p6_completion()`，必须与执行阶段保存的完整父子验收一致。
6. 只投影重新验收后的 `publishable_aspects`，其余维度保留状态和原因，主题列表为空。任何范围、来源、原文、计数或目标完整性异常抛出全局错误，不能保留成功子集。

版本化公开结构：

```text
review-p6-publication-v1
  analysis_mode = multi_aspect_negative_drilldown
  status, parent_status, complete
  scope                         完整绑定范围与标注身份
  required_aspects[]             原始冻结顺序，不缩减
  publishable_aspects[]          独立通过的维度，保持冻结顺序
  D                             当前范围成功标注评论数
  ranking_source, selection_basis_sha256
  dimensions[]
    aspect, label, rank, status, reason, reason_code
    M_a, S_a, prepared_reviews, negative_rate, review_coverage
    coverage, sampling, population_mode
    topics_total, omitted_topic_count, truncation_reason
    topics[0..3]
      topic_id, name, definition, semantic_status
      N_a_t, sample_denominator, sample_rate, opinion_count
      evidence[1]
        source_result_id, aspect, topic_id, opinion_id, sentiment
        review_id, product_id, published_at, source, dataset_version
        file_sha256, line_number, evidence_ref
        annotation_id, revision_id, opinion_source
        text, quote, quote_start, quote_end
  limitations[], answer_text, publication_sha256
```

执行/准备对象不反序列化自 API。公开历史读取通过 `/public/tasks/{id}`，要求任务库保存时的完整任务封存摘要、公开 DTO 摘要、状态、范围及确定性正文一致；读取不启动分类、不查评论库。`task-api.ts` 对 P6 任务必取此公开复验结果，失败时关闭正文，不回退使用原任务诊断 JSON。

摘要用于完整性，不是签名；沿用 P1-A 的服务端任务库信任边界，不能防御能同时重写任务、结果和摘要的数据库特权写入者。本轮未改变可信排名摘要、分母、来源认证机制，也未更改冻结 RAG 契约。

旧公开分析 v1 不承载 P6 主题；公开接口额外返回 `p6_publication`，旧字段继续存在。`read_public_answer()` 对封存有效的 P6 任务直接返回确定性模板，避免回落至旧全局事实截断。

## 4. 状态映射

| 父级业务状态 | Agent / 公开状态 | Worker | task_acceptance.complete | 公开主题 |
| --- | --- | --- | --- | --- |
| 全部必做维度 complete | ok | succeeded | true | 全部必做维度的已支持主题 |
| 至少一维 complete，其他未完成 | partial | partial | false | 仅独立通过维度 |
| 全部 insufficient_evidence | unsupported | succeeded | false | 无 |
| 全部 skipped_budget | error | failed | false | 无 |
| 无成功维度的失败组合 | error | failed | false | 无 |
| 全局身份/范围/来源/输入错误 | error | failed | false | 无公开 DTO / 无主题 |

子级沿用 complete、insufficient_evidence、failed、skipped_budget。DTO 的父级内部名称沿用 P6-A/C 的 insufficient_evidence；外层 API 不新增 unsupported 以外的证据不足状态。未新增任务库/API 状态枚举。

真实离线 Worker 路径验证首维、中间维、末维 timeout；两维完成一维证据不足；全部证据不足；全部费用预算跳过。partial 的任务错误码仍为既有 `partial_result`。聊天仅对有效 P6 DTO 放开部分正文，旧单维 partial 的安全屏障原样保留。

## 5. 统计、引用、抽样与截断

| 符号 | 来源与含义 | 可发布口径 |
| --- | --- | --- |
| D | P6-B 同快照六维统计的 successful_reviews | 当前完整绑定范围的成功标注去重评论数 |
| M_a | 同快照 negative_review_counts[aspect] | 该维全量负面去重评论数；一级负面率 M_a/D |
| S_a | 实际消费的冻结候选观点中 selected_reviews | 该维纳入分类的去重评论数；未启动为 0 |
| N_a,t | validate_topic_result 重算的 review_count | 已分析样本内该主题去重评论数；主题比例 N_a,t/S_a |

分类样本分母包含未归属观点对应的评论；没有主题不代表无问题。页面和正文另外保留观点归属覆盖 classified_opinions/selected_opinions。有限候选使用最近评论及观点顺序的有界非随机抽样；DTO 保存原 sampling 与 coverage，不对样本外推。

合成覆盖不足用例：D=31、客服 M=30、S=20，主题比例仍以 20 为分母，样本覆盖为 20/30。测试拒绝篡改计数或位置。不同维度引用同一 review_id 时分别保留各自 opinion_id、topic_id、source_result_id 和负向片段，不按 review_id 全局去重。各维评论数不能相加视为独立用户或评论数。

每维按已经验证的主题顺序取最多三个，不改变冻结维度顺序。对每个主题，从已核验归属匹配取支持片段，把相对观点位置换为原评论的 Unicode 字符位置，保留文件指纹、原始行号及标注/人工修订来源。前端使用 Unicode code point 定位，避免 JavaScript UTF-16 切片错位。

每维四个已支持候选时，公开三个、`omitted_topic_count=1`、`truncation_reason=per_aspect_top3`，页面、复制和导出均说明未展示全部候选。三维可同时公开九个主题与九条引用，无旧全局八/十条证据或十五条 finding 限额造成的维度饥饿。

确定性模板只格式化已验证名称、定义、计数、范围及引文；不生成原因推断、整改建议或全量主题频次。持续显示“模型发现并复核的候选，未经人工确认”。Markdown 中的评论和候选文本转义，避免文本变成注入的标题、链接或图片。

## 6. 修改文件与兼容性

| 文件 / 函数 | 本轮职责 |
| --- | --- |
| 新 agents/review_publication.py | publish_p6_execution、publication_text、read_p6_publication；白名单投影、确定性回答、封存读取 |
| agents/review_execution.py | P6ExecutedAnalysis.revalidate_for_publication；重新验证冻结来源与独立父子验收 |
| agents/reviews.py | run_review_agent 仅在退出数据库事务及内部执行后接入发布；异常全局关闭 |
| agents/review_answers.py | read_public_answer 的专用封存 P6 模板读取 |
| api/routes/public_analysis.py | 独立公开 DTO 读取；失败不回退至不可信保存正文 |
| core/config.py | AgentSettings.p6_publication_enabled；AGENT_P6_PUBLICATION_ENABLED，严格 0/1、默认 0 |
| 新 frontend/features/agent/p6-publication.ts | TS 契约、状态/目标/范围/计数/分母/引用运行时检查 |
| 新 P6Results.tsx / p6-results.css | 完成卡片、未完成原因、证据展开、原文高亮、抽样提示和响应式布局 |
| features/agent/ChatMessage.tsx、AgentResults.tsx | 模式限定展示入口；保留旧结果门禁 |
| types.ts、shared/api/task-api.ts | 可选 P6 字段、P6 任务验收 partial、服务器公开复验读取 |
| features/agent/report-export.ts | P6 使用与复制相同的确定性正文；无原始诊断 JSON 附录泄漏 |
| 新 tests/test_p6d_publication.py | 19 项后端专项及真实 Worker 产物生成器 |
| 新 frontend/features/agent/p6-publication.test.tsx、__fixtures__/p6-publications.json | 31 项前端测试；输入由真实离线 Worker 生成，非手写卡片 |
| 新 tests/p6d_browser_fixture.py、frontend/tests/p6d-publication.e2e.mjs | 临时库、真实 HTTP/Worker/Vite/Chrome、视口与交互验收 |

未修改 review_multi_aspect、review_preparation、review_topics、review_plan、review_acceptance、review_tools、review_compression、review_answer_facts、jobs/worker 或旧测试的入场内容。未新增依赖、数据库迁移、分类器、规划框架或前端业务 API。P0-A/B、P1-A、F01、P6-A/B/C 的既有断言全部保留。

## 7. 开关与预算边界

`AGENT_P6_PREPARATION_ENABLED=0`、`AGENT_P6_EXECUTION_ENABLED=0`、新增 `AGENT_P6_PUBLICATION_ENABLED=0` 为默认配置，未修改本地 .env 或配置真实费用授权。

发布开关只允许发布本次已经授权执行并通过复验的产物，不能跳过前两门。单独开启发布不会产生收费请求；三个开关全开但没有费用授权时，真实 Worker 仍为 skipped_budget/error/failed，模型尝试为 0。

沿用 P6-C 每维两次、整轮最多六次尝试、每请求不超过配置及 20 秒、每维 40 秒、整轮 120 秒、body 16000 字节、1200/3000 输出 token 预留与失败保守计费。发布、回答、复制、导出及历史读取均为零新增模型、Embedding 和评论数据库查询；不自动重试。没有用线程超时冒充取消，也未改变迟到结果拒绝机制。

## 8. 验证记录

| 验证 | 结果 |
| --- | --- |
| 原有适用后端 537 项 + P6-D 19 项 | 556/556 PASS；0 failures/errors/skips |
| 最后确定性原因文案调整后的 P6-D 专项 | 19/19 PASS |
| 前端全部 26 个原有文件 / 236 项 + 新 31 项 | 267/267 PASS；最终使用较低测试并发，无断言或超时上限修改 |
| TypeScript + Vite production build | PASS；保留既有大 bundle 提示 |
| 真实 API / Worker / Vite / Chromium | 7 个 P6 场景 PASS：完整、首/中/末维失败、一个证据不足、全部证据不足、全部预算跳过 |
| 1440 / 1024 / 768px | 无文档横向溢出，无 P6 子节点水平溢出；三列/两列/单列，逐主题滚动可达，第三维引用可展开 |
| 浏览器复制及实际下载 | 7 个场景均与后端 answer_text 一致；Windows 剪贴板仅归一化 CRLF/LF，下载正文逐字一致 |
| 模型/Embedding/外网检测 | paid_model_calls=0；embedding_calls=0；external_network_attempts=0；33 次固定供应商模拟请求 |
| 历史私有导入边界检查 | 原有 1 个测试方法的 13 个失败子项独立保留；0 errors/skips，未清理或放宽 |
| 入场文件摘要复核 | 旧测试与冻结评审资产无本轮改动；原 B/C 未提交工作保留 |

后端专项包括来源、范围、原文和计数故障注入；故意重新计算执行摘要仍无法绕过冻结输入及独立验收。拒绝客户端 complete、诊断和 payload 字典；拒绝跨会话/身份；真实 Worker 全局发布故障无公开成功子集。公开读取要求服务端封存，不信任单独保存文本。

开发中发现并修复：隔离浏览器夹具缺少工作台表初始化、Windows 剪贴板换行差异、滚动容器元素截图产生裁切/空白、测试文件的 TS 显式类型转换。一次同时运行构建、浏览器和高并发前端全量时，旧 AgentResults 测试超时；未改断言或测试时限，降低文件并发后全量复验通过。

可重现命令（PowerShell，项目根目录；功能测试清单延用前阶段缓存的原 537 项）：

```powershell
$env:PYTHONPATH='.;src;tests'
.\.venv\Scripts\python.exe -m unittest test_p6d_publication -v
.\.venv\Scripts\python.exe .cache/p6d/run_checks.py functional
.\.venv\Scripts\python.exe .cache/p6d/run_checks.py static
npm --prefix frontend test -- --maxWorkers=2 --minWorkers=1
npm --prefix frontend run build
node frontend/tests/p6d-publication.e2e.mjs
```

本机证据：`.cache/p6d/functional-summary.json`、`functional-tests.txt`、`new-tests.txt`、`frontend-all.txt`、`build.txt`、`static-summary.json`、`change-audit.json`；浏览器报告与全部/部分/证据不足的下载正文位于 `output/playwright/p6d/`。截图包括 `complete-{1440,1024,768}.png`、`third-dimension-{1440,1024,768}.png`、`fail_effectiveness-768.png`、`empty-768.png`。已实际查看截图，核对对齐、换行、原文高亮、状态和滚动可达性。

## 9. 未验证事项与完成边界

固定供应商响应不证明真实模型的主题语义质量。真实供应商的价格/usage 完整性、可靠取消、尾延迟、真实账单，以及 PostgreSQL 的真实事务隔离/快照性能仍未验证。离线原文来源均是临时 SQLite 导入的合成评论，不能外推为生产分析质量。

仅完成 P6-D 要求的离线结果发布与 UI 集成。三个服务端默认开关、费用 fail-closed、原分类定义与冻结 RAG/人工评审资产保持边界；没有真实收费请求、生产数据库操作或生产部署，也没有继续开展真实环境验收。
