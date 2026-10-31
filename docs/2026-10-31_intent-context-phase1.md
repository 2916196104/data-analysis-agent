# 意图理解与多轮追问：第一阶段交付

日期：2026-10-31。仅修改意图、上下文关联与执行入口，不改页面布局、回答生成、评论标注或统计 SQL。

## 审查结论

完整检查了 `followup.ts → useAgentChat.ts → 任务提交 → TaskWorker → review_semantics.py / review_plan.py → reviews.py → review_tools.py` 及对应测试。

1. 前端“完整问题”分支只继承范围，不继承情感和查询目标。“使用效果主要集中在哪些地方”因此丢失上一轮 positive 条件。
2. “那物流方面呢”未匹配维度短句，落入模糊指代；前端规则未命中的口语也没有稳定的上下文关联。
3. Hook 仅保留成功结果条件，失败轮次的用户比较对象和查询目标随未验证数据一起被丢弃。
4. `context_task_id` 原来只被 Worker 记录，没有用于后端补全。后端按单轮文本重新规划，无法恢复上一轮省略的条件。
5. 商品简称主要在优缺点专用路径解析；普通正向查询和比较指代缺少共享的目录匹配及歧义检查。
6. 规则无法识别的请求在进入模型工具规划前就被拒绝；原工具规划无法补救自然语言意图缺口。

实施前已说明改动文件、接口兼容方式与错误继承风险；本轮按该范围实施。

## 实际实现

- 新增 `agents/review_intent.py`：`ReviewIntent` 与规则优先解析，表达查询类型、商品表述、情感、一级维度、下钻/原文目标、主题、时间和澄清状态。规则确定时保留原 `ReviewQueryPlan`；下钻、上下文补全和模型补充均编译到现有工具计划。
- 新增 `jobs/analysis_context.py`：最多读取 4 轮任务链，检查会话、数据集、标注身份、商品与时间绑定。`user/users` 只保存用户请求；`validated` 必须来自 succeeded、无 error、已有 passed 且再次通过数值、引用和范围校验的任务。
- 失败请求的明确实体、情感与可确定查询目标可以继续使用；失败结果里的统计、原文和 result_ids 不作为可信来源。新问题的明确商品优先于历史与模型省略/替换。
- 前端分开保存最近用户意图引用 `Conversation.intent` 与已验证结果条件 `Conversation.context`。规则未知的口语也携带任务引用，不发送本地统计或完整聊天历史。明确的维度、情感、周期和原文分页继续走原快速转换；失败后的可确定维度/情感/周期请求交给后端恢复用户条件。
- 商品匹配严格限定当前数据集。普通查询可沿用已明确选择的型号；“这两款”的比较指代必须唯一匹配具体商品，多型号时列候选 ID。当前页面已明确选两款时不要求重复提供 ID。不用当前商品替代不存在的商品。
- 只读事务、工具参数验证、固定工具注册表、调用预算、结果完整性、分子分母与证据引用验证、失败发布保护均保留。

## 模型与接口边界

新增可选配置 `AGENT_SEMANTIC_PARSING=rules_then_llm`，默认启用规则后的补充解析；`rules_only` 关闭该补充调用，保留规则和有限上下文能力。真实 `.env` 未修改。

每个未命中请求最多一次语义模型调用，SDK 重试为 0，超时沿用配置并限制到最高 30 秒；输入最多 16,000 UTF-8 字节。只发送当前问题、实体表述、明确用户意图、最多 4 项已验证查询条件与 Schema，不发送评论、统计、完整历史或服务凭据。

模型必须返回 `query_type` 和 `needs_clarification`，其他槽位允许省略。额外 SQL 字段、非法维度、非用户提供的实体、明确情感冲突、替换本轮商品、越界/猜测日期均不能直接执行。复制已冻结日期作为继承接受。解析超时或非法响应是 `intent_timeout/invalid_intent` 失败，不伪装成用户信息不足。

没有新增路由、任务必填字段或数据库迁移。继续复用 `conversation_id/context_task_id`；已删除或不存在的关联任务不妨碍自包含新请求。结果新增可选 `user_intent`、`intent_resolution`、`intent_model_calls`，`total_model_calls` 包含语义调用，原 `model_calls` 继续指规划模型。

执行提示词版本更新为 `review-agent-v8`。重启后端和 Worker 后对新提交任务生效；未重启用户原有进程或改写历史任务。

## 文件变更

| 范围 | 文件 |
| --- | --- |
| 语义与计划编译 | `src/data_analysis_agent/agents/review_intent.py`、`review_semantics.py`、`reviews.py`、`review_prompts.py` |
| 有限任务上下文 | `src/data_analysis_agent/jobs/analysis_context.py`、`worker.py` |
| 配置 | `src/data_analysis_agent/core/config.py`、`.env.example` |
| 前端状态与追问 | `frontend/src/features/agent/followup.ts`、`useAgentChat.ts`、`chat-state.ts` |
| 单元与队列回归 | `tests/test_review_intent.py`、`test_assistant_semantics.py`、`test_review_agent.py`、`test_review_plan.py`；前端 `followup.test.tsx`、`chat.test.tsx` |
| 浏览器验收 | `frontend/tests/pros-cons.e2e.mjs` 增加意图模式，复用隔离夹具与实际 API/Worker；`assistant.e2e.mjs` 修正模拟商品提问措辞 |
| 使用说明 | `docs/review-agent.md` |

两个旧后端测试和旧浏览器脚本曾用“美的空调”描述实际不存在美的商品的模拟数据；现改为“所选商品”。原计数、分母及发布断言保留，避免为通过旧夹具而违反“不存在商品不可替换”的要求。

## 验收证据

| 案例 | 实际检查 |
| --- | --- |
| A | 正向汇总后原句追问，Worker 恢复 positive + effectiveness，执行观点明细，未调用规划或语义模型 |
| B | “美的空调的好评主要是什么”选择 `get_positive_aspects`，统计口径不变 |
| C | 保留适用的正向汇总或正向细分；存在多个目标时具体列明汇总/原文等选择；基线证据的窗口、页长保留 |
| D | 失败轮次只恢复用户对象，刷新后仍可关联；品牌多型号明确列出两品牌候选 ID，未查询替代商品；明确选择两款后可以比较 |
| E | 未知商品、已知与未知商品混合请求都拒绝替代，统计查询为 0 |
| F | 原统计回归、范围与情感约束、日期、分母、证据、工具预算、失败保护通过；隔离业务库前后哈希一致 |

新意图专项包含 14 项测试，并扩展失败目标优先级、情感/周期切换、口语补充、冻结日期、模型 SQL/实体注入、跨会话/范围与有限历史链检查。前端新增追问与关联测试，基础问答内容仍不进入分析上下文。

最终验收日志：

- [后端相关回归](../output/intent-phase1-backend-complete.log)：222 项运行，219 项通过、3 项外部 PostgreSQL 测试跳过。
- [前端回归](../output/intent-phase1-frontend-complete.log)：22 个文件、201 项通过。
- [构建](../output/intent-phase1-build-complete.log)：TypeScript/Vite 通过，保留既有大于 500 kB 分包提示。
- [A—F 浏览器专项](../output/intent-phase1-browser-complete.log)：实际 FastAPI、Worker、Vite、Chromium，本机模拟模型；1440/1024/768/390 像素无横向溢出，输入区在视口内，已检查桌面与窄屏截图，浏览器错误 0。
- [原优缺点专项](../output/intent-phase1-pros-cons-regression.log)：品牌分组、复制、查询快照、历史恢复通过，模型请求 0。
- `git diff --check` 通过。

复现专项与前端验证：

```powershell
$env:LABEL_EXECUTION_MODE = 'local'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_review_intent.py -v
npm --prefix frontend test -- --maxWorkers=2
npm --prefix frontend run build
$env:E2E_INTENT_PHASE1 = '1'
node frontend/tests/pros-cons.e2e.mjs
```

以上浏览器与后端离线验证使用模拟商品和模型，不能视为真实品牌结论或真实 PostgreSQL 集成通过。需本机网络的验证在获准的沙箱外执行，隔离服务由验收脚本收尾。

旧完整助手 E2E **未通过**：首先在已不存在的提示文案阻塞；临时使用输入禁用断言继续排查后，又在当前已改名的“切换范围”按钮超时，尚未进入分析提交。记录保留在 [初次日志](../output/intent-phase1-existing-e2e.log) 与 [继续排查日志](../output/intent-phase1-existing-e2e-final.log)。本阶段不迁移整套旧布局脚本，最终只保留与实体约束有关的模拟商品措辞修正，不能把该旧完整 E2E 计为通过。

### 真实模型单例

使用当前配置的 `deepseek-ai/DeepSeek-V4-Flash`，仅发送“那大家到底是夸它哪些点呀？”及精简正向上下文，无评论或统计，共进行两次独立实测：

- 缩短为 10 秒请求阈值时超时，总耗时 12.474 秒（含客户端开销）。因此最终语义请求沿用配置超时，最高 30 秒，而非擅自固定成更短值。
- 30 秒阈值下 7.353 秒返回合法正向 `opinion_breakdown` 与冻结窗口；与测试预设的 `aspect_summary` 存在分类分歧，原记录 `success=false` 保留，未改成分类通过。
- 捕获该 JSON 后，通过隔离数据库的真实只读工具链回放成功、校验 passed，未增加模型请求。由此验证了响应可安全执行，也发现了需要后续评测的汇总/明细分类差异。

证据：[首次超时](../output/playwright/intent-live/semantic.json)、[实际响应](../output/playwright/intent-live/semantic-configured-timeout.json)、[工具回放](../output/playwright/intent-live/replay.json)。本机验收目录已被 Git 忽略。该单例不是泛化准确率评测，也不等同于真实模型与生产数据库的整链路验收。

## 尚未支持与下一阶段

- 不保证任意口语的汇总/细分分类一致性；复杂清除筛选、多个并列目标、跨多个历史主题的指代仍可能要求具体说明。
- 新的相对日期表达不由模型自行猜测。保留原有前端确定性日期短语；新增日期必须明确且符合窗口约束。
- 仍只有现有六个一级维度与原文检索，没有新建标准细分标签、开放主题聚类、正向趋势、因果或商业优先级统计。
- 原文下一页依赖可验证的成功游标；失败结果不能提供可信页数或总数，必要时须明确要查询的页面。
- 基线原文切换到当前窗口的观点细分需要明确确认，不会自动把旧基线证据换成当前窗口；普通维度追问继续保留原文窗口和页长。
- 下一阶段优先建立带标准意图的口语评测集，分别测商品匹配、情感继承、汇总/下钻、必要澄清和拒绝替代的准确性，再验证真实模型延迟与 PostgreSQL 快照。单独迁移旧完整浏览器脚本的过期 UI 选择器。

本阶段完成后停止，未继续重写布局、回答生成或标注系统。
