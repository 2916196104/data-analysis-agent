# P1-A · 商品评论 Agent 可信分析状态扩展验收报告

日期：2026-11-09。前置：P0-A、P0-B 离线验收及现有 P6 方案。

## 结论

**实现及离线功能验证完成，等待独立验收。P6 Planner 已具备受范围约束的可信历史排名输入，但本阶段没有实现 P6 自动选维度、工具编排或主题分类。**

- 最终功能套件 **450/450 通过**：包含 P0-A/P0-B 全部 432 个适用测试及 P1-A 新增 18 项，0 failure / error / skip。
- 目标两轮对话可恢复六维负面排名、成功标注分母、精确范围和来源引用。第二轮仍保持现有澄清行为，查询执行与模型调用均为 0；同时携带经重新验证的历史排名。
- 扩展到任务、参数、依赖和包结构检查的一轮运行：468 个测试方法，1 个静态审计方法内有 **13 个既有私有导入失败子项**，详见测试节。不能声称全仓库检查全绿。
- 没有新增表、迁移、平行状态数据库、Agent 框架或客户端可信统计参数。未调用收费模型、Embedding，未读写生产数据库或部署。

## 1. 审计与修改前结构

审计了 `jobs/analysis_context.py`、`jobs/store.py`、Worker 分析入口、任务参数模型，以及 `reviews.py`、`review_intent.py`、`review_plan.py`、`review_acceptance.py` 和现有上下文/任务持久化测试。

原有机制：

```json
{
  "user": {"task_id": "父任务", "question": "上一轮问题"},
  "users": [],
  "turns_read": 1,
  "validated": {
    "task_id": "已验证任务",
    "scope": {},
    "sentiment": "negative",
    "analysis_type": "issues",
    "queries": [{"kind": "issues", "parameters": {}}],
    "result_refs": ["r1"]
  }
}
```

其中 scope 实际包含完整商品、来源、数据集、标注身份、日期、基线及截止时间；上面省略具体值。`validated` 原本重新进行数字/来源校验，但只恢复条件和结果 ID 字符串，没有六维计数、分母、排序、保存时完整性摘要或结构化待完成目标，也没有重新执行 P0-B 业务验收来授予排名信任。

任务结果已经保存在既有 `tasks.result_json`；`request_hash` 是请求幂等哈希，不能证明保存后的结果未变。Worker 原本只把经过参数模型校验的会话 ID、父任务 ID 和选择范围交给 `load_analysis_context()`，没有接收前端统计数字的入口。

修改业务代码前新增 9 个隔离测试：1 failure、2 errors，直接暴露排名和状态字段缺失；之后扩展为最终 18 项。基线记录：`.cache/p1a/before-tests.txt`。

## 2. 修改后的状态结构

保留原有条件恢复结构，新增 `analysis_state` 投影：

```json
{
  "version": "review-analysis-state-v1",
  "ranking_basis": {
    "source": {
      "task_id": "来源任务 ID",
      "result_id": "r1",
      "integrity_version": "analysis-task-integrity-v1",
      "task_sha256": "保存时摘要",
      "result_sha256": "r1 原始结果摘要",
      "validation_sha256": "本次数字和业务复核记录摘要",
      "finished_at": "来源任务结束时间",
      "snapshot_at": "来源分析快照时间"
    },
    "scope": {},
    "scope_sha256": "完整范围指纹",
    "sentiment": "negative",
    "denominator": {"field": "successful_reviews", "value": 3},
    "ranking": [
      {"rank": 1, "aspect": "effectiveness", "negative_review_count": 1, "negative_rate": 0.333333, "denominator": 3}
    ]
  },
  "pending_goal": null
}
```

示意只展示一个排名行；实际必须具有完整六行。示例数字来自临时合成评论，不是生产数据。排名按 `(-negative_review_count, aspect)` 排序，保留零计数维度，不在本阶段选择前三项。

`pending_goal` 为另一类状态：合法澄清任务保存的用户请求，不是分析事实。字段包含 source、scope、scope_sha256、原 P0-B `RequestGoal` 快照、`status=awaiting_aspect` 和 `completed=false`。当前仅恢复单 SKU、负向、主题目标缺维度的澄清路径；不会把失败/证据不足的任务数字升级为可信排名。

这些字段是服务端历史输入的投影。当前请求自己的 request_goal 仍由 P0-B 冻结；新任务结束并保存后，下一次加载才会为该任务产生带 task_id 的来源引用。恢复时不直接相信已经保存的派生 analysis_state，而是重新读取原始任务结果构建它。

## 3. 保存、引用与重新核验

### 保存时

`jobs.store.finish_task()` 仅对 analysis 类型的字典结果，在既有 `progress_json.analysis_integrity` 中保存：

```text
version = analysis-task-integrity-v1
sha256 = SHA256(canonical_json([version, task_id, terminal_status, parameters, result]))
```

完整参数包含会话、上下文及执行范围；完整结果包含原始统计、请求目标、计划和业务验收。摘要与结果由同一条终态 UPDATE 保存。原有 `status='running' AND worker_id=?` 写入权限条件保留，过期 Worker 不能覆盖任务或摘要。任务表、schema version 和请求幂等哈希不变。

摘要不是由前端传入，也不从待保存结果中接受“我已可信”的自报字段。它是数据库内部的完整性依据，**不是数字签名**：不能防御拥有数据库写权限、能够同时改写结果及摘要的管理员或攻击者。

### 后续请求

1. Worker 仍通过 `load_analysis_context(task_db, parameters)` 加载有限父任务链。先执行原有 analysis 类型、conversation_id、身份及范围边界检查，不做跨会话搜索。
2. 从任务库重新读取原始 parameters/result/progress。存在新摘要但失配时停止这条链，不能改用自报的 passed 标志放行。损坏 JSON、任务不存在或不完整时安全降级。
3. `restore_ranking()` 要求任务 succeeded、无 error、结果 ok、原数字校验 passed、原业务验收 passed 且 complete=true。失败、partial、insufficient_evidence 不具备排名资格。
4. 重新构造已保存查询计划及运行契约，复用 `validate_report()`、`expected_result_scopes()` 和 P0-B `validate_task_completion()`，验证原始结果、范围及实际业务完整性。不是只读取旧验收标志。
5. 只接受单 SKU、未限定 aspect、当前窗口、单个 get_top_issues 结果；排除正向汇总、单维度统计、基线比较、截断列表和其他复合能力。
6. 要求六维集合完整，分母与负面计数为合法整数且有界，分母定义为 successful_reviews；逐行核对 issues 与 current.aspects 的计数及稳定排序，再生成最小排名投影与来源摘要。
7. 在选择范围变化后，以及 Agent 最终绑定商品/日期后，再用 `analysis_state_for_scope()` 比对完整范围及指纹。新商品、新日期、基线、截止时间、来源、数据集或标注身份不能使用旧排名。

`analysis_state_for_scope()` 是范围投影器，不是独立认证入口；其输入必须来自服务端任务库加载器。API 参数模型继续拒绝 analysis_state/ranking_basis 等客户端字段；即使直接向加载器附带伪造数字，也会被忽略。Worker 没有增加前端传入 conversation_context 或可信排名的路径。

## 4. 待完成目标与旧任务兼容

- `restore_pending_goal()` 仅接收具有有效保存时摘要、同会话/范围、外层 unsupported 且 needs_clarification=true 的单商品负向主题澄清结果，使用已冻结的 P0-B 请求快照。
- `pending_context()` 将最新结构化目标及其来源交给原有意图/业务目标恢复逻辑；该来源及更早的旧意图文字不再重新解析。更新的用户请求仍可按原有优先级覆盖目标。
- `review_intent._user_conditions()` 仍使用原 `pending_query_type='aspect_drilldown'` 机制；`review_acceptance.build_request_goal()` 只改变继承信息的取得方式，没有修改 P0-A/P0-B 的当前请求语义规则或已确认契约版本。
- 原有用户文字和查询条件仍保留，便于旧任务回退及范围实体恢复。跨会话、范围变化或摘要失配不恢复结构化目标。
- **旧任务没有保存时摘要时，只保留原查询条件恢复，不提供可信排名，也不伪造历史封存依据。** 如需让其成为 P6 的可信排名输入，需要在新版本中重新执行并保存排名。这是安全兼容边界；不能通过当场计算一个新哈希来证明历史结果未被改写。

## 5. 目标场景实测

使用临时 SQLite 中商品 `100013588053` 的合成评论：

| 步骤 | 实测结果 |
| --- | --- |
| “100013588053的差评主要集中在哪些方面？” | ok；业务验收 passed；完整六维排名；成功标注分母 3 |
| 保存首轮任务，再从任务库恢复 | ranking_basis 有 task_id/r1、完整 scope、保存时摘要、结果摘要、复核摘要和六行排名 |
| “具体体现在哪里？” | 保留单 SKU / negative；analysis_state 携带可信排名；仍提示选择维度，unsupported；query_executions=0、total_model_calls=0 |
| 保存该澄清轮，再次加载 | 恢复 awaiting_aspect 的冻结主题目标，以及首轮排名来源 |
| “物流方面呢？”的纯解析测试 | 原语义编译为单维度 get_aspect_drilldown；测试只检查计划，不执行分类；旧澄清文字的语义解析入口被设置为抛错，仍正常完成目标承接 |

原始场景快照在 `.cache/p1a/scenario.json`。这里证明的是可信输入就绪与省略目标承接，不是多维度下钻已实现。

## 6. 文件与函数

| 文件 | 本阶段职责 |
| --- | --- |
| jobs/store.py | analysis_result_integrity；finish_task 以原有终态权限保存摘要，保留非 analysis 任务行为 |
| jobs/analysis_context.py | 有界同会话恢复；完整性检查；恢复可信投影；变化范围失效；旧任务条件兼容 |
| 新增 jobs/analysis_state.py | restore_ranking、restore_pending_goal、analysis_state_for_scope、pending_context；集中完成可信投影，不另建状态库 |
| agents/reviews.py | 在最终有效范围上附加历史 analysis_state，包括澄清出口；不增加工具调用 |
| agents/review_intent.py | _user_conditions 优先消费已核验的结构化待完成目标 |
| agents/review_acceptance.py | build_request_goal 使用同一受控历史目标来源，保持当前请求目标语义与版本 |
| 新增 tests/test_trusted_analysis_state.py | 18 项状态/隔离/篡改/兼容/权限测试 |
| 本报告 | 独立阶段记录 |

源码路径以 `src/data_analysis_agent/` 为根。`review_plan.py`、`review_semantics.py`、主题分类器、冻结 RAG 运行契约与评审资产本阶段未修改。没有修改任何现有测试断言。

## 7. 测试与证据

| 新增测试覆盖 | 证明内容 |
| --- | --- |
| trusted_ranking_restores_all_six_counts_denominator_and_source | 六行排名、顺序、计数、分母、scope 指纹及 task/result 来源对应 |
| target_followup_exposes_verified_basis_without_running_p6 | 目标追问携带可信输入，0 次查询/模型调用 |
| selection_changes_invalidate_incompatible_rankings | 商品、日期、基线、截止时间、来源、数据集、模型、端点、提示词版本改变使排名失效 |
| explicit_question_scope_change_does_not_publish_previous_basis | 原文显式改商品并成功绑定后，不附带旧商品排名 |
| cross_conversation_missing_and_incomplete_tasks_do_not_supply_rankings | 跨会话、任务不存在、结果不完整均不提供排名 |
| arithmetic_consistent_parent_tamper_is_detected_by_saved_digest | 修改数值校验不涉及的答案正文仍发现原结果变动 |
| actual_count_tamper_is_rejected_even_when_numeric_fields_are_consistent | 同步改计数/比例/排序后原数值校验仍通过，但保存时摘要拒绝该父任务 |
| business_failed_insufficient_and_partial_tasks_never_supply_rankings | 失败、证据不足、部分任务不成为可信来源 |
| fresh_business_validation_rejects_saved_pass_flags_with_wrong_goal | 即使保存了 passed 标志与有效摘要，重新业务验收不通过仍不能提供排名 |
| incomplete_reordered_and_filtered_rankings_are_not_full_rankings | 缺维度、错排序、单维度与正向汇总均不冒充全维负面排名 |
| legacy_task_preserves_conditions_without_upgrading_unsealed_numbers | 旧任务恢复条件，不升级未封存数字 |
| result_reference_is_stable_across_fresh_reads_without_writes | 多次重新加载引用一致，临时任务库文件哈希不变 |
| seal_does_not_weaken_task_execution_ownership | 非拥有执行权的 Worker 不能覆盖结果或摘要 |
| pending_theme_goal_is_recovered_without_reparsing_its_old_text | 冻结目标恢复、原始排名承接、拒绝旧文本再次意图解析 |
| pending_goal_is_not_reused_after_scope_change | 待完成目标与排名随不兼容范围同时失效 |
| frontend_cannot_submit_ranking_numbers_or_trusted_state | 参数模型拒绝可信状态/排名参数 |
| client_supplied_counts_are_ignored_even_before_parameter_validation | 加载器不读取附带的客户端排名数字 |
| malformed_result_json_is_safe_to_ignore | 损坏持久化 JSON 不导致错误恢复或异常放行 |

上述方法在测试文件中均带 `test_` 前缀。

执行记录：

| 检查 | 结果 |
| --- | --- |
| 修改前新增基线 | 9 项，1 failure / 2 errors，证明缺少可信状态 |
| 核心意图、业务验收与状态回归 | 64/64 通过 |
| P1-A 最终专项 | 18/18 通过 |
| P0-A / P0-B 全部适用回归 + P1-A | **450/450 通过**，121.840 秒，0 error / skip |
| 扩展任务/依赖/包结构检查 | 468 项，1 个测试方法内 13 个静态失败子项；其余方法通过，0 error / skip |
| Python 语法编译、git diff --check | 通过 |

扩展静态失败均来自既有私有接口导入：agents 下 reviews、review_retrieval、review_shadow_siliconflow、review_structured_evidence，以及 reviews 下 e1a_b3_runtime、e1a_b3_worker、e1a_prediction、e1_preparation、embedding_store、exploratory_e1、historical_snapshot、rag_corpus、semantic_retrieval。失败规则为 `test_dependency_boundaries.DependencyBoundaryTests.test_implementation_uses_public_interfaces_and_one_connection_factory`。

通过任务开始时的 SHA-256 对照，12 个文件原样未变；reviews.py 去除本轮新增的两行公共状态投影代码后，源码摘要也与基线完全一致。13 个模块的私有导入都已在本轮之前存在，记录见 `.cache/p1a/preexisting-private-imports.json`。本阶段没有为清理这些历史告警而越界修改冻结 RAG 或人工评审代码，也没有放宽静态测试。

最终功能集包含完整 P0-B 432 项及 P1-A 18 项；扩展运行中的 P1-A 还是当时的 15 项，因此两个总数不直接相减作为通过率。完整目标清单在 `.cache/p1a/final-summary.json`，日志为 `final-regression.txt`；专项日志 `final-state-tests.txt`，扩展审计日志 `regression.txt`。

最终功能运行禁止 socket 和 psycopg 连接；数据均为临时 SQLite/固定夹具。保存、篡改和权限测试只操作隔离测试任务库。旧 Windows 异步/真实 PostgreSQL 环境不在本轮离线通过声明内。

复跑：

```powershell
$env:PYTHONPATH='src;tests'
.venv/Scripts/python.exe -m unittest test_trusted_analysis_state -v
.venv/Scripts/python.exe -m unittest test_review_router_p0a test_review_acceptance_p0b test_review_intent test_review_task_completion -v
.venv/Scripts/python.exe -m unittest test_task_submission test_task_logging test_package_layout -v
```

## 8. 资产与剩余边界

任务开始对 1616 个既有文件做摘要快照，未读取生产数据库文件。完成后只有本报告列出的五个既有源码发生变化，其他已受检文件全部保持原样；另新增状态辅助模块、18 项测试文件和本报告。P0-A/P0-B 的既有测试与验收报告、用户原有前端修改、P6 方案及冻结资产未被改写。

P6 下一阶段可以使用：原始 task/result 引用、完整范围指纹、negative 情感、成功标注分母、六维排序与计数，以及可证明来源的待完成目标。**这满足可信历史输入条件，不等于 P6 Planner 已实施。** 后续必须在最终范围上调用状态投影，并在分类前核对当前业务数据的样本计数是否仍与历史一致，再按 P6 方案选择最多三个非零维度。

尚未覆盖：真实 PostgreSQL 持久化/并发、旧 Windows 异步链路、真实主题质量、生产负载，以及同时改写结果与摘要的数据库特权攻击。SHA-256 证明的是已保存任务快照的完整性，不证明当前业务库没有变化，也不等同人工确认。P1-A 不刷新生产统计、不自动重跑历史排名、不调用主题分类或部署。

本阶段停止，等待独立验收。
