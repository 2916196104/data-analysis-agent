# 语义判断与引用坐标分离：独立离线实验

2026-11-07。**选择方案 B：模型返回精确原文，程序在受信来源与指定范围内唯一定位，随后仍由原 B2 审计。** 已实现独立版本契约、最小转换原型及 24 项专项；加上原有 B1/B2 42 项回归，共 **66 项不同离线测试通过，无跳过**。没有真实 SiliconFlow、PostgreSQL 或 BGE 调用，没有重跑 D71–D75、E81–E85。

## 为什么选择 B

| 方案 | 与已有证据的关系 | 维护与验收 | 本轮决定 |
|---|---|---|---|
| A：模型继续返回坐标，只强化提示 | [D74](rag-engineering-p5-single-four-state-result.md) 的 `[25,79)` 终点越过全文 62；[E84](rag-engineering-p5-v3-independent-result.md) 在已强化的 v3 下仍返回 `[20,48)`，实际文字在 `[19,48)` | 接口变化最少，但正确性仍依赖模型计数；更强提示不能确定性排除这两类错误，仍须逐次失败关闭 | 保留原路径，不再为本轮新增一轮提示试错 |
| B：模型返回精确文本，程序定位 | 两条失败都说明模型所选文字可能确实存在，而坐标不可靠；E 组五条仅是诊断标签匹配，不能当成有效成绩 | 复用[已有定位原型](../tests/shadow_reference_locator_prototype.py)，对唯一匹配精确计算 code point，歧义可确定性拒绝；增加一个独立输出契约和薄转换层即可验收 | **本轮实现，限 tests 内离线使用** |

B 消除的是“模型手算 start/end”这一输出责任，不证明模型更懂语义，也不能保证模型逐字抄录、选择充分证据或避免重复片段。两份真实失败报告仅作为方案选择依据，本轮没有读取旧私有诊断、重放旧回答或生成修正后的历史结果。

## 输出契约与接口差异

新契约：**`p5b-text-evidence-output-experiment-v1`**，必须显式给出 version；机器可读 [Schema](schemas/p5-text-evidence-output-experiment-v1.schema.json)。定义和转换位于 [tests/shadow_text_evidence_prototype.py](../tests/shadow_text_evidence_prototype.py)，没有新增运行时模块或依赖。

| 项目 | 原活动 B1 输出 | 新实验输出 |
|---|---|---|
| version | `p5b-evidence-output-draft-v1` | `p5b-text-evidence-output-experiment-v1`，必填 |
| 单条引用 | key、source_identity_sha256、**start、end**、text | 仅 **key、source_identity_sha256、text** |
| 坐标生产方 | 模型 | 受信程序，且只能来自允许范围中的唯一精确匹配 |
| 事实判断 | fact_id、key、四状态、三类引用、reason_code、rationale | 保留相同字段及枚举；引用改为 TextReference |
| 关系判断 | relation_id、四状态、witnesses、支持/反驳引用、reason_code、rationale | 保留相同字段及枚举；关系引用也只返回文本 |
| 请求绑定 | request_id、input_sha256、execution_status | 保留；模型不能改契约、事实 ID、候选或必要关系 |
| 错误坐标 | 原样失败 | **字段本身禁止出现**；不忽略、不截断、不替换 |
| 自动发布 / 语义认证 | 无 | 无 |

新输出不包含最终答案、许可集合、置信度、修改后的契约或来源。输入继续复用原 B1 的候选、quote 绝对范围、全文及来源字段；没有修改活动三份 B1 Schema。这里“模型仅选择”指它提交事实/关系语义判断及精确文字，必要的请求绑定与审查字段仍保留，不能自行制造坐标。

离线入口签名为：

```python
evaluate(contract, result, raw, *, trusted_contract_sha256, request_id)
```

`result` 必须是原 P0 已核验、仍持有有效内存绑定的 SemanticResult；`raw` 是新版本模型输出字符串，本轮由本地测试预置。不存在接受任意旧 JudgeInput JSON 就自建来源信任的入口，也没有提供旧回答转换或修复函数。

## 信任边界变化

原流程要求模型同时提供语义选择和合法坐标；新流程允许**新实验输出省略坐标**，由程序作为坐标生产方。因此这确实改变了实验输出的接受契约，不能称为原 B1 的无行为变化修复。活动 B1/B2 仍完全不变。

程序只扩展坐标生产责任，不扩展来源或语义信任：

1. **来源准入**：调用原 `prepare_input`，复用固定契约摘要、scope、目标、P0 内存绑定、有效来源和全文哈希检查。不再访问 repository 或 encoder，不从模型返回的摘要反推可信来源。
2. **新输出结构与请求绑定**：严格解析新版本，拒绝额外坐标/许可字段、非法类型、重复 JSON 键和超出 65536 UTF-8 字节的输出；校验 request_id/input_sha256。
3. **引用定位**：复用原 `locate_fragment`，按候选 key 唯一绑定候选，核对 source identity、全文 SHA-256、quote 范围及切片；只做逐字匹配，不 trim、翻译、归一化或模糊匹配。
4. **原 B1/B2 独立审计**：新输出全部引用定位成功后，只在内存生成标准 Span 供原输出模型与 `audit_output` 检查。事实矩阵、候选归属、引用身份/范围/切片、四状态依据、重复引用和关系见证仍按原规则执行。
5. **独立语义阅读**：唯一定位只证明字符位置。B2 仍只检查结构与一致性，不能判断文字是否真正支持或反驳目标。语义验收继续需要独立标准，不使用模型 rationale 自证。

任一定位或 B2 步骤失败都不返回部分 resolved_output。成功只返回独立 `OfflineTextRun`，标记 `offline_experiment_only`；其 ShadowOutcome 始终 `semantic_verified=false`、`publication_allowed=false`、`permitted_target_fact_ids=[]`。`validated_shadow` 不意味着语义已验证，更不意味着可用于正式回答。

## 精确定位与歧义拒绝

| 引用类型 | 唯一允许的搜索范围 |
|---|---|
| fact support_refs | 当前候选 quote 的全文绝对 `[quote_start,quote_end)` |
| fact refutation_refs | 同上 |
| fact context_veto_refs | 当前候选对应的同一评论全文 `[0,len(review_text))` |
| relation support_refs / refutation_refs | 引用候选自己的 quote；原 B2 还核对它属于关系 witnesses |

发现一个匹配才计算 `start` 和 `end=start+len(text)`；Python 字符位置为 Unicode code point，不是 UTF-8 字节或 UTF-16 单元。发现两个位置即判歧义，计入重叠匹配；不选择首个、最近位置或用旧错误坐标消歧。

重复是针对**允许搜索范围**判断：某段在 quote 内唯一而在 quote 外重复，support 可由固定 quote 范围唯一定位；同段作为 context veto 在全文出现两次时必须拒绝。找不到、空文本、来源不一致、全文或 quote 漂移均失败关闭，不借用同评论另一个候选的 quote，也不搜索别的评论。

## 测试证据

[24 项新测试](../tests/test_shadow_text_evidence_prototype.py) 使用 F201–F205 和其他新合成文本，预期由开发者预置；没有调用待测模型生成答案。

| 边界 | 实际验证 |
|---|---|
| 来源 | 未验证/伪造 P0 标志、全文/哈希/annotation/file 摘要/quote 漂移拒绝；模型 key/source identity 不符拒绝；另一个合法候选的引用不能挂到当前事实行 |
| 坐标 | 支持、quote 内撤回、全文撤回均生成精确绝对区间；emoji 前缀与空格保留，区别 code point、UTF-8、UTF-16 |
| 歧义与原文 | 重复与重叠匹配拒绝；无匹配、组合字符归一化、空格删除和翻译后的文字拒绝 |
| 范围 | quote 外事实不能补支持或反驳；全文撤回可定位，但不能从其他评论借用，即使它实际存在于另一个候选 |
| 事实和状态 | 未知 fact/key、矩阵缺项/重复、缺少反驳依据、unknown 携带支持、重复引用均继续失败 |
| 关系 | same_review/before 的见证及 quote 引用保留；跨评论、未知/缺失见证、未知关系、before 无依据、引用不属于 witness 均拒绝 |
| 语义边界 | 新五案例按预置标准覆盖 supported、not_supported、quote refuted、context refuted、unknown；另行构造“未知对象硬判 supported”和“用产品编号当反驳”的反例，**定位/B2 可以通过，但独立预期不通过，semantic_verified 仍为 false** |
| 旧接口 | 旧版本、缺失 version、包含 start/end 或 publication_allowed 的新输出都拒绝；用另一条新合成输入构造的旧坐标错误回答，在转换前后仍被旧 B2 拒绝 |
| 原 Agent/Adapter | src/scripts 无新原型导入；默认关闭 Adapter 保持 disabled；活动 B1 不能直接消费新版本输出 |
| 副作用 | 原型不读写文件、不建 SDK/Worker；测试禁用真实网络、OpenAI 客户端、PostgreSQL、BGE 和 Popen，评估前后来源事件计数、输入与输出均不变 |

新专项最终 **24/24，0.529 秒**；原有 B1 设计与 B2 Adapter 回归 **42/42，6.838 秒**，包含正式策略/历史回答兼容、真实本地 Mock Worker 生命周期和关系检查。共 **66 项不同测试通过，无跳过**，不是模型准确率。

复核命令（仓库根目录）：

```powershell
$env:PYTHONPATH='src;tests;.'
.venv/Scripts/python.exe -B -m unittest test_shadow_text_evidence_prototype -v
.venv/Scripts/python.exe -B -m unittest test_p5b1_structured_evidence_design test_review_structured_shadow -q
git diff --check
```

## 隔离与保留范围

开始前在内存登记的 **375 个既有源码、入口、测试、夹具、Schema 和文档文件摘要，完成后 375/375 不变**，包括原定位原型、活动 B1/B2/Shadow Adapter、v2/v3 入口与结果报告。新测试还明确禁止读取 outputs 和 D/E 固定案例文件的情况下完成一次原型评估。

没有访问旧私有输出目录，因此没有对旧私有产物额外做逐字节复核；隔离证据是新原型没有文件/Provider/Worker 接口、运行路径没有导入它、评估测试禁止历史访问，以及既有源码/夹具/结果文档摘要不变。D74/E84 的原始无效状态、诊断标签和正式准确率 N/A 均未改写。没有运行两组历史实验或其入口测试。

仅新增四个文件：本报告、新实验 Schema、tests 下的原型和专项测试。Python 语法、JSON Schema 一致性及差异空白检查通过。没有前端或生产代码变更，无需前端构建或浏览器验收；没有新增审批或审计系统。

## 是否值得一次新的真实验证

**值得，限另行授权的新实验。** 本轮已证明程序可确定性承担唯一文本定位并保留原审计边界，尚未证明真实模型能稳定输出新契约、逐字复制充分证据、避免歧义或保持四状态语义质量。

未来应使用全新文本和独立版本提示/输出契约，调用前冻结语义与引用标准，预检新 Schema 的实际兼容性及完整请求预算。旧 v3 提示要求模型报坐标，不能直接沿用其请求体或兼容性结论。失败仍原样保留，不为了通过而改预期或追加调用；不得把新结果变成历史 D/E 的有效成绩。

本轮未准备新付费入口，也未接入正式 Agent、自动引用修复或正式许可。**离线原型完成，停止。**
