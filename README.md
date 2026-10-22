# 数据分析 Agent：销售分析与评论标注工作台

评论业务库和后台任务现支持 **PostgreSQL / SQLite**。PostgreSQL 默认将业务表、任务表放在同一专用数据库的 `public` schema；原 SQLite 参数继续可用。安装、`.env` 配置、已有数据的原子迁移、校验和回退步骤见 [PostgreSQL 配置与迁移](docs/postgresql.md)。迁移只接受尚未初始化的目标，**迁移已有数据前不要执行目标库的 `init`**。

一个独立 Python 项目，支持本地销售 CSV/Excel 的读取、字段映射、校验、统计、绘图和 Markdown 报告，并接入 HelloAgents 单 Agent 的自然语言工具调用。应用统一通过 `load_sales_data` 读取数据，原有 `load_sales_csv` 接口继续保留。固定流程无需 API 密钥；自然语言流程使用支持原生 Function Calling 的 OpenAI 兼容模型服务。

新增独立的 [React 评论分析工作台](frontend/README.md)，使用 TypeScript、Vite、Ant Design 和 ECharts。支持选择已有评论数据、服务端路径导入、提交结构化分析、真实任务进度、历史恢复、原文证据和 JSON 导出。启动与后端 Origin 配置、接口映射及现有能力边界见前端说明。

新增独立的 Amazon 评论/商品元数据 JSONL 导入入口，使用标准库 SQLite，支持 `.jsonl`/`.jsonl.gz` 流式读取、去重、原文定位和数据质量摘要。**环境配置和业务数据库初始化由你执行**；原有销售入口继续使用。配置步骤、可运行的模拟数据及完整契约见 [第三阶段 B：评论数据导入](docs/review-import.md)。

All_Beauty 的完整真实文件已下载到 `data/private/amazon_reviews_2023/raw/`，已选取 4 款美发造型工具，在临时库验证 4,941 条唯一评论的导入、去重和原文位置。选品、真实验收结果及业务库操作步骤见 [All_Beauty 真实数据说明](docs/all-beauty-real-data.md)。

新增 [评论标注工作台](docs/review-workbench.md)：本机网页支持商品、UTC 日期、维度、情感、模型处理状态及人工复核状态筛选；分页查看原文、观点与模型版本；人工确认、纠错及历史回查；按商品查看标注进度。人工记录另存，保留模型原始结果。无需新增依赖或模型配置，**数据库升级仍由你执行**，请先按说明备份：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.review_workbench init --db data/private/reviews.sqlite3
.\.venv\Scripts\python.exe -m data_analysis_agent.review_workbench serve --db data/private/reviews.sqlite3
```

浏览器打开 `http://127.0.0.1:8765`。页面不会自动升级数据库或调用模型，原有销售分析入口继续使用。

已新增 [六维度评论观点标注](docs/review-labeling.md)：复用现有模型配置，保存维度、情感、原文片段及位置、模型和提示词版本；支持增量跳过、失败重试与只读进度查询。业务库检查已确认导入及去重正确，可标注正文 4,939 条。观点表初始化和环境配置仍由你执行：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.review_label_cli init --db data/private/reviews.sqlite3
.\.venv\Scripts\python.exe -m data_analysis_agent.review_label_cli run --db data/private/reviews.sqlite3 --limit 20
.\.venv\Scripts\python.exe -m data_analysis_agent.review_label_cli status --db data/private/reviews.sqlite3
.\.venv\Scripts\python.exe -m data_analysis_agent.review_label_cli evidence --db data/private/reviews.sqlite3 --review-id 1
```

标注 `run` 与进度 `status` 现可使用相同的商品、UTC 起止日期及 `--as-of` 回放范围。新增只读 `query` 命令，按明确的模型、服务地址与提示词版本返回有效观点，优先采用最新人工复核快照。无需升级数据库，完整参数、示例和时间口径见 [按范围查询评论](docs/review-query-scope.md)。

新增 [评论统计与证据工具](docs/review-analytics.md)：`get_trend`、`get_top_issues`、`compare_products`、`get_evidence` 共用上述范围与人工有效观点规则，按去重评论计算数量、覆盖率和负面占比。命令行 `python -m data_analysis_agent.review_analysis_cli` 提供趋势、基线变化、商品对比、证据查询及 JSON 导出；无需前端、数据库升级或线上模型调用。

新增 [分析记录、月度回放与规则告警](docs/review-monitoring.md)：`python -m data_analysis_agent.review_monitor_cli` 保存本次分析输入、人工观点和统计证据；按 UTC 月末回放，检查样本量、覆盖率、负面数量及占比增长；支持冷却去重、告警查询、确认和忽略。首次使用须显式执行该入口的 `init --db`，只新增监控表。命令不调用模型，样本不足会记录“无法判断”。

`run` 会向配置的模型端点发送评论正文；默认每批最多 20 条。重复执行继续下一批，成功结果不重复调用模型；`--retry-failed` 可显式纳入失败项。可用 `--limit 200 --workers 4` 同时处理最多 4 条评论的模型请求，并在终端逐条查看进度；默认单路。用[观点标注说明](docs/review-labeling.md)中的人工复核方法比较模型准确率。开发联调仅用模拟样本请求线上接口；业务库中的真实标注由你执行。已有观点表时无需重复初始化。

```powershell
New-Item -ItemType Directory -Force data/private | Out-Null
.\.venv\Scripts\python.exe -m data_analysis_agent.review_cli init --db data/private/reviews.sqlite3
# 本机真实文件已准备好；仅导入选定的 4 款商品。
.\.venv\Scripts\python.exe -m data_analysis_agent.review_cli import --db data/private/reviews.sqlite3 --reviews data/private/amazon_reviews_2023/raw/All_Beauty.jsonl.gz --meta data/private/amazon_reviews_2023/raw/meta_All_Beauty.jsonl.gz --product-id B00R1TAN7I --product-id B019GBG0IE --product-id B01M1OFZOG --product-id B01N4885PD
.\.venv\Scripts\python.exe -m data_analysis_agent.review_cli summary --db data/private/reviews.sqlite3
.\.venv\Scripts\python.exe -m data_analysis_agent.review_cli evidence --db data/private/reviews.sqlite3 --review-id 1
```

导入不会自动创建数据库或调用模型。有坏记录时保留合法记录及错误明细，退出码为 3；文件损坏等整体失败会回滚本次导入。评论工作台、模型标注、评论统计、规则监控与销售 Agent 使用各自入口，后续再增加评论问答。

## 运行

新增 [统一 HTTP API 与后台任务](docs/review-http-api.md)：商品评论、人工复核、四个分析工具、运行记录与告警统一通过 `/api/v1` 调用。导入、标注、分析、回放使用独立 SQLite 任务库持久化，支持进度查询、幂等提交、显式重试和中断恢复。原有 CLI 保留，本次未新增或运行测试，验证由你进行。首次按文档安装依赖并显式初始化后启动。

已提供 **42 个 IDEA 运行配置**，保存于 [.run](.run/)。在顶部运行配置列表选择 **03 查看评论库摘要** 即可开始；脚本路径、工作目录、业务参数均已填好。**05 自定义数量导入亚马逊评论** 可在运行时输入新增评论上限，**18 亚马逊并发标注200条** 可在 4 路并发下继续增量标注。HTTP 服务使用 **69 → 70 → 71**；完整用途、历史回放范围与推荐顺序见 [IDEA 一键运行说明](docs/idea-running.md)。

**在 IntelliJ IDEA 中直接点击运行：** `review_cli.py`、`review_label_cli.py`、`review_analysis_cli.py`、`review_monitor_cli.py`、`review_workbench.py`、`agent_cli.py`、`main.py` 和 `__main__.py` 均支持按文件运行，无需改为模块模式。选择项目的 `.venv` Python 解释器；首次未填写参数时显示帮助并正常退出，不执行业务操作。

例如，右键 `src/data_analysis_agent/review_cli.py` 选择运行。要查看评论库摘要，在该运行配置的“脚本参数 / Parameters”中填写 `summary --db E:/AllProject/data-analysis-agent/data/private/reviews.sqlite3`，再点击绿色运行按钮，即可输出 JSON。标注入口可以填写 `status --db E:/AllProject/data-analysis-agent/data/private/reviews.sqlite3` 查看进度。需要导入、标注或回放时，按各入口帮助填写对应子命令与参数。

使用相对路径时，将运行配置的工作目录设置为实际项目根目录（本机为 `E:\AllProject\data-analysis-agent`）。入口不会自动修改工作目录；业务实现文件（例如 `review_store.py`）由入口调用。原有 `python -m ...` 和安装后的命令行入口仍按原有规则执行。

要求 Python 3.12 或 3.13（`>=3.12,<3.14`，与固定的 HelloAgents 1.0.1 一致）。以下命令在 PowerShell 中执行，复用已有 `.venv`；目录内已有 IDE 文件和虚拟环境均保留。

```powershell
Set-Location E:\AllProject\data-analysis-agent
if (!(Test-Path .venv\Scripts\python.exe)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m data_analysis_agent
```

安装依赖需要能访问 Python 包源。无需激活虚拟环境；运行上述固定流程也无需复制 `.env.example`。`pyproject.toml` 声明 Matplotlib、`hello-agents==1.0.1`、python-dotenv、`openpyxl==3.1.5` 和 `defusedxml==0.7.1`；CSV、Decimal 统计和测试框架使用标准库。

默认读取 [模拟销售 CSV](data/samples/sales_mock.csv)，在终端输出 JSON 概览和商品汇总，同时生成：

- `outputs/sales_by_product.png`：按商品汇总的销售额柱状图，无需桌面显示环境。
- `outputs/report.md`：数据概览、商品汇总表、图表相对链接。

指定自己的输入和输出目录：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent --input data/private/sales.csv --output-dir outputs/my_sales
```

相对路径以当前工作目录为基准。请先把自己的 CSV 放到指定路径；重复运行会覆盖输出目录内的同名图表和报告，不修改输入文件。安装后也可以使用 `.\.venv\Scripts\sales-analysis.exe` 运行同一个入口。

运行验证：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

如果受限环境提示系统临时目录权限不足，先把当前 PowerShell 进程的临时目录和绘图库缓存切换到项目内，再运行上述验证命令。本次 Windows 沙箱验证使用了此设置；普通本地环境通常无需设置。

```powershell
New-Item -ItemType Directory -Force .cache/tmp | Out-Null
$env:TEMP = (Resolve-Path .cache/tmp).Path
$env:TMP = $env:TEMP
$env:MPLCONFIGDIR = Join-Path (Get-Location) '.cache/matplotlib'
```

测试覆盖模拟数据预期结果、金额精度、CSV 异常、图表与报告、工具结果引用、模型认证/超时/重试、调用预算和日志脱敏。Agent 集成测试通过模拟 HTTP 传输层驱动真实 HelloAgents 和 OpenAI SDK，无需密钥且不调用外部 API。

Windows 受限沙箱可能阻止 asyncio 初始化所需的本地套接字，表现为 Agent 测试等待。此时在允许本地套接字的普通 PowerShell 中执行相同测试命令；临时目录配置仅解决文件权限问题。

第一阶段已在 Python 3.12.10、Matplotlib 3.11.2 上实际完成默认 CLI 全流程和 8 项测试；第二阶段有 13 项离线配置/工具/框架测试，第三阶段 A 增加 9 项 Excel/CSV 和 Agent 验证，当时共 30 项通过。第三阶段 B 新增评论导入离线测试，联调和使用说明见对应阶段文档。

## 第二阶段：自然语言分析

先创建本地配置文件，已有文件保留：

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

在 `.env` 中填写 `LLM_MODEL_ID`、`LLM_API_KEY`、`LLM_BASE_URL`。服务必须支持原生工具调用；密钥不进入 Git。配置完整后执行：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.agent_cli --question "总销售额是多少？"
.\.venv\Scripts\python.exe -m data_analysis_agent.agent_cli --input data/samples/sales_mock.csv --question "按商品汇总并生成图表和报告。"
.\.venv\Scripts\python.exe -m data_analysis_agent.agent_cli --question "计算利润率。"
```

前两个问题预期为 `status=ok`，结果来自工具计算；利润率问题预期为 `status=unsupported` 并说明缺少成本指标。新入口成功退出 0、配置/数据/模型失败退出 1、参数错误退出 2、不支持的请求退出 3。安装后的 `sales-agent` 命令对应同一入口。

模型最终答复若为完整的 JSON 代码块，应用会移除代码块后校验；若无法解析为 JSON，会在原有总调用预算内请求一次格式纠正。纠正仍失败时返回 `agent_error`，不把已有工具结果冒充任务已完成。未知结果引用和模型自行提供的统计字段仍被拒绝。

2026-10-20 已使用硅基流动 `deepseek-ai/DeepSeek-V4-Flash` 完成上述三个问题的真实在线验收，工具调用、统计结果及产物均符合预期。本机记录见 [在线验收记录](outputs/phase2-live-validation.md)。

每次运行创建 `outputs/<run_id>/`，包含 `request.json`、`trace.jsonl`、`results/`、`outcome.json`，以及按问题需要生成的图表和报告。终端返回该目录绝对路径。详细配置、接口核对、工具契约和联调状态见 [第二阶段说明](docs/phase2.md)。

## 第三阶段 A：直接分析 Excel

把自己的 `.xlsx` 文件放在 `data/private/`，首行为表头。默认识别 `date/销售日期/日期`、`product/商品名称/商品`、`sales_amount/销售额/销售金额`。工作簿只有一个数据工作表时可以省略 `--sheet`；多个工作表时必须明确选择。

假设文件为 `data/private/my_sales.xlsx`，工作表名为“销售明细”：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent --input data/private/my_sales.xlsx --sheet "销售明细" --output-dir outputs/my_excel_check
.\.venv\Scripts\python.exe -m data_analysis_agent.agent_cli --input data/private/my_sales.xlsx --sheet "销售明细" --question "按商品汇总并生成图表和报告。" --output-dir outputs/my_excel
```

其他表头可通过 `--column-map` 指定 UTF-8 JSON 文件，映射方向为“标准字段 → 原始表头”；参考 [映射示例](docs/column-map.example.json)。CSV 也支持中文表头和相同的映射选项。公式单元格需先粘贴为值，旧 `.xls` 需另存为 `.xlsx`。工作表、实际字段映射和文件指纹会记录到结果和报告中。

2026-10-20 已用同批模拟数据验证 CSV、中文表头 Excel、自定义映射 Excel 的统计和 PNG 一致，并实际完成 Excel 的单 Agent 模型联调。详细使用、数据边界和本机复验命令见 [第三阶段 A 说明](docs/phase3.md)。

## 模拟数据与预期结果

`sales_mock.csv` **全部为人工构造的模拟数据，不代表真实业务**。每一行的 `data_source` 均为 `SIMULATED`；此列用于标识数据性质，不参与分析。商品名使用英文，以便样例图在不同系统字体环境中正常显示。

日期范围为 `2026-10-01` 至 `2026-10-03`，共 9 条记录、3 种商品。每行视为一条销售记录，销售额单位由输入数据决定，不假定币种。

| 指标 | 预期值 |
| --- | ---: |
| 总销售额 | 900.00 |
| 平均每条销售额 | 100.00 |
| 最小单条销售额 | 50.00 |
| 最大单条销售额 | 180.00 |

| 商品 | 预期销售额 |
| --- | ---: |
| Coffee | 400.00 |
| Juice | 250.00 |
| Tea | 250.00 |

汇总按销售额降序排列；相同销售额按商品名升序排列，因此 Juice 在 Tea 之前。金额使用 `Decimal` 计算，返回两位小数字符串以避免 JSON 浮点误差；平均值使用 `ROUND_HALF_UP` 四舍五入。图表显示时才转为浮点数。

## 数据契约

CSV 使用逗号分隔，UTF-8 或 UTF-8 BOM 编码。内部标准字段如下，原始表头也可使用上文的中文名称或显式映射：

| 字段 | 规则 | 示例 |
| --- | --- | --- |
| `date` | 有效的 `YYYY-MM-DD` 日期 | `2026-10-01` |
| `product` | 去除首尾空白后非空；商品名区分大小写 | `Coffee` |
| `sales_amount` | 非负普通十进制金额，最多两位小数 | `120.50` |

允许额外列，读取时忽略；重复/空字段名、缺少必要列、行列数不一致、空文件、只有表头、无效日期、空商品、非法金额和编码均报错。金额不接受千位分隔符、科学计数法、NaN、Infinity 或负数；本阶段不处理退款。重复记录保留并按多条销售计入，输入不应混用不同币种。

坏数据整份拒绝，不静默删除或填补，也不在校验通过前生成产物。`SalesDataError` 说明字段或行号；文件不存在等问题使用标准 `OSError` 子类。CLI 将这些错误写入标准错误并退出 1，成功退出 0，命令参数错误由 argparse 退出 2。

## 项目结构和独立函数

```text
data-analysis-agent/
├── data/samples/sales_mock.csv   # 唯一纳入 Git 的模拟数据
├── src/data_analysis_agent/
│   ├── data_loader.py           # CSV/Excel 统一入口、工作表、映射与校验
│   ├── analysis.py              # 纯统计函数，不读写文件
│   ├── reporting.py             # PNG 图表和 Markdown 报告
│   ├── main.py                  # 流程编排和 CLI
│   ├── config.py                # 显式读取本地配置、校验与脱敏
│   ├── agent_tools.py           # 绑定当前数据的四类工具与执行记录
│   ├── agent.py                 # 单 Agent、调用预算、结果引用校验
│   ├── agent_cli.py             # 第二阶段自然语言入口
│   ├── review_loader.py         # Amazon JSONL/gzip 流式解析和校验
│   ├── review_store.py          # SQLite 导入、去重、质量统计和原文证据
│   ├── review_cli.py            # 评论 init/import/summary/evidence 独立入口
│   ├── review_labeler.py        # 六维度 Pydantic 输出与原文校验、模型标注
│   ├── review_label_store.py    # 观点表显式初始化、尝试历史和增量查询
│   ├── review_label_cli.py      # 标注 init/run/status/evidence/query 独立入口
│   ├── review_scope.py          # UTC 范围和当前模型/人工有效观点共享口径
│   ├── review_analytics.py      # 趋势、问题基线、商品对比和证据业务工具
│   ├── review_analysis_cli.py   # 四类工具的 JSON 命令行入口与导出
│   ├── review_monitor.py        # 同一快照分析、月度回放和规则判断
│   ├── review_monitor_store.py  # 运行快照、告警命中与人工处理历史
│   ├── review_monitor_cli.py    # init/run/replay、记录查询与告警处理
│   ├── review_workbench.py      # 本机工作台 HTTP 与显式 init/serve 入口
│   ├── review_workbench_store.py # 筛选分页、商品进度与人工复核快照
│   ├── workbench_assets/        # 无外部资源的 HTML/CSS/JavaScript 页面
│   ├── __main__.py              # python -m 入口
│   └── __init__.py
├── tests/test_workflow.py
├── tests/test_agent.py           # 无外部 API 的真实框架集成测试
├── tests/test_excel.py           # CSV/Excel 一致性、类型与错误验证
├── tests/test_reviews.py         # 临时 SQLite、JSONL、原文、去重与回滚测试
├── tests/test_review_labels.py   # 无网络的六维度、证据、增量和失败恢复测试
├── tests/test_review_workbench.py # 临时库与本机 HTTP 的筛选、复核、进度测试
├── tests/test_review_scope.py    # 范围、回放截止、版本隔离与有效观点测试
├── tests/test_review_analytics.py # 统计口径、基线、证据、CLI 与只读测试
├── tests/test_review_monitor.py # 冷却、去重、证据快照、回放与事务测试
├── docs/phase2.md
├── docs/phase3.md
├── docs/review-import.md
├── docs/review-labeling.md
├── docs/review-workbench.md
├── docs/review-query-scope.md
├── docs/review-analytics.md
├── docs/review-monitoring.md
├── docs/column-map.example.json
├── outputs/                     # 运行时生成，不提交
├── pyproject.toml
├── .gitignore
├── .env.example
└── README.md
```

| 函数 | 作用 / 返回值 |
| --- | --- |
| `data_loader.validate_sales_fields(fieldnames)` | 独立检查表头；无返回值 |
| `data_loader.load_sales_data(input_path, *, sheet=None, column_map=None)` | 统一读取 CSV/Excel，返回含 records/source 的 SalesDataset |
| `data_loader.load_sales_csv(input_path)` | 保留的 CSV 函数，返回已校验的记录列表 |
| `data_loader.load_sales_xlsx(input_path, *, sheet=None, column_map=None)` | 单独读取一个 Excel 工作表，返回同格式记录列表 |
| `analysis.get_data_overview(records)` | 返回记录数、商品数、日期范围和金额统计字典 |
| `analysis.summarize_sales_by_product(records)` | 返回排序后的 `{商品: 金额字符串}` |
| `reporting.generate_sales_chart(totals, output_path)` | 写入 PNG，返回绝对路径字符串 |
| `reporting.write_markdown_report(overview, totals, chart_path, output_path, source_name)` | 写入报告，返回绝对路径字符串 |
| `main.run_analysis(input_path, output_dir)` | 完整流程，返回统计结果及产物路径字典 |

在安装项目后，可从任意 Python 调用方使用这些函数：

```python
import json
from data_analysis_agent.data_loader import load_sales_csv
from data_analysis_agent.analysis import get_data_overview, summarize_sales_by_product
from data_analysis_agent.main import run_analysis

rows = load_sales_csv("data/samples/sales_mock.csv")
print(get_data_overview(rows))
print(summarize_sales_by_product(rows))
result = run_analysis("data/samples/sales_mock.csv", "outputs")
print(json.dumps(result, ensure_ascii=False, indent=2))
```

记录使用 `SalesRecord` TypedDict（日期和金额均为字符串），函数有类型标注和 docstring；输出能直接 JSON 序列化。统计函数接受统一入口产出的已校验记录。HelloAgents 适配集中在新增模块中，原有读取、统计和报告函数继续独立调用。

## 本地配置和 Git

`.env.example` 包含空的模型配置项和有限的调用预算默认值。自然语言入口显式读取当前目录的 `.env`，也可用 `--env-file` 指定；进程环境变量优先。第一阶段入口继续只使用 CLI 参数，不读取 `.env`。

真实数据放 `data/private/`，产物放 `outputs/`。`.gitignore` 忽略 `.env` 及其变体（保留 `.env.example`）、常见密钥文件、整个数据目录（只放行明确的模拟 CSV）、常见数据文件扩展名、运行产物、日志、缓存、虚拟环境和 IDE 配置。自定义产物目录也应放在 `outputs/` 下；提交前使用 `git status --short` 检查，不使用强制添加来绕过忽略规则。

销售分析当前面向一份小型 CSV 或一个 Excel 数据工作表，不提供大数据流式销售统计、退款分析、多币种换算或成本指标。评论入口已支持 SQLite、流式导入、六维度增量标注、本机人工复核、范围查询、四类统计证据工具、运行快照、按月历史回放及规则告警；评论问答、HTTP API 与定时运行留到后续阶段。
