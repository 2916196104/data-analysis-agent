# 评论分析助手：商品范围与自然语言回答专项修复

验收日期：2026-11-02。执行范围仅包括现有评论分析助手的范围绑定、意图路由、回答和性能记录；未新增 Agent 框架或重构页面。工作区已有的维度内容下钻改动保留，本文记录本阶段合并后的行为。

## 根因

1. 前端 `analysisParameters()` 用 `product_ids: []` 表示“全部商品”。后端曾把这个空数组直接交给意图和计划层；“当前空调”这种单数指代既没有唯一商品，也没有在选工具前澄清，最终落入仅支持单商品的二级主题工具并显示技术限制。
2. “某个维度有什么问题/好在哪里”与“差评集中在哪些维度”的路由边界不够清晰。前者需要观点内容或二级主题，后者只需要六维度统计。仅有一级计数时，回答不能补造具体投诉内容。
3. 简单排名的摘要只串接维度名；计数和分母分散在条目里。规则已经足以确定 `get_top_issues` / `get_positive_aspects` 时，原执行链仍可能请求规划模型两次。

## 修复与文件

| 部位 | 改动 |
| --- | --- |
| `src/data_analysis_agent/agents/reviews.py` | 校验目录商品后，把“全部商品”冻结为本次数据集的实际 ID；单工具维度排名直接执行确定性计划；记录范围、意图、规划模型、查询、回答及总耗时和规划模型次数。继续使用只读连接、工具注册表和原有发布校验。 |
| `src/data_analysis_agent/agents/review_intent.py`、`review_semantics.py`、`review_plan.py`、`review_prompts.py` | 区分显式商品、单商品指代、全部商品总体统计、维度计数与观点内容；显式越界商品拒绝；单数指代在多商品范围内先澄清；多商品内容使用现有观点归组，单商品可下钻二级主题。 |
| `src/data_analysis_agent/agents/review_answer_facts.py`、`review_answers.py`、`review_evidence.py`、`review_evidence_loop.py` | 根据已验证指标动态组成完整摘要；观点内容只引用实际归组；零命中说明具体维度无匹配观点；原文哈希留在结构化证据而不塞入正文；保留证据诊断、受控补查、失败/部分完成发布保护。 |
| `tests/test_review_dimension_content.py`、`test_review_plan.py`、`test_review_agent.py`、`test_review_answers.py`、`test_review_evidence_loop.py`、`frontend/tests/sessions.e2e.mjs`、`scripts/accept_evidence_loop.py` | 覆盖范围、意图、确定性计划、前后会话、真实 API/队列/Worker/浏览器、证据控制及模型调用记录。旧测试中的简单统计规划模型预期同步更新，复杂计划的情感错配与超时保护仍单独验证。 |

`frontend/src/analysis-draft.ts`、`useAgentChat.ts`、`useChatSessions.ts` 和 `PublicAnswerView.tsx` 经检查沿用现有协议：新会话按当前全局选择提交，历史任务保留原参数和范围快照；正文优先显示 `public_answer.summary`，统计和执行记录仍可展开。本阶段无需改动这些前端业务文件。

## 修复前后范围与计划

| 场景 | 修复前 | 修复后 |
| --- | --- | --- |
| 选中 `100013588055`，问“差评主要集中在哪里” | 范围正确，但走规划模型，摘要偏数据罗列 | 实际 ID 不变；`get_analysis_context` + `get_top_issues`；规划模型 0 次，动态中文总结 |
| 选中单商品，问“客服方面有什么问题” | 可能只返回客服计数 | `get_opinion_breakdown` 获取客服负向观点；没有观点时说明证据不足 |
| 选中单商品，问“物流好在哪里” | 二级主题依赖意图识别 | 物流正向 `get_aspect_drilldown`；主题必须由真实观点归属复核 |
| 全部商品，问“当前空调物流好在哪里” | 落入单商品工具并显示技术限制 | 先冻结目录实际 ID，随后提示指定商品 ID 或明确询问全部商品总体统计；不执行观点查询 |
| 全部商品，问“所有商品的物流好评整体怎么样” | 可能被单商品工具拦截 | `get_positive_aspects` 汇总当前数据集商品；没有物流好评时明确说明零命中，不编造体验 |
| 全部商品但明确写合法 ID | 指代与选择可能混淆 | 在目录验证后绑定该 ID |
| 单商品选择中明确写另一 ID | 可能悄悄切换对象 | 拒绝越界切换，要求先更改选择范围 |
| 新会话或切换商品后追问 | 容易误用旧会话上下文 | 新会话无旧 `context_task_id`；历史任务维持原范围，新任务使用新选择 |

## 自然语言回答前后

用户原快照的主要正文是“客服 165 条，32.7%；使用效果 66 条，13.1%；物流 53 条，10.5%”式罗列。修复后在真实商品 `100013588055` 的本地只读执行中，公开摘要为：

> 当前已标注评论中，客服是差评提及最多的维度：505 条成功标注评论中有 165 条提到该维度，占 32.7%。其他较多的维度是使用效果 66 条（13.1%）、物流 53 条（10.5%）。这些数字说明评价涉及哪些维度，具体内容仍需查看对应观点。

这段文字由本轮查询返回的维度、去重评论数、分母和占比动态拼接；商品、数量和维度均未硬编码。只读统计独立复核：本窗口 510 条评论、505 条成功标注，前三维度分别为 165/505、66/505、53/505。该结果不能说明客服问题的具体原因。原始聚合和执行计时见 [真实商品只读验收记录](../output/acceptance/real-product-100013588055-readonly.json)。

## 调用、耗时和验证

真实商品的上述只读执行：`get_analysis_context` 与 `get_top_issues` 各一次；查询执行 2 次；规划、回答、主题模型均 0 次。范围解析 142.2 ms、意图解析 6.0 ms、规划模型 0 ms、查询 289.9 ms、回答 2.3 ms、整轮 450.5 ms。用户修复前快照记录规划模型 2 次、整轮 11.3 秒；两次运行环境不同，耗时不作为严格性能基准。真实评论原文未发送给外部模型。

隔离合成数据的真实模型回环使用现有 API、持久队列和 Worker，3 个场景均通过：P01 0 次模型请求/0.469 秒；L02 1 次请求/6.535 秒；T02 3 次请求/12.081 秒。合计外部请求 4 次、总运行 21.272 秒；各请求耗时和 HTTP 尝试记录见 [真实模型合成数据报告](../output/acceptance/review-scope-live-synthetic-2026-11-02/report.json)。这些是合成评论，不是商品 `100013588055` 的原文。

离线故障控制集 8 个场景全部通过，覆盖预算不足、查询失败、伪造引文、写入尝试、越界日期、歧义、`partial` 和 `failed`；只读业务数据库哈希未变化，失败/部分完成未发布正常回答。详见 [故障控制报告](../output/acceptance/review-scope-controls-2026-11-02/report.json)。

评论测试 293 项中 290 项通过、3 项因隔离 PostgreSQL 条件未配置而跳过，见 [测试日志](../output/acceptance/review-suite-2026-11-02.log)。前端 216 项 Vitest 测试与生产构建通过。隔离 FastAPI/Worker/Vite/Chromium 回归通过，生成 1440、1024、768、390 px 共 20 张截图；检查了页面宽度、消息宽度、输入区、历史抽屉和关键交互。简单排名及总体正向统计均为 0 次规划模型，浏览器整套场景本地模拟模型共 7 次请求。详见 [浏览器验收记录](../output/playwright/sessions-sYoL65/acceptance.json)。

复现命令（项目根目录 PowerShell，真实模型测试需已有本地 `.env` 配置）：

```powershell
$env:PYTHONPATH='tests'; $env:LABEL_EXECUTION_MODE='local'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_review*.py' -q
cd frontend; npm.cmd test -- --maxWorkers=1; npm.cmd run build; cd ..
node frontend/tests/sessions.e2e.mjs
$env:PYTHONPATH='.;tests'
.\.venv\Scripts\python.exe -m scripts.accept_evidence_loop --controls --output output/acceptance/review-scope-controls-2026-11-02
.\.venv\Scripts\python.exe -m scripts.accept_evidence_loop --live-synthetic --output output/acceptance/review-scope-live-synthetic-2026-11-02
```

## 未解决和边界

- 真实数据只核验了 `100013588055` 的负面维度统计及模板回答；客服/物流原文下钻、跨全部真实商品的总体统计没有在生产数据上执行。对应路由使用隔离合成数据验证。
- 复杂分析的模型文案仍受既有事实与来源复核约束；上述自动检查不能代替人工判断表达是否足够自然。
- 前端使用 `[]` 表示“全部商品”的协议仍保留；执行结果冻结具体 ID，提交记录仍保留原选择语义。未来如果目录商品在两轮之间变化，需以各轮执行快照解释差异。

本阶段完成后停止，不继续开展新框架或页面大规模重构。
