# Q15 / CASE-012实时一致性反馈修复

本轮修复了工作台编辑字段后错误列表滞留的问题，并说明any_of缺失集合的既有含义。没有更改人工评分、契约、引用或确认记录，没有改变人工参考协议或判断器业务逻辑。

## 真实文件核验

读取的最新导出为 `outputs/rag-retrieval-quality/baseline-v1/e1-human-adjudication-session (2).json`，SHA-256为 `166265b082f0039456647df30d8d1a50a95105f9ba5196d29cbbf80987097acc`。原样备份在新的Git忽略目录 `outputs/rag-retrieval-quality/q15-live-validation-fix-20261105/`。

E0057确实是有效的Q15 proposed契约确认：evidence、purchase_price、high_price_or_price_drop、any_of(f0,f1)、无必要关系、独立目标f0/f1。CASE-012仍为filtered，契约确认序号一致，没有共享版本失效。

| 字段 | 最新导出实际值 |
|---|---|
| f0.support / 引用 | not_supported / 空 |
| f1.support / 引用 | direct / 当前候选1的合法key及quote区间 |
| reference_state | sufficient |
| supported_target_fact_ids | [f1] |
| missing_required_fact_ids | [f0] |
| CASE确认事件 | 空，尚未由本人提交 |

真实导出的Python结构校验只报告“缺失事实集合与逐事实判断不一致”；页面引擎对同一保存值报告该冲突及“待本人确认CASE”。没有重现f0缺引用或sufficient条件错误。不能据此指责人工把f0填成了direct，也不能据保存状态断言截图/报错时的完整浏览器内存状态。

## 根因与代码证据

原页面的 `edited()` 只更新会话和顶部计数；底部错误列表只在 `caseView()` 整体重绘时生成。支持等级、理由和许可/缺失勾选均会调用edited，却没有刷新该列表。因此表单值和导出已经变化，旧错误仍可显示。

修复前用完全合成数据复现：先显示f0为direct且无引用的错误，随后操作选择not_supported，保存值已正确改变，底部仍保留旧错误。3项页面回归测试在修复前失败；2项协议测试当时已通过。证据在私有before-fix-tests.log。此次没有用真实CASE预置答案或调用判断器推理。

另一个问题是界面只写“缺失的必要事实”，没有解释它与“所有未获支持的备选事实”的差别，容易将any_of中的未选项f0填入缺失集合。

## any_of字段语义

既有 `e1_review_protocol.validate_human_reference()`、`human_adjudication.review_errors()`、页面校验和 `evidence_judge` 输出约定一致：

- all_of需要全部目标事实及必要关系；未直接支持的事实列入缺失集合。
- any_of至少一个目标事实直接支持且必要关系满足时，核心条件已经满足，missing_required_fact_ids为空。其余备选项未获支持，仍在fact_findings中保留，不升级为direct。
- any_of尚未满足时，仍按既有规则列出未直接支持的事实；关系缺失另由关系判断表示。

因此，在本人维持“f0未支持、f1直接支持、无必要关系、sufficient且仅许可f1”的前提下，missing_required_fact_ids应为 `[]`。这是现有结构规则，不是新的语义结论。真实导出中的[f0]没有被程序清空，需要本人核对并操作。

## 最小修改范围

| 本轮文件 | 改动 |
|---|---|
| scripts/templates/e1_adjudication.js | 编辑后局部刷新当前CASE的错误、失败提示及字段说明，不重绘输入框；提取现有核心条件计算供校验和只读提示共用；清楚显示direct、未标为direct、协议要求的缺失集合及当前填写值 |
| tests/test_adjudication_live_validation.py | 新增6项完全合成测试，含修复前失败复现、输入框保持、许可/缺失/理由实时反馈、any_of仅f1支持、all_of及必要关系边界、显式确认和导出恢复 |
| 本报告 | 保存真实核验、协议语义、验证结果和接续操作 |

Git中已有的Q20修复暂存改动全部保留，未改写暂存区。此次没有修改Python校验规则、QueryContract、来源验证、原始问题、候选顺序或filtered模式。私有新版HTML仅复用原24题、48组、222候选及相同bundle摘要，没有覆盖旧HTML。

## 实际验证

- 45/45专项通过，含本次新增6项。
- 189/189专项及相关回归通过，45项包含其中，不重复相加。
- 原两项队列测试仍为0通过、2错误：`test_review_scope.ReviewScopeTests.test_run_date_filters_incremental_skip_and_scoped_progress`、`test_run_retry_and_cutoff_forwarding_with_real_mocked_sdk`。错误仍为SQLite临时测试进入仅支持PostgreSQL的队列，未擅自修业务逻辑。
- 真实会话恢复前后内容完全一致，57条真实事件未变；页面与Python对48个CASE的就绪结论一致。当前契约9/24、CASE确认16/48、同输入配对就绪0/4。
- 新版提示计算出direct=[f1]、未标为direct=[f0]、协议缺失=[]；真实记录仍保留missing=[f0]及空CASE确认事件。
- 312个既有私有文件哈希全部不变，含原导出、旧页面、归档和历史评分。新版HTML载荷摘要与原bundle一致，未加入新来源。
- Node脚本语法与Git差异空白检查通过。未执行真实E1、模型推理、数据库读取或正式参考转换。

测试命令和原始日志在私有run_checks.py、final-test-results.json、before-fix-tests.log及相关final日志；verify_delivery.py、restoration-check.json及audit.before/after记录真实恢复和结构校验。

实际浏览器布局和截图没有重新验收：此前工具拒绝file协议，未换协议绕过。本轮完成的是Node合成DOM操作及真实数据只读恢复校验，不将其称为真实浏览器或语义质量验收。

## 本人接续操作

1. 如现有页面还有未导出的编辑，先另存新进度。再手动打开私有新目录review.html，恢复最新导出的JSON；当前备份session.original.json对应(2)，含57条真实事件。
2. 进入CASE-012，核对Q15/E0057与filtered，检查quote和已保存的完整评论上下文。支持集合仍只能引用quote内的事实，完整评论只作解释或否决。
3. 如果本人维持当前判断，在“缺失的必要事实”中取消f0，得到[]；保留f0未支持、f1直接支持及仅许可f1，并亲自检查其余字段。程序不会自动做这一步或补造确认。
4. 点击“校验并确认本CASE”，随后以新文件名导出，运行本目录validate-session.ps1。CASE确认仍须真实人工触发，其他未完成条目仍需继续复核。

当前没有把CASE-012标记为已通过，也未生成正式参考。全部人工确认及独立离线校验完成前不进入冻结或E1，history_unproven限制继续保留。
