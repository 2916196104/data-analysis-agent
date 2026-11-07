# P5 Text Evidence 新契约真实可行性验证准备

2026-11-07。**准备完成，真实模型调用未授权、未执行。** 默认入口预检为 `ready_pending_explicit_text_evidence_authorization`，`missing=[]`。本轮真实 SiliconFlow、PostgreSQL、BGE 调用均为 0。新 Schema 的供应商接受情况仍为 **unverified**；Mock 成功只证明 SDK 请求构造与本地链路。

## 原型审查与最小改动

已阅读[离线设计](rag-engineering-p5-text-evidence-offline.md)、[v3 结果](rag-engineering-p5-v3-independent-result.md)、[D 组结果](rag-engineering-p5-single-four-state-result.md)、[引用修复方案](rag-engineering-p5-reference-refuted-offline.md)，以及现有契约、定位、B1/B2、Adapter、Worker、Provider 和失败诊断实现。

| 审查项 | 结论 / 本轮处理 |
|---|---|
| 新引用禁止 start/end | 已实现，继续严格拒绝额外字段；输出版本必填 |
| request_id/input_sha256、完整事实×候选矩阵 | 原绑定与 B2 矩阵检查有效；本轮把同一矩阵检查提前到定位之前，避免处理不属于本请求的矩阵 |
| key/source identity、全文哈希、quote 范围和切片 | 已实现，复用原定位逻辑；可信来源仍由原 P0 准入，不能靠模型回传哈希自证 |
| 三类引用搜索范围 | support/refutation 限当前候选 quote；context veto 限同一评论全文 |
| 重复、重叠、缺失文本 | 已实现，继续失败关闭；不同评论相同文字分别按候选身份绑定 |
| 关系引用与 witnesses | 定位后仍由原 B2 核对 witness、同评论及 before 证据；没有新增或放宽规则 |
| 转换后是否必经 audit_output | 是；新增强制让 audit_output 拒绝的测试，确认不会返回部分 resolved_output 或成功 |
| 失败可观测性 | 原型只有结果错误码；本轮补充阶段、候选 key、引用类型、受限原文和受保护写盘，不能确定字段时记录 null |

没有发现“绕过原 B2 审计即成功”的原型路径。必要改动是提取公共实验模块、提前矩阵绑定与增加诊断信息；没有重复实现原精确定位算法或语义判断器。

## 模块边界与新旧接口

| 文件 | 职责 |
|---|---|
| [experiments/text_evidence_protocol.py](../experiments/text_evidence_protocol.py) | 从 tests 提取原新契约及转换；先解析/绑定，再定位、构造标准 B1 Span、调用原 audit_output |
| [experiments/text_evidence_locator.py](../experiments/text_evidence_locator.py) | 原精确定位算法迁移，逐字搜索与两次匹配即拒绝的规则不变 |
| [experiments/text_evidence_shadow.py](../experiments/text_evidence_shadow.py) | 独立提示、严格新 Schema 投影、Provider 子类、实验 Adapter 与诊断子类 |
| [experiments/text_evidence_synthetic.py](../experiments/text_evidence_synthetic.py) | 独立虚构来源、确定性 SyntheticEncoder/Repository，通过真实本地 P0 校验；不访问 DB/BGE，不依赖 tests |
| [scripts/run_rag_shadow_text_evidence.py](../scripts/run_rag_shadow_text_evidence.py) | 固定五案例、摘要/预算预检、一次性目录、独立授权、标签比较与私有保存 |
| tests/shadow_text_evidence_prototype.py、tests/shadow_reference_locator_prototype.py | 保留原导入位置的薄兼容层，避免旧离线测试维护第二份实现 |
| [新案例](../tests/fixtures/shadow_text_evidence_cases_v1.json)、[HTTP Mock](../tests/fixtures/shadow_text_evidence_http_mock.py)、[新专项](../tests/test_rag_shadow_text_evidence.py) | 冻结预期、真实 SDK 的 Mock HTTP、失败/清理与隔离测试 |

另新增 experiments 包标识及本报告。没有改动 src、依赖、API、数据库或任何正式协议注册。experiments 和新入口不导入 tests；真实实验读取 tests/fixtures 中的固定 JSON，不加载测试 Python 模块。正式 Agent 仍不依赖实验模块。

输出契约仍为 **`p5b-text-evidence-output-experiment-v1`**，原[实验 Schema](schemas/p5-text-evidence-output-experiment-v1.schema.json) 字节保持不变。与活动 B1 的区别仅是实验输出的引用没有 start/end，保留 key/source_identity_sha256/text、四状态、事实/关系判断、必要见证与请求绑定。

新 Provider 自行构造 **Text Evidence JSON Schema** 请求；机械严格投影要求全部字段显式出现、移除 default、禁止额外属性，不使用旧 B1 输出 Schema。旧 B1 的已保存兼容性探针不为新 Schema 背书，也没有为本轮额外发送一次真实探针。

信任边界：模型只选择语义状态及精确原文；程序是坐标生产方。父进程先用原 P0/B1 准备可信输入，子进程的 envelope 来自该可信控制配置，仅供失败诊断；模型无法提供工厂或 envelope 配置。父进程再次解析原始新输出、绑定请求、定位并执行原 B2。唯一文本位置不是来源证明或语义证明。

新 Adapter 复用原 `_invoke` 的有界 IPC、deadline、取消及 kill/wait/线程回收，原 Worker 不变。没有把新输出交给旧 Worker 的 B1 诊断解析器；独立捕获子类对新契约分阶段诊断，复用原大小限制、脱敏、ACL 与原子禁止覆盖写入。正常结构结果也必须等待独立语义阅读，不能自动晋级。

## 新的五条固定案例

| ID | quote | 完整评论 | 固定预期 / 引用标准 |
|---|---|---|---|
| T601 | 我询问保修日期，客服嘲笑我连日历都不会看。 | 🧩虚构机松岚-Q6：我询问保修日期，客服嘲笑我连日历都不会看。 | **supported**；support_refs 须充分表达本人被客服嘲笑。完整 quote 的程序区间 `[10,31)` |
| T602 | 客服认真核对保修日期，回答始终很客气。 | 虚构机松岚-Q6：客服认真核对保修日期，回答始终很客气。我没有其他沟通记录。 | **not_supported**；相反体验不是撤回，不输出支持或反驳依据 |
| T603 | 先前那句客服辱骂我的投诉是我捏造的，我现在明确撤回。 | 虚构机松岚-Q6勘误：先前那句客服辱骂我的投诉是我捏造的，我现在明确撤回。 | **refuted**；quote 内 refutation_refs 须包含实际否认或撤回。完整 quote `[11,37)` |
| T604 | 客服在窗口挖苦我。 | 虚构机松岚-Q6初记：客服在窗口挖苦我。重新核对工牌：那是仓库收货员，先前对客服的指责失实，我撤销该项差评。 | **refuted**；context_veto_refs 指向角色更正/撤回段 `[20,54)`，不从全文补支持 |
| T605 | 接电话的那个人不停讥讽我。 | 虚构机松岚-Q6：接电话的那个人不停讥讽我。对方未说岗位，我无法分辨是客服还是门店联络员。 | **unknown**；保留角色歧义，不虚构客服身份或明确撤回 |

共同 question 为“有无客服态度差的评论？”，fact_id=`service_bad`，目标为作者本人体验松岚-Q6客服态度差。每条的目标、预期理由、引用类型及语义标准均已保存在冻结 JSON 中。上表区间是本地复核示例，不是发送给模型的标准答案；其他精确、唯一、语义充分的片段仍需独立阅读。

案例的 ID、quote、全文与 C/D/E 公开夹具逐项比对均不同；没有重跑旧实验。新案例由开发者在待测试模型调用前编写，不宣称是独立人工金标准。源准备在第一步即只保留 id/question/quote/review_text，污染全部预期字段也不改变请求投影。预期、理由、veto 注释、引用标准与评分规则均不进入模型输入或真实工厂配置。

## 版本与冻结摘要

| 内容 | SHA-256 |
|---|---|
| 提示 `p5-text-evidence-instructions-v1` | `a658b5667e7596461d46795300a151159233a6a54cbbe1659125434a4791af95` |
| 原新契约 Schema（规范 JSON） | `fd779cd32978b76369a4dccdb310c9008e99667d49155834a69ec79b7a6685b1` |
| 实际发送的严格 Schema 投影 | `332ff61606e85686a562a8281bd10b8794700d894c7cc10d992ad15edd469c8d` |
| 固定案例文件（含预期） | `2210526b8fc75ed25a5410deca043bc3e6251bd6fdedb6154bfcb544b08b536b` |
| 后端输入封套 | `881126f4713ce312da2478832144f1ce228ffdac8659a86741c6efe0d217a85c` |
| 完整请求体（系统提示、投影、严格 Schema、参数） | `06c4ec01c17a7a604698f4e4c23b952985c838da7bffac2a8991911af324da8b` |

以上均为本次重新生成和冻结，没有复用旧 v3 请求摘要、预算或 Schema 兼容性结论；漂移即阻断。

## 预算与一次性运行限制

| 项目 | 本次设置 / 预检 |
|---|---|
| 模型 / 配置 | deepseek-ai/DeepSeek-V4.1-Flash；仅现有独立 `.env.shadow`，不混用业务配置 |
| 输入估算 | **7453 ≤ 8000**，余量 547；`utf8_bytes_div2_plus1024_v1`，完整系统消息、来源、全文与新严格 Schema 均计入 |
| 输出参数 / 思考 | **2500** / `enable_thinking=false` |
| 请求 / 重试 | 最多 **1 次**；SDK、HTTP 重试均为 0，无自动补跑 |
| 时限 | 外层 30 秒；SDK read/connect/write/pool=20/5/10/5 秒 |
| 按本次输入估算的忙时条件费用 | `(7453×2+2500×8)/1000000` = **¥0.034906** |
| 按原固定输入/输出边界的条件预算 | `(8000×2+2500×8)/1000000` = **¥0.036** |

价格使用项目保存的输入 ¥2、输出 ¥8／百万，无缓存优惠；本轮未查询供应商价格、账户或计数端点。估算不是原生 tokenizer，两个费用数都不是账单硬上限。新 Schema 可能被拒绝；超时、usage 缺失、实际 token 或服务端计费口径可导致实际费用未知或超出估算。服务别名不是不可变模型快照。

实际 token/usage、模型身份、截断、思考及预算异常均会停止；未知费用不会记成 0。整个尝试完成、失败或取消后目录永久占用，入口没有改模型、案例、提示或输出目录的选项，不删除目录来获得下一次机会。

## 私有目录与失败保存

新路径为 `outputs/rag-semantic-retrieval/text-evidence-synthetic-v1/`，诊断固定为其下 `diagnostics/failure.json`，已确认由 Git 忽略。预检不创建运行目录；本轮该目录**尚不存在**。授权后先排他创建并限制整个目录为 owner-only，再写产物及调用。

运行将保存 preflight、冻结预期、本次模型输入、契约、合成来源、usage、执行回执和 summary。只有完整通过本地结构与 Provider/预算核验才保存标准 B1 judgments；结构失败不比较诊断标签，`validated_label_count=0`、`label_match_count=null`、`formal_accuracy=null`。

| 失败阶段 | 保存与拒绝行为 |
|---|---|
| provider / timeout / cancelled | API/模型身份/截断异常或进程截止、取消；可无原文，保存安全元数据，费用未知保留未知 |
| text_schema | 新 Schema、非法 start/end、非法 JSON/类型等；能定位时记录候选 key 与引用类型 |
| input_binding / matrix_binding / source_binding | 请求或摘要、事实矩阵、来源身份或全文/quote 不一致；拒绝转换 |
| text_location | 无匹配、重复或重叠歧义；记录候选、引用类型与固定错误码 |
| b1_conversion / b2_audit | 标准 Span 构造或原 B2 失败；保留原错误，不放宽校验 |
| semantic_labels | 结构通过但标签不符合冻结预期；保存失败原文和不匹配候选，停止，不修改预期 |

来源准入在请求前失败时没有合法模型输出可保留，只返回 source_binding 的安全拒绝状态；不会制造可信 envelope。对有可信输入的模型失败，捕获信息包括阶段、错误码、候选 key、引用类型和请求摘要；全局错误或没有引用时相关字段为 null。

原文沿用 65536 字节上限、512 KiB 文件上限、已知密钥及敏感字段脱敏、私有 ACL、原子禁止覆盖写入；不保存 Header、密钥或完整私人配置。脱敏后的原文不得用来重新晋级。磁盘故障/强杀可能使诊断保存失败，`failure_diagnostic_saved=false`，权威失败结果仍保持失败。SDK 在失败响应前未取得正文时只能保留元数据，不能虚构原文。

即使五个标签全部匹配，真实引用仍须按上述冻结标准独立阅读。测试中的“日历”可被唯一定位但不足以证明客服态度差：程序明确保持 `pending_independent_reading`、`semantic_verified=false`，不会把结构和标签匹配当成真实语义通过。

## 离线验收与隔离

最终专项 **48/48，30.678 秒**：原型 24 项 + 新运行路径 24 项。适用 B1/B2 与原失败诊断回归 **53/53，50.013 秒**。合计 **101 项不同测试通过，无跳过**。父/子进程屏蔽真实网络、数据库及 BGE；实际 SDK 请求仅走 Mock HTTP，没有真实供应商接受证据。未知模型 key 不直接写入诊断元数据，密钥回显的脱敏回归通过。

覆盖四状态、quote 撤回/全文否决、中文与 emoji、空白和 Unicode 精确匹配、重复/重叠/缺失、不同评论同文、来源伪造/哈希漂移、角色/事实语义反例、关系 witnesses、非法坐标、新旧 Schema 隔离、正确标签但缺失依据、合法引用但语义错误、诊断磁盘故障、SDK 失败、模型漂移、截断、超时、取消、预算及一次性占用。原测试继续验证正式 Agent、reported_experience_v2 和历史回答兼容。

开始前登记的 379 个既有文件中，**377 个摘要未变**；仅两个 tests 原型文件改为兼容导入。src、活动 B1 三份 Schema、实验 Schema、正式 Agent/v2、原 Adapter/Worker/Provider/诊断模块、旧提示、C/D/E 夹具与历史报告均不变。未读写旧私有产物，没有重新生成 D74/E84 输出或修正其状态。仅为文本独立性检查读取旧公开夹具，不执行其入口或旧实验测试。

复核命令：

```powershell
$env:PYTHONPATH='src;tests;.'
.venv/Scripts/python.exe -B -m unittest test_shadow_text_evidence_prototype test_rag_shadow_text_evidence -q
.venv/Scripts/python.exe -B -m unittest test_p5b1_structured_evidence_design test_review_structured_shadow test_shadow_failure_diagnostics -q
git diff --check
```

Python 语法、Schema 一致性、源代码导入检查和差异空白检查通过。没有前端改动，无需前端构建或页面验收。未新增审批、历史审计或其他开发阶段。

## 预检与未来授权命令

默认或显式 preflight 均只做本地准备：

```powershell
.venv/Scripts/python.exe -B scripts/run_rag_shadow_text_evidence.py
.venv/Scripts/python.exe -B scripts/run_rag_shadow_text_evidence.py preflight
```

实际预检关键字段：`status=ready_pending_explicit_text_evidence_authorization`、`missing=[]`、`candidate_count=5`、`input_estimate=7453`、`provider_new_schema_acceptance=unverified`、`old_b1_compatibility_reused=false`、`spending_authorized=false`，真实 LLM/DB/Embedding 调用计数均为 0。

**本轮未执行**的未来单次命令：

```powershell
.venv/Scripts/python.exe -B scripts/run_rag_shadow_text_evidence.py run --authorize-fixed-text-evidence-shadow --accept-estimated-token-risk
```

下一次需要明确授权：使用现有独立 SiliconFlow 密钥，向指定模型发送 **T601–T605 的 quote、完整虚构评论、必要契约及来源字段、独立 Text Evidence 提示和新 JSON Schema，最多一次付费可行性请求**，不发送预期或评分规则；接受新 Schema 可能拒绝、估算不准、超时、usage 缺失、费用超过 ¥0.036 和服务别名漂移的风险；允许按现有隐私保护保存受限诊断。该授权不包含真实业务数据、DB/BGE、旧组重跑、补跑、正式发布或自动 LLM 许可。

始终 `publication_allowed=false`、`semantic_verified=false`、`permitted_target_fact_ids=[]`。**本轮离线准备完成，停止并等待独立真实请求授权。**
