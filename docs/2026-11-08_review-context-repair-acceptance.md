# 商品评论 Agent：多轮范围与负面观点修复验收

日期：2026-11-08。范围：用户要求的 A–E；完成工程实现与离线验收，未执行生产分类或发布部署。

## 已修复的行为

1. 同会话中，上轮明确查询单商品 A 后，未修改商品、数据集或日期的数量、具体表现、原文追问继续使用 A。任务链接恢复时也保留原页面选择与实际执行范围，避免将 A 错判为全部商品。
2. 显式商品、已确认的页面筛选变更优先于继承值。数据来源、数据集或标注身份改变时不恢复旧上下文；跨会话不能串用父任务。原有“超出页面已确认范围先确认切换”的边界保留。
3. 数量/占比使用精确统计；“体现在哪里”等具体表现使用候选子主题分析；原文/举例使用证据查询；“都在说什么”读取原始观点分组并明确其不是同义主题排名。确定的原文查询也直接执行已有计划，无需再调用规划模型。
4. 品牌命中多个 SKU 时，说明匹配数量和候选 ID，并要求明确单 SKU 进行子主题分析；整体统计可先在页面明确要合并的商品，再查询所选范围的整体统计。本轮没有实现品牌跨 SKU 的自动主题归并，不会暗选型号。
5. 新增独立任务级验收：有效范围、继承字段、实际查询快照、所需工具、主题批次完整性、去重计数与原文引用。维度计数不能完成主题任务。计划外增加维度或商品筛选也会被拒绝。
6. 零证据、全部无法归类时标记 `task_acceptance.complete=false / insufficient_evidence`，明确“分析未完成”，不发布主题名称或频次。任务传输层仍可为 `succeeded`，表示任务执行结束；业务结果为 `unsupported`，两者不等价。失败或超时批次继续走既有阻断，不能发布完整主题结论。

新增验收用于本轮涉及的普通统计、观点、原文和子主题路径；语义检索及双向优缺点继续沿用已有冻结契约与校验，不由本次改动替换。

## 子主题数据缺口及分类边界

原始观点表没有已人工确认的子主题标签。因此本轮复用已有 `review_topics` 流程，不迁移数据库、不写回标签：

```text
已标注负面物流观点（精确商品/日期/标注身份）
  → 有预算的候选主题发现及定义
  → 独立逐条归属与连续原文位置复核
  → 校验主题支持观点、商品、维度、情感及来源
  → 由程序按评论 ID 去重计数
  → 展示候选主题、样本分母、未归类数量与代表原文
```

主题名称、定义和归属是模型候选，未经人工确认；计数由已验证归属及评论 ID 计算，不接受模型直接输出数字。一个评论可含多个观点、进入多个主题，各主题数量不可相加。受限样本必须保留抽样方式和覆盖率，不外推为全量频次。引用保留评论 ID、观点 ID、原文区间及来源引用；原文截取必须与评论正文逐字一致。RAG 不参与主题统计的分子或分母。

本轮只用合成评论与明确命名为“模拟…”的本地分类器验证流程。测试中的“2 条评论、3 条观点”是去重控制样本，不能解释为商品 100010219881 的真实主题数量。真实商品的主题准确率、候选是否遗漏、全量频次及真实模型延迟仍未验收。

## 主要代码位置

路径相对于仓库根目录；审计前调用链见 [审计及最小计划](2026-11-08_review-context-audit-and-plan.md)。

| 环节 | 文件和实现 |
| --- | --- |
| 页面范围与 URL 恢复 | `frontend/src/features/agent/useAgentChat.ts:79,104`：恢复/提交可选 `selection_scope`；`followup.ts:24,59,90`：范围白名单、空值等价和短追问继承。 |
| 会话任务 State | `src/data_analysis_agent/jobs/parameters.py:46`、`submission.py:46`、`worker.py:241`：复用现有任务载荷保存元数据；省略时保留旧载荷，结构化任务移除该字段。 |
| 服务端恢复 | `src/data_analysis_agent/jobs/analysis_context.py:8`：同会话、同身份验证；比较当前与原页面选择，记录显式修改字段；只恢复通过重新验证的历史查询条件。 |
| 范围解析与路由 | `src/data_analysis_agent/agents/review_intent.py:243`：先合并有效范围，再做指代和计划；`review_semantics.py:206` 区分目标；`review_plan.py:225` 选择负面子主题工具。 |
| 工具约束 | `src/data_analysis_agent/agents/review_tools.py:85` 复用输入模型；`review_plan.py:65` 归一化完整参数并检查；`review_compression.py` 按相同规则选取公开结果。 |
| 观点、子主题与引用 | 复用 `reviews/analytics.py:get_aspect_opinions`、`agents/review_topics.py:274` 及 `review_validation.py`，未新增平行分类器或检索统计链。 |
| 任务级验收 | 新增 `src/data_analysis_agent/agents/review_acceptance.py`，由 `agents/reviews.py:348` 在正常回答生成前执行；错误目标、范围或缺失证据不发布正常结论。 |
| 前端与导出 | `frontend/src/AgentResults.tsx:125` 单独显示任务验收；`features/agent/report-export.ts:72` 导出同一验收状态。保留既有摘要、表格、历史与原始审计结果。 |
| 回归测试 | 新增 `tests/test_review_task_completion.py`、`tests/review_context_browser_fixture.py`、`frontend/tests/review-context.e2e.mjs`；补充 URL/短追问及任务参数兼容测试，更新旧的“空证据正常完成”断言。 |

## 验收结果

| 层级 | 实际结果 | 证据 |
| --- | --- | --- |
| 评论分析后端 | 435 项：432 通过、3 跳过；跳过项需要单独提供隔离 PostgreSQL，本轮未连接生产 PostgreSQL。 | `outputs/review-context-repair-20261108/backend-final.log` |
| 新增任务目标回归 | 8 个测试方法覆盖下表场景，包含多组追问与反例；已纳入上述 435 项。 | `tests/test_review_task_completion.py` |
| API/任务兼容 | 20 项全部通过，包括可选元数据省略、结构化载荷不变、身份不一致拒绝。 | `outputs/review-context-repair-20261108/api-tasks-final.log` |
| 前端单元测试 | 26 个文件，235 项全部通过。测试环境有已有的伪元素样式提示。 | `outputs/review-context-repair-20261108/frontend-final.log` |
| 生产构建 | 通过；保留现有大包提示，无新增依赖。 | `outputs/review-context-repair-20261108/build-final.log` |
| 浏览器离线端到端 | 真实 API/Worker/Vite/Chromium，临时 SQLite；8 个场景通过，真实模型 SDK 尝试 0、Embedding 尝试 0。 | `output/playwright/review-context-repair-20261108/acceptance.json` |
| 布局与交互 | 1440/1024/768px：页面宽度均等于视口，无横向页面溢出；输入区未超出底边。检查主题表格、验收详情和报告下载。 | 同目录 `actual-*.png`、`topics-768.png`、`task-acceptance-768.png`、`candidate-report.md` |
| 保护资产 | 修改前后 1,754 文件逐项 SHA-256 一致，无新增、删除或内容变化。 | `outputs/review-context-repair-20261108/protected-verification.json` |

关键场景：

| 场景 | 检查点 |
| --- | --- |
| 单商品物流负面主题 | 只读 A，正确情感与维度；同一评论含同类多个观点时评论只计一次；逐字核对原文位置。 |
| 多轮承接 | A 后分别追问数量、具体表现、观点内容、原文，均保留 A；包括“这款商品”的指代。 |
| 显式切换 | 明确 B 后仅有 B 的证据；页面日期变更优先，未改商品仍保留 A。 |
| 品牌多 SKU | 合成目录匹配 5 个 SKU，明确澄清，不运行统计或分类。 |
| 跨会话/数据集 | 改 conversation_id 或 dataset_version 后拒绝恢复旧上下文；浏览器新会话使用当前页面的完整范围。 |
| 无证据/全未归类 | 业务目标未完成，无虚构主题或频次，无正常分析亮点。 |
| 任务目标反例 | 只有计数、错范围或缺少来源引用时不能判完整；额外的未计划筛选被拒绝。 |
| URL 与短追问 | 新浏览器加载任务链接，再问“具体体现在哪里”“那物流方面呢”，仍查 A，携带正确父任务。 |

## 复验命令与未验证项目

PowerShell，仓库根目录：

```powershell
$env:PYTHONPATH='.;tests;src'
$env:LABEL_EXECUTION_MODE='local'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_review*.py'
.\.venv\Scripts\python.exe -m unittest test_task_submission test_task_logging test_api_routes test_public_analysis_api
```

前端目录：

```powershell
npm test -- --reporter=dot
npm run build
node tests/review-context.e2e.mjs
```

浏览器脚本自行启动随机本机端口的独立测试服务，并仅结束自己创建的子进程。测试库、分类器和模型阻断均由测试夹具提供；不会启动现有生产 Worker。

未执行外部 LLM、Embedding、数据迁移、生产读写、人工审批或评分变更；未重启现有生产服务。真实模型分类质量、真实生产数据库集成和真实业务页面需要另行验收。历史已保存的错误回答不会被改写，查看修复行为需要部署更新后提交新任务。
