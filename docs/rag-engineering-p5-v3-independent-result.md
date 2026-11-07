# v3 独立验证结果：撤回判别符合预期，全文引用仍失败

2026-11-07。**已有的唯一真实调用收到 HTTP 200，但 E84 的 `context_veto_refs[0]` 起点错误，B2 返回 `output_invalid / citation_span_mismatch`。** 诊断中的五条标签均符合冻结预期；它们仍是 `unvalidated_diagnostic_only`，正式可评分记录为 **0/5，准确率 N/A**。`publication_allowed=false`、`semantic_verified=false`，许可集合为空。

## 执行记录与本轮操作

用户授权后，本轮先运行原入口的离线 preflight，发现 `experiment_already_claimed`。只读查看既有私有目录，确认已存在完整的一次执行回执及失败诊断，记录时间为 Asia/Taipei **2026-11-07 00:01:04.073–00:01:13.442**，`model_calls=1`、`attempts=1`、`explicit_send_authorized=true`、`estimated_budget_risk_accepted=true`。

因此本轮没有再执行 run 命令或发起模型请求，直接分析这一份已有记录；没有将再次授权解释为补跑机会。文件本身不能证明该次记录的人工触发渠道或执行者，本报告只陈述回执可核实的执行事实。

[私有实验目录](../outputs/rag-semantic-retrieval/v3-independent-synthetic-v1/) 中实际存在：

| 产物 | 关键证据 |
|---|---|
| [execution-summary.json](../outputs/rag-semantic-retrieval/v3-independent-synthetic-v1/execution-summary.json) | stopped、shadow_validation_failed、structure_valid=false、validated_label_count=0、label_match_count=null、formal_accuracy=null |
| [provider-usage.json](../outputs/rag-semantic-retrieval/v3-independent-synthetic-v1/provider-usage.json) | 一次请求、HTTP 200、response_received、finish_reason=stop、usage_known=true |
| [shadow-execution.json](../outputs/rag-semantic-retrieval/v3-independent-synthetic-v1/shadow-execution.json) | citation_span_mismatch、Worker 正常退出且回收、诊断保存成功 |
| [diagnostics/failure.json](../outputs/rag-semantic-retrieval/v3-independent-synthetic-v1/diagnostics/failure.json) | 原始模型正文和首错详情，unvalidated_diagnostic_only；未脱敏改写 |
| preflight.json / model-input.json / contract.json / retrieval.json / frozen-expectations.json | 五条冻结输入、完整来源、原契约与调用前预期快照 |
| shadow-judgments.json | **不存在**，B2 没有产出整批有效 judgments |

目录受 owner-only 权限保护；沙箱直接读取被拒绝后，获准只读查看，未修改 ACL。保存 preflight 的 `spending_authorized=false` 是调用前快照，不覆盖 execution-summary 中明确的本次发送及费用风险接受标志。

## 结构验收

对保存输入恢复后端封套，仅在内存调用原 `parse` 和 `audit_output` 复核，未调用模型、数据库或 Embedding。原始正文可通过 B1 Schema，request_id/input_sha256、事实×候选五行矩阵均匹配。三个实际引用均绑定正确候选及 source identity。

| 案例 / 引用 | 模型提交区间 | 原文核验 |
|---|---|---|
| E81 / support_refs[0] | `[10,29)` | quote 内，来源和切片完全一致，19 code point |
| E83 / refutation_refs[0] | `[12,34)` | quote 内，来源和切片完全一致，22 code point，包含实际否认与撤回 |
| E84 / context_veto_refs[0] | **`[20,48)`** | 来源正确、边界合法，但切片文字不一致；引用文本为 29 code point，区间只有 28 |

E84 的实际全文为：

> 虚构设备青禾-Z3：客服当面嘲讽了我。核实后更正：那人是配送员，原先针对客服的指责不实，我撤回。

模型的 `text` 为“核实后更正：那人是配送员，原先针对客服的指责不实，我撤回。”；该片段在全文的真实位置为 **`[19,48)`**。提交的 `[20,48)` 实际切出“实后更正：那人是配送员，原先针对客服的指责不实，我撤回。”，漏掉首字“核”。

首错定位：`fact_judgments[3].context_veto_refs[0]`，review_id=84，quote `[10,19)`，全文 48 code point；`bounds_valid=true`、`text_matches=false`、`matches_if_quote_relative=false`。这次是起点多算 1 导致切片不匹配，与旧 D74 的终点越界不同。不能从输出证明模型内部为何算错。

没有把起点改成 19、重建引用或重新审计修正回答来晋级。原始正文离线重放仍失败。B2 在 E84 引用处提前停止，不能声称 E84 剩余许可检查、E85 逐行审计及后续关系检查在该次完整审计中已通过。本组没有必要关系，也不检验复杂关系质量。

## 按冻结标准阅读语义

以下仅为失败诊断的独立阅读，不是正式准确率或有效许可；没有用模型自己的 rationale 证明结论。

| 案例 | 冻结预期 | 原始诊断标签 | 独立阅读结果 |
|---|---|---|---|
| E81 | supported | supported | quote 直接报告本人被客服讽刺；支持引用正确，无借用全文补充事实 |
| E82 | not_supported | not_supported | 正面体验没有撤回既有差评；三个引用数组为空，没有误当反驳 |
| E83 | refuted | refuted | quote 明确说先前评价不属实并撤销；refutation_refs 正确引用 `[12,34)`，本条未复现明确撤回漏判 |
| E84 | refuted | refuted | 选取的文字确为同评论对象更正与撤回，字段选用 context_veto_refs 正确；**绝对区间错误，精确引用验收失败** |
| E85 | unknown | unknown | 全文仍无法确定工作人员身份；三个引用数组为空，保留对象歧义 |

本次在诊断层观察到正确区分相反体验、明确撤回和未知对象；quote 内撤回引用正确，全文撤回引用仍不可靠。因为整批结构失败，这些标签不计入正式混淆矩阵或准确率，原汇总的零矩阵、null 标签比较与 0 条有效记录保持原样。

这五条是新文本，但并非独立人工盲评金标准；没有同集 v2 对照调用，不能量化改善幅度，也不能声称 v3 整体通过或已解决引用生成问题。

## 输入、预算与运行保护

只读核验结果：保存的模型投影与冻结案例重新准备的投影完全相同，quote、完整评论、全文摘要和全部来源字段逐项匹配；保存 retrieval 与本地合成来源重建一致，预期快照与冻结夹具一致。发送投影未包含 expected_status、expected_reason、reference_standard、reference_policy、target_fact、veto_text 或 provenance。它证明本组合成输入一致，不是业务数据库的新来源证明。

- 提示版本：`p5-shadow-siliconflow-instructions-v3-independent-v1`。
- 提示摘要：`c809025aeac408ded54f87144601526fb280712597ea8262d4ed128f2fca3e8e`。
- 冻结夹具摘要：`0645417e9ed9eec5ee8834cb271dfd987cc01945ed018039a57c2fa630bf1cac`。
- 后端输入摘要：`8c8f6a96bc02129ebc97393346b5902b1aeac5dfe2adfc48494c7bd40b2a280e`，与 preflight、usage、诊断和 Worker 回执一致。
- 请求体离线复算摘要：`b3ed3547bebba4513bae8f8efbc7b4c93ce1887a6203b1ed717cf08e9fb66bee`，与调用前预检一致。

| 项目 | 本次回执 |
|---|---|
| 模型 / HTTP / 结束原因 | deepseek-ai/DeepSeek-V4.1-Flash / 200 / stop |
| 输入估算 / 实际输入 | 7859 / **3210 token**，上限 8000 |
| 输出 / 思考 / 缓存 | **996 / 0 / 0 token**，输出参数 2500，无 reasoning_content |
| 总 token | **4206** |
| SDK / Provider / Shadow 总耗时 | **9.359 / 9.375 / 10.469 秒**；各层包含关系，不相加 |
| 项目固定忙时费率费用估算 | **¥0.014388**，`(3210×2+996×8)/1000000`，与 usage 一致 |
| 独立供应商账单 | 未查询，实际扣费仍未知 |
| Worker | exit_code=0、reaped=true、threads_released=true、termination_requested=false |
| 发布 / 语义验证 | false / false；permitted_target_fact_ids=[] |

未见此次请求超时、输出截断、预算超限、模型身份冲突或非零思考 token 的回执。请求与响应均为服务别名，不证明模型快照不可变。SDK 重试配置仍为 0，记录 attempts/model_calls 均为 1；本轮没有追加请求。

诊断原文 **2678 字节**，SHA-256 `cf9b92180f03890a52a3eb50d9a1f4ffdd5f2e178daeb583de4c036995e2c293`，与 Worker output_sha256 一致。failure.json 文件 SHA-256 为 `317ee110cd0587956dd3dd4ef0660ba2a125e7338cd3993be29999112dcfabeb`；execution-summary.json 为 `889335bd78d67629c84df4e8c24536f6c83f8aa0d8a036ff0748deceb06bdf36`。文件位于既有大小限制内，redaction_applied=false；原件仍留在私有目录。

## 本轮验证范围与停止状态

本轮只读离线检查了 B1 解析、原始 B2 失败重放、固定摘要、模型投影无预期字段、来源/全文一致性、三个引用、保存原文哈希、单次请求回执及费用复算；分析代码显式屏蔽真实网络、SDK 客户端、PostgreSQL 和 BGE。没有重跑准备阶段的 148 项测试，也没有把其历史通过记录当成真实语义成绩。

仅新增本结果报告，既有源码、提示、Schema、冻结预期、准备报告及实验产物保持原样。唯一机会已有执行记录且已消耗；结果按原规则失败关闭，分析完成后停止。
