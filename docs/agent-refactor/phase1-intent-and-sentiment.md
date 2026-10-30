# 阶段 1：商品评论 Agent 正负面语义路由与正向统计能力

实施日期：2026-10-30（Asia/Taipei）  
基础审查：[phase0-audit.md](phase0-audit.md)  
本阶段范围：正向维度聚合、正向统计工具、轻量情感意图路由、确定性回答和结果校验。  
本阶段未修改任务提交协议、数据库表结构、评论/标注内容、`ChatMessage.tsx` 或 `AgentResults.tsx`，未进入阶段 2～7。

## 修改文件

| 文件 | 修改内容 |
| --- | --- |
| `src/data_analysis_agent/reviews/analytics.py` | 在现有 `_summaries()` SQL 上增加正向评论和六维度正向评论计数；新增 `get_positive_aspects()`；保留旧负向输出形状，通过 `include_positive=True` 只对新工具返回正向字段。 |
| `src/data_analysis_agent/agents/review_tools.py` | 注册 `get_positive_aspects`；将正向/负向意图传入工具上下文；正向问题拒绝负向统计工具，负向问题拒绝正向工具，并校验证据情感参数。 |
| `src/data_analysis_agent/agents/review_prompts.py` | 提示词升级为 `review-agent-v4`；新增轻量 `detect_sentiment_intent()`，处理正向、负向和“不是问差评，我想知道好评”等转折表达。 |
| `src/data_analysis_agent/agents/reviews.py` | 将意图标签加入 Agent prompt；增加正向结果的确定性摘要；把意图和绑定范围传给结果校验。 |
| `src/data_analysis_agent/agents/review_validation.py` | 校验正向排名、去重计数、两个占比分母、零分母状态、范围身份和正负情感结果匹配。 |
| `tests/test_review_capabilities.py` | 增加正向 SQL、人工修订、零分母、错误计数/范围、工具防误用测试。 |
| `tests/test_review_agent.py` | 增加情感意图和正向工具链路测试，更新工具清单及 prompt 版本断言。完整 HelloAgents 用例仍受当前 Windows 异步环境阻塞。 |
| `frontend/tests/assistant.e2e.mjs` | 仅将导出内容中的 prompt 版本断言更新为 `review-agent-v4`。未修改 React 组件。 |

## 正向统计工具

新增工具：`get_positive_aspects`。

它沿用 `analysis_scope()`、`current_reviews_sql()`、`_snapshot()` 和 `effective_opinions`，没有新建 SQL 框架或数据表。`effective_opinions` 只包含当前模型标注或最新人工复核的有效观点，且只统计 `processing_status='success'` 的评论。

工具结果的主要字段如下：

```json
{
  "analysis_type": "positive_aspects",
  "sentiment": "positive",
  "data_status": "ok",
  "scope": {"model": "...", "prompt_version": "...", "date_from": "...", "date_to": "..."},
  "summary": {
    "review_count": 0,
    "eligible_text_reviews": 0,
    "successful_reviews": 0,
    "positive_review_count": 0
  },
  "sample_size": {
    "review_count": 0,
    "eligible_text_reviews": 0,
    "successful_reviews": 0,
    "positive_review_count": 0
  },
  "aspects": [
    {
      "rank": 1,
      "aspect": "effectiveness",
      "aspect_label": "使用效果",
      "positive_review_count": 0,
      "positive_rate": null,
      "positive_success_rate": null,
      "positive_rate_denominator": 0,
      "positive_success_rate_denominator": 0
    }
  ]
}
```

SQL 计数全部使用 `COUNT(DISTINCT CASE WHEN ... THEN c.id END)`：

- `positive_review_count`：当前范围内至少有一个正向观点的去重评论数。
- `positive_<aspect>`：指定六维度中有正向观点的去重评论数。
- 一条评论可以同时计入多个维度，但在每个维度内只计一次。
- `positive_rate = positive_review_count_of_aspect / positive_review_count`，表示正向观点评论内部的维度提及占比。
- `positive_success_rate = positive_review_count_of_aspect / successful_reviews`，表示该维度正向提及占全部成功标注评论的比例。
- 两个分母分别通过字段名和 `definitions` 明确返回；任何分母为零时返回 `null`。

`data_status` 有三个状态：

- `ok`：存在成功标注且至少存在一个正向观点。
- `no_positive_mentions`：有成功标注，但没有有效正向观点。
- `insufficient_data`：成功标注数为零，无法计算正向观点比例。

六个维度仍是现有 `quality`、`effectiveness`、`price`、`packaging`、`logistics`、`service`，没有从评论文本或星级推断制冷、静音、安装等新维度。

## 兼容处理

现有负向统计的公开结构保持不变。`_summary()` 默认仍只返回原有负向字段；只有正向工具显式传入 `include_positive=True` 时，才在新结果中加入正向计数和正向维度字段。因此既有趋势、商品对比、数据质量、窗口对比、前端负向表格和历史结果不会因为新增 SQL 列而改变 JSON 形状。

正向结果仍使用现有 `result_id`、`kind`、`parameters`、`data` 结构，并写入现有 Agent `results`。旧任务没有 `positive_aspects` 结果时继续按原结构读取；prompt 身份升级为 `review-agent-v4` 后，新任务会使用新路由，历史 `review-agent-v3` 结果仍保留可读性。没有修改任务请求必填字段或数据库 schema。

## 意图路由

`detect_sentiment_intent()` 是轻量、可测试的规则层，不引入新的 Agent 框架。它先识别带有完整业务含义的正向/负向表达，再检查否定前缀和转折后的目标：

| 示例 | 意图 |
| --- | --- |
| “好评主要集中在哪些方面” | `positive` |
| “哪些方面评价最好” | `positive` |
| “差评和投诉主要是什么” | `negative` |
| “哪些方面存在问题” | `negative` |
| “不是问差评，我想知道好评” | `positive` |
| “按月查看包装趋势” | `unknown`，交给趋势工具 |

识别出的意图以 `<query_intent>` 注入模型 prompt。工具层再做硬校验：

- `positive` 问题不能调用 `get_trend`、`get_aspect_trend`、`compare_products`、`get_top_issues` 或 `compare_time_windows` 作为正向统计；必须改用 `get_positive_aspects`。
- `negative` 问题不能调用 `get_positive_aspects`。
- 正向问题调用 `get_evidence` 时必须传 `sentiment="positive"`；负向问题必须传 `sentiment="negative"`。
- 不明确的情感方向不会被强行改成正向或负向；普通趋势、质量、星级和明确情感证据仍可按对应工具处理。

任务结果同时保留 `sentiment_intent`（`positive`/`negative`）和兼容业务命名的 `analysis_intent`（`positive_aspects`/`negative_aspects`），两者都是新增可选字段。

最终校验还会检查：正向意图至少引用 `positive_aspects` 或正向证据，负向意图至少引用负向统计结果或负向证据。背景结果中仍可能包含既有负向字段，但这些字段不会替代正向核心结果。

## 最终回答结构

`grounded_answer()` 新增 `positive_aspects` 分支，继续使用确定性模板，不增加第二次 LLM 调用：

1. 先说明范围评论数、成功标注数和至少包含正向观点的评论数。
2. 按正向提及评论数排序，默认只展示前三个非零主要维度。
3. 对每个维度同时展示正向观点评论内部占比和成功标注占比，并写明两个分母。
4. 同一评论可命中多个维度，回答中明确“按评论 ID 去重”。
5. 没有成功标注或没有正向观点时，只返回数据不足/暂无正向观点说明，不把空值写成 0%。

正向回答不会把负面占比作为主要结论，也不会自动追加多页原文。完整结构化结果仍保存在现有 `results` 中，供既有审计和导出能力使用。

## 测试结果

### 通过

| 命令 | 结果 |
| --- | --- |
| `..\\.venv\\Scripts\\python.exe -m unittest test_review_capabilities.CapabilityTests -q`（在 `tests/`） | 11/11 通过；包含正向去重、多个维度、人工复核替换、零分母、错误计数/范围、正负向工具防误用和既有负向能力。 |
| `..\\.venv\\Scripts\\python.exe -m unittest test_review_agent.ReviewAgentTests.test_scoped_tools_reject_unknown_fields_products_and_write_tools_and_reuse_results test_review_agent.ReviewAgentTests.test_product_schema_lists_only_complete_ids_in_bound_scope test_review_agent.ReviewAgentTests.test_sentiment_intent_handles_positive_negative_and_correction -q`（在 `tests/`） | 3/3 通过。 |
| `.venv\\Scripts\\python.exe -m unittest discover -s tests -p test_review_analytics.py -q` | 13/13 通过。 |
| `.venv\\Scripts\\python.exe -m unittest discover -s tests -p test_reviews.py -q` | 24/24 通过。 |
| `npm test -- --maxWorkers=2`（`frontend/`） | 21 个文件、151 项通过。 |
| `npm run build`（`frontend/`） | TypeScript 和 Vite 构建通过；保留既有大于 500 kB 的 chunk 警告。 |
| `.venv\\Scripts\\python.exe -m py_compile ...` | 本阶段 Python 源码和新增测试编译通过。 |

### 受环境限制

| 验证 | 结果 |
| --- | --- |
| `test_review_agent.py` 完整 HelloAgents/SDK 模拟链路 | 已重新尝试；测试在首个真实 Agent 模型调用处超过有限等待，没有结果输出，随后停止。当前 Windows 环境的 TestClient/asyncio 本地套接字问题与阶段 0 相同，不能宣称完整集成通过。 |
| 新增正向 Agent 端到端测试 | 正向工具、SQL、回答和校验的测试代码已加入；直接运行 HelloAgents 的测试同样受上述异步阻塞。工具层防误用和统计/回答底层测试已通过。 |
| 浏览器 1440/1024/768 和真实商品数据 | 本阶段没有修改 React 页面，也未重新启动服务做浏览器验收；当前环境缺少可用 Playwright/agent-browser 和完整外部服务条件。受控 SQLite 测试数据不是生产商品结果。 |

## 核心问题的受控测试结果

测试 fixture 包含 6 条评论、3 条成功标注评论和现有六维正向观点。`get_positive_aspects()` 返回：

- 范围评论数：6。
- 成功标注评论数：3。
- 至少一个正向观点的去重评论数：3。
- 主要维度：`effectiveness` 2 条；`logistics`、`price`、`quality` 各 1 条。
- 使用效果正向观点内部占比：2/3，即 0.666667。
- 使用效果占成功标注评论比例：2/3，即 0.666667。
- 一条评论包含多个正向维度时，各维度分别计数，但正向评论总数仍按评论 ID 去重。

该结果来自隔离测试数据库，不代表美的空调或其他生产商品的实际统计。生产问题“美的空调好评主要集中在哪些方面？”现在会在明确范围和标注版本下优先选择 `get_positive_aspects`；当前环境未完成真实模型供应商和浏览器端到端复验，因此不宣称生产数据回答已通过。

## 下一阶段需要处理的问题

- 失败任务仍可能携带部分 Agent 结果并被前端展示，这属于阶段 0 已确认的终态问题，本阶段没有修改 `ChatMessage.tsx` 或任务状态协议。
- 结果压缩、公开回答和内部 trace 仍未拆分；本阶段只增加正向确定性模板。
- 重复工具调用的硬限制、多页证据预算和统一响应组装仍留待后续阶段。
- 若下一阶段需要在 UI 中展示 `positive_aspects` 表格，应先增加向后兼容的前端结果类型/展示适配，再进行页面验收。

本阶段到此停止，等待验收后再进入下一阶段。
