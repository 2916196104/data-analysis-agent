# 意图理解与多轮追问：第一阶段补缺与复验

日期：2026-11-01（Asia/Taipei）。本次在仓库已有的结构化语义解析和有限任务上下文之上修补缺口。没有新增接口必填字段、数据库迁移或依赖；没有重写布局、回答生成或评论标注。

浏览器与后端验收使用隔离 SQLite、真实 API/Worker 和受控模型。结果不能解释为真实品牌结论，也不能替代真实模型泛化能力或 PostgreSQL 集成验收。

## 审查范围与已有能力

完整阅读了 `followup.ts`、`useAgentChat.ts`、`review_semantics.py`、`review_plan.py`、`reviews.py`、`review_tools.py`，并沿调用关系检查任务参数、提交、Worker、`analysis_context.py`、`review_intent.py`、配置及对应测试。

当前调用链为：前端冻结范围和父任务引用 → 任务 API/Worker → 有限会话上下文 → 规则/受约束语义解析 → 确定性查询计划 → 注册表和只读工具 → 结果、来源及发布校验。

仓库已具备 `ReviewIntent`、`AGENT_SEMANTIC_PARSING=rules_only|rules_then_llm`、最多四轮的任务上下文、用户意图与成功结果分离，以及失败比较对象恢复。本次没有重复新增这些能力。实施前，已有意图专项 14/14 通过，但仍复现以下缺口。

## 根因、证据与处理

| 证据与触发方式 | 根因 | 修补位置与行为 |
| --- | --- | --- |
| 成功正向汇总 → 失败的“小米空调差评” → 再失败的“使用效果主要集中在哪些地方” → “那物流方面呢”，实际得到 `sentiment=all` | 只重建最近一轮请求，以缺失情感的单轮计划覆盖了更早明确的负向条件 | `review_intent.py` 按时间合并最多四轮可确定的用户请求，逐轮补全条件；从失败轮次只读取请求文本 |
| 正向汇总后问“主要集中在哪些地方”，实际计划成为全部情感观点查询 | 省略维度的重复追问未进入上下文转换，直接按独立问题规划 | 共享 `_followup_intent()` 处理唯一的汇总/观点目标，保留商品、情感、维度及细分状态；趋势等目标仍提出具体澄清 |
| “外星牌冰箱的好评主要是什么”实际查询当前空调 | 实体提取依赖空调词尾或已知品牌，未提取其他明确名称 | `review_semantics.py` 补充“名称的好评/差评/评价”等主语识别；已知品牌的错误品类（例如“小米手机”）也不能被替换成该品牌空调 |
| “那再往里挖挖呢”被前端模糊指代分支截断 | 前端在后端结构化解析之前作了泛化否决 | 有父任务引用时转交后端；缺少上下文、失败证据游标和真正的多目标歧义继续受校验约束 |
| 模型复制上轮“制冷”主题时被拒绝 | 主题只允许来自当前文本，未接受精简上下文中的已明确主题 | 精简模型输入包含主题；仅接受当前文本或同维度历史条件中的主题，拒绝编造新主题 |

## 实际文件变更

| 文件 | 本次变化 |
| --- | --- |
| `src/data_analysis_agent/agents/review_intent.py` | 共享追问条件转换；按时间合并有限用户意图；向模型传递合并后的精简条件；主题来源约束 |
| `src/data_analysis_agent/agents/review_semantics.py` | 明确商品主语识别；并列对象分句；泛指词不作为商品名称 |
| `frontend/src/features/agent/followup.ts` | 有任务引用的口语交给后端解析和具体澄清 |
| `tests/test_review_intent.py` | 新增五项测试，覆盖连续失败、重复追问、未知品类、具体澄清、历史主题来源；补充下钻状态断言 |
| `frontend/src/features/agent/followup.test.tsx` | 新增带代词口语下钻的 Hook 提交测试；校正由后端负责歧义澄清的预期 |
| `frontend/tests/pros-cons.e2e.mjs` | 扩展 A—F、连续失败、口语下钻和未知品类验收；等待实际回答与观点表渲染后截图 |
| `scripts/phase7_cases.py` | 修正过期的 mixed 用例：原预期“不支持”与本次修改前已提交的优缺点能力冲突；现明确要求两商品 × 正负方向的四项观点查询 |
| 本文档 | 记录本次审查、修改、复现和验证边界 |

`useAgentChat.ts` 已能传递失败轮次任务引用和隔离成功结果，本次无须修改。`review_plan.py`、`reviews.py`、`review_tools.py` 沿用现有计划、工具注册、数值校验与发布保护。

开始任务时已有的 `docs/review-label-queue.md`、`jobs/label_queue.py`、`jobs/label_worker.py`、`tests/test_label_queue.py` 改动保留，未纳入本次修改。

## 接口与安全边界

- 继续使用 `POST /api/v1/tasks`、`conversation_id`、`context_task_id`，没有新增 API 路由或任务字段。
- `ReviewIntent` 字段和既有结果协议不变。模型输入中的精简用户条件增加已有 `topic` 信息，不发送完整聊天历史、评论、统计或失败结果。
- 每次补充语义解析仍最多一次请求、最高 30 秒、无 SDK 重试、输入上限 16,000 UTF-8 字节；明确规则仍优先。新的口语转交可能增加一次语义请求。
- 用户意图条件与 `validated` 成功查询保持分离。失败结果中的统计、原文、总数和分页游标不被合并；成功结果仍由上下文加载器复核范围、数字和引用。
- 商品始终匹配当前数据集，当前明确要求优先。实体不存在或型号不唯一时不执行替代商品的统计。
- 模型不能输出 SQL 或自由调用工具；意图继续编译成既有查询计划，通过 Pydantic、工具范围、预算和结果校验后执行。

## 验收结果

| 检查 | 实际结果与证据 |
| --- | --- |
| 最终后端相关回归 | **148/148 通过**，包含 19 项意图专项；[日志](../output/intent-context-backend-complete.log) |
| 前端全量 | **22 个文件、202/202 通过**；[日志](../output/intent-context-regression-frontend.log) |
| 前端构建 | TypeScript/Vite 通过，保留既有大于 500 kB 的分包警告；[日志](../output/intent-context-build.log) |
| A—F 与新增浏览器场景 | **15 轮通过**，受控语义请求共 3 次，每个口语场景一次；[验收 JSON](../output/playwright/intent-phase1-0X4jpu/acceptance.json) |
| 原优缺点浏览器回归 | 四查询、品牌分组、复制、查询快照、刷新恢复和缺失商品说明通过，模型请求为 0；[验收 JSON](../output/playwright/pros-cons-994R0u/acceptance.json) |
| 视口与截图 | 1440/1024/768/390 像素均无页面或消息横向溢出，输入区在视口内；另保存 1440/1024/768 的观点明细表截图；人工检查了桌面、390px 澄清及 768px 明细表 |
| 统计与只读 | 正负口径、分子分母、日期窗口、原文引用、范围/参数校验、工具预算和失败发布回归通过；浏览器隔离业务库 SHA-256 前后相同 |
| 差异格式 | `git diff --check` 通过 |

相关后端回归覆盖 `review_intent`、`assistant_semantics`、`review_plan`、`review_context_performance`、`review_agent`、`review_capabilities`、`review_answers`、`review_compression`、`phase7_acceptance`、`review_analytics`、`review_scope`、`task_submission` 和 `public_analysis_api` 测试模块。

A/B 的好评汇总和使用效果细分保持 positive；C 保留适用目标，多目标时明确询问汇总/趋势/原文；D 在失败和刷新后恢复两品牌，品牌匹配多个型号时列出具体候选 ID；E 拒绝不存在商品和错误品类；F 沿用既有统计、证据、只读和失败保护。

浏览器证据：

- [桌面多型号澄清](../output/playwright/intent-phase1-0X4jpu/intent-1440.png)
- [390px 多型号澄清](../output/playwright/intent-phase1-0X4jpu/intent-390.png)
- [768px 观点明细](../output/playwright/intent-phase1-0X4jpu/intent-detail-768.png)

第一次受限环境中的 HTTP/浏览器运行未完成，日志保留为 `intent-context-regression-backend.log`、`intent-context-recheck-browser.log`，未计为通过。允许本机回环连接后的首次后端回归为 147 项、146 项通过，唯一失败为上述过期 mixed 预期；最终增加主题测试并修正预期后为 148/148。未将旧失败记录改写为通过。

## 复现命令

在项目根目录运行以下命令。浏览器脚本会启动并清理自己的隔离 API、Worker、模拟模型和 Vite 服务。

```powershell
$env:LABEL_EXECUTION_MODE = 'local'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_review_intent.py -v
npm --prefix frontend test -- --maxWorkers=2
npm --prefix frontend run build
$env:E2E_INTENT_PHASE1 = '1'
$env:SSLKEYLOGFILE = ''
node frontend/tests/pros-cons.e2e.mjs
```

## 限制、风险与下一阶段

- 最多四轮任务链；跨更早历史、多个主题切换或多组商品的省略指代仍可能需要明确商品、维度和分析目标。
- 失败轮次通过规则恢复可确定的用户要求。只有模型才能理解的失败请求，尚未实现对其结构化意图状态的独立持久化与再次校验。
- 商品主语规则覆盖明确名称和已有目录匹配，尚不保证任意别名、错别字、复杂并列句和所有品类的实体识别；此类表达仍应提供完整名称或 ID，不能据本次样本宣称任意自然语言均可靠。
- 复杂清除筛选、未覆盖的相对日期和跨历史主题组合尚未扩大支持。没有新增标准细分标签、同义观点聚类、正向趋势、因果或商业优先级能力。
- 本次没有调用真实供应商模型，没有连接生产 PostgreSQL，也没有运行整套工作台业务 E2E；现有原完整助手脚本的旧 UI 选择器问题未在本次处理。
- 下一阶段建议固定一套带标准意图和必要澄清标签的多轮口语评测集，分别评估实体、情感、维度、下钻和拒绝替代，再对配置模型与 PostgreSQL 做受限的真实联调。

本阶段到此停止。
