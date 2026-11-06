# 全新单请求四状态合成验证准备

2026-11-06。**准备及离线预检完成，真实调用未获授权、未执行。** 状态 `ready_pending_explicit_single_authorization`，`missing=[]`。本轮真实 SiliconFlow、PostgreSQL、BGE-M3 调用均为 0。

## 固定案例

[新夹具](../tests/fixtures/shadow_single_four_state_v1.json) 含 5 条全新虚构评论，D71–D75；旧 Q 组的评论和 quote 均未复用。共同目标事实为“评论作者本人曾体验虚构样机星芒-Y7的客服态度差”，原解析器生成 `service_bad`。合成来源 ID 为 71–75，使用既有 FakeRepository/FakeEncoder，经原 P0/B1 校验；不声称这是业务库或向量检索结果。

| ID | quote | 完整评论 | 预期及理由 |
|---|---|---|---|
| D71 | 我问退换流程时，客服不耐烦地打断我，还说别再问了。 | 虚构样机星芒-Y7记录：我问退换流程时，客服不耐烦地打断我，还说别再问了。包装没有破损。 | **supported**：本人客服负面体验，包装信息不撤回评价 |
| D72 | 客服耐心地把退换步骤讲清楚了，我对这次沟通很满意。 | 虚构样机星芒-Y7记录：客服耐心地把退换步骤讲清楚了，我对这次沟通很满意。这是我第一次联系他们。 | **not_supported**：相反的正面体验，没有否认或撤回此前差评 |
| D73 | 我昨天写的客服故意凶我是不实评价，我现在明确撤回这项差评。 | 虚构样机星芒-Y7补记：我昨天写的客服故意凶我是不实评价，我现在明确撤回这项差评。 | **refuted**：quote 内明确否认并撤回本人原评价 |
| D74 | 客服接电话时对我吼了几句。 | 虚构样机星芒-Y7初记：客服接电话时对我吼了几句。补充核实：当时接听的是安装预约员，我把角色弄错了；上面关于客服的抱怨撤销。 | **refuted**：同评论全文纠正对象、撤销原客服主张，用 context_veto_refs 否决 |
| D75 | 那位接电话的人语气很冲。 | 虚构样机星芒-Y7记录：那位接电话的人语气很冲。我无法确认他的身份，可能是客服，也可能是安排安装的人员，通话里没有说明。 | **unknown**：无法确定客服/安装角色，不能猜测或确定认定对象不符 |

预期在目标模型调用前由编码助手编写并固定，未由待测试 SiliconFlow 模型生成；它们是开发案例，未冒称独立人工金标准或留出泛化集。

夹具 SHA-256：`89dd2aef2d2ca5f5d1ddcb65d0c1d70b00943d8b00aa83cb6abbcde065b46fde`。

准备请求摘要：`600d1797517befb5cebb86ab3d67792c850d2fb614eff9cfe97a63a491d2fab9`。预检固定检查两个摘要，变化即阻断。请求准备只提取 id/question/quote/review_text，预期标签、理由、target_fact 和 veto_text 标注均不进入模型投影或正式 JudgeFactory 配置。污染所有预期字段后，模型 payload 仍逐字相同。

## 入口、预算与诊断路径

[新入口](../scripts/run_rag_shadow_single.py) 默认仅 preflight，复用原合成输入帮助函数、状态比较、SiliconFlow JudgeFactory、提示 v2、B1 三份 Schema、StructuredEvidenceShadow 和现有失败诊断模块。本轮没有修改这些实现或旧入口。

| 项目 | 固定设置 / 预检结果 |
|---|---|
| 模型 | deepseek-ai/DeepSeek-V4.1-Flash，现有独立 `.env.shadow` 配置 |
| 提示 | p5-shadow-siliconflow-instructions-v2 |
| 候选 / 请求 | 5 条，最多 **1 次**，SDK 重试 0，无补跑或自定义模型/问题/输出目录选项 |
| 输入估算 | **7816**，保持 utf8_bytes_div2_plus1024_v1，目标上限 8000 |
| 输出参数 | 2500，enable_thinking=false |
| 时限 | read/connect/write/pool=20/5/10/5 秒；外层 deadline=30 秒 |
| 条件费用预算 | **¥0.036**，按项目固定忙时输入 ¥2、输出 ¥8／百万，8000 输入和 2500 总生成 token、无缓存优惠计算 |
| 正式许可 | publication_allowed=false、semantic_verified=false、空许可集合 |

¥0.036 是条件预算，**不是供应商账单硬上限**。估算不等于准确 tokenizer；超时后 usage 可能未知，服务端思考/计费口径及实际账单也没有额外确认。本轮未查询账户或计数端点。

新的私有实验目录为：

```text
outputs/rag-semantic-retrieval/four-state-single-synthetic-v1/
```

创建 adapter 时已显式传入 `development_diagnostic_dir=str(OUTPUT_DIR/'diagnostics')`，失败文件固定为：

```text
outputs/rag-semantic-retrieval/four-state-single-synthetic-v1/diagnostics/failure.json
```

路径检查通过：新目录未占用、位于既有 outputs 私有边界内，`git check-ignore` 确认诊断路径被忽略。预检只验证计划路径；正式 run 才排他创建父目录，诊断子目录仅在需要留存失败时创建。**当前保留目录及诊断目录均未创建。** 离线 Mock 在另一全新临时目录中实际验证了该诊断路径机制。

任何已授权尝试都会保留一次性目录占用，包括异常和取消；再次执行在构造 adapter 前拒绝。不会删除目录、恢复重试或改用其他路径。失败原文继续沿用现有 65536 字节上限、512 KiB 文件上限、脱敏、私有权限和原子禁止覆盖写入。诊断落盘不保证在强制终止/磁盘故障时成功，父进程拒绝无效输出的判断不依赖落盘成功。

## 授权后判读规则

结构失败时记录原 outcome、usage、清理回执及诊断保存标志。若诊断成功，可从私有 failure.json 读取首个候选、引用字段和 Unicode 区间问题；不修正偏移、不把原文重新包装为 validated_shadow、不追加请求。5 条有效语义比较保留 N/A。

结构通过后，才比较 5 条标签与固定预期，保存逐条结果及四状态混淆矩阵。目标数量为 supported 1、not_supported 1、refuted 2、unknown 1，要求 5/5 标签匹配，并独立阅读实际引用与全文：D71 的支持、D72 相反体验与撤回的区别、D73 quote 内撤回、D74 全文纠正否决和 D75 角色歧义都须核对。模型理由不能自证，自动匹配也不自动关闭 `pending_independent_reading`。

预期引用边界仅供本地复核，不额外发送给模型：D71 quote `[12,37)`；D73 quote `[12,41)`；D74 quote `[12,25)`，全文撤回段 `[25,62)`。单位为评论全文中的 Unicode code point。完成或失败后立即停止，正式 Agent/v2 和人工参考保持原样。

## 离线验证

新增 [入口专项](../tests/test_rag_shadow_single_entry.py) **7/7 通过，6.019 秒**；现有 B1/B2 与诊断专项 **53/53 通过，42.998 秒**；本轮共 60 项不同离线测试通过，未声称重跑全部既有 121 项。

新专项使用真实本地 B2 子进程和 SDK Mock HTTP。验证固定案例/摘要、完整来源与 quote、四状态结构兼容、答案污染不影响请求、预检不构造 SDK 客户端、每次恰好一个 Mock 请求、显式诊断注入、引用失败定位、SDK 失败、未授权拒绝，以及成功/失败后重复执行均被拒绝。所有模拟输出由本地测试提供，不代表目标模型质量。

实际执行：

```powershell
.venv/Scripts/python.exe -B scripts/run_rag_shadow_single.py preflight
$env:PYTHONPATH='src;tests;.'
.venv/Scripts/python.exe -B -m unittest test_rag_shadow_single_entry -v
.venv/Scripts/python.exe -B -m unittest test_shadow_failure_diagnostics test_p5b1_structured_evidence_design test_review_structured_shadow -q
git diff --check
```

182 项既有运行源码、Schema、旧夹具、旧入口及历史实验文件的前后 SHA-256 均一致。仅新增本说明、新入口、新夹具和新测试；没有更改正式 Agent、v2、人工参考、原 Q1/Q2/Q3 或旧实验目录。

## 待授权命令与范围

以下真实命令**本轮没有执行**，须在用户明确授权后、仓库根目录运行一次：

```powershell
.venv/Scripts/python.exe -B scripts/run_rag_shadow_single.py run --authorize-fixed-single-shadow --accept-estimated-token-risk
```

需要的授权范围为：使用现有 SiliconFlow 独立密钥，向指定模型及提示 v2 发送 **D71–D75 这 5 条虚构评论的 quote、完整评论和必要 B1 来源字段，最多 1 次付费请求**；接受本地估算不准确、超时、usage 缺失和实际扣费超过 ¥0.036 条件估算的风险；允许在新的 Git 忽略私有目录中保存现有保护机制约束的失败正文及诊断。不包含其他案例、真实评论、数据库/BGE、旧实验重跑、自动重试或正式发布。

**准备阶段已完成并停止，等待明确授权一次付费合成验证。**
