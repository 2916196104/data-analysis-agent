# 第三阶段 A：Excel 与统一数据入口

CSV 和 Excel 共用 `load_sales_data()`，返回包含 `records` 和 `source` 的 `SalesDataset`。两种格式转成相同的 `SalesRecord` 后，复用现有概览、商品汇总、绘图、报告和 HelloAgents 单 Agent。`load_sales_csv()` 的调用方式及原有两个 CLI 入口均保留。

## 安装与运行自己的 Excel

在项目根目录的 PowerShell 中执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

将自己的工作簿保存为 `data/private/my_sales.xlsx`。第一行必须是表头，从第二行开始填写销售记录。假设工作表名为“销售明细”，运行：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent --input data/private/my_sales.xlsx --sheet "销售明细" --output-dir outputs/my_excel_check
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.agent --input data/private/my_sales.xlsx --sheet "销售明细" --question "按商品汇总并生成图表和报告。" --output-dir outputs/my_excel
```

命令中的文件名和工作表名需要与实际文件一致。固定流程不调用模型，成功退出 0；Agent 使用现有 `.env`，成功退出 0、不支持的指标退出 3、数据或模型错误退出 1。

工作簿只有一个数据工作表时可以省略 `--sheet`；多个工作表时必须指定，不合并数据，也不自动猜测第一张。缺少选择或名称不存在时，错误中列出可用的数据工作表。CSV 不能使用 `--sheet`。

## 自动表头识别

| 内部字段 | 自动识别的原始表头 |
| --- | --- |
| `date` | `date`、销售日期、日期 |
| `product` | `product`、商品名称、商品 |
| `sales_amount` | `sales_amount`、销售额、销售金额 |

表头首尾空白会去除；英文表头区分大小写。同一字段同时存在多个候选列时，必须显式指定映射。重复表头、缺少字段、空表头和多个字段映射到同一列均拒绝。其他列忽略，不参与统计；不要额外添加合计行。

## 使用其他表头

复制示例配置后，修改副本中的原始表头名称：

```powershell
if (!(Test-Path data/private/column_map.json)) { Copy-Item docs/column-map.example.json data/private/column_map.json }
```

映射方向是“内部字段 → 原始表头”，文件必须是 UTF-8 JSON 对象。例如：

```json
{
  "date": "成交日",
  "product": "品名",
  "sales_amount": "实收金额"
}
```

可以仅指定要覆盖的字段，其他字段继续自动识别。应用到任一入口：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent --input data/private/my_sales.xlsx --sheet "销售明细" --column-map data/private/column_map.json --output-dir outputs/my_excel_check
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.agent --input data/private/my_sales.xlsx --sheet "销售明细" --column-map data/private/column_map.json --question "总销售额是多少？" --output-dir outputs/my_excel
```

CSV 也支持相同的自动识别和 `--column-map` 参数。模型不能选择路径、工作表或映射；这些选项由命令行或应用调用方确定。

## Excel 数据规则

- 支持 `.xlsx`，不支持旧版 `.xls`、加密工作簿或宏工作簿。旧文件需另存为 `.xlsx`。
- 日期可以是 `YYYY-MM-DD` 文本，或 Excel 原生日期。含有非零时间部分的日期时间拒绝；没有日期格式的裸数字不猜测为日期序列号。
- 商品必须是非空文本；金额可以是数字或普通十进制文本，要求非负、最多两位小数。布尔值、千位分隔文本、货币符号、非法金额均拒绝，不自动四舍五入修正原始数据。
- 必要字段中的公式与 Excel 错误单元格拒绝。请在 Excel 中复制并“粘贴为值”后再分析，避免使用过期缓存或把没有缓存的公式当作空值。
- 完全空白的行跳过。只缺少部分必要字段的行仍然报错。没有表头的数据列会报错。
- 文件只读打开并及时关闭，不修改原工作簿。当前仍面向小型销售数据，不新增退款、多币种、成本或任意指标计算。

读取依赖固定为 [openpyxl 3.1.5](https://pypi.org/project/openpyxl/3.1.5/)，项目的 Python 范围继续为 3.12/3.13；本机 Python 3.12.10 已验证。按[官方读取接口](https://openpyxl.readthedocs.io/en/stable/tutorial.html#loading-from-a-file)使用 `read_only=True`、`data_only=False`、`keep_links=False`，并安装 `defusedxml==0.7.1` 保护 XML 解析。日期处理遵循[官方日期说明](https://openpyxl.readthedocs.io/en/stable/datetime.html)。

## 来源记录与独立函数

`source` 包含实际文件名、绝对路径、SHA-256、格式、选定工作表和实际字段映射。固定流程将其放入返回 JSON 和报告；Agent 的 `request.json`、各结果 JSON、报告中也记录这些信息。文件指纹不同或工作表不同，来源记录会明确区分，统计结果仍按相同口径计算。

```python
from data_analysis_agent.sales.loader import load_sales_data, load_sales_csv, load_sales_xlsx
from data_analysis_agent.sales.workflow import run_analysis

dataset = load_sales_data("data/private/my_sales.xlsx", sheet="销售明细")
records = dataset.records
source = dataset.source
result = run_analysis("data/private/my_sales.xlsx", "outputs/my_excel_check", sheet="销售明细")
```

`load_sales_csv(path)` 和 `load_sales_xlsx(path, sheet=...)` 返回记录列表；上述函数的 `column_map` 均接受 Python 映射对象或 JSON 文件路径。原有 `run_analysis(input_path, output_dir)`、`run_agent_analysis(input_path, question, output_root)` 参数保持兼容，新增选项均为关键字参数。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

30 项测试通过，保留原有 21 项并新增 9 项。覆盖同批 CSV/Excel 的记录、统计、PNG、报告一致性；原生日期和金额；工作表选择；自动与显式映射；歧义/重复/缺失字段；非法日期/金额/公式；空工作表、坏文件；CLI 和真实 HelloAgents 的离线集成。

2026-10-20 已完成真实 CLI 和模型联调。本机验证文件均为 **SIMULATED 模拟数据**，位于 `outputs/phase3/fixtures/sales_mock.xlsx`：

| 工作表 | 用途 | 预期总额 |
| --- | --- | ---: |
| 销售明细 | 中文表头，与原模拟 CSV 同一批记录 | 900.00 |
| 另一个月 | 验证仅分析指定工作表 | 123.00 |
| 自定义表头 | 成交日、品名、实收金额，使用显式映射 | 900.00 |

本机可以直接执行以下命令复验：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent --input outputs/phase3/fixtures/sales_mock.xlsx --sheet "销售明细" --output-dir outputs/phase3/manual_excel
.\.venv\Scripts\python.exe -m data_analysis_agent --input outputs/phase3/fixtures/sales_mock.xlsx --sheet "自定义表头" --column-map docs/column-map.example.json --output-dir outputs/phase3/manual_custom
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.agent --input outputs/phase3/fixtures/sales_mock.xlsx --sheet "销售明细" --question "按商品汇总并生成图表和报告。" --output-dir outputs/phase3/manual_agent
```

900.00 对应 Coffee 400.00、Juice 250.00、Tea 250.00。已实际核对三种来源的统计和 PNG 字节一致，报告中的统计表一致，来源信息分别记录；真实模型调用汇总、图表和报告工具并返回 `ok`。完整记录见本机 [验证结果](../outputs/phase3/verification.json)。这些运行产物由 Git 忽略；新检出项目请使用自己的 Excel，自动化测试会独立生成临时验证文件。
