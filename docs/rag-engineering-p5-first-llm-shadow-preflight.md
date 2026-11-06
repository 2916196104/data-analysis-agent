# 首次受控真实 LLM Shadow：接入准备与运行预检

2026-11-06。**接入代码和离线验证已完成，真实实验尚未执行。现在停止在预检，等待用户明确授权。** 当前还缺独立 `SHADOW_API_KEY`；未连接业务 PostgreSQL、加载真实 BGE-M3、探测模型账号或发送评论。不能据此回答模型是否改善真实过度拒答、是否有错误许可或实际延迟/成本。

## 选定模型及费用预算

候选是 **OpenAI `gpt-5.4-mini-2026-03-17`**，固定快照，不使用可漂移别名。厂商官方文档列明 Chat Completions、Structured Outputs 及 reasoning effort `none` 支持；本次明确设置 none、无工具。标准文本价为输入 **$0.75**、缓存输入 **$0.075**、输出 **$4.50／百万 token**。这只是实验候选，尚无本项目语义效果证据。[OpenAI 模型文档](https://developers.openai.com/api/docs/models/gpt-5.4-mini)

| 项目 | 本次固定值 |
|---|---|
| POST endpoint | `https://api.openai.com/v1/chat/completions` |
| 模型 | `gpt-5.4-mini-2026-03-17`；响应模型不匹配即停止 |
| 输出模式 | `json_schema / strict=true`，复用 B1 输出 Schema 的机械投影 |
| 次数 | 最多 3 次；每题 1 次，不重试、不换题补足数量 |
| 每次输入预算 | 8000 token，包含消息、Schema 和 1024 token 协议余量的本地预检 |
| 每次输出限制 | `max_completion_tokens=2500`，包括可能的 reasoning token |
| 总输入/输出预算 | 24000 / 7500 token |
| 计价口径 | Standard/default，输入全部按未缓存价预算，不假定缓存优惠 |
| 按上限计算 | 单次 $0.01725；三次 **$0.05175** |
| 拟申请支出预算 | **$0.06 USD**，未实际支出 |
| 请求与资源 | SDK 20 秒（连接 5 秒），外层 deadline 30 秒；`max_retries=0` |
| 数据/输出上限 | HTTP 响应体 131072 字节，模型正文 65536 字节；超限失败 |

输入 token 检查覆盖完整评论和 Schema，不截断否决上下文。实际服务端消息封装不是永久不变的公开计数规则，本地 tokenizer 加余量属于保守预检，**不是供应商账户的硬扣费上限**；官方也将本地消息计数称为估计。[官方 token 计数说明](https://developers.openai.com/cookbook/examples/how_to_count_tokens_with_tiktoken)

每次响应核对实际 input/output/cache token，超预算或模型身份漂移立即停止后续调用；已发出的请求无法追溯撤销计费。最终费用按实际 usage 和运行当日价格记录，缺 usage/取消时标记未知，不能当作 $0。没有设置或宣称任何供应商账户扣费硬限额。

## 当前配置检查

- 主 Agent 现有 `Pro/deepseek-ai/DeepSeek-V3.2` / `api.siliconflow.cn` 配置可读；只核对是否存在，未输出密钥。
- Shadow 使用新 `.env.shadow`，**不回退到 `LLM_*` 或默认 `OPENAI_API_KEY`**。新文件已创建在 Git 忽略范围，key 留空，主 `.env` 字节未变。
- 独立项为 `SHADOW_MODEL_ID`、`SHADOW_BASE_URL`、`SHADOW_API_KEY`、`SHADOW_TOKENIZER_CACHE`。前两项固定为本报告值，词表路径指向项目 `.cache/rag-shadow-tokenizer`。
- 本地已有 OpenAI SDK 1.109.1、httpx 0.28.1、tiktoken 0.14.0，无安装或升级。
- 下载的唯一辅助资产是官方公开 `o200k_base` tokenizer 词表，**3613922 字节**，SHA-256 为 `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d`，与已安装 tiktoken 的固定摘要一致。不是模型权重，没有评论数据上传；运行时缺缓存/摘要不符则失败，不自动下载。
- RAG/PostgreSQL 配置项已存在；没有连接验证当前内容、索引数量、账号余额或模型访问权限。现有标注 profile 摘要为 `1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304`，真实读取仍由 P0 检查有效来源和空间。

当前离线预检结果为 `configuration_incomplete`，唯一已报告缺项是 `SHADOW_API_KEY`；真实运行授权也仍未取得。请只在本地配置密钥，不在聊天中发送。

## 三个固定问题与覆盖边界

| ID | 原问题 | 检查目的 |
|---|---|---|
| S1 | 有没有制冷不足的反馈？ | 明确买家自述的识别、正常事实/引用检查 |
| S2 | 有没有制冷效果好的反馈？ | 在正向候选里检查无关上下文是否导致 v2 过度否决，以及 LLM 是否正确处理 |
| S3 | 是否有客服态度差的反馈？ | 重点核查对象、否定、转述、假设和上下文冲突，防止无依据支持 |

三个目标均由现有 RuntimeQueryContract 产生，模型不生成目标，不允许临时改题或绕过 unsupported。**这是预定检查目的，不是已确认候选包含相应现象。** 本轮尚未读取真实候选；如果 S2 没有出现无关上下文否决，或 S3 没有否定/转述/假设，则必须报告该风险未覆盖，不能宣称通过，也不自动增题或改评论制造案例。

## 需要授权的真实读取与发送范围

本报告建议的执行授权范围为：

1. 仅 `reviews_csv / reviews-csv`，商品 `100013588055`，UTC `2021-09-11` 至 `2022-08-26`（含结束日）。沿用现有标注 profile、BGE-M3/1024 维、`quote-utf8-v1` 和当前已有向量，不扩索引。
2. 最多 **3 次 P0 语义检索/查询编码**，使用现有本地模型和 PostgreSQL 只读 RR 事务；一次模型初始化可在三题间复用。没有 SQL 统计题、向量同步、写库、标注或新模型下载。
3. P0 来源检查会读取该范围的完整有效语料及关联状态，并批量补齐候选全文，**不能把数据库读取范围表述为只有 Top-5**。此前 P3-B 实测为 510 条评论、1091 个有效观点、200 条向量；本轮未重查，不能把旧数量冒充当前数量。
4. 发送目的地明确为 **OpenAI**。每题仅发送当前 Top-5 的 quote、精确区间、对应完整评论、来源引用元数据、固定问题及活动契约；最多 15 个排名条目、最多 15 条关联评论（实际可能重复而更少）。不发送全语料、向量、人工参考/评分、主 Agent 密钥或数据库凭据。标注 endpoint 从模型输入移除。
5. 三题可属于不同当前数据库事务，分别记录来源观察/摘要；不宣称构成不可变历史快照。源数据冲突、空候选、输入不合格、超时或输出错误时停止，不重试、不扩大范围。

HTTP 设置 `store=false`，但它不能被描述为零保留；官方说明标准 API 滥用监控日志通常可保留 30 天，具体取决于账户数据控制。[官方 API 数据控制](https://developers.openai.com/api/docs/guides/your-data)

## 已实现的真实 Factory 与安全行为

新增 `src/data_analysis_agent/agents/review_shadow_openai.py`：

- 显式限定模型及 endpoint，只允许一次 `judge()` 调用。复用现有 OpenAI SDK，工厂与 StructuredEvidenceShadow 的原接口兼容。
- 沿用三份 B1 Schema；仅按厂商 strict 输出要求将已有可省略数组改为必须显式返回空数组，移除默认值。没有新增事实、关系或协议字段。[官方 Structured Outputs 约束](https://developers.openai.com/api/docs/guides/structured-outputs)
- 系统提示与评论 JSON 分离；评论、quote、源字符串被指定为不可信数据，不可作为指令。无工具、历史、外部检索或多轮推理。此隔离降低风险，**不能在真实实验前宣称已完全防止提示注入或语义错判**。
- 支持/反驳必须在 quote 内；完整评论仅解释或否决。所有事实 ID、来源、字符区间、关系见证继续由 B1/B2 后端检查，状态始终 `publication_allowed=false`。
- 禁用 HTTP 环境代理继承和 TLS key log；HTTP 响应读取先限长。空内容、拒答、截断、工具调用、非法 JSON、模型漂移或 usage 异常失败关闭，不自动改写后重试。
- 外层沿用 B2 的真实子进程终止/等待/管道回收；正常调用 close，取消则终止本地连接。远端已接收的计算和费用可能无法撤销，不能声称已取消远端任务。
- 用独立 usage sidecar 保存请求/输入摘要、模型、耗时、实际 token 和费用，不向 B1 输出 Schema 塞入元数据；不记录凭据或 HTTP 错误正文。被强制终止、来不及回传 usage 时明确保留未知。

正式 Agent 没有安装新钩子，v2 assessment、permitted 集合、引用、回答、历史读取及前端均不受此工厂影响。

## 命令与后续产物

以下预检命令已经执行，**完全离线，不会发模型请求或连接数据库**：

```powershell
.venv/Scripts/python.exe -B scripts/run_rag_shadow_first.py preflight --shadow-env .env.shadow --business-env .env
```

在配置独立 key 且用户明确批准上述数据读取、OpenAI 发送范围和预算后，才允许执行以下命令；**本轮未执行**：

```powershell
.venv/Scripts/python.exe -B scripts/run_rag_shadow_first.py run --shadow-env .env.shadow --business-env .env --output-dir outputs/rag-semantic-retrieval/first-live-shadow-v1
```

入口只支持固定三题和固定范围，没有自定义 question、商品或扩量参数；新输出目录必须在 Git 忽略的私有范围，且不能覆盖已有目录。每题保存 `contract.json`、`retrieval.json`、`v2-assessment.json`、独立 Shadow 执行/合法判断、`provider-usage.json`，最后保存已执行数量、已知费用和未知 usage 数。缺输出/失败的题目不会当作已完成；不会因没有发生目标现象而自动补题。

之后的语义复核要逐项查看原文支持、上下文、对象与否定作用域，尤其检查“v2 拒绝、LLM 支持”的候选究竟是规则覆盖不足还是 LLM 错误。只通过 JSON、哈希和区间检查不算正确；不根据模型输出生成新人工金标准。三例只用于首次程序和语义观察，不足以证明泛化或放行正式许可。

## 实际测试、变更与停止结论

- 新增 Factory、固定实验入口 `scripts/run_rag_shadow_first.py`、`tests/test_review_shadow_openai.py`、`tests/fixtures/shadow_openai_mock.py` 及本报告；没有修改既有源码或三份 Schema。
- 新接入专项 **14/14**；适用回归 **565/565** 通过（包含原 551 与新增 14；85 项子集不重复累加）。两项既有 SQLite/PostgreSQL 标注队列错误单独保留，未计为通过。
- 验证了实际 OpenAI SDK 请求的 fixed model、strict JSON、none、store=false、token cap、20 秒 SDK timeout、max_retries=0；429/超时只尝试一次，响应超限关闭流，实际 usage/cache 计费计算可复核。
- 用合成 HTTP transport 在真实子进程中验证正常回传及无限 SDK 请求的取消；PID 一致、进程/线程均回收。没有真实 HTTP 模型请求。原 B2 超时、异常退出等回归也通过。
- 验证 preflight 不创建模型客户端/检索服务，固定执行入口最多三题、失败停止且不重试；评论中的伪系统指令留在 data 中，不进入可信系统提示。
- 119 项既有源码/Schema/主 `.env` 字节摘要无变化；新 `.env.shadow`、tokenizer 缓存、测试和预检产物均在 Git 忽略目录。本轮没有重新读取私有人工评分或历史归档。

日志位于 `outputs/rag-semantic-retrieval/p5-shadow-preflight-20261106/`；执行过专项 unittest、全量离线回归、上述 preflight 和 `git diff --check`。完整回归仅为 Windows 内部 socketpair 在允许本机通信的环境执行，入口仍拦截业务 PostgreSQL 与外部 HTTP；没有改系统权限或业务数据。

本轮网络读取仅用于官方文档/价格和公开 tokenizer 词表。最初公开词表准备受环境 TLS key log/沙箱 DNS 限制，改用显式验证证书、无密钥日志的下载后成功；未安装模型、关闭 TLS 验证或发送评论。

**停止状态：准备完成，缺独立密钥及真实运行授权。** 模型稳定性、事实准确性、过度拒答改善、无依据许可、真实单次时延/费用与是否值得扩大样本，全部仍待授权后的实际结果；本阶段不发布虚构的“真实实验报告”，不启用正式 LLM 许可。
