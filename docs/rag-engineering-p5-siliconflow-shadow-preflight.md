# SiliconFlow 首次 LLM Shadow：兼容改造与离线预检

2026-11-06。**最小适配和离线验证已完成，真实调用仍被阻断，未执行 S1/S2/S3。** 独立密钥已配置，但不能据此宣称该模型已兼容严格 Schema，或已具备可保证的收费上限。本轮没有调用 SiliconFlow API（也未调用 models/账户探针）、连接业务数据库、加载 Embedding 或发送真实评论。

## 实际修改

| 文件 | 修改原因 |
|---|---|
| 新增 `src/data_analysis_agent/agents/review_shadow_siliconflow.py` | 固定模型/地址的独立 Factory、文件专用凭据读取、请求参数、响应限长、usage 检查及兼容性阻断 |
| `scripts/run_rag_shadow_first.py` | 增加显式 provider 选择；CLI 默认 SiliconFlow；保留 `--provider openai`，固定三题和范围不变；预检不通过时，在检索和输出目录创建前停止 |
| 新增 `tests/test_review_shadow_siliconflow.py` | SiliconFlow Mock HTTP、真实本地子进程及阻断测试 |
| 新增 `tests/fixtures/shadow_siliconflow_mock.py` | 纯合成 SDK 响应/挂起/异常夹具；不代表真实模型语义 |
| `tests/test_review_shadow_openai.py` | 一项配置测试改读临时合成 OpenAI 配置，避免依赖用户已经切换 provider 的真实 `.env.shadow` |
| 本报告 | 兼容性结论、预算及后续执行边界 |

原 `review_shadow_openai.py` 保持字节不变。复用其不含厂商参数的系统提示、B1 Schema 机械投影和 HTTP 限长实现；未新增依赖、Schema、Agent 钩子、审批或历史认证机制。

调用方式仍为 RuntimeQueryContract → P0 本次来源验证的 SemanticResult → B2 白名单 → Factory/一次性 worker → B1/B2 确定性检查 → 独立 ShadowOutcome。原始来源、Unicode 区间、事实×候选完整性、关系见证及逻辑目标校验均保留。`publication_allowed=false`、`semantic_verified=false`、正式许可集合为空；v2、SQL、回答和历史读取不消费新输出。

## 模型与严格输出兼容性

| 项目 | 本轮核实结果 |
|---|---|
| Provider/API | SiliconFlow 国内站，Chat Completions；`https://api.siliconflow.cn/v1/chat/completions` |
| 固定请求模型 | `deepseek-ai/DeepSeek-V4.1-Flash` |
| 官方公布服务修订 | `DeepSeek-V4.1-Flash-1017`，2026-10-21 升级；ID 未变。这是文档依据，**本轮未观察实际服务响应，不宣称不可变快照** |
| API 层格式 | 官方 Chat API 列出了 `json_schema`，不再笼统声称平台只能 JSON Object |
| 此模型 + strict + 原 Schema | 尚无足够的模型级承诺或实际请求证据；嵌套 `$defs/$ref`、约束及 `strict=true` 的完整支持仍待确认，**不是已证实不支持，也不是已兼容** |
| JSON Object | 未实现降级；400/其他失败立即停止，不改格式重试 |
| tokenizer | 未核实与该服务修订对应的词表、chat template 和 Schema 计数规则；现有 OpenAI `o200k_base` 缓存不复用、不自动下载替代词表 |

版本依据为[官方升级公告](https://docs.siliconflow.cn/docs/release-notes/overview)。平台 API 支持项与模型兼容性须分开理解；[Chat API](https://docs.siliconflow.cn/docs/api/chat-completions-post)列出 Schema 格式，但[JSON 指南](https://docs.siliconflow.cn/docs/userguide/guides/json-mode)示例仍为 JSON Object，不能用该示例证明原 strict Schema 兼容。

发送参数准备为 `max_tokens=2500`、`enable_thinking=false`、`n=1`、`stream=false`、温度 0、原 Schema 的 `json_schema/strict=true`。SDK 用 `extra_body` 传入 `enable_thinking`，不照搬 `reasoning_effort=none`、`max_completion_tokens`、`store` 或 `service_tier`，不启用工具。

官方说明 `max_tokens` **不包含思维链**；推理指南还指出部分模型达到 `thinking_budget` 后可能继续思考。因此没有把 `thinking_budget=0`、随意填写思考额度或单独 `max_tokens=2500` 宣称为总输出硬限额。`enable_thinking=false` 对此模型的行为尚待验证。[参数说明](https://docs.siliconflow.cn/docs/api/chat-completions-post)、[推理模型说明](https://docs.siliconflow.cn/docs/userguide/capabilities/reasoning)

当前三个阻塞码：`model_strict_schema_unverified`、`native_tokenizer_binding_unverified`、`model_non_thinking_budget_unverified`。它们不由 dotenv/CLI 的批准布尔值解除；真实 Factory 在创建 SDK 客户端前拒绝。MockTransport 是只能在代码中注入的无网络测试端口，工厂配置拒绝 `transport`、`encoding` 或 `allow_unverified` 等额外字段。

## 配置、超时与数据保护

- `.env.shadow` 存在；`SHADOW_MODEL_ID`、`SHADOW_BASE_URL` 与本轮固定值一致，`SHADOW_API_KEY` 非空。只核对配置，不验证余额或 key 的远端有效性。
- 不读取主 Agent 凭据作为后备，也不接受进程环境中的 `SHADOW_API_KEY` 覆盖文件。关闭 dotenv 插值。父进程只传配置文件路径和其摘要，worker 从同一字节版本读取 key；密钥不写入参数、模型输入、usage 或报告。
- 原 `SHADOW_TOKENIZER_CACHE` 字段未修改；它的存在不等于 DeepSeek tokenizer 可用。主 `.env`、`.env.shadow` 均保持原字节。
- SDK 20 秒（connect 5、write 10、pool 5）、外层 30 秒，`max_retries=0`；HTTP body 上限 131072 字节、模型正文 65536 字节。请求仅允许固定 HTTPS Chat endpoint，不跟随重定向，不继承 HTTP 代理或 TLS key log。
- B2 原子进程 deadline/cancel/kill/wait/管道释放不变；本地关闭不保证远端推理或计费被撤销。这是生命周期隔离，不是额外 OS 权限沙箱。
- 来源失败、模型漂移、意外思考内容、usage 冲突、截断、拒绝、工具调用、非法 JSON、未知事实或错引用均失败关闭。成功 JSON 和合法引用仍不证明语义正确。

## token 与金额预算

以下为 **SiliconFlow 国内站人民币价格**，不是国际站、DeepSeek 官方或旧 V4-Flash 价格。2026-11-06 核对[官方价格页](https://www.siliconflow.cn/pricing)，单位为元／百万 token：

| 北京时间 | 未缓存输入 | 缓存输入 | 输出 |
|---|---:|---:|---:|
| 00:00–02:00、08:00–24:00 | 2.00 | 0.04 | 8.00 |
| 02:00–08:00 | 1.00 | 0.02 | 4.00 |

在“输入不超过 8000，**包括思考在内的总生成量**不超过 2500，缓存全部未命中”的条件下：

- 忙时单次 `(8000×2+2500×8)/1000000 = ¥0.036`；三次 **¥0.108**。
- 闲时单次 **¥0.018**；三次 **¥0.054**。预算不依赖命中缓存或闲时优惠。
- **这只是条件估算；目前可保证的实际最高费用为 N/A。** 原 `$0.06` 不适用。本轮没有批准或发生付费模型请求，也未自动设置账户消费限额。

当前本地 token 数及计数误差均为 **N/A**：没有已验证的服务 tokenizer/template，不用中文字符数、字节数或 OpenAI 词表冒充。测试注入的 SyntheticEncoding 仅验证超预算分支，不用于真实预算。

后续应核对响应 `usage.prompt_tokens/completion_tokens/total_tokens`、`prompt_tokens_details.cached_tokens` 和兼容缓存字段；可用时检查 `completion_tokens_details.reasoning_tokens`。官方响应示例将 reasoning 放在 completion 的明细中，不能再重复相加；本实现对非思考请求收到 reasoning 时直接失败。**仍需确认该模型实际 usage/收费口径；未知思考量不能当作零。** 当前保存的 `cost_cny_at_peak_rates` 仅为按忙时费率估算，缓存信息缺失按未命中保守估计，不是精确账单。[官方响应字段](https://docs.siliconflow.cn/docs/api/chat-completions-post)

取消、网络失败、usage 缺失、服务端封装差异、价格变化和跨计价时段均可能使本地无法事先保证收费。强制终止而无回执的请求费用必须保留未知，不能填零；真实账单需与供应商回执核对。解决这些预算边界前不放行真实数据调用。

## 三题及后续授权范围

| ID | 固定原问题 |
|---|---|
| S1 | 有没有制冷不足的反馈？ |
| S2 | 有没有制冷效果好的反馈？ |
| S3 | 是否有客服态度差的反馈？ |

只保留 `reviews_csv / reviews-csv`、商品 `100013588055`、UTC `2021-09-11` 至 `2022-08-26`，既有标注身份、本地 BGE-M3/1024 维及 pgvector 索引，每题 Top-5。最多三次，每题一次，不重试、不换题。若未召回预期的否定/无关上下文现象，记录未覆盖。

后续真实执行需要的读取与发送边界：

1. 最多三次本地查询编码及 PostgreSQL 只读来源检查。P0 会读取范围内完整有效语料和必要状态，再批量补齐候选全文，**不是只读取 15 条观点**。此前 510 条评论、1091 个有效观点及 200 条向量只是旧实测，本轮未查询更新数量，不扩大索引。
2. 向 SiliconFlow 发送固定问题、活动契约及版本、每题 Top-5 的 quote、对应完整评论、绝对字符区间、rank/key、review/annotation/product/date、来源身份/原文/快照/label profile/file hash 及行引用。最多 15 个排名条目、至多 15 条关联评论，重复会更少；不发送全语料、向量、人工答案/评分、v2 结果、相似度、标注 endpoint 或凭据。
3. 完整评论仅解释或否决 quote；不截断上下文后放行。当前没有读取真实候选，因此无法列出将发送的具体评论文本，也未声称三题现象已覆盖。
4. 三题来自各自当前观察事务，不升级为历史可信来源；所有结果存 Git 忽略私有目录，不修改正常 Agent 的状态、许可、回答或历史。
5. 这是将评论及上下文交给第三方处理。[供应商收集清单](https://docs.siliconflow.cn/docs/legals/personal-information-collection-list)列有推理/计费日志及内容安全审核中的 prompt/输出；未取得零保留承诺，不能沿用 OpenAI 的数据条款或声称不留存。

已实际运行的离线命令：

```powershell
.venv/Scripts/python.exe -B scripts/run_rag_shadow_first.py preflight --provider siliconflow --shadow-env .env.shadow --business-env .env
```

结果为 `blocked_compatibility`，key/db/rag 均为“配置存在”，不是远端可用性或真实质量通过。

只有上述兼容性/token 预算得到可验证依据，并获明确的数据发送与费用授权后，才使用以下真实执行命令；**本轮未执行，当前代码会在任何检索前阻断它**：

```powershell
.venv/Scripts/python.exe -B scripts/run_rag_shadow_first.py run --provider siliconflow --shadow-env .env.shadow --business-env .env --output-dir outputs/rag-semantic-retrieval/first-siliconflow-shadow-v1
```

如必须靠真实请求确认 strict Schema/思考行为，可另行明确授权不含评论的纯合成兼容性探测及其收费边界；本轮没有执行，也不擅自将它增加为第四次实验调用。兼容性证据不是修改 `human_confirmed`、放宽原 Schema 或开启 JSON Object 降级的替代品。

## 验证结果与停止结论

日志位于 Git 忽略目录 `outputs/rag-semantic-retrieval/p5-siliconflow-preflight-20261106/`。合成 SDK 返回 unknown 是程序夹具，不能证明真实 DeepSeek 识别事实、改善过拒或避免误许可。

首轮专项 31 项中一项测试错误地期待新状态 `timed_out`；已改为断言既有 B2 的 `model_failed` 和 `deadline_exceeded`，生产状态机未改。原始诊断保存在 `initial-test-failure.txt`。修正后 SiliconFlow 17/17、原 OpenAI 14/14，合计 **31/31** 通过。

已验证 Mock HTTP 成功、400/401/403/429/500、超时、非法 JSON、未知事实/越界 Unicode、usage/缓存冲突、过大响应、配置漂移、无密钥后备、无格式降级、固定三题/范围、真实本地子进程退出/超时/主动取消及资源释放。正式真实请求路径始终阻断；没有借测试注入向真实网络发送数据。

适用后端回归 **582/582 通过**（包括上述 31 项，不重复累加），覆盖正式 Agent、SQL、v2、P0 来源、引用、历史读取和 B1/B2 协议。两项旧队列测试仍各报 `持久标注队列只支持 PostgreSQL`，为既有 SQLite 夹具与生产队列要求不符；单独保留 **0/2 通过、2 errors**，未改业务逻辑。

本轮真实 Windows 子进程的合成 HTTP 样例，均已退出并释放管道线程（一次观察，不是性能结论）：

| 场景 | PID | 退出码 | 总耗时约 s | 实际结果 |
|---|---:|---:|---:|---|
| 正常 Mock SDK | 5436 | 0 | 1.110 | validated_shadow，仍不发布 |
| 无限 HTTP 等待到期 | 19788 | 1 | 2.015 | model_failed / deadline_exceeded，强制终止 |
| HTTP 已开始后主动取消 | 31820 | 1 | 1.110 | cancelled，强制终止 |
| 进程异常退出 | 548 | 17 | 1.078 | model_failed / worker_exited |

实际命令：

```powershell
$env:PYTHONPATH='src;tests'
.venv/Scripts/python.exe -B -m unittest test_review_shadow_siliconflow test_review_shadow_openai -v
.venv/Scripts/python.exe -B outputs/rag-semantic-retrieval/p5-siliconflow-preflight-20261106/run_checks.py
git diff --check
```

首轮完整回归在受限环境的 TestClient 用例停滞，已中止并保留 `regression-sandbox-interrupted.log`；允许 Windows 本机内部 socketpair 后同一离线入口完成。入口持续拦截业务 PostgreSQL 和外部 HTTP，SDK 子进程仅注入 MockTransport，没有业务/模型网络连接，也没有系统配置修改。

修改前后核对 123 项源码、Schema、配置及原脚本/测试文件：仅脚本及配置测试这两项按表修改，其余 **121 项字节不变**，包括原 OpenAI 适配器、三份 Schema、正式源码、`.env` 和 `.env.shadow`。原已暂存改动保持原样；没有读取私有人工参考/历史归档来重新认证。私有日志、回执及配置均为 Git 忽略范围；前端未修改，未重做浏览器验收。

**本阶段到预检停止：不运行真实 Shadow，不提供新的业务许可，不声称已得到真实语义效果。** 下一步只需解决已列出的模型兼容性和预算依据并取得真实调用授权，无需新增审批平台或历史认证阶段。
