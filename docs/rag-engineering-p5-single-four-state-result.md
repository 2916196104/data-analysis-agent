# 单请求四状态合成验证：离线结果分析

2026-11-06。**用户执行的唯一请求已收到 HTTP 200；本次实际失败点是 D74 的 `context_veto_refs[0]` 绝对终点越界：返回 `[25,79)`，可信评论全文只有 62 个 Unicode code point。** 开发诊断成功保留了失败原文，能够定位候选、引用字段、区间和标签。另观察到 D73 将明确撤回误标为 not_supported。

整批 B2 结果为 output_invalid，正式可评分记录为 **0/5，准确率 N/A**。诊断中的五条标签仅作 `unvalidated_diagnostic_only` 分析，不计入正式验收。本轮只读分析并新增本报告，没有调用模型、重跑入口或修改代码、固定答案、权限及历史产物。

## 实际存在的产物

先枚举了[本次目录](../outputs/rag-semantic-retrieval/four-state-single-synthetic-v1)，再逐一读取下列实际文件。私有诊断受 owner-only 权限保护，沙箱直接读取被拒绝；随后以获准的只读权限读取，未调整 ACL。没有输出密钥、HTTP 凭据或未能对应可信合成评论的模型文本。

| 文件 | 存在及内容 |
|---|---|
| [execution-summary.json](../outputs/rag-semantic-retrieval/four-state-single-synthetic-v1/execution-summary.json) | 存在；stopped、shadow_validation_failed、model_calls=1、structure_valid=false、failure_diagnostic_saved=true |
| [shadow-execution.json](../outputs/rag-semantic-retrieval/four-state-single-synthetic-v1/shadow-execution.json) | 存在；output_invalid、citation_span_mismatch、一次尝试、Worker 正常退出并回收 |
| [provider-usage.json](../outputs/rag-semantic-retrieval/four-state-single-synthetic-v1/provider-usage.json) | 存在；HTTP 200、response_received、usage_known=true |
| [diagnostics/failure.json](../outputs/rag-semantic-retrieval/four-state-single-synthetic-v1/diagnostics/failure.json) | 存在，3746 字节；原文、首错详情及 unvalidated_diagnostic_only 标志齐全 |
| [model-input.json](../outputs/rag-semantic-retrieval/four-state-single-synthetic-v1/model-input.json) | 存在；5 个固定候选、quote、全文和必要来源字段 |
| [contract.json](../outputs/rag-semantic-retrieval/four-state-single-synthetic-v1/contract.json) | 存在；service_bad 单事实、all_of、无必要关系 |
| [retrieval.json](../outputs/rag-semantic-retrieval/four-state-single-synthetic-v1/retrieval.json) | 存在；5 条合成来源，与保存输入逐项一致 |
| [preflight.json](../outputs/rag-semantic-retrieval/four-state-single-synthetic-v1/preflight.json) | 存在；输入估算 7816、固定摘要、诊断路径及原限制 |
| shadow-judgments.json | **不存在**；B2 没有返回校验通过的 judgments，不应补建 |

原汇总的五条 model_status 均为 null，混淆矩阵全零，是入口在结构失败后保留 N/A 的结果；这不表示模型没有返回正文。其 preflight 中 spending_authorized=false 是执行前固定快照字段，实际 execution-summary 中 explicit_send_authorized=true、estimated_budget_risk_accepted=true。

## 失败阶段

以下结论来自本次文件及当前校验执行顺序，没有借用旧 Q1 的失败推断：

| 阶段 | 本次证据与结论 |
|---|---|
| Provider / SDK | HTTP 200、finish_reason=stop、response_received；没有已报告的 Provider 异常、超时、模型漂移或思考异常 |
| 严格 JSON / B1 Schema | 保存正文可解析；按 B2 到达引用检查的顺序，Schema 已通过。不能把整个 B2 失败称为 Schema 失败 |
| 请求身份 / 输入摘要 | 原文 request_id/input_sha256 与保存输入一致；后端封套摘要离线复算也与预检、usage、B2 回执一致 |
| 事实×候选矩阵 | 5 条 service_bad×候选记录完整，无重复或缺失；已通过矩阵检查 |
| 来源身份 | 两个实际引用（D71 支持、D74 全文否决）的 key/source_identity 均与可信候选匹配；其余三条没有引用 |
| 引用区间 | **D74 全文否决引用终点越界**，首错为 citation_span_mismatch |
| 事实许可 / 关系 | D71–D73 在首错之前完成现有单行检查；D74 余下许可条件、D75 的逐行审计和后续关系检查被提前中断。不能声称整批许可或关系通过 |
| Worker / 清理 | Worker exit_code=0，reaped=true、threads_released=true；不是 Worker 崩溃或清理失败导致本次停止 |

没有必要关系，原文 relation_judgments 为空；本次不能测量复杂关系质量。失败关闭后 publication_allowed=false、semantic_verified=false、permitted_target_fact_ids=[]，没有有效许可或模型支持集合。

## 首个错误的精确定位

私有诊断与父进程回执的错误码、正文摘要一致：

| 字段 | 实际值 |
|---|---|
| error_codes | `["citation_span_mismatch"]` |
| section / judgment_index | fact_judgments / **3**（零基索引，第 4 条） |
| candidate_key | `rag-opinion-v1:e66d4beea6b0dde86076ecae8d0ca8a8598eee635e496b8348b6196b34f2229a` |
| 对应来源 | **D74**，rank=4，review_id=74，annotation_id=174 |
| reference_type / reference_index | **context_veto_refs / 0** |
| start / end | **25 / 79** |
| allowed_start / allowed_end | **0 / 62**，该字段允许同评论全文范围 |
| quote_start / quote_end | 12 / 25 |
| bounds_valid | **false** |
| text_matches | **true** |
| matches_if_quote_relative | **false** |
| coordinate_unit / review_code_points | unicode_code_point / 62 |
| provider_failed | false |
| raw_bytes / redaction_applied | **2632 / false** |
| evidence_status | unvalidated_diagnostic_only |

保留原文实际为 2632 UTF-8 字节，其 SHA-256 与诊断和 Worker 回执都一致：`1f17088c25bfe624dec196634c4bf82bfd7b866b579b205c25f27714990b9c08`。这是本次真实保存的正文，不是恢复旧 Q1 或重建模型回答。

D74 的可信评论为：

> 虚构样机星芒-Y7初记：客服接电话时对我吼了几句。补充核实：当时接听的是安装预约员，我把角色弄错了；上面关于客服的抱怨撤销。

实际引用文字是后半段“补充核实：当时接听的是安装预约员，我把角色弄错了；上面关于客服的抱怨撤销。”，与可信全文逐字一致，长度 **37 code point**，位于 **[25,62)**。模型给出的区间长度为 79−25=54，终点多出 **17 code point**。

关键区别是：Python `review_text[25:79]` 会把超出长度的终点截到文本末尾，因此它与 `review_text[25:62]` 都返回同一撤回段。**text_matches=true 不代表边界合法。** 原校验还要求 `0 <= start < end <= len(review_text)`，所以正确拒绝了该引用。

对坐标解释逐项比对：

- **可确定**：起点与可信绝对起点相同，绝对终点越过全文上界；属于区间越界，不是引用文字改写，也不是把 quote 外文字放进 support_refs。
- **不符合统一的 quote 相对偏移解释**：模型 `[25,79)` 作为 quote 相对位置不能得到该撤回段；实际字段也正确使用了全文否决类型，而非 quote 内支持/反驳类型。
- **不符合 UTF-8 字节坐标**：可信撤回段的字节区间为 `[69,180)`，不是 `[25,79)`。
- 此条文本全部处于 BMP，UTF-16 单元区间与 code point 区间同为 `[25,62)`；不能用代理对差异解释 79。没有证据说明模型为何生成这个数值，不进一步猜测内部计数或推理过程。

本次原文只有两个引用：D71 的 support_refs `[12,37)` 合法；D74 的上述 context_veto_refs 非法。没有其他 refutation_refs；D73 的问题是标签漏判，不是一个已观察到的反驳引用偏移错误。

## 五条语义观察：仅诊断，不计正式成绩

下表实际状态均直接读取自失败原文。**整批仍为 output_invalid，五条正式语义评分全部 N/A**；单条局部引用合法也不能越过整批失败关闭。

| 案例 | 固定预期 | 实际可观察状态 | 结构有效性与引用有效性 | 诊断性语义阅读 / 尚未确认风险 |
|---|---|---|---|---|
| D71 | supported | supported | B1 可解析，首错前单行通过；support_refs `[12,37)` 与 quote、身份及全文完全匹配 | 本人报告客服不耐烦和打断，包装信息没有撤回该事实；局部支持成立，仍不能单独发布 |
| D72 | not_supported | not_supported | 首错前单行通过；无支持/反驳/否决引用 | 正面客服体验不支持负向目标，未再误标为 refuted；仅此一条观察不能证明普遍修复 |
| D73 | **refuted** | **not_supported** | 首错前单行结构可接受；无引用，因此没有明确否认/撤回的反驳依据被输出 | **反驳标签漏判**：quote 明确说本人昨天的差评不实并撤回，符合固定 refuted 定义。拒绝提供支持不等于四状态分类正确；内部原因不能从标签推定 |
| D74 | refuted | refuted | 本行 context_veto_refs `[25,79)` 越界，导致整批失败；全文文字及身份本身相符 | “安装预约员认作客服”及“抱怨撤销”确实构成纠正撤回，状态方向符合预期，但数值引用不合格，必须保持无效 |
| D75 | unknown | unknown | B1 可解析；因 D74 首错，完整逐行审计未继续；无引用 | 全文仍不能确定接听人是客服或安装安排人员，保留角色歧义合理；并未验证模糊否定范围能力 |

**正式统计**：完整结构通过 0/1 批；有效语义记录 0/5；正式准确率 N/A。原汇总的空许可、null 比较及零矩阵均保留原样。

**unvalidated_diagnostic_only 标签对照**：4 条与预期一致，D73 一条不一致；以下只是诊断矩阵，不是正式准确率或可用证据统计：

| 固定预期＼观察标签 | supported | refuted | not_supported | unknown |
|---|---:|---:|---:|---:|
| supported | 1 | 0 | 0 | 0 |
| refuted | 0 | 1 | 1 | 0 |
| not_supported | 0 | 0 | 1 | 0 |
| unknown | 0 | 0 | 0 | 1 |

诊断未见错误的正向支持或将相反体验标为 refuted，观察到了 D73 的反驳标签漏判及 D74 的数值引用失败。语义依据来自固定文本与规则的独立阅读，没有用模型自己的理由证明判断正确；这也不是新增独立人工金标准。

## Token、费用、耗时和保护

| 项目 | 本次记录 |
|---|---|
| 请求数 / HTTP / 结束原因 | 1 / 200 / stop |
| 请求及响应模型 | deepseek-ai/DeepSeek-V4.1-Flash；服务别名不是不可变模型快照 |
| 提示 | p5-shadow-siliconflow-instructions-v2；提示摘要与预期一致 |
| 输入估算 / 实际输入 | 7816 / **3202 token** |
| 输出 / 思考 / 缓存命中 | **923 / 0 / 0 token**；无 reasoning_content |
| 总 token | **4125** |
| 请求准备 / Provider 响应验证 | 均记录为 0.000s，属于计时分辨率内，不是证明没有执行 |
| SDK / Provider 总耗时 | **7.875s / 7.875s** |
| Shadow 总耗时 | **8.875s**，已包含 Provider 阶段，不能重复相加 |
| usage 忙时费用估算 | **¥0.013788** |
| 费用回执是否缺失 | usage 和费用估算字段均存在；独立供应商账单未查询，实际扣费仍未核对 |
| Worker | PID 3620、exit_code=0、reaped=true、threads_released=true、termination_requested=false |

记录时间 UTC 2026-11-06 14:50:25.063–14:50:32.938，即 Asia/Taipei **22:50:25–22:50:32**。未见本次超时、截断、token 超限或重试证据。

按运行记录采用的项目固定忙时输入 ¥2、输出 ¥8／百万、无缓存折扣计算：`(3202×2 + 923×8)/1000000 = ¥0.013788`，与保存费用字段完全一致。没有将账单未知项记为 0，也没有用此单样本调整 tokenizer 估算。

离线只读复核确认：输入后端封套摘要、预检、usage、B2 回执一致；固定夹具 SHA-256 仍为 `89dd2aef2d2ca5f5d1ddcb65d0c1d70b00943d8b00aa83cb6abbcde065b46fde`；保存的五条 quote、全文、全文哈希和合成来源逐项吻合，没有把预期字段放入模型投影。Worker PID 3620 的当前只读查询结果为不存在，线程回收结论来自执行回执。

保存来源为 SYNTHETIC / SYNTHETIC-P1，入口使用合成 FakeRepository/FakeEncoder；没有正式 Agent 发布、v2 assessment 更新或许可变更的证据。依据限于入口和这些回执，不把它扩展为对整个运行环境、其他进程或数据库历史的审计结论。本轮未连接业务系统，也未运行测试或真实入口；仅新增本报告，既有代码及产物保持原样。

## 诊断能力与最小后续建议

**新诊断解决了此前 Q1 的正常结构失败路径可观测性缺口**：本次既保留了完整失败正文，又定位到 D74/context_veto_refs[0]/end=79，并看到了独立的 D73 标签问题。文件与原文均在既定大小上限内，未发生脱敏改写，正文摘要与 Worker 回执一致；没有因为保存诊断而放行无效输出。它不能补回旧 Q1，亦不能保证强制终止或磁盘故障时留存成功。

建议的后续修复对象有两项，**本轮均不实施**：

1. **引用生成可靠性**：先以这份只读原件建立离线回归，明确 `[25,79)` 即使切片文字相同也必须拒绝。后续优先评估避免模型手工计算 end、由受信代码对唯一原文片段计算并严格验证坐标的生成方案；不删除边界检查、不直接截断 end、不修正当前历史输出为 validated_shadow。具体变更范围需另行确定。
2. **明确撤回的四状态区分**：单独保留 D73“本人原差评不实并撤回”的固定预期，针对 not_supported/refuted 混淆做离线验证。不能为了消除本次不匹配而改预期、重写标签或重试模型。

目前仍不能声称四状态语义质量通过、正式准确率为某个百分比、复杂关系或转述/假设/模糊否定均已验证，也不能据此启用正式 LLM 许可或自动扩大真实评论实验。

**本次唯一机会已消耗；离线分析完成并停止。**
