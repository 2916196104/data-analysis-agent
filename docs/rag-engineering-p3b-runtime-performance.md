# P3-B：模型热复用、批量来源补齐与正式 Worker 接入

后续状态：2026-11-06 已获单独授权并完成真实 10 次语义冷/热及 2 次 SQL 对照，结果见 [P3-B 真实冷/热性能对照](rag-engineering-p3b-real-performance.md)。以下保留工程阶段原始报告；其中“尚未执行”“等待授权”描述的是该阶段结束时状态。

2026-11-06。工程实现和合成验证完成；**尚未执行真实 BGE-M3 或 PostgreSQL 冷/热对照，不能宣称实际提速**。沿用 P3-A 的 `reported_experience_v2`、商品范围、来源检查和引用政策，没有修改 R0、B2 参考、人工裁决或向量。

## 实际变更

| 文件 | 变更及目的 |
|---|---|
| `reviews/embedding_process.py` | 增加 `PersistentEmbeddingProcess` 和进程池；原一次性 `LocalEmbeddingProcess` 保留 |
| `scripts/persistent_embedding_worker.py` | 复用 `LocalBgeM3Embedder`，提供 initialize/encode/health JSONL 服务，一次加载、多次编码 |
| `core/config.py`、`.env.example` | 增加明确的 `RAG_PYTHON`、`RAG_TIMEOUT_SECONDS`；沿用 `RAG_MODEL_CACHE` |
| `agents/review_retrieval.py` | 默认服务工厂接入持久模型进程，增加检索及许可计时；语义政策未改 |
| `agents/reviews.py` | 语义路径先结束范围规划事务，在事务外编码，然后开启新只读快照重新验证来源 |
| `reviews/semantic_retrieval.py` | 单请求向量预备、同快照有效语料复用、候选评论批量补齐、阶段计时 |
| `reviews/embedding_store.py` | 原搜索逻辑提取到内部函数，允许同快照复用已独立读取的语料；公开接口及 SQL 排序/过滤不变 |
| `jobs/worker.py` | Worker 退出时释放模型进程；仍从原任务执行入口调用 `run_review_agent()` |
| `tests/test_rag_runtime.py`、`tests/fixtures/synthetic_embedding_worker.py` | 实际本地合成子进程、并发/超时/崩溃/退出、事务及批量补齐测试 |
| `tests/test_semantic_retrieval.py` | 将来源适配测试调整为批量读取，保留来源冲突等校验 |
| `tests/test_review_semantic_integration.py` | 默认工厂、正式任务 Worker 连续两任务及编码失败测试 |

上述简写源码目录均为 `src/data_analysis_agent/` 下路径。本报告为新增文件。私有 `outputs/rag-semantic-retrieval/p3b-20261106/` 保存测试日志、规则保护记录、离线测试驱动和待授权真实对照脚本；目录已确认被 Git 忽略。

## 正式运行入口与生命周期

配置示例（需要由运行环境实际设置，本轮未改真实 `.env`）：

```dotenv
RAG_PYTHON=E:/AllProject/data-analysis-agent/.cache/embedding-eval/venv/Scripts/python.exe
RAG_MODEL_CACHE=E:/AllProject/data-analysis-agent/.cache/embedding-eval/hf/hub
RAG_TIMEOUT_SECONDS=600
```

这两个路径沿用 P2/P3-A 已使用的独立 Python 与模型缓存。业务 Python 无需安装 torch；模型子进程使用自己的环境。配置后仍从现有 Agent 任务入口执行，链路为：

`TaskWorker._execute → run_review_agent → 默认 create_semantic_service → shared_embedding_process → 持久本地 worker → LocalBgeM3Embedder → pgvector → 独立来源验证 → 原许可规则 → 原回答/引用验证`。

不再要求私有测试脚本注入 encoder/factory。合成测试实际经过任务配置读取、默认服务工厂和任务结果保存；仅替换模型为合成 encoder、来源为 FakeRepository。正式 Worker 的真实数据库运行仍待授权。

- 进程按解释器、缓存和超时配置共享，首次请求惰性加载；同一宿主进程内相同配置复用。固定 BGE-M3 修订、1024 维、512 token 上限、CPU 4 线程，使用已有编码实现；不自动下载、安装或改变空间。
- 每次仅发送原问题，不传评论、数据库凭据或人工参考。子进程环境使用白名单、HF 离线模式和网络连接阻断。此处是本地模型服务边界，**不声称新增了 AppContainer 或账户级隔离**。
- 串行锁防止同一模型并发编码；请求 UUID 与空间 ID 防止响应串线；向量维度、归一化、有限值继续检查。健康检查可通过客户端 `health()` 显式触发，未额外建立健康服务。
- 超时覆盖排队、初始化和 RPC；拿不到锁返回 busy。进程崩溃、超时、非法输出或空间错误停止进程并锁定失败，不自动重试、换模型或回退普通服务。Agent 返回错误，不发布正常引用。恢复需要显式重启 Worker/关闭进程池后重新调用。
- Worker 正常/异常结束循环及 Python 退出钩子负责回收；实际合成测试检查子进程已退出、读取线程结束。正在执行的调用受超时约束，退出可能等待它结束；强制终止宿主、系统断电不属于正常退出验证。
- 未设置 `RAG_PYTHON` 时保留既有进程内加载路径；该路径仍要求业务环境具备依赖，不会在子进程失败后被自动选作后备。

## 来源与事务优化

仅语义查询改变事务流程：短事务解析当前商品和 scope → rollback → 创建/复用模型并编码 → 新只读 REPEATABLE READ 事务 → 重新检查商品归属 → 当前有效语料/检索/补齐/验证。模型等待期间连接仍存在，但没有打开的数据库事务。原 SQL 统计路径保持不变。

每个 `_PostgresSnapshot` 最多读取一次完整有效语料；搜索和补齐在同一事务中使用它。访问时核对事务快照和事务时间，离开上下文即失效、清除缓存。没有跨请求或跨事务的正文/来源缓存。

候选对应评论由单条限定 source、dataset、商品、UTC 日期及评论 ID 的 SQL 联表读取正文和导入来源。有效 annotation、human revision、观点全集仍从独立数据库语料构建；不是从向量候选复制。保留完整观点集合、`effective_snapshot_sha256`、内容哈希、观点键、原文切片、来源标识及候选版本一致性检查。批量返回缺行、重复、内容或导入身份漂移均失败；原 SQL 距离排序和过滤参数经合成对照保持一致。

本次消除的是检索/补齐间的重复语料扫描及逐候选调用聚合型 `get_evidence()`；Agent 的 overview 等既有统计仍保留，不声称整轮只执行一次 SQL。

## 实际验证

执行命令：

```powershell
.venv/Scripts/python.exe -B outputs/rag-semantic-retrieval/p3b-20261106/run_checks.py
git diff --check
```

测试驱动拦截真实 PostgreSQL 与外部 HTTP；使用临时合成 SQLite、FakeRepository、合成编码器及实际 Windows 子进程。SDK 合成测试所需本机 socketpair 使用已获执行权限，没有连接业务数据库或运行真实模型。

最终专项结果 **85/85 通过**；适用完整回归 **486/486 通过**，结果见同目录 `regression-release.json`（含专项，不能与专项数相加）。`git diff --check` 通过。两项既有 `test_review_scope` 队列错误单列：`test_run_date_filters_incremental_skip_and_scoped_progress`、`test_run_retry_and_cutoff_forwarding_with_real_mocked_sdk`，均因 SQLite 测试调用只支持 PostgreSQL 的持久标注队列而报错，未修复、未计作通过。

关键实测证据：

- 合成冷/热向量完全一致，同一 PID，初始化计数为 1；4 线程共 8 请求结果各自正确。
- 超时、异常退出、零向量、错误请求/空间身份均失败；不会重启重试或产生正常回答引用。
- 正式 `TaskWorker._execute` 连续两次合成任务成功，默认工厂接入，同一 PID、一次加载、第二次为热请求，回答完全一致；实际 `_loop` 退出释放子进程。
- 编码期间连接无事务；预备向量只消费一次且绑定原请求。旧快照及事务身份变化被拒绝，新快照重新读取来源。
- 原搜索与批量适配的候选、距离、顺序及 SQL 参数一致；一份语料、一次批量评论补齐。来源缺失、重复、正文/导入身份冲突被拦截。
- 原 P0/P1、否定/复合事实许可、回答引用、SQL 统计及 E1/R0 相关回归纳入测试；未修改语义实现。`review_reported_facts.py` 和 `evidence_judge.py` 与 HEAD 一致，摘要见 `protected-rules.json`。

合成测试不能证明新批量 SQL 已在实际 PostgreSQL 执行成功，也不能证明真实 BGE 冷/热一致或提速。未运行 B3-B、外部 LLM、真实查询、向量同步或数据库写入。

## 固定真实对照方案：等待单独授权

已准备私有 `run_real.py`，缺少授权参数时在数据库/模型调用前退出。拟复用 P3-A 保存的原 5 个语义问题和 1 个 SQL 问题；每个语义问题关闭旧模型池后冷启动一次，再同进程热请求一次。总计 **10 次语义查询/编码、5 次模型加载、2 次 SQL 控制查询**，不新增问题、不自动重试。这里“冷”指模型进程冷启动，并未清除 OS 文件页缓存。

只读范围：`reviews_csv / reviews-csv`、商品 `100013588055`、UTC `2021-09-11` 至 `2022-08-26` 内最多 510 条评论、其全部有效观点、必要标注/修订及导入元数据；既有 200 条向量，每问 Top-5。空候选问题仍限定原 `2021-09-11`。为商品绑定读取该数据集必要产品目录。沿用 P3-A profile、EmbeddingSpace 和 `quote-utf8-v1`；不能将来源读取量缩写为只有 Top-5。

授权后才可执行：

```powershell
.venv/Scripts/python.exe -B outputs/rag-semantic-retrieval/p3b-20261106/run_real.py --authorized-p3b-comparison --python E:/AllProject/data-analysis-agent/.cache/embedding-eval/venv/Scripts/python.exe --cache E:/AllProject/data-analysis-agent/.cache/embedding-eval/hf/hub
```

脚本使用默认服务工厂，不注入模型服务。事务检查只读/RR、范围计数及向量数，设置 statement timeout 30 秒、lock timeout 3 秒，结束 rollback/close；父进程拦截外部 LLM/HTTP，模型仍离线。保存每次原始结果后验证候选、许可与 P3-A 一致、公开回答可重建；错误或内容漂移立即停止。结果写独立私有时间目录，不覆盖旧结果。

记录 `embedding_runtime.model_load_ms/encode_ms/cold_start/process_id`、`embedding_preparation_duration_ms`、检索 `effective_corpus_ms/pgvector_fetch_validate_ms/source_hydration_ms/source_verification_ms/permission_ms` 及既有回答/总耗时。`pgvector_fetch_validate_ms` 包括数据库调用、fetch、结构和来源行校验，不能称为纯服务端执行时间；`search_including_corpus_ms`、编码总计等包含子项，不重复求和。

资源估算沿用 P3-A 实测：CPU 4 线程、单模型峰值约 1.7 GiB，建议预留 4–8 GiB 可用内存；本轮尚无新的实测值。单请求最长模型等待 600 秒，串行、失败停止。每题一组冷/热对照只用于检查功能和初步耗时，不能给出稳定 P95 或总体提速结论；进一步重复测量需先看这批结果，再明确次数。

## 剩余问题与停止点

工程接入已具备进入真实冷/热对照的条件；实际环境配置、真实 SQL/模型兼容性和性能改善仍待验证。模型共享仅限同一宿主进程，多 Worker/不同配置会各自持有模型；当前串行策略优先控制内存，不支持批量调度或跨进程服务。现有未知语义和复合关系覆盖边界未扩展。

本轮到此停止。下一步需要单独批准上述 **10 次语义 + 2 次 SQL** 的读取和本地模型执行；没有自动使用此前 P2/P3-A 授权继续运行。
