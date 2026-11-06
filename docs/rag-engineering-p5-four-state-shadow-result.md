# P5 四状态真实 Shadow 结果

2026-11-06。**固定流程执行一次，Q1 发起 1 次请求并收到 HTTP 200，但 B2 因 `citation_span_mismatch` 拒绝输出；Q2/Q3 未执行。** 流程按约定异常停止，退出码 1。没有重试、补跑、换模型、删除占用目录或扩大样本。

本次确认了引用结构失败，**没有取得可评分的逐候选模型判断，无法测得四状态语义准确率**。不能将未评分记为全部错误、全部正确或 unknown。

## 执行与证据

已有安全预检返回 `ready_pending_explicit_authorization`、`missing=[]`；三个输入估算仍为 7695、7631、7750。该预检的 `spending_authorized=false` 是入口固定的预检字段，实际 run 根据用户本轮明确授权传入两项授权参数；未改写预检记录。

按用户指定仅运行一次：

```powershell
.venv/Scripts/python.exe -B scripts/run_rag_shadow_quality.py run --authorize-fixed-synthetic-shadow --accept-estimated-token-risk
```

保存目录：[four-state-synthetic-shadow-v1](../outputs/rag-semantic-retrieval/four-state-synthetic-shadow-v1)。主要依据为 [执行汇总](../outputs/rag-semantic-retrieval/four-state-synthetic-shadow-v1/execution-summary.json)、[Q1 B2 回执](../outputs/rag-semantic-retrieval/four-state-synthetic-shadow-v1/Q1/shadow-execution.json)、[Q1 usage](../outputs/rag-semantic-retrieval/four-state-synthetic-shadow-v1/Q1/provider-usage.json) 和 [Q1 本地比较](../outputs/rag-semantic-retrieval/four-state-synthetic-shadow-v1/Q1/comparison.json)。所有原始文件保持原样。

实际只发送 C01–C05 的 5 条虚构评论及必要 B1 字段。没有连接 PostgreSQL、运行 BGE-M3、读取或发送真实商品评论。Q2/Q3 在本地准备过合成输入，但未发送且没有批次结果目录。

## 结构正确与语义正确分开统计

| 检查项 | 结果与依据 |
|---|---|
| HTTP/API | 1/1 请求返回 HTTP 200、finish_reason=stop；无超时、无重试 |
| 输出 JSON / B1 Schema | 按现有校验执行顺序，可确认 Q1 已通过解析，随后才进入引用校验；原输出未保存，不能再次独立重放 |
| 请求绑定 / 事实×候选矩阵 | 同一顺序表明 Q1 已通过输入摘要绑定及完整 5 对事实×候选检查；不代表标签语义正确 |
| 完整 B2 输出校验 | **0/1 批通过**，失败码 `citation_span_mismatch` |
| 精确引用 | 至少一处区间越界或原文切片与引用文字不一致；具体候选、引用类别和正确数量未知 |
| 来源身份 | 运行前合成来源经原 P0/B1 输入检查；到达失败引用时该引用身份已通过，但不能声称全部输出引用身份均通过 |
| 关系 | 三批输入无必要关系；Q1 在引用失败处提前返回，后续关系检查未完成；复杂关系语义质量 N/A |
| 语义可评分覆盖 | **0/15**；Q1 的 5 条因无效输出未留存而不可评分，Q2/Q3 的 10 条未执行 |
| 标签匹配数量 / 比例 | 已确认匹配 0 条、已确认不匹配 0 条；可评分分母为 0，匹配率 **N/A** |
| 正式发布 | publication_allowed=false、semantic_verified=false、permitted_target_fact_ids=[] |

`check_span` 对 quote 内支持/反驳使用 quote 边界，对全文否决使用同评论全文边界，并检查 `[start,end)` 原文切片等于引用文字。当前错误码无法区分越界、偏移错误、文字改写等具体原因，更不能证明是哪一种语义误判。

### 无效输出保存缺口

[B2 适配器](../src/data_analysis_agent/agents/review_shadow_adapter.py) 仅在 `validated_shadow` 时返回 `judgments`；本次返回 `None`。固定入口因此没有生成 `Q1/shadow-judgments.json`，provider 只保存 usage，不保存模型内容。

Q1 回执保留了 **2771 输出字节**和 SHA-256 `b84e1641ddd207291073a570f523ce6bba557fecfd410c40c701a9f5b9ba4327`，但摘要不能还原标签、理由或引用。`comparison.json` 中的 null 表示没有可评分输出，不是供应商没有返回内容。先前入口只保证“B2 返回的判断可保存”，没有保证无效原文留存；这个缺口在本次真实结构失败时暴露。

因此无法按要求恢复 Q1 的五个实际标签或逐个引用错误。本报告明确保留这一限制，没有猜测、修改保存记录或通过补跑掩盖。

## 15 条案例与待人工确认点

下表预期来自未修改的 [固定夹具](../tests/fixtures/shadow_quality_cases_v1.json)。复核点是固定验收标准，**不是对模型已输出内容的评价**。

| 案例 | 预期 | 模型实际状态 | 逐条仍需确认的语义点 |
|---|---|---|---|
| C01 | supported | N/A：Q1 无效输出未留存 | 本人客服差评应由 quote 支持；包装好评不撤回投诉 |
| C02 | not_supported | N/A：Q1 无效输出未留存 | 相反好评不应误标 refuted；无法判断此次是否复发 |
| C03 | refuted | N/A：Q1 无效输出未留存 | 必须引用本人明确否认此前差评并撤回的文字 |
| C04 | refuted | N/A：Q1 无效输出未留存 | 全文明确纠正客服/配送员对象并作废评价，应以 context_veto_refs 否决 |
| C05 | not_supported | N/A：Q1 无效输出未留存 | 朋友转述不应当作作者本人体验 |
| C06 | not_supported | N/A：Q2 未执行 | 尚未发生的条件假设不应当亲历 |
| C07 | not_supported | N/A：Q2 未执行 | 安装人员不能合并为客服 |
| C08 | not_supported | N/A：Q2 未执行 | 配送人员不能合并为客服 |
| C09 | unknown | N/A：Q2 未执行 | 未完成的否定表达不得强行补全作用域；此固定预期本身仍待独立人工确认 |
| C10 | unknown | N/A：Q2 未执行 | 工作人员身份不确定，应保留角色歧义 |
| C11 | not_supported | N/A：Q3 未执行 | “没有批评我的态度”不等于评价客服态度，也不是必然 unknown |
| C12 | supported | N/A：Q3 未执行 | 客服差评与配送好评可共存，不借用全文新增支持 |
| C13 | not_supported | N/A：Q3 未执行 | quote 内第一人称实际来自朋友，须结合全文解释说话者 |
| C14 | not_supported | N/A：Q3 未执行 | 与 C12 相同 quote 被全文明确限定为假设 |
| C15 | supported | N/A：Q3 未执行 | 后来改善不撤销昨天确实发生的体验 |

没有可定位到具体案例的模型标签错误；已确认错误仅能定位为 **Q1 批次引用一致性失败**。false_support、false_rejection、refutation_label_error、unknown_misclassification 等语义错误数全部为 **N/A**，不能因保存的 error_types 为空就写成“错误为零”。

### 四状态混淆矩阵

行是固定预期、列是可评分的实际状态；沿用原汇总的零计数，额外列出未评分数量以避免误读。

| 预期＼实际 | supported | refuted | not_supported | unknown | 未评分 |
|---|---:|---:|---:|---:|---:|
| supported | 0 | 0 | 0 | 0 | 3 |
| refuted | 0 | 0 | 0 | 0 | 2 |
| not_supported | 0 | 0 | 0 | 0 | 8 |
| unknown | 0 | 0 | 0 | 0 | 2 |

不存在“15 条全错”或“0% 语义准确率”的结论；这是 **0 条可评分记录**的矩阵。

### supported / refuted 的文本依据与引用边界

以下仅核对夹具的预期依据，区间为完整评论中的 Unicode code-point `[start,end)`，**不是模型实际返回的引用**：

| 案例 | 固定文本依据 | 预期区间 / 用途 |
|---|---|---|
| C01 | 我咨询时，客服态度恶劣，还反复打断我。 | `[9,28)`，quote 内直接支持 |
| C03 | 我先前写的客服态度差不属实，我撤回那句评价。 | `[12,34)`，quote 内明确反驳 |
| C04 | 更新：我把配送员认成客服了，上面针对客服的评价作废。 | `[18,44)`，全文否决；原 quote 为 `[9,18)` |
| C12 | 客服态度恶劣，反复打断我。 | `[9,22)`，quote 内直接支持；本次未发送 |
| C15 | 昨天客服态度恶劣，反复打断我。 | `[9,24)`，quote 内直接支持；本次未发送 |

not_supported 对转述、假设和相反体验的实际处理，以及 unknown 对真实歧义的保留情况，本轮均不可判定。所有 15 条仍未获得独立人工语义验收；Q1 缺失的模型文本不能仅凭现存摘要完成逐条人工复核。

## Token、耗时、费用与清理

| 项目 | Q1 | Q2 | Q3 |
|---|---:|---|---|
| API 请求次数 | 1 | 0 | 0 |
| 本地输入估算 | 7695 | 7631（预检） | 7750（预检） |
| 实际输入 token | 3163 | N/A | N/A |
| 实际输出 token | 1003 | N/A | N/A |
| 思考 / 缓存命中 token | 0 / 0 | N/A | N/A |
| 请求准备 | 0.016s | N/A | N/A |
| SDK 调用 | 8.281s | N/A | N/A |
| Provider 响应验证 | 0.000s（计时分辨率内） | N/A | N/A |
| Provider 总耗时 | 8.297s | N/A | N/A |
| B2 总耗时 | 9.344s | N/A | N/A |
| 忙时 usage 费用估算 | ¥0.014350 | 无请求增量 | 无请求增量 |
| 结果 | HTTP 成功，引用校验失败 | 因 Q1 失败未执行 | 因 Q1 失败未执行 |

Q1 请求时间为 UTC 14:04:08.029–14:04:16.316，即 Asia/Taipei **2026-11-06 22:04:08–22:04:16**。实际响应模型为 `deepseek-ai/DeepSeek-V4.1-Flash`，提示版本 v2，finish_reason=stop；无 reasoning_content、无 token 超预算、无截断或超时。模型 ID 为服务别名，不能视为不可变快照。

总 usage 为 **4166 token**，按本次 provider 固定忙时输入 ¥2／百万、输出 ¥8／百万计算：`(3163×2 + 1003×8)/1e6 = ¥0.014350`。这是记录中的费用估算，没有查询账户账单，不等同实际扣费证明。各层耗时相互包含，不重复相加。当前估算仍非供应商计数上界，本次数据不用于调低门槛。

Worker PID 19720，exit_code=0、reaped=true、threads_released=true、termination_requested=false；随后只读 Get-Process 精确查询该 PID 不存在。子进程正常返回与主流程因 B2 输出无效而退出 1 并不矛盾。

## 离线复核与下一步判断

本轮仅做预检、一次真实流程和保存材料的只读检查。检查确认 C01–C05 输入与固定夹具一致、quote 区间和全文哈希一致；B1 封套摘要与 usage/B2 回执一致；模型投影不含预期字段或预期理由；调用总数为 1，Q2/Q3 未执行。夹具摘要仍为 `5254fb26cad7c3df4476dc91ecb8c9679300dbe4fd5ed0b77fe8a65fffcce60b`。没有重跑此前 110 项 Mock 回归，亦未将其当作真实成绩。

**本次不能支持扩大真实评论验证，也不能据此断定模型四状态语义能力差。** 当前可确认的是精确引用可靠性出现失败，且失败输出的保存不足，导致语义质量不可观测。未来若另行开展工作，优先处理无效输出的最小留存能力，使结构失败仍可独立诊断；这只是建议，本轮不改 B2、不新建审计平台、不补跑本组。

只新增本报告，保留当前占用目录及所有原记录；正式 Agent、reported_experience_v2、Schema、提示、案例和预期均未修改。**实验已结束并停止，未自动扩量，未启用正式 LLM 判断许可。**
