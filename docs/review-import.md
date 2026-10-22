# 第三阶段 B：Amazon 评论与商品元数据导入

流式读取 Amazon Reviews 2023 的评论和商品元数据，保存到 SQLite，提供去重、逐条原文定位和数据质量摘要。原有销售 CSV/Excel、图表、报告及单 Agent 入口继续使用。

**环境配置和业务数据库初始化由你执行。** 此功能使用 Python 标准库 `sqlite3`，无需安装数据库服务、创建账号或设置端口；不读取 `.env`，不调用模型或下载数据。`import`、`summary`、`evidence` 都要求数据库已经存在，只有显式执行 `init` 才会创建数据库。本次自动化测试仅使用临时数据库。

本机已获取 All_Beauty 完整真实文件，并完成 4 款商品的真实导入验收。实际文件位置、选品、验收计数及需要你执行的步骤见 [All_Beauty 真实数据操作说明](all-beauty-real-data.md)。下面的通用步骤适用于自行准备其他类目文件。

导入后可以使用独立的 [六维度观点标注入口](review-labeling.md)，按模型/提示词版本增量处理，原始商品、评论和导入历史保持独立。

## 配置并导入真实数据

在项目根目录打开 PowerShell。先确认现有 Python 支持 SQLite：

```powershell
Set-Location E:\AllProject\data-analysis-agent
.\.venv\Scripts\python.exe -c "import sqlite3; print(sqlite3.sqlite_version)"
```

自行创建数据目录并初始化专用数据库。数据库路径没有默认值，每条命令都必须显式指定；可以更换为你选择的目录。父目录必须先存在。`init` 对已初始化的评论库返回 `created=false`，拒绝覆盖其他文件、空文件或其他业务数据库。

```powershell
New-Item -ItemType Directory -Force data/private | Out-Null
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.reviews init --db data/private/reviews.sqlite3
```

从 [Amazon Reviews 2023 项目页](https://amazon-reviews-2023.github.io/main.html) 自行下载所选类目的 `review` 和 `meta` 文件，放到 `data/private/`。以下假设使用 `All_Beauty.jsonl.gz` 和 `meta_All_Beauty.jsonl.gz`；文件名应与实际下载文件一致。解压后的 `.jsonl` 也可直接读取。

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.reviews import --db data/private/reviews.sqlite3 --reviews data/private/All_Beauty.jsonl.gz --meta data/private/meta_All_Beauty.jsonl.gz
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.reviews summary --db data/private/reviews.sqlite3
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.reviews evidence --db data/private/reviews.sqlite3 --review-id 1
```

可以只传 `--reviews` 或 `--meta`。同时传入时先处理元数据，再处理评论，整个命令使用同一个事务。先导入评论也可以：系统创建缺少元数据的商品占位记录，后续导入元数据会补全。

确定商品 ID 后，可重复传入 `--product-id` 筛选 `parent_asin`，例如下面的模拟商品 ID：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.reviews import --db outputs/review_demo/reviews.sqlite3 --reviews outputs/review_demo/mock_reviews.jsonl --dataset-version demo-simulated --product-id DEMO-P1 --product-id DEMO-P2
```

此命令使用下一节生成的模拟文件。默认筛选仍需逐行经过整个文件；选中商品以外的合法 JSON 对象计为 `filtered_records`，不再验证其评分等字段。JSON 损坏或商品 ID 格式错误仍计入坏记录。

### 自定义导入数量与提速

添加 `--max-reviews 1000`，本次最多新增 1000 条评论；可改为任意正整数，省略时不限数量。例如只导入指定商品的评论：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.reviews import --db data/private/reviews.sqlite3 --reviews data/private/amazon_reviews_2023/raw/All_Beauty.jsonl.gz --product-id B00R1TAN7I --max-reviews 1000
```

- 上限统计本次新增的唯一评论，不是文件行数或数据库总数。重复、坏记录及未选中的商品不占额度；有效的空正文评论占额度。
- 达到上限后停止解析评论，摘要返回 `limit_reached=true`、`scan_complete=false`；`total_lines` 仅表示已扫描的行数。这表示已完成本批目标，不会单凭达到上限返回失败。即使上限恰好落在文件末行，也保守标记为未确认扫描完整文件。
- 再次运行会从文件开头扫描，跳过已存在评论，继续新增最多指定数量；不是从上次行号直接恢复。坏记录可能再次计入导入历史。已有四款商品的全部评论已导入时，再设上限也不会重复新增；要扩大范围请调整或删除 `--product-id`。
- 限量导入不检查停止位置之后的评论 JSON 或 gzip 尾部完整性，不能当作整份文件的数据质量验收；整文件 SHA-256 前后校验仍然保留，所以即使数量很少也有完整文件读取开销。
- `--meta` 可省略。提供时仍先完整处理符合商品筛选的元数据，评论上限不限制商品元数据；后续可以单独补充元数据。

导入实现复用单条评论的内容指纹，并使用有界商品缓存减少重复的占位记录写入；保留原有事务、去重、原文定位与文件变更回滚规则，无需升级数据库。数量较少时，主要节省后续解压、JSON 解析与数据库写入；不会保证所有文件和机器都有固定提速比例。

IDEA 可选择 **05 自定义数量导入亚马逊评论**，运行时输入本次新增上限（默认 1000），默认仍筛选现有四款商品。网页“从服务端目录导入”提供“本次最多新增评论数”，留空即不限；HTTP 参数对应 `max_reviews`，仅提供元数据时不能设置此参数。

2026-10-21 使用 20,000 条、4 款商品的人工模拟评论，在全新临时库中对比改动前后，各运行 3 次取中位数：JSONL 全量导入从 1.0892 秒降至 0.9454 秒，gzip 从 1.0551 秒降至 0.9166 秒，耗时约下降 13%；两种格式限量新增 1000 条分别为 0.0661 秒和 0.0453 秒。限量测试完成的工作量较少，不与全量视为同等任务。全量结果的评论身份、内容指纹及行号一致；此结果不代表真实大文件的固定性能。原始记录保存在本机 `.cache/import-speed/benchmark.json`。

本次验证：127 项 Python 测试、12 项前端单元测试及前端构建通过；真实 API、worker 和浏览器的隔离模拟验收确认，包含 3 条新评论的文件设置上限 1 时只新增 1 条，并返回停止标记。浏览器验收记录位于 `output/playwright/frontend-hFUeJV/acceptance.json`。未调用线上模型或向业务库导入数据。

安装入口已在 `pyproject.toml` 声明为 `review-import`。如需使用 `.venv\Scripts\review-import.exe`，由你重新执行 `pip install -e .` 注册；直接使用上面的 `python -m` 无需额外注册，且本次没有新增依赖。

## 使用模拟数据验证完整流程

这一节会由你创建 **SIMULATED 人工构造数据**，用于验证命令，不代表真实商品反馈。首次执行生成脚本；文件已存在时拒绝覆盖。之后可以重复执行导入命令验证去重。

```powershell
@'
import json
from pathlib import Path

root = Path("outputs/review_demo")
root.mkdir(parents=True, exist_ok=True)
reviews = [
    {"parent_asin": "DEMO-P1", "user_id": "DEMO-U1", "rating": 5,
     "text": "Works well.", "timestamp": 1693526400000, "data_source": "SIMULATED"},
    {"parent_asin": "DEMO-P1", "user_id": "DEMO-U2", "rating": 2,
     "text": "The package arrived damaged.", "timestamp": 1693612800000,
     "data_source": "SIMULATED"},
    {"parent_asin": "DEMO-P2", "user_id": "DEMO-U3", "rating": None,
     "text": "", "timestamp": 1693699200000, "data_source": "SIMULATED"},
]
meta = [{"parent_asin": "DEMO-P1", "title": "Simulated beauty product",
         "main_category": "All Beauty", "details": {"Brand": "Demo"},
         "data_source": "SIMULATED"}]
for name, rows in (("mock_reviews.jsonl", reviews), ("mock_meta.jsonl", meta)):
    with (root / name).open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
'@ | .\.venv\Scripts\python.exe -

.\.venv\Scripts\python.exe -m data_analysis_agent.cli.reviews init --db outputs/review_demo/reviews.sqlite3
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.reviews import --db outputs/review_demo/reviews.sqlite3 --reviews outputs/review_demo/mock_reviews.jsonl --meta outputs/review_demo/mock_meta.jsonl --dataset-version demo-simulated
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.reviews summary --db outputs/review_demo/reviews.sqlite3
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.reviews evidence --db outputs/review_demo/reviews.sqlite3 --review-id 1
```

全新模拟库的预期结果：

| 指标 | 预期值 |
| --- | ---: |
| 评论数 `review_count` | 3 |
| 商品数 `product_count` | 2 |
| 缺少元数据的商品数 `products_without_metadata` | 1 |
| 缺少元数据的评论数 `reviews_without_metadata` | 1 |
| 空正文评论数 `empty_text_count` | 1 |
| 缺少评分评论数 `missing_rating_count` | 1 |
| 坏记录累计次数 `rejected_record_count` | 0 |

重复执行完整导入命令，两份文件的 `inserted_records` 都为 0，评论和元数据的 `duplicate_records` 分别为 3 和 1。每次导入尝试仍会新增 `import_runs` 记录。

## 输入与统一字段

支持 UTF-8（首行可以有 BOM），一行一个 JSON 对象；支持 `.jsonl` 和 `.jsonl.gz`，后缀不区分大小写。原始对象的额外字段保留在 `raw_json` 中。

| 统一字段 | Amazon 来源及规则 |
| --- | --- |
| `source` | 固定为 `amazon_reviews_2023`，表示输入适配器 |
| `dataset_version` | 默认 `amazon-reviews-2023`；用 `--dataset-version` 标记独立快照或模拟数据 |
| `product_id` | 非空 `parent_asin`，首尾空白去除；评论和商品元数据据此关联 |
| `source_record_id` | 程序生成的评论身份 SHA-256，规则见下节 |
| `text`、`title` | 原样保留文本，不改写正文；缺失时为空字符串；显式 `null` 或其他类型拒绝 |
| `rating` | 可缺失或为 `null`；否则必须是 1—5 的有限数字，不接受布尔值或数字字符串 |
| `published_at` | `timestamp` Unix 毫秒整数转换的 UTC 时间，保留毫秒 |
| `fetched_at` | 本地首次导入时间（UTC），不代表源站抓取时间 |
| `asin`、`user_id` | 文本，可缺失为空；不接受显式 `null` 或其他类型 |
| `raw_json` | 解码后的原始 JSON 行，保留行内空格与全部字段，移除 BOM 和行结束符 |

时间字段兼容官方示例中的 `sort_timestamp`。两者同时存在时必须相同；不猜测秒、毫秒等单位。此适配器只接受 1996—2023 年的评论时间，以识别误传的秒级时间戳；其他版本或数据源需要另加适配器。

空正文和缺评分记录保留并计入质量摘要，供后续决定是否参与文本分析。`has_text` 使用 Python Unicode 空白规则判断，空正文不计为导入失败。标题、品牌、类目缺失的商品元数据可以导入，缺少标题另有质量计数。

Amazon 数据用于历史回放。当前功能仅保存时间与原始数据，尚未实现回放查询、观点标注或趋势告警；商品元数据里的累计评分、价格等也不代表任意历史时点的状态。[官方字段说明](https://amazon-reviews-2023.github.io/main.html#data-fields)

## 去重与来源证据

评论唯一键为 `(source, dataset_version, source_record_id)`。有 `user_id` 时，身份指纹由标准化后的商品 ID、变体 ID、用户 ID、标题、正文、评分和发布时间生成。JSON 键顺序、空格、`3` 与 `3.0` 的差异，以及帮助票数等附加字段变化，不会新增一条评论。不同文件中的相同评论也可识别。

缺少用户 ID 时，使用原始文件 SHA-256 与物理行号作为身份依据，避免误合并不同匿名用户的相同内容。同一文件重复导入可去重，但匿名评论跨文件、重新压缩或重排行后不能保证去重。来源没有可靠的原生评论 ID，因此编辑过正文或评分的记录保留为另一条记录；目前不推断评论修订关系。

重复记录保留首次成功导入的原文、位置和 `fetched_at`，后续来源只记录导入计数。同版本、同商品的元数据若内容相同则去重；内容不同则登记冲突，保留首次内容。独立快照可用不同 `dataset_version` 隔离；汇总商品数包含各版本的独立商品记录。

`evidence` 返回评论 ID、正文、原始 JSON、文件绝对路径、文件指纹、导入批次和行号，以及：

```text
sha256:<原始文件 SHA-256>#line=<物理行号>
```

对 gzip 输入，SHA-256 针对压缩文件本身，行号针对解压后的物理行（空行也计入）。正文中的转义换行不增加物理行号。文件移动后，仍可从数据库读取保存的原文；数据库未保留压缩文件本身，应自行保留下载文件。

## 质量摘要与错误处理

每份输入返回一份 `imports` 摘要并写入 `import_runs.summary_json`：

| 字段 | 含义 |
| --- | --- |
| `total_lines`、`blank_lines` | 物理行数及跳过的空行数 |
| `filtered_records` | 商品筛选排除的记录数 |
| `inserted_records`、`duplicate_records` | 新增记录数、重复记录数；补全商品占位记录计为新增元数据 |
| `invalid_records` | 无法导入的记录数，包括元数据冲突 |
| `valid_records` | 新增与重复之和 |
| `empty_text_records`、`missing_rating_records`、`missing_user_id_records` | 本次有效评论中的质量计数，包含重复记录 |
| `error_samples` | 最多 10 条错误的物理行号与原因，避免大文件生成过大的终端摘要 |
| `product_ids` | 本次筛选条件；空列表表示全部商品 |
| `warnings` | 没有符合条件的有效记录等提示 |

计数满足 `total_lines = blank_lines + filtered_records + inserted_records + duplicate_records + invalid_records`。评论质量计数与商品元数据计数分别解释；元数据输入的正文、评分、用户缺失计数均为 0。

数据库 `quality` 是当前全库去重后的统计，含评论数、商品数、日期范围、缺失字段及元数据关联情况；`product_sample` 最多列出评论最多的 20 个商品。`rejected_record_count` 是历次导入拒绝记录的累计次数，重复导入同一坏记录会再次计数。

逐行 JSON、UTF-8 或字段错误保存到 `import_errors`，包含行号、原因及原始字节（BLOB），随后继续读取。JSON 重复键、NaN/Infinity、溢出浮点数及无法编码的 Unicode 都会拒绝。错误摘要不输出评论正文；原文和坏行仍可能包含个人信息，应放在本地私有目录。

文件打开失败、gzip 损坏、文件读取期间指纹变化、数据库写入失败等属于整个命令的失败：回滚该次命令的评论、元数据、错误记录和导入历史，已有数据保留。此时不会留下已提交的失败批次，原因由 CLI 返回。空文件、只有空行或全部被筛选排除会返回 `partial` 并给出提示。

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功，包括全部为重复记录 |
| 1 | 数据库未配置、文件或数据库错误；导入写入回滚 |
| 2 | 命令参数错误 |
| 3 | 部分完成：有坏记录，或某个输入没有有效记录；合法数据及质量记录已经提交 |

可用 `import_run_id` 查询完整错误，下面代码只读访问数据库，由你在项目根目录执行：

```powershell
@'
import sqlite3
from contextlib import closing
from pathlib import Path

uri = Path("data/private/reviews.sqlite3").resolve().as_uri() + "?mode=ro"
with closing(sqlite3.connect(uri, uri=True)) as connection:
    for row in connection.execute(
        "SELECT import_run_id, line_number, reason FROM import_errors ORDER BY id"
    ):
        print(row)
'@ | .\.venv\Scripts\python.exe -
```

## 存储、性能与 Python 调用

SQLite 使用四张表：`products`、`reviews`、`import_runs`、`import_errors`。商品采用来源、数据版本和商品 ID 的复合主键，评论引用商品和导入批次；启用外键并建立商品/发布时间索引。数据库使用应用 ID 与 schema 版本识别，拒绝导入到不匹配的数据库，没有自动迁移或自动改配。

每次只解析一行；文件指纹按 1 MiB 分块读取。为检查输入变化，每份文件依次进行导入前指纹读取、逐行解析、导入后指纹读取，共三次顺序扫描；gzip 只在解析时解压。内存随单条记录大小和 SQLite 缓存增长，不随评论总量构造 Python 列表。整个命令使用一个写事务，期间占用 SQLite 写锁；首版不提供断点续传，失败后可依靠去重重跑。

```python
from data_analysis_agent.reviews.store import (
    get_quality_summary,
    get_review_evidence,
    import_amazon_jsonl,
)

# 先由你通过 CLI init 初始化该库。
result = import_amazon_jsonl(
    "data/private/reviews.sqlite3",
    reviews_path="data/private/All_Beauty.jsonl.gz",
    meta_path="data/private/meta_All_Beauty.jsonl.gz",
)
quality = get_quality_summary("data/private/reviews.sqlite3")
evidence = get_review_evidence("data/private/reviews.sqlite3", 1)
```

CLI 的 `partial` 对应 Python 返回字典的状态；文件及数据库错误则抛出异常。`ReviewDataError` 定义在 `review_loader.py`。Python 调用与命令行共用完全相同的实现。

## 验证与实现位置

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_reviews.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

测试数据全部在临时目录生成，覆盖普通/BOM/gzip 输入、Unicode 原文、毫秒时间、商品关联、去重、匿名记录、元数据冲突、坏记录保存、摘要限长、筛选、命令行退出码、文件变化与 gzip 损坏回滚，以及千条记录导入时禁用整文件读取接口。Windows 沙箱的临时目录和 Agent 本地套接字限制仍按 [README](../README.md#运行) 处理。

2026-10-20 已实际通过全部 46 项离线测试：原有 30 项销售/Excel/Agent 测试和新增 16 项评论导入测试；`pip check` 通过。完整 Agent 回归在允许 asyncio 本地套接字的环境执行，HTTP 使用模拟传输，不请求真实模型。上文模拟数据生成脚本及初始化、导入、重复导入、质量摘要、原文查询命令也已在独立临时目录验证，预期计数一致。随后已下载 All_Beauty 完整类目文件，选取 4 款商品，在临时库验收 4,941 条唯一评论及 4 条元数据的导入、去重与逐条原文定位，见 [真实数据记录](all-beauty-real-data.md)。业务数据库初始化和实际导入操作仍由你执行。

| 实现文件 | 职责 |
| --- | --- |
| `src/data_analysis_agent/reviews/loader.py` | JSONL 流式解析、分块指纹与 Amazon 字段校验 |
| `src/data_analysis_agent/reviews/store.py` | 显式初始化、事务导入、去重、来源定位和质量查询 |
| `src/data_analysis_agent/cli/reviews.py` | `init`、`import`、`summary`、`evidence` 四个子命令 |
| `tests/test_reviews.py` | 无网络、无模型调用的临时数据库测试 |

`data/private/`、`outputs/`、JSONL、SQLite 和数据库辅助文件由 Git 忽略。导入模块负责评论数据底座；已新增独立的 [观点标注模块](review-labeling.md)，后续可在原始评论、观点及来源记录上增加回放统计和告警。
