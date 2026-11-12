# 商品 055：首次完整 Agent 业务链路验收记录

## 本机下一步入口

仓库已有 [一次性执行入口](../scripts/run_055_agent_e2e_once.py)，无需新建脚本。入口会调用生产 `run_review_agent()`；入口的 `--execute-authorized` 才触发真实调用，使用固定商品和范围、既有只读身份、一次本地查询编码和一次 Top-5，并阻断意外 LLM 与 Python 网络调用。完整限制和验收条件见下文。此次仅静态核对入口，**未执行脚本**；预期限制并不代替实际运行证据。

仅在用户对该次实际操作另行明确授权后，由用户在本机 PowerShell 的项目目录执行一次：

```powershell
Set-Location -LiteralPath 'E:\AllProject\data-analysis-agent'
& '.\.venv\Scripts\python.exe' '.\scripts\run_055_agent_e2e_once.py' --execute-authorized
```

失败时保留输出，不自动重试。该命令不启动持久 Worker，不能验收队列、跨进程 State 持久化、HTTP 或前端；也不解除 P6-E1B 阻断。

日期：2026-11-12（Asia/Taipei）。**本机最近两次尝试：FAILED / FAIL。** 两次均成功进入 PostgreSQL 只读事务，范围、Router、Planner 和查询契约检查通过；均在本地 Embedding 调用前停止，未执行检索或得到最终回答。此前 Codex 受限环境的尝试则未执行 SQL；两种执行环境的记录不得混同。详见下述诊断。

## 本机失败定位与离线修复

[第一次本机记录](evaluation/agent-055-first-agent-e2e/run-20261112T061501923617Z.json)和[第二次本机记录](evaluation/agent-055-first-agent-e2e/run-20261112T061624539170Z.json)均显示：一次只读连接、6 条读取语句、Agent 1 次、Embedding 和 Top-5 均为 0，事务回滚和连接关闭成功，数据库修改及 LLM 调用为 0。原记录只保留 `business_error`，没有保留具体错误消息，因此不能从历史记录直接断言其完整异常链。

静态检查发现，一次性入口的服务工厂把包含 `baseline_from` 和 `baseline_to` 的 Schema 兼容范围直接传给 `extra='forbid'` 的 `RetrievalScope`。使用纯合成范围独立验证：去掉这两个非检索字段的范围可解析，而包含它们时返回 `invalid_retrieval_scope`。这与两次本机记录在编码前失败一致，属于有证据支持的入口缺陷，但由于历史记录未保存异常消息，不能称为对历史异常的直接捕获。

已在 `scripts/run_055_agent_e2e_once.py` 中仅移除比较输入中的两个基线字段，再进行同样严格的范围相等校验；商品、日期、来源、身份和其他范围字段仍须一致，不改变生产 Agent。现有合成测试夹具同时修复了重复关键字和不可迭代游标问题，未删改断言。**7/7 离线测试通过**，含原生业务链路配合合成数据库与固定编码器的测试；真实数据库和真实 Embedding 均未调用。该测试不证明修复后本机真实执行会成功。

本次两条历史记录保留不变，未自动重跑。后续若用户决定再次执行，使用下方命令；新运行需要重新观察真实来源、Validator 与最终回答，不复用旧检索结果。P6-E1B 仍 BLOCKED。

## 已有真实前置证据

[首次真实 RAG 结果](agent-055-first-real-rag-integration-result.md)及其[脱敏记录](evaluation/agent-055-first-real-rag/run-20261112T043912972154Z.json)确认：COMPLETED / A，真实编码 1 次、Top-5 1 次、召回 5 个观点、许可 3 个观点，来源和 Validator 均 PASS、数据库修改 0。3 个许可观点属于 **2 条不同评论**：

| 前次 rank | review_id | annotation_id | opinion_id |
|---|---|---|---|
| 1 | 9958 | 5489 | 10751 |
| 2 | 9993 | 5524 | 10808 |
| 5 | 9993 | 5524 | 10807 |

这是前次检索的事实，**不是本次完整 Agent 的结果或预填答案**。新运行须从自身真实检索的 permitted_keys 和 review_id 去重计数，不硬编码“3 个观点、2 条评论”来凑验收。

## 生产链路核对与待验收项

| 项目 | 已定位的生产机制 | 完整 Agent 实测状态 |
|---|---|---|
| State | [run_review_agent()](../src/data_analysis_agent/agents/reviews.py)保存 result.scope、user_intent.resolved_product_ids/effective_time_range、query_contract.scope；ReviewToolContext 持有同一运行范围。首次无历史会话时 analysis_state 可为空排名/待办状态，不能把它误称为未绑定商品 | NOT_EXECUTED |
| Intent Router | [resolve_review_request()](../src/data_analysis_agent/agents/review_intent.py)的 semantic_direct 规则路径，目标 delivery_slow，固定单商品；不需要意图 LLM | NOT_EXECUTED；纯规则检查符合预期 |
| Planner | [build_query_plan()](../src/data_analysis_agent/agents/review_plan.py)选择 semantic_evidence / search_semantic_evidence；原问题不改写，Top-5、aspect=None、sentiment=None | NOT_EXECUTED |
| RAG Tool | [ReviewToolContext.execute()](../src/data_analysis_agent/agents/review_tools.py)通过真实注册表调用生产 execute_retrieval()；Agent 内部先 prepare()，再让同一 service 消费已准备的真实查询向量，禁止重复语义检索 | NOT_EXECUTED |
| Validator | P0 来源认证 → validate_retrieval() → [validate_report()](../src/data_analysis_agent/agents/review_validation.py)的“查询目标匹配”及引用检查 → semantic_answer()事实绑定检查；[adapt_result()](../src/data_analysis_agent/agents/review_results.py)只传递 permitted_references() | NOT_EXECUTED |
| 最终回答及引用 | [NaturalLanguageAnswerGenerator.generate() / semantic_answer()](../src/data_analysis_agent/agents/review_answers.py)从生产许可生成通用模板回答；逐项核对 opinion_key、原文区间、scope、来源及事实绑定；语义证据不截断或替换 | **尚无完整 Agent 最终回答或引用结果** |
| 成功/失败根因 | 当前未触发执行，不存在已证实的 Agent 运行失败根因。前次 RAG 成功不足以证明 State 到最终发布的完整链路通过 | NOT_EXECUTED，不记 PASS/FAIL |

本轮只对原问题和固定输入执行了本地 Schema/纯规则检查：商品与当前日期保持不变，semantic_direct=true，规划工具为 search_semantic_evidence，意图模型调用 0。没有把该检查冒称为真实 Agent 执行。

还确认了一个应如实记录的职责区别：本题旧式 build_request_goal()/task_goal 返回 None，不触发 validate_task_completion()；不能要求或伪造该分支的 task_acceptance=passed。本题使用已生成的 RuntimeQueryContract（delivery_slow）及 validate_report() 中的 task_matches() 核对目标，再由生产事实/引用校验约束最终回答。这不是旧式 Goal 模块或 State 持久化已实测通过的证明。

## 单次调用计划与预计成本

**唯一问题：商品100013588055有没有送货慢的反馈？**

沿用 reviews_csv / reviews-csv / 100013588055，UTC `[2021-09-11T00:00:00Z, 2022-08-27T00:00:00Z)`、既有标注 profile 和 Embedding 空间，账号 rag_audit_ro、安全本地凭据。空间及模型配置与[已核对计划](agent-055-first-real-rag-integration-plan.md)一致：固定修订 BGE-M3、1024 维、CLS/L2；不重新审计、同步或扩张覆盖。

| 计划调用 | 单题预算 / 说明 |
|---|---|
| 完整生产业务入口 | **run_review_agent() 1 次**，不重写 State/Router/Planner/Tool/Validator/回答流程 |
| 注册工具 | 生产 get_analysis_context 1 次 + search_semantic_evidence 1 次；前者是当前范围上下文，不是额外独立审计 |
| 查询 Embedding | **1 次本地编码**，由生产 Agent 的 prepare() 触发；不提前在外层再编码一次 |
| Top-K | **1 次真实 Top-5**，不加过滤、不增大 K、不补关键词、不重试；不能用旧脱敏 JSON 拼回检索结果 |
| 语言模型 | **意图 0、规划 0、主题 0、最终回答 LLM 0**；本题走生产规则直达及许可模板。若路径要求额外 LLM 调用，停止，不自动转入通用模型工具循环 |
| 数据库 | 仅业务只读连接。生产入口先用规划快照绑定范围，回滚后编码，再建新的 RR 只读证据快照；不把规划快照或前次检索记录当作本次来源证明 |
| 资源和费用 | **预计外部 API 费用 0**；实际成本为本地编码 CPU/RAM、只读查询和模板渲染。前次独立 RAG 耗时约 9.06 秒仅供参考，本次 Agent 总耗时未实测，不承诺相同延迟 |

### 调用边界

- 生产 [TaskWorker](../src/data_analysis_agent/jobs/worker.py)的 analysis 分支实际调用的正是 run_review_agent()。直接调用它可验收完整 Agent **业务链路**，不会另造工作流。
- Worker 的领取、心跳、finish_task() 会写任务状态库，与当前“不修改数据库”限制冲突。因此本次建议不启动持久 Worker、不提交生产任务、不写任务数据库；**HTTP/队列调度、State 的跨进程持久化或重启恢复不计为本次通过项**。运行内 State 和返回 scope 仍需真实验证。
- 现有 BaselineScope 强制要求 baseline_from/to。待批准的单题兼容参数可均设为 `2021-09-10`，仅满足现有 Schema；本题实际查询窗口保持上述固定范围，禁止调用 baseline 窗口工具或将其当作比较数据。这不是额外评论读取范围。本轮仅验证了该输入 Schema，未读取这一天的数据。
- 使用已有配置；不修改生产代码、环境文件、权限或数据库。连接只读及现有超时配置必须保留。新运行结束须记录事务结束、连接与本次编码进程关闭情况；任何失败不自动重试。
- 实际执行仅在用户明确授权后，由本机触发一次；本轮没有运行旧 RAG 脚本或新完整 Agent 测试。

## 实际运行后的通过判据与记录内容

须同时观察生产 result.status=ok、validation.status=passed，其中“查询目标匹配”检查通过，并完成以下检查，才能登记业务闭环通过；本题不把未适用的旧式 task_acceptance 当作已通过：

1. result.scope、user_intent、query_plan、query_contract 绑定原问题、商品 055、指定 source/dataset/profile 和当前 UTC 窗口；运行中不串商品或日期。
2. trace 显示 semantic_evidence 规划及一次成功 search_semantic_evidence；真实编码和距离查询各一次，所有模型调用分项符合预算。保留原生 get_analysis_context 调用，不伪装成只有一条 SQL。
3. 检索候选通过生产来源与快照认证；最终 public_answer.evidence_refs 中每个 opinion_key 都属于**本轮** permitted_keys，且无遗漏、重复伪造、未许可引用或截断后改变意义的引文。
4. 分别计算许可观点数与 `len(set(review_id))`，不把三个观点说成三条独立评论；重复评论 ID 的不同许可观点可以分别引用。
5. 最终 answer、public_answer.summary/highlights、evidence_refs、caveats 和 sample_note 均记录实际生成值；不预写答案。只陈述本次可信买家反馈，不推断普遍性、发生率或商品客观质量，不宣称 891 条未索引观点已检索。
6. 若无许可、来源/Validator 失败、工具未完成、预算超出或清理失败，如实记录 unsupported/error 或验收失败；不拼接旧的成功候选或最终回答。

脱敏记录应包含 State 的允许字段、Router/Planner 结果、工具 trace、实际编码/Top-K/各类模型调用次数、elapsed_seconds 与 performance、来源 ID/key/快照、原生最终回答和合法短引用。完整 result.results/compact_context 可能含全文或私有标注 endpoint，不能整包直接写公开报告；不保存密码、连接串或原始向量。

此前[未执行记录](evaluation/agent-055-first-agent-e2e/authorization-pending.json)中：完整 Agent、Embedding、Top-K、LLM 和数据库调用均为 0；最终回答/引用/真实耗时为 null。该历史准备记录不等于后续授权尝试的结果。P6-E1B 独立阻断保持原状。

<!-- agent-055-once-runtime -->

## 最新一次性入口记录

状态：**COMPLETED / PASS**。真实 Agent 调用：1。

[脱敏 JSON](evaluation/agent-055-first-agent-e2e/run-20261112T061904314027Z.json)。生产返回状态：ok；许可观点：3；不同评论：2。

实际调用、逐项检查、清理及根因见 JSON；失败时禁止发布答案。

生产最终回答（许可短引用）：

在本次检索到的评论中，有买家明确报告了以下情况。

评论 #9958（2022-07-13）写道：“送货很慢” [r1]

评论 #9993（2022-07-27）写道：“物流太慢了” [r1]

评论 #9993（2022-07-27）写道：“商品到货时间太长了，等了八天才到货” [r1]

仅描述本次检索且可明确核验的买家自述，不代表客观性能、总体比例或全部评论。
完整评论仅作上下文否决；相似度不代表事实支持。未识别表达属于语义覆盖不足。

语义Top-K候选不作为统计样本或总体分母。

结论仅限本次 Top-5 的合法买家自述；不代表全部评论或未索引观点。P6-E1B 阻断不变。
