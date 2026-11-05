# RAG Step 4B E1-A B1：离线实现与合成验证

日期：2026-11-05，Asia/Taipei。

**B1已完成：新增探索性参考发布器、独立预测契约、R0适配、独立审计与指标计算。28项专项、238项含专项回归通过。实际参考发布0次，真实8组/48组预测0次。** 本轮仅按用户确认的E1-A v1实施B1，没有执行B2或后续运行。

协议依据：[E1-A方案](rag-step4b-e1a-exploratory-evaluation-plan.md)。该方案已在本轮用户指令中获确认，不再以旧文档的“待确认”字样阻塞B1；参考发布、真实预测及外部模型仍未授权。

## 1. 实际新增文件

| 文件 | 职责与主要入口 |
|---|---|
| [e1a_protocol.py](../src/data_analysis_agent/reviews/e1a_protocol.py) | PredictionInput/PredictionOutput、操作授权记录、固定来源政策及R0结构条件；digest、authorize、validate_prediction_input |
| [e1a_reference_release.py](../src/data_analysis_agent/reviews/e1a_reference_release.py) | ReleasedCase/ReferenceRelease；build_reference_release构建新发布物，publish_reference_release独占目录发布 |
| [e1a_prediction.py](../src/data_analysis_agent/reviews/e1a_prediction.py) | build_prediction_input白名单、validate_observed_sources独立观察投影复核、run_r0纯离线适配 |
| [e1a_output_audit.py](../src/data_analysis_agent/reviews/e1a_output_audit.py) | audit_output：结构/来源/契约约束和已冻结人工精确引用映射，产生OutputAudit |
| [e1a_evaluation.py](../src/data_analysis_agent/reviews/e1a_evaluation.py) | evaluate_e1a：读取固定审计结果、保留应评台账、计算条件及全范围覆盖指标和F−U |
| [test_e1a_b1.py](../tests/test_e1a_b1.py) | 28项完全合成测试及合成会话/来源/授权夹具 |
| 本报告 | 真实完成范围、执行接口、验证与剩余边界 |

本轮没有修改既有Python模块、原strict接口、人工会话、冻结run、原始参考、旧阶段报告、检索/过滤/排序、Agent、API、前端、数据库或Embedding。没有新增依赖。

实际日志位于Git忽略目录 `outputs/rag-retrieval-quality/e1a-b1-synthetic-validation-20261105/`。测试中的参考发布仅发生在合成临时目录，未在项目真实release路径生成参考。

## 2. 契约、执行接口及权限边界

### 2.1 独立输入与输出

输入版本为e1a-prediction-input-v1，复用QueryContract和ExploratoryInput，包含原问题及摘要、活动契约及修订号、独立目标声明、实验/引擎身份、候选和上下文/来源摘要。由validate_prediction_input重新校验scope、内容摘要、目标ID及原Top-5边界。

输出版本为e1a-prediction-output-v1，包含execution_status、三状态或失败null、逐事实FactFinding、逐关系RelationFinding、许可/缺失集合、精确FactEvidence、上下文否决记录、原因及原始R0结果。结构均可JSON往返，拒绝非法枚举及额外字段。

`build_prediction_input()`只接受显式白名单参数，不接收bundle、session、reference或人工评审备注。查询侧independent_target_fact_ids属于已确认查询条件，不是人工实际支持答案。人工事实等级、许可/缺失答案、评分、距离、确认理由和审计建议不能成为输入字段。

### 2.2 操作授权

OperationAuthorization绑定action、data_kind、scope、操作对象的规范摘要及approval_ref；调用方还须从独立控制面提供预期授权记录摘要。发布和预测的action分开，合成授权不能用于frozen_query，其他问题/范围的授权不能复用。

这些记录是可校验的授权绑定，不是数字签名或人员认证；调用方不能自行把本地摘要包装成来源独立封存。真实使用仍须有用户已明确授予的对应操作权限。本轮只构造并使用标记synthetic的模拟授权。

### 2.3 调用方式

模块是无导入副作用的函数库，本轮没有提供会自动执行真实批次的CLI。

| 动作 | 后续调用方需要提供的内容 |
|---|---|
| 构建参考发布物 | 原bundle/session、两者预期规范摘要、publish_reference授权及预期摘要、release_id、实际发布时间 |
| 写入发布目录 | 上述参数及不存在的目标目录；真实数据目标必须位于项目outputs/rag-retrieval-quality私有范围 |
| 构建预测输入 | 问题/契约/修订/独立目标、既有ExploratoryInput、scope和预期问题/契约/批次摘要 |
| R0预测 | 固定PredictionInput、独立保存的ExploratorySources、预期input/source摘要、predict_r0授权及预期摘要 |
| 审计 | 同一输入/来源、已发布参考及预期摘要、原始输出或None；不传预测器或模型对象 |
| 汇总 | OutputAudit集合、完整expected_pairs、各审计结果的预期摘要，可选冻结分组 |

session规范JSON摘要与原始会话文件字节SHA-256不是同一个值。B2调用方须先核对原导出文件锚点，再计算/固定结构摘要供发布函数使用，不能把两类摘要混传。真实授权及实际发布时间由获准的发布调用方提供，不能复用测试时间或合成人员。

本轮实际执行的专项命令为：

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_e1a_b1.py -v
```

该命令只运行合成夹具，不加载真实人工会话或真实评论执行预测。

## 3. 参考发布器：已实现，尚未实际发布

发布器先校验bundle/session固定摘要和操作授权，再调用既有validate_session。只有契约、CASE、配对及最终完成记录均有效，才从当前已确认事件派生新HumanReference。

新发布物复用原state、逐事实/关系、许可/缺失集合、来源区间和本人决定理由；reviewer_decisions从原真实确认事件投影，不增加评审人。human_confirmed、contract_confirmed与confirmed状态只在新发布物中由有效事件和发布权限推导，原会话与pending包装不修改。没有凭布尔值代替审计，也不把同人复核伪造成独立adjudicator。

ReferenceRelease保留原会话/bundle/完成事件及每题/每CASE事件摘要、原问题摘要、契约修订、来源摘要、单人限制、既往相关性评分接触情况及实际发布时间。固定history_unproven、independent_seal=false，不能升级历史状态。

写入先在同父目录暂存reference-release.json与manifest，再移动为最终目录；已有目标拒绝覆盖。写入失败清理受限暂存目录，不生成“已发布”标记。真实frozen_query输出另受项目私有目录边界约束。

测试证明：缺完成事件、事件被篡改、授权action/data_kind/scope不符或摘要不符都会拒绝；即使测试重新绑定输入摘要，缺失的真实确认链仍不能绕过。合成发布失败及重复执行均保持原数据。

## 4. R0探索入口与隔离验证

run_r0从sources.records按当前候选key重建独立比较批次，检查来源/范围/种类及摘要、两次带时区观察、完整评论快照覆盖、quote/原文区间、输入哈希、稳定观点键、排名、来源引用及上下文摘要。比较记录来自保存的SQL观察投影，不复制候选充当来源。

该入口不调用prepare_trusted_batch或strict judge入口，不修改其规则；在明确的探索政策校验后复用现有纯规则内核。raw_r0原样保留，合成对照验证其状态和精确引用与原R0一致。来源校验失败时不执行规则，返回validation_failed/status=null；内核异常返回execution_failed/status=null。

匹配的受控显式条件给direct及原精确引用；未建立匹配给unknown，不反推not_supported。必要关系只暴露原R0联合分配已确认的结果，否则unknown。原R0对uncertainty/clarification、统计/澄清任务、关系绑定目标不作为独立子答案的保守政策保留。

R0的missing是其运行策略下的条件诊断：正常any_of充分时为空；若澄清/统计等策略阻断，仍保留原R0未匹配事实，不能为了与人工答案相同而重写。审计显式识别这一版本化R0策略；与人工的状态、许可、缺失集合差异照常进入评价。查询因保守政策被拒答仍是正常预测，不能改成执行失败后从过度拒答分母消失。

隔离验证结果：

- 顶层输入、ExploratoryInput、EvidenceBatch及candidate层注入reference_state均被严格模型拒绝。
- 序列化预测输入不包含人工findings、reviewer_decisions、决定理由、评分、距离或评审人标识。
- R0函数没有reference参数；测试在禁止文件打开、网络及strict准备/判断入口调用时仍可执行合成预测。
- 参考和审计仅在评价侧使用；原问题和独立目标还与发布参考绑定，替换问题或扩大独立目标会被拒绝。

以上证明的是**函数和数据结构的逻辑隔离**，不是OS账户/ACL或恶意代码沙箱。真实worker进程、只读runtime目录和文件权限隔离尚未实现，需在B3运行前另行落实；不据此声称能抵御拥有相同系统权限的任意代码读取参考。

## 5. 独立输出审计与指标

audit_output不调用R0、judge_evidence或validate_judge_result，不靠重新执行同一预测规则来当语义验收。它复核固定输入、来源和参考，校验：

- 当前query/mode、契约内容/修订、问题摘要、独立目标、batch/context及来源身份。
- 事实/关系ID完整唯一，direct有引用，许可与直接支持一致，all_of/any_of、R0明确策略、必要关系及缺失集合一致。
- 引用必须在本列表、身份字段一致、落在quote半开区间；quote外上下文不能补入支持。same_review不能跨source/dataset/product/review拼接；before/contrast语义另受人工关系映射约束。
- sufficient/limited/insufficient、许可索引和逐事实引用一致；失败不能携带正常结论冒充insufficient。

可信调用参数或参考身份不符会明确抛出错误终止；输入来源检查及可解析预测错误产生明确失败审计，保留错误码与原始输出摘要。缺输出None单独记missing。可解析的非法输出引用仍统计；无法解析的输出使主引用有效率保留N/A，不因少算失败输出制造100%。

精确人工映射审核分为：已获人工许可且fact/key/精确区间/必要关系及上下文映射均覆盖→支持；许可超出人工集合→许可级不支持；其他合法新子区间或来源映射→未评。未评不自动算错或对，主支持率N/A，另报已评子集和覆盖率。这个组合结论是“人工许可及引用映射支持”，不证明新的自然语言理由都正确。

指标由固定OutputAudit计算，新增/包含：三状态一致率及混淆矩阵、有证据许可率、过度拒答率、无证据不当许可率、事实/关系一致率、许可/缺失集合一致率、引用有效率、支持映射覆盖、全参考成功率与许可覆盖、可答未服务率和unknown覆盖。

全部expected_pairs进入终态台账，缺审计、缺输出、来源失败、执行失败、验证失败均保留；零分母N/A。条件指标只在有效比较上计算，同时报告完整H/N覆盖及损失，不能将失败当正确拒答。U/F逐query计算F−U，检查契约内容及修订、来源/参考版本、引擎/上下文/实验身份，两侧指标都有效才进入配对分母；输出逐项排除原因，支持冻结分组。

合成“全部拒答”测试得到：可答类许可率0、过度拒答率1、未服务率1、一致率0；它不能被包装成安全提升。另有非零F−U、零关系/零引用N/A、语义未评、无支持却许可、缺输出及失败台账测试。

## 6. 实际测试与保护结果

| 项目 | 本轮结果 |
|---|---|
| B1新专项 | 28/28通过，0失败、错误、跳过 |
| 专项及适用既有回归 | 238/238通过，含上述28项，不重复相加 |
| 两项既有队列测试 | 单独执行：0通过、2错误，仍为SQLite进入仅支持PostgreSQL队列；未修业务逻辑，未计通过 |
| 真实参考/预测/模型/业务数据库读取 | 均为0 |
| 原文件保护 | 既有私有材料及Python业务源码按开始时摘要复核；原始会话、E0145、候选及strict接口未改动 |

组合回归加载test_e1a_b1及human_adjudication、adjudication_page、q20_adjudication_sync、adjudication_live_validation、e1_readiness、exploratory_e1、query_contract、evidence_judge、retrieval_evaluation、historical_snapshot。真实网络和psycopg连接在测试驱动中禁用；队列错误另记，不静默跳过。

日志为私有目录中的b1-specialist.log、final-regression.log、existing-queue-errors.log、test-results.json及final-test-results.json。早期regression.log保留，最终采用补齐覆盖未知细分统计后的final结果。本轮发布和预测测试全部采用完全合成的数据与模拟授权，不使用原真实评论及人工裁决建立新公开夹具，也不把测试通过写成真实E1质量通过。

## 7. 已知限制、阻塞与B2资格

1. 当前仅R0，15个受控显式句式及既有保守政策不变；没有新LLM、自动查询解析、业务回答或检索策略优化。
2. 独立语义审计只覆盖已发布人工映射；新合法区间保持未评，不能全面理解自然语言理由、识别所有隐藏外推或替代人工关系审查。R0输出使用固定可审计理由，但未来其他模型不能据此直接放行。
3. 操作授权和审计摘要依赖调用方固定的控制面记录；它们不是签名认证、历史来源证明或独立封存。发布/评价调用方不能从待验证材料临时取值自证。
4. 没有真实批量运行CLI、worker权限隔离、预算/超时/重试调度或输出人工追加审计界面。函数库与合成验证不代表B3/B4运行环境已验收。
5. 57条history_unproven、独立封存与正式质量门槛的限制继续保留，历史可信批次仍0/48。

**B2的离线发布技术前置已具备：** 发布器和确认链/协议校验、保留原始记录、私有新目录与失败保护均已通过合成验证；不需要重新进行24份契约或48份CASE评审。但本轮没有用真实E0145执行发布器，也没有验证真实48份发布结果。

B2开始前须获得新的明确参考发布授权，先只读复核原导出文件锚点、当前事件链/活动版本和来源输入，再提供真实发布授权记录、固定规范摘要、实际发布时间及私有新目标目录。真实48份发布后还应逐份验证，不从28项合成通过推定48份发布已经通过。

OS隔离与真实预测授权属于后续B3/B4前提，不以本次B1自动满足。当前到此停止，等待B2或其他下一阶段的明确授权。
