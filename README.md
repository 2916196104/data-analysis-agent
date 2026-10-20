# 数据分析 Agent：CSV、Excel 与单 Agent

一个独立 Python 项目，支持本地销售 CSV/Excel 的读取、字段映射、校验、统计、绘图和 Markdown 报告，并接入 HelloAgents 单 Agent 的自然语言工具调用。应用统一通过 `load_sales_data` 读取数据，原有 `load_sales_csv` 接口继续保留。固定流程无需 API 密钥；自然语言流程使用支持原生 Function Calling 的 OpenAI 兼容模型服务。

## 运行

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

第一阶段已在 Python 3.12.10、Matplotlib 3.11.2 上实际完成默认 CLI 全流程和 8 项测试；第二阶段有 13 项离线配置/工具/框架测试，第三阶段 A 增加 9 项 Excel/CSV 和 Agent 验证，目前共 30 项通过。联调状态见对应阶段说明。

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
│   ├── __main__.py              # python -m 入口
│   └── __init__.py
├── tests/test_workflow.py
├── tests/test_agent.py           # 无外部 API 的真实框架集成测试
├── tests/test_excel.py           # CSV/Excel 一致性、类型与错误验证
├── docs/phase2.md
├── docs/phase3.md
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

当前面向一份小型 CSV 或一个 Excel 数据工作表，不提供大数据流式统计、退款分析、多币种换算或成本指标。SQL、页面与连续追问留到后续阶段。
