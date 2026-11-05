# RAG Step 4B E1-A B3-A：运行入口、合成验证与权限阻塞

日期：2026-11-05，Asia/Taipei。

**离线运行入口、固定8项输入准备和合成验证已完成；B3-A尚未全部验收，当前不具备进入B3-B的条件。** Windows权限专项真实执行失败：进程令牌已被确认是独立AppContainer，但Python在执行权限探针前以`0xC0000142`退出。因此没有取得“worker无法读取reference”的成功实测证明，不能以目录分开、令牌存在或普通子进程成功代替权限隔离通过。

本轮真实8组/48组预测均为0，未执行B4。没有发放真实预测permit、修改E0145或已发布参考、连接数据库、调用外部模型、重检索或重建向量。`history_unproven`、`independent_seal=false`、历史可信批次0/48继续保留。

## 1. 核对依据与当前状态

已完整读取E1-A方案、B1实施报告、B2发布报告，以及现有预测/协议/发布/独立审计/指标模块和B2 publication manifest。旧报告中的未发布状态按其原日期保留，不覆盖历史材料；本轮按用户明确授权实施B3-A，不重新申请B1/B2审批。

实际只读复核B2 publication-manifest.json的固定字节摘要及其中登记的发布文件摘要，重新核对ReferenceRelease规范摘要、已保存inputs/observed-sources和原bundle摘要，再投影运行输入。没有重新运行参考发布器或进行人工裁决。

| 对象 | 结果 |
|---|---|
| B2发布状态 | published_and_independently_validated；48份发布参考；原48/48独立校验登记保持 |
| B2发布物、原会话、冻结输入及原评分 | 既有文件字节摘要未变化 |
| 本轮运行输入 | 固定8/8，身份和候选顺序通过离线检查，共35条排名条目 |
| 人工答案隔离 | runtime不包含人工事实/关系等级、许可/缺失答案、理由、评分或指标；逐层遍历8份实际输入检查通过 |
| R0 | explicit-clauses-zh-v1；原语义规则和strict接口不变 |
| 来源性质 | exploratory_current_observation_history_unproven；不是历史重现或独立可信来源 |
| Windows隔离 | 实现了失败关闭的启动路径；实际权限专项失败，未验收 |
| 真实预测、数据库补读、外部模型调用 | 0 / 0 / 0 |

沿用原reviews_csv / reviews-csv、商品100013588055、UTC 2021-09-11至2022-08-26、原run/manifest/scope、BGE-M3/1024维、quote-utf8-v1和200条索引。未重复M1–M3。57条历史状态仍未证明，不因运行输入准备改变等级。

## 2. 实际新增及修改文件

| 文件 | 改动和原因 |
|---|---|
| src/data_analysis_agent/reviews/e1a_b3_runtime.py（新增） | freeze/check/run/audit CLI；固定8项、配置和代码摘要、执行permit、单次占用记录、逐CASE台账、raw保存及独立后审计 |
| src/data_analysis_agent/reviews/e1a_b3_worker.py（新增） | 严格WorkerRequest、当前候选来源投影校验、R0调用及管道JSON输出；真实输入另需LaunchGrant |
| src/data_analysis_agent/reviews/e1a_windows_worker.py（新增） | 私有CPython运行副本、Windows AppContainer令牌核对、目录RX授权、显式管道句柄、单进程Job与超时终止；无普通进程降级 |
| src/data_analysis_agent/reviews/e1a_prediction.py（修改） | 仅将已校验后的原R0投影提取为_predict_r0_validated，供两个受来源校验保护的入口复用；run_r0原授权及完整来源检查保留 |
| tests/test_e1a_b3_runtime.py（新增） | 16项合成/CLI/运行副本测试及1项单独执行的Windows实际权限测试 |
| 本报告（新增） | 实际完成范围、运行方法、测试证据和阻塞结论 |

没有新增依赖。为使CPython无需获得已安装venv的访问权限，复制其解释器、标准库、Pydantic及现有传递依赖到本次新目录，使用._pth限定导入路径。不复制.env、人工参考或私有数据到代码目录。psycopg仅因现有来源模块的传递导入存在于运行副本，未调用连接；未修改数据库模块。

原evidence_judge.py、prepare_trusted_batch()、人工协议、参考发布器、B1输出审计及指标定义、检索与过滤业务逻辑均未改动。没有调整R0受控句式或根据人工参考优化规则。

## 3. 固定配置与实际8项身份

| 顺序 | CASE | query / mode | 原候选数 | 当前活动契约修订 |
|---:|---|---|---:|---:|
| 1 | CASE-004 | Q20 / unfiltered | 5 | 28 |
| 2 | CASE-009 | Q20 / filtered | 5 | 28 |
| 3 | CASE-012 | Q15 / filtered | 5 | 1 |
| 4 | CASE-023 | Q07 / unfiltered | 5 | 1 |
| 5 | CASE-034 | Q07 / filtered | 5 | 1 |
| 6 | CASE-040 | Q21 / filtered | 5 | 1 |
| 7 | CASE-005 | Q17 / filtered | 0 | 1 |
| 8 | CASE-028 | Q23 / unfiltered | 5 | 1 |

修订号来自实际B2发布物；Q20当前持久化修订号为28，不能将之前语义方案名称“Revision 2”误作本轮活动版本号。这里只核对身份，不评价真实预测表现。8个列表涉及6个query，只有Q20/Q07是完整U/F配对。

RunConfig固定engine=R0、experiment_id=e1a-b3-r0-debug-v1、engine_version=explicit-clauses-zh-v1、上述实验/来源政策、context=quote_full_review_veto / 1、attempts_per_case=1、automatic_retries=0。技术超时固定为30秒/CASE，不是质量阈值；冻结后更改配置或源码即拒绝运行。全48项、其他引擎、扩大/减少/重排8项清单均不由本入口接受。

已生成的私有准备目录：

`outputs/rag-retrieval-quality/e1a-b3a-validation-20261105/prepared-runtime-v2/`

| 冻结对象 | SHA-256 |
|---|---|
| B2 ReferenceRelease规范JSON | fece48317074ddf52b74ba1ec2692094b4b48d8d0b15ed9d1f95213cc5f3f6cc |
| B3 RunConfig规范JSON | 389bc108bbdb35f246f98e13ce7716861e8dc155ff0e69410735dc960604ade6 |
| config.json文件字节 | 83f8fb8db8f304e06cd162f504441e3b9b516cd3bb39833f5bd82b6ee6f30d80 |
| 108份Python源文件清单规范JSON | faf255f529ff0e29c68c91932669d44c18d0190bde0032cd5100b60a8c9d9860 |

最终活动准备版本为prepared-runtime-v2；先前v1及其检查记录保留且从未运行，v1不再作为当前代码配置使用。这里的v2是本地配置目录修订，R0规则版本仍不变。

源码清单包含新增/未提交源码，不只使用Git HEAD。实际执行还会单独登记运行副本内解释器、依赖和源码的文件摘要，并核对复制后的源码与冻结清单完全一致。运行配置、输入、批次、上下文、问题、契约和候选键顺序均绑定；本次只创建准备目录，没有执行占用记录或真实预测授权。

这些都是本地完整性摘要，不是独立来源封存或人工批准的替代物。

## 4. 入口与数据流

```powershell
.venv/Scripts/python.exe -B -m data_analysis_agent.reviews.e1a_b3_runtime --help
```

四个子命令显式分开，不存在“准备后自动预测”：

1. **freeze**：核对B2发布及来源锚点，从原bundle只取问题文本，从已确认契约只取查询条件，从已保存探索输入取原候选。逐条经过完整观察来源校验后，再从独立保存来源记录投影comparison_batch。写入全新的准备目录，预测授权仍为false。
2. **check**：只检查配置、当前源码及全部8份请求的身份、范围、摘要和顺序，不调用规则。
3. **run**：要求单独提供B3执行permit及其外部固定摘要、授权依据文件及摘要、配置摘要、新运行目录和受保护参考路径。permit绑定data_kind和整个配置，发布权限不能代用。真实输入还必须包含原B2参考、原会话、原bundle的访问检查目标。只有OS探针实际成功，才尝试当前CASE；真实worker入口也要求额外绑定的LaunchGrant。
4. **audit**：独立控制进程读取已经固定摘要的raw manifest，逐项验证落盘文件后，才加载参考/来源进行审计并计算指标。不会调用预测器或修改raw。

本轮实际已执行freeze和check，在禁止网络、psycopg连接及真实_judge调用的保护下完成。**没有对真实准备目录调用run或audit。**

可安全复核本次已准备输入的命令：

```powershell
.venv/Scripts/python.exe -B -m data_analysis_agent.reviews.e1a_b3_runtime check --prepared outputs/rag-retrieval-quality/e1a-b3a-validation-20261105/prepared-runtime-v2 --config-sha256 389bc108bbdb35f246f98e13ce7716861e8dc155ff0e69410735dc960604ade6
```

run/audit完整参数可通过对应`--help`查看。本轮不创建真实执行permit或可直接启动真实预测的命令文件。Operation/ExecutionPermit及LaunchGrant是控制面完整性绑定，不是数字签名、人员认证或历史来源证明；真实执行仍要求用户的明确授权。

### 4.1 worker实际收到什么

WorkerRequest只包含问题/PredictionInput、契约与版本、当前列表的候选、允许完整评论上下文、同列表的独立来源比较投影、scope及摘要。比较投影不是待测candidate的自复制：控制面先校验完整已保存SQL来源，再从来源records按当前key投影；worker再次核验逐字段来源和quote区间。

完整93条来源集合、其他CASE的评论、ReferenceRelease、人工会话、fact/relation人工等级、人工许可/缺失答案、决定理由、0/1/2评分、cosine distance及指标都不传入worker。空列表不附带其他评论。活动契约中的independent_target_fact_ids是查询条件，不是人工实际许可答案。

R0支持事实仍只能来自quote；完整评论仅解释/否决。现有B上下文规则、unknown政策、必要关系和保守拒答全部保留。规则不认识某条件时仍报unknown，而非伪装成已证明无证据。

### 4.2 结果与失败保存

后续获准运行时，reference留在原B2保管目录；新运行目录分为runtime、raw、isolation、audit。worker只读公共运行副本和当前CASE目录，通过专用管道返回输出；raw和audit由控制进程写入，worker不获写权限。

每个主尝试在raw/CASE-ID下以独占创建方式保存output.raw、stderr.raw和receipt.json，先写精确字节，再检查JSON/输出结构。记录输入摘要、实际时间、耗时、进程退出码、超时、失败类型和raw摘要。没有输出也保留空raw及终态，不能当作insufficient。

execution-claim.json避免同一准备版本被静默换目录重跑；发生异常不自动删除占用记录或重试。超时、输出过大、非法JSON、身份或引擎执行失败均保留首结果并停止后续尝试；其余CASE记录not_attempted_after_stop。准备运行副本失败则全8项记录not_attempted_setup_failed。OS失败记录isolation_or_launch_failed，不执行当前R0。

独立后审计保留全部8项，失败和未执行项目仍进入应评台账；超时的部分JSON不能恢复成正常成功。原始潜在越界许可及不可解析输出仍按B1失败/N/A规则暴露，不靠失败缩小风险统计。

## 5. Windows权限实现及实际验证

实现参照Microsoft的[AppContainer启动说明](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)和[进程属性API](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute)：使用独立Package SID和SECURITY_CAPABILITIES，能力集合为空；只对新建公共代码/当前输入目录授予该SID的RX。未更改原参考、项目根目录或已安装Python的ACL。

启动时先挂起进程，通过Windows令牌API核对TokenIsAppContainer和实际Package SID，加入只允许一个进程、关闭即终止的Job，再恢复执行。禁止子进程，只继承明确列出的NUL stdin和stdout/stderr管道；环境仅含Windows启动所需路径，不继承凭据、.env或PYTHONPATH。临时AppContainer配置在退出时删除，不创建Windows用户，不修改系统/桌面ACL。

权限探针与预测是不同进程。探针尝试以只读权限打开受保护文件/目录并立即关闭，**不读取正文**；受保护路径必须真实返回Access Denied；同时当前runtime文件须能只读打开且写打开被拒绝，输出完整成功回执才放行。仅有目标不存在、进程失败、空输出或自报状态均不能通过。实际预测worker不收到这些参考路径。

### 5.1 本机实际结果

| 检查 | 实际结果 / 边界 |
|---|---|
| 普通合成worker CLI | 成功，输出与原R0一致；只是普通子进程功能验证，不是权限证明 |
| 独立CPython运行副本 | 成功导入并执行合成R0；无需访问已安装venv |
| 工具默认环境 | 父进程已是AppContainer；嵌套启动失败，未用同权限进程降级 |
| 必要执行权限申请 | 已正常申请并执行工具沙箱外的限定合成测试；不是自动审批拒绝 |
| 独立worker令牌 | 已由OS核对TokenIsAppContainer=true、Package SID一致；能力数0 |
| 实际权限专项 | **1项失败，0项通过，0项跳过**；Python退出码3221225794（0xC0000142），stdout/stderr均为空 |
| reference读取被拒绝 | **未验证成功**：探针尚未执行，不能推断权限已经正确 |
| 真实worker端到端超时/权限/输出 | 尚未通过OS链路验证；合成控制层测试不能补上这项 |

早期尝试让worker写私有raw低完整性目录时，icacls设置标签返回Access is denied，提升执行后仍失败。当前实现改为控制面写raw的专用管道方案，不再要求这一标签权限。随后AppContainer进程能创建，但仍在Python初始化前退出。指定既有默认desktop等诊断未解决；未修改桌面ACL、关闭安全控制或安装服务。

`0xC0000142`表明初始化失败；**尚未定位具体失败DLL或宿主权限对象**，不能将推测写成已证实根因。当前事实是令牌创建成功、权限探针未运行成功。B3-A不得因此宣称OS隔离通过，也不能直接进行真实预测验证。

实际失败日志：`outputs/rag-retrieval-quality/e1a-b3a-validation-20261105/actual-windows-isolation-final.log`；结构汇总为actual-windows-isolation-final.json。日志保留真实失败，而非将其转换成跳过或insufficient。

## 6. 合成及回归测试

| 测试层 | 实际结果 |
|---|---|
| B3专项默认集合 | 17项：16通过，0失败，0错误，1个OS专项留待单独执行 |
| B3+B1及适用既有回归 | 255项：254通过，0失败，0错误，1个相同OS专项未在此集合执行；包含上面16项，不相加 |
| 单独Windows实际权限专项 | 1项：0通过、1失败、0错误、0跳过；不能计为验收通过 |
| 两项已知队列测试 | 2项：0通过、2错误，仍为SQLite进入仅支持PostgreSQL持久队列；未修原业务逻辑 |
| 本轮真实输入离线检查 | 8/8通过；不是8次真实预测 |

合成覆盖固定CASE的缺项/增项/重复/顺序/模式；共享契约和源码变化；顶层及嵌套人工字段注入；候选之外来源不进入worker；输出JSON、来源身份与quote精确区间；空候选；unknown；same_review/before；跨评论错误拼接；无引用direct许可；全拒答的过度拒答率；超时、缺输出、非法输出和执行失败的精确raw保存；独立审计不调用规则；全8项失败台账、N/A及U/F分母。合成测试将请求标记为frozen_query，验证缺少LaunchGrant时会在规则执行前拒绝；没有拿真实评论执行这一测试。

超时和失败调度在合成控制层验证；实际Windows探针失败，不能声称OS下超时终止与端到端预测已验收。审计/汇总合成用例中8项全部进入分母登记，6项有效比较、1项不可解析、1项超时；完整U/F配对有效数为2。全拒答用例有证据许可率0、过度拒答率1，不能包装成质量改善。

专项命令：

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_e1a_b3_runtime.py -v
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/e1a-b3a-validation-20261105/run_final_synthetic_checks.py
```

Windows实际专项通过PowerShell内联Python驱动设置E1A_RUN_OS_TEST=1，仅加载WindowsIsolationTests，在正常申请执行权限后运行。后续也可在普通Windows用户终端执行以下**仅合成**测试；通过之前仍不得运行真实预测：

```powershell
$env:E1A_RUN_OS_TEST = '1'
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_e1a_b3_runtime.py -k actual_appcontainer -v
Remove-Item Env:E1A_RUN_OS_TEST
```

回归包含human_adjudication、adjudication_page、q20_adjudication_sync、adjudication_live_validation、e1_readiness、exploratory_e1、query_contract、evidence_judge、retrieval_evaluation、historical_snapshot、e1a_b1和本轮模块。测试控制进程禁用真实网络和psycopg连接。普通/副本worker的子进程测试只使用合成R0数据，不接收真实评论。

两项队列错误保留完整测试ID：

- test_review_scope.ReviewScopeTests.test_run_date_filters_incremental_skip_and_scoped_progress
- test_review_scope.ReviewScopeTests.test_run_retry_and_cutoff_forwarding_with_real_mocked_sdk

错误仍为“持久标注队列只支持 PostgreSQL。”，本轮未连接业务数据库来解决测试。

## 7. 私有证据与保护检查

全部本轮运行输入和日志位于Git忽略目录：

`outputs/rag-retrieval-quality/e1a-b3a-validation-20261105/`

主要材料包括protected.before.json、protection-check-final.json、specialist/regression/known-queue-errors的日志和结构汇总、actual-windows-isolation的失败证据、run_final_synthetic_checks.py、real-input-freeze-final.log、runtime-input-validation-final.json、prepared-runtime-v2/及早期合成os-probe/材料。旧准备、B2发布、E0145和原人工文件不覆盖。

保护检查实际结果：开始时工作区干净；**388个既有私有文件全部摘要不变**；105个既有源码仅e1a_prediction.py发生上述获授权的提取，其他104份不变，另新增3个模块。原strict接口和R0语义内核未变；原会话、48份参考及冻结222条候选未变。108份当前源码与冻结配置一致。

`git diff --check`通过；新私有输入受outputs规则忽略；`git ls-files outputs/`无输出。没有Git提交或暂存私有评论/标签。保护摘要证明的是文件完整性，不是历史来源证明或独立封存。

## 8. 未解决事项与B3-B放行条件

**现在不满足B3-B条件。** 首要阻塞为AppContainer内Python初始化失败，尚无reference访问拒绝的有效实测证明。必须先在可用Windows会话复现并修复这一运行问题，或另行审查具备实际账户/ACL边界的实现；不能以普通目录隔离或绕过探针代替。若要修改系统/桌面ACL、创建账户/服务等，应另行明确授权，本轮未执行。

之后仍需：

1. 同一冻结运行器通过真实OS的合成权限专项，确认runtime可读、reference不可读及正常输出；完成OS端到端超时/失败验证。任何代码修改另建配置版本，保留本次版本和失败材料。
2. 用户明确授予B3-B仅8项真实R0调试权限；提供绑定最终配置的真实permit和授权依据，不能复用本轮B3-A或B2发布权限。
3. 执行前重验输入、参考、源码/运行副本与环境摘要及受保护路径，固定一次主尝试政策；不根据预期成绩改规则或参考。
4. 真实raw保存后执行独立审计；未知语义、未评引用和失败保留原N/A/覆盖口径。质量数值门槛仍待确认，已见24题不能称为独立泛化测试。

探索路线不要求重做人工裁决或继续M1–M3读取；历史可信E1仍缺独立历史状态和封存依据，不能在此升级。当前到此停止，不运行B3-B/B4、真实R0/L1、外部LLM或Agent集成。
