# P5 Text Evidence 单次真实可行性验证结果

2026-11-07。**本次唯一真实请求返回 HTTP 200，Text Evidence 新契约解析、精确文本定位及原 B1/B2 审计均通过。五条标签与冻结预期一致，三个实际引用按冻结语义标准阅读均充分。** 本组开发案例标签匹配为 **5/5**；这不是生产准确率或独立人工金标准成绩。

全过程保持 `publication_allowed=false`、`semantic_verified=false`、`permitted_target_fact_ids=[]`。原 summary 的 `formal_accuracy=null`、`semantic_basis_review=pending_independent_reading` 保留原样，本报告只追加按冻结标准的阅读结论，不回写许可或实验产物。

## 授权、预检与唯一执行

执行前阅读[准备报告](rag-engineering-p5-text-evidence-readiness.md)、固定入口、新 Schema、案例、定位和诊断实现，完成只读 preflight：`missing=[]`，输出目录未占用，案例/提示/Schema/输入/完整请求摘要全部一致，输入估算 7453，不超过 8000。

仅执行一次用户指定命令：

```powershell
.venv/Scripts/python.exe -B scripts/run_rag_shadow_text_evidence.py run --authorize-fixed-text-evidence-shadow --accept-estimated-token-risk
```

命令正常退出，控制台回执为 completed、stop_reason=null、model_calls=1、failure_stage=null。运行记录时间 UTC 2026-11-06 18:29:26.648–18:29:33.169，即 Asia/Taipei **2026-11-07 02:29:26.648–02:29:33.169**。执行后没有再次运行入口、清理目录、重试、换模型或补跑。

实际目录：[text-evidence-synthetic-v1](../outputs/rag-semantic-retrieval/text-evidence-synthetic-v1/)。读取 owner-only 产物使用获准的只读权限，未修改 ACL。

## 1. 供应商对新 Schema 的接受情况

本次实际模型为 `deepseek-ai/DeepSeek-V4.1-Flash`，提示 `p5-text-evidence-instructions-v1`。usage 记录 HTTP 200、response_received、finish_reason=stop，请求和响应模型一致，严格输出 Schema 摘要与预检及本地请求复算一致。因此可确认**供应商接受了本次携带新 Schema 的请求，并返回可由新契约处理的内容**。

这是一条真实可行性观察，不证明所有非法变体都会被供应商拒绝，也不建立通用 Schema 严格执行保证。usage 内的 `new_schema_provider_acceptance="unverified"` 是 Provider 初始化时固定记录的字段，本次实现不会在响应后更新它；本报告依据 HTTP/usage/本地转换回执作上述有限判断，不修改该原始字段。

## 2. 新契约解析与留存边界

实际执行状态为 `validated_shadow`。按已执行且未变更的转换路径，原始内容先通过 `p5b-text-evidence-output-experiment-v1` 解析、request_id/input_sha256 绑定、完整事实×候选矩阵检查，再唯一定位引用并构造标准 B1 Span，最后通过原 audit_output。该契约禁止模型提供 start/end，没有忽略模型坐标或修复旧输出的步骤。

**成功路径没有持久化原始 Text Evidence 输出正文。** 保存的是规范化后的 B1 judgments，以及记录原始输出 2678 字节的执行回执；没有可供重新解析的原始新契约 JSON，也没有其原始正文哈希。新契约解析成功依据实际执行回执与调用路径，不能额外声称已经逐字复核原始响应或供应商严格 Schema 的每个必填字段。未从 B1 judgments 反向重建、补存或冒充原始模型输出。

## 3. 五条候选的引用定位结果

本次仅有三个非空引用。只读复核按各自候选 key、source identity、全文、quote 绝对范围及引用类型再次逐字定位，三个都只有一个允许位置，且与保存的 B1 Span 完全一致。

| 候选 | 引用类型 | 保存的绝对 code point 区间 | 实际引用与位置核验 |
|---|---|---|---|
| T601 | support_refs | **`[10,31)`**，21 字符 | 我询问保修日期，客服嘲笑我连日历都不会看。——完整 quote，唯一、来源一致、切片一致 |
| T602 | 无引用 | 不适用 | 三个引用数组均为空，符合 not_supported 的本次标准，不是定位失败 |
| T603 | refutation_refs | **`[11,37)`**，26 字符 | 先前那句客服辱骂我的投诉是我捏造的，我现在明确撤回。——完整 quote，唯一、来源一致、切片一致 |
| T604 | context_veto_refs | **`[20,54)`**，34 字符 | 重新核对工牌：那是仓库收货员，先前对客服的指责失实，我撤销该项差评。——同评论全文否决段，唯一、来源一致、切片一致 |
| T605 | 无引用 | 不适用 | 三个引用数组均为空，保留 unknown，不伪造客服身份或撤回依据 |

区间均满足 `end=start+len(text)`。support/refutation 没有越出对应 quote，context veto 没有跨评论。T601 的全文前缀包含 emoji，程序起点仍为 code point 10，未按 UTF-16 单元或 UTF-8 字节计数。没有发生重复/重叠歧义、缺失原文或坐标不匹配。

## 4. B1/B2 结构与来源核验

实际回执和对已保存 judgments 的原 B2 只读再审计一致：

- 标准 B1 输出可解析，五个 `service_bad × candidate` 判断完整、唯一，无未知事实或候选。
- request_id 为 `TEXT-EVIDENCE-T601-T602-T603-T604-T605`，后端输入摘要与 preflight、usage、Worker 回执一致。
- 五个候选的 quote、完整评论、全文 SHA-256、来源字段和保存的合成 retrieval 与冻结输入重新准备结果一致；冻结预期快照未改变。
- 三个实际引用的 candidate key/source identity 和全文切片均完全匹配。程序定位结果独立复算仍为上述三个唯一位置。
- 原 audit_output 返回 validated_shadow、structural_goal_satisfied=true、reason_codes=[semantic_review_required]；没有错误详情。

`model_supported_fact_ids=[service_bad]` 只表示 Shadow 中的结构覆盖；正式许可集合仍为空。本组是单事实、没有必要关系，relation_judgments 为空；不能据此宣称复杂关系语义质量已验证。

来源始终为 SyntheticEncoder/SyntheticRepository 构造的虚构输入，经原 P0 的本地来源核验。保存 JSON 的一致性不构成业务数据库或历史生产来源证明。本轮未查询业务 PostgreSQL、调用 BGE 或发送真实商品评论。

## 5. 标签比较与实际语义依据

以下阅读直接基于冻结评论和实际保存的引用，不用模型 rationale 自证：

| 候选 | 冻结预期 | 实际标签 | 按冻结标准阅读 |
|---|---|---|---|
| T601 | supported | supported | 本人明确报告被客服嘲笑，完整支持引用包含人物、行为与本人体验；充分，无 quote 外补充支持 |
| T602 | not_supported | not_supported | 只有客服客气的正面体验，没有否认或撤回自身原投诉；正确区分相反体验与 refuted |
| T603 | refuted | refuted | 明确承认原投诉捏造并撤回；反驳引用完整保留实际否认和撤回，充分，不是只摘“客服辱骂我” |
| T604 | refuted | refuted | 全文纠正对象为仓库收货员，并撤销客服差评；实际 context_veto 引用包含更正和撤回，充分，只用于否决原 quote |
| T605 | unknown | unknown | 作者明确无法分辨客服/门店联络员；全文仍未消除角色歧义，保留 unknown 合理 |

标签匹配 **5/5**；本报告对这五条的引用充分性和状态按冻结标准阅读也均符合预期。与旧 D/E 组的整批结构失败不同，此次五条是结构有效的本组比较；不过样本小、由开发者预置、不是独立人工金标准，没有同文本 v3 对照，不能推算泛化改善幅度或正式生产准确率。

本报告不调整预期，也不将人工式阅读结论写回 `semantic_verified`。原始 summary 的 formal_accuracy=null 和 pending_independent_reading 标志保持不变。

## 6. 失败阶段与诊断状态

**本次没有触发失败阶段。** `failure_stage=null`、`stop_reason=null`，不是 Provider/Schema/绑定/定位/B1转换/B2 审计/标签不匹配失败。

`failure_diagnostic_saved=false`，且 `diagnostics/failure.json` 不存在：成功路径不应保存失败诊断，不能解释为诊断写盘故障。`semantic_review_required` 是保留独立语义审查边界的状态码，不是结构失败。

本次全部区间来自新契约的程序定位，没有将 D74/E84 无效输出改写或晋级。旧实验状态、v2/v3 和历史产物保持原样。

## 7. 实际 token、耗时和费用估算

| 项目 | 本次记录 |
|---|---|
| 请求 / HTTP / 结束原因 | **1 / 200 / stop**，SDK/HTTP 重试配置均为 0，Worker attempts=1 |
| 输入估算 / 实际输入 | **7453 / 3040 token**，原输入边界 8000 |
| 实际输出 / 输出参数 | **940 / 2500 token** |
| 总 token | **3980** |
| 思考 / 缓存命中 | **0 / 0 token**，无 reasoning_content |
| SDK 请求耗时 | **6.515 秒** |
| Provider 总耗时 | **6.531 秒** |
| Shadow 总耗时 | **7.610 秒**，包含 Provider，不与其相加 |
| 项目固定忙时费率估算 | **¥0.0136**，`(3040×2+940×8)/1000000`，与 usage 一致 |
| 独立供应商账单 | 未查询，actual_bill_known=false；不是已核对的实际扣款 |

本次未见超时、截断、模型身份冲突、非零思考 token 或预算超限。费用按项目固定忙时输入 ¥2、输出 ¥8／百万计算，不因运行时段自行套用未核对的优惠。服务别名不是不可变模型快照；单次实际 token 不能变成今后估算误差的保证。

## 8. Worker 回收和正式发布保护

Worker PID=21472，exit_code=0、reaped=true、threads_released=true、termination_requested=false。回执支持该子进程正常退出并回收；没有用“有响应”代替资源清理成功。

`publication_allowed=false`、`semantic_verified=false`、`permitted_target_fact_ids=[]` 在 summary 和 outcome 中一致，没有正式发布、reported_experience_v2 更新或 Agent 接入。执行前登记的 389 个既有源码、实验、Schema、夹具和文档文件在完成后的摘要核对全部不变；仅新增本报告及本次新私有运行产物。

## 9. 实际产物与只读复核范围

本次目录实际有九个文件：contract.json、execution-summary.json、frozen-expectations.json、model-input.json、preflight.json、provider-usage.json、retrieval.json、shadow-execution.json、shadow-judgments.json。没有失败诊断或独立原始 Text 输出文件。

| 关键产物 | SHA-256 |
|---|---|
| [execution-summary.json](../outputs/rag-semantic-retrieval/text-evidence-synthetic-v1/execution-summary.json) | `a4a4de063a6c02c0fcc3bb4c7fcd12f659880d3bfe54d62dcf54b7b3391894b5` |
| [provider-usage.json](../outputs/rag-semantic-retrieval/text-evidence-synthetic-v1/provider-usage.json) | `87b5b535fd985ebc4e7db0087a34e3f0593ce044ff3a4e32b08206d6ebafb5a9` |
| [shadow-judgments.json](../outputs/rag-semantic-retrieval/text-evidence-synthetic-v1/shadow-judgments.json) | `c60c1e3ef0b754bc66c5aa36b8e042bea358f6c2cee5e699fc270bd0535d3ad3` |

只读复核了冻结案例、完整输入/来源、预期快照、三个唯一引用、标准 B1/B2、模型/提示/Schema/请求绑定、标签比较、费用复算和回收标志。检查代码屏蔽真实网络、SDK 客户端、PostgreSQL 和 BGE，没有额外模型调用、账户查询或测试重跑。

本次提示 SHA-256 为 `a658b5667e7596461d46795300a151159233a6a54cbbe1659125434a4791af95`；严格 Schema 为 `332ff61606e85686a562a8281bd10b8794700d894c7cc10d992ad15edd469c8d`；后端输入为 `881126f4713ce312da2478832144f1ce228ffdac8659a86741c6efe0d217a85c`；完整请求体离线复算为 `06c4ec01c17a7a604698f4e4c23b952985c838da7bffac2a8991911af324da8b`。均与调用前冻结一致，模型投影未包含预期标签、理由、引用标准或评分规则。

**唯一机会已使用，结果报告完成后停止。不追加实验、不正式集成，不删除或覆盖运行目录。**
