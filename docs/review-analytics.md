# 评论趋势、问题对比和原文证据

四个业务工具读取现有 SQLite，返回可序列化的 JSON。无需新增依赖、升级数据库、配置前端或调用线上模型。标注、进度与工具继续共用 [范围和有效观点口径](review-query-scope.md)。

## 在命令行运行四个工具

在项目根目录 PowerShell 选择一组已有模型配置，并明确指定分析范围。示例选择配置列表第一组；有多个模型或提示词时请核对选中的身份。

```powershell
$progress = .\.venv\Scripts\python.exe -m data_analysis_agent.review_label_cli status --db data/private/reviews.sqlite3 | ConvertFrom-Json
$progress.profiles | Select-Object requested_model, base_url, prompt_version
$profile = @($progress.profiles)[0]
if ($null -eq $profile) { throw '尚无标注配置，请先完成一小批标注。' }
$analysisArgs = @(
  '--db', 'data/private/reviews.sqlite3',
  '--model', $profile.requested_model,
  '--base-url', $profile.base_url,
  '--prompt-version', $profile.prompt_version,
  '--date-from', '2016-01-01',
  '--date-to', '2016-03-31',
  '--as-of', '2016-03-31',
  '--source', 'amazon_reviews_2023',
  '--dataset-version', 'amazon-reviews-2023'
)
.\.venv\Scripts\python.exe -m data_analysis_agent.review_analysis_cli trend @analysisArgs --period month
.\.venv\Scripts\python.exe -m data_analysis_agent.review_analysis_cli top-issues @analysisArgs --baseline-from 2015-10-01 --baseline-to 2015-12-31
.\.venv\Scripts\python.exe -m data_analysis_agent.review_analysis_cli compare-products @analysisArgs
.\.venv\Scripts\python.exe -m data_analysis_agent.review_analysis_cli evidence @analysisArgs --aspect packaging --page-size 10
```

增加 `--product-id B00R1TAN7I` 可以限定单款商品；参数可重复指定多款。省略时统计所选数据来源和数据集版本下的全部商品。尚未成功标注的区间仍可查询评论量和覆盖率，负面占比会返回 `null`；工具不会自动补做标注。

安装项目后，`review-analysis` 对应同一入口；`python -m` 不需要重新注册快捷命令。

## 保存 JSON

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.review_analysis_cli trend @analysisArgs --period month --output outputs/review-analysis/trend-2016-q1.json
```

命令仍向标准输出返回完整 JSON，同时创建输出父目录和新文件。已有文件不会覆盖；重复保存时换一个文件名或省略 `--output`。数据库及其 `-wal`、`-shm`、`-journal` 辅助文件禁止作为输出路径。证据 JSON 含评论正文，应保存在被 Git 忽略的 `outputs/` 下。

## 共用参数和统计口径

| 参数 | 要求 |
| --- | --- |
| `--db` | 已存在、已初始化观点表的 SQLite 路径 |
| `--model`、`--base-url`、`--prompt-version` | 三者必填，指定唯一模型配置；不读取 `.env` 或自动选择最近模型 |
| `--date-from`、`--date-to` | 必填的 UTC 日期，严格使用 `YYYY-MM-DD`，起止日期均包含当天 |
| `--as-of` | 可选的回放截止日期或带时区的时间，和起止日期取交集 |
| `--product-id` | 可重复的商品 ID，省略时包含全部商品 |
| `--source`、`--dataset-version` | 可选的来源和导入版本；有多套数据时应明确指定 |
| `--output` | 可选的新 JSON 文件路径 |

日期、时区和模型版本校验复用原有查询范围模块。统计按当前正文指纹和明确模型配置选取成功结果；同一配置成功后产生的失败记录不会覆盖成功结果。最新人工复核快照优先，人工空观点快照保持为空。

统计单位为去重评论，不是观点条数：同一评论含多个包装负面片段，包装负面评论数仍为 1；一条评论涉及多个负面维度时，各维度都可计入，但总负面评论数仍为 1。

| 返回字段 | 口径 |
| --- | --- |
| `review_count` | 所选范围的全部唯一评论，包含空正文 |
| `eligible_text_reviews` | 有正文的评论数 |
| `successful_reviews` | 当前正文与所选配置下成功标注的评论数，成功空观点也计入 |
| `failed_reviews`、`unprocessed_reviews`、`empty_text_reviews` | 失败、尚无尝试、空正文的评论数 |
| `reviewed_reviews` | 当前成功标注已有人工复核快照的评论数 |
| `labeling_coverage` | 成功标注数 ÷ 有正文评论数；分母为 0 时为 `null` |
| `negative_review_count` | 至少一个当前有效负面观点的去重评论数 |
| `negative_rate` | 负面评论数 ÷ 成功标注数；分母为 0 时为 `null` |
| `aspects` | 六个维度各自的负面评论数量和占比，分母同为成功标注数 |

占比使用 0—1 小数，保留至六位；例如 `0.25` 表示 25%。未标注、失败和空正文不视为正面。中性、未知、成功无观点的评论属于已处理样本，但不计入负面。多维度占比之和可以超过 1。

所有工具返回规范化的 `scope`。三个统计工具还返回 `definitions`，说明去重单位、占比分母、回放字段和人工结果策略。评论覆盖率较低时，负面指标只描述已标注样本，应结合覆盖率解释。

## 查询趋势

`trend --period day` 按 UTC 日分桶，`--period month` 按 UTC 月分桶，默认月度。返回整体 `summary` 和 `buckets`。

时间桶采用请求范围与截止时间的交集，保留可见范围内的空日或空月；空桶评论数为 0，占比为 `null`。不生成截止日期之后的桶，截止早于开始日期时返回空桶列表。最多生成 10000 个时间桶，超出时需缩小区间或使用月度。

指定多款商品时趋势对所选集合汇总。每个桶的占比分母是该桶成功标注的评论数，整体分母是整个区间成功标注的评论数，因此整体占比不等于桶占比的简单平均。

## 查询主要问题与基线变化

`top-issues` 必须传入 `--baseline-from` 和 `--baseline-to`。基线结束日期须早于当前窗口开始日期，两者使用相同的商品、来源、版本、模型配置、回放截止时间及人工口径，并在同一个只读事务内查询。

返回当前窗口 `current`、基线 `baseline`、`baseline_scope` 和排序后的 `issues`。默认返回六个维度；`--top-n` 可取 1—6。按当前负面评论数降序，同数按维度代码升序，保证顺序稳定。

每个问题包含当前及基线的负面数量和占比、`negative_review_count_change` 数量差，以及 `negative_rate_change_pp` 百分点差。例如从 20% 增至 35%，百分点差为 15。任一窗口没有成功标注时，占比变化为 `null`。数量差表示观察到的数量差，不代表已确认的风险。

窗口长度可由调用方选择，工具不自动换算每日数量。长度不同、截止时间截断窗口或覆盖率不同时，应结合各窗口的 `scope`、样本量和覆盖率评估变化。工具本身提供统计；[监控入口](review-monitoring.md) 调用它完成规则告警和月度回放。

## 对比商品

`compare-products` 返回 `products`。每条按 `source＋dataset_version＋product_id` 单独分组，不将不同导入版本合成一个商品。每款商品都有与趋势相同的数量、覆盖率及六维度占比。

`no_reviews_product_ids` 返回请求中在当前范围没有可见评论的商品 ID，这不表示该商品在其他时段不存在。省略来源或版本时，趋势与主要问题会汇总选中的全部评论；商品对比仍保留来源和版本的独立条目。

## 查询证据

`evidence` 默认筛选负面观点。可以使用：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.review_analysis_cli evidence @analysisArgs --aspect quality --sentiment negative
.\.venv\Scripts\python.exe -m data_analysis_agent.review_analysis_cli evidence @analysisArgs --sentiment all --page 1 --page-size 20
```

`--aspect` 限六个维度；`--sentiment` 支持 `positive/negative/neutral/unknown/all`。维度和情感须匹配同一条观点。`--review-id` 可进一步指定一条评论。

按评论分页，`total` 是匹配评论数，`total_opinions` 是匹配观点数。每页 1—100 条，按发布时间、评论 ID 倒序。每条记录仅附带匹配筛选条件的观点，并返回：

- 评论 ID、商品、来源、数据集版本、发布时间和正文。
- 当前正文指纹、模型记录 ID、模型响应标识、人工记录 ID，以及 `opinion_source`。
- 各观点的维度、情感、原文片段、Unicode 字符起止位置。
- 输入文件路径、SHA-256、物理行号，以及 `evidence_ref`。

位置满足 `text[quote_start:quote_end] == quote`，从 0 开始，左闭右开。查询不返回用户 ID 或完整原始 JSON。无匹配观点的未标注评论不会作为证据返回；可以通过趋势或原有 `query` 查看其处理状态。

## 从后端或 Agent 调用

函数位于 `data_analysis_agent.review_analytics`：`get_trend`、`get_top_issues`、`compare_products`、`get_evidence`。命令行直接调用这些函数，后续 API 和 Agent 可复用同一入口。

公共关键字参数与 CLI 同名，Python 商品参数为 `product_ids` 列表。`get_trend` 使用 `period='day'/'month'`；`get_top_issues` 要求 `baseline_from`、`baseline_to`；`get_evidence` 可用 `sentiment=None` 表示全部情感。

直接传入数据库路径时，每次工具调用使用同一个 SQLite 只读事务，保证该次调用内的数量和证据来自一致视图。`as_of` 控制评论发布时间，模型和人工结果使用当前已有记录，数据改变后重新查询可能获得新结果。[监控入口](review-monitoring.md) 复用这些函数时共享一个已有事务，并保存本次输入和结果快照；四个独立查询命令仍不写库。

## 验证和错误处理

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_review_analytics.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试使用临时模拟库，覆盖 UTC 边界、跨月和空桶、观点去重、人工纠正与空快照、模型隔离、正文变更、未标注数据、截止时间、基线差值、证据位置、稳定分页、只读访问和导出保护。不调用线上模型，也不修改业务库。

2026-10-21 交付验证中，新增 13 项业务工具测试，连同范围查询等已有测试共 100 项全部通过。真实业务库的只读命令验证覆盖 4 款商品、2016 年第一季度 304 条评论，三个月分别为 98、98、108 条；当时该区间标注覆盖率为 0，负面占比为 `null`，证据为空。该记录仅说明命令和统计口径验证，不代表已经发现商品风险。

成功退出 0，日期、参数值、数据库或文件错误返回 JSON `status='error'` 并退出 1；命令格式或缺少必填参数由 argparse 退出 2。工具仅做统计与证据查询；运行记录、月度回放和规则告警由 [监控模块](review-monitoring.md) 提供，定时运行和模型问答仍是后续模块。
