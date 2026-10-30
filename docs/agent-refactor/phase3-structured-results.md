# 阶段 3：结构化结果、确定性压缩与公开数据边界

完成日期：2026-10-30。

本阶段在阶段 0～2 的现有编排、SQL、查询计划和校验之后增加结果适配与压缩。没有新增模型调用、依赖、数据库迁移或前端视觉改动。完整工具结果继续保存，新增字段不替换旧结果或旧回答模板。

验收使用隔离 SQLite、受控标注和模拟模型，不是美的空调的生产分析结果。公开读取增加独立白名单接口和 `--public-only` 服务模式；项目仍是本机应用，没有新增账号系统或多用户鉴权。默认旧服务保留完整任务 API，不能据此声称旧接口已脱敏。

## 1. 修改范围与调用链

已阅读阶段 0、1、2 报告并检查实际 `review_plan`、`review_tools`、`review_validation`、`reviews`、统计实现、任务存储、API、前端结果/导出和相关测试。复用原 `result_id / kind / parameters / data`，没有改动统计 SQL 或查询缓存。

| 文件 | 本阶段职责 |
| --- | --- |
| `src/data_analysis_agent/agents/review_results.py`（新增） | Pydantic 统一统计、样本、时间序列、证据模型；适配已校验工具快照。 |
| `src/data_analysis_agent/agents/review_compression.py`（新增） | 压缩参数、`AnalysisResultCompressor`、`CompactAnalysisContext`、`PublicAnalysis`、历史快照兼容读取。 |
| `src/data_analysis_agent/agents/reviews.py` | 成功校验后追加三个新字段；异常移除未确认回答；非成功结果追加空公开状态。 |
| `src/data_analysis_agent/api/routes/public_analysis.py`（新增） | 只返回公开分析的 GET 路由。 |
| `src/data_analysis_agent/api/app.py` | 注册公开路由；可选仅公开读取模式，不注册旧内部/写入路由或启动 worker。 |
| `src/data_analysis_agent/cli/server.py` | 增加可选 `serve --public-only`；默认调用方式兼容。 |
| `tests/test_review_compression.py`（新增） | 16 项独立压缩测试。 |
| `tests/test_public_analysis_api.py`（新增） | 5 项白名单、历史读取、状态及路由隔离测试。 |
| `tests/test_review_agent.py` | 原好评 SDK 调用链测试追加新产物断言。 |
| `scripts/phase3_compression_example.py`（新增） | 生成可重复的四类完整数据流水线和 UTF-8 大小测量。 |
| `output/agent-refactor/phase3-example.json`（新增） | 受控正向、短趋势、长趋势、商品比较的全部阶段快照。 |
| `docs/agent-refactor/phase3-structured-results.md`（新增） | 本报告。 |

实际链路：

```text
任务提交 / 原 worker
  → run_review_agent
  → ReviewQueryPlan
  → ReviewToolContext + 原工具注册/参数检查/缓存/预算
  → 原 SQL 聚合 / 有效观点 / 人工复核优先规则
  → results[result_id] = {result_id, kind, parameters, data}
  → validate_report（原数值、范围、语义和计划完整性检查）
  → grounded_answer（保留现有确定性模板）
  → build_analysis_artifacts
      → 按计划工具及参数选择结果
      → adapt_result → structured_analysis（统一模型，完整时序）
      → AnalysisResultCompressor → compact_context
      → public_from_context → public_analysis
  → 原任务存储（旧字段 + 可选新字段）

公开 GET → 读取冻结任务 → 检查任务/Agent/校验状态
         → 重验已有快照 → 适配/压缩/白名单 → 公开响应
```

压缩发生在完整结果已校验之后，不再查库，不触发模型，不增加工具调用。构造产物时显式传入当前 `context.results`，不依赖 `finally` 尚未组装的字段。

## 2. 统一模型和统计口径

所有模型使用现有 Pydantic，顶层拒绝额外字段并采用严格类型。完整快照仍以旧 `results` 为审计依据，统一模型是其可追溯适配视图，不要求把每个原工具专用字段重复存一份。

| 模型 | 主要字段与含义 |
| --- | --- |
| `StructuredAnalysisResult` | `result_id, kind, analysis_type, sentiment, validation_status, scope, statistics, evidence_refs, filters, warnings`。 |
| `StatisticGroup` | `group_id, scope, sample_size, metrics, aspects, trends`；商品和当前/基线窗口分组保存。 |
| `SampleSize` | 全部评论、有效正文、成功/失败/待处理标注、覆盖率、可选正向观点评论数、空正文及人工复核数。 |
| `Metric` | `name, value, unit, numerator, denominator, denominator_name`；比例使用 `fraction_0_to_1`，变化使用 `percentage_points`。 |
| `AspectStatistic` | 六维代码、中文名称、确定性排名、计数及比例指标。 |
| `TrendSeries / TrendPoint` | 维度、日/月粒度、每桶样本、计数/分母/比例；完整模型不截断时序。 |
| `EvidenceReference / EvidenceQuote` | 来源结果、评论/商品 ID、日期、哈希行号引用、模型或人工观点来源、维度/情感、原文区间及截断标记。 |

计数与比例保持阶段 1 原义：

| 指标 | 分子 | 分母 |
| --- | --- | --- |
| `positive_review_count`（样本） | 至少一个有效正向观点的去重评论数 | 计数，无比例分母。 |
| 维度 `positive_review_count` | 该维度有效正向提及的去重评论数 | 计数；同条评论可进入多个维度。 |
| 维度 `positive_rate` | 该维度正向评论数 | 至少一个正向观点的评论数。 |
| 维度 `positive_success_rate` | 该维度正向评论数 | 成功标注评论数。 |
| 汇总 `positive_success_rate` | 至少一个正向观点的评论数 | 成功标注评论数；对应原 summary 的 `positive_rate`，在统一视图中明确命名。 |
| `negative_rate` | 整体/相应维度负向去重评论数 | 成功标注评论数。 |
| `labeling_coverage` | 成功标注评论数 | 有效正文评论数；同条样本保留两个计数。 |
| `star_1`～`star_5` | 对应有效整数星级评论数 | `rated_reviews`；独立于标注。 |

分母为零的比例继续为 `null`，不会转换成 0。原 `results` 的字段名称、六维去重、负面指标、人工修订、标注身份和统计定义均未改变。适配器只读取 SQL 已给出的数字，排序按提及计数降序、维度代码升序打破并列，不引入新的细分标签。

## 3. 压缩器与实际上下文结构

入口 `build_analysis_artifacts(result, plan, options=None)` 要求 Agent `status=ok` 且 `validation.status=passed`。它按计划的工具类型和参数筛选 `result_ids`，检查 `plan.satisfied`，然后适配、压缩、构造公开视图。全程不修改传入结果。

`AnalysisResultCompressor(options).compress(question, plan, structured_results)` 接收已校验统一模型，返回 `CompactAnalysisContext`：

| 字段 | 实际类型和用途 |
| --- | --- |
| `schema_version` | 当前 `1.0`。 |
| `question / analysis_type / sentiment` | 本轮问题和计划方向。 |
| `scope_summary` | 白名单范围列表，`scope_ref` 对应来源/数据集/商品/日期/时点/时区和标注指纹。 |
| `sample_summary` | 样本列表，使用 `sample_ref` 与 `scope_ref`，重复背景只保存一次。 |
| `key_findings` | 确定性发现对象；例如维度排序或趋势方向、峰谷、最大相邻变化。 |
| `statistics` | 按来源和分组保存指标、维度、压缩时序及样本/范围引用。 |
| `evidence` | 明确请求的有限真实片段与引用，不含完整正文或文件路径。 |
| `caveats` | 结构化限制代码及对应范围/样本引用。 |
| `source_result_ids` | 全部所需原工具引用，未删除 `result_id` 追溯关系。 |

可配置默认值：`top_aspects=3`、`max_trend_points=24`、`max_evidence_items=10`、每条评论最多 2 个引文、每个引文最多 240 字符、显著相邻变化阈值 20 个百分点。证据数量还受到阶段 2 计划上限限制，压缩器只裁剪已检索结果，不会调用下一页。

`small_sample` 采用成功标注少于 20 条，`insufficient_coverage` 采用覆盖率为空或小于 80%，沿用已有回答的限制阈值。其他代码包括 `no_successful_annotations`、`no_positive_mentions`、`missing_time_buckets`、`limited_bucket_samples`、`boundary_buckets_may_be_partial` 等。这些是描述性限制，不是统计显著性检验。

## 4. 各类型压缩规则

### 正负面维度

正向只保留正向样本总数、计数、两个明确分母及默认前 3 个有提及的维度。若没有任何提及，则保留空比例/零计数的统计和限制，但不生成主要维度排名发现。负向采用相同去重口径，保留成功标注分母及当前/基线分组。不混入相反方向指标，不自动添加证据。完整六维仍在原结果及统一模型中。

### 趋势

维度趋势只包含计划指定的维度；不携带整体负面占比替代维度结论。每个保留点带原样本量、覆盖率及明确分母。摘要根据计数用 Decimal 确定性计算百分点变化，保留首尾有效点、比例峰谷、计数峰值、最大相邻变化、达到阈值的变化、缺失边界、所有转折及平台峰谷首尾。

方向分为 `up / down / flat / fluctuating / insufficient_data`，来自有效相邻变化，不是仅比较首尾。无成功标注的桶保持 `null`，不跨缺失桶计算相邻变化。`first_last_change_pp` 与相邻趋势判断分开。

`max_trend_points` 是软目标：关键波动超过目标时宁可超出并给出 `target_exceeded` 和限制代码。其余名额确定性选择均匀位置。输出包括原点数、遗漏点数；原完整时序仍在 `results` 和 `structured_analysis`，绘图应继续使用原完整时序。

### 证据

保留真实评论 ID、来源哈希行号、有效观点来源、维度/情感过滤、窗口和原文位置。按计划及参数限制条数，引文超长时只截取原文前缀，更新结束位置并设置 `truncated=true`；另给出引文截断/遗漏限制。不会把匹配样本当总体统计，也不将片段当完整评论。未请求证据时 `evidence=[]`。

### 比较、质量及星级

不同商品使用独立实际商品范围；当前与基线使用各自实际日期范围，不把分母相加。窗口变化复用已有确定性数值。相同范围、相同样本的背景用引用去重，独立指标不混算。比较无评论商品保留独立范围及空状态。质量保留总体缺正文/失败指标及样本；星级保留原有效评分分母，不伪造标注覆盖率。原质量失败分类、来源细分等专用明细继续在旧完整结果中保留。

## 5. 公开数据、诊断与兼容

新成功任务追加 `structured_analysis`、`compact_context`、`public_analysis`。原 `answer / answer_sections / results / trace / usage / agent_model_identity / result_ids / scope` 等仍按原路径保存。原 `grounded_answer` 继续服务当前 UI，本阶段不把压缩上下文交给模型。

`PublicAnalysis` 实际字段为 `schema_version, status, scope_summary, summary_metrics, highlights, statistics, evidence_refs, caveats, source_result_ids, details_available`。只从压缩白名单构造，不透传原 task、parameters 或工具 data。`details_available=true` 表示存在完整审计结果，不代表取得审计权限。

范围只公开业务来源、数据集、商品、实际日期、时点和时区。完整 `model / base_url / prompt_version` 仅保留在内部完整快照；公开视图使用稳定 `annotation_profile_ref` 指纹做标注版本对应。该指纹用于关联，不是密码学访问凭证，也不意味着无法猜测原版本。

新增 `GET /api/v1/public/tasks/{task_id}`，返回原 `Result` 信封内的 `{task_id, status, public_analysis}`，不返回旧 task 整体。外层 `status` 是原任务状态；内层是分析发布状态，消费者必须检查内层状态。比如任务 succeeded 但快照无法校验时，公开分析为 failed。

普通只读消费方可以使用 `serve --public-only`：只注册公开读取路由，不注册旧任务、原文、配置、提交、重试等路由，不启动 worker。`?view=internal` 不能切回内部视图。原 Host/Origin 限制、no-store 响应头和启动前数据库检查继续生效。该模式读取已有任务，仍需要有效业务库、任务库和 import-root，不是任务提交服务。

旧完整 API 默认保留，供受信任的本机操作端继续使用；审计也可使用原 `cli.server task` 命令和本机数据库权限。若要给不同信任级别的客户端提供服务，应将普通客户端限定到仅公开读取实例，并限制访问完整实例及数据库。本阶段没有提供网络级角色鉴权，不能把默认完整实例作为多用户安全边界。

历史任务通过 `read_public_analysis(task)` 临时转换：仅读取已保存冻结快照，不查当前业务数据、不调用模型、不回写历史任务。缺少新字段甚至缺少旧 validation 的成功任务，可重验原统计并转换。存在已保存情感意图时也重验语义。不直接信任持久化的 `public_analysis`，防止脏字段混入。未知旧结构无法可靠转换时只关闭新公开视图，原历史读取与 JSON/Markdown 导出仍可使用。

failed、partial、queued、running、存在 task.error、校验失败不发布成功统计或证据；unsupported 使用明确状态和能力不足代码，不伪造正常分析。原阶段 2 worker 的失败发布保护、工具预算和缓存均未修改。

## 6. 受控完整示例及结构变化

完整、未省略的四阶段输出见 [phase3-example.json](../../output/agent-refactor/phase3-example.json)。四个案例分别包含 `raw_tool_results → structured_analysis → compact_context → public_analysis`。复现命令（项目根目录）：

```powershell
.\.venv\Scripts\python.exe scripts/phase3_compression_example.py
```

场景 A 的问题文本为“美的空调好评主要集中在哪些方面？”，但数据库是测试夹具，实际解析范围是 P1/P2/P3，来源 `amazon_reviews_2023`，数据集 `amazon-reviews-2023`，2023-01-01～2023-03-31。不能将此输出解释为真实美的商品结论。

原 SQL 快照共 6 条评论、5 条有效正文、3 条成功、1 条失败、1 条待处理、覆盖率 0.6；至少有一个正向观点的评论 3 条。完整正向维度为使用效果 2、物流 1、价格 1、质量 1、包装 0、服务 0；同条评论可以命中多个维度，不能把维度计数相加当评论总数。

原结构通过 `results.r1.data.summary / aspects / definitions` 保存所有统计和身份。统一结构将它们变为 `statistics[0].sample_size / aspects[].metrics`。以下是统一与压缩视图中同一使用效果指标的实际子对象（占比继承 SQL 的六位小数）：

```json
{
  "aspect": "effectiveness",
  "aspect_label": "使用效果",
  "rank": 1,
  "metrics": [
    {"name": "positive_review_count", "value": 2, "unit": "reviews"},
    {"name": "positive_rate", "value": 0.666667, "unit": "fraction_0_to_1", "numerator": 2, "denominator": 3, "denominator_name": "positive_review_count"},
    {"name": "positive_success_rate", "value": 0.666667, "unit": "fraction_0_to_1", "numerator": 2, "denominator": 3, "denominator_name": "successful_reviews"}
  ]
}
```

完整统一模型的可选元数据含 null；压缩视图省略不适用的元数据，但绝不省略空比例的 `value:null`。该例两个分母恰好都为 3，字段仍分开，不能据此合并口径。

压缩结果保留前三项使用效果、物流、价格，`source_result_ids=["r1"]`，没有证据或负面指标；范围引用 `scope:17660bcc770a99cd`，样本引用 `sample:9dc3d846e3cdffd7`，标注指纹 `profile:6b27e4e29ab093c0`。`key_findings` 为这三个真实维度的有序代码，`caveats` 为 `small_sample`、`insufficient_coverage`。公开视图把样本映射到 `summary_metrics`、发现映射到 `highlights`，保留统计和来源，`status=ok`、`evidence_refs=[]`，不包含问题文本、trace、完整身份或 API 地址。

场景 B 只保留 packaging/logistics 两条时序，各自指向 r1/r2。额外 36 月示例用于测量长时序。独立受控峰值测试包含五月 90/100、其他有效月 10/100、七月无标注，确认五月峰值、七月 null 和 +80 个百分点变化不会被省略；另有重复平台峰谷测试。

场景 C 比较 P1/P2，P1 当前 3 条评论、1 条成功、覆盖率 0.5，P2 当前 2 条评论、1 条成功、覆盖率 0.5。每个商品保留不同 scope/sample 引用和自己的成功标注分母。计划另外要求的当前/基线主要问题也以独立组保留，不把商品组与汇总组混合计算。

## 7. 实际序列化大小

测量定义：对每个阶段数据调用现有 `canonical_json`，计算其 UTF-8 字节数；不是 Markdown 字数、pretty JSON 文件大小或 Token 估算。原始列包含该轮原工具 results（含 overview），统一列仅包含计划相关结果。四列不是相加后的任务总大小；因为旧结果和新视图同时保存，完整任务存储总体会增加。

| 受控场景 | 原始工具 results | 统一结果 | compact_context | public_analysis |
| --- | ---: | ---: | ---: | ---: |
| 正向维度 | 4779 | 4130 | 2847 | 2770 |
| 包装/物流 3 个月 | 5617 | 4498 | 4702 | 4644 |
| 包装/物流 36 个月 | 26671 | 29974 | 20362 | 20304 |
| 双商品及计划内窗口分析 | 7106 | 12684 | 7832 | 7771 |

短趋势、复合结果会因明确分母、范围和引用产生固定开销，复合例的压缩上下文仍大于原始工具 JSON。不能承诺所有问题都缩小，更不能把本表当生产 Token/延迟改善比例。长趋势保留关键变化优先于固定长度，后续调用方仍需处理上下文总预算。

## 8. 测试与构建结果

最终相关后端回归 **102 项通过**，不是只统计新增测试。记录：[phase3-backend-tests.log](../../output/agent-refactor/phase3-backend-tests.log)。命令在项目 `tests` 目录执行：

```powershell
..\.venv\Scripts\python.exe -m unittest test_review_compression test_public_analysis_api test_review_plan.QueryPlanTests test_review_agent.ReviewAgentTests test_review_capabilities.CapabilityTests test_review_capabilities.AgentCapabilityTests test_review_analytics test_reviews test_api_routes -q
```

| 检查 | 本次结果及证据范围 |
| --- | --- |
| 独立压缩测试 | 16 通过：正向真实排名/双分母，负向/基线，零分母，无标注，低覆盖率，多维不相加，人工覆盖，中间峰值/null，指定维度，平台峰谷，多商品独立范围，证据引用/片段预算，白名单/原结果不变，背景去重，历史转换，失败/部分完成/伪造字段及语义拒绝。 |
| 新公开 API | 5 通过：白名单、只读实例无法绕回旧路由、旧 API 全量相等、Host/Origin/缺任务、partial 不泄漏。 |
| 原计划/Agent/能力 | 38 通过（10+16+12）；包含真实 SDK + MockTransport、查询计划、同参缓存、分页、工具后模型超时、worker 失败保护，新产物接入断言。 |
| 原 analytics / reviews / API routes | 43 通过（13+24+6）。 |
| 前端 `npm test -- --maxWorkers=2` | 21 个文件、157 项通过，未修改前端源码。 |
| 前端 `npm run build` | TypeScript + Vite 生产构建通过；保留既有大 chunk 提示。 |
| `node tests/assistant.e2e.mjs` | 现有隔离 SQLite + 本地模拟模型 + API/Worker + Vite/Chromium 全链通过；76 张截图，脚本报告错误 0。 |
| `git diff --check`、CLI 帮助 | 通过；新 `--public-only` 参数可见。 |

浏览器记录：[acceptance.json](../../output/playwright/assistant-lVPKEE/acceptance.json)。覆盖 1440/1024/768/390；76 个布局记录的页面/消息整体横向溢出数为 0。检查包含基础问答、正负向统计、历史恢复、JSON/Markdown 下载、范围冻结、追问及证据下一页。此处是原 UI/导出的兼容回归，公开接口由上述 API 测试单独验收，没有声称前端已经切到新接口。

开发中的失败已修复：首次接入在 `finally` 前读取未组装的 `result.results`，旧 Agent 回归出现 8 个失败、2 个错误；改为传入当前完整上下文快照后通过。人工修订案例暴露零提及维度被补入主要排名，已改为优先只选有提及维度，全部无提及时不生成排名发现。一次命令在 tests 目录误用根目录虚拟环境相对路径，未启动测试，改正后执行上述最终回归。没有降低旧断言。

Windows TestClient/SDK 回归在允许本机套接字的执行环境完成。原阶段 0 的受限 asyncio 阻塞不再作为本轮隔离回归结果。运行中仍有 Starlette/httpx 弃用提示、Python 3.13 推荐提示和既有 jsdom 提示，不影响上述通过结果。

未运行生产 PostgreSQL/Redis 联调、真实模型语义评估、生产商品数据查询或性能压测；未因这些外部服务缺席把相关验证标成通过。对真实美的商品没有生产统计结论。

## 9. 已知限制与阶段 4 接口

1. 计划理解仍是阶段 2 的有限规则；正向趋势/正向对比和六维以外的细分仍不支持。本阶段没有扩大业务能力。
2. 压缩不是硬 Token 限额；保留关键趋势时允许超过目标点数，公开/压缩对象也可能比小型原结果更大。增加三个可选快照会增加持久化和旧 JSON 导出的体积。
3. 新公开接口在读取时重新校验和压缩，避免直接透传旧数据；超长历史快照的读取成本没有做生产压测。未知旧结构会关闭新公开视图，原审计读取保留。
4. 默认完整 API 仍服务现有本机前端；仅公开读取模式提供路由能力隔离，没有多用户权限系统，也没有迁移当前前端提交/轮询。外部消费方不能据此访问默认完整服务。
5. 证据是已有排序的匹配样本，前缀截断带明确标记；后续解释不能把部分片段推断为整条评论或总体因果结论。

阶段 4 可对接以下现成接口，不需要重跑 SQL 或重建工具框架：

| 接口 | 使用约定 |
| --- | --- |
| `build_analysis_artifacts(result, plan, options=None)` | 在既有校验通过后构造三类产物；直接使用返回的 `compact_context`。 |
| `AnalysisResultCompressor(options).compress(question, plan, structured_results)` | 输入已校验统一结果，输出 Pydantic `CompactAnalysisContext`。 |
| `CompactAnalysisContext.model_dump()` | 后续回答生成器唯一需要的统计上下文，不传完整 results、trace、usage 或原始模型身份。 |
| `public_from_context(context)` | 从同一上下文构造公开展示统计；保持来源引用与限制。 |
| `read_public_analysis(task)` | 冻结历史结果安全读取和状态保护，不触发生成。 |

后续回答生成应围绕 `source_result_ids`、分母、null 和 `caveats` 验证输出，保留既有超时/失败发布保护；需要硬上下文预算或用户级鉴权时另行设计。本阶段未启动第二次 LLM 调用，也未进入阶段 4。
