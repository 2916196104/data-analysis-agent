# P5 四状态真实 Shadow：固定入口与离线预检

2026-11-06。**入口和离线检查完成；真实模型实验尚未执行。** 预检为 `ready_pending_explicit_authorization`、`missing=[]`。本轮真实 LLM、PostgreSQL、BGE-M3 调用均为 0；没有模型准确率或真实延迟结论。

## 交付与复用

- [固定入口](../scripts/run_rag_shadow_quality.py)：默认只做 preflight，固定 Q1–Q3，没有自定义候选、模型、输出目录或补跑选项。
- [入口专项测试](../tests/test_rag_shadow_quality_entry.py)：7 项离线测试。
- 本文：预检结果、执行边界与授权后分析约定。

原 [15 条夹具](../tests/fixtures/shadow_quality_cases_v1.json)、[方案测试](../tests/test_shadow_quality_plan.py) 与 [技术方案](rag-engineering-p5-semantic-quality-budget-plan.md) 均未修改。夹具 SHA-256 固定为 `5254fb26cad7c3df4476dc91ecb8c9679300dbe4fd5ed0b77fe8a65fffcce60b`。

入口复用现有 RuntimeQueryContract、B1、StructuredEvidenceShadow、SiliconFlow JudgeFactory、提示 v2、来源/精确引用/关系校验以及进程 deadline 和清理。合成来源沿用已有 `verified` 测试帮助函数的 FakeRepository/FakeEncoder，再经原 P0 校验；这不是业务库或真实向量检索结果。正式 Agent、reported_experience_v2、Schema、配置和旧实验产物未改动。

## 预检结果与请求隔离

| 批次 | 固定案例 | 候选数 | 输入估算 | 输出参数 |
|---|---|---:|---:|---:|
| Q1 | C01–C05 | 5 | 7695 | 2500 |
| Q2 | C06–C10 | 5 | 7631 | 2500 |
| Q3 | C11–C15 | 5 | 7750 | 2500 |

输入准备首先仅提取 `id/question/quote/review_text`，再构造固定契约和合成来源。`expected_status/expected_reason/veto_text/target_fact` 等本地评分字段不进入输入准备、模型投影或 JudgeFactory 配置。全文中原本的撤回/转述等文字完整保留，不是额外答案提示。

离线测试将所有预期、理由和 veto 标注替换成污染标记，得到的模型 payload 仍逐字相同；三个实际准备的请求也与原方案帮助函数输出完全一致。真实子进程只接收 B1 投影和原 SiliconFlow 配置，不接收评分表；预期在父进程本地比较时使用。

## 未来执行保护

只使用 `.env.shadow` 的现有独立配置。`run` 必须同时显式给出 `--authorize-fixed-synthetic-shadow` 和 `--accept-estimated-token-risk`；缺任何一个都在创建目录或 adapter 前拒绝。**本轮没有执行这两个授权参数下的 run。** 这是复用现有入口授权惯例，不增加审批系统。

固定结果目录为 `outputs/rag-semantic-retrieval/four-state-synthetic-shadow-v1`，本轮未创建。沿用原入口的排他目录创建与文件 `open('x')`：一旦正式尝试即占用目录，即使失败/中断也拒绝再次运行，不覆盖历史或自动恢复。每批仅执行一次 adapter，总计最多 3 次；原 SDK 重试为 0。

输入估算目标 8000、输出参数 2500、read/connect/write/pool=20/5/10/5 秒、外层 deadline=30 秒均保留。沿用前一方案忙时条件预算 ¥0.108，仍非账单硬限制。超时、Schema/引用/身份异常、意外思考、预算异常或清理失败停止后续批次；语义标签错误保留并继续剩余固定批次，不重试、不改预期。缺 usage 时实际费用未知。

## 获得明确授权后的分析

每批保存模型投影、合成来源、原契约、B2 执行回执、模型判断（如可用）、原 provider usage 和本地 comparison；汇总记录 15 条候选、预期与输出、混淆矩阵、批次状态、token/时延/费用及未执行项。

自动比较仅判定固定标签是否一致，记录 false_support、false_rejection、refutation_label_error、unknown_misclassification，以及相反体验误标反驳、转述/假设当亲历、角色混用、模糊否定被强判等适用类型。失败或结构不合法输出单列 N/A，不计作 unknown；原判断若被 B2 返回仍单独保存，不覆盖为预期答案。

**标签一致也不自动判定语义通过。** 每条记录保留 `semantic_basis_review=pending_independent_reading`。正式执行后必须独立阅读 quote、全文和实际引用，核对反驳是否真有否认/撤回/纠正、全文是否仅解释或否决、角色是否一致；不能用模型理由自证。随后生成简洁结果报告，给出四状态分项表现和是否值得扩大真实评论验证的判断。复杂关系本组为空，其真实语义质量应标 N/A。

无论结果如何，`publication_allowed=false`、`semantic_verified=false` 与正式许可隔离不变。

## 已执行的离线验证

```powershell
.venv/Scripts/python.exe -B scripts/run_rag_shadow_quality.py preflight
$env:PYTHONPATH='src;tests;.'
.venv/Scripts/python.exe -B -m unittest test_rag_shadow_quality_entry test_shadow_quality_plan test_shadow_semantic_boundaries test_review_shadow_siliconflow test_siliconflow_schema_probe test_review_shadow_openai test_p5b1_structured_evidence_design test_review_structured_shadow -q
git diff --check
```

**110/110 通过，耗时 33.819 秒**，包含原 103 项和新增 7 项。新入口测试屏蔽真实数据库、HTTP 和 BGE，真实本地 B2 子进程仅使用 MockJudge；验证了三批最多各一次、语义错误不触发重试、超时回收、无效输出后停止、再次运行拒绝、夹具漂移阻断和预期污染不影响请求。Mock 得分不代表真实 LLM 得分。首次测试有一处严格 Pydantic tuple 解析方式不符，已改用现有 JSON 协议解析后通过完整集合。

未改前端，未运行前端构建或页面验收。当前阶段完成并停止；下一步需要用户明确批准向 SiliconFlow 发送这 15 条虚构评论并接受费用风险。
