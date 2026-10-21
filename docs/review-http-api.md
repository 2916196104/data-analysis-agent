# 运行统一 HTTP API 与后台任务

API 统一接入现有评论查询、标注进度、人工复核、四个分析工具、运行记录与告警处理。导入、标注、保存分析、月度回放通过持久化任务提交，HTTP 立即返回 `task_id`，worker 在后台串行执行。本次不新增前端，原有 CLI 继续可用。

**本次按你的安排没有新增或运行测试，也没有启动业务服务、调用模型、安装依赖或初始化业务数据库。下面步骤由你执行。** 当前虚拟环境尚未安装新增的 FastAPI、Uvicorn，须先安装项目依赖。

## 首次启动

在项目根目录执行；升级已有业务库前先在停止写入后备份。

```powershell
Set-Location E:\AllProject\data-analysis-agent
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m data_analysis_agent.review_api_cli init --db data/private/reviews.sqlite3
.\.venv\Scripts\python.exe -m data_analysis_agent.review_api_cli serve --db data/private/reviews.sqlite3 --port 8000 --import-root data/private --env-file .env
```

`init` 显式初始化评论、标注、人工复核、监控表及任务库。已有受支持的表保留原数据；各部分分别提交，如果后续一步失败，前面完成的初始化保留，可以排查后重跑。业务库的父目录必须已存在。`serve` 只检查表结构，不会自动初始化或升级。

默认位置：

| 内容 | 路径 |
| --- | --- |
| 原业务 SQLite | `data/private/reviews.sqlite3` |
| 新任务 SQLite | `data/private/reviews.tasks.sqlite3` |
| worker 互斥文件 | `data/private/reviews.sqlite3.worker.lock` |
| 模型配置 | 服务启动时选择的 `.env` 文件，进程环境变量优先 |
| 导入根目录 | `data/private/`，相对文件路径以此为基准 |

可通过每个命令的 `--task-db` 指定任务库。任务库使用自己的数据库标识，绑定原业务库的绝对路径，不能替换或覆盖业务库；迁移项目目录后，应使用新的任务库。两个库都仍然使用 SQLite，独立任务库使长事务导入期间仍可查询任务进度。

启动后可打开：

- `http://127.0.0.1:8000/docs`：自动接口文档，可填写参数并发送请求。
- `http://127.0.0.1:8000/openapi.json`：接口契约。
- `http://127.0.0.1:8000/api/v1/health`：数据库、任务计数、worker 心跳。

`/docs` 使用 FastAPI 自带文档页面，其脚本资源需要浏览器能够访问对应 CDN；`openapi.json` 和 API 本身不依赖这些资源。

## 直接在 IDEA 中运行

已新增以下配置，工作目录均为项目根目录，解释器均为项目 `.venv`：

| 配置 | 用途 |
| --- | --- |
| **69 安装项目与HTTP依赖（首次）** | 执行 `python -m pip install -e .`，需要可用的 Python 包源 |
| **70 初始化HTTP与任务库** | 执行上述显式 `init` |
| **71 启动HTTP API与后台任务** | 默认模式；一个 API 服务带一个串行 worker |
| **72 启动独立后台worker** | 可选，后台任务与 API 分开运行 |
| **73 仅启动HTTP API（独立worker模式）** | 配合 72 使用，参数含 `--no-worker` |
| **74 查询后台任务列表** | 只读查询任务状态 |
| **75 查询后台任务详情** | 运行时输入任务 ID，查看进度、参数、结果和错误 |
| **76 重试失败或部分完成任务** | 输入原任务 ID，生成关联的新任务，等待 worker 执行 |

通常执行 **69 → 70 → 71**，再通过接口提交业务任务。72、73 是另一种启动组合，不需要与 71 同时运行。71、72、73 是常驻进程；停止时使用 IDEA 停止按钮或终端 `Ctrl+C`。

## 接口与返回格式

公共前缀 `/api/v1`。普通查询成功返回 HTTP 200，人工复核保存返回 201，任务提交和重试返回 202。

```json
{"status":"ok","result":{"total":0,"page":1,"page_size":20,"items":[]}}
```

错误统一为：

```json
{"status":"error","error":{"code":"not_found","message":"任务不存在。"}}
```

| HTTP 状态 | 含义 |
| --- | --- |
| 400 | 业务参数或业务范围不合法 |
| 403 | Host 或浏览器 Origin 不允许 |
| 404 | 请求的资源不存在 |
| 409 | 幂等键冲突、人工复核版本冲突或任务状态不允许重试 |
| 422 | 请求字段类型、必填字段或取值校验失败；`error.details` 返回字段位置 |
| 500 | 未预期的服务错误 |
| 503 | 数据库忙、不可用或模型配置不完整 |

参数字段名使用下划线，例如 `date_from`、`product_ids`，与 CLI 的 `--date-from`、`--product-id` 写法不同。分页为 `page=1&page_size=20`，页大小最多 100。GET 的商品列表用重复参数：`product_ids=B00R1TAN7I&product_ids=B019GBG0IE`。省略 `product_ids` 选择所有商品。

### 商品、评论、标注与人工复核

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | 服务状态、队列计数和 worker 心跳；`worker_available` 表示 30 秒内有心跳且未标记停止 |
| GET | `/quality` | 复用原数据质量摘要 |
| GET | `/products` | 商品分页；可按 `source`、`dataset_version` 过滤 |
| GET | `/options` | 商品筛选 key、模型 `profile_id`、维度和状态选项 |
| GET | `/reviews` | 原工作台的组合筛选与分页 |
| GET | `/reviews/{review_id}` | 原文、模型版本、有效观点、模型及人工历史 |
| POST | `/reviews/{review_id}/human-reviews` | 保存人工确认或纠正，复用现有并发版本校验 |
| GET | `/labeling/status` | 按日期、截止时间、商品和可选完整模型身份查看进度 |
| GET | `/labeling/opinions` | 指定完整模型身份，分页查询有效观点 |
| GET | `/labeling/evidence/{review_id}` | 评论原文、来源及各次模型标注 |

`/reviews` 支持 `profile_id`、`product_key`、`date_from`、`date_to`、`aspect`、`sentiment`、`processing_status`、`human_status` 与分页；行为与原工作台保持一致。不指定 `profile_id` 时沿用最新模型身份，因此需要固定版本时显式传入 `/options` 返回的 ID。

人工复核的请求体字段为：`annotation_id`、`expected_revision_id`（首次为 JSON `null`）、`action`（`confirmed` 或 `corrected`）、`reviewer`、可选 `note`。纠正时另传完整 `opinions` 列表，条目为 `aspect/sentiment/quote`，可传空列表；确认时不要传观点列表。原文引用必须逐字存在于该评论，冲突返回 409。

### 四个业务工具与分析记录

| 方法 | 路径 | 复用函数 |
| --- | --- | --- |
| GET | `/analytics/trend` | `get_trend`，另传 `period=day` 或 `month` |
| GET | `/analytics/top-issues` | `get_top_issues`，另传 `baseline_from/baseline_to` 和可选 `top_n` |
| GET | `/analytics/compare-products` | `compare_products` |
| GET | `/analytics/evidence` | `get_evidence`，支持维度、情感、评论 ID 与分页 |
| GET | `/analysis-runs` | 分页查询已保存分析 |
| GET | `/analysis-runs/{run_id}` | 查看参数、冻结输入、结果与告警关联 |
| GET | `/alerts` | 告警筛选与分页 |
| GET | `/alerts/{alert_id}` | 告警证据与处理历史 |
| POST | `/alerts/{alert_id}/actions` | 请求体为 `status`（`confirmed/ignored`）、`actor`、可选 `note` |

四个统计工具均要求 `model/base_url/prompt_version/date_from/date_to`；另外支持 `product_ids/source/dataset_version/as_of`。UTC 日期、未标注数据处理、观点去重、模型隔离、人工有效结果优先等全部沿用原业务函数。

证据查询的 `sentiment` 默认 `negative`；传 `all` 查询全部情感，也支持 `positive/neutral/unknown`。

告警筛选支持 `status`（`pending/confirmed/ignored`）、`product_id`、`aspect`、`source`、`dataset_version`、`run_id` 与分页。

### 后台任务

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/tasks` | 提交 `kind` 与相应 `parameters`，返回任务和 `reused` |
| GET | `/tasks` | 按 `status/kind` 分页查询；列表不携带完整参数和结果 |
| GET | `/tasks/{task_id}` | 完整参数、进度、结果、错误、起止时间和父任务 ID |
| POST | `/tasks/{task_id}/retry` | 只允许失败或部分完成的任务，创建新的关联任务 |

提交及重试可带 HTTP 头 `Idempotency-Key`（1—200 字符非空 ASCII）。同一个键和相同参数返回原任务；键已用于不同参数时返回 409。任务响应另带 `Location` 指向任务详情。

`kind` 支持 `import`、`label`、`analysis`、`replay`，`/docs` 中可查看四种请求体。`parameters` 不允许传数据库路径、密钥、模型配置文件路径或任意未知字段，这些由服务端启动参数控制。

## 手动走通查询、提交和轮询

服务启动后，另外打开项目终端执行。以下操作由你按需要运行。

```powershell
$apiBase = 'http://127.0.0.1:8000/api/v1'
Invoke-RestMethod "$apiBase/health"
Invoke-RestMethod "$apiBase/products?page=1&page_size=20"
Invoke-RestMethod "$apiBase/labeling/status"
```

### 提交历史回放（不调用模型）

先通过 `/labeling/status` 核对模型身份；示例使用当前项目配置。数据标注不足时回放仍可成功，但结果会记录 `insufficient_data`。

```powershell
$replayRequest = @{
    kind = 'replay'
    parameters = @{
        model = 'deepseek-ai/DeepSeek-V4-Flash'
        base_url = 'https://api.siliconflow.cn/v1'
        prompt_version = 'review-opinions-v1-e61de599683cba6703c2f61e85125d0be4627c2763440486aa717e4bc884036e'
        product_ids = @('B00R1TAN7I', 'B019GBG0IE', 'B01M1OFZOG', 'B01N4885PD')
        source = 'amazon_reviews_2023'
        dataset_version = 'amazon-reviews-2023'
        month_from = '2016-01'
        month_to = '2016-03'
        baseline_months = 1
    }
} | ConvertTo-Json -Depth 10
$submission = Invoke-RestMethod "$apiBase/tasks" -Method Post -ContentType 'application/json' -Body $replayRequest
$taskId = $submission.result.task.task_id
Invoke-RestMethod "$apiBase/tasks/$taskId" | ConvertTo-Json -Depth 30
```

每隔数秒再次查询最后一条请求，查看 `status`、`progress` 和 `result`。服务不会因为轮询而再次执行任务。完成后查询业务分析记录：

```powershell
Invoke-RestMethod "$apiBase/analysis-runs" | ConvertTo-Json -Depth 10
Invoke-RestMethod "$apiBase/alerts?status=pending" | ConvertTo-Json -Depth 10
```

### 提交标注（会调用配置的模型）

仅在你准备好发送评论正文并消耗模型额度时执行：

```powershell
$labelRequest = @{
    kind = 'label'
    parameters = @{
        product_ids = @('B00R1TAN7I', 'B019GBG0IE', 'B01M1OFZOG', 'B01N4885PD')
        date_from = '2015-12-01'
        date_to = '2016-03-31'
        as_of = '2016-03-31'
        limit = 20
        retry_failed = $false
    }
} | ConvertTo-Json -Depth 10
$submission = Invoke-RestMethod "$apiBase/tasks" -Method Post -ContentType 'application/json' -Body $labelRequest
$taskId = $submission.result.task.task_id
Invoke-RestMethod "$apiBase/tasks/$taskId" | ConvertTo-Json -Depth 20
```

成功记录跳过，每个任务只处理本批次最多 20 条，不自动把范围内所有评论全部标注。模型身份在入队时记录，密钥不入库；worker 执行前重新读取服务选择的 `.env`，如果模型、服务地址或提示词身份已变化，任务失败并要求重新提交。

### 提交本机文件导入

请求体结构如下。相对路径以 `--import-root data/private` 为基准；禁止越出该目录，worker 执行前再次检查文件。

```json
{
  "kind": "import",
  "parameters": {
    "reviews_path": "amazon_reviews_2023/raw/All_Beauty.jsonl.gz",
    "meta_path": "amazon_reviews_2023/raw/meta_All_Beauty.jsonl.gz",
    "dataset_version": "amazon-reviews-2023",
    "product_ids": ["B00R1TAN7I", "B019GBG0IE", "B01M1OFZOG", "B01N4885PD"]
  }
}
```

`kind=analysis` 的参数沿用回放示例中的完整模型身份、商品和来源，把月份改成 `date_from/date_to/baseline_from/baseline_to`，可另传 `as_of`。`analysis/replay` 均可传 `rules` 对象，字段和默认值见 [规则告警说明](review-monitoring.md)。

## 状态、进度与重试

```text
queued → running → succeeded
                 → partial
                 → failed
failed / partial → 显式 retry → 新的 queued 任务
```

任务详情包含：`task_id/kind/status/parent_task_id/created_at/started_at/finished_at/updated_at/progress/parameters/result/error`。

| 类型 | 进度含义 |
| --- | --- |
| 导入 | 哈希、每约 1000 行的扫描进度、文件完整性核对；扫描期间 `total=null`，完成后使用文件数 |
| 标注 | 本批选中评论数、已处理数、成功与失败数、模型调用数 |
| 单次分析 | 一个分析窗口，执行前 0/1，保存完成后 1/1 |
| 回放 | 总月份数、已提交月份数、当前月份及最近 `run_id` |

`completed/total` 的计量单位由 `unit` 指定，不同阶段可能不同；`total=null` 时不要计算百分比。标注的失败条目也属于已处理，最终状态以 `status` 和 `result` 为准。查询 `worker_available` 和心跳可区分“正在执行”与“没有 worker”。

- `succeeded`：该批任务执行完成；不表示已经标注全部评论，也不保证存在告警。
- `partial`：导入存在坏行、标注存在失败或回放有月份未完成；保留完整业务返回结果。
- `failed`：执行失败或 worker 中断。重启 worker 时，把遗留 `running` 标为 `failed/worker_interrupted`；原 `queued` 保留并继续消费。

重试返回新 `task_id`，保留原任务和父子关系，不覆盖历史。对同一父任务已有等待或运行的重试时，复用该重试。完成后若仍需重试，应使用最新失败或部分完成任务的 ID。

```powershell
$retrySubmission = Invoke-RestMethod "$apiBase/tasks/$taskId/retry" -Method Post
$retryTaskId = $retrySubmission.result.task.task_id
Invoke-RestMethod "$apiBase/tasks/$retryTaskId" | ConvertTo-Json -Depth 20
```

标注重试自动纳入失败记录，并继续跳过成功结果，但仍受原批次 `limit` 限制；它也可能处理同范围的未标注记录。回放重试复用已提交且输入未变的月份；导入重试复用现有去重规则，但会新增导入历史。

worker 不自动重跑失败或中断任务，避免不知情地重复发起模型调用。业务写入与任务状态位于不同 SQLite 事务，因此进程恰好在业务提交后、任务状态保存前退出时，任务可能显示中断，但业务结果已存在；重试依赖现有增量、去重与分析复用规则。网络请求结果不确定时仍可能再次调用模型，这里不承诺严格的“只执行一次”。

## 独立 worker 与运行边界

默认 `serve` 同时启动 worker。需要分开管理时开两个终端：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.review_api_cli worker --db data/private/reviews.sqlite3 --import-root data/private --env-file .env
.\.venv\Scripts\python.exe -m data_analysis_agent.review_api_cli serve --db data/private/reviews.sqlite3 --port 8000 --no-worker
```

每个业务库最多一个 worker，采用操作系统文件锁；进程退出后锁自动释放，遗留的 `.worker.lock` 文件本身不表示锁仍被占用。不要手动删除运行中 worker 的锁文件。第二个 worker 启动会失败；多 API 进程应使用 `--no-worker`，这版 CLI 固定使用一个 Uvicorn 进程。

停止 worker 时先停止领取新任务，在下一个进度回调处中断当前任务；进行中的模型请求或单月分析可能需要等待结束。导入在事务内部中断会回滚本次导入；已完成的标注和已提交月份保留。强制结束进程后由下次 worker 启动恢复状态。当前未提供强制取消运行中任务的接口。

API 首版只绑定本机，暂不提供账号和公网访问。浏览器来源默认仅允许服务自身；未来独立前端接入时显式设置 `--allow-origin http://localhost:5173`，也支持重复配置，不使用通配 Origin。

服务内部的 worker 串行执行长任务。原同步 CLI 继续保留，因此不要同时通过旧 CLI 和后台任务执行同一批模型标注，以免重复调用；业务库长事务期间人工写入可能返回 503，稍后重试。状态、进度与错误查询使用独立任务库。

## 命令行任务查询

保留原来的导入、标注、统计、监控 CLI；另提供不依赖 HTTP 请求的任务查询与重试入口：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.review_api_cli tasks --db data/private/reviews.sqlite3
.\.venv\Scripts\python.exe -m data_analysis_agent.review_api_cli task --db data/private/reviews.sqlite3 --task-id $taskId
.\.venv\Scripts\python.exe -m data_analysis_agent.review_api_cli retry --db data/private/reviews.sqlite3 --task-id $taskId
```

实现使用 FastAPI 的 [应用生命周期](https://fastapi.tiangolo.com/advanced/events/) 管理内置 worker。任务记录、恢复与互斥由项目自行持久化，未使用仅附属于一次 HTTP 响应的后台函数作为任务队列。
