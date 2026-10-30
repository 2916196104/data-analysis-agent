# 多轮追问真实模型验收

日期：2026-10-30（Asia/Taipei）。本次使用项目现有 PostgreSQL、现有评论标注和 `.env` 中的 DeepSeek V3.2 配置，浏览器通过真实 API 和 Vite 页面完成验收。

## 数据与授权

- 数据集：`reviews_csv / reviews-csv`。
- 商品：`100013588053`、`100010219881`；物流负面观点数量分别为 273、69。
- 当前窗口：2022-01-01 至 2022-08-27；基线窗口：2021-01-01 至 2021-12-31。
- 模型：`Pro/deepseek-ai/DeepSeek-V3.2`，供应商地址为项目已配置的 `api.siliconflow.cn`。
- 用户已明确批准将这两款商品的统计上下文和少量物流原文发送到该供应商；未保存 API 密钥。
- 任务写入独立临时任务库；业务数据库以只读连接打开。验收前后所有业务表摘要均相同。

## 结果

| 轮次 | 实际工具与关键参数 | 模型调用 | 结果 |
| --- | --- | ---: | --- |
| 初始问题 | `get_aspect_trend`，包装，`period=day` | 2 | 通过 |
| 只看物流 | `get_aspect_trend`，物流，`period=day` | 2 | 通过 |
| 换成按月 | `get_aspect_trend`，物流，`period=month` | 2 | 通过 |
| 查原文 | `get_evidence`，物流负面，当前窗口，第 1 页，共 278 条 | 2 | 通过 |
| 继续查原文 | 同一筛选，第 2 页 | 2 | 通过；与第 1 页评论 ID 不重复 |
| 范围变化后确认 | 先显示澄清选项，再选择沿用上轮范围查询第 1 页原文 | 2 | 通过 |

每个任务的 `status=ok`、结果引用和确定性 `validation.status=passed` 均通过。无上文的“只看物流”和含糊的“那它呢？”在前端澄清阶段完成，没有创建任务或调用模型。范围切换后的澄清在 1440、1024、768 像素下均可见，刷新后保留待确认状态；确认后才创建任务。

## 实际修复

真实模型首次把 JSON 中的 `question` 当作业务数据的一部分，忽略了“按日”并重复调用包装趋势直到耗尽调用预算。已修复：

- 将用户查询与 `<bound_context>` 业务上下文分隔，明确前者是本轮请求。
- 将趋势工具的 `period` 声明为必填，避免模型依赖默认的按月。
- 成功工具响应明确告知不要重复调用并返回已完成结果 ID。
- 提示词升级为 `review-agent-v3`，约束显式周期、重复调用和最终引用。
- 增加后端回归：工具 schema 周期必填、成功完成信号、查询/业务上下文分隔。

## 验证命令

- `npm test -- src/features/agent/followup.test.tsx src/features/agent/chat.test.tsx --maxWorkers=2`：32 项通过。
- `npm run build`：TypeScript 和 Vite 构建通过；保留既有大分包警告。
- `.venv/Scripts/python.exe -m unittest discover -s tests -p test_review_agent.py -q`：通过。
- `node frontend/tests/assistant-live.e2e.mjs --live`：通过，浏览器错误 0。
- 真实证据目录：[followup-live-BJjzPm](E:/AllProject/data-analysis-agent/output/playwright/followup-live-BJjzPm)。其中 `report.json`、六个任务 JSON、三张澄清截图和 `business-unchanged.json` 可复核。

真实验收仍是固定范围的一次运行，不能代表模型对任意自然语言问题的泛化准确率或生产性能。统计上下文和物流原文已按授权发送到配置的第三方模型服务。
