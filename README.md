# 数据分析 Agent：第一阶段

一个独立 Python 项目，先完成本地销售 CSV 的读取、校验、统计、绘图和 Markdown 报告。架构面向后续单 Agent：所有文件读取统一经过 `load_sales_csv`，本阶段无需模型、网络服务或 API 密钥；HelloAgents 在下一阶段接入。

## 运行

要求 Python 3.11+。以下命令在 PowerShell 中执行，复用已有 `.venv`；目录内已有 IDE 文件和虚拟环境均保留。

```powershell
Set-Location E:\AllProject\data-analysis-agent
if (!(Test-Path .venv\Scripts\python.exe)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m data_analysis_agent
```

安装依赖需要能访问 Python 包源。无需激活虚拟环境，也无需复制 `.env.example`。`pyproject.toml` 声明构建和运行依赖，运行时只依赖 Matplotlib；CSV、Decimal 统计和测试均使用标准库。

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

测试覆盖模拟数据预期结果、小数精度、平均值四舍五入、BOM、异常表头/行/金额/编码、缺失文件、空数据、零金额图表、Markdown 转义，以及实际生成图表和报告的完整流程。

本阶段已在 Python 3.12.10、Matplotlib 3.11.2 上实际完成默认 CLI 全流程、8 项测试和 `pip check`；另验证了文件不存在时退出 1、参数错误时退出 2，以及安装后的 `sales-analysis` 入口。

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

CSV 使用逗号分隔，UTF-8 或 UTF-8 BOM 编码，必须包含以下大小写敏感的表头：

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
│   ├── data_loader.py           # 统一读取入口和字段/内容校验
│   ├── analysis.py              # 纯统计函数，不读写文件
│   ├── reporting.py             # PNG 图表和 Markdown 报告
│   ├── main.py                  # 流程编排和 CLI
│   ├── __main__.py              # python -m 入口
│   └── __init__.py
├── tests/test_workflow.py
├── outputs/                     # 运行时生成，不提交
├── pyproject.toml
├── .gitignore
├── .env.example
└── README.md
```

| 函数 | 作用 / 返回值 |
| --- | --- |
| `data_loader.validate_sales_fields(fieldnames)` | 独立检查表头；无返回值 |
| `data_loader.load_sales_csv(input_path)` | 统一读取 CSV，返回已校验的记录列表 |
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

记录使用 `SalesRecord` TypedDict（日期和金额均为字符串），函数有类型标注和 docstring；输出能直接 JSON 序列化。统计函数接受统一入口产出的已校验记录。下一阶段可以添加 HelloAgents 工具适配层并注册这些能力，无需让读取、统计、报告依赖 Agent 框架；当前不创建额外的 Agent、工具注册器或模型配置。

## 本地配置和 Git

`.env.example` 仅包含说明，不含密钥；本阶段不读取 `.env`，参数通过 CLI 传入。下一阶段接入模型时再添加对应配置。

真实数据放 `data/private/`，产物放 `outputs/`。`.gitignore` 忽略 `.env` 及其变体（保留 `.env.example`）、常见密钥文件、整个数据目录（只放行明确的模拟 CSV）、常见数据文件扩展名、运行产物、日志、缓存、虚拟环境和 IDE 配置。自定义产物目录也应放在 `outputs/` 下；提交前使用 `git status --short` 检查，不使用强制添加来绕过忽略规则。

本阶段在内存中处理一份小型 CSV，不提供流式读取、退款分析或多币种换算。HelloAgents 接入按计划留到下一阶段。
