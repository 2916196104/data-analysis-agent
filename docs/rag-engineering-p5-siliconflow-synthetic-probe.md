# SiliconFlow 单次合成兼容性探针：授权前准备

2026-11-06。**准备及离线测试完成，真实 API 调用 0 次，等待用户单独授权。** 本报告不是实际模型实验结果，不启动 S1/S2/S3。

## 固定请求与执行限制

| 项目 | 固定内容 |
|---|---|
| Provider / 模型 | SiliconFlow / `deepseek-ai/DeepSeek-V4.1-Flash` |
| endpoint | `https://api.siliconflow.cn/v1/chat/completions` |
| 凭据 | 只从项目 `.env.shadow` 的 `SHADOW_API_KEY` 读取；不复制到其他配置、不输出密钥、不回退主 Agent |
| 实验 | `synthetic_schema_compatibility_only`，独立于三次真实评论实验列支 |
| 虚构范围 | `SYNTHETIC / synthetic-only-v1`，商品 `SYNTHETIC-PRODUCT`，日期 `2031-01-01` 至 `2031-01-02` |
| 问题 | 有无制冷不足的评论？ |
| 完整虚构评论 | `【虚构】制冷不足。` |
| quote | `制冷不足`，原文 Unicode 绝对区间 **[4, 8)** |
| 矩阵 | 1 个运行时目标事实 × 1 个候选；无必要关系 |
| 输出协议 | 完整 B1 输出 Schema 的既有 strict 投影，包括全部 `$defs/$ref`、引用与关系定义；未改为简单 yes/no 或 JSON Object |
| 次数 | 最多 1 次，不重试、不换模型、不换题；成功、失败或取消均不能用同一入口再跑第二次 |
| 参数 | `enable_thinking=false`，`max_tokens=768`，`n=1`，`stream=false`，温度 0，无工具 |
| 时限/限长 | SDK 20 秒，外层 30 秒；HTTP body 131072 字节，模型正文 65536 字节 |

只构造内存中的虚构来源，并用 P0 + FakeEncoder/FakeRepository 校验，再走原 B2 worker 与事实/引用检查。不连接 PostgreSQL、不加载真实 BGE-M3、不读取人工裁决、历史参考或真实商品评论；没有 Agent 钩子。

`publication_allowed=false`、`semantic_verified=false` 始终保留，正式许可集合为空。完整评论只作解释/否决；合法 JSON 和引用不代表一般自然语言蕴含已经验证。

## tokenizer、价格与预算风险

已找到 DeepSeek 官方发布的[1017 模型及 prompt 编码实现](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017)。这修正了“上游没有编码资源”的可能误解，但**未找到 SiliconFlow 对其部署词表、模板及 strict Schema 封装完全一致的可验证绑定，公开 API 目录中也未找到适用于此模型的可靠计数接口**；不能断言供应商不存在私有能力。本轮没有下载模型/词表、安装依赖，未用 `o200k_base` 冒充准确 tokenizer。

保守启发式为：**完整序列化请求的每个 UTF-8 字节计 1 token，再加 1024 协议余量**。系统消息、固定输入、完整虚构上下文和整个 strict Schema 均计入，不截断。

- 实际固定请求体 **6909 字节**，本地 proxy **7933 token**，低于本探针的 8000 proxy 检查值。
- 它不是原生 token 计数或数学上界，误差边界 **N/A**；供应商可能采用不同消息/Schema 包装，真实计费以 usage 和账单为准。
- 2026-11-06 重新读取[SiliconFlow 国内价格页](https://www.siliconflow.cn/pricing)：忙时（北京时间 00–02、08–24）输入/输出/缓存为 **¥2 / ¥8 / ¥0.04** 每百万 token；闲时（02–08）为 **¥1 / ¥4 / ¥0.02**。
- 假设输入为该 proxy、包括思考在内的总生成量不超过 768、全部未命中缓存，单次忙时估算 **¥0.022010**，闲时 **¥0.011005**。这是合成探针的独立估算，不混入三题 ¥0.108。
- **可保证的最高扣费仍为 N/A。** 官方 `max_tokens` 不含思考部分，`enable_thinking=false` 正是待验证项；超时终止本地进程不保证撤销远端计费。[参数与 usage 文档](https://docs.siliconflow.cn/docs/api/chat-completions-post)

授权后记录实际 input/output/total/cache/reasoning tokens；reasoning 是 completion 明细时不重复计费，缺字段保留未知。按供应商价格和实际 usage 计算忙/闲费率估算及发送/接收时段估算；跨时段或缺 usage 保留区间/未知，不能伪称精确账单。未取得账户级硬消费上限保证。

## 最小改动及离线验证

| 文件 | 作用 |
|---|---|
| `scripts/run_siliconflow_schema_probe.py` | 固定合成输入、离线预检、专用一次性入口；复用现有 SiliconFlow SDK/解析器，通过精确 payload 比较拒绝其他输入 |
| `src/data_analysis_agent/agents/review_shadow_siliconflow.py` | 少量内部复用方法；补齐安全的 HTTP 状态、模型、finish_reason、reasoning 是否存在/字节数、total tokens、UTC 起止时间记录；普通 Factory 的阻断保持不变 |
| `tests/test_siliconflow_schema_probe.py` | 新增 15 项合成测试 |
| `tests/fixtures/shadow_siliconflow_probe_mock.py` | 真实 SDK 经 MockTransport 的合法引用、错误、挂起和退出夹具 |
| 本报告 | 请求内容、预算、证据边界和执行命令 |

脚本默认仅 preflight；付费 run 需显式参数。固定私有目录以排他创建阻止重复运行，worker 另以排他 `http-attempt.json` 消费一次机会；失败不重建目录、不自动清理重试。没有 `--payload`、`--model`、`--max-calls` 或可改运行目录的扩量参数。普通业务 Factory 不接受探针开关，不能用本探针发送 S1/S2/S3 或任意真实文本。

专项 **46/46**（新增 15 + 既有 31）通过：验证原 B1 Schema 未变、合成 P0 来源和 [4,8) 引用、完整事实矩阵、固定输入、无凭据后备、HTTP 400、截断/漂移/非法 JSON、思考输出、未知事实/错区间、无重试/无 JSON Object 降级、一次性执行、实际本地子进程退出/超时/主动取消及回收。测试只用 Mock HTTP，没有付费请求。

最终适用回归 **597/597 通过**（已含上述 46 项，不重复累加）。两项旧 SQLite/PostgreSQL 队列测试单独重现为 **2 errors、0/2 通过**，错误仍为 `持久标注队列只支持 PostgreSQL`；未修改业务队列逻辑。补齐缺响应/缺 usage 时的 N/A 处理后重新执行完整回归，日志不是修正前的旧结果。

```powershell
.venv/Scripts/python.exe -B outputs/rag-semantic-retrieval/p5-siliconflow-synthetic-probe-preparation-20261106/run_checks.py
git diff --check
```

完整回归使用允许 Windows TestClient 本机 socketpair 的执行环境，测试入口拦截真实 PostgreSQL、外部 HTTP，SDK 测试仅注入 MockTransport；不扩大业务权限。实际子进程回执见 `os-process-receipts.json`，正常、异常、到期与取消均确认进程及管道线程回收。这不代表远端计费可撤销。

修改前后检查 123 项已有源码/Schema/配置/业务实验入口：仅现有 SiliconFlow 文件按表调整，其余 **122 项字节未变**，包括 `.env`、`.env.shadow`、三份 Schema、原 OpenAI Factory、正式 Agent/v2 及 `run_rag_shadow_first.py`。未读取私有人工裁决/历史参考来重新认证。真实探针目录仍不存在；准备材料和日志处于 Git 忽略范围。前端未修改，不重跑浏览器验收。

## 当前结果与授权后的判定方式

| 验收问题 | 当前实际结果 |
|---|---|
| strict Schema 是否被真实 API 接受 | **未执行，N/A** |
| 真实 JSON 是否通过 B1/B2 | **未执行，N/A**；合成 Mock 已通过，不替代真实结果 |
| 非思考模式实际表现 | **未执行，N/A** |
| 实际 input/output/reasoning tokens、费用和延迟 | **未执行，N/A**；真实调用数为 0 |
| 可否进入 S1/S2/S3 | **不可**；三个业务阻断均保留 |

授权后的 `result.json` 分开记录：带 strict 参数的 HTTP 请求是否被接受、JSON 是否通过 B1/B2、finish_reason、返回模型、reasoning 字段是否存在/是否非空及 token usage。只观察到一次合法输出，不能证明供应商对所有 Schema 分支强制约束，也不能证明任意业务请求的非思考或收费上限。

`reasoning_tokens=0` 且无 reasoning_content 时只能称“本次未观察到思考输出”；字段缺失为 unknown，不能当作零。出现思考、截断、模型身份冲突或非法输出则失败关闭并记录，不发第二次请求。不自动写回或删除三个业务阻断；只按实际证据讨论哪些已得到局部支持，尤其不能凭一次结果解除 tokenizer/业务预算风险。

## 命令与停止位置

已经实际运行的**离线**命令：

```powershell
.venv/Scripts/python.exe -B scripts/run_siliconflow_schema_probe.py preflight
$env:PYTHONPATH='src;tests'
.venv/Scripts/python.exe -B -m unittest test_siliconflow_schema_probe test_review_shadow_siliconflow test_review_shadow_openai -v
```

只有用户明确授权“一次不含真实评论的付费 SiliconFlow 合成兼容性探针”后，才执行下列命令；**本轮未执行**：

```powershell
.venv/Scripts/python.exe -B scripts/run_siliconflow_schema_probe.py run --allow-paid-synthetic-once
```

未来结果固定存于 Git 忽略目录 `outputs/rag-semantic-retrieval/siliconflow-synthetic-schema-probe-v1/`，包括一次性尝试标记、预检、完整合成请求、worker 回执、usage、合法结构化输出和结果摘要。错误诊断不回显 SDK 错误正文、密钥或堆栈；实际返回模型及 reasoning 元数据在失败时也尽量保留。没有读写真实业务来源或正式答案。

本轮离线材料在 `outputs/rag-semantic-retrieval/p5-siliconflow-synthetic-probe-preparation-20261106/`。**到此停止并等待明确付费授权，不运行真实探针或 S1/S2/S3。**
