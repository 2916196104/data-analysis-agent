# 通用维度内容下钻修复与验收

已修复“客服方面有什么问题？”被当作一级数量查询的问题。六个合法维度共用内容意图判据，优先执行现有本地观点查询；已有证据补查、范围约束和发布校验继续生效。未修改数据库、标注、统计口径、API 协议或前端组件。

真实数据只读核查已完成，外部模型调用为 0。本次真实结果按相同观点原文分组，**未做同义表达的语义归并，不能称为完整问题类型排名**。以下浏览器验收使用隔离合成数据，不冒充真实商品结果。

## 1. 根因与证据

核查原任务，而非仅用问句推测：

| 字段 | 原始值 |
|---|---|
| 第二轮任务 | `33bea0bbea6b42368badf7d21fe75e8b` |
| 原始 question | 客服方面有什么问题？ |
| conversation_id | `3c833791-5bbc-4d1e-9f85-9fff245dc3c0` |
| context_task_id | `4ae73d5865c64c318caeda1ed8559660` |
| 父任务问题 | 美的空调的差评主要集中在什么地方？ |
| 解析方式 | rules，context_turns = 1，意图模型调用 = 0 |

会话与父任务关联正确，商品及原始日期正确恢复。问题发生在内容意图识别与证据层级，而不是父任务丢失。

| 证据 | 发现 | 修复位置 |
|---|---|---|
| E1：历史任务 `user_intent.query_type=aspect_summary`，`sub_intent=overview` | 共享语义规则漏掉“有什么问题”“投诉什么”“主要抱怨什么”等内容表达，已接受的错误汇总计划不会再走语义模型兜底 | [review_semantics.py](../src/data_analysis_agent/agents/review_semantics.py)、[review_prompts.py](../src/data_analysis_agent/agents/review_prompts.py) |
| E1：仅要求并执行 `get_top_issues(aspect=service)` | 计划只获取一级计数，没有观点内容可供回答 | [review_plan.py](../src/data_analysis_agent/agents/review_plan.py)、[review_intent.py](../src/data_analysis_agent/agents/review_intent.py) |
| E1：assessment 为 `facts_sufficient/statistics`，completion 为 `not_needed` | 证据判断及补查入口也遗漏这类表达，未识别内容不足 | [review_evidence.py](../src/data_analysis_agent/agents/review_evidence.py)、[review_evidence_loop.py](../src/data_analysis_agent/agents/review_evidence_loop.py) |
| E1：`result_ids=[r1]`，`generation_status=template`，回答仅有 752/1660 | 不是“已经取得观点但生成器没使用”；当时根本没有查询观点内容 | [review_answer_facts.py](../src/data_analysis_agent/agents/review_answer_facts.py)、[review_answers.py](../src/data_analysis_agent/agents/review_answers.py) |

修复路径：识别维度内容请求 → 绑定当前范围与有效上下文 → 查询真实观点 → 后端计数与原文引用校验 → 输出具体表述。若从已确认的一级统计计划开始，现有补查只执行一次受控循环；失败、预算不足或引用不合法时不发布完整结论。

## 2. 修复前后计划及实际执行

| 项目 | 原始任务 | 最终代码只读重放 |
|---|---|---|
| query_type / sub_intent | aspect_summary / overview | opinion_breakdown / drill_down |
| analysis_type | issues | opinion_breakdown |
| sentiment / aspect | negative / service | negative / service |
| 必需工具 | get_top_issues | get_opinion_breakdown |
| requires_evidence | false | true，最多 3 条代表证据 |
| 实际工具顺序 | get_analysis_context → get_top_issues | get_analysis_context → get_opinion_breakdown |
| evidence_assessment | facts_sufficient / statistics | descriptive_evidence_available / content |
| evidence_completion | not_needed，0 次补查，未识别缺口 | not_needed，0 次补查，首轮已取得观点内容 |
| result_ids | r1 | r1 |
| generation_status | template | template |
| 模型调用 | 历史 trace 含规划模型调用 | 意图、规划、主题、回答均为 0 |

最终执行工具参数：

```json
[
  {"tool": "get_analysis_context", "parameters": {}},
  {"tool": "get_opinion_breakdown", "parameters": {
    "product_ids": ["100013588053"],
    "aspect": "service",
    "sentiment": "negative",
    "topic": null
  }}
]
```

工具使用后端冻结的原始范围：

```json
{
  "source": "reviews_csv",
  "dataset_version": "reviews-csv",
  "product_ids": ["100013588053"],
  "date_from": "2020-08-25",
  "date_to": "2022-08-27",
  "baseline_from": "2020-07-01",
  "baseline_to": "2020-07-31",
  "as_of": null,
  "model": "Pro/deepseek-ai/DeepSeek-V3.2",
  "prompt_version": "review-opinions-v1-e61de599683cba6703c2f61e85125d0be4627c2763440486aa717e4bc884036e"
}
```

这里的 model 是既有标注身份，不表示本次调用该模型。标注服务地址仍由原任务绑定，报告不展示地址。当前查询不使用基线统计。

已检查 [review_tools.py](../src/data_analysis_agent/agents/review_tools.py)：`get_opinion_breakdown` 在原有只读工具层查询；`get_aspect_drilldown` 则会把选取的观点交给主题归并模型。本次没有明确的真实评论外发授权，因此新内容表达选择前者。既有明确主题分析能力保留，其合成测试继续通过；没有新增 Agent 组件或新的分析工具权限。

## 3. 真实查询结果与回答示例

当前范围 1699 条评论，其中 1660 条成功标注。客服负面匹配 **752 条去重评论、1119 条观点**。一级占比 752/1660 = 45.3%；下表每组占比以 **752 条客服负面匹配评论**为分母，两种分母不混用。

| 实际观点原文 | 去重评论数 | 观点数 | 组内评论占比 | 代表评论 / 源文件行 |
|---|---:|---:|---:|---|
| 安装不及时 | 2 | 2 | 0.3% | #8267 / 1815 |
| 电话一直通话中，客服就是个机器人，没有一点** | 2 | 2 | 0.3% | #8983 / 2616 |
| 服务差 | 2 | 2 | 0.3% | #8407 / 1963 |

三条证据的源文件 SHA256 均为 `90d5061b67a724ee7584ea01e78d6115865294470f7a7ae29597c3a7a94acccf`。原文位置、商品、日期、标注版本和统计分母均通过既有验证。没有用这些少量代表评论推算其他问题类别的数量。

最终回答摘要实际为：

> 已核验的具体表述包括“安装不及时”、“电话一直通话中，客服就是个机器人，没有一点**”、“服务差”。以下按相同观点原文统计去重评论数；同义表述尚未合并，不能据此认定完整问题类型排名。

随后逐组展示去重评论数、观点数、占比、明确分母及评论引用，例如：

> 观点原文“安装不及时”：2 条评论、2 条观点，占本次匹配评论 0.3%（分母 752 条），负面；评论依据 #8267，源文件第 1815 行。

以上为已查询的真实表述。不能把“服务差”擅自解释为“回复慢”“态度差”或“售后推诿”，也不能把精确表述的 2 条评论当作合并全部同义表达后的问题总量。

## 4. 测试与浏览器验收

| 验证 | 结果 |
|---|---|
| `test_review*.py` 全部评论相关回归 | 290 项执行，287 通过，3 项缺少隔离 PostgreSQL 环境而跳过 |
| 最终维度内容与 assistant_semantics 专项 | 17 项通过（包含上述维度测试的重复验证） |
| 任务提交、重试身份与幂等 | 4 项通过 |
| HTTP → 持久队列 → 后台 Worker → 公开读回 | 3 个正常场景通过 |
| HTTP 故障控制 | 8 个场景通过：预算、超时、伪造引用、试图写入、日期越界、歧义、部分主题、主题失败 |
| 前端单测 | 最终全量串行重跑 216/216 通过；首次并行运行曾有 1 项 10 秒超时，对应文件单独重跑也为 6/6 通过 |
| 前端生产构建 | 通过；保留既有大分包提示 |
| Chromium E2E | 通过；真实 FastAPI/Worker/Vite，隔离 SQLite 和本机模拟模型 |
| 真实商品原条件只读重放 | status=ok，validation=passed，外部模型调用 0 |

新增测试覆盖 A—F：一级负面统计后追问；无历史单商品；客服正向与负向分离（含多轮切换）；商品/会话变更不能继承旧父任务；仅计数时内容不足且不编造；直接查询和补查超时、预算不足时禁止完整发布。G 由既有意图、主题、统计、只读、证据校验、任务幂等及会话 E2E 联合覆盖。

浏览器中实际操作“差评主要集中在什么地方？”→“包装方面有什么问题？”，验证父任务关联，实际工具为 `get_opinion_breakdown`，包装负面去重评论 4 条、分母 4 条及真实夹具原文“包装破损”。同时保留新建、切换、刷新、异步返回隔离、范围切换和旧存储迁移的会话回归。

已检查 1440、1024、768、390 四种宽度的截图和页面测量：无横向溢出，内容与输入组件正常；长回答在窄屏中纵向滚动。本次没有修改页面样式。

- [1440px 内容下钻截图](../output/playwright/sessions-yiE709/dimension-content-1440.png)
- [1024px 内容下钻截图](../output/playwright/sessions-yiE709/dimension-content-1024.png)
- [768px 内容下钻截图](../output/playwright/sessions-yiE709/dimension-content-768.png)
- [390px 内容下钻截图](../output/playwright/sessions-yiE709/dimension-content-390.png)
- [E2E 断言与测量记录](../output/playwright/sessions-yiE709/acceptance.json)

## 5. 修改文件

- `review_semantics.py`：统一内容表达识别，避免“商品质量方面”被误当商品实体。
- `review_prompts.py`：补充“称赞”“抱怨”的情感词。
- `review_plan.py`、`review_intent.py`：单轮和多轮内容请求优先本地观点工具。
- `review_evidence.py`、`review_evidence_loop.py`：共用内容判断，保留一次补查与范围、预算保护。
- `review_answer_facts.py`、`review_answers.py`：摘要使用已核验原文，计数不足以回答具体内容时明确限制。
- `tests/test_review_dimension_content.py`：新增六维度及 A—F 回归。
- `tests/test_review_evidence_loop.py`、`scripts/accept_evidence_loop.py`：补查故障夹具从显式一级统计开始；正常内容问题验证直接下钻，不再要求多余补查。
- `frontend/tests/sessions.e2e.mjs`：追加维度追问的真实页面与后台执行验收。
- `scripts/dimension_drilldown_audit.py`：原始任务、父任务和冻结范围的只读审计，阻断外部模型入口。
- 本报告。

以上源码位于 `src/data_analysis_agent/agents/`，除单独列出的测试、脚本和报告外无其他业务文件修改。

## 6. 复现与证据索引

在项目根目录 PowerShell 中执行：

```powershell
$env:LABEL_EXECUTION_MODE='local'
.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_review*.py' -q
.venv/Scripts/python.exe -m unittest discover -s tests -p test_task_submission.py -q
$env:PYTHONPATH='tests'
.venv/Scripts/python.exe -m unittest test_review_dimension_content test_assistant_semantics -q
.venv/Scripts/python.exe -m scripts.accept_evidence_loop --output outputs/dimension-content/http-success-repeat
.venv/Scripts/python.exe -m scripts.accept_evidence_loop --controls --output outputs/dimension-content/http-controls-repeat
.venv/Scripts/python.exe -m scripts.dimension_drilldown_audit --execute --output outputs/dimension-content/readonly-repeat.json
node frontend/tests/sessions.e2e.mjs
npm.cmd --prefix frontend test -- --maxWorkers=1
npm.cmd --prefix frontend run build
```

真实审计需要现有 `.env` 指向任务和评论数据库以及数据库可达。脚本读取最近 40 个包含目标商品的分析任务，选择对应问句，按原始参数查询；不提交新任务、不改写历史任务，也不外发评论。未来任务增多导致目标不在此窗口时会明确报错。

| 证据 | 本地文件 | SHA256 |
|---|---|---|
| E1：原始任务及修复前规则计划 | [dimension-before.json](../outputs/dimension-content/dimension-before.json) | `8c20f4d0bd67812c4407a1319fbf3ad096fb7b335c030a64f44b1b87df5503ca` |
| E2：最终真实只读重放 | [dimension-after.json](../outputs/dimension-content/dimension-after.json) | `aba9dfb77f4cad636f2cbcdd1b72ecd3af2e0bf017fe4bd41ad18aaf80447c74` |
| E3：浏览器验收 | [acceptance.json](../output/playwright/sessions-yiE709/acceptance.json) | `3149b0c98386a915f0038a8317d1353cc5c1012f9cdbb5c26b7ac6420f6083be` |

其他日志：`output/dimension-review-regression.log`、`output/dimension-final-unit.log`、`output/dimension-task-tests.log`、`output/dimension-frontend-final.log`、`output/dimension-frontend-build.log`。HTTP 详细报告位于 `outputs/dimension-content/dimension-http-success/` 和 `dimension-http-controls/`。真实结果保存在 Git 忽略的 `outputs/`，没有提交到版本库。

## 7. 剩余限制

- 未获得真实原文外发授权，未运行真实模型语义归并；真实输出为精确观点分组，已有主题归并只用受控合成数据回归。
- 标注沿用既有模型结果，未人工逐条复核；本次校验验证计数、范围与引用一致性，不等于重新认证标注准确率。
- 未重启用户正在使用的服务或改写历史结果；运行中的旧 Worker 需要重新加载代码，新提交的问题才使用修复。历史任务不会自动重跑。
- 未执行压力测试、跨设备同步或新增会话功能。本阶段至此停止。
