# P6-C 共享预算与受控多维执行验收

日期：2026-11-09。依据：P6-A、P6-B、P6 实施准备、F01 报告及当前源码。本轮入场包含未提交的 P6-B 改动；没有回滚或覆盖这些改动。

## 1. 结论

**P6-C：PASS（离线工程验收）。P6-D 开发准入：PASS；真实收费执行及生产准入：BLOCKED。**

可信 P6PreparedAnalysis 可以在单个共享预算约束下，按冻结维度顺序调用原分类器，完成候选发现、独立归属、程序去重与逐维/父级验收。1/2/3 维分别最多 2/4/6 次尝试。失败、费用不足、迟到响应、输入/来源异常均有明确状态，不缩减原目标。

功能合并运行 **537/537 PASS**，原 512 项全部保留，新增 P6-C 25 项。最终状态映射收敛后又重跑新增 25 项，全部通过。历史 13 个静态私有导入失败独立保留。

没有调用真实收费 LLM、真实 Embedding、生产库或部署；模型响应、单价、usage 和时钟均为受控离线夹具。未修改前端、复制、导出或 P6-D 公开结果逻辑。本阶段停止。

## 2. 修改前基线与代码审计

P6-B 的目标冻结、独立计划校验、当前统计比较、同快照预读和输入封存已存在；实际 Agent 在事务内准备完成后以 unsupported 提前退出。ReviewToolContext 对 P6 主题工具在缓存检查前硬拒绝。

原 analyze_aspect_topics 默认负向最多 80 种表达、20 一批，每维可能进行多次归属请求，并有独立 60 秒窗口；直接循环三次无法满足整轮 6 次/120 秒要求。原 _call 有 16000 字节 body 限额、1200/3000 输出上限及 SDK max_retries=0，但没有跨维费用和时间预留、usage 记账或迟到响应拒绝。

审计了 review_multi_aspect、review_preparation、review_topics、review_tools、review_plan、review_acceptance、reviews、review_compression、review_answers 和 config。确认 P6-A/P6-B 可复用；压缩器只接受完整且通过校验的结果，旧部分公开路径不应被新父 partial 直接复用，因此本轮不接入公开压缩/回答生成。

先新增三项测试执行修改前代码：**3 项，1 PASS、2 errors、0 skip**。已有准备仍能返回 ready/0 模型调用；共享预算和执行模块尚不存在，导入报错。日志 `.cache/p6c/before-tests.txt`，SHA-256：`5de30853d01b6b17126aaad5493e2aff470018dca35710f37e04e8243dbc4bce`。

## 3. 修改文件与职责

| 文件 / 函数 | 本轮改动 |
| --- | --- |
| 新增 agents/review_budget.py | P6CostAuthorization、P6OfflineTransport、P6SharedBudget；串行预检、预留、计数、截止、usage、停止状态及安全审计 |
| 新增 agents/review_execution.py | execute_p6_prepared；全局重验、顺序执行、逐维验收、父级汇总；P6ExecutedAnalysis 隐藏正文并只公开安全诊断 |
| agents/review_topics.py：analyze_aspect_topics | 可选 frozen_input/shared_budget 分支，直接消费预选 rows/units/items，保留来源 ID，不调用 _units 重选输入；旧默认路径不变 |
| agents/review_topics.py：_call / _parse_topic_response / call_topic_model | 复用原 wire/schema/提示词与解析，预算放在供应商调用之前；公共 P6 入口不导入或复制私有分类器 |
| core/config.py：AgentSettings / from_values | 独立 AGENT_P6_EXECUTION_ENABLED，严格 0/1、默认关闭 |
| agents/reviews.py：run_review_agent | 准备异常携带内部对象，退出事务及 closing 后才协调执行；保留工具硬阻断、公开功能门和真实尝试计数 |
| 新增 tests/test_p6c_budget_execution.py | 25 项测试，使用真实准备输入、实际 wire/解析/分类器、模拟供应商及假时钟 |
| 本报告 | 状态、费用、输入及验证证据 |

相对本轮入场，只改变 review_topics.py、reviews.py、core/config.py 三个已有文件。P6-A RequestGoal、选择器、Planner、两级验收器、P6-B 准备模块、工具硬阻断、旧测试和前置报告均保持入场内容。未新增依赖、平行分类器、自由规划框架、数据库表或迁移。

## 4. 服务端授权与默认拒绝

P6-B 准备开关不授予执行许可。进入 C 路径至少要求独立 `AGENT_P6_EXECUTION_ENABLED=1`；真实供应商请求还须由受控服务端提供授权费用 profile，包含：

- 明确 authorized=true、allow_real=true。
- 与当前模型/base_url 一致的身份、币种、有限非负费用上限和输入/输出每 token 单价。
- 完整已知的计费类别。本版仅支持明确覆盖 input/output；额外或未知计费类别拒绝。
- 具有明确标识的可靠 token 上界函数，覆盖实际 system prompt、wire body/schema 及模型消息协议开销。

默认 make_execution_budget 不提供任何费用授权、价格或可靠 tokenizer，所以**单独打开执行开关仍为零收费尝试**。没有把字节数换算成真实 token，也没有查询或硬编码真实供应商价格。

离线传输必须显式使用 P6OfflineTransport。它仅是可信内部测试组件，不是客户端可提交参数；任务参数模型拒绝 execution flag、budget、cost_authorization、offline_transport 和准备对象。预算授权对象不可变，运行中不能通过公共属性替换授权/传输或清除停止原因。

上界函数是服务端受信 profile 的责任，不凭一个名字证明数学可靠。本轮仅有合成上界，尚无可用于真实费用授权的 tokenizer/价格/计费合同验证，因此真实调用准入仍 BLOCKED。

## 5. 共享预算状态机与执行前约束

| 状态/动作 | 前置条件和变化 |
| --- | --- |
| created → start | 只允许启动一次；冻结 1—3 个唯一维度、输入摘要、全局重验回调；启动 120 秒分类时钟 |
| begin_aspect | 严格按 required_aspects 顺序；不能重复、插入或切换正在调用的维度；建立本维 40 秒截止 |
| preflight | 检查输入/来源、服务端执行许可、价格和计费类别、剩余时间、阶段顺序、次数、body、token 上界和费用余额 |
| reserved / started | 在供应商入口前，原子地记录本次尝试并保留最大费用；每维发现至多 1 次、归属至多 1 次，总计≤6 |
| received | 记录 usage/未知费用，核对输入和输出上界、计费类别、单请求/单维/整轮截止，重新验证冻结来源 |
| accepted | 原 wire 解析完成后再次检查截止与输入；随后复用原候选/归属语义校验 |
| failed | 失败尝试仍计数、仍保留费用；不重试，不隐式补调 |
| halted | 401/402/403、上界/计费合同异常或时钟异常阻断后续请求；保留完整维度及停止原因 |

重入供应商调用、重启同一预算、重复阶段、第四个维度和额外尝试均在供应商入口前被拒绝。预算是串行协调器内部状态，不为任意同进程 Python 代码提供沙箱隔离。

每次实际 timeout 为 `min(配置超时, 20, 本维剩余, 整轮剩余)`。除了 SDK timeout，还记录本次 started_at+timeout 的截止；返回、解析后、逐维验收后都要检查。body 为实际 wire JSON，含 schema，最多 16000 UTF-8 字节；发现输出≤1200，归属≤3000 tokens。所有 SDK 请求 max_retries=0。

普通 HTTP timeout 不证明服务器端计算已取消。本实现同步等待 SDK 返回/抛错，不用线程超时假装取消；客户端调用保持串行，但远端超时后是否继续计费/计算未验证。迟到响应记录尝试和费用后拒绝，不作为完成产物。120 秒是分类预算，不是排队/SQL/整个任务的可靠硬取消承诺。

## 6. 费用、usage 与失败记账

每次请求前：

```text
reserve = input_token_upper_bound × input_price
        + stage_max_output_tokens × output_price
```

先确认累计 reserve 不超过授权上限，再增加 ledger 并调用供应商。当前实现**整轮不释放任何预留**，即使已知 usage 较低，也不把差额用于追加调用。失败、超时或缺失 usage 更不能当作零费用释放。

usage 含合法 prompt_tokens/completion_tokens 时记录报告的实际费用；缺失/非法时 actual_cost=null。汇总 actual_cost_total 只有所有尝试费用均已报告才有值，否则 null；reported_cost_subtotal 仅是已报告部分的合计。模型语义产物是否通过与账单是否完整已知是不同事实：在可靠最大预留仍覆盖调用且其他校验通过时，usage 缺失不自动伪造失败或零账单。

报告输入超上界、输出超上限或额外计费类别时，该响应无发布资格并停止后续尝试。即使 token 字段缺失，明确出现的额外收费类别也会触发停止。真实供应商若违反其上界/计费合同，客户端无法追回已经产生的费用；本轮只验证拒绝和记账行为，没有真实账单保证。

合成例子使用币种 TEST，输入和输出单价均为 0.0001 TEST/token，合成输入上界每次 1000 tokens，模拟 usage 为 50/20。它们不是供应商报价或真实 token 估计：

| 场景 | 内部父状态 | 尝试 | 保留费用 TEST | 报告实际总费用 TEST |
| --- | --- | ---: | ---: | ---: |
| 三维全部完成 | complete | 6 | 1.8600 | 0.0420 |
| 授权上限 0.7 | partial | 2 | 0.6200 | 0.0140 |
| 全部无支持主题 | insufficient_evidence | 6 | 1.8600 | 0.0420 |
| 默认无费用授权 | skipped_budget | 0 | 0 | 未知/null |
| usage 全缺失 | complete | 6 | 1.8600 | 未知/null |
| 首维响应晚于 20 秒 | partial | 5 | 1.4600 | 0.0350 |

上限 0.7 的例子第一维已用最大预留 0.62，剩余 0.08 无法预留下一次发现的 0.22，故后两维 skipped_budget；不按较低实际 usage 追加调用。

安全证据 `.cache/p6c/execution-examples.json`，SHA-256：`6a5bb000508961281547102a220e48205e153171b4d447bb34408bc1a17d8f5f`。只保存预算、状态和去除主题名称/原文后的计数，不保存供应商请求正文或完整评论。

## 7. 冻结输入消费、串行分类和独立验收

协调器只接受 P6PreparedAnalysis 和 P6SharedBudget 的实际内部实例，不接受客户端字典或历史任务诊断。执行前重新验证整包/逐维摘要、最终范围、模型身份、完整目标集合及原始排名来源，并调用 validate_p6_plan。

预算 start 绑定每维 input_sha256。每维通过 get_input(aspect) 获取副本，分类器直接使用封存的 rows/units/items/query_scope/summary，不再重新选择候选或重建来源 ID。每个供应商调用前后和每维结束还会重新验证输入、来源及计划。输入/原始任务在执行中变化是全局硬失败，不返回可发布的部分成功。

analyze_aspect_topics 仍使用原 DISCOVER_PROMPT、CLASSIFY_PROMPT、wire/schema、候选和归属校验、recompute_topics、validate_topic_result。P6-B 已限制为最多 20 种表达，所以归属恰好至多一批，未复制或另建分类器。供给模型的单位数和支持片段不等于模型自由生成的计数；评论/观点去重与主题分母仍由程序计算。

每维检查工具参数、negative、范围、M_a、冻结原文/来源、完整批次、主题引用和样本覆盖，再复用未经修改的 validate_p6_completion / P0-B Validator 独立验收。分类器不能改写冻结 scope/summary/items/opinions；发生这种来源变动时全局拒绝。

测试中的同一评论含两个 service 观点时，主题评论数=6、观点数=7；去重按 review_id，不把观点数当评论数。另一个样例全量匹配 M_a=31，但只纳入 20 条评论，主题计数/分母都是 20，population_mode=sample，不外推为 31 或成功标注总数 D。

SQL/读取边界沿用 P6-B：准备退出事务并关闭连接后才能进入 C。分类测试禁止 get_top_issues、get_aspect_opinions 和重新连接；在已有临时写连接中修改数据库后，冻结结果仍使用原负面观点。没有分类缓存未命中回库分支，也没有删除 ReviewToolContext 的硬阻断。

## 8. 父子状态和 P6-D 公开门

子级保留 complete、insufficient_evidence、failed、skipped_budget；父级通过原 validate_p6_completion 返回 complete、partial、insufficient_evidence、skipped_budget、failed。全部必做维度分别通过才能 complete=true；部分失败时原 required_aspects 和所有子状态/原因始终保留。

内部 P6-A 映射原样保留：complete→ok/succeeded，partial→partial/partial，insufficient_evidence→unsupported/succeeded，skipped_budget 或 failed→error/failed。诊断显式记录 mapped_agent_status / mapped_task_status。

P6-D 尚未实现，因此增加独立的公开门而不直接发布内部成功子集：

- 内部 complete / partial 在 Agent 上仍为 unsupported，task_acceptance.complete=false，没有公开主题、成功 highlights 或回答模型调用。
- 内部 insufficient_evidence 仍为 unsupported。
- 内部 failed / skipped_budget 在实际 Worker 中保持 error/failed；全局输入/来源错误也是硬失败。
- 原单维、正向和其他路径的 partial 行为没有修改，旧前端无需接受新顶层状态枚举。

P6ExecutedAnalysis 的正文在隐藏 repr 的冻结 JSON 中；validated_results 只返回独立允许的维度，供未来 D 的内部适配使用。实际任务 JSON 只写入安全诊断、usage/预留、状态和原因，不默认保存候选主题正文或跨任务恢复许可。

## 9. 故障注入和验证结果

新测试使用真实临时 SQLite 排名和 P6-B 准备对象，走真实 wire 解析、原分类器和两级 Validator。独立来源夹具原本封锁 _call；C 测试恢复真实 _call 路径，只在预算后的供应商传输边界提供固定原始 JSON，没有用直接返回 TopicCatalog 的替身绕开预算。

| 测试类别 | 断言 |
| --- | --- |
| 1/2/3 维串行 | 顺序固定，2/4/6 次；输出1200/3000、timeout≤20、重试0 |
| 前置预算故障 | 费用不足、未知价格/授权/计费类别/token 上界、执行开关关闭时不进入供应商 |
| 防绕过 | 拒绝重启、重入、重复阶段/额外调用、第四维；不可用公共属性重置授权或停止状态 |
| 模型错误 | 发现/归属失败、首/中/末维超时、非法 JSON、截断均不自动重试；保留完整父目标 |
| 鉴权与 usage | 401/402/403 停止后续；缺失 usage 保留预留；超上界/未知额外收费停止 |
| 时间 | 单请求迟到、维度剩余18秒、全局121秒、配置7秒等注入；迟到不完成、截止无新调用 |
| 冻结与来源 | 缺失/损坏输入、诊断 JSON、错计划/范围/身份和执行中来源变动全局拒绝 |
| 无数据库回读 | 分类期间数据库变化不影响冻结输入；SQL入口被设为抛错仍能完成 |
| 原文与统计 | 去重、位置和来源、样本 S_a、覆盖与模型候选标记由旧 Validator 继续检查 |
| 实际 Agent | 模拟供应商开始时连接已关闭；完整内部执行仍不公开成功；默认费用授权为空时零尝试且受控失败 |

最终执行记录：

| 检查 | 结果 |
| --- | --- |
| 新增 P6-C | 25/25 PASS，含多组故障子项 |
| 原适用回归 | 512/512 PASS，P6-A/P6-B、P0-A/P0-B/P1-A/F01 均包含 |
| 功能合并 | **537/537 PASS**，206.564 秒，0 failure / error / skip |
| 最终失败映射及预算停止状态专项复验 | 25/25 PASS，22.058 秒，0 failure / error / skip |
| 静态私有接口审计 | 独立 FAIL：原13个失败子项，0 error / skip，无新增模块失败 |
| Python 编译、git diff --check | PASS |
| 外部收费服务 / 真实 Embedding / 生产库 | 0 次；未执行部署 |

功能合并封锁 socket/psycopg 外部连接；独立夹具封锁 OpenAI 客户端，C 还封锁额外意图/规划/Embedding 入口。固定供应商响应不等于真实模型语义质量或真实费用验收。旧测试没有删除、skip、expectedFailure 或放宽断言。

入场对 1594 个已有文件做 SHA-256 快照。本轮仅上述三个已有源码改变；旧测试、P6-A/P6-B/F01 报告、可信存储、冻结 RAG RuntimeQueryContract、人工评审资产和前端均保持原摘要，无删除。证据和资产核验在 `.cache/p6c/`，完整核对见 change-audit.json。

历史私有导入仍是 agents/reviews、review_retrieval、review_shadow_siliconflow、review_structured_evidence 及 reviews/e1a_b3_runtime、e1a_b3_worker、e1a_prediction、e1_preparation、embedding_store、exploratory_e1、historical_snapshot、rag_corpus、semantic_retrieval；本轮没有清理或放宽规则。

复跑（仓库根目录 PowerShell）：

```powershell
$env:PYTHONPATH='.;src;tests'
.venv/Scripts/python.exe -m unittest test_p6c_budget_execution -v
.venv/Scripts/python.exe .cache/p6c/run_checks.py functional
.venv/Scripts/python.exe .cache/p6c/run_checks.py static
.venv/Scripts/python.exe .cache/p6c/probe.py
.venv/Scripts/python.exe -m compileall -q src/data_analysis_agent/agents/review_budget.py src/data_analysis_agent/agents/review_execution.py src/data_analysis_agent/agents/review_topics.py tests/test_p6c_budget_execution.py
git diff --check
```

static 预期继续独立失败13子项。probe 仅保存安全状态/统计摘要；准备对象的时间和输入摘要随复跑变化，不作为跨进程授权。

## 10. 未验证风险与 P6-D 准入

**P6-D 开发准入 PASS**：可以在已独立验收的子结果上设计公开适配、引用公平分配、partial 显示、复制和导出，但必须继续保留全部目标、逐维状态、候选未经人工确认和真实分母，不能直接把父 partial 传入旧完整结果压缩器。

真实执行仍 BLOCKED：未配置或验证真实费用授权、价格、tokenizer 上界、供应商额外计费类别、账单或取消行为；未运行真实 PostgreSQL 并发与真实模型质量测试。P6-B 的 SQLite 同快照证据和 PostgreSQL 协议检查继续保留，不能由本轮离线分类推导生产数据库已验收。

本阶段没有生产读写、收费模型/Embedding、生产部署或冻结资产修改。完成 P6-C 后停止，不进入 P6-D。
