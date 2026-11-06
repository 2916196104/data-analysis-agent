# 引用定位与 refuted 漏判：离线修复方案及冻结回归

2026-11-06。已完成精确片段定位的最小离线原型、独立提示修订候选及真实失败原文的冻结回归，**136/136 项离线测试通过，无跳过**。没有真实 SiliconFlow、数据库或 Embedding 调用，没有重跑实验或改写历史结果。

自动重建错误坐标会改变当前信任边界，故本轮**没有接入运行路径**。活动提示仍为 v2；v3 只是离线候选。以下结果证明规则、提案和现有校验的兼容性，不代表真实模型已修复。

## 可确认原因

依据[本次真实结果](rag-engineering-p5-single-four-state-result.md)和保存原文：

| 问题 | 能确认的原因 | 不能确认的内容 |
|---|---|---|
| D74 | 模型提交的 context_veto_refs 区间为 `[25,79)`，引用文字与全文后缀一致，但全文只有 62 code point；正确片段在 `[25,62)`。原校验正确拒绝越界 | 不符合统一 quote 相对坐标、UTF-8 字节或 UTF-16 单元解释；无法证明模型内部为何多算 17 个字符 |
| D73 | quote 明确否认并撤回作者自己的原客服差评，模型仍返回 not_supported，且没有 refutation_refs；属于固定四状态标准下的 refuted 漏判 | 提示中的定义竞争可能促成漏判，但不能证明它是唯一原因，或仅改措辞就能修复 |

提示 v2 的 not_supported 写为“没有直接支持”，refuted 写为“当前候选相关主张明确被否认、撤回或纠正”。撤回后确实不再支持目标，但**明确撤回应优先归入 refuted**，而不是停留在一般的“不支持”。v2 已有正确区分和引用约束，缺少明确的检查优先级，真实模型也没有稳定遵守现有定义。

## 精确片段定位的选择与接入影响

最小算法：在可信请求里按候选 key 找唯一候选，核对 source_identity；只在该候选的允许范围内做逐字匹配，计算 `end=start+len(text)`，不规范化、不模糊匹配、不跨评论借用文字。

- support_refs / refutation_refs 只搜索该候选 quote 的全文绝对范围；context_veto_refs 只搜索其同一 review 全文。
- 核验全文哈希、quote 区间与 quote 原文一致；匹配结果携带候选 key、review_id、source identity 和引用类型。
- 单一位置返回唯一提案；两个及以上位置（含重叠匹配）返回 ambiguous，不挑第一个、最近一个或借用错误的旧坐标消歧。
- 未找到、空片段、来源不符、源文本漂移或未知引用类型都不生成唯一提案；不在其他候选中补搜。
- 文本唯一匹配只证明文字位置，不能证明支持/反驳语义，也不能建立 P0 来源信任。冻结 JSON 封套只用于离线重放，不恢复当前数据库来源证明。

实现位于 [tests/shadow_reference_locator_prototype.py](../tests/shadow_reference_locator_prototype.py)，仅供离线验证。所有返回值均标记 unvalidated_diagnostic_only、publication_allowed=false、semantic_verified=false；不返回修复后的模型回答，不调用 audit_output 自动晋级，也没有被 Judge、Worker、B2、正式 Agent 或实验入口导入。

**接入影响须先确认**：

| 接法 | 影响与本轮决定 |
|---|---|
| 保留 B1，但忽略/替换模型的错误 start/end 后再审计 | Schema 可以不变，但把“必须提交合法区间”变成“后端允许修复区间”，改变证据接受条件和信任边界；**未实施** |
| 让模型只选原文或片段 ID，后端生成 Span | 当前 Span 必须有 start/end；省略字段或引入新片段 ID 需要修改 B1 或新增输出转换契约；**未实施** |
| 只作离线定位提案；原始 Span 继续按现有 B1/B2 验证 | 不改变运行规则，可以研究唯一定位/歧义拒绝并保留原失败；**本轮采用** |

即便提案唯一，也不能直接将本次 D74 改成有效证据。测试证明：定位提案为 `[25,62)` 后，原始回答仍是 `[25,79)`，原审计仍返回 citation_span_mismatch。没有截断 end 或放宽边界验证。

## refuted 的最小提示修订候选

[shadow_siliconflow_prompt_v3_candidate.txt](../tests/fixtures/shadow_siliconflow_prompt_v3_candidate.txt) 独立保存完整候选提示，未覆盖活动 v2 或其版本摘要。主要澄清：

1. 先判断说话者、对象、否定范围和时间能否确定；无法确定仍为 unknown。
2. 在判断“缺少直接支持”前，检查**该候选自己的相关主张**是否明确被否认、撤回或纠正。quote 内撤回用 refutation_refs，同评论全文撤回用 context_veto_refs。
3. 本人明确说此前评价不实并撤回，即使该 quote 现在不支持原目标，也应为 refuted，而非 not_supported。
4. 单纯相反体验不构成撤回，不同买家不能相互撤销；后续改善也不抹去没有撤回的历史负面体验。
5. 引用保持全文绝对 Unicode code point 区间，要求 `end=start+code_point_length(text)` 且不越过全文长度；不能提供可靠来源绑定区间时返回现有 execution_status=failed 形式，不编造数字或反驳依据。

候选保留固定目标、原文不可信、全文只解释/否决、quote 内支持、角色区分、关系证据和不可发布约束。它不依靠 D73/D74 的具体文本或答案编号，也不由待测试模型生成。

候选提示为 **3152 UTF-8 字节**，文本摘要 `c809025aeac408ded54f87144601526fb280712597ea8262d4ed128f2fca3e8e`。只在内存替换系统消息、沿用其余固定五条请求时，当前近似输入估算为 **7987**，仍保留 8000 限制，仅有 13 的估算余量；这不是准确 token 计数或未来请求预算保证。没有因此提高预算、缩减候选、截断全文或删除来源。

活动 v2 仍为 `p5-shadow-siliconflow-instructions-v2`，摘要仍是 `6d0e268c77b3dfc77956ec31b154ff5f04a84db34d15271f40c1ee93c85c130d`。未来若选用候选，必须作为新版本及新实验重新绑定，不能修改旧入口的历史提示标识或原运行记录。

## 冻结回归与验证

新增 [冻结清单](../tests/fixtures/shadow_single_failure_regression_v1.json) 只提交摘要、原始/预期标签和区间，不提交私有原文。测试只读原有 `outputs/rag-semantic-retrieval/four-state-single-synthetic-v1/diagnostics/failure.json`，同时校验：

- 诊断文件 SHA-256：`3293f5e4c1426d26f5273645ca72042c081d58e4b2cc6910014801e3b40ed282`。
- 原文 2632 字节、SHA-256 `1f17088c25bfe624dec196634c4bf82bfd7b866b579b205c25f27714990b9c08`，redaction_applied=false。
- 输入摘要 `600d1797517befb5cebb86ab3d67792c850d2fb614eff9cfe97a63a491d2fab9`。
- 原始标签 `[supported, not_supported, not_supported, refuted, unknown]` 与固定预期 `[supported, not_supported, refuted, refuted, unknown]` 分开冻结，不修改任意一方。

[新增专项](../tests/test_shadow_reference_semantic_regression.py) 共 8 项：4 项定位/规则/候选提示检查，4 项私有真实原文冻结重放。私有原文不在 Git 中；没有访问权限的环境会明确跳过冻结类，本轮获准只读访问后实际执行，**无跳过**。

| 验证目标 | 本轮证据 |
|---|---|
| D74 原始非法区间继续拒绝 | 对原始文本审计仍为 output_invalid / citation_span_mismatch，bounds_valid=false、text_matches=true |
| 合法、来源一致的引用可通过 | 另行编写的 Mock 回答使用原预期和合法引用，B1/B2 结构通过；不是修复后的历史结果，也不代表真实语义正确 |
| 无可靠引用继续失败关闭 | 缺少撤回引用、身份不符、非法区间均拒绝；重复文本、缺失文本和错误角色搜索范围不产生唯一定位 |
| D73/D74 反驳标准清晰 | D73 明确撤回需 quote 内反驳引用，D74 需同评论全文否决；拿掉依据即 refutation_without_basis |
| 相反体验/不同买家/后续改善 | 由独立编写的期望检查 not_supported、跨买家支持不被撤销、历史体验未撤回仍可支持；Mock 不推理标签 |
| 候选提示与接口兼容 | 实际 SDK 的 Mock HTTP 验证请求 Schema 不变、候选系统消息正确传入、合法作者给定回复结构通过；没有真实模型调用 |
| 诊断、超时、取消和正式隔离 | 原有适用回归继续通过，包含 Worker 清理、失败写盘、private ACL、预算、关系约束、正式 Agent/v2 与历史行为隔离 |

组合回归 **136/136 通过，81.588 秒**：原有 128 项加新增 8 项。父/子进程的既有 Mock 夹具继续屏蔽真实 HTTP、PostgreSQL 和本地 BGE；本轮没有运行任何真实实验入口。

```powershell
$env:PYTHONPATH='src;tests;.'
.venv/Scripts/python.exe -B -m unittest test_shadow_reference_semantic_regression test_rag_shadow_single_entry test_shadow_failure_diagnostics test_rag_shadow_quality_entry test_shadow_quality_plan test_shadow_semantic_boundaries test_review_shadow_siliconflow test_siliconflow_schema_probe test_review_shadow_openai test_p5b1_structured_evidence_design test_review_structured_shadow -q
git diff --check
```

## 文件与保护范围

本轮只新增本报告、定位测试原型、候选提示、冻结清单和回归测试。没有改运行源码、活动 v2、B1 Schema、B2、诊断模块、正式 Agent、reported_experience_v2、人工参考或已结束实验入口。原始固定案例保持不变；冻结原文仍在原私有目录，未删除、覆盖、复制到 Git 或生成新的许可。

完成后核对本轮登记的 151 项既有源码、Schema、旧入口、固定夹具及两份相关历史实验目录/报告摘要，**151/151 均保持一致**。没有扩展为新的历史审计体系。

## 后续是否需要真实验证

**有必要做一次另行授权、独立且小规模的验证，但本轮不执行或准备自动运行。** 它应使用新的固定案例来测量候选提示的撤回优先级和坐标可靠性，保留全字段来源、原边界、预算、诊断及一次性限制。D73/D74 已是开发案例，不能把其重放成绩当成独立泛化成绩。

仍待真实验证：候选提示是否减少 refuted 漏判、是否影响相反体验和 unknown 边界、是否能稳定给出合法全文否决区间，以及新提示的实际 token/延迟。精确文本唯一定位仍不能证明语义；自动坐标重建的运行时接入则需先确认上述 Schema/信任边界影响，不在本轮授权范围内。

**离线工作已完成并停止。没有宣称真实模型已修复，也没有启用任何正式 LLM 判断许可。**
