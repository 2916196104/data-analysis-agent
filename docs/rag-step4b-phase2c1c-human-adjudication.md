# Step 4B-2c-1c：人工裁决与契约确认工作台

本轮已建立本地人工裁决界面及独立最终校验工具，装入24份共享契约、48个CASE、222条原候选和22个P1案例，并实现契约变更后两模式空白重审、逐项确认、配对裁决、导出恢复及条件式参考准备。

**实际人工确认尚未发生：当前契约决定0/24、CASE复核决定0/48、必须人工裁决的配对0/2，尚不具备进入正式参考冻结阶段的资格。** 原18项sufficient/30项insufficient只是保留的初评值，不能当作本阶段已复核结果。任务停在等待本人使用界面作决定；没有替人填答案或伪造审批。

## 1. 输入与隔离

已阅读最新人工评审审计报告及完整48项任务清单，逐条复用对应私有case-findings、case-field-proposals、contracts.proposed、paired-audit、references.pending、来源及哈希材料。入口仍绑定原 evidence-review-draft (3).json：

- 原导出SHA-256：`d5e97d4c4726f689c4a474c1853b6fabd8f6864d6eff03b763a990ce819dc009`。
- 原输入摘要：`f14ec3941b20a08e4859b80b9f041d944f66733d397ebef5017c3a28f1e2f4a1`。
- 本轮输入包规范JSON摘要：`f0ee9e6d2a0bcaa16baf17118d97fe7da9843098b919a6335160dd2729ef7b5a`。
- 实验性质：`exploratory_current_observation_history_unproven`。
- 上下文：`quote_full_review_veto` / version 1。

私有工作区：`E:/AllProject/data-analysis-agent/outputs/rag-retrieval-quality/step4b-phase2c1c-adjudication-20261104/`，实际入口为其中 review.html。完整原文、候选、逐条标签、决定和日志只保存在Git忽略目录；公开模板及合成测试不含真实评审数据。

保留 reviews_csv / reviews-csv、商品100013588055、UTC 2021-09-11至2022-08-26、原run/manifest/scope、BGE-M3/1024维/quote-utf8-v1和原200条索引限制。没有新检索、数据库补读、模型调用、向量同步或Agent集成；没有修改原严格trusted_batch接口。57条来源仍历史未证明，不将本地输入摘要当作独立历史证明。

## 2. 实际新增文件

| 新文件 | 用途 |
|---|---|
| src/data_analysis_agent/reviews/human_adjudication.py | 独立会话、事件/引用/条件校验、CLI和条件式待确认参考输出 |
| scripts/templates/e1_adjudication.html | 自包含本地页面模板和绿色布局 |
| scripts/templates/e1_adjudication.js | 人工操作、双模式重置、编辑失效、逐项复核与事件留痕 |
| tests/test_human_adjudication.py | 完全合成的状态、来源、版本、记录及CLI测试 |
| tests/test_adjudication_page.py | 完全合成的DOM按钮流程、导出恢复和引用目标保持测试 |
| 本报告 | 实现、验证、实际人工状态和下一步边界 |

既有QueryContract、证据判断器、人工参考协议、检索排序/过滤、API、前端应用、Agent及Supervisor/LangGraph未修改。新模块仅在显式离线命令中运行，不被业务路径接入。

## 3. 查询契约确认流程

页面默认Q20，其后优先原needs_revision查询，再显示其他变更提案和剩余查询。每题展示原问题、原共享契约、建议契约、差异、对象与细分主题、task_type/sentiment、事实/运算符/必要关系、独立许可和不确定项。

本人须选择原契约、建议契约或自定义版本并填写理由，再点击确认。没有批量自动接受；scope_ref和本轮建议过滤边界不可改。同一query的U/F只引用一份活动契约和同一修订号。

契约内容或独立目标ID变化时，立即将两份CASE的工作状态设为未选择，事实/关系unknown，引用、许可、缺失集合和旧确认清空。原判断在对照面板及输入包中永久保留。改变修订号、原字段ID相同或旧结论曾是sufficient，都不能绕过新版本重审。

只修改确认理由或重新确认相同载荷，也会使相关CASE确认失效，但不自动改变事实判断。身份或边界声明变化会使已有确认失效，历史事件保留，不能用旧确认证明新内容已审。

Q03的独立许可与必要关系边界仍按原协议保守校验；工作台不会为迁就现有判断规则删掉同评论约束。Q13的正反替代目标与mixed同评论共现有明确区别；Q23仍可保留clarify，不替人工猜测电池或使用条件。

## 4. CASE逐项复核

48个CASE全部可进入，Q20相关项首先展示，其余P1优先；22个P1、2个P2及24个原无CASE级发现项都需本人单独确认。每项保留：

- 原判断和原理由，只读可展开。
- 原审计严重度、原字段依据及修改建议；重点条件式建议与实际工作判断并排可查。
- 原候选顺序、quote、完整评论、来源引用、review/annotation身份和字符区间。
- 当前问题类型、对象、细分主题及活动契约。
- 逐事实支持、事实来源、关系及其来源、状态、许可与缺失集合、有效备注和本次决定理由。
- 本人逐项处理审计问题的记录及最终本项确认事件。

支持程度和参考状态由本人选，不由程序推断；添加引用不会自动设direct。引用目标会跨重绘保持，避免连续添加证据时误切换到另一事实。字符区间按Unicode码点处理，不能超出当前候选quote。完整评论只能解释、限定或否决，不能从quote外借用新支持事实；上下文否决区间另存。

CASE-004/009要求先确认Q20并按活动版本复核；CASE-022/031/035/036/046提供原字段与条件式修订入口。工作台不自动把CASE-046改为sufficient，也不替人工把unknown改成not_supported；但insufficient继续许可事实、direct缺引用、关系缺直接事实来源等均会阻断确认。

## 5. 配对冲突裁决

Q05、Q07必须在两份CASE复核后填写统一裁决理由并显式确认。界面展示两侧模式、原判断和当前判断，固定相同候选/顺序/上下文的摘要依据，并可直接跳转到两份CASE修改。不会把一侧答案自动复制到另一侧。

Q02、Q18也是相同输入对照，最终要通过一致性检查。总计4组机器一致性检查，其中2组要求实际人工裁决事件。判断一致比较状态、逐事实支持、必要关系、许可和缺失集合；不强制两边选用完全相同的合法证据片段。修改一侧后，既有配对及完成记录失效。

Q07的before(f0,f1)与全部三事实联合要求分开显示；不靠字符先后自动判断事件先后。CASE-012不同支持判断的重复理由会被提示复核，不因any_of中另一个事实未支持而自动降低最终状态。

## 6. 实际决定与确认记录

只有本人点击对应操作才新增事件。事件包含项目ID、操作种类、本人代号、当前声明、浏览器生成的UTC时点、所确认的完整载荷及摘要、输入包摘要和前一个事件摘要。当前实际事件数为0，没有伪造第二评审人、裁决人或批准时间。

这些是本地操作声明及可追溯性记录，不是数字签名、独立身份认证或外部可信时间。最终校验不只看一个true标记，还要求内容、版本、事件种类/对象、摘要链及当前身份声明一致；编辑后的旧事件不能继续生效。

本阶段的复核完成记录与正式参考批准分开。生成的待确认参考始终保留human_confirmed=false、formal_confirmation_record=null、usable_for_evaluation=false；不在本阶段执行最终冻结。原有human-reference协议及安全约束保持不变。

## 7. 最终一致性校验与条件式输出

独立入口为 `python -m data_analysis_agent.reviews.human_adjudication`，接收固定bundle、本人导出的session、预期bundle摘要和新输出目录。随工作区提供validate-session.ps1和完整命令说明，也可将导出交回助手执行该离线校验。

校验内容包括合法QueryContract、不可变范围/过滤、共享修订、全部事实与关系、direct来源、all_of/any_of、limited独立许可及关系阻断、insufficient许可冲突、引用身份/区间、上下文、逐项问题处理、相同输入配对及全部实际确认记录。

每次输出consistency-audit.json和report.md，分别给出每题/每CASE/每配对的结果和剩余问题。**仅当24份契约、48份CASE、所有配对和本轮完成事件均有效，才输出references.pending.json，并报告具备进入正式参考冻结准备的资格。** 页面端只是即时预检，离线Python校验为最终依据；不从浏览器导出内容直接相信“已完成”标记。

未完成返回专门的未就绪结果，不生成待冻结参考；身份、事件链、来源或格式失败会明确报错，不伪装成正常insufficient。输出目录必须新建，不覆盖原导出、历史审计或之前的校验结果。

## 8. 当前真实执行状态

| 项目 | 本轮实际状态 |
|---|---:|
| 已装入共享查询契约 | 24/24 |
| 已装入CASE及候选 | 48/48、222/222 |
| P1优先项 | 22/22 |
| 实际本人契约决定 | 0/24 |
| 实际本人CASE复核决定 | 0/48 |
| 必须实际裁决的配对 | 0/2 |
| 当前同输入配对已满足全部复核条件 | 0/4；因CASE尚未确认，不等于发现4组语义冲突 |
| 当前事件与正式批准记录 | 0 |
| 本轮真实待冻结参考生成 | 0；初始校验未就绪 |
| 正式冻结 / 真实E1 / 模型 / 数据库补读 | 均为0 |

原48份判断、24份提案和来源材料未变。当前JavaScript和Python对同一真实输入包的摘要一致，初始化均显示0份新确认；没有把旧accept_draft或初评状态自动升级为本轮决定。

## 9. 实际验证

| 检查 | 结果 |
|---|---|
| 新专项 | 26/26通过，无失败、错误或跳过 |
| 专项加适用既有协议回归 | 107/107通过；26项包含于107项，不重复相加 |
| 既有队列错误单独重现 | 0通过、2错误，保留SQLite/PostgreSQL限制，未计为通过 |
| 合成完整UI操作链 | 选择契约→分别确认两CASE→统一配对→本轮提交→导出，共5个独立事件；通过 |
| 契约变更与恢复测试 | 两模式重置、内容编辑失效、伪造标记/事件ID拒绝、导入身份及摘要校验均通过 |
| CLI测试 | 合成完整输入才生成待确认参考；未完成输入只生成审计，正式标记均不批准 |
| 真实包初始化检查 | 24/48/4项覆盖、JS/Python摘要一致、0事件、资格false |
| 原文件保护 | 297个既有文件摘要保持不变，包括原评审、上阶段私有审计和冻结归档 |

测试全部使用合成决策，不代人工决定真实语义；真实输入包仅作读取、绑定和初始化核对，没有自动点击真实页面确认。旧错误仍为以下两个临时SQLite测试进入PostgreSQL队列，未改业务逻辑：

- test_review_scope.ReviewScopeTests.test_run_date_filters_incremental_skip_and_scoped_progress
- test_review_scope.ReviewScopeTests.test_run_retry_and_cutoff_forwarding_with_real_mocked_sdk

实际浏览器验收受限：Edge入口不可用，Chrome工具随后明确拒绝file:协议，只允许HTTP/HTTPS。没有改用其他浏览器或HTTP服务绕过该策略。因此本轮没有实际浏览器布局截图或1440/1024/768宽度验收。已完成的是Node中的合成DOM事件测试和脚本验证，不宣称浏览器视觉验收通过。

开发期间出现字典/括号语法和测试夹具返回值解包错误，已修正并实际重跑；最终采用final4测试日志。没有以初次失败或合成通过替代真实人工复核。

主要执行命令：

```powershell
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/step4b-phase2c1c-adjudication-20261104/build_workspace.py
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/step4b-phase2c1c-adjudication-20261104/run_checks.py final4
.venv/Scripts/python.exe -B -m data_analysis_agent.reviews.human_adjudication --bundle outputs/rag-retrieval-quality/step4b-phase2c1c-adjudication-20261104/bundle.json --session outputs/rag-retrieval-quality/step4b-phase2c1c-adjudication-20261104/session.initial.json --expected-bundle-sha256 f0ee9e6d2a0bcaa16baf17118d97fe7da9843098b919a6335160dd2729ef7b5a --output outputs/rag-retrieval-quality/step4b-phase2c1c-adjudication-20261104/initial-validation
git diff --check
git status --short
```

初始真实会话的校验未就绪符合预期；没有输出references.pending.json。各测试、初始化、浏览器限制、文件摘要与隔离检查留存在本轮私有目录。

## 10. 本人接续操作与停止条件

请手动打开私有review.html，从Q20开始选择并确认契约，再按P1优先处理全部CASE、配对和完成记录。定期导出进度，可恢复继续。导出含私有信息，应保存至本地忽略目录。

全部复核后，将导出的e1-human-adjudication-session.json交回或运行随附校验命令。只有最终校验通过，才会生成真实的待正式确认参考及对应审计报告；随后停在正式参考冻结资格审查，不自动运行E1。

当前工作台和校验能力已准备好，人工决定仍待填写，不能宣称本阶段裁决全部完成。历史来源限制、单人限制、质量门槛待人工确认及原24题非独立泛化集的事实继续保留。
