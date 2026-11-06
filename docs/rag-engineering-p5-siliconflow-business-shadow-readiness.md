# S1/S2/S3 SiliconFlow Shadow：实测证据准入与运行准备

2026-11-06。**必要代码调整及离线预检完成，当前停在独立业务授权之前。** 本轮没有重新运行一次性探针、连接 PostgreSQL、加载真实 BGE-M3 或发送评论；没有安装 Agent 钩子或修改 v2。

## 三项兼容性条件

| 条件 | 最新状态及依据 |
|---|---|
| `model_strict_schema_unverified` | **已由保存的真实证据满足**：HTTP 200、返回模型一致、原 strict Schema 输出通过 B1/B2。读取 `result.json`、`provider-usage.json`、`offline-result-verification.json` 和 `request-body.json`，核对已固定的 SHA-256 后再检查身份/参数；不靠布尔开关声明通过 |
| `native_tokenizer_binding_unverified` | **仍未证明**。业务可在用户明确接受估算风险后使用版本化估算；`offline_tokenizer_ready=false`、误差上界 N/A 保持明确 |
| `model_non_thinking_budget_unverified` | **非思考功能有一次实测，费用硬上界仍未证明**。保留 `enable_thinking=false`；用户未接受风险前继续阻断；风险接受不等于获得供应商硬限额 |

证据只适用于 SiliconFlow 国内 Chat Completions、`deepseek-ai/DeepSeek-V4.1-Flash`、既有 B1 输出 Schema 及对应结构化/采样参数。代码将当前 Schema 与保存请求逐项比较，绑定 model、endpoint、已知服务修订、temperature、n、stream、enable_thinking 和整个 response_format。证据文件缺失/变更、已知模型修订变化或 Schema/相关参数变化时重新阻断，预算风险标记也不能跳过这些检查。

真实探针输出参数为 **768**；本次业务计划恢复原目标 **2500**，预检同时显示这两个值，不伪称已经测过长输出。当前服务 ID 是可漂移别名；本地无法识别供应商未公告且响应仍用相同 ID 的后台换权重，因此没有声称固定不可变快照。单事实探针不证明所有关系分支或语义判断正确。

本地摘要固定仅用于已有兼容性观测完整性，不是历史来源封存、人工批准或新的审计体系。原始探针及其结果文件不改写。

## tokenizer 与预算策略

重新核对了 DeepSeek 官方[1017 编码实现](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017/blob/main/encoding/encoding.py)及 SiliconFlow 的[Chat API 文档](https://docs.siliconflow.cn/docs/api/chat-completions-post)。上游有 tokenizer/prompt 编码资源，但没有获得 SiliconFlow 部署词表、消息模板及 Schema 封装与之完全一致的可验证绑定，也未找到该模型公开的可靠服务端计数接口。本轮未下载资源或安装依赖；现有主 Python 环境没有 tokenizers/transformers，不使用 BGE tokenizer 或 OpenAI 词表冒充准确计数。

业务估算方法明确为 **`utf8_bytes_div2_plus1024_v1`**：

`ceil(完整序列化请求 UTF-8 字节数 / 2) + 1024`

计入系统提示、原问题/契约、所有候选/完整上下文和完整输出 Schema；它是带余量的粗略估算，**未经供应商校准、没有可证明误差上界，极端文本仍可能低估**，不能作为计费硬限制。不根据一次探针的 7933/992 比值拟合，也不把估算写成实际 token。

为何调整：原“1 字节计 1 token +1024”用于一个短合成候选的完整业务请求就得到 **10100**（请求体 9076 字节），会使首次业务路径固定无法执行。新方法对同一合成输入估为 **5562**；这是估算政策变化，不是供应商能力升级，因此必须在用户单独接受该风险后才允许真实使用。原输入目标 **8000**、输出参数 **2500** 均未提高。

运行时估算超过 8000：在 HTTP 前拒绝整题并停止，不截断全文、不删候选、不降低 Top-5、不替换问题。响应后再次检查实际输入/输出、total、cache、reasoning、finish_reason 和模型身份；实际量超目标、思考出现、reasoning usage 缺失/异常、截断、模型漂移或来源/结构错误都停止后续调用。模型原文、引用矩阵和关系见证继续交给原 B1/B2 验证。正常结构输出却没有完整成功 usage 回执也停止，不能继续下一题。

按 2026-11-06 再次核对的[官方价格](https://www.siliconflow.cn/pricing)，未命中缓存且实际输入/总生成量分别不超过 8000/2500 时：

| 时间（北京时间） | 输入 / 输出 / 缓存（元/百万） | 单次条件估算 | 三次条件估算 |
|---|---|---:|---:|
| 00–02、08–24 | 2 / 8 / 0.04 | ¥0.036 | **¥0.108** |
| 02–08 | 1 / 4 / 0.02 | ¥0.018 | ¥0.054 |

不依赖缓存优惠安排预算。估算、max_tokens、30 秒本地 deadline 都不能提供供应商扣费硬上界；超时或取消可能仍收费，缺 usage 保留未知。2500 是最终输出参数，官方说明其不含思考；非思考虽已实测一次，业务仍保留意外思考停止检查。此风险需单独授权，本轮没有该授权。

## 固定实验、P0 读取与第三方发送

原问题和顺序不变：

1. **S1：有没有制冷不足的反馈？**
2. **S2：有没有制冷效果好的反馈？**
3. **S3：是否有客服态度差的反馈？**

固定 `reviews_csv / reviews-csv`、商品 `100013588055`、UTC `2021-09-11` 至 `2022-08-26`（含结束日），沿用当前配置标注身份、本地 BGE-M3/1024 维、既有 pgvector 索引、原候选排序和每题 Top-5。最多三次 LLM 调用，每题一次，不重试、不补题；提前失败可少于三次。

**授权需覆盖的实际数据库读取范围**：最多三次 P0 查询，各自使用只读 RR 快照；不是仅三条 SQL。现有 `read_effective_corpus()` 会读取上述范围的**完整有效语料及评论全文**、标注/修订状态、观点 key/quote/区间和导入来源字段，验证完整有效观点集合与快照；随后从既有向量检索 Top-5，批量再次读取候选评论全文及来源身份。还会核对同 source/dataset 下这个已选商品的必要 product_id，不读取其他商品目录。连接结束 rollback/close，编码按现有服务发生在事务建立之前。

P3-B 的旧实测为 510 条评论、1091 个有效观点、200 条向量；**本轮未查询更新数量**。不运行写库、向量同步、重标注、索引扩容或历史备份读取，也不宣称 200 个向量覆盖全部观点。

**向 SiliconFlow 发送**：每题原问题、活动 RuntimeQueryContract/版本/事实及必要关系，当前 Top-5 的 quote 和对应完整评论、绝对 Unicode 区间、rank/key、review_id/annotation_id、product/date、来源 identity/原文/内容/有效快照/label profile/file SHA 与行引用。最多 15 个排名条目、至多 15 条关联完整评论，可能重复而更少；完整评论仅用于解释或否决 quote。真实原文尚未读取，不能预先列出具体文本或保证 S2/S3 风险现象一定出现。

模型不接收全语料、向量、人工评分/裁决、v2 assessment、距离分数、标注 endpoint、主 Agent 凭据或数据库 DSN。原始 v2 判断独立保存，Shadow 的 unknown/支持/否决/关系与引用独立保存；后续须逐项人工看原文蕴含，不能凭 JSON/哈希称为语义通过。`publication_allowed=false`、`semantic_verified=false`，不更改正式回答、引用或历史。

## 实际代码与验证

- `review_shadow_siliconflow.py`：接入已保存兼容性证据；增加只针对 token/费用不确定性的显式风险字段及估算；保留所有来源/输出检查，补足 reasoning usage 缺失的停止条件。
- `scripts/run_rag_shadow_first.py`：保持原三题及范围；业务读取/发送授权与预算风险接受需同时具备；成功输出若缺 usage 则停止。
- 两份既有测试文件只更新旧阻断预期，并新增三项准入测试：证据/模型/Schema/参数漂移、风险接受不能绕过校验、两种授权及缺 usage 停止。未新增语义问题或扩大模型效果测试集。

已实际执行：

```powershell
.venv/Scripts/python.exe -B scripts/run_rag_shadow_first.py preflight --provider siliconflow --shadow-env .env.shadow --business-env .env
$env:PYTHONPATH='src;tests'
.venv/Scripts/python.exe -B -m unittest test_review_shadow_siliconflow test_siliconflow_schema_probe test_review_shadow_openai -v
```

专项 **49/49 通过**；适用回归 **600/600 通过**（包含上述 49 项，不重复累加）。两项既有 SQLite/PostgreSQL 队列错误单独重现为 **2 errors、0/2 通过**，仍为 `持久标注队列只支持 PostgreSQL`，未修改队列业务。实际 preflight 为 `strict_schema=verified_by_saved_probe`、`estimated_budget_risk_accepted=false`、`business_data_and_send_authorized=false`，当前缺项只有原生计数/总费用风险接受与独立业务读取发送授权，没有再次要求通过一个新的探针。

回归复现入口为 `outputs/rag-semantic-retrieval/p5-siliconflow-business-admission-20261106/run_checks.py`；仅为 Windows TestClient 本机 socketpair 使用允许本机通信的执行环境，入口拦截真实 PostgreSQL 和外部 HTTP。Mock 子进程沿用原有夹具，不执行真实探针或业务题。

124 项既有源码/配置/Schema/入口的摘要检查中，仅上述两个生产文件按计划变化，其余 **122 项未变**；两份测试文件另按表更新。原始探针的 **11 项产物摘要全部一致**，没有覆盖真实回执；`.env`、`.env.shadow`、三份 Schema、一次性探针脚本和正式 Agent/v2 字节不变。日志、实际 preflight 与保护检查位于同一 Git 忽略目录。业务输出目录尚未创建，本轮新增真实 API、业务数据库、Embedding 调用均为 **0**。

## 授权后可执行命令与剩余条件

下列命令仅在用户明确批准上述数据库读取、Top-5 quote/全文发送及估算/费用风险后执行；**本轮未执行**：

```powershell
.venv/Scripts/python.exe -B scripts/run_rag_shadow_first.py run --provider siliconflow --shadow-env .env.shadow --business-env .env --authorize-fixed-business-shadow --accept-estimated-token-risk --output-dir outputs/rag-semantic-retrieval/first-siliconflow-shadow-v1
```

两个 CLI 参数是执行者对本次限定授权的明确声明，不是模型开关、自动产生的审批记录或 `allow_unverified`。证据/模型/Schema 验证失败时，即便同时指定它们仍阻断；它们不会将 tokenizer/收费风险标记为已经验证。正常 Agent 没有加载这些配置。

**没有发现其他静态准入阻断。** 配置项存在不代表本轮重验了数据库、模型或语料；当前源版本/向量有效性、每题真实输入预算、候选覆盖和模型实际结果仍由授权后的正常运行检查，失败即停止。别名后台升级、估算误差、第三方实际账单和语义误判仍是明确风险，不额外建立审批平台或历史认证阶段。

本轮停止，等待业务数据库读取、评论发送和费用风险的单独明确授权。
