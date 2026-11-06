# P5-B1：独立结构化证据判断契约设计与 Mock 验收

2026-11-06。本阶段已完成独立草案 Schema、测试专用接口原型和隔离验证，**没有接入真实 LLM，没有修改正式许可或回答链路**。新结果只用于 Shadow 观察；结构验证通过不代表语义蕴含已证实，更不提供回答许可。

本轮新增文件只有本报告、`docs/schemas/p5b1-*.schema.json`、`tests/p5b1_contract_prototype.py`、`tests/test_p5b1_structured_evidence_design.py`、`tests/fixtures/p5b1_new_expressions.json`。原型全部位于 tests，生产代码不导入它；没有新依赖、API、数据库表或审批平台。

## 1. 当前复用点与实际缺口

| 当前实现 | 本设计如何复用 |
|---|---|
| `review_task_contract.RuntimeQueryContract` | 输入直接嵌套原类型，保留原问题、scope、事实 ID/描述、all_of/any_of、必要关系；不复制目标模型或重写问题 |
| `review_intent / review_plan` | 仍是目标与范围的产生方。证据判断器不能把 retrieval_only/unsupported 改成 supported |
| P0 `SemanticResult / SemanticEvidence / ReviewSource` | 使用原候选顺序、观点版本、quote/区间和完整上下文；保持 current_effective_readonly_v1，不能升级为历史可信来源 |
| `semantic_retrieval._verified_sources()` | 原型复用完整有效观点集合、来源版本、scope、原文切片及快照核验；独立 fixture 来源不从待测候选复制 |
| `review_retrieval.validate_retrieval()` | 原 v2/历史策略仍独立重算，不接受新输出代替 assessment |
| `review_answers.semantic_answer()` | 当前只接受原许可策略及其事实绑定；本阶段不扩展 |
| `review_compression.read_public_analysis()`、`read_public_answer()` | 历史读取继续重建原策略；不重跑新模型，不升级旧结论 |

P5-A 仍有两个重要限制：部分 `RequiredFact.description` 等于事实 ID，外部判断器不能只凭 ID 猜测含义；真实解析器对任意改写、复杂否定、any_of/before 等仍可能产生未知或 unsupported。B2 若需要这些目标，必须在原目标产生边界提供可靠描述/澄清，不能让证据模型自己补目标。本轮没有修改该边界。

`RuntimeQueryContract` 也没有独立子问题许可集合，本版**不设计 limited 发布许可**。可以观察部分事实，但不能推定它们可以单独回答。新增该能力应以后在同一运行时契约中明确表达。

## 2. 新旧策略隔离

- 设计版本：`p5b-evidence-input-draft-v1` / `p5b-evidence-output-draft-v1`；尚非生产策略发布。
- v2、legacy、R0、B2 参考、人工裁决和历史审计保持原样；新输出不是它们的兼容替代品。
- 新结果不包含可由模型设置的最终许可集合、统计数值、scope 修改或用户目标修改。后端只做 Shadow 结构汇总。
- 原型结果固定 `semantic_verified=false`、`publication_allowed=false`、`permitted_target_fact_ids=[]`，没有 override/v2 旁路开关。
- 当前运行时只读来源政策不等于历史快照证明；旧 E1 的 history_unproven 等限制不因本设计消失。自产摘要仅用于本次输入绑定，不是独立封存或批准。

隔离测试实际调用合成 Agent，再添加独立 Shadow 记录，公开分析及历史回答保持相同；将新输出塞进 `assessment` 被原校验器拒绝。这不是生产 Shadow hook，本轮没有安装 hook。

## 3. 输入 Schema 与接口

完整可机器读取的 Schema：

- [后端输入封套](E:/AllProject/data-analysis-agent/docs/schemas/p5b1-backend-input.schema.json)
- [模型输入白名单投影](E:/AllProject/data-analysis-agent/docs/schemas/p5b1-model-input.schema.json)
- [模型输出](E:/AllProject/data-analysis-agent/docs/schemas/p5b1-model-output.schema.json)

JSON Schema 约束结构和枚举；跨字段、来源、引用和关系约束由下文的后端检查补充，不能仅靠 JSON Schema 宣称安全。

| 输入字段 | 约束 |
|---|---|
| version / request_id | 独立协议版本；请求 ID 由调用方分配 |
| contract | **原 RuntimeQueryContract**；事实和关系不能由判断器产生或改写 |
| contract_sha256 | 与调用方独立持有的原目标摘要比较，不从输出反推预期目标 |
| retrieval_sha256 | 绑定原 SemanticResult 的问题、scope、顺序、版本及来源观察 |
| source_policy / context_policy / context_version | 当前来源 + `quote_full_review_veto / 1`，不允许静默更换 |
| candidates[] | 原 rank/key、review_id、annotation_id、商品/日期、quote/区间、完整评论、原文哈希、有效快照、label_profile_key、文件行引用及来源身份摘要 |
| input_sha256（模型投影） | 后端完整封套的摘要，由模型回传；校验器使用自己保留的封套核对 |

模型投影从同一契约机械生成，仅移除 scope 中的标注 endpoint `base_url`；不是另一套目标解析。原始完整契约留在后端。模型不接收 cosine distance、v2 assessment、人工参考、相关性评分、人工许可或模型评价成绩。

原型接口定义：

```python
prepare_input(contract, result, *, trusted_contract_sha256,
              trusted_sources, request_id) -> JudgeInput
model_payload(request) -> str
MockJudgePort.judge(payload_json: str) -> str
audit_output(request, raw_output) -> ShadowOutcome
run_mock_shadow(request, judge) -> ShadowOutcome
```

其中 `trusted_sources` 是独立 repository fixture 的完整来源记录，不能由待测候选重建。本轮用它演示来源检查，未执行数据库读取。未来生产适配应复用 P0 在同一事务已完成的来源核验和调用方持有的结果；不为模型重复读取数据库，也不建立新的历史认证体系。

只有目标明确、`fact_search/evidence/supported`、无不确定项、无统计目标且 scope 匹配的契约进入判断。SQL、原因、未解析目标和 unsupported 直接停止，不能被模型“修好”。工程预算草案为最多 5 个候选、8 个事实、单评论 40000 字符、投影 128000 UTF-8 字节；超限阻断，不截断否决上下文。它们不是质量验收门槛，B2 需按选定模型重新明确 token/输出预算。

## 4. 输出 Schema 与状态含义

输出最外层只有版本、request_id、input_sha256、execution_status、fact_judgments、relation_judgments；严格禁止额外字段。它不返回 sufficient/limited 或正式答案。

每个事实对**每个候选**都必须有一条判断，不能只挑有利候选，不能省略未识别项：

| 字段 | 含义 |
|---|---|
| fact_id / key | 必须来自当前契约和当前候选；全笛卡尔积唯一且完整 |
| status | supported / refuted / not_supported / unknown |
| support_refs | 只能在该候选 quote 内的直接支持子区间 |
| refutation_refs | 只能在 quote 内的反驳子区间 |
| context_veto_refs | 同评论全文内的解释/否决区间，只能否决，不提供新支持事实 |
| reason_code / rationale | 受限原因码 + 简短可审查依据；不作为业务回答发布 |

引用结构为 `key, source_identity_sha256, start, end, text`。区间是 Python Unicode code point 的左闭右开绝对原文索引；不 trim、不做 Unicode 归一化。必须与原文精确切片一致，支持/反驳区间还必须完全位于原候选 quote 中。

| 状态 | 语义及限制 |
|---|---|
| supported | 模型主张该候选 quote 直接支持该目标事实，且无其报告的局部否决；仍是待验证的模型判断 |
| refuted | 该候选断言被明确反驳或上下文撤回，不能作为本事实的支持；不代表全库不存在此类反馈 |
| not_supported | 在已检查材料内没有直接支持，例如主题/对象不符、仅背景相关；是证据不足，不是事实为假 |
| unknown | 无法可靠理解语义、否定作用范围、对象、时间或上下文冲突；与明确无支持分开统计 |

状态是候选局部判断：另一位买家的相反体验不否定“存在某买家反馈”。同一评论同一事实的反驳/否决则排除该评论的支持见证。引用与状态一致只能作结构检查，模型把无关合法原文说成支持的语义错误，不能靠哈希识别。

关系判断使用 `relation_0` 等后端顺序 ID，引用原 `required_facts.relations`，不允许模型增加关系。每项返回 status、witnesses（事实 ID→候选 key）、支持/反驳区间、原因码和依据。

- same_review：支持见证必须覆盖该关系全部事实，且确实是同一 review_id；不能跨评论拼接。
- before：同样绑定同一评论见证，另需 quote 内的时间关系证据。文本位置先后不能证明事件先后；是否真正蕴含先后仍待语义判断验证，原型不宣称已解决。
- any_of/all_of 由后端聚合；已有关系均是**必要关系**，不能因 any_of 命中一项就擅自忽略关系。原契约没有“分支关系可选”语法，不猜测其含义。
- 未知关系 ID/事实 ID 或缺失判断为协议失败；合法关系返回 unknown 则为条件未满足，两者均无许可，但分别统计。

## 5. 状态机与失败关闭

| 步骤/结果 | 后续行为 |
|---|---|
| 契约未解析、任务不支持 | not_eligible；不调用判断器，回到原有澄清/不支持结果 |
| 来源/目标/范围验证失败 | 输入执行失败；不当作正常证据不足，不调用判断器 |
| 空候选 | skipped_empty；不调用判断器，不推断全量不存在 |
| 输入超预算 | input_invalid/context_budget_exceeded；不截断后放行 |
| 超时、适配器异常、模型声明失败 | model_failed；无自动重试，不暴露内部异常文字 |
| 非法 JSON、未知 ID、引用越界、缺字段或逻辑冲突 | output_invalid；拒绝整份新输出 |
| 结构完整、部分事实/关系 unknown | validated_shadow，目标条件未满足；记录未知覆盖，不发布 |
| 结构完整且形式上的目标条件满足 | validated_shadow/structural_goal_satisfied=true，仍 semantic_verified=false，仍不发布 |

ShadowOutcome 的 `model_supported_fact_ids` 只是通过结构检查的模型主张。`unestablished_fact_ids` 包含所有未支持的原子事实；`missing_required_fact_ids` 按逻辑条件计算，any_of 命中后为空；关系缺口单列 `unmet_relation_ids`。这避免将“未满足备选项”和“核心条件不满足”混为一谈。

当前原型只模拟 TimeoutError，不实现真实网络截止、取消或进程终止。B2 必须实现实际 deadline、输出字节上限、异常归类和资源释放；不能将本轮 Mock 超时测试写成真实调用超时验收。

## 6. 后端确定性检查与失败回退

校验顺序固定：调用方的原契约/输入绑定 → P0 来源版本与范围 → 候选集合/顺序 → 输出 Schema → 事实×候选完整性 → 精确引用 → 状态/证据一致 → 必要关系见证 → 逻辑目标汇总。

检查必须使用后端保留的原输入，不接受模型自行提交“预期 scope”“可信来源”或新契约。不能用合法的区间和主题相似冒充语义证明，也不能从完整评论借入 quote 外的正向事实。未确定的否定、假设、转述、纠正或冲突保留 unknown；不得从个别自述产生总体比例、客观原因或长期可靠性结论。

Shadow 失败不会扩大 v2 原有许可。主链路已通过的 v2 回答保持原样，v2 的未知/不足也保持原样；不因新输出失败启动旧 SQL 原文替代或默认放行。生产历史继续按原版本回放，不调用新判断器“修复”旧答案。

## 7. Shadow Mode 后续接入方案

本轮没有启用配置或调用真实模型。B2 建议只实现一个默认关闭的独立适配器：从已获准、已完成 P0 核验的同一候选快照与活动 RuntimeQueryContract 生成白名单输入，经后端检查后将原始输出和 ShadowOutcome 单独保存到本地私有目录。

正式 v2 回答仍独立完成，Shadow 不修改 assessment、许可集合、事实目标、排名、用户会话或最终回答；不要把原始 Shadow 理由直接塞进前端结果。比较时单列执行覆盖、unknown、结构错误及语义争议，既看无依据许可，也看可回答却被拒绝，不能靠全部 unknown/拒答宣称改善。无需新增审批平台。

模型适配层应只有结构化判断端口，不提供 SQL、文件读写、检索或修改契约的工具。评论全文属于不可信数据，不能当成系统指令。真实模型的 prompt、模型版本、输入处理、上下文策略和预算需要固定；更换版本后不能混报成绩。

## 8. 测试矩阵与实际结果

| 测试组 | 实际验证 |
|---|---|
| 原契约复用 | 原 RuntimeQueryContract 类型、JSON 往返、范围不可变、投影 Schema 不复制事实定义 |
| 自由表达 | 新引文改写可以承载 Mock supported；原 v2 对同引文仍按原规则拒绝，未改规则；未解析的自由问题在输入前停止 |
| 否定/纠正 | quote 内反驳、全文否决、emoji 前缀区间、假设 unknown；quote 外事实不能成为支持 |
| all_of / any_of | 原子未支持集合、核心缺失集合和逻辑满足状态分别检查；没有自行生成 limited |
| 同评论关系 | 同评论见证结构通过；跨评论、未知/缺失关系、未知见证事实拒绝 |
| 时间关系 | 缺少时间证据拒绝；合法见证和引用可通过结构检查，但 semantic_verified 保持 false |
| 来源 | 原文/quote/hash 漂移、跨商品/日期/标注版本、文件引用及来源策略篡改拒绝 |
| 输出攻击/错误 | 未知事实、key/摘要篡改、错区间/原文、重复/缺失行、非法枚举、额外许可字段、重复 JSON 键、NaN 拒绝 |
| 失败关闭 | Mock 超时/异常/声明失败、空候选、超预算，无自动重试与正式许可 |
| 生产隔离 | 合成 Agent 与历史公开回答不受 Shadow 影响；新输出不能替换 v2 assessment |

原 P5 15 问保留为**已见路由/准入回归**，P11 仍使用原问题及对应合成日期范围；本轮没有将它们重新命名为泛化测试或重测真实召回。另有独立文件中的 12 个新表达场景，覆盖改写、对象、否定、撤回、假设、上下文及空候选。这些是开发者编写的合成协议预期，不是人工金标准；已参与本轮 Mock 开发验证，未来模型调参后不能再宣称是独立未见泛化集，真实模型验证仍需保留未用于调参的集合。

实际执行：

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_p5b1_structured_evidence_design.py -v
.venv/Scripts/python.exe -B outputs/rag-semantic-retrieval/p5b1-20261106/run_checks.py
git diff --check
```

专项 20/20 通过；适用后端回归 529/529 通过（包括原 509 与新增 20，85 项 RAG/运行时子集已包含，不重复相加）。两项既有 SQLite/PostgreSQL 标注队列错误单独重现并保留，不计为通过。无真实数据库、Embedding 或外部 LLM 调用。

首轮专项 18 项有两处夹具错误：用于超预算的中文数据实际未超过字节上限，以及已见双事实场景漏提供 unknown 关系项；修正夹具后新增隔离/Schema 测试，20 项通过，未放松校验。原始失败日志保留。

保护材料及日志在 Git 忽略目录 `outputs/rag-semantic-retrieval/p5b1-20261106/`。122 项现有 src/阶段报告文件按本轮前后 SHA-256 核对；原有已暂存工作保持原样。前端未修改，本轮未重跑浏览器或真实 P4/P3 使用验收。

## 9. P5-B2 实施条件与尚未解决风险

**具备继续实现独立、默认关闭适配器的设计基础；不具备据此启用真实模型或生产许可的条件。** 下一阶段最小工作为：

1. 只接收可靠目标；处理现有 ID 式 description 的可解释定义。未知目标由原意图边界澄清，unsupported 不得在适配器中翻成 supported。
2. 将测试原型中的来源绑定、输出检查和版本隔离落实为独立实现，继续保留原 v2；为输入/输出大小、实际超时/取消和异常清理补充真实运行层测试。
3. 选定结构化输出方案、模型与预算，并在明确获准后开展 Shadow 模型验证。需要评估语义错许可、错误拒答、未知覆盖及延迟，而不只验证 JSON/引用合法。尚无模型费用、延迟或语义质量实测。
4. before、否定纠正、局部冲突仍有自由语义判断风险；来源哈希不能解决它们。当前没有独立子问题的运行时许可定义，不能擅自开放 limited；SQL 比例、因果和索引覆盖边界继续保留。

本阶段到此停止，不实施 P5-B2/B3，不启用任何模型或正式策略。
