# 第二阶段：二级主题分析验收报告

日期：2026-11-01（Asia/Taipei）。实现范围为单个商品、单个一级维度、单个正向或负向情感的二级主题分析；没有进入第三阶段回答生成重构。

实现与隔离验收已完成。真实 PostgreSQL 的只读提取与原文校验通过；配置的真实模型在隔离合成观点上的归并通过。**真实评论与外部模型的联合调用未执行**：自动审批拒绝了将真实评论发送到当前外部模型的操作，理由是缺少对该具体数据外发目的地的明确授权。没有绕过该限制，也不把分开的两项验收记为联合通过。

## 审查结论

已审查 `reviews/analytics.py`、`reviews/scope.py`、`agents/review_tools.py`、`review_plan.py`、`review_intent.py`、`review_compression.py`、`review_results.py`，并追踪结果校验、任务状态、公开读取和 React 展示。

| 已有能力与证据 | 真正缺口 | 本次处理 |
| --- | --- | --- |
| `get_opinion_breakdown()` 按 `o.quote,o.sentiment` 精确分组，最多返回 10 组、前 3 组代表证据 | 同义表达分属不同组；不是二级主题统计 | 保留返回契约，新增独立二级分析工具 |
| `get_evidence()` 返回完整观点、评论 ID、原文跨度、文件 SHA-256/行号、商品、时间、annotation_id/model_version/revision_id | 原工具没有完整的观点归属清单 | 复用该有效观点查询，补充有预算的提取适配器 |
| `current_reviews_sql()` 复用成功标注和最新人工复核；PostgreSQL 的 BEGIN 由适配层转换为 REPEATABLE READ READ ONLY | 不存在主题语义归并能力；“制冷/静音/安装”的关键词只用于检索 | 不改有效观点和统计 SQL 的既有含义 |
| 第一阶段已经补全商品、情感、维度及下钻意图 | 下钻仍执行精确原文分组 | 将满足本阶段范围的下钻编译为 `get_aspect_drilldown` |

## 数据流程与接口

继续使用原任务 API、队列、HelloAgents、FastAPI、React 和 PostgreSQL/SQLite 适配层；没有数据库迁移、向量数据库、新 Agent 框架或额外依赖。

1. 前端携带冻结范围与 `context_task_id`，后端沿用第一阶段解析；“使用效果具体好在哪里”可以承接上轮正向条件，也识别其明确正向表达。
2. 计划生成 `get_aspect_drilldown(product_ids=[具体 ID], aspect=effectiveness, sentiment=positive)`。首版必须恰好一款商品，维度与情感有枚举校验；不支持把基线原文暗中切换为当前窗口。
3. `get_aspect_opinions()` 在同一只读快照中复用有效观点、总体计数和原文证据，最多读取最近 100 条匹配评论。
4. 本次执行最多纳入 200 条观点、80 种精确原文表达。相同观点文本只请求一次语义判断，后端保留并展开每条原始观点的来源，不以文本去重代替观点计数。
5. 发现请求从最多 20 种真实表达中提出最多 8 个候选主题，给出标准名称、定义及依据 ID。没有真实依据的候选目录拒绝执行。
6. 独立的归属复核请求按每批 20 种表达处理，判断是否支持候选定义，并返回原文支持跨度。每个输入 ID 恰好返回一次；可归入多个主题，或者保留 uncertain/conflicting/unclassified。
7. 后端验证目录 ID、观点 ID、重复/遗漏、状态、原文跨度、一级维度和情感，将复核结果展开为完整来源清单，重新计算所有数量、分母及比例。
8. 结果继续通过 `validate_report()`，再沿用现有结果适配、压缩和模板回答。主题分析不追加回答模型调用。

模型只看到有限观点原文、候选定义和 Schema，不接收完整聊天历史、数据库连接、SQL 或全部原始评论正文。模型响应中的额外统计字段会被拒绝。

### 新结果类型

新增工具/结果为 `get_aspect_drilldown` / `aspect_drilldown`。`ReviewIntent.query_type` 增加可选枚举值 `aspect_drilldown`，既有 `sub_intent=drill_down` 在单维度、单向情感条件下也可编译为该工具；旧的中性/全部情感、原文检索和多商品优缺点工具保留。

结果保存：

- `topics`：主题 ID、标准名称、定义、所属维度/情感、去重评论数、观点数、分母/占比、全部成员观点 ID/评论 ID、代表依据 ID。
- `opinions/items`：冻结的原始观点、评论、标注版本与精确引用；模型无权改写。
- `catalog/assignments`：候选主题目录和复核清单，供结果重新验证、计算和审计。
- `coverage`：纳入、已复核、已归类、未归类、未处理观点数，以及模糊/矛盾/未归类原因计数。
- `analysis_status`：complete、partial、failed、insufficient_data；`population_mode` 区分 full 和 sample。
- `grouping_version/grouping_model/model_calls/duration_ms`：归并版本、模型、调用数与耗时。

主题 ID 由 `aspect-topics-v1 + 一级维度 + 情感 + NFKC 规范化名称` 生成稳定哈希，相同版本和标准名称不依赖返回顺序。动态主题改名会改变 ID；本阶段没有承诺跨模型、跨命名变化的固定分类体系。

### 统计口径

- 每个主题的 `review_count` 按评论 ID 去重，`opinion_count` 按真实观点条目计算。同一条评论或观点可涉及多个主题，主题数量不能相加作为总评论数。
- `review_rate = 主题去重评论数 / selected_reviews`，显式保存 `review_denominator`。
- full 模式下纳入全部匹配观点，分母就是该维度、该情感的匹配评论数。仍不代表主题体系已经穷尽，未归类状态不会被强行分配。
- 超过提取/表达预算时为 sample，分母只代表实际纳入样本。样本按最近评论及观点顺序截取，为非随机探索；不能将样本比例外推到总体。
- `opinion_coverage = classified_opinions / selected_opinions`；`population_opinion_coverage = evaluated_opinions / total_opinions`。这两个值分别表示归类覆盖和总体观点的处理覆盖。

### 发布与异常

候选发现和独立归属复核提供语义判断，后端验证来源与计算；无法用计算证明任意自然语言语义必然正确。因此主题明确标为 **模型归并候选、未经人工确认**，不把它们当作标准人工标签。

部分批次失败时，只保留通过检查的归属和统计快照，任务终态为 partial；既有公开读取和聊天页不将其发布为完整主题结论，也不会让该任务的数据成为下一轮可信结果。失败轮次的明确用户意图仍按第一阶段规则保留。

全部归属失败或候选目录无效时任务 failed，原始观点及失败码保留用于排障，没有成功正文。空观点不调用模型，返回明确的数据不足说明。没有同时被真实来源和归属复核支持的主题不出现在主题表中。

## 典型输出

以下来自**真实配置模型处理隔离合成评论**的实际输出，不是美的商品统计。

| 模型归并候选 | 去重评论数 | 观点数 | 分母 | 占比 |
| --- | ---: | ---: | ---: | ---: |
| 制冷速度 | 2 | 3 | 4 条匹配评论 | 50.0% |
| 运行噪声 | 1 | 1 | 4 条匹配评论 | 25.0% |

“降温很快”和“制冷速度快”被归到“制冷速度”，同一条评论含有这两条观点时主题评论数仍只计一次。同一条评论还包含“运行声音小”，因此能同时进入另一主题。6 条观点中已归类 4 条，另有 1 条模糊观点和 1 条矛盾观点；覆盖率为 4/6，未强制归类。

该次稳定主题 ID 分别为 `topic:77b94ae56ead3b74b488`、`topic:243db223daa61ce79e61`。原文和每条成员来源在[真实模型合成验收快照](../output/topics-real-model-synthetic/report.json)中保存。

## 实际验证

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 后端相关回归 | **154/154 通过**，包括 6 项二级主题测试及原意图/统计/证据/预算/公开读取/任务回归 | [日志](../output/topics-backend-complete.log) |
| 前端全量 | **23 个文件、205/205 通过**；新增 3 项主题展示/追问/部分结果保护测试 | [日志](../output/topics-frontend-final.log) |
| TypeScript/Vite | 通过；保留既有大于 500 kB 分包提示 | [日志](../output/topics-build-verified.log) |
| 二级主题浏览器 | 正向追问、同义归并、负向物流、未归类、刷新零重算、业务库哈希不变通过；四种宽度的答案和两行主题表均实际渲染 | [验收 JSON](../output/playwright/topics-phase2-1bR9q1/acceptance.json) |
| 第一阶段浏览器回归 | 15 轮上下文、连续失败、商品不存在、多型号澄清等通过 | [验收 JSON](../output/playwright/intent-phase1-6VkZUP/acceptance.json) |
| 原优缺点浏览器 | 多商品分组、复制、快照、刷新恢复通过，模型请求 0 | [验收 JSON](../output/playwright/pros-cons-BwxUaX/acceptance.json) |
| 真实配置模型 | `Pro/deepseek-ai/DeepSeek-V3.2`；只输入隔离合成观点，2 次主题请求，**22.934 秒**，验证 passed，输出见上表 | [日志](../output/topics-real-model-synthetic.log) |
| 真实 PostgreSQL | 只读连接，纯本机提取/校验，模型请求 0；本次提取 **0.885 秒** | [冻结范围及结果](../output/topics-real-postgres-readonly/extraction.json) |
| 差异格式 | `git diff --check` 通过 | 当前工作区检查 |

真实 PostgreSQL 验收绑定：商品 `100013588053`，`reviews_csv / reviews-csv`，UTC 日期 **2021-09-11—2022-08-26**，标注身份指纹 `profile:1b58dc189d7d3ea6`。使用效果正向集合为 **308 条去重评论、667 条观点**；提取最近 100 条评论、186 条观点，逐项核对维度、情感、原文跨度和文件引用，全部通过。**没有为这 308 条真实评论生成或发布二级主题结论。**

浏览器使用本机模拟主题模型，视口为 1440/1024/768/390px，均无页面横向溢出、输入区位于视口内，浏览器错误 0。已人工检查桌面及窄屏答案，并保留每种宽度的主题表截图：

- [桌面答案](../output/playwright/topics-phase2-1bR9q1/topics-1440.png)
- [768px 主题表](../output/playwright/topics-phase2-1bR9q1/topics-table-768.png)
- [390px 主题表](../output/playwright/topics-phase2-1bR9q1/topics-table-390.png)

测试还覆盖了伪造候选依据、编造观点/主题 ID、篡改计数或原文、模型输出额外统计字段、稳定主题 ID、批次超时、部分任务公开视图为空及上下文回退到成功任务。A—F 均有专项覆盖；G 由现有回归与两套原流程浏览器验收覆盖。

首次新专项的夹具字段错误、首次回归中需迁移的新工具预期以及 TypeScript 测试夹具缺少必填字段均已修复。失败日志仍保留，不计为通过；没有通过删除数值/范围/状态断言取得验收结果。

## 修改文件

| 范围 | 文件与职责 |
| --- | --- |
| 新语义模块 | `src/data_analysis_agent/agents/review_topics.py`：候选发现、独立归属复核、预算、完整清单、重算与验证 |
| 只读提取 | `src/data_analysis_agent/reviews/analytics.py`：新增 `get_aspect_opinions`，复用原有效观点查询 |
| 意图与计划 | `review_intent.py`、`review_plan.py`、`review_semantics.py`、`review_prompts.py`：新增类型和明确口语情感，下钻路由 |
| 工具与执行 | `review_tools.py`、`reviews.py`、`jobs/worker.py`：参数范围、执行和模型计数、partial/failed 终态 |
| 校验与必要展示适配 | `review_validation.py`、`review_results.py`、`review_compression.py`、`review_answer_facts.py`、`review_answers.py`：统一验证、主题事实及模板；不重写回答系统 |
| React | `types.ts`、`AgentResults.tsx`、`agent-report.ts`、`ExtendedResults.tsx`、`PublicAnswerView.tsx`、`followup.ts`、`report-export.ts`：类型、主题表、限制、追问、导出及部分结果保护；没有布局/CSS 重构 |
| 测试 | `tests/test_review_topics.py`、`tests/topic_fixture.py`、`topics.test.tsx`、既有意图/计划测试、`scripts/phase7_cases.py`、`frontend/tests/pros-cons.e2e.mjs` |
| 验收脚本 | `scripts/review_topics_live.py`：真实库只读提取；`scripts/review_topics_model_probe.py`：只用合成 SQLite 验证真实模型 |
| 报告 | 本文及上述验收产物 |

## 复现

在项目根目录运行：

```powershell
$env:LABEL_EXECUTION_MODE = 'local'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_review_topics.py -v
npm --prefix frontend test -- --maxWorkers=2
npm --prefix frontend run build
$env:E2E_TOPIC_PHASE2 = '1'
$env:SSLKEYLOGFILE = ''
node frontend/tests/pros-cons.e2e.mjs
```

纯只读真实库检查不调用模型：

```powershell
.\.venv\Scripts\python.exe -m scripts.review_topics_live --extract-only --output output/topics-real-postgres-readonly
```

真实模型的隔离合成数据检查不连接真实数据库，会消耗模型额度：

```powershell
.\.venv\Scripts\python.exe -m scripts.review_topics_model_probe --output output/topics-real-model-synthetic
```

## 风险、成本与剩余限制

- 单次最多 5 次主题请求；每次输入不超过 16,000 UTF-8 字节，输出上限 3,000 tokens，无 SDK 重试；单请求最高 20 秒，总模型阶段按 60 秒剩余预算约束。没有统计供应商账单或建立吞吐/并发基线。
- 仅发现有限表达中的候选主题，再对纳入表达复核，不能保证穷尽主题或任意语义归并准确。当前真实模型只做了一个合成样例，不是准确率评测。
- 没有人工确认的固定二级标签体系；主题名变化会改变 ID。尚未实现跨次稳定分类映射、版本化持久缓存、离线全量主题归类和主题漂移监控。
- 只支持单商品、单维度、单向情感；没有新增多商品二级主题比较、因果判断、商业优先级或正向趋势。
- 预算采样按最近评论及观点顺序，可能存在时间和表达偏差；同一原文复用同一语义判断，缺少完整上下文的短观点仍可能误判，必须保留模型候选和未归类说明。
- 一级维度与情感沿用已有有效标注，不因主题名称重新划分；上游标注错误会传递到本阶段，本阶段不重新标注评论。
- 主题分析期间保持原只读快照，模型延迟会延长事务存活时间；没有做生产压力验收。
- 真实 PostgreSQL 观点向 `api.siliconflow.cn` 当前配置模型外发的联合验收被自动审批拒绝，需明确授权后才能补充。当前已验证结果不足以声称真实商品主题识别准确或生产全链路已通过。

建议后续先建立人工标注的二级主题及不确定状态评测集，再评估真实数据上的准确率、延迟和成本，之后决定离线归类/持久缓存。本阶段停止，不自动进入第三阶段。
