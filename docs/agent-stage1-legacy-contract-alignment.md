# Agent 第一阶段：F01/P6-B 旧测试契约对齐

日期：2026-11-11，Asia/Taipei（UTC+08:00）。**本轮测试契约收口 PASS：四项原冲突测试 4/4、合并专项 54/54、适用后端兼容回归 743/743 通过，0 skip。** 正式 Oracle v2 仍为 **61 PASS / 1 FAIL**，G25-T1 未修复、未改判；第一阶段整体业务验收尚未全部通过。

本轮仅修改两个测试文件，未修改生产 Agent、Oracle、人工裁决、评分器、P6-E1B 安全门或授权。可以单独进入 G25-T1 最小修复任务的审查与授权，本轮不自动开始。

## 1. 先复现，再确定冲突性质

使用当前 v5 源码与原隔离测试入口，修订前四项测试重新得到 **1 failure、3 errors**，与上轮结果一致。原测试源码、原始断言和本轮修订前日志独立保存，没有覆盖此前失败材料。

| 原测试 ID（类名前缀见下） | 原目的和旧预期 | 实际失败与性质 | Oracle v2 之外的边界 |
|---|---|---|---|
| F01：test_a_short_sentiment_keeps_themes_with_correct_empty_positive_result | 短情感追问不降级为统计，直接承接 positive/themes；无好评证据时 insufficient_evidence | Goal 实际为 null，旧测试读取 levels 报 TypeError；**旧业务预期冲突** | 空正向证据下的真实 Worker、工具授权检查 |
| F01：test_legacy_user_conditions_without_structured_state_keep_theme_goal | 移除 analysis_state，仍从合法用户历史重构主题并执行 | Goal=null，旧测试读取 levels 报 TypeError；**旧业务预期冲突** | 无结构化状态的直接 Agent 调用，不能捏造可信排名/待办 |
| F01：test_positive_theme_with_matching_evidence_completes_without_paid_model | 有正向合成证据后，“那好评呢？”应 ok/complete | 实际 unsupported 澄清，断言 ok 失败；**旧业务预期冲突** | 数据存在是否会绕过目标消歧；正向主题真实分类与证据验收 |
| P6-B：test_explicit_other_requests_and_f01_short_sentiment_do_not_trigger_p6 | 普通统计/原文/趋势不误触发 P6；准备轮后的短好评句直接得到主题计划 | Router 正常抛 QuestionScopeError，旧测试未捕获；**旧业务预期冲突** | P6 已准备、尚未分类的任务上下文与普通分析路径共存 |

F01 类为 `test_p1a_f01.F01WorkerTests`；P6-B 类为 `test_p6b_planner_snapshot.P6BPlannerSnapshotTests`。四项原冲突均未发现需要修改生产代码的实现缺陷；其错误来自不再成立的旧预期，不是 Worker 未处理异常。

依据为未修改的 [正式人工 A 记录](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-human-adjudication.v1.json)、[Oracle v2](E:/AllProject/data-analysis-agent/tests/fixtures/agent_stage1/oracle.v2.json)及 [v5 修复验收](E:/AllProject/data-analysis-agent/docs/agent-stage1-oracle-v2-p0-p1-fix-acceptance.md)。仅在总体统计与未完成主题竞争时，短句“那好评呢？”必须先澄清，证据存在与否不能替代用户的层次选择。

证据：[修订前四项原始日志](E:/AllProject/data-analysis-agent/.cache/agent-stage1-legacy-alignment/before-four/tests.txt)、[修订前结果](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-legacy-alignment/before-four-results.json)。旧源码保存为同一私有目录的 `before-test_p1a_f01.py`、`before-test_p6b_planner_snapshot.py`。重现旧断言须在独立副本使用这些快照，当前测试文件不回写旧版。

## 2. 保留 ID，准确替换已被裁决取代的断言

| 原 ID | 旧输入 → 当前输入 | 旧断言 → 对齐后的断言 |
|---|---|---|
| F01 test_a… | “那好评呢？”不变 | positive/themes、可执行主题计划、insufficient_evidence → Goal=null、positive 已知、needs_clarification=true、无主计划、工具/分类=0、禁止 complete=true |
| F01 test_legacy… | “那好评呢？”及移除 analysis_state 的调用方式均不变 | 从文字恢复可执行主题 → 澄清且不凭文字生成可信状态；原任务库中的合法 pending 及来源仍完整保留 |
| F01 test_positive_theme_with_matching_evidence… | 有正向证据时“那好评呢？”不变 | 必须分析成功 → 仍须澄清且模型调用=0；原“充分证据可完成主题”的合法目的拆到新增明确主题测试 |
| P6-B test_explicit_other_requests… | 原四个统计/原文输入、趋势输入及最后“那好评呢？”均不变 | 最后一轮直接取得主题计划 → Goal=null、Router 澄清、真实 Worker 零工具；准备任务的冻结目标与完整性保持原样 |

修改文件：

- [tests/test_p1a_f01.py](E:/AllProject/data-analysis-agent/tests/test_p1a_f01.py:80)：新增严格澄清断言、正向合成证据/固定响应及真实主题验收辅助函数；仅更新三个原测试，并增加四个合法能力用例。
- [tests/test_p6b_planner_snapshot.py](E:/AllProject/data-analysis-agent/tests/test_p6b_planner_snapshot.py:255)：保留原统计、原文及趋势断言；将冲突的短句分支改为澄清；增加明确正向主题走普通计划的用例。

四项历史 ID 全部保留，代码注释明确标注正式人工 A / Oracle v2 的契约变更依据。其他原测试方法 AST 不变；F01 原 `setUp` 与 `authorized` 的工具授权检查也未改变。没有 skip、expectedFailure、按 ID 强制 PASS、生产分支特殊处理或删除测试。

逐方法旧/新源码及断言：[old-and-aligned-assertions.json](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-legacy-alignment/old-and-aligned-assertions.json)；完整 [测试差异](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-legacy-alignment/test-contract-changes.diff)。独立 [测试对齐绑定](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-legacy-alignment/test-alignment.v1.json)记录原/新 SHA-256、保留与新增 ID、正式人工依据及 v5 摘要，不改写原冻结清单。

## 3. 澄清不是证据不足，也不是主题能力被禁用

更新后的澄清断言同时检查：Goal=null、Router positive/needs_clarification=true、query_type=null、无主计划/required_tools、业务查询/工具/主题模型/总模型调用全部为 0、无结果引用、未 complete，以及同时询问好评统计和主题具体表现。固定模型入口显式 assert_not_called。

原 negative/themes 待办不能只剩 awaiting_aspect 标签：测试比较完整目标、scope、scope_sha256、source.task_sha256 与保存的原任务，要求 has_saved_integrity 和 restore_pending_goal 校验通过；Worker 澄清后重新加载任务链，得到与原待办逐对象相同的结果。

两个特殊边界单独处理：

- **无结构化状态的旧调用**：当前结果不能从文字自造可信 ranking/pending；原任务库中的待办仍能经完整性校验恢复。没有把缺失状态解释成分析已完成。
- **P6 已准备未分类的调用**：保持原准备任务及冻结三维目标、完整性摘要不变，不把它伪造成 awaiting_aspect 或 complete。后续短句仍零执行，准备及分类均不重新启动。

为保留旧测试中合法的正向能力，新增以下五个测试：

| 新测试 | 实际覆盖 |
|---|---|
| F01 test_explicit_positive_theme_with_matching_evidence_completes_without_paid_model | 明确“那好评具体体现在哪里？”；真实主题程序执行固定的发现及归属复核，Validator 验收完成 |
| F01 test_explicit_positive_theme_without_evidence_is_insufficient_not_clarification | 同一明确主题请求无正向证据时，实际查询后 insufficient_evidence/complete=false，不冒充澄清或成功 |
| F01 test_unique_single_theme_context_allows_short_positive_switch | 完成客服负面主题后，“那好评呢？”唯一承接 service/positive/themes，允许实际分类并完成 |
| F01 test_short_positive_after_only_statistics_uses_positive_counts | 只有已完成负面统计，没有竞争待办链时，短好评句仍执行 get_positive_aspects，实际正向评论数为 1，不调用主题模型 |
| P6-B test_explicit_positive_theme_keeps_ordinary_plan_after_p6_preparation | 明确正向主题可生成普通 get_aspect_drilldown 计划并通过 Goal/Plan 检查；freeze_p6_goal 返回 existing，不进入多维负面 P6 |

正向主题成功测试不只检查标签：固定模型响应实际调用 **2 次**，顺序为 TopicCatalog、TopicBatch；要求 get_aspect_drilldown、task_plan_check.passed、validation.passed、task_acceptance.complete=true，并重新执行 validate_topic_result。

合成来源为隔离测试库中的“客服回复及时”：支持评论去重数 **1**、主题分母 **1**，review_ids 与实际观点来源一致；逐条核对商品 A、service/positive 及原文位置切片。主题保持 `model_reviewed_not_human_confirmed`，不冒充人工确认。这证明固定响应下工程链路可完成正向主题，不能证明真实模型的语义质量。

## 4. 最终测试结果及执行中的问题保留

| 验证层次 | 实际结果 |
|---|---|
| 四项原冲突，修订前 | 4项：1 failure、3 errors，原始日志保留 |
| 四项原冲突，最终修订后 | **4/4 PASS** |
| F01 / P6-B / G24-G30 合并专项 | **54/54 PASS**：17 + 20 + 17，无跳过 |
| 原 P0/P1 及适用后端完整兼容回归 | **743/743 PASS，0 failure、0 error、0 skip** |
| 原兼容测试 ID | **738/738 保留**，包括本轮授权修订的四项；新增5项，没有减少覆盖 |
| 正式 Oracle v2 | **30组、62轮：61 PASS / 1 FAIL / 0 PENDING** |

证据：[最终四项](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-legacy-alignment/aligned-four-results.json)、[54项专项](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-legacy-alignment/focused-results.json)、[743项兼容结果及完整ID](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-legacy-alignment/compatibility-results.json)、[完整回归日志](E:/AllProject/data-analysis-agent/.cache/agent-stage1-legacy-alignment/final-full/tests.txt)。

执行中有两项测试/调用问题，没有隐藏或归为 Agent 业务通过：

1. 新增正向统计断言误用了负面统计的 `current` 字段；实际 get_positive_aspects 契约为 `summary.positive_review_count`。首次合并专项及草稿完整回归因此报 KeyError。核验真实 analytics.py 后修正测试字段，期望数量仍为 1，未改业务实现。草稿 focused/full 日志保留；最终完整回归对最终测试版本重新运行。
2. 第一次调用已有评测入口传入相对输出目录，入口在写评分文件时触发 relative_to 错误。保留原始 run-01.json 及日志，改用绝对输出路径重跑原入口，正式结果来自 run-02。未修改入口或评分器，也未将未完成的评分写入当成业务验收结果。

## 5. Oracle v2 独立评分与剩余 FAIL

使用原 v5 源码、正式 Oracle v2 与未修改的 v2.1 评分合同；原有数据模式、故障注入、合成模型响应和评分分母不变。

| 业务验收项 | 结果 |
|---|---|
| G24-T2、G30-T3 | 继续 PASS，Goal未冻结、工具前澄清 |
| 最初59轮PASS | **59/59 保持** |
| 上轮v5的61轮PASS | **61/61 保持** |
| G01～G04、G13～G15、G22/G23、G28/G29 | 保持通过，含目标纠正、失败后范围继承、规划/结果故障拦截 |
| G10～G12、G17～G20 | 澄清、partial 与 stale 边界保持通过 |
| State / Router / Goal / Planner | 各 **60/60** |
| 工具阶段及目标匹配 | **59/60** |
| 工具前阻断 / 注入错误拦截 | **10/10、2/2** |
| 严格任务完整完成 | **42/43** |
| 逐轮 / 对话整体契约 | **61/62、29/30** |

完整 [62轮原始链路及独立对照](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-legacy-alignment/oracle-v2-turns.jsonl)、[正式评分](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-legacy-alignment/oracle-v2-assessment.json)。两个主动注入故障单独统计，未从62轮总验收排除。

**唯一剩余业务 FAIL：G25-T1。** 带 SKU 的物流慢问题能检索到合成候选，但当前冻结命题规则不能许可该问句，assessment=query_semantics_uncovered、permitted_keys=[]。回答仍不作确定结论，未产生 complete=true；不能把检索流程结束等同于原事实问题完成。本轮不修改该预期、RAG 许可或证据，不伪造引用，也不将它归因于053的真实向量覆盖缺口。

743项兼容通过与61/62业务通过是不同指标，不能用前者将 G25-T1 改判为 PASS。固定语料与响应也不等于真实数据库或真实模型验收。

## 6. 保全、安全与可复验入口

本轮预先保存的 **891个既有文件**中，仅两个获准测试文件发生变化；**88个v5生产源码摘要全部相同**。原测试ID未删除，除四项外原测试方法AST不变；Oracle v1/v2/pending、人工裁决、评分器、原冻结清单、旧报告/评分/失败记录、P6-E1B授权与恢复材料保留。

生产源码继续绑定 [原v5清单](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-v2-fix/final/source-revision.v5.json)，SHA-256 为 `1a257a154f8db17e66b7d207ccd02ead75de68419064f176e7dd13c735c746d5`。本轮没有创建新的业务源码修订版本。

完整兼容回归的 **13,960次 SQLite 连接**仅面向本轮合成临时目录；PostgreSQL连接、外部网络尝试、收费模型和真实Embedding调用均 **0**。Oracle评测仍使用原内存/协议/analytics/检索替身，连接替身 **64/64关闭**，外部禁止调用尝试为0；没有连接业务库或操作生产数据。

复验须使用新输出目录，避免覆盖本次日志：

```powershell
.\.venv\Scripts\python.exe scripts/check_agent_stage1_p0p1_regression.py --source-revision docs/evaluation/agent-stage1-v2-fix/final/source-revision.v5.json --targets .cache/agent-stage1-legacy-alignment/four-targets.json --output-dir .cache/agent-stage1-legacy-alignment/recheck-four
.\.venv\Scripts\python.exe scripts/check_agent_stage1_p0p1_regression.py --source-revision docs/evaluation/agent-stage1-v2-fix/final/source-revision.v5.json --targets docs/evaluation/agent-stage1-v2-fix/regression-targets.json --output-dir .cache/agent-stage1-legacy-alignment/recheck-full
.\.venv\Scripts\python.exe scripts/run_agent_stage1_v2_fixes.py --output-dir E:/AllProject/data-analysis-agent/.cache/agent-stage1-legacy-alignment/oracle-recheck
```

[汇总与保全核验](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-legacy-alignment/summary.json)、[执行产物摘要](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-legacy-alignment/artifact-sha256.json)、[含本报告的最终摘要](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-legacy-alignment/final-artifact-sha256.json)。

## 7. 是否可单独进入 G25-T1

**可以作为下一项独立业务修复任务，但本轮不启动。** 已无本轮四项测试契约冲突，既有合法正向能力与G24/G30安全边界保持，独立Oracle明确只剩G25-T1。后续应另行限定范围/命题分离的最小修复、引用许可及原预期保全，不借此放宽事实证据规则。

本轮契约对齐 PASS 不代表第一阶段整体业务 PASS，更不授予数据库、模型费用或部署权限。P6-E1B 继续 **BLOCKED**，PG-01～08 **NOT EXECUTED**；真实供应商、数据库一致性和模型语义质量仍未验证。完成本轮后停止，不自动开启 RAG 修复或真实环境任务。
