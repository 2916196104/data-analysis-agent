# SiliconFlow 单次合成兼容性探针：真实结果

2026-11-06。依据用户明确授权，先执行既有离线 preflight（`missing=[]`），再执行报告中的一次性命令。**唯一一次真实请求完成，HTTP 200，B1/B2 校验通过；没有第二次请求、重试、模型切换或 S1/S2/S3 调用。**

## 实际运行结果

| 项目 | 实测 |
|---|---|
| Provider / endpoint | SiliconFlow，`https://api.siliconflow.cn/v1/chat/completions` |
| 请求 / 返回模型 | 均为 `deepseek-ai/DeepSeek-V4.1-Flash`，身份检查通过 |
| 服务修订 | 返回的是上述模型 ID；`1017` 仍为既有公告依据，响应未独立证明权重快照身份 |
| 执行时间 | UTC 2026-11-06 09:32:23 至 09:32:27；北京时间 17:32，忙时 |
| 次数 / HTTP | 一次尝试，HTTP **200**，`max_retries=0` |
| 请求格式 | 完整既有 B1 strict 投影，`json_schema / strict=true`；没有 JSON Object 降级 |
| 返回 JSON | **通过 B1/B2**：请求/输入摘要、事实×候选矩阵、来源身份及引用区间均有效 |
| 结束方式 | `finish_reason=stop`，未截断 |
| 非思考设置 | `enable_thinking=false`；响应**没有提供 reasoning_content 字段**，`reasoning_tokens=0`，本次未观察到思考输出 |
| input / output / total | **992 / 319 / 1311 token** |
| 缓存输入 | **0 token** |
| 输出规模 | B2 接收模型正文 **781 字节**，低于限额 |
| Factory/SDK 阶段 | **3.985 秒**，含本地准备、HTTP 与解析，不是单独的服务端推理时间 |
| B2 Shadow 总耗时 | **4.907 秒**，包含上述阶段，不能重复相加 |
| 整条命令墙钟时间 | 工具记录约 **6.001 秒**，包含导入及前置步骤 |
| 进程清理 | PID 4828，退出码 0；`reaped=true`、`threads_released=true`，未强制终止 |
| 发布 | `publication_allowed=false`、`semantic_verified=false`，正式许可集合为空 |

虚构范围和请求体与准备材料完全一致：`SYNTHETIC / synthetic-only-v1`、商品 `SYNTHETIC-PRODUCT`，评论 `【虚构】制冷不足。`，单事实 `cooling_poor`、单候选，无必要关系。模型返回 `supported / direct_report`，支持引用为 `制冷不足`，绝对原文区间 **[4, 8)**；没有反驳或上下文否决引用，关系列表为空。

离线复核再次确认来源 key、source identity、事实 ID 和原文切片精确一致。该简单字面自述直接支持所问反馈；**未测试语义改写、否定、转述、假设、复杂关系或真实评论质量**，不据此扩展正式许可。

## 实际 usage 对应费用

按本轮沿用的[SiliconFlow 国内站价格](https://www.siliconflow.cn/pricing)，忙时未缓存输入 ¥2、输出 ¥8／百万 token，依据真实 usage：

`(992 × 2 + 319 × 8) / 1,000,000 = ¥0.004536`

这是**按实际 usage 和对应费率计算的费用估算**，不是已核对账户的实扣账单；没有额外调用账单/账户接口。按闲时费率对应 ¥0.002268，本次客户端发送/接收区间均在忙时，采用 ¥0.004536。思考量为 0，不重复加入 completion 计费。

本次本地保守 proxy 为 7933，供应商实际 prompt usage 为 992；二者口径不同，不能用本次差值推出通用换算比例或准确原生 tokenizer。模型响应及 usage 的一次观测，也不能提供任意业务请求的绝对扣费上限。

## 三项阻断的证据结论

| 阻断 | 本次证据与处理结论 |
|---|---|
| `model_strict_schema_unverified` | **已有实测依据解除“当前服务是否接受本 B1 Schema 并返回可校验结构”的未知**：strict 请求 HTTP 200，JSON 通过 B1/B2。证据限本次模型/Schema/单事实路径；不证明所有关系分支或语义判断正确，也不能独立证明供应商内部解码强制机制 |
| `native_tokenizer_binding_unverified` | **保留**。一次 usage 不证明 SiliconFlow 部署的词表、模板及 Schema 封装绑定，仍没有可靠的本地准确计数依据 |
| `model_non_thinking_budget_unverified` | **保留其业务预算保证部分**。`enable_thinking=false` 的本次功能表现已有正证据，reasoning 为 0；但不能凭一次小输入保证任意上下文的总生成量/收费硬边界 |

执行结果文件保留了代码运行时原有的三个阻断名称；本轮**没有改动 Factory 放行配置或业务代码**。上表是本次证据分析，不能把结果文件中的旧列表误读成“Schema 仍未测试”。剩余 tokenizer 与业务预算风险仍未解决，且本次授权不包含业务数据发送，因此**不进入 S1/S2/S3**。

## 保护检查与可复核材料

- 本轮只使用 `.env.shadow` 的独立 key，没有输出密钥。124 项源码、Schema、配置及两个实验入口的前后 SHA-256 **全部一致**；没有修改正式 Agent、reported_experience_v2、人工参考或历史裁决。
- PostgreSQL 读取、真实 BGE-M3、真实评论发送和业务 Shadow 调用均为 **0**。唯一模型输入为既有固定虚构材料；没有探测其他模型或更换输入。
- 固定一次性目录和 HTTP 尝试记录均已保留，未清理或重新创建。没有再次运行一次性命令去测试拒绝行为。
- 实际执行了离线 preflight、单次真实调用、离线 B1/B2/引用复核、保护摘要检查及 `git diff --check`。本轮未修改代码，未重跑此前 597 项合成回归。

```powershell
.venv/Scripts/python.exe -B scripts/run_siliconflow_schema_probe.py preflight
.venv/Scripts/python.exe -B scripts/run_siliconflow_schema_probe.py run --allow-paid-synthetic-once
```

原始回执位于 Git 忽略目录 `outputs/rag-semantic-retrieval/siliconflow-synthetic-schema-probe-v1/`：`http-attempt.json`、`request-body.json`、`provider-usage.json`、`execution.json`、`validated-output.json`、`result.json`、`offline-result-verification.json`、`protection-check.json` 和 `artifact-digests.json`。完整模型输入、请求和输出均为虚构数据，不含凭据。

**执行已结束并停止；没有第二次 API 请求，没有自动进入 S1/S2/S3，也没有开启正式模型证据许可。**
