# N1/N2/N3 第二轮 Shadow 运行准备

2026-11-06。固定入口、离线预检和测试已完成。**本轮真实数据库、BGE-M3 和 LLM 调用均为 0；正式实验目录尚未创建。等待新的数据库只读、第三方发送及费用风险授权。**

## 入口与执行命令

仅新增三个文件，没有修改原 S1/S2/S3 入口或正式业务模块：

- [固定入口](E:/AllProject/data-analysis-agent/scripts/run_rag_shadow_boundaries.py)：复用原配置预检、SiliconFlow Factory、P0、RuntimeQueryContract、B2 Shadow 和 v2 评估函数。
- [入口测试](E:/AllProject/data-analysis-agent/tests/test_rag_shadow_boundaries_entry.py)：合成来源、Fake 编码/仓储、真实本地子进程及 Mock HTTP。
- 本报告。

已实际执行的安全命令，默认不创建 Worker、不查询数据库、不加载模型、不发 HTTP：

```powershell
.venv/Scripts/python.exe -B scripts/run_rag_shadow_boundaries.py preflight
```

**以下真实命令仅供另行明确授权后使用，本轮未执行：**

```powershell
.venv/Scripts/python.exe -B scripts/run_rag_shadow_boundaries.py run --authorize-fixed-boundary-shadow --accept-estimated-token-risk
```

入口不接受问题、商品、日期、模型、Top-K、配置文件或输出目录覆盖参数。固定读取根目录 `.env.shadow`、`.env`，Shadow Key 只通过原独立配置接口取得，不打印或写入结果。`.env` 的主 Agent 凭据不提供给 Judge。

输出固定为 Git 忽略目录 `outputs/rag-semantic-retrieval/semantic-boundary-shadow-v2/`。开始读取/推理前以排他 mkdir 占用目录，文件排他创建；成功、失败或中断后均不允许重新运行覆盖，不能修改占用记录补跑。不会调用旧 `run_authorized()` 或改变旧 CASE 常量。

## 固定问题与范围

| CASE | 原问题（不改写） | 已解析事实 | 当前检索提示 |
|---|---|---|---|
| N1 | 是否有安装师傅态度很差的评论？ | installer_bad | 无 aspect/sentiment 硬过滤 |
| N2 | 有无客服态度很好的反馈？ | service_good | aspect=service，sentiment 不过滤 |
| N3 | 有没有噪音不大的反馈？ | noise_low | 无 aspect/sentiment 硬过滤 |

三题均由现有解析器产生 supported RuntimeQueryContract，仍由 P0 使用原始问题编码。固定 `reviews_csv / reviews-csv`、商品 `100013588055`、UTC `2021-09-11` 至 `2022-08-26`、每题 Top-5；最多三次查询编码、三次 LLM 尝试，每题至多一次，无重试、换题或补题。空候选记录 skipped_empty/零调用，继续原列表，不生成替代题。

标注 profile 固定为 `1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304`；EmbeddingSpace 固定为 `embedding-space-v1:11e6754662dbd13fc56c6e624bc9416c1c2766f962787267fd4a57b1fa8d622e`，沿用现有 BGE-M3/1024 维、quote-utf8-v1 及已有 200 条索引，不同步或扩容。本轮未连接数据库重新核对库存数量。

P0 沿用只读 REPEATABLE READ 事务中的完整有效语料与独立来源验证、原排序/区间/哈希校验；事务在 LLM 调用前释放。来源政策仍是 current_effective_readonly_v1，不升级为历史可信来源。

## 兼容性、预算与停止条件

本地实际预检结果：

| 项目 | 结果 |
|---|---|
| Provider/model/endpoint | SiliconFlow、deepseek-ai/DeepSeek-V4.1-Flash、https://api.siliconflow.cn/v1/chat/completions |
| 严格 Schema | verified_by_saved_probe；检查既有真实探针的摘要、模型/Schema/API 参数绑定，未重新探针或降级 json_object |
| 提示 | p5-shadow-siliconflow-instructions-v2；固定摘要 6d0e268c77b3dfc77956ec31b154ff5f04a84db34d15271f40c1ee93c85c130d |
| 本地配置 | 独立 Shadow Key、PostgreSQL、RAG 配置字段存在；密钥未输出，模型/数据库实际可用性本轮未实测 |
| 上下文 | quote_full_review_veto / 1；支持只来自 quote，全文仅解释/否决，不截断否决上下文 |
| 预算 | 每题输入估算目标 8000、max_tokens=2500、enable_thinking=false、max_retries=0 |
| 时限/大小 | HTTPX read=20/connect=5/write=10/pool=5 秒，外层 30 秒；沿用模型输出 65536 字节、HTTP 响应 131072 字节上限 |

当前 missing 为 native_tokenizer_binding_unverified、model_non_thinking_budget_unverified、fixed_business_data_and_send_authorization_required。前两项属于尚未取得本轮风险授权的预算不确定性，不是重新要求开发 tokenizer 或适配器。已有一次零思考观测不保证后续永远无思考；模型别名也不是不可变快照。

沿用 2026-11-06 已核对的[官方价格](https://www.siliconflow.cn/pricing)：忙时每百万输入/输出/缓存命中 ¥2/¥8/¥0.04。实际每题输入/输出均不超过 8000/2500、无缓存优惠时，每题 ¥0.036、三题 **¥0.108**；02–08 价格条件下三题 ¥0.054。这是条件估算，**本地 token 估算无已证明误差上界，max_tokens 与取消也不是供应商扣费硬上限**。原 S3 未知费用不混入新实验。

来源、输出、usage 绑定、模型/提示身份、思考量、finish_reason、费用或资源清理异常即停止后续题；异常不触发新调用。仅汇总校验通过回执的忙时费用估算，未核验的 usage/费用单列，不以零替代。达到条件预算后的停止只能限制后续调用，不能撤销已发生的扣费。

## 记录和数据发送边界

每题分别保存 contract、P0 retrieval、v2-assessment、shadow-execution、合法时的 shadow-judgments，以及子进程写出的 provider-usage。总表保留全部三个 CASE，未执行题标记 not_started。记录请求准备、SDK、响应验证、Provider/Shadow 总耗时，输入/输出/思考 token、模型身份、finish_reason、受限传输事件及费用估算；失败阶段没有测得的指标保留缺失/N/A。

未来授权需要覆盖该商品/日期/标注范围内 **P0 完整有效语料和来源元数据读取**，不只是 Top-5：现有 P0 会重建完整有效集合，再补齐候选对应原文；不额外读取其他商品、时间或人工裁决。前次读取规模不能冒充本轮已查询结果。

发往 SiliconFlow 的是每题问题/契约、Top-5 quote、同评论完整上下文、精确 Unicode 区间及必要来源身份字段，最多 15 个排名条目、至多 15 条关联评论（可能重复）。不发送 v2 判断、人工参考、历史裁决、cosine distance、向量、主 Agent 密钥或数据库凭据。

四状态输出只是模型主张。后续需逐候选阅读 quote/全文，区分直接支持、仅相反体验、明确纠正/撤回、角色不符、转述/假设及语义未知；每个 supported 都必须检查自然语言支持，不能只查 JSON/引用。未出现的风险记未覆盖/N/A，不换题或挑评论。新问题仍可能召回已见评论，不能称为独立泛化集。

## 实际测试与保护结果

```powershell
$env:PYTHONPATH='src;tests;.'
.venv/Scripts/python.exe -B -m unittest test_rag_shadow_boundaries_entry -v
.venv/Scripts/python.exe -B outputs/rag-semantic-retrieval/p5-n123-preparation-20261106/run_checks.py
git diff --check
```

- 新增 **8/8** 专项通过，覆盖固定范围/参数、离线无副作用、两项授权缺一即停、身份/预算漂移、三题合成全流程、一次性目录、空候选、缺 usage、模型/思考/截断/摘要/费用错误、来源失败、Ctrl+C 和清理异常；最后复测 8/8 通过。
- 适用回归 **614/614** 通过，包含新增 8 项，不重复累加。此前两项队列错误单独重现为 **2 errors、0/2 通过**，仍为 SQLite 夹具触发“持久标注队列只支持 PostgreSQL”，未改队列业务。
- 41 份本地合成进程回执均 reaped/threads_released，包括本轮新入口的 5 个 SDK+Mock HTTP 子进程。初次成功路径测试因夹具清空 Windows 系统环境触发 WinError 10106；已修正测试夹具，未修改生产 Worker。
- 修改前登记的 **242 项已有源码/Schema/测试/配置全部未变**；原 S1/S2/S3 的 23 项产物、原一次性探针 11 项产物摘要均一致。没有改正式 Agent、v2、P0、B1/B2、R0、B2 参考或历史记录。

日志、配置预检和保护摘要位于 [Git 忽略的准备目录](E:/AllProject/data-analysis-agent/outputs/rag-semantic-retrieval/p5-n123-preparation-20261106)。合成通过不代表真实语义质量通过。

**未发现其他静态工程阻断。** 当前只等待本轮业务读取/发送授权及 token/费用风险接受；真实候选覆盖、逐题预算、当前来源和服务响应需在获准执行时检查。publication_allowed=false、semantic_verified=false、正式许可集合为空，未安装 Agent 钩子。到此停止，不自动执行真实三题。
