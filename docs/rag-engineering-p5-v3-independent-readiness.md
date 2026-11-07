# v3 独立真实语义验证：准备报告

2026-11-06。**只准备，未执行真实调用，等待独立付费授权。** 预检为 `ready_pending_explicit_v3_authorization`、`missing=[]`。本轮真实 SiliconFlow、业务数据库、Embedding 调用均为 0。`publication_allowed=false`、`semantic_verified=false`。

## 固定案例与预期

采用本轮开始时工作区已有、尚未跟踪的 [E81–E85 草稿](../tests/fixtures/shadow_v3_independent_cases_v1.json)，保留其内容并冻结摘要。五条评论、quote 和 ID 均不同于 D71–D75，也不复用旧 C 组。共同目标为“作者本人曾体验客服态度差”，原解析器生成 `service_bad`；青禾-Z3 为虚构设备，来源使用既有 FakeRepository/FakeEncoder，经原 P0/B1 校验，不连接业务来源。

| ID | quote | 完整评论 | 固定预期 |
|---|---|---|---|
| E81 | 我问售后政策时，客服讽刺我不懂还乱问。 | 虚构设备青禾-Z3：我问售后政策时，客服讽刺我不懂还乱问。 | **supported**：本人直接报告客服负面态度，没有撤回 |
| E82 | 客服接待耐心，回答问题一直很客气。 | 虚构设备青禾-Z3：客服接待耐心，回答问题一直很客气。这是我第一次咨询。 | **not_supported**：单纯相反的正面体验，不是撤回，也不能反驳其他买家 |
| E83 | 我之前说客服骂人并不属实，现在撤销这条差评。 | 虚构设备青禾-Z3更正：我之前说客服骂人并不属实，现在撤销这条差评。 | **refuted**：quote 内明确否认并撤回自己的既有差评 |
| E84 | 客服当面嘲讽了我。 | 虚构设备青禾-Z3：客服当面嘲讽了我。核实后更正：那人是配送员，原先针对客服的指责不实，我撤回。 | **refuted**：同一评论全文纠正对象并撤回，否决初始 quote |
| E85 | 那位工作人员说话很不客气。 | 虚构设备青禾-Z3：那位工作人员说话很不客气。对方没说明身份，我不能确定是客服还是物流接线员。 | **unknown**：全文仍不能可靠确定对象，保留歧义 |

预期标签、理由和引用标准由开发者在待测试模型调用前冻结，未由 SiliconFlow 生成。这里的独立指全新文本、新实验和独立产物，不代表盲评、独立人工金标准或统计意义上的泛化评测。此次没有 v2 对照调用，未来一次 v3 结果只能描述本组表现，不能量化同集 v2→v3 改善幅度。

引用标准冻结如下，区间均为**评论全文 Unicode code point 绝对 `[start,end)`**：

| ID | quote 区间 | 必需依据与充分引用示例 |
|---|---|---|
| E81 | `[10,29)` | quote 内 `support_refs`，完整 quote `[10,29)` 可充分支持 |
| E82 | `[10,27)` | 无支持或撤回依据；不把正面体验写成 refutation，规范示例三个引用数组均为空 |
| E83 | `[12,34)` | quote 内 `refutation_refs`；完整 quote `[12,34)` 包含实际否认和撤回，不能只引用“客服骂人” |
| E84 | `[10,19)` | `context_veto_refs` 引用全文 `[19,48)`：“核实后更正：那人是配送员，原先针对客服的指责不实，我撤回。”；不能放入 quote 内 refutation 或 support |
| E85 | `[10,23)` | 对象未知，无支持/反驳依据；规范示例三个引用数组均为空，不凭全文补出客服身份 |

上表给出充分引用示例；其他逐字、来源一致且语义充分的精确子区间可独立阅读判定，不把“必须使用整个示例段”加成新 B2 规则。所有引用必须匹配同一候选 key、source identity 和原文切片，满足 `end=start+len(text)` 与合法上下界。全文只能解释、限定或否决 quote，不能补充支持事实。

## 独立提示、入口与目录

[新入口](../scripts/run_rag_shadow_v3_independent.py) 默认只做离线预检；独立工厂为 `scripts.run_rag_shadow_v3_independent:create_judge`。通过既有 Judge 子类替换新实验的系统消息和 usage 提示标识，沿用原传输、预算、超时、无重试、诊断和 B1/B2。没有修改活动 v2、正式规则、Schema 或校验，也没有导入离线坐标定位原型。

- 新实验提示版本：`p5-shadow-siliconflow-instructions-v3-independent-v1`。
- 提示内容：[既有 v3 候选](../tests/fixtures/shadow_siliconflow_prompt_v3_candidate.txt)，3152 UTF-8 字节；发送文本不含文件末尾换行。
- 提示 SHA-256：`c809025aeac408ded54f87144601526fb280712597ea8262d4ed128f2fca3e8e`。
- 活动 v2 仍为 `p5-shadow-siliconflow-instructions-v2`，摘要 `6d0e268c77b3dfc77956ec31b154ff5f04a84db34d15271f40c1ee93c85c130d`。
- v3 澄清撤回优先于一般不支持、quote 内反驳与全文否决的字段边界、相反体验不是撤回、未知对象不猜测，以及绝对 code point 坐标和无法可靠引用时失败关闭。

固定夹具（含预期及引用标准）SHA-256：`0645417e9ed9eec5ee8834cb271dfd987cc01945ed018039a57c2fa630bf1cac`。

准备输入封套 SHA-256：`8c8f6a96bc02129ebc97393346b5902b1aeac5dfe2adfc48494c7bd40b2a280e`。

含 v3 系统消息、完整模型投影和原严格 Schema 的请求体 SHA-256：`b3ed3547bebba4513bae8f8efbc7b4c93ce1887a6203b1ed717cf08e9fb66bee`。漂移即阻断，不为了预算改评论、来源或校验。

独立私有路径：`outputs/rag-semantic-retrieval/v3-independent-synthetic-v1/`；显式诊断路径：其下 `diagnostics/failure.json`。预检只检查计划路径，不消耗一次性目录；本轮未创建该运行目录。授权执行时排他创建，并在写入任何产物前使用既有 owner-only 权限机制限制整个新目录。诊断沿用 65536 字节原文上限、512 KiB 文件上限、脱敏和原子禁止覆盖写入；路径由 Git 忽略。

目录一旦占用，成功、失败或取消均不能通过此入口再次运行；无其他案例、输出目录、模型或提示的 CLI 选项。失败诊断落盘仍受磁盘故障、强制终止等实际限制，不影响失败关闭。

## 预算预检

| 项目 | 固定值 / 实测预检 |
|---|---|
| 模型 | `deepseek-ai/DeepSeek-V4.1-Flash`，现有独立 `.env.shadow` |
| 输入估算 | **7859 ≤ 8000**，余量 141；既有 `utf8_bytes_div2_plus1024_v1`，包含系统提示、全量模型投影和 Schema |
| 输出参数 | **2500**，`enable_thinking=false` |
| 调用 / 重试 | 最多 **1 次**；SDK / HTTP 重试 0，无补跑 |
| 时限 | 外层 30 秒；SDK read/connect/write/pool 为 20/5/10/5 秒 |
| 条件费用 | **¥0.036**，按项目固定忙时输入 ¥2、输出 ¥8／百万，`(8000×2+2500×8)/1000000`，无缓存优惠 |

费用使用当前项目已保存费率作条件计算，本轮未查询供应商价格、账户或 token 端点。估算不是原生 tokenizer 计数，¥0.036 也不是账单硬上限；实际 token、服务端生成/思考口径、超时后的计费或 usage 缺失均可能使账单未知或超过条件预算。模型别名也不是不可变快照。

## 运行与验收

仓库根目录可安全执行的离线预检：

```powershell
.venv/Scripts/python.exe -B scripts/run_rag_shadow_v3_independent.py preflight
```

以下为**另行授权后才可执行一次**的真实命令，本轮未执行：

```powershell
.venv/Scripts/python.exe -B scripts/run_rag_shadow_v3_independent.py run --authorize-fixed-v3-shadow --accept-estimated-token-risk
```

真实运行会在本地私有目录先保存冻结预期，再发送只含固定输入的请求。来源准备先使用 id/question/quote/review_text 白名单，标签、理由、引用标准和 veto 注释均不进入模型 payload 或真实 JudgeFactory 配置。

结构通过必须同时满足原 B1/B2、来源身份和原文区间；非法引用原样失败，不修复坐标。结构失败只保留现有受限诊断，`validated_label_count=0`、`label_match_count=null`、混淆矩阵为零、正式准确率 N/A，不输出通过校验的 judgments。

结构通过后才比较标签。标签不匹配立即记录 `fixed_label_mismatch` 并停止，不修改预期、不再调用。即使五条匹配，仍要按上述冻结标准独立阅读实际依据；`semantic_basis_review=pending_independent_reading`，`formal_accuracy=null`，不会把标签匹配自动当成语义通过。语义依据不充分也只能记录失败，不能通过重新标注或调用消除失败。

## 离线证据与验证

[新增 12 项测试](../tests/test_rag_shadow_v3_independent_entry.py) **12/12 通过，11.407 秒**；真实 SDK/B2 子进程只连 [Mock HTTP](../tests/fixtures/shadow_v3_http_mock.py)。父、子进程测试均屏蔽真实网络、PostgreSQL 和 BGE。覆盖新文本独立性、预期污染不影响发送内容、完整来源、固定摘要和预算、v3 请求/usage 绑定、活动 v2 不变、合法四状态引用、非法引用/缺失反驳拒绝、code point 与 UTF-16 区别、单次占用、结构失败不评分、语义标签失败和 SDK 错误不重试。

原有回归也已复核：132 项在沙箱内通过（73.101 秒），私有原文不可读导致 `FrozenRealFailureTests` 整类暂跳过；随后获准在沙箱外只读访问既有诊断，该类 **4/4 通过（0.392 秒）**。合计原有 **136/136**、本轮 **148 项不同测试通过**，没有剩余跳过项。没有运行真实实验，未修改历史原文或 ACL。

复核命令：

```powershell
$env:PYTHONPATH='src;tests;.'
.venv/Scripts/python.exe -B -m unittest test_rag_shadow_v3_independent_entry -v
.venv/Scripts/python.exe -B -m unittest test_shadow_reference_semantic_regression test_rag_shadow_single_entry test_shadow_failure_diagnostics test_rag_shadow_quality_entry test_shadow_quality_plan test_shadow_semantic_boundaries test_review_shadow_siliconflow test_siliconflow_schema_probe test_review_shadow_openai test_p5b1_structured_evidence_design test_review_structured_shadow -q
# 上一命令若因私有诊断权限跳过冻结类，获准只读访问后单独运行：
.venv/Scripts/python.exe -B -m unittest test_shadow_reference_semantic_regression.FrozenRealFailureTests -q
git diff --check
```

上述固定输入、Mock 产物与测试是证据，能确认准备和失败边界可工作；真实语义改善仍无观测，下一步只能是独立授权后的一次验证。没有页面改动，无需前端构建或浏览器布局检查。

文件变更仅为新报告、新入口、新测试、新 Mock，以及纳入本次冻结的既有未跟踪 E81–E85 草稿；全部既有跟踪文件保持不变。Python 语法与差异空白检查通过。

## 待独立授权的范围

使用现有独立 SiliconFlow 密钥，向上述模型和 **v3 新实验提示**发送 **E81–E85 五条虚构评论的 quote、完整评论、固定问题/事实契约及必要 B1 来源字段**，最多一次付费请求；不发送冻结预期。需接受估算不准确、超时/usage 缺失、费用可能超过 ¥0.036 及服务别名漂移的风险，并允许在新私有目录保存现有保护范围内的失败原文和诊断。授权范围不含真实评论、数据库、Embedding、D71–D75 重跑、自动重试、引用修复或正式发布。

**准备完成后停止；真实效果、实际 token、延迟和账单均待这次独立授权。**
