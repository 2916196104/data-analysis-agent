# AI 助手绑定当前商品范围：修复与验收

日期：2026-11-01（Asia/Taipei）。基线提交：`2b91065`。

本次修复已完成。评论分析默认使用页面本轮选择的数据库商品 ID；简称与自然语言前缀不会重新猜测并替换商品。明确提及范围外的单款商品时，在统计前提示用户先在页面确认切换；明确的多商品比较沿用已有能力。没有改数据库、标注、统计分母、SQL 权限或 API 字段。

真实 PostgreSQL 验证只执行只读查询，外部模型请求被禁止。浏览器验收使用隔离 SQLite、真实 API/Worker/Vite/Chromium 和受控模型，页面中的 DEMO/SIMULATED 数据不能作为真实商品结论。

## 实际执行过程与根因

范围从 `App.tsx` 的全局 `AnalysisDraft` 进入 `analysisParameters()`，生成 `source`、`dataset_version`、`product_ids`、日期及 `model/base_url/prompt_version` 标注身份；`useAgentChat.send()` 在异步读取历史之前用 `structuredClone()` 冻结本轮选择。任务通过已有 `POST /api/v1/tasks` 提交，Worker 用 `load_analysis_context()` 重新读取并验证有限父任务链。

后端 `run_review_agent()` 读取当前来源和数据集的商品目录，调用 `resolve_review_request()`，由 `product_mentions()`、目录匹配与 `resolve_question_scope()` 绑定对象，再由 `build_query_plan()` 编译计划。只读工具注册表仍检查数据集、已选商品、参数、预算和结果来源。

问题出在绑定后的实体处理：旧正则把“用户对于美的空调”识别为名称，且在“空调”处截断，丢掉后面的“风酷”。旧简称匹配也不能处理目录中的英文品牌括号、系列、长型号修饰文字及系列/品类顺序。随后实体匹配否决了前端已经传来的有效 ID。另有普通明确商品、历史条件和模型实体重新替换页面范围的路径，以及目录刷新将失效选择过滤成空数组的风险。

## 文件与具体修改

| 文件 | 本次逻辑 |
| --- | --- |
| `frontend/src/analysis-draft.ts` | 校验所选 ID 非空且存在于当前来源/版本的真实目录；失效或跨数据集 ID 不生成可提交参数；复制 ID 数组 |
| `frontend/src/App.tsx` | 同一数据集目录刷新时保留失效选择，交给范围校验要求重选，避免静默过滤成“全部商品” |
| `frontend/src/features/agent/followup.ts` | 普通新问题及确定性追问优先保留页面商品 ID；上轮比较曾扩大过对象时，不自动把扩大后的对象带入新问题；历史显式引用仍沿用被引用轮次 |
| `agents/review_semantics.py` | 去除“用户对于”等询问前缀；保留品类后的系列；使用目录片段匹配简称，不配置品牌特例；新增统一 `bind_product_ids()`，越界单款请求提示确认切换；同系列多型号列出具体候选 ID |
| `agents/review_intent.py` | 复用统一绑定，禁止模型修改已验证 ID 集合；继承适用情感、维度和主题；支持“这些好评具体反映什么”；显式比较保留情感与细分主题限制，并兼容原有物流比较后的证据补查 |
| `agents/reviews.py` | 在语义解析前验证传入商品确实属于当前数据集，阻止用问题文本“修复”失效 ID 后继续统计 |
| `tests/test_review_product_binding.py` | 新增 13 项，覆盖默认范围、简称/前缀、未知系列、其他商品、明确比较、多型号歧义、模型改 ID、三轮继承、商品/版本切换、旧历史及无效 ID |
| `tests/test_review_intent.py`、`tests/test_assistant_semantics.py` | 更新原先要求“另一款商品直接覆盖页面”的预期；同商品失败轮次继续验证情感与趋势继承；切换后模型仍不得用旧实体覆盖新对象 |
| `frontend/src/analysis-draft.test.ts`、`frontend/src/features/agent/followup.test.tsx` | 增加失效/空白/跨数据集 ID、数组复制及上轮扩展范围不污染本轮选择测试 |
| `frontend/tests/pros-cons.e2e.mjs` | 增加商品范围验收模式，真实页面提交、切换商品、刷新历史、核验执行 ID、截图；旧意图模式的失败场景改在已选商品内执行 |

表中后端 `agents/` 路径均位于 `src/data_analysis_agent/agents/`。`review_plan.py`、任务协议和上下文加载器已沿调用链检查，继续使用既有实现。

页面原有“商品留空表示全部”仍适用于明确的全数据集选择。无效的单商品 ID 不再被目录刷新变成空数组。工具结果中的 `parameters.product_ids=[]` 是既有的“继承本轮绑定范围”协议；验收以实际 `data.scope.product_ids` 核对执行对象，不将该工具参数误判为全库查询。

## 解析前后与真实执行 ID

从现有 PostgreSQL 目录确认，风酷系列有四款可匹配商品：`100013588053`、`100013588055`、`100019881984`、`100025520132`。本次单选验证使用 `100013588053`，其完整标题包含 `美的(Midea) 1.5匹 风酷 新一级能效 … KFR-35GW/N8XHC1`。

| 问题 | 修复前实体/行为 | 修复后实际结果 |
| --- | --- | --- |
| 制冷好评主要集中在哪些方面？ | 无显式实体，已有默认范围 | 无需补名称，查询 `100013588053` |
| 美的风酷空调的制冷好评主要集中在哪些方面？ | 抽取“美的风酷空调”；旧匹配不适应真实长标题 | 目录匹配当前已选型号，查询 `100013588053` |
| 用户对于美的空调风酷的制冷好评主要集中在哪些方面？ | 抽取“用户对于美的空调”和“美的空调”，前者导致未找到 | 抽取“美的空调风酷”，查询 `100013588053` |
| 当前选 A，明确要求范围外的单款 B | 原逻辑可以直接替换为 B | 提示先确认切换，`query_executions=0` |
| 明确要求不存在的品牌/系列/品类 | 存在提取遗漏后回退选择的风险 | 返回具体未找到名称，`query_executions=0` |

三种真实问法均保留 `aspect=effectiveness`、`sentiment=positive`、`topic=制冷`，执行 `get_opinion_breakdown`，没有返回全部一级好评排名。标注身份来自库内现有 `Pro/deepseek-ai/DeepSeek-V3.2` / `review-opinions-v1-…` 版本；日期为该商品的 `2021-09-11` 至 `2022-08-26`。本次匹配 168 条评论，三次均 `validation=passed`，连接的 `transaction_read_only=on`。这只是本次版本、日期和检索条件下的数量。

多选上述四款再使用同一简称时，会返回四个候选 ID，要求明确具体商品，不能随意挑其中一款。

证据及复现脚本：[真实查询结果](../output/product-binding-live.json)、[只读验证脚本](../output/product-binding-live-probe.py)、[执行日志](../output/product-binding-live.log)。脚本中的旧解析来自本次运行时的基线 HEAD；查看历史实现时应固定到 `2b91065`。

## 商品切换、多轮及回归结果

| 验证 | 结果 | 证据 |
| --- | --- | --- |
| 最终后端相关回归 | **198/198 通过** | [日志](../output/product-binding-backend-final.log) |
| 证据补查与商品绑定专项复验 | **22/22 通过** | [日志](../output/product-binding-evidence-recheck.log) |
| 前端全量 | **23 个文件，208/208 通过** | [日志](../output/product-binding-frontend-final.log) |
| TypeScript/Vite 构建 | 通过；保留既有大于 500 kB 的分包警告 | [日志](../output/product-binding-build-final.log) |
| 商品范围浏览器流程 | **9 轮通过**，无页面错误 | [验收 JSON](../output/playwright/product-scope-TkaAKg/acceptance.json) |
| 原意图与追问浏览器回归 | **15 轮通过**，语义补全请求 3 次 | [验收 JSON](../output/playwright/intent-phase1-dj1HKj/acceptance.json) |
| 真实 PostgreSQL | 三种问法的真实查询 ID、主题、情感及结果校验一致 | [结果](../output/product-binding-live.json) |
| 差异格式 | `git diff --check` 通过 | 本次最终检查 |

后端回归包含计划、Agent、能力、统计、范围、证据评估及自动补查、压缩、回答、回答稳定性、上下文、任务提交、公开接口和阶段七验收。统计分母、日期边界、标注身份隔离、只读访问、工具预算、失败发布及伪造证据保护继续通过。

“好评主要集中在哪些方面？”→“制冷方面呢？”→“这些好评具体反映什么？”继承相同商品、正向情感和制冷主题。手动从 DEMO-A 切换到 DEMO-B 后，新问题实际查询 DEMO-B，提交请求不携带旧 `context_task_id`；旧回答及原执行范围在切换和刷新后保持一致。后端同时验证了数据集、模型和标注版本变化时不加载旧上下文。

已检查 [1440px](../output/playwright/product-scope-TkaAKg/scope-1440.png)、[1024px](../output/playwright/product-scope-TkaAKg/scope-1024.png)、[768px](../output/playwright/product-scope-TkaAKg/scope-768.png) 截图。页面和聊天区无横向溢出，输入区留在视口内；观点表已实际渲染。768px 表格沿用现有容器内横向滚动。通过实际范围编辑控件完成商品切换；本次查询没有趋势图，不将图表计为视觉验收项目。隔离业务库的 SHA-256 前后相同。

初始受限环境的本机服务连接未完成；浏览器还先后暴露了测试中的多型号误设预期、下拉控件操作方式和缺省字段预期问题，修正后重新完整运行。比较保护曾影响物流证据补查，已修复并由最终 198 项回归覆盖。这些先前运行未计为通过。

## 证据、结论与调用路径

| 证据 | 可核对观察 | 对应结论和位置 |
| --- | --- | --- |
| E1：真实查询 JSON 及基线解析 | 前缀问法旧实体含“用户对于”，新实体保留系列，实际 ID 一致 | F1：根因在实体提取/目录简称匹配；修复 `review_semantics.py` |
| E2：商品绑定测试及浏览器任务快照 | 越界单款零查询；新选择查询新 ID；旧历史范围不变 | F2：页面本轮选择优先；修复前端参数与追问、后端统一绑定 |
| E3：最终后端回归与业务库哈希 | 统计/范围/证据/只读保护通过，隔离业务库内容未变 | F3：沿用现有统计和只读工具，不改标注及数据库口径 |

调用路径为：全局草稿 → 参数校验与冻结 → 任务 API/Worker → 同范围有限上下文 → 目录实体绑定 → 既有查询计划 → 范围受限的只读工具 → 结果和证据校验 → 回答及固定历史范围。

## 复现与仍有边界

在项目根目录运行；HTTP 与浏览器测试需要允许本机回环连接。真实库脚本需要现有 `.env` 中的数据库配置，不输出凭据，不调用真实供应商模型。

```powershell
$env:PYTHONPATH = 'tests;src'
$env:LABEL_EXECUTION_MODE = 'local'
.\.venv\Scripts\python.exe -m unittest test_review_product_binding test_review_intent test_assistant_semantics test_review_plan test_review_agent test_review_capabilities test_review_analytics test_review_scope test_review_evidence test_review_evidence_loop test_review_context_performance test_review_compression test_review_answers test_review_answer_stability test_task_submission test_public_analysis_api test_phase7_acceptance -v
npm.cmd --prefix frontend test -- --maxWorkers=2
npm.cmd --prefix frontend run build
$env:E2E_PRODUCT_SCOPE = '1'
$env:SSLKEYLOGFILE = ''
node frontend/tests/pros-cons.e2e.mjs
$env:E2E_PRODUCT_SCOPE = '0'
$env:E2E_INTENT_PHASE1 = '1'
node frontend/tests/pros-cons.e2e.mjs
.\.venv\Scripts\python.exe output/product-binding-live-probe.py
```

- 多个已选型号匹配同一简称、多个历史目标的省略指代仍需具体澄清；最多恢复四轮任务链。
- 未增加通用别名字典、错别字纠正或任意品类的完整实体识别保证。复杂并列句、否定改选及未覆盖别名仍应提供完整目录名称或 ID。
- 明确的范围外单商品需要在现有范围编辑器中选择并应用；没有新增一句“确认”自动切换商品的协议。
- 正向趋势、正向商品比较和多商品二级主题的既有限制保留；不能改查负向或上级维度来冒充支持。
- 未验证真实供应商模型的口语泛化、长时并发压力或整套工作台 E2E。真实 PostgreSQL 只验证了这里记录的只读商品查询；浏览器数据为隔离模拟数据。

本任务到此停止。
