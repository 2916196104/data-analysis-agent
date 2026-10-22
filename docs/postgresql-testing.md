# 用 PostgreSQL 验证和使用整个项目

先完成只读检查，再按需要做标注、分析和人工复核。所有 PowerShell 命令均在项目根目录 `E:\AllProject\data-analysis-agent` 执行；数据库连接从 `.env` 的 `DATABASE_URL` 读取。

**本机已经完成迁移，不需要再执行初始化或迁移。以下写操作由你按顺序运行；本指南编写时只检查了数据库和服务，没有执行新标注、导入或人工复核。**

第 5—8 步会保存业务记录，执行前建议先按文末的[备份步骤](#写操作前备份当前-postgresql)导出当前数据库。

## 1. 确认本次测试的起点

2026-10-22 本次只读核对结果如下。后续运行会改变标注、分析和任务数量，不要求一直保持这些数字。

| 内容 | 当前数量 |
| --- | ---: |
| 商品 | 4 |
| 评论 | 4,941 |
| 导入历史 | 14 |
| 模型标注尝试 | 576 |
| 模型观点 | 1,103 |
| 分析快照 | 9 |
| 后台任务 | 8，全部成功 |
| 人工复核 / 告警 | 0 / 0 |

当前 `.env` 的模型是 `Pro/deepseek-ai/DeepSeek-V3.2`，该版本已有 534 条成功标注、7 条失败、4,398 条有正文评论尚未处理。另有 V4-Flash 的历史标注，两个版本独立统计。

**IDEA 的 11 读取 `.env` 调用当前模型；13、15、30—34、41—42 的参数中仍预设 V4-Flash。** 使用这些编号配置前，要将它们的 `--model` 改为本次测试的模型，并保持 `--base-url`、`--prompt-version` 及日期范围一致。也可以直接使用下面根据当前标注版本生成的命令参数。这里没有自动更改已有模型配置。

## 2. 在 DataGrip 中确认连接的就是 PostgreSQL

使用本机已有的数据源：主机 `127.0.0.1`、端口 `5432`、数据库 `reviews`、用户 `review_agent`，密码填写你自己设置的原始密码。

打开查询控制台，执行：

```sql
SELECT current_database(), current_user,
       inet_server_addr(), inet_server_port();

SELECT
    (SELECT COUNT(*) FROM public.products) AS products,
    (SELECT COUNT(*) FROM public.reviews) AS reviews,
    (SELECT COUNT(*) FROM public.review_annotations) AS annotations,
    (SELECT COUNT(*) FROM public.review_opinions) AS opinions,
    (SELECT COUNT(*) FROM public.review_analysis_runs) AS analysis_runs,
    (SELECT COUNT(*) FROM public.tasks) AS tasks;

SELECT business_path FROM public.task_context;
```

验收标准：数据库为 `reviews`、用户为 `review_agent`、端口为 5432；商品数 4、评论数 4,941；`task_context.business_path` 为 `postgresql://127.0.0.1:5432/reviews`。

再看几条评论，确认原始数据存在：

```sql
SELECT id, product_id, rating, published_at, has_text
FROM public.reviews
ORDER BY id
LIMIT 10;
```

后面的导入、标注、人工复核通过项目入口完成；DataGrip 用来查看结果，避免绕过项目的校验、版本和历史记录规则。

## 3. 检查项目和 HTTP 服务

先在 IDEA 运行 **03 查看评论库摘要**、**14 查看全库标注进度**、**74 查询后台任务列表**。预期均返回 `status=ok`。

前后端本次核对时已运行，可直接打开：

- 工作台：`http://127.0.0.1:5173`
- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/api/v1/health`

健康检查应显示 `business_database="ok"`、`worker_available=true`。也可以在 PowerShell 执行：

```powershell
$pgApiBase = 'http://127.0.0.1:8000/api/v1'
Invoke-RestMethod "$pgApiBase/health" | ConvertTo-Json -Depth 8
```

服务没有运行时，在 IDEA 选择 **71** 或 **77** 启动后端，两个配置选择一个；前端在另一个终端执行 `cd frontend`、`npm run dev`。不要与现有后台 worker 重复启动。

## 4. 先跑一遍不调用模型的只读分析

在 PowerShell 准备共享参数，选取当前 V3.2 标注版本。后续第 4、7 步的命令复用这些变量；新开终端后需重新执行本段。

```powershell
$pgPython = '.\.venv\Scripts\python.exe'
$pgLabelProgress = & $pgPython -m data_analysis_agent.cli.labeling status --db env:DATABASE_URL | ConvertFrom-Json
$pgAnalysisProfile = $pgLabelProgress.profiles |
    Where-Object {
        $_.requested_model -eq 'Pro/deepseek-ai/DeepSeek-V3.2' -and
        $_.prompt_version -eq $pgLabelProgress.current_prompt_version
    } |
    Select-Object -First 1
if (-not $pgAnalysisProfile) { throw '未找到本次要测试的标注版本，请先检查配置 14 的输出。' }
$pgQueryArgs = @(
    '--db', 'env:DATABASE_URL',
    '--model', $pgAnalysisProfile.requested_model,
    '--base-url', $pgAnalysisProfile.base_url,
    '--prompt-version', $pgAnalysisProfile.prompt_version,
    '--source', 'amazon_reviews_2023',
    '--dataset-version', 'amazon-reviews-2023'
)
```

按月趋势：

```powershell
& $pgPython -m data_analysis_agent.cli.analytics trend @pgQueryArgs --date-from 2016-01-01 --date-to 2016-03-31 --period month
```

四款商品对比：

```powershell
& $pgPython -m data_analysis_agent.cli.analytics compare-products @pgQueryArgs --date-from 2016-01-01 --date-to 2016-03-31
```

当前 3 月与基线 2 月的问题变化：

```powershell
& $pgPython -m data_analysis_agent.cli.analytics top-issues @pgQueryArgs --date-from 2016-03-01 --date-to 2016-03-31 --baseline-from 2016-02-01 --baseline-to 2016-02-29
```

原文证据：

```powershell
& $pgPython -m data_analysis_agent.cli.analytics evidence @pgQueryArgs --date-from 2016-01-01 --date-to 2016-03-31 --sentiment all --page-size 5
```

验收标准：退出码 0，`status=ok`；返回的 `scope.model` 是选定的 V3.2；趋势月份、商品、评论原文和引用位置对应；这四个查询不增加标注、分析快照或任务记录。

在 DataGrip 中核对同一时间窗的原始评论数：

```sql
SELECT product_id, COUNT(*) AS review_count
FROM public.reviews
WHERE source = 'amazon_reviews_2023'
  AND dataset_version = 'amazon-reviews-2023'
  AND published_at >= '2016-01-01T00:00:00.000+00:00'
  AND published_at < '2016-04-01T00:00:00.000+00:00'
GROUP BY product_id
ORDER BY product_id;
```

对比工具中的 `review_count` 应与 SQL 一致。标注覆盖率只针对选定模型；负面占比的分母是成功标注评论，不是全部原始评论。

## 5. 用前端验证后台任务和持久化

这一阶段会写入任务和分析快照，不调用模型。

1. 打开工作台，新建分析，选择现有数据集、商品及 V3.2 标注版本。
2. 当前窗口选 `2016-03-01` 至 `2016-03-31`，基线窗口选 `2016-02-01` 至 `2016-02-29`。
3. 备注填写便于识别的内容，例如“PostgreSQL 三月分析验收”，点击开始分析。
4. 等待任务完成，查看趋势图、商品对比、负面问题、证据，下载 JSON。
5. 刷新网页，再从历史列表恢复该任务，确认结果仍存在。

备注只用于识别任务，当前前端执行固定统计，不会把备注交给模型自由问答。

在 DataGrip 中查看新记录：

```sql
SELECT task_id, kind, status, created_at, started_at, finished_at
FROM public.tasks
ORDER BY created_at DESC, task_id DESC
LIMIT 10;

SELECT run_id, created_at, as_of
FROM public.review_analysis_runs
ORDER BY created_at DESC, run_id DESC
LIMIT 10;
```

验收标准：新任务为 `analysis`，最终状态为 `succeeded`，结果能恢复，下载内容对应同一运行 ID。输入与已有快照完全相同时会复用原 `run_id`，因此任务增加而分析快照数不增加也可能正常。任务执行很快时页面可能直接显示完成。

**同步 CLI 直接执行业务时，不会自动创建 `tasks` 记录。验证队列必须使用前端或 HTTP `/tasks` 提交。**

## 6. 小批量验证真实模型标注

本阶段会发送评论正文到 `.env` 配置的模型服务，消耗模型额度并写入标注结果。先记录下面同范围的进度，再只处理最多 5 条。共享变量需先按第 4 步设置：

```powershell
& $pgPython -m data_analysis_agent.cli.labeling status @pgQueryArgs --date-from 2015-12-01 --date-to 2016-03-31 --as-of 2016-03-31
```

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.labeling run --db env:DATABASE_URL --source amazon_reviews_2023 --dataset-version amazon-reviews-2023 --date-from 2015-12-01 --date-to 2016-03-31 --as-of 2016-03-31 --limit 5 --workers 1 --env-file .env
```

运行后再次执行上面的同范围进度命令；**14** 用于补充检查全库进度。成功结果会增量跳过，重复执行会继续下一批。若该范围已经没有待处理项，模型调用为 0 属于正常结果；可选择另一个实际存在、尚未标注的时间窗。

在 DataGrip 中检查实际写入的模型和状态：

```sql
SELECT id, review_id, requested_model, status, error_code, created_at
FROM public.review_annotations
ORDER BY id DESC
LIMIT 10;

SELECT a.review_id, a.requested_model,
       o.aspect, o.sentiment, o.quote, o.quote_start, o.quote_end
FROM public.review_opinions o
JOIN public.review_annotations a ON a.id = o.annotation_id
ORDER BY o.id DESC
LIMIT 10;
```

验收标准：进度实际变化，新记录的模型与 `.env` 一致，成功标注及观点原文对应。评论总数应保持 4,941；一条评论可以有多个观点，也可以成功标注但没有观点，所以观点数不要求与成功评论数相等。存在失败时查看 `error_code`，排查后才用 `--retry-failed`。

首次验证先使用上述 5 条命令。17、18 是批量模型操作，其中 17 当前实际参数是 `--limit 1000`，与配置名称中的数量不同；不要仅凭名称判断调用量。

如果要验证 HTTP 标注任务，可在 `/docs` 的 `POST /api/v1/tasks` 中提交下面的 JSON，再用返回的 `task_id` 查询详情。它同样会调用模型：

```json
{
  "kind": "label",
  "parameters": {
    "source": "amazon_reviews_2023",
    "dataset_version": "amazon-reviews-2023",
    "date_from": "2015-12-01",
    "date_to": "2016-03-31",
    "as_of": "2016-03-31",
    "limit": 5,
    "workers": 1,
    "retry_failed": false
  }
}
```

## 7. 验证人工复核、监控和回放

人工复核会保存真实处理记录。IDEA 运行 **21 启动人工复核工作台**，打开 `http://127.0.0.1:8765`，选择正确模型版本及一条成功标注，核对原文后确认或纠正，并填写实际复核人。

验收标准：`review_human_reviews` 增加记录；纠正观点写入 `review_human_opinions`，原 `review_annotations` 和 `review_opinions` 不被覆盖。重新查询同一模型与范围，结果采用最新人工复核快照；旧分析快照保留原结果。

然后可保存一次 3 月监控分析，写入快照并按默认规则判断告警：

```powershell
& $pgPython -m data_analysis_agent.cli.monitoring run @pgQueryArgs --date-from 2016-03-01 --date-to 2016-03-31 --baseline-from 2016-02-01 --baseline-to 2016-02-29
```

验证逐月回放：

```powershell
& $pgPython -m data_analysis_agent.cli.monitoring replay @pgQueryArgs --month-from 2016-01 --month-to 2016-03 --baseline-months 1
```

之后运行 **43 → 44** 查看保存的快照，运行 **45** 查询告警。只有实际出现告警时，才继续 **46 → 47 或 48** 核查并处理；这些编号的查询不依赖预设模型参数。

告警为空、或者判断为 `insufficient_data`，均不意味着 PostgreSQL 出错。默认规则要求当前和基线样本量、覆盖率、负面数量及增幅同时达标；当前大部分评论尚未标注。不要为了看到告警而修改真实评论。正向触发和边界场景用后面的隔离集成测试验证。

## 8. 可选：验证导入和去重

当前四款商品已经导入。运行 **02 导入四款商品评论** 会重新扫描真实文件，重复评论会被跳过，但会增加导入历史。

验收标准：数据源、版本、商品及输入文件均相同时，评论仍为 4,941，结果显示重复记录，原评论与原文证据不被替换。`--max-reviews` 限制的是新增评论；已有评论不占额度，因此同一文件全是重复记录时，仍可能扫描到文件末尾。

这一步可能耗时，首轮可先跳过。要验证坏文件、事务回滚或重复并发，不要在业务库手工制造故障，使用下一步的隔离测试。

## 9. 自动化测试：区分离线回归与 PostgreSQL 集成

离线回归，在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

也可运行 IDEA **90**。这主要使用临时 SQLite 和模拟数据；未配置测试管理员连接时，6 个 PostgreSQL 集成项会标记 `skipped`，不能把它解释为实际 PostgreSQL 测试已经通过。

运行真正的 PostgreSQL 集成测试，需要一个能创建数据库并删除其自建数据库的测试账号。当前应用账号 `review_agent` 没有 `CREATEDB` 权限。可以在本机 `.env` 临时填写 `TEST_POSTGRES_ADMIN_URL`，指向测试服务器的 `postgres` 数据库；该连接使用测试管理员密码，特殊字符需要 URL 编码。不要将连接内容发到聊天中。

格式如下，将占位内容换为实际管理员用户名和经过 URL 编码的密码。它与日常使用的 `DATABASE_URL` 是两个不同配置项：

```dotenv
TEST_POSTGRES_ADMIN_URL=postgresql://TEST_ADMIN:URL_ENCODED_PASSWORD@127.0.0.1:5432/postgres
```

填写后执行下面的命令，使测试从 `.env` 获得变量；只把变量写进 `.env`，直接运行 unittest 不会自动启用这些测试。

```powershell
@'
import os
import unittest
from data_analysis_agent.core.config import read_settings

url = read_settings().get('TEST_POSTGRES_ADMIN_URL')
if not url:
    raise SystemExit('请先在本机 .env 配置 TEST_POSTGRES_ADMIN_URL。')
os.environ['TEST_POSTGRES_ADMIN_URL'] = url
suite = unittest.defaultTestLoader.discover('tests', pattern='test_postgres.py')
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
'@ | .\.venv\Scripts\python.exe -
```

预期 7 项均通过，没有 `skipped`。测试创建并清理自身随机命名的 `agent_test_*` 数据库，不使用 `reviews` 业务库做故障实验；覆盖迁移一致性、失败回滚、后续自增、只读限制、复核冲突、任务幂等、worker 互斥、API 和真实后台执行。完成后可删除 `.env` 中的临时测试管理员连接。

前端回归，在 `frontend` 目录执行：

```powershell
npm test
npm run build
npm run test:e2e
```

现有 `test:e2e` 使用隔离的 SQLite 模拟样本，验证浏览器通用流程；本机 PostgreSQL 端到端流程由前面的工作台提交、DataGrip 查表和历史恢复验证补足。**91** 也是临时库导入验证，不会写入旧 SQLite 业务库或 PostgreSQL 业务库。

销售分析是另一条 CSV / Excel 流程，当前没有将销售数据保存到 PostgreSQL。要验收整个仓库，再分别运行 **60 销售固定流程（模拟数据）**，检查汇总表、图表和报告；按需要运行 **61 销售问答 Agent（调用模型）**，后者会消耗模型额度。这两项不作为 PostgreSQL 写入验证。

## 写操作前备份当前 PostgreSQL

旧 SQLite 备份不包含切换到 PostgreSQL 后新增的分析、标注或复核。准备进行第 5—8 步前，可先导出当前数据库。下面命令会在终端提示输入 `review_agent` 的原始密码，不把密码写入命令参数：

```powershell
New-Item -ItemType Directory -Force data/private/backups | Out-Null
$pgBackupFile = Join-Path 'data/private/backups' ('reviews-before-test-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fffffff') + '.dump')
& 'C:\Program Files\PostgreSQL\18\bin\pg_dump.exe' -h 127.0.0.1 -p 5432 -U review_agent -W -Fc -f $pgBackupFile reviews
```

运行成功后备份文件应存在且非空。实际的连接、启动和停止说明见 [本机运行记录](local-postgresql-running.md)，编号配置用途见 [IDEA 运行说明](idea-running.md)。

## 推荐第一轮顺序

**DataGrip 连接与条数 → 03 / 14 → 工作台提交一次分析 → DataGrip 核对 tasks 和快照 → 5 条模型标注 → 再次查询统计 → 21 人工复核 → 单月监控与回放 → 按需要测试导入与自动化。**

连接成功、页面能打开只是起点；新结果在 PostgreSQL 中持久化、刷新后能恢复、不同模型统计不混用、失败场景能在隔离库回滚，才算覆盖完整流程。
