# P1-A 独立验收

日期：2026-11-09。审查对象：当前工作区的可信分析状态实现及 `2026-11-09_P1-A-trusted-analysis-state-acceptance.md`。

**结论：FAIL。** 排名来源、完整性、范围隔离及明确完整请求的承接通过；待完成主题目标之后的短情感追问存在端到端失败，不能把既有 450 项通过直接作为全部验收通过。P6 执行准入为 BLOCKED，设计准备见独立的 `P6-implementation-readiness.md`。

本扩展在既有任务库内封存结果摘要，并在后续请求重新验证原始结果、恢复最小排名。它没有新建状态数据库，也没有实现多维度下钻。本轮只新增独立测试和两份报告，没有修复或改变运行时代码。

## 1. 必须修复的发现 F01

**待完成主题目标后的“那好评呢？”执行统计后报错**（功能正确性问题）。

位置：

- `agents/review_acceptance.py:35,117–140`：短情感表达符合 REFERENCE，业务目标继承上一轮 themes，同时情感切为 positive。
- `agents/review_intent.py:219–220`：pending_query_type 对 short_aspect 生效，但没有对 short_sentiment 生效；从此前 issues 查询编译为 aspect_summary。
- `agents/reviews.py:355–360`：最终验收发现主题目标与统计计划冲突，阻止完成并形成 business_error。

实际 Worker 三轮对话：

1. `100013588053的差评主要集中在哪些方面？` → succeeded / ok，六维负面排名已验证。
2. `具体体现在哪里？` → succeeded / unsupported，保存 awaiting_aspect 主题目标，查询与模型调用均为 0。
3. `那好评呢？` → **failed / error**。

第三轮关键断言：

| 字段 | 实测值 |
| --- | --- |
| request_goal.levels | themes |
| request_goal.sentiment | positive |
| query_plan.analysis_type | positive_aspects |
| required_tools | get_positive_aspects |
| validation.status | passed |
| task_acceptance.plan_check.passed | false |
| task_acceptance.status / complete | failed / false |
| 外层任务 / Agent 状态 | failed / error |

这不是正负情感串线或错误统计被发布成功：情感正确切换，P0-B 验收正确拒绝错误能力。但合法追问在查询之后以业务错误结束，说明目标继承与计划继承没有达成一致。

**最小修复方向：** 保持 P0-B 独立目标验收，单独修复 Router 对待完成主题的短情感切换，使其与冻结目标一致；如无法唯一确定目标，应在执行前澄清。不能把验收目标降成 statistics 来制造通过。本轮只记录，不修改已确认契约或运行逻辑。

对照实验将服务端 context 中的 analysis_state 去掉、保留原 users/validated 后，同一第三轮仍失败。因此不能简单将问题归咎于新摘要或状态存储；它是历史意图承接与独立目标组合的兼容缺口。本轮没有确定最早引入提交。

若跳过此项，P6 会在现有不一致的目标继承之上增加多个维度与部分完成状态，扩大状态分歧。应先修复并重跑失败用例，再开放 P6 执行。

## 2. 独立测试路径与夹具核查

新增 `tests/test_p1a_independent_validation.py`，没有复用原 P1-A 的评论语料或 save 辅助方法作为可信来源生产者。

独立创建临时业务 SQLite 和任务 SQLite；使用真实导入、标注存储、enqueue、claim_task、`TaskWorker._execute()`、finish_task、get_task 和 load_analysis_context。只模拟 Agent 配置身份读取，不模拟任务结果、封存摘要、排名提取或验收。网络、PostgreSQL、OpenAI 客户端及主题模型入口均设为抛错；没有主题模型执行。

页面同时选择两个 SKU，首轮明确指定 A，实际查询缩小为 A。这比仅以单 SKU 调用 Agent 后直接 finish_task，更接近真实选择范围与有效范围分离的路径。未发现首轮排名在该 Worker 路径上与原夹具不一致；漏检发生在第三轮短情感切换的覆盖缺口。

合成 SKU A 的人工构造预期如下，成功标注评论数 D=6；不同维度有重叠评论，计数不得相加：

| 排名 | 维度 | 负面去重评论数 | 分母 |
| --- | --- | ---: | ---: |
| 1 | service | 5 | 6 |
| 2 | effectiveness | 4 | 6 |
| 3 | logistics | 4 | 6 |
| 4 | quality | 2 | 6 |
| 5 | price | 1 | 6 |
| 6 | packaging | 0 | 6 |

并列维度按 aspect 字符串排序。测试独立用标准库 JSON+SHA-256 计算摘要，不使用被测 analysis_result_integrity() 作为期望值来源。

## 3. 关键状态断言

| 核验 | 结果与证据 |
| --- | --- |
| 来源任务与 result_id | 与 Worker 保存的实际 task_id / r1 一致，非前端附带字段 |
| scope 与指纹 | 结果为单 SKU A；完整 BaselineScope 的独立规范 JSON 摘要一致 |
| 六维计数、顺序、分母 | 与上述预先构造的真值逐项一致；D=6，六行完整 |
| 任务完整性 | 摘要独立重算绑定 version、task_id、terminal_status、原始 parameters、完整 result |
| 结果完整性 | result_sha256 与来源 r1 原始记录一致；重新执行数字/业务校验后，其独立摘要与 validation_sha256 一致 |
| 旧任务 | 无保存时摘要时保留查询条件，不提供可信排名；没有回填一个新哈希冒充旧封存 |
| 跨会话 / 不存在的任务 | 不恢复排名 |
| 商品、时间、数据集、标注身份变化 | 不兼容 ranking_basis 和 pending_goal 被清除 |
| 父任务篡改 / 不完整结果 | 摘要失配或结果缺失，不能提供排名 |
| 真实 Worker 失败 | 配置身份变化导致任务失败；若向前回退，来源仍是原成功任务，不能是失败任务 |
| pending_goal | 从已封存澄清任务恢复 themes / negative / awaiting_aspect |
| 本轮完整明确请求 | 数量、原文、完整好评维度请求均覆盖 pending，正常通过业务验收 |
| 本轮短情感请求 | F01 失败，仍需修复 |
| 客户端可信状态注入 | AnalysisParameters 拒绝 analysis_state / ranking_basis 字段 |

原有 P1-A 测试另外覆盖了算术自洽篡改、重复读取无写入、执行权不被摘要削弱、字段不完整、旧目标不重复语义解析等，本轮将它们全部重新运行，而非仅引用原报告。

## 4. 实现审查与信任边界

- `jobs/store.py:282–305`：摘要由服务端终态写入生成，与 result_json 同一条 UPDATE 保存；原 running/worker_id 条件未放宽。摘要位于已有 progress_json，未新增表。
- `jobs/analysis_context.py:12–88`：先检查任务类型、会话、身份与范围，再核验保存时摘要；有界父链及环检测保留。加载的是任务库原始结果，非浏览器贴出的统计。
- `jobs/analysis_state.py:37–97`：新任务 succeeded、数字和业务通过后，还重建计划/运行契约并重新校验；要求单 SKU、未限定维度的当前负面统计、六维完整、计数有界、分母定义和稳定排序一致。
- `jobs/analysis_state.py:100–147`：pending 是未完成用户目标，不是分析事实；最终范围投影可清除不兼容的历史输入。pending_context 本身不是鉴权器，只能消费受控加载器的上下文。
- `agents/reviews.py:422–423`：最终有效范围上的 analysis_state 是历史输入投影；本轮新结果不是在这里自封存，而由 Worker 完成任务后保存。

SHA-256 不是数字签名：无法防御具有数据库写权限、能同时重写结果与摘要的人。此限制已在原报告明确，本次未将其误认作新漏洞。受控 API 不允许客户端提交 result/progress 作为可信结果，内部范围投影不能单独充当来源认证。

## 5. 历史完整性不等于当前数据新鲜

独立测试在临时业务库导入并标注一条新包装负评后：

- 历史任务完整性继续成立，恢复 D=6、packaging=0 的原排名。
- 当前真实统计变为 D=7、packaging=1。
- P1-A 仍恢复原历史快照，这符合“历史排名”含义，不应静默替换数字。

这不是 P1-A 的来源错误，但证明 P6 在开始分类前必须另做当前样本口径检查。仅有 scope_sha256 和历史任务摘要，不能证明当前业务库未变化。

## 6. 13 个静态私有导入失败

重新执行原静态审计：1 个方法、13 个失败子项、0 error。独立解析当前源码与 Git 暂存基线，以下各模块的私有导入语句逐项完全相同（13/13）。不依赖原报告的哈希结论。

| 模块（相对 src/data_analysis_agent） | 私有符号 |
| --- | --- |
| agents/reviews.py | _scope |
| agents/review_retrieval.py | _document、_scope、_in_scope |
| agents/review_shadow_siliconflow.py | _LimitedStream |
| agents/review_structured_evidence.py | _scope |
| reviews/e1a_b3_runtime.py | _validate_sources |
| reviews/e1a_b3_worker.py | _predict_r0_validated、_validate_sources |
| reviews/e1a_prediction.py | _validate_sources、_judge、_input_digest |
| reviews/e1_preparation.py | _validate_sources |
| reviews/embedding_store.py | _snapshot |
| reviews/exploratory_e1.py | _validate_sources |
| reviews/historical_snapshot.py | _review_state |
| reviews/rag_corpus.py | _snapshot |
| reviews/semantic_retrieval.py | _search_effective_opinions、_document、_review_state |

影响：违反仓库“仅通过公共接口导入”的静态约束，存在私有实现变更带来的耦合风险；本次没有证据表明这些导入已导致运行故障。它们不是 P1-A 新增问题，不是 F01 的原因，也不能被描述为静态检查已通过。后续应以单独维护范围公开适当接口并更新调用方，不能为本次验收放宽断言或改动冻结评审模块。

## 7. 独立执行结果与结论分层

| 检查 | 结果 |
| --- | --- |
| 新增真实 Worker 独立测试 | 11 项：10 通过、1 失败，0 error / skip |
| P0-A / P0-B / P1-A 原适用回归重新运行 | 450/450 通过 |
| 合并完整运行 | 461 项：460 通过、1 失败，128.228 秒 |
| 私有接口静态审计 | 1 方法内 13 个既有失败子项 |
| 生产库、收费 LLM、Embedding、部署 | 均未执行 |

独立测试首轮曾把“选择不存在商品”预期为 failed，但当前已确认行为是澄清类 unsupported，由 Worker 保存为 succeeded；这属于测试设置错误，已改成真实配置身份失配来制造执行失败。没有修改运行逻辑，也没有将 F01 改为 expectedFailure 或放宽断言。

判定：

- 可信排名来源、计数、分母、范围和完整性：**PASS**。
- 常规 pending 恢复与完整明确请求覆盖：**PASS**。
- 短情感追问的端到端承接：**FAIL（F01）**。
- P1-A 独立整体验收：**FAIL**。
- 立即进入 P6 执行：**BLOCKED**。可按第二份报告准备修复与实施，但不能宣称准入全部满足。

复跑：

```powershell
$env:PYTHONPATH='src;tests'
.venv/Scripts/python.exe -m unittest test_p1a_independent_validation -v
.venv/Scripts/python.exe -m unittest test_trusted_analysis_state test_review_router_p0a test_review_acceptance_p0b -v
.venv/Scripts/python.exe -m unittest test_dependency_boundaries.DependencyBoundaryTests.test_implementation_uses_public_interfaces_and_one_connection_factory -v
```

本机证据在 `.cache/p1a-independent/`：final-independent-tests.txt、regression-summary.json、regression.txt、worker-probe.json、private-import-baseline.json、static-audit.txt。报告已保留核心断言，不以临时日志作为唯一证据。

本轮开始快照覆盖 1619 个既有文件，核验均无变化；新增内容仅独立测试及两份报告。未验证真实 PostgreSQL、旧 Windows 异步链路、生产负载或真实模型质量。审查结论：先修复 F01 并复验，再开放 P6。
