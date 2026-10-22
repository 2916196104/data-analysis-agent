# 按商品、日期与模型版本查询评论

标注、进度和有效观点查询共用商品、UTC 日期和回放截止时间筛选。无需新增依赖或升级数据库；现有命令仍可使用。本次增加后端与命令行能力，页面保持原样。

基于同一口径，已提供趋势、问题基线变化、商品对比和证据四类业务工具，使用方法见 [评论统计与证据](review-analytics.md)。

`status`、`query` 只读，不读取 `.env` 或调用模型。`run` 使用原有配置，会将选中评论正文发送到模型端点；先查看范围进度，再自行决定批次大小。

## 查看指定范围的进度

在项目根目录 PowerShell 执行：

```powershell
$reviewScopeArgs = @(
  '--db', 'data/private/reviews.sqlite3',
  '--product-id', 'B00R1TAN7I',
  '--date-from', '2016-01-01',
  '--date-to', '2016-03-31',
  '--as-of', '2016-03-31'
)
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.labeling status @reviewScopeArgs
```

返回 `review_count`、`eligible_text_reviews`、`empty_text_reviews`、规范化后的 `scope` 和按模型配置分组的 `profiles`。同一范围的模型进度与待标注选择使用相同评论集合；`status` 的总数还包括空正文，`run` 只选择非空正文。

`status` 默认列出所选范围内有尝试记录的配置。也可同时传入 `--model`、`--base-url`、`--prompt-version` 指定一组配置；三者必须一起提供。指定配置没有记录时，返回该配置的零次尝试和范围内全部待处理数量。没有范围参数时，原有全库进度查询继续可用。

## 在同一范围增量标注

沿用上面的 `$reviewScopeArgs`：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.labeling run @reviewScopeArgs --limit 20
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.labeling status @reviewScopeArgs
```

成功结果仍按“评论 ID＋当前正文指纹＋模型 ID＋服务地址＋提示词版本”跳过。默认跳过失败项；`run @reviewScopeArgs --limit 20 --retry-failed` 会在同一范围内选择待处理和失败评论。空正文不调用模型。筛选不改变原有按评论 ID 选取批次的顺序，也不删除范围外结果。

`run` 的模型与服务地址来自原有配置，提示词版本来自程序当前规则。`run` 返回批次 `scope` 和实际使用的模型身份。无效日期在读取模型配置之前报错，范围没有可处理评论时不创建模型客户端。

## 查询某个模型版本的当前有效观点

`query` 要求明确提供 `--model`、`--base-url`、`--prompt-version`，全程只读。先从已有标注配置中选择一组，再应用同样的范围：

```powershell
$allProgress = .\.venv\Scripts\python.exe -m data_analysis_agent.cli.labeling status --db data/private/reviews.sqlite3 | ConvertFrom-Json
$allProgress.profiles | Select-Object requested_model, base_url, prompt_version
$profile = @($allProgress.profiles)[0]
if ($null -eq $profile) { throw '尚无模型标注记录，请先完成一小批标注。' }
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.labeling query @reviewScopeArgs --model $profile.requested_model --base-url $profile.base_url --prompt-version $profile.prompt_version --page-size 20
```

示例明确选择列表第一组配置；有多组时应按实际目标选择。某组配置在该时间范围内没有处理记录时，评论显示为待处理，观点为空，不混入其他配置的结果。

结果包括 `scope`、`total`、`page`、`pages`、`page_size` 和 `items`。每条评论返回正文、发布时间、处理状态、关联模型记录 ID、模型响应标识 `model_version`、正文指纹、人工记录 ID、有效观点和 `evidence_ref`。不返回用户 ID 或完整原始 JSON。

| 字段 | 含义 |
| --- | --- |
| `processing_status` | `pending`、`success`、`failed`、`empty`；成功空观点也属于成功 |
| `opinion_source` | `human` 为最新人工快照，`model` 为模型原始观点，尚无成功结果时为 `null` |
| `revision_id` | 当前采用的人工记录 ID，没有人工复核时为 `null` |
| `opinions` | 当前有效观点，包括维度、情感、原文片段和 Unicode 字符起止位置 |
| `evidence_ref` | 输入文件 SHA-256 与物理行号，格式为 `sha256:…#line=…` |

人工复核仅作用于对应模型配置、对应正文的成功标注。最新人工快照优先；人工纠正为空列表时保持为空。没有人工记录时采用模型观点。旧正文的标注和人工记录不参与当前结果，其他模型、端点或提示词的记录保持隔离。

缺少全部人工复核表的旧标注库可直接查询模型观点，不会自动建表。人工复核表只有部分存在或版本不支持时会报错，避免静默忽略已有人工数据。

## 日期与回放口径

| 参数 | 格式与行为 |
| --- | --- |
| `--product-id` | 可重复，去除首尾空白并去重；省略时包含全部商品 |
| `--date-from` | 严格的 `YYYY-MM-DD`，包含该 UTC 日的 00:00:00 |
| `--date-to` | 严格的 `YYYY-MM-DD`，包含整天，SQL 使用次日 00:00:00 之前 |
| `--as-of` | UTC 日期包含整天；带时区的 ISO 8601 时间包含该时刻，如 `2016-03-01T08:00:00+08:00` 等价于 `2016-03-01T00:00:00Z` |
| `--source` | 可选的数据来源，按完整字符串匹配 |
| `--dataset-version` | 可选的数据集版本，避免将不同导入版本一起查询 |
| `--page`、`--page-size` | 仅 `query` 使用；每页 1—100 条，按发布时间、ID 倒序稳定分页 |

起止日期与 `as_of` 同时生效，取交集。截止日期早于开始日期时返回空范围。`date_from > date_to`、不存在的日期、没有时区的时间，以及无法计算次日的 `9999-12-31` 结束日期会报错。时区时间按评论存储的毫秒精度规范化到 UTC。

回放截止时间限制的是评论 `published_at`。模型标注和人工复核采用当前已有记录，因此可以用后来产生的标注分析历史评论；本次没有增加“冻结某次人工复核快照”的功能。重复查询期间发生新人工复核时，有效观点会随最新快照变化。

模型配置身份由请求模型 ID、服务地址和提示词版本共同决定；模型响应中的 `model_version` 作为记录返回。模型服务若在同一别名后更换权重，应用无法仅靠该别名识别变化，评测时仍应固定可追溯的模型配置。

## 在 Python 后端调用

调用方可以直接使用 `pending_reviews`、`run_labeling`、`labeling_status` 和 `query_review_opinions`。新增的 `product_ids`、`date_from`、`date_to`、`as_of`、`source`、`dataset_version` 均为关键字参数；默认范围保持原有全库行为。

`query_review_opinions` 要求 `model`、`base_url`、`prompt_version`。`labeling_status` 可省略整组模型身份来分别查询全部配置，但不接受不完整的身份。返回的 `scope` 可用于核对查询是否采用相同范围。

`status` 的 `opinion_count` 继续表示模型原始观点数；新增 `effective_opinion_count` 表示应用最新人工快照后的观点数，`reviewed_reviews` 表示所选范围已人工复核的评论数。成功、失败、待处理数量都按当前正文计算，尝试次数保留范围内评论的历史记录。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_review_scope.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

范围测试使用临时模拟库和 OpenAI SDK 的模拟 HTTP 传输，覆盖 UTC 日边界、截止时刻、时区转换、空范围、增量跳过、失败重试、版本隔离、正文变更、人工空快照、原文位置、数据集版本、分页和只读访问。测试不会调用线上模型或修改业务库。
