# P5 Shadow 失败诊断修复

2026-11-06。已补齐**显式启用的开发 Shadow** 在结构校验失败时的受控原文留存与引用定位，纯离线验证通过。本轮没有 SiliconFlow、PostgreSQL 或 BGE-M3 真实调用，没有运行 Q1/Q2/Q3，也没有删除或改写已占用的实验目录。

## Q1 能证明的阶段与原文丢失原因

依据原 [Q1 回执](../outputs/rag-semantic-retrieval/four-state-synthetic-shadow-v1/Q1/shadow-execution.json) 和 [结果报告](rag-engineering-p5-four-state-shadow-result.md)：HTTP 200，Worker 输出 2771 字节，父进程报 `citation_span_mismatch`。原正文没有保存，**不能判断具体哪个候选、字段或模型偏移出错，也不能断定 Q1 混淆了相对坐标或 UTF-8 字节偏移**。

原执行顺序为：

1. SiliconFlow SDK 返回，Judge 检查 usage、模型身份、结束原因、非思考模式和正文大小，再解析严格 JSON。
2. Worker 检查返回类型及 UTF-8 输出字节数，正文经受限管道交给父进程；stdout/stderr 的其他 SDK 输出被抑制。
3. B1 `JudgeOutput` 验证 Schema，随后检查 request_id/input_sha256 及完整事实×候选矩阵。
4. 对每条引用先检查 key/source identity，再检查区间边界和原文切片；事实许可及关系检查继续在后面执行。
5. Q1 在引用切片检查失败。因此可按代码顺序确认此前的解析、Schema、输入绑定和矩阵检查已通过；不能宣称剩余引用、事实许可或关系全部通过。
6. 原 `audit_output` 把异常压缩为只有错误码的 `ShadowOutcome`。适配器只为 `validated_shadow` 构造 `judgments`，无效正文虽曾在父进程内存中，却没有返回给固定入口；provider sidecar 只保存 usage。调用结束后只剩字节数、SHA-256 和错误码。

旧 Q1 仍不可恢复，新代码不回填其正文或语义分数。

## 最小改动

| 文件 | 改动 |
|---|---|
| [review_shadow_diagnostics.py](../src/data_analysis_agent/agents/review_shadow_diagnostics.py) | 新增 Worker 私有失败捕获、脱敏、目录约束、安全权限和有界原子写入 |
| [review_shadow_siliconflow.py](../src/data_analysis_agent/agents/review_shadow_siliconflow.py) | 增加内存回调；在供应商身份/usage/大小检查后、JSON 解析前提供正文及已知密钥脱敏信息；默认无回调 |
| [review_shadow_worker.py](../src/data_analysis_agent/agents/review_shadow_worker.py) | 仅按父进程开发控制字段建立捕获器；在隔离进程内审计并保存失败诊断，保存失败被抑制 |
| [review_shadow_adapter.py](../src/data_analysis_agent/agents/review_shadow_adapter.py) | 增加可选 `development_diagnostic_dir`，默认 None；目录和后端封套仅经控制管道进入 Worker，不进入模型消息 |
| [review_structured_evidence.py](../src/data_analysis_agent/agents/review_structured_evidence.py) | 保持原校验条件、失败码和四状态不变，为异常附加私有定位信息；`failure_details` 仅由开发诊断调用收集 |
| [Mock HTTP 夹具](../tests/fixtures/shadow_diagnostic_http_mock.py)、[专项测试](../tests/test_shadow_failure_diagnostics.py) | 在父/子进程屏蔽真实网络、数据库和 BGE，使用实际 SDK 与实际 B2 子进程验证 |

没有新增或修改 B1 三份 Schema，也没有更改提示、输入预算、SDK 重试或业务 Agent 架构。固定实验入口未修改、未自动启用诊断；未来新实验须由后端显式传入全新的开发诊断目录，并另行取得真实调用授权。

## 留存边界与字段

`development_diagnostic_dir` 必须是仓库 **Git 忽略的 outputs/** 内尚不存在的绝对路径，父目录须存在；拒绝外部路径及现有 symlink/junction 路径组件。模型不能通过正文、契约或响应字段指定文件位置，文件名固定为 `failure.json`。这是现有同用户进程的私有文件保护，未将 Worker 变为 OS 安全沙箱。

原文上限为 `min(max_output_bytes, 65536)` 个 UTF-8 字节，整份诊断文件上限 **512 KiB**。只在失败时创建新目录：POSIX 目录 0700、临时文件 0600；Windows 在新空目录移除继承权限并仅授予当前用户 SID 完全控制，成功后才写数据，文件继承该 DACL。临时文件写完、flush/fsync 后用原子硬链接发布，目标存在即失败，最后删除临时链接；不会覆盖已有记录。若被强制终止，可能没有最终文件或留下私有临时文件，不能保证每次取消都完成落盘。

保存正文前替换已知 API Key（含 JSON 转义形式），并清理 Authorization 值、常见 api_key/password/database_url 值和数据库连接串。不收集 HTTP 头、SDK 异常原文、完整配置或数据库设置。若脱敏改变正文，`redaction_applied=true`；原字节数及 SHA-256 仍对应脱敏前文本，不能将脱敏文本当作逐字原件重放。

| 私有字段 | 用途 |
|---|---|
| request_id、input_sha256 | 绑定后端请求；不信任模型自报 ID |
| raw_bytes、raw_sha256、raw_model_text、redaction_applied | 有界失败正文、完整性线索及脱敏标志 |
| provider_failed、error_codes | 区分供应商阶段失败与 B1/B2 诊断结果；不替代父进程最终 outcome |
| section、judgment_index / relation_id | 定位事实判断或关系判断 |
| candidate_key、reference_type、reference_index | 定位可信候选和 support_refs/refutation_refs/context_veto_refs 中的引用 |
| start/end、allowed_start/end、quote_start/end、review_code_points | 对照模型坐标、允许边界和评论长度 |
| bounds_valid、text_matches | 区分越界与原文切片不一致；身份不符另给 key_matches/source_identity_matches |
| coordinate_unit、matches_if_quote_relative | 标明 code point 口径，并提供“按 quote 相对位置解释会匹配”的排查线索 |

目前记录**首个失败**。JSON/Schema 无法解析、输入绑定或矩阵不符时，可能只有错误码和原文，没有已确认的候选/引用定位；不从未验证字段猜测可信身份。模型漂移、SDK 失败、超限正文等在捕获点前已被拒绝，不留存其模型原文。非法 JSON 可在 provider 抛错前捕获，原有 `model_failed` 处理保持不变。

诊断对象标记 `evidence_status=unvalidated_diagnostic_only`。公开 debug_record 仅增加 `failure_diagnostic_saved` 布尔值，不包含路径、正文或私有细节。父进程仍独立执行原有权威审计；不读取诊断文件中的原文作为证据，也不将 Worker 的诊断审计结果当作许可。

## 坐标与失败关闭

支持/反驳引用必须位于 quote 的**评论全文绝对区间**内；context_veto_refs 可在同一评论全文范围内。全部使用 Python `str` 的 Unicode code point `[start,end)` 切片，没有先编码为 UTF-8，没有使用 UTF-16 单元或可见字形计数，也不做规范化、翻译或文字补全。中文和 emoji 的多字节编码不会改变该口径。

`matches_if_quote_relative=true` 仅说明该解释能在 quote 中匹配，**不证明模型意图、不修正偏移、不重试**。所有原来失败的引用仍失败；诊断写盘失败也不能产生 judgments。`publication_allowed=false`、`semantic_verified=false` 和空许可集合保持不变，不更新 v2 assessment。

## 离线验证与保护

适用组合回归 **121/121 通过，67.835 秒**，包含原 110 项和新增 11 项。最后补充诊断文件状态读取的 OSError 隔离及准确脱敏标志后，专项复跑 **11/11 通过，34.639 秒**。

覆盖合法引用、中文/emoji 绝对坐标、quote 相对坐标、UTF-8 字节偏移、越界、合法边界但文字不符、context_veto/support 坐标混用、多候选单条失败、非法 JSON、Schema/摘要/来源身份不符、模型指定路径注入、HTTP/正文过大、模型漂移、SDK 失败、HTTP 开始后取消、默认禁用、Worker 写盘失败、脱敏、Windows ACL、原子写入和禁止覆盖。SDK/超限/取消仍为原失败状态；引用错误仍是 citation_span_mismatch。现有回归继续覆盖 Worker 大小限制、deadline、关系约束、正式 Agent/v2 隔离和清理。

```powershell
$env:PYTHONPATH='src;tests;.'
.venv/Scripts/python.exe -B -m unittest test_shadow_failure_diagnostics test_rag_shadow_quality_entry test_shadow_quality_plan test_shadow_semantic_boundaries test_review_shadow_siliconflow test_siliconflow_schema_probe test_review_shadow_openai test_p5b1_structured_evidence_design test_review_structured_shadow -q
git diff --check
```

首轮专项有 1 个测试夹具错误：将超大正文直接塞入受 16384 字符限制的 JudgeFactory 配置；已改为在 Mock HTTP 内生成超限正文，保留现有配置大小限制。

保护检查：Git 的运行源码差异仅涉及上表四个已有模块，另新增诊断模块与测试；正式 Agent/v2、三份 Schema、旧入口和案例没有修改。Schema 等价、提示身份、原有输入估算和正式路径隔离由组合回归继续验证。固定案例 SHA-256 仍为 `5254fb26cad7c3df4476dc91ecb8c9679300dbe4fd5ed0b77fe8a65fffcce60b`。`git check-ignore` 确认诊断路径被忽略，`git diff --check` 通过。

原 Q1 目录仍存在，回执仍为一次调用、2771 字节、citation_span_mismatch，输出摘要仍为 `b84e1641ddd207291073a570f523ce6bba557fecfd410c40c701a9f5b9ba4327`，没有新增其缺失的 shadow-judgments.json；没有恢复或改写历史判断。测试私有文件仅在全新 `outputs/diagnostic-test-*` 临时目录生成并清理。

后续显式启用的新实验已具备正常完成结构失败路径的原文留存与首个引用错误定位能力；本轮未实测真实供应商或真实数据。Q1 原文无法补回，强制终止、磁盘故障或权限不足时也不能保证留存成功，但失败关闭不依赖留存。**本轮代码修复和离线检查完成并停止；新的真实实验须另行明确授权。**
