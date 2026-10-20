# 第二阶段：单 Agent 工具调用

本阶段保留第一阶段固定流程，增加自然语言 CLI。使用一个 HelloAgents `SimpleAgent` 选择工具，应用负责数据入口、调用预算、运行记录和结果校验。

## 安装与框架核对

```powershell
Set-Location E:\AllProject\data-analysis-agent
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip check
```

依赖固定为 `hello-agents==1.0.1`，Python 要求 `>=3.12,<3.14`，本机为 3.12.10。版本与安装要求依据 [官方 PyPI 页面](https://pypi.org/project/hello-agents/1.0.1/)；工具定义参考 [官方自定义工具说明](https://github.com/jjyaoao/HelloAgents/blob/main/docs/custom_tools_guide.md)。HelloAgents 作者为 HelloAgents Team，项目源码见 [官方仓库](https://github.com/jjyaoao/HelloAgents)。

已核对实际安装版本的接口：

- `SimpleAgent(..., tool_registry=registry, max_tool_iterations=...)`。
- 自定义 `Tool` 实现 `get_parameters()` 和 `run(parameters)`，返回 `ToolResponse`。
- `ToolRegistry.register_tool(tool)` 注册工具；`SimpleAgent.run()` 内部通过 `aexecute_tool()` 调度。
- `RunBudget(max_calls=...)` 限制模型逻辑调用次数。

1.0.1 没有 SDK 客户端注入配置项。项目在 `_new_llm()` 中集中设置 OpenAI 客户端的超时与重试，使用固定版本的适配器属性；集成测试覆盖这一处兼容调整。升级框架时需要重新核对该接口。

## 配置模型

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

用本地编辑器填写 `.env`。不需要在聊天中发送密钥。

| 配置项 | 含义 | 默认值 / 范围 |
| --- | --- | --- |
| `LLM_MODEL_ID` | 模型服务提供的模型标识 | 必填 |
| `LLM_API_KEY` | 对应服务的密钥 | 必填，仅本地保存 |
| `LLM_BASE_URL` | OpenAI 兼容 API 基础地址，按服务要求保留 `/v1` 等路径 | 必填，无 URL 凭据、查询参数或片段 |
| `AGENT_MAX_STEPS` | 模型逻辑调用总上限 | 8，范围 1～30 |
| `AGENT_MAX_TOOL_CALLS` | 允许实际调度的工具调用上限 | 12，范围 1～40 |
| `LLM_TIMEOUT_SECONDS` | 每次 HTTP 请求的网络超时 | 30，范围大于 0 且不超过 300 |
| `LLM_MAX_RETRIES` | 每次请求在暂时性失败后的额外重试数 | 1，范围 0～3 |

阶段二使用 OpenAI 兼容协议和原生 Function Calling。配置读取限定为指定文件，环境变量覆盖同名文件值，不自动寻找父目录的 `.env`。密钥不会出现在配置对象 repr、运行 JSON、工具轨迹或终端结果中；远端异常正文不落盘。

### 硅基流动配置

使用硅基流动时，API 协议是 OpenAI 兼容协议，服务地址和密钥使用硅基流动账号对应的配置。国内站地址为 `https://api.siliconflow.cn/v1`，[官方 Chat Completions 文档](https://api-docs.siliconflow.cn/docs/api/chat-completions-post) 给出的 V4 示例模型为 `deepseek-ai/DeepSeek-V4-Flash`，并提供 `tools` 参数示例。

国际站使用 `https://api.siliconflow.com/v1`，其模型 ID 应按国际站控制台填写；[国际站 V4 Pro 工具调用示例](https://www.siliconflow.com/blog/deepseek-v4-pro-function-calling) 使用 `deepseek-ai/DeepSeek-V4-Pro-0813`。模型 ID 和账号可用性以相应控制台为准，不能只填 `deepseek v4`。

本地 `.env` 中填写对应的 `LLM_MODEL_ID`、`LLM_API_KEY` 和 `LLM_BASE_URL`，无需修改 Agent 的统计工具代码。模型名称和服务地址确定后，仍需真实密钥才能完成在线联调。

SDK 按其规则重试临时连接错误、超时、限流及服务端错误，认证错误不自动重试。重试计入同一次模型逻辑调用；默认最多 8 次逻辑调用，每次最多 2 次 HTTP 尝试。外层单轮等待也有上限，但网络超时不等同于严格的整任务墙钟截止时间。

## 运行与验收

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.agent_cli --question "总销售额是多少？"
.\.venv\Scripts\python.exe -m data_analysis_agent.agent_cli --input data/samples/sales_mock.csv --question "按商品汇总并生成图表和报告。"
.\.venv\Scripts\python.exe -m data_analysis_agent.agent_cli --question "计算利润率。"
```

也可使用 `.\.venv\Scripts\sales-agent.exe`。通过 `--output-dir outputs/my_analysis` 指定运行根目录；每次创建新的运行子目录，不覆盖之前的任务。

| 请求 | 预期结果 | 退出码 |
| --- | --- | ---: |
| 总销售额 | `status=ok`，工具计算得到 900.00 | 0 |
| 商品汇总、图表和报告 | Coffee 400.00，Juice 250.00，Tea 250.00，并返回真实产物路径 | 0 |
| 利润率 | `status=unsupported`，说明缺少成本指标 | 3 |
| 配置缺失、坏数据、文件不存在、认证失败或超时 | `status=error`，说明原因并保存可用运行记录 | 1 |
| CLI 参数不合法 | argparse 提示用法 | 2 |

第一阶段入口继续有效，不需要模型配置：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent
```

## 工具与计算依据

| 工具 | 参数 | 作用 |
| --- | --- | --- |
| `inspect_dataset` | 无 | 返回字段、概览和缺失成本指标的说明；保存 `overview` |
| `summarize_sales` | 无 | 按商品汇总并检查总分一致；保存 `sales_by_product` |
| `create_chart` | `result_id` | 仅接受当前运行的商品汇总结果，生成 PNG，保存 `chart` |
| `build_report` | `result_id`、`chart_id` | 核对统计和图表来源，生成 Markdown，保存 `report` |

应用在启动任务时通过 `load_sales_csv()` 一次性加载已选 CSV，并计算文件 SHA-256。Agent 不能指定其他数据路径或输出文件名，也不能给图表/报告传入自行编造的金额。原始明细留在本地，模型接收字段、概览和聚合结果。

Agent 的最终响应只允许 `status` 和已经生成的 `result_ids`。应用校验引用后读取保存的结果，组装终端答案；统计值和报告正文由原有确定性函数生成。直接输出金额、未知结果引用或额外统计字段会被拒绝。模型决定分析步骤，程序保证被展示的数字来自实际工具结果；自然语言任务是否覆盖完整仍需按验收案例检查。

```text
outputs/<run_id>/
├── request.json                 # 问题、模型名称、限制、数据来源与指纹
├── trace.jsonl                  # 模型响应元信息、工具请求和实际执行回执
├── results/
│   ├── overview.json
│   ├── sales_by_product.json
│   ├── chart.json
│   └── report.json
├── sales_by_product.png         # 仅在相应工具执行后生成
├── report.md                    # 包含统计表、图表和来源结果引用
└── outcome.json                 # ok / unsupported / error，已有结果和耗时
```

结果记录含 `source_sha256` 和 `source_result_ids`，因此能沿“报告 → 统计/图表结果 → 数据指纹 → 原文件”复核。`model_calls` 记录模型逻辑调用数，`model_responses` 记录返回的模型响应数；SDK 内部 HTTP 重试不单独计为模型调用。服务未返回用量时 `usage` 为 `null`，不将其报告为零消耗。轨迹不保存私有思考过程或原始模型异常正文。

`tool_calls` 为注册表调度尝试数，包含超过上限时被拒绝的请求；实际进入工具执行的次数不会超过配置上限。

## 离线验证与在线联调状态

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`tests/test_agent.py` 使用真实 HelloAgents、工具注册表和 OpenAI SDK，仅通过 `httpx.MockTransport` 模拟服务响应。验证三个验收问题、参数/结果引用拒绝、有限重试、认证失败、超时、模型/工具调用上限，以及不记录测试密钥。原有 8 项测试同时运行。

Windows 沙箱可能限制临时目录或 asyncio 的本地套接字：临时目录设置见 README；本地套接字被阻止时，应在普通 PowerShell 执行测试。该测试不请求真实模型服务。

**2026-10-20 已完成真实模型在线联调**：硅基流动国内站 `https://api.siliconflow.cn/v1`，模型 `deepseek-ai/DeepSeek-V4-Flash`，密钥保存在本地 `.env`。

| 验收问题 | 真实结果 | 实际工具调用 |
| --- | --- | --- |
| 总销售额是多少？ | `ok`，900.00，9 条记录、3 种商品 | `inspect_dataset` |
| 按商品汇总并生成图表和报告 | `ok`，Coffee 400.00、Juice 250.00、Tea 250.00，图表和报告已生成 | `summarize_sales` → `create_chart` → `build_report` |
| 计算利润率 | `unsupported`，明确说明缺少成本指标 | `inspect_dataset` |

本次三项验收共 8 次模型逻辑调用、5 次工具调度。已核对工具执行回执、数值、图表/报告及数据来源引用，并检查 17 份运行产物中没有配置的 API 密钥。原有 17 项离线测试通过记录继续保留。

本机完整记录见 [真实模型验收记录](../outputs/phase2-live-validation.md) 和 `outputs/phase2-live-validation.json`；每项请求、结果和工具轨迹位于 `outputs/live_acceptance/<run_id>/`。这些产物均由 Git 忽略，需要复现时执行上面的 CLI 命令。
