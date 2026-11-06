# SiliconFlow Shadow：语义边界修正与下一轮验证准备

2026-11-06。已完成最小提示修正、离线测试及 S3 超时分析；**没有请求 SiliconFlow、连接业务数据库、加载 BGE-M3 或重跑 S1/S2/S3**。这次完成的是工程修正，不代表真实模型的四条错误反驳已经修复。

## 实际原因与改动

原 B1 协议已将 refuted 定义为候选主张被明确反驳或撤回。但 SiliconFlow 复用了 OpenAI 通用提示，未明确说明“相反体验不是对存在性反馈的反驳”。S1/S2 保存的四条 refuted 均属于相反体验；引用真实、区间合法，后端结构检查无法识别这种自然语言分类错误。这里只能确认提示存在歧义、模型确实出现错误，不能证明提示歧义是唯一原因。

本轮仅修改一个已有源码文件，新增一个测试文件及本报告：

| 文件 | 本轮用途 |
|---|---|
| `src/data_analysis_agent/agents/review_shadow_siliconflow.py` | 独立系统提示 `p5-shadow-siliconflow-instructions-v2`；补充受限阶段耗时、传输事件和异常类型，便于后续定位超时 |
| `tests/test_shadow_semantic_boundaries.py` | 合成语义预期、Mock HTTP、引用/关系反例、超时诊断与泄露检查 |
| 本报告 | 修正依据、实际测试、后续三题方案与限制 |

提示 v2 明确以下边界，B1 Schema、状态枚举及后端校验均不变：

| 状态 | 应用边界 |
|---|---|
| supported | quote 对目标对象、主题和说话者的事实提供直接支持 |
| not_supported | 不提供直接支持，包括不同主题/对象、仅转述/假设、相反体验；不同买家相互矛盾也不能互相撤销反馈的存在 |
| refuted | 当前候选涉及的主张被明确否认、撤回或纠正；必须引用实际反驳，不能只引用相反评价 |
| unknown | 对象、说话者、否定范围、时间或上下文关系无法可靠确定 |

quote 内的反驳使用 refutation_refs；同评论全文中的明确撤回使用 context_veto_refs。全文只解释或否决，不能补支持事实。其他对象的好评、无关转折不自动否决 quote；客服、售后、配送、安装角色不能自动合并。系统提示仍将评论视为不可信数据，不允许改目标或拼接评论。

提示 SHA-256：`6d0e268c77b3dfc77956ec31b154ff5f04a84db34d15271f40c1ee93c85c130d`。新旧提示分别为 2814/2815 UTF-8 字节，没有扩大预算。原合成探针只证明结构/API 兼容；不能作为新提示的语义质量证明。原 OpenAI 提示未修改。

## 测试与正式系统隔离

新增 **6/6** 测试通过，包含 12 个单候选情形，以及不同买家配对、同评论/跨评论关系、错误引用等检查：

- 正面体验对负面目标、负面体验对正面目标：预期 not_supported。
- 明确纠正、先报告后在全文撤回：预期 refuted，核对反驳或否决区间。
- 客服抱怨与其他服务角色好评共存：不因无关上下文否决；安装或配送抱怨不能支持客服目标。
- 朋友转述、买家直接体验、假设、无关转折、模糊否定：分别保留预期区分与 unknown。
- 不同评论的相反体验不抹去有效支持；同评论双事实可成立，跨评论伪造 same_review 被拒绝。
- 无反驳依据、quote 外反驳引用被拒绝；**故意提供语义错误但区间合法的 supported 仍可能通过结构检查**，但 semantic_verified/publication_allowed 始终为 false。

这些是开发者编写的预期输出，经真实 SDK 的 Mock HTTP 和原 B1/B2 检查验证；**Mock 不进行语义推理，不能证明 LLM 已学会这些边界**。S1/S2/S3、原 P5 探针、B1 新表达及本轮合成情形都属于已见开发案例。

实际执行：

```powershell
$env:PYTHONPATH='src;tests'
.venv/Scripts/python.exe -B -m unittest test_shadow_semantic_boundaries test_review_shadow_siliconflow test_siliconflow_schema_probe test_review_shadow_openai -v
.venv/Scripts/python.exe -B outputs/rag-semantic-retrieval/p5-shadow-semantics-v2-20261106/run_checks.py
.venv/Scripts/python.exe -B -m unittest test_shadow_semantic_boundaries -v
git diff --check
```

Provider/探针专项 **55/55** 通过；适用回归 **606/606** 通过，包含上述专项，不重复相加。最后一次新增专项复跑 **6/6** 通过。既有两项队列测试单独重现为 **2 errors，0/2 通过**：SQLite 夹具触发“持久标注队列只支持 PostgreSQL”，未修改业务队列。

回归禁止真实 PostgreSQL/外部 HTTP，36 份本地合成子进程回执均 reaped、threads_released；包含实际挂起、超时、取消和异常退出测试。新增超时诊断测试只有 Mock ReadTimeout，不代表定位了原 S3 的网络阶段。

保护检查：本轮开始时的 124 项源码/Schema/配置/入口摘要中，仅 SiliconFlow 模块发生预期变化；`.env`、`.env.shadow` 未变。原业务实验 **23/23** 产物、原一次性探针 **11/11** 产物摘要一致。没有读取人工历史裁决。原有用户改动保留，正式 v2、P0、SQL、回答/历史读取和 Agent 调度源码未修改。

## S3：已知与未知

| 已保存证据 | 结论 |
|---|---|
| 一次 SDK 尝试；provider_elapsed_ms=20343；外层 total_ms=21391 | 不是多次自动重试；不能把总耗时全部归为模型推理 |
| APITimeoutError；无 HTTP 状态、模型内容或 usage | 无法区分连接、发送、响应等待/读取或服务端处理；也不能证明远端未计费 |
| PID 30544、exit_code=70、reaped=true、threads_released=true、termination_requested=false | SDK 错误后子进程正常走异常清理，未触发外层强制终止，无本地资源泄漏证据 |
| 本地输入估算 7832，原输入 6526 字节 | 没有在预检中超过 8000；本地估算不是供应商计费 token 或时延保证 |

现有 HTTPX 配置是 read=20、connect=5、write=10、pool=5 秒的**分阶段**超时，并非严格的 SDK 总时限 20 秒；B2 的 30 秒 deadline 才覆盖输入准备、进程启动、调用和输出检查。本轮原样保留，max_retries=0，不补跑 S3。

后续诊断增加 request_preparation_ms、sdk_call_ms、response_validation_ms，以及最多 32 条白名单传输事件和异常类型链；不保存 URL、请求头、错误原文、正文或密钥。它只能诊断未来调用，不能回填旧 S3 的未知阶段。强制终止仍不能保证取消供应商已收到的请求；缺 usage 时费用继续为未知，不记为零。

代码有重复 JSON 校验/序列化，但没有证据证明它导致本次约 20 秒超时。单事实单候选合成输入 30 次本地准备/估算/序列化的中位数 **8.503ms**、最大 **9.942ms**；不含启动/网络/模型，也不能替代 S3 性能测量。因此未增加缓存、放宽时限或移除校验。

## 下一轮小规模验证草案（未授权、未执行）

独立记录 N1–N3，使用提示 v2；不修改或重跑原 S1/S2/S3。

| ID | 固定问题 | 重点 |
|---|---|---|
| N1 | 是否有安装师傅态度很差的评论？ | 安装对象与客服/售后分离，正确支持与对象错误 |
| N2 | 有无客服态度很好的反馈？ | 相反体验应不支持；无关上下文不导致拒绝；若存在否认或撤回则检查否决 |
| N3 | 有没有噪音不大的反馈？ | 否定范围、相反体验与真正纠正的区别 |

三题已用原解析器静态生成 supported 契约，事实分别为 installer_bad、service_good、noise_low；未给模型增加目标解析权。**撤回、转述、假设是否出现在 Top-5 尚未知**；首轮若未覆盖必须记 N/A，不能自动换题、挑评论或声称已验证该风险。

固定范围仍为 `reviews_csv / reviews-csv`、商品 `100013588055`、UTC `2021-09-11` 至 `2022-08-26`；沿用原标注身份/EmbeddingSpace、BGE-M3 1024 维及已有 200 条索引，每题 Top-5。未来 P0 只读验证需要范围内完整有效语料及来源元数据，沿用前次最多 510 条评论的范围，而非只读 15 条；范围/身份变化即停止，不自行扩大。发往 SiliconFlow 的只有三题契约、Top-5 quote/精确区间、对应全文与必要来源字段，至多 15 个排名条目/15 条关联评论，不发送人工参考或 v2 结果。

最多 **3 次** LLM 调用、3 次本地查询编码，每题一次，无自动重试；输入估算目标 8000、max_tokens=2500、enable_thinking=false、SDK/外层超时保持不变。若来源、Schema、模型、usage、思考量、截断、预算或清理异常，停止后续题；失败可能消耗额度且没有 usage。

按 2026-11-06 只读复核的[官方价格](https://www.siliconflow.cn/pricing)，每百万 token 忙时输入/输出/缓存命中为 ¥2/¥8/¥0.04，02–08 时段为 ¥1/¥4/¥0.02。假设实际输入不超过 8000、输出不超过 2500、无缓存优惠：忙时每题 **¥0.036**、三题 **¥0.108**；低价时每题 ¥0.018、三题 ¥0.054。它们是条件估算，不是扣费硬上限；原 S3 未知费用也不混入新实验。原生计数绑定和模型别名服务漂移风险继续保留。

计划保存于 Git 忽略的私有目录，live_authorized=false；将来使用新目录 `outputs/rag-semantic-retrieval/semantic-boundary-shadow-v2`。**尚未新增真实运行入口，旧 run_rag_shadow_first.py 仍锁定 S1/S2/S3，不得改参数冒充新实验。** 后续若获三题读取/第三方发送/费用风险授权，只需复用原端口增加固定三题入口，无需新适配器或审批平台。

新问题不等于独立泛化测试集：同商品、同索引可能召回已见评论，应记录重合，未覆盖风险不作通过结论。真实验证需逐条阅读支持/反驳/否决引用，同时统计错误许可、错误拒答、unknown、未覆盖及失败；publication_allowed、semantic_verified 仍为 false。

本轮材料与回归日志：[私有准备目录](E:/AllProject/data-analysis-agent/outputs/rag-semantic-retrieval/p5-shadow-semantics-v2-20261106)。到此停止，等待新的真实调用授权。
