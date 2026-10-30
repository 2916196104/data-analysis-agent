# 阶段 2：工具调用、证据预算与失败任务展示

日期：2026-10-30（Asia/Taipei）。基于阶段 0 审查和阶段 1 工作区继续实施。本阶段没有修改生产评论/标注、数据库结构、任务提交必填字段或统计口径，没有增加第二次 LLM 回答生成调用，也没有进入阶段 3。

## 交付结果

- 新增轻量查询计划，区分任务类型和情感方向；只向模型注册本轮需要的业务工具。
- 普通统计不能自动查原文；证据默认 3 条、1 页，每轮明确数量上限 10 条。更多证据通过显式下一页追问获取。
- 缓存复用规范化参数的成功结果；分别记录尝试、缓存命中和实际业务查询执行次数。缓存调用仍受总尝试预算限制。
- 后端失败保留诊断数据，前端仅在 `task.status=succeeded`、Agent 状态有效且校验未失败时发布回答。
- `positive_aspects` 增加类型、摘要、独立正向表格、两个分母和 Markdown 导出适配。
- 原先阻塞的完整 HelloAgents/SDK 离线测试在允许本机套接字的执行环境中恢复运行；这里的模型响应都是模拟响应，不代表真实供应商表现。

## 修改文件清单

| 文件 | 本阶段职责 |
| --- | --- |
| `src/data_analysis_agent/agents/review_plan.py`（新增） | 查询计划、能力识别、工具参数约束、证据预算、计划完成性判断。 |
| `src/data_analysis_agent/agents/review_prompts.py` | 保留阶段 1 情感识别；提示词升级 `review-agent-v5`，要求遵守应用计划。 |
| `src/data_analysis_agent/agents/review_tools.py` | 计划内工具注册、参数拦截、证据页/条数执行预算、缓存归一化及计数。 |
| `src/data_analysis_agent/agents/reviews.py` | 模型调用前生成计划；不支持的能力直接返回说明；结果校验接入计划和实际子查询范围；保存新计数。 |
| `src/data_analysis_agent/agents/review_validation.py` | 维度排名/时间序列的任务匹配，证据情感/维度过滤，绑定范围与合法商品子集、基线窗口兼容。 |
| `src/data_analysis_agent/jobs/worker.py` | 非有效 Agent 状态或校验失败统一 failed，移除未经确认的正文，保留统计与 trace 诊断。 |
| `frontend/src/types.ts` | `PositiveAspect`、`positive_aspects` 联合类型及可选诊断计数。 |
| `frontend/src/features/agent/ChatMessage.tsx` | succeeded 发布条件、failed/partial 隐藏结果、部分完成提示与原重试入口。 |
| `frontend/src/AgentResults.tsx` | 正向结果标题/入口；错误及不支持状态的保守展示。 |
| `frontend/src/features/agent/ExtendedResults.tsx` | 最小正向六维统计表，两个分母和空值显示。 |
| `frontend/src/agent-report.ts`、`frontend/src/features/agent/report-export.ts` | 正向摘要和 Markdown 统计表/口径。 |
| `frontend/src/features/agent/followup.ts` | 识别“继续查看下一页”；保留历史页大小，新趋势转证据默认 3 条。 |
| `tests/test_review_plan.py`（新增） | 隔离 SQL、同步受控编排、预算、缓存、分页、计划完成性及 worker 失败保存。 |
| `tests/test_review_agent.py` | 真实 HelloAgents/SDK 模拟链路及工具后超时；验证模型只获得计划所需工具。 |
| `tests/test_review_capabilities.py` | 保留原断言，复合问题明确请求原用例实际验证的四项能力。 |
| `frontend/src/features/agent/chat.test.tsx` | 成功/历史、失败/超时/部分完成/校验失败的发布保护与重试。 |
| `frontend/src/features/agent/followup.test.tsx` | 下一页表达、旧页大小兼容与新默认值。 |
| `frontend/src/features/agent/report-export.test.tsx` | 正向维度两个占比分母、空值和导出。 |
| `frontend/tests/assistant.e2e.mjs` | 模拟模型遵循实际计划；实际本机 API/Vite/Chromium 回归和正向/失败/部分状态截图。 |

`agents/runtime.py`、`useTask()` 无须改动：原模型轮次、总调用预算、终态停止轮询、切换任务时取消旧请求的行为继续复用。没有重新实现阶段 1 正向统计 SQL。

## 查询计划与工具选择

`ReviewQueryPlan` 是一个 dataclass，不引入新框架。包含 `analysis_type`、`sentiment`、`required_tools`、`optional_tools`、`requirements`、`requires_evidence`、证据页/条数预算、范围和不支持原因。当前默认 optional_tools 为空。

情感使用现有 `detect_sentiment_intent()`；分析类型单独识别聚合、趋势、原文、质量、星级、商品/窗口对比。无法明确识别的请求保守返回 `unsupported`，不会默认扩充全部工具。该规则层覆盖明确表达及现有前端追问协议，不等同于任意自然语言的完整语义理解。

| 问题 | 计划与边界 |
| --- | --- |
| 美的空调好评主要集中在哪些方面？ | `positive_aspects` + positive，只需 `get_positive_aspects`，证据预算 0。商品须在已选绑定范围内。 |
| 美的空调差评主要集中在哪些方面？ | `issues` + negative，复用 `get_top_issues`。 |
| 给我 3 条制冷效果好的原文 | 返回明确能力限制；不会把宽泛使用效果标注当成制冷筛选，也不伪造细分标签。 |
| 按月查看包装和物流问题变化 | 分别查询 packaging、logistics 的 `get_aspect_trend(period=month)`；不查询星级、商品对比或原文。 |
| 好评主要方面，并给出 3 条证据 | 聚合 + positive 原文，最多 3 条、1 页。 |
| 最近半年好评趋势 | 模型调用前 `unsupported`：尚无正向时间序列工具，禁止负向趋势替代。 |

计划不仅写在 prompt 中：注册表过滤工具，`check_call()` 检查调用参数，`ReviewToolContext` 检查证据预算，`validate_report()` 检查 required requirements 是否均已满足。复杂请求仍可执行多个必要工具，例如数据质量、包装趋势、星级、窗口比较四项协同，不存在统一两个工具上限。

## 缓存、预算与诊断口径

| 字段 | 含义 |
| --- | --- |
| `tool_calls`（兼容保留） | 全部工具调度尝试，包含自动 overview、缓存调用及失败尝试。 |
| `model_tool_attempts`（新增） | 模型发起的工具调用尝试，不含应用预先读取的 overview。 |
| `cache_hits`（新增） | 命中成功结果缓存的次数。 |
| `query_executions`（新增） | 实际调用统计/证据业务查询函数的次数，包含 overview；执行后报错也计入。不是底层 SQL 语句条数。 |
| trace 的 `cache_hit` | 标识该条成功调用是否复用缓存。 |

Pydantic 先填充默认参数，缓存键再将商品列表排序去重，把“空商品列表沿用绑定范围”与显式全选商品归一化。缓存命中直接返回原始 result_id 的深拷贝，不重跑数据库查询、不分配新 ID。

没有取消缓存调用的尝试计数：原 `AGENT_MAX_TOOL_CALLS` 和模型 `max_steps` 预算继续阻止循环。测试中同一聚合调用三次只执行一次聚合，出现两次缓存命中；继续循环触发 TOOL_LIMIT，任务不会完整成功。

## 证据与显式分页

- 无原文/证据要求时，计划不包含 `get_evidence`。
- 默认一次 3 条、1 页；明确指定 1～10 条时使用指定页大小。
- 超过 10 条给出缩小数量或继续下一页提示，不静默截断用户要求。
- 同轮只允许计划指定的一页；修改到第 2/3 页、扩大页大小、更换无关筛选均会被拒绝。
- 用户显式指定“第 N 页”或前端把“继续查看下一页”解析为现有 JSON 查询时，可单独读取这一页。
- 前端继续沿用上轮参数中的商品、来源、数据集、日期、标注身份、aspect、sentiment、window、page_size，只把 page 增一；历史每页 5 条不会强行改成新默认 3 条。
- 受控测试导入 6 条物流负面评论，第一轮“给我看 3 条物流差评”和第二轮明确下一页各返回 3 条，六个评论 ID 无重复。没有生产数据参与。

## 语义和数值校验

保留阶段 1 的计数、分母、正向排名、人工有效意见和情感校验。新增计划完成性检查：

1. “主要方面/排名/集中”计划必须包含对应情感的维度聚合；单独证据不能满足排名请求。
2. 趋势计划必须引用 period 正确、具有 `buckets` 数组的时间序列结果；overview 或单窗口统计不能替代。
3. 原文参数的 sentiment、aspect 必须与实际有效意见一致。
4. 每个子结果按它实际被授权的商品子集、current/baseline 或自定义小窗口校验，避免阶段 1 的整范围相等检查误拒合法基线或子集。
5. 不支持结果不能携带未经校验的业务 result_ids 冒充成功结论。

数值仍由既有 SQL 和校验代码计算，模型不负责生成统计数字。

## 任务终态与前端发布保护

后端：工具后模型超时、调用预算耗尽、最终 JSON 错误或结果校验失败时，不进入成功发布。worker 保存 `result.results`、trace、计数与 error，删除失败结果中的 answer/answer_sections。新任务状态仍为原有的 failed，没有增加新的状态体系或模型降级生成。

前端：必须同时满足任务 succeeded、无 task.error、Agent status 为 ok/unsupported、已有 validation 没有 failed，才可显示完整回答和结果。旧成功任务缺少新版 validation 时仍可读取。

- failed：只显示错误、未发布说明与原重试按钮，隐藏统计/原文/报告导出。
- partial：明确显示“任务部分完成，完整回答尚未确认”，同样隐藏正文结果，允许原重试协议。
- running/queued：不提前发布 task.result。
- succeeded 但 Agent/校验无效：显示校验失败说明，不显示结果。
- 已打开的数据详情也受发布条件保护，防止任务状态更新后残留原文详情。

内部诊断仍能通过已有任务 API、任务排障能力获取；不会放入失败聊天正文。提交、Idempotency-Key、result_ids/results/scope 和重试父链的结构保持兼容。

## 正向结果最小前端适配

新增正向类型和独立结果表，显示六维名称、正面提及去重评论数、占正向观点评论比例及分母、占成功标注评论比例及分母。空分母显示“无数据”；成功标注但无正向意见时说明数据状态。旧负向趋势表和历史任务结构不变。Markdown 导出增加正向统计表和两个分母说明，JSON 仍保留完整引用。

没有调整页面导航、配色或重新设计聊天布局。

## 测试与验收记录

| 检查 | 结果 |
| --- | --- |
| `python -m unittest discover -s tests -p test_review_plan.py -q` | 10 项通过：计划、证据、双页、缓存、循环预算、语义拒绝、不支持能力、同步编排和 worker 超时诊断。 |
| `python -m unittest discover -s tests -p test_review_agent.py -q` | 16 项通过；真实 HelloAgents/OpenAI SDK + MockTransport + 隔离 SQLite，包含工具后 ReadTimeout。 |
| `python -m unittest discover -s tests -p test_review_capabilities.py -q` | 12 项通过；包含阶段 1 全部正向能力和原负向/窗口能力。 |
| `python -m unittest discover -s tests -p test_review_analytics.py -q` | 13 项通过。 |
| `python -m unittest discover -s tests -p test_reviews.py -q` | 24 项通过。 |
| `npm test -- --maxWorkers=2`（frontend） | 21 个文件、157 项通过。 |
| `npm run build`（frontend） | TypeScript + Vite 通过；仍有既有大 chunk 警告。 |
| `node tests/assistant.e2e.mjs`（frontend） | 本地模拟模型 + 隔离 API/Worker + 现有 Vite + Chrome。完整 76 张截图版本通过，页面脚本错误 0。 |

以上 Python 使用项目 `.venv/Scripts/python.exe`。完整 Agent/TestClient 测试在允许本机 socket 的执行环境运行，不再沿用阶段 0/1 的“阻塞未完成”作为本轮结论；受限环境本身仍可能卡住 asyncio。测试不是生产 PostgreSQL 性能或真实模型理解评测。

最终浏览器通过记录：[assistant-g5DEQb/acceptance.json](../../output/playwright/assistant-g5DEQb/acceptance.json)。该记录包含 1440/1024/768/390 的布局度量：documentWidth 未超视口，消息区无整体横向溢出，无额外对话框遮挡。已检查正向表格桌面/窄屏及失败/部分完成截图，表格在窄屏中保留自身横向滚动，不撑宽页面。此浏览器正向样本缺少正向观点，正确呈现空分母；非零正向数值另由 SQL/SDK 单测验证。失败/partial 页面在受控任务响应下验证，后端真实异常到 failed 的链路由 Python SDK/worker 测试验证，二者不混称生产超时端到端。

开发中保留的失败记录：首次扩展脚本未从刷新后的基础模式切回分析模式，等待提交响应超时；随后 `assistant-8GldTp` 因 Ant Design 表头与隐藏测量元素同名触发 Playwright strict mode。均已修复测试定位，没有放宽业务断言。构建曾因 unreachable unsupported 分支触发 TypeScript TS2367，删除重复分支后构建通过。原记录未清理。

## 改造前后调用次数

下面是同一隔离数据库、相同手工调用序列的受控对照，关闭计划的分支代表阶段 1 的开放工具调度行为，不是对真实供应商的速度测量。

| 场景 | 无计划分支 | 有计划分支 |
| --- | --- | --- |
| 好评主要方面，却额外尝试证据页 1、页 2 和数据质量 | 4 次模型工具尝试；实际业务查询 5 次（含 overview），证据查询 2 次 | 同一 4 次尝试仍计预算；无关工具被拦截，实际业务查询 2 次（overview + 正向聚合），证据 0 次 |
| 正常简单好评请求 | 阶段 1 理想路径本来就是 overview + 聚合 | 本轮真实离线 SDK/浏览器路径：1 次模型工具调用，2 次业务查询（含 overview），没有原文；不声称已改善原本理想路径的调用数 |
| 相同正向参数三次，包含不同商品顺序 | 已有同参缓存，但未分别记录查询数 | 1 次聚合、2 次缓存命中，result_id 不变；后续重复尝试仍触发总预算 |

本阶段主要消除额外查询和发布误导，没有测量或声称真实 token 成本、真实模型延迟、吞吐提升百分比。

## 已知边界与下一阶段

- 查询计划是可验证的有限规则，未涵盖任意复杂中文语义；未知或正向趋势/对比返回能力不足，避免错误方向结论。以后可扩展规则/工具，但应保留失败关闭和参数校验。
- 证据依旧是按数据库时间/ID 排序的匹配样本，没有新增代表性排序算法。
- 现有六维标签不能直接给出制冷/静音/安装排名或筛选；本阶段明确拒绝此类精细检索。
- 内部诊断/公开回答的全面分层、自然语言回答生成与统计压缩仍留待后续；本阶段保持确定性答案。
- 未访问真实供应商、生产商品数据或运行生产 PostgreSQL/Redis 联调。所有数据示例均为受控样本，不能作为美的空调的实际结论。

阶段 2 完成后停止，等待验收，不自动进入阶段 3。
