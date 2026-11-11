# Agent 第一阶段：G25-T1 RAG 命题解析与最终离线验收

日期：2026-11-11，Asia/Taipei（UTC+08:00）。**本轮最小修复及冻结离线业务验收 PASS：Oracle v2 30组、62轮全部通过；RAG专项143/143、适用兼容回归762/762通过，0 skip。** 原61轮PASS保持；G25-T1从FAIL转为有合法来源及精确引用的PASS。

这仅证明固定合成语料/检索替身下的工程与业务契约，不证明真实评论检索质量、真实模型质量或真实PostgreSQL事务一致性。未连接真实数据库、调用收费LLM或真实Embedding；P6-E1B继续BLOCKED，PG-01～08 NOT EXECUTED。本轮完成后停止。

## 1. 真实复现与首个失败节点

先使用当前v5、正式Oracle v2及原v2.1评分器重新执行62轮，得到 **61 PASS / 1 FAIL（G25-T1）**，未依据旧报告推断当前行为。

原问题：`100013588055有没有物流很慢的反馈？`

已绑定范围：商品 `100013588055`；`source=reviews_csv`、`dataset_version=stage1-synthetic-v1`；当前窗口 `2023-02-01～2023-02-28`；标注身份为 stage1-labels / stage1-labels-v1，来源URL为合成的 labels.stage1.invalid。目录读取先于Router/Planner，resolved_product_ids为该单商品。

| 环节 | v5修复前 | v6修复后 |
|---|---|---|
| 原始问题、QueryScope、resolved_product_ids | 原问题；单商品055，经过目录与范围绑定 | **完全保留** |
| RequestGoal | null，走独立RAG契约 | 相同，未降级成统计 |
| Router / Planner | semantic_evidence；required_tools=[search_semantic_evidence] | 相同 |
| SemanticQuery.question / 检索输入 | 原始带SKU问题 | **逐字节内容不变**，不替换Embedding输入 |
| RuntimeQueryContract.evidence_question | 原始带SKU问题 | `有没有物流很慢的反馈？` |
| review_reported_facts.target | null | facts=[delivery_slow]，relation=null，text=物流很慢 |
| RuntimeQueryContract.support_status | retrieval_only，required_facts为空 | supported，要求原有delivery_slow事实 |
| 检索候选 | 4条，来源/位置已校验 | **同样4条，原始候选与来源身份逐对象相同** |
| assessment / permitted_keys | insufficient / []，query_semantics_uncovered | sufficient / 仅1条，explicit_report_only |
| Validator | 当时通过来源校验，但命题未获许可 | 来源、有限命题、引用及任务契约校验通过 |
| task_acceptance | null，RAG使用独立事实许可 | 仍null，**未强塞complete=true** |
| Agent / Worker | ok / succeeded，表示检索流程结束，未完成原事实任务 | ok / succeeded，有被许可的买家自述引用 |
| 最终回答 | 不能确认候选直接支持问题，暂不作确定判断 | 仅报告本次检索中评论#101的买家自述并引用原文 |

评分器首个失败字段为 **tool_target**；真实调用链的根因更早：RuntimeQueryContract编译时，`evidence_question`只分离日期前缀，SKU仍留在有限命题前面，`target`的整句语法匹配失败。Router和检索商品范围本身正确，不是数据覆盖或意图识别失败。

原始 [修复前运行](E:/AllProject/data-analysis-agent/.cache/agent-stage1-g25/before/run-01.json)、[修复前评分](E:/AllProject/data-analysis-agent/.cache/agent-stage1-g25/before/assessment-01.json)均独立保存。完整九字段、候选、契约、逐事实评估及回答前后对照见 [g25-before-after-trace.json](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-g25/g25-before-after-trace.json)。

## 2. 最小改动与商品绑定边界

| 文件/函数 | 修改 |
|---|---|
| [review_task_contract.py / evidence_question、build_task_contract](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_task_contract.py:18) | 接受调用方已经独立校验的scope；仅单商品、前缀精确匹配且剩余文本符合原有限命题语法时，分离该冗余SKU |
| [review_retrieval.py / execute_retrieval、validate_retrieval](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_retrieval.py:142) | 许可计算和重验都向同一函数传入已核验范围；所有原来源、范围、偏移、身份、上下文校验保留 |
| [review_answers.py / semantic_answer](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_answers.py:390) | 最终回答的目标复核使用已验证的来源范围，避免与执行端规范化规则矛盾 |
| [review_structured_evidence.py / prepare_input](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_structured_evidence.py:122) | 结构化证据目标复核使用绑定契约范围，保持原始问题与规范化命题一致 |

后两处仅传递范围参数，是现有消费端必要的一致性修改，没有扩大回答能力或重写RAG。`review_reported_facts.py` **整个文件字节摘要未变**：target语法、PATTERNS、蕴含边、上下文否决、逐事实许可、same_review关系和精确引用逻辑不变。

分离规则与安全依据：

1. **目录校验仍在服务端入口完成**：先读取当前source/dataset下商品目录，验证所选商品，再由Router独立绑定显式商品；文本出现055不等于已完成绑定。
2. `evidence_question`是内部文本处理函数，不查询目录，也不签发证据许可。其scope必须来自上述绑定链；不传scope时，带SKU原句保持不变。任意字典、客户端自报ID或规范化文本都不是发布授权。
3. 当前范围必须只含一个商品，且问题在合法开头精确包含该ID。范围冲突、多商品、句中SKU、较长数字串或第二商品条件不满足规则时，不能删除。
4. 不做“删除数字”正则。只按绑定ID的完整字面长度切分，再调用**原target语法**检查完整剩余问题；否定、比较、数量、内部时间条件等不被删去来凑出一个合法命题。
5. 原有日期前缀分离继续受日期/范围一致性检查约束；原始问题、SemanticQuery、契约original_question不改写，范围及scope_sha256保留完整时间条件。
6. 规范化只改命题识别。后续仍须独立证明来源、原文、上下文、每个事实和必要关系；失败不能产生permitted_keys。

“已知ID + 自报匹配scope但目录为空”的实际Agent反例在目录门即停止，规范化函数未被调用；未知目录商品也零查询、零检索。不是只凭字符串或scope形状判定商品可信。

[生产v5→v6精确差异](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-g25/production.v5-v6.diff)；[许可标准保全证明](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-g25/permission-policy-preservation.json)：RuntimeQueryContract结构/版本不变，task_matches不变；执行、重验、结构化输入及回答函数除传递scope参数外AST相同，没有删除校验。

## 3. 事实许可为何只增加一条

修复前后全部候选与来源身份相同，四条原文观点都为“物流很慢”。但**文本命中不等于许可**：

| 评论 | 修复后结果 | 原规则依据 |
|---|---|---|
| #101，商品055，annotation_id=1101 | 唯一permitted候选；delivery_slow直接支持 | 引用是完整独立断言；上下文没有改变/否决物流事实 |
| #102 | 不许可，context_unresolved | 引用前仍有未获解释的上下文前缀，不能仅凭子串供证 |
| #103 | 不许可，context_unresolved | 同上，保留原上下文否决 |
| #104 | 不许可，context_unresolved | 同上，保留原上下文否决 |

唯一许可key为：

`rag-opinion-v1:0beaa95e402eeb91e098869d1764063cec7b11c170c991774a23efc9ad0b404c`

其原文字符区间是 **[10,14)**，切片严格等于“物流很慢”；source/dataset、product、annotation、profile、版本摘要和出处信息仍由原来源校验处理。`fact_bindings`记录delivery_slow、review_id=101、key、位置及quote_sha256，最终回答引用集合与permitted_keys一致。

回答为：“在本次检索到的评论中，有买家明确报告了以下情况。评论#101（2023-02-10）写道：‘物流很慢’。”保留仅限检索买家自述、Top-K不作统计总体、相似度不等于事实支持等限制。没有外推全量投诉率、因果、客观物流性能或整体质量。

旧v5失败快照没有被追溯升级：当前重验发现旧规范化目标与新绑定目标不一致，历史公开回答返回failed且无引用；旧快照、旧回答和失败轨迹原字节保留。[历史与版本绑定反例](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-g25/adapter-and-history-checks.json)同时记录错误父版本、越界源码及错误摘要均被拒绝。

## 4. RAG安全反例

新增 [test_agent_stage1_g25_rag.py](E:/AllProject/data-analysis-agent/tests/test_agent_stage1_g25_rag.py)，**19项全部通过**；与已有来源、命题、Validator及RAG契约回归合计 **143/143 PASS**。

| 场景 | 断言与实际结论 |
|---|---|
| 匹配SKU、明确命题 | 目录先于规划；055→delivery_slow；仅一条支持引用，完整Validator通过 |
| 原问题与规范化依据 | 原问题/检索输入不变，契约scope_sha256正确，命题文本单独保存 |
| 仅文本含已知ID，没有独立scope | 不分离SKU，target仍不识别 |
| SKU与scope不一致 | 不分离；无借用另一商品范围的许可 |
| 目录未知商品 | 实际Agent目录门拒绝，业务查询与结果为0 |
| 文本、scope看似匹配但目录为空 | 入口拒绝；规范化函数未被调用 |
| 多商品、归属不唯一 | 不删除前缀，不授予单商品事实许可 |
| 原合法无SKU问句 | 与已规范化问句产生完全相同的原事实许可 |
| SKU位于句中、长数字或否定前缀 | 不错误删除 |
| 否定、比较、数量、内部时间或第二商品 | 不删去这些条件来获得匹配 |
| 合法日期+SKU | 保留原查询及完整scope；同范围才允许分离前缀 |
| 日期冲突 | 不授权SKU分离，实际Agent零查询 |
| 有候选但事实相反/否定上下文 | permitted_keys=[]，不变成sufficient |
| 四条候选只有一条上下文可确认 | 仅#101获许可，另三条仍拒绝 |
| 原文偏移、来源、商品或annotation被篡改 | validate_retrieval拒绝 |
| 自报规范化文本或permitted_keys被篡改 | 重新计算后拒绝 |
| 053向量候选缺口 | no_candidates、insufficient、无引用，不伪造证据 |
| 055两个事实分别由不同评论支持 | 两条来源均验证、各事实direct_report，但same_review关系不成立，仍无许可/无高亮 |
| 持久化结果重新公开 | 再次核验原问题、范围及精确绑定，仅公开#101 |

关系反例使用新增的两条合成来源，通过原SemanticRetrievalService、FakeEncoder/FakeRepository完成校验，未改原冻结语料。首轮该新反例未接入正确的测试服务钩子，仍返回原四条候选，触发“4!=2”的测试失败；修正新增测试的semantic_service注入点后通过，原失败日志保留。**正式62轮评测一直使用未修改的原Harness/语料/固定响应，初次生产修复复评时已为62/62；该反例修正不参与制造正式通过率。**

## 5. v6绑定与测试适配范围

[source-revision.v6.json](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-g25/source-revision.v6.json) SHA-256：

`3e4ff014bb6b2f81e40cf8196093c16e2a7cf9e173485eec2a24f82135410fd1`

父版本为保留的v5（SHA-256 `1a257a154f8db17e66b7d207ccd02ead75de68419064f176e7dd13c735c746d5`）。生产文件集合仍为88个，较v5仅四个文件改变；旧v5绑定没有覆盖。

旧评测入口只允许先前阶段的源码变更列表，不能诚实地用v5摘要执行v6。新增 [run_agent_stage1_g25.py](E:/AllProject/data-analysis-agent/scripts/run_agent_stage1_g25.py)只做版本化源码绑定：严格校验旧冻结资产、v4/v5父链、四个获准差异与全部88个摘要，再复用原Harness和原v2.1评分器。兼容回归复用原隔离执行器，只替换版本绑定入口；其数据库、网络和PostgreSQL程序阻断保持原样。

原17项G24/G30专项仅调整setUpClass尊重调用方已核验的源码版本；业务断言不变。另一个旧单测ID `test_g25_t1_permission_failure_remains_no_published_claim`曾专门固定记录已知缺陷；现在按**原本就要求合法许可的冻结Oracle v2**更新断言：输入、语料、数据模式不变，必须来源/引用验证通过且仅评论#101获许可，不能将任意候选当事实。它不是人工Oracle或评分规则变更。[两处测试适配差异](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-g25/test-binding-and-defect-expectation.diff)可独立审查。

共核验 **918个既有文件**：四个获准生产文件及上述两处测试适配发生变化；其余旧冻结文件、评分器、数据夹具、人工评审资产和失败证据未变。

## 6. 完整独立验收

| 验证项 | 结果 |
|---|---|
| G25-T1修复前→后 | FAIL→PASS，未改变独立预期 |
| 新增安全反例 | **19/19 PASS** |
| 现有与新增RAG回归 | **143/143 PASS** |
| 适用后端完整兼容回归 | **762/762 PASS，0 failure、0 error、0 skip** |
| 原兼容覆盖 | **743个ID全部保留**，新增19项，没有缩小覆盖 |
| Oracle v2 | **30组、62轮：62 PASS / 0 FAIL / 0 PENDING** |
| 原61轮PASS | **61/61保持，无退化** |
| State / Router / Goal / Planner / 工具目标匹配 | 各60/60自然输入符合 |
| 工具前阻断 / 故障拦截 | 10/10、2/2 |
| 严格完整任务 / 对话整体契约 | 43/43、30/30 |

G24/G30零执行澄清、17项专项、F01/P6-B合法正向统计/主题分析、既有P0/P1、partial/stale及G28/G29故障防护均包含在完整兼容与62轮评测中，未退化。

完整结果：[RAG回归](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-g25/rag-regression.json)、[762项兼容与全部测试ID](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-g25/full-regression.json)、[原始完整日志](E:/AllProject/data-analysis-agent/.cache/agent-stage1-g25/full-01/tests.txt)、[正式Oracle评分](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-g25/oracle-v2-assessment.json)、[62轮完整原始链与比较](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-g25/oracle-v2-turns.jsonl)。

原Oracle v1/v2/pending、正式人工裁决、v2.1评分器及历史失败记录保留。第一阶段固定评测集的唯一剩余FAIL已消除，没有发现新的P0/P1退化；这不代表所有自然语言输入都能理解或都应许可。

## 7. 能证明与不能证明的能力

**本轮能证明**：真实Worker、State、Goal、Router、Planner、工具、Validator和回答模板在既有合成边界下遵守固定多轮契约；已验证单商品范围可与冗余SKU前缀分离；规范化不会直接授权候选；来源、上下文、关系和引用仍独立决定事实许可；失败历史不自动升级。

**本轮不能证明**：真实055或053的检索召回/准确率、真实评论主题语义质量、真实Embedding覆盖、真实供应商计费/取消行为、真实PostgreSQL并发与事务一致性、前端实机验收或生产可用性。053实际覆盖缺口未被离线替身“修复”。

完整兼容运行仅使用本轮synthetic-temp合成SQLite夹具，13,960次连接全部受目录限制，外部尝试为0。Oracle评测的64个协议连接替身全部关闭；真实PostgreSQL/业务库、收费模型、真实Embedding调用均为0。没有执行P6-E1B恢复、修改其安全门或进行部署。

复验（新目录，避免覆盖旧日志）：

```powershell
.\.venv\Scripts\python.exe scripts/run_agent_stage1_g25.py --output-dir E:/AllProject/data-analysis-agent/.cache/agent-stage1-g25/oracle-recheck
.\.venv\Scripts\python.exe scripts/run_agent_stage1_g25.py --regression --source-revision docs/evaluation/agent-stage1-g25/source-revision.v6.json --targets .cache/agent-stage1-g25/rag-targets.json --output-dir .cache/agent-stage1-g25/rag-recheck
.\.venv\Scripts\python.exe scripts/run_agent_stage1_g25.py --regression --source-revision docs/evaluation/agent-stage1-g25/source-revision.v6.json --targets docs/evaluation/agent-stage1-g25/regression-targets.json --output-dir .cache/agent-stage1-g25/full-recheck
```

[汇总](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-g25/summary.json)、[执行产物摘要](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-g25/artifact-sha256.json)、[含本报告的最终摘要](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-g25/final-artifact-sha256.json)。

**结论：G25-T1最小修复PASS；当前冻结第一阶段离线业务验收PASS。真实数据库/模型/检索质量仍未验收，P6-E1B仍BLOCKED。本轮停止，不自动开展下一阶段。**
