# 商品 055 真实 RAG 联调准入

检查日期：2026-11-11，Asia/Taipei。**结论：BLOCKED，不具备执行一次真实端到端联调的条件。** 本轮只读检查本地文件与源码、运行无副作用的解析函数；没有连接数据库、启动 Agent 或 Worker、执行检索、加载 Embedding 或调用模型。

## 当前检查结果

| 项目 | 本轮结论 | 依据及边界 |
|---|---|---|
| P2-C3 源码 | **匹配** | [P2-C3 版本清单](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-g25/source-revision.p2-c3.v1.json)绑定的 88 个生产文件摘要全部匹配；清单摘要 `8afbb5d3448c0e6cf6304c326f5f2c86c2adfcc14804a459741eaf0f65b10f77`。这是离线开发基线，不是生产认证。 |
| 数据库配置 | **存在，连接有效性未知** | 项目 `.env` 中 `DATABASE_URL` 非空；当前进程没有同名环境变量。没有输出连接串、读取数据库或验证服务器身份、凭据、权限和事务设置。 |
| 安全读取当前数据库 | **BLOCKED / 未尝试** | P6-E1B 独立环境及 OS 安全预检仍 BLOCKED；本轮没有建立经过核验并明确获准的只读身份和目标，不能将非空配置视为授权。 |
| pgvector 与索引 | **当前未知** | 源码和迁移要求 pgvector 与 `vector(1024)`；历史记录曾观察到扩展和索引，未证明目前仍可用。 |
| 055 当前评论、标注和向量 | **未知** | 没有进行当前 SELECT；既有数字仅能报告其历史时点和范围。 |
| RAG 检索、Validator 来源 | **未具备当前来源准入证明** | 需要在已授权只读快照中重建当前有效语料、核对向量成员和引用；本轮不能伪造或继承认证。 |
| 精确目标问题 | **事实解析不支持原文前缀** | 下文纯函数检查确认该原问句的 `target` 结果为 null；不代表实际端到端已执行或最终状态已实测。 |
| 一次真实端到端联调 | **BLOCKED / NOT EXECUTED** | 当前库及身份、向量/来源、精确问题契约、运行资源和单次执行授权尚未齐备。 |

本轮读取配置时只记录键是否存在、非空和必要本地路径是否存在；没有在报告记录密钥、完整连接信息或敏感路径。`.env` 中 `RAG_PYTHON` 和 `RAG_MODEL_CACHE` 均已配置，路径分别存在且为文件、目录；这**不能证明**解释器依赖、模型文件完整性、模型可加载或向量可用。

## 既有记录与当前数据不能混同

可追溯的历史范围为 `reviews_csv / reviews-csv`、商品 100013588055、UTC 2021-09-11 至 2022-08-26，使用原标注 profile。数据版本在历史范围和迁移契约中明确，但当前是否相同未经核验。

- [此前真实 P3-A 记录](E:/AllProject/data-analysis-agent/docs/rag-engineering-p3a-real-regression.md)在其观察时点记录 055 的 510 条评论、505 条当前内容匹配且指定版本的成功标注行、pgvector 0.8.7、`vector(1024)` 和 200 条未失效向量。该报告说明标注是行数，不能未经去重当成其他分母。
- 本轮只读核对历史 `business-acceptance.json` 的文件摘要，其 2026-11-02 UTC 观察记录 1091 条有效观点、其中 200 条已索引、891 条未索引，200 条覆盖 105 条评论；同一历史快照中元数据匹配。这个记录不能回答当前向量数、最新修订或本问题是否命中。
- [第二阶段数据准备报告](E:/AllProject/data-analysis-agent/docs/agent-stage2-real-business-evaluation-readiness.md)记录 055 的 57 条真实来源评论、93 个观点是历史候选并集，历史完整性为 `history_unproven`；不是全商品总体或本问题的新 Top-K。原 CSV 的 511 条是原文件行数，不是当前有效去重评论数。
- 既有 [项目交付报告](E:/AllProject/data-analysis-agent/docs/project-final-delivery.md)记录过不同问题的 055 有限真实 RAG 展示；这些是历史结果，不能证明本问句或当前服务通过。

因此，“当前评论数量、成功标注数量、当前有效向量数量、索引有效性和当前关联一致性”全部为 **UNDETERMINED**，不是 0，也不是历史数值的续用。

## 当前 RAG 依赖和来源门

[迁移定义](E:/AllProject/data-analysis-agent/migrations/003_review_opinion_embeddings.up.sql)要求已有 pgvector，向量表为 `review_opinion_embeddings`，并保存 review、annotation、模型/人工观点、人工修订、profile、文本摘要、快照摘要、空间版本和失效时间；另有 `rag_embedding_sync_runs` 和范围/关联 B-tree 索引。当前实现通过 `<=>` 精确排序；**ANN 索引不是该实现的已证必需条件**。本次没有检查实际扩展或索引。

[embedding_store.py](E:/AllProject/data-analysis-agent/src/data_analysis_agent/reviews/embedding_store.py:20) 绑定 BAAI/bge-m3 固定修订、1024 维、`quote-utf8-v1` 和指定向量空间；[embedding.py](E:/AllProject/data-analysis-agent/src/data_analysis_agent/reviews/embedding.py:10) 使用本地缓存及固定修订。查询时仍需要本地查询编码；即使不产生外部费用，也属于本轮禁止的 Embedding 执行，需要后续明确授权。

[PostgresSemanticRepository](E:/AllProject/data-analysis-agent/src/data_analysis_agent/reviews/semantic_retrieval.py:415) 需要稳定只读事务、当前有效语料及快照，关联 `products`、`import_runs`、`reviews`、`review_annotations`、`review_opinions`、人工复核/观点表和向量表；检索后重新读取完整来源并校验身份及内容。[validate_retrieval](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_retrieval.py:171) 检查范围、profile、快照、原文摘要、引用位置和重新计算的事实许可。已有历史 JSON 不具备自动签发当前 P0 来源认证的效力。

## 本次目标问题的静态与纯函数核验

目标：“商品100013588055有没有送货慢的反馈？”

在不调用 Agent、模型、数据库或检索的前提下，使用一个**合成的单商品 scope 字典**运行 `wants_semantic_search`、`evidence_question` 和 `target`：

| 检查 | 实际纯函数结果及含义 |
|---|---|
| `wants_semantic_search(原题)` | `true`；[Router](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_intent.py:346) 和 [Planner](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_plan.py:122) 源码在该条件下指向 `search_semantic_evidence`。实际目录绑定、完整路由和工具执行未运行。 |
| 商品和日期 | 代码通过目录匹配 SKU 并将显式/页面 scope 限定；本题未写日期，实际范围取决于已验证的当前请求范围和选择。合成 scope 不证明真实目录存在或当前选择有效。 |
| `evidence_question(原题, 合成单商品 scope)` | 仍为原题。原因是 [前缀处理](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_task_contract.py:18)要求文本直接以 SKU 开始；原题先出现“商品”。 |
| `target(上述文本)` | `null`，无法从该原文提取 `delivery_slow`。事实模式支持“送货慢”，但不等于已解析该完整问题。 |
| 控制性语法检查 | 仅 SKU 开头或不带 SKU 的同一问句经纯函数处理后可得到 `delivery_slow`；这不是用户授权改题，也不是实际执行结果。 |
| `assess` 与回答 | 未输入真实候选、未运行。源码在目标未覆盖时保守不许可，并有不足提示路径；具体 Agent/Worker 状态和文本未实测。 |

因此，本次精确问题存在一个独立的**静态语义准入缺口**：不能把纯函数测试中的合成 scope 当作真实目录认证，也不能为了联调强行删除“商品”或使用不同问法后宣称原题通过。若业务目标是验证此原题，需要单独、最小地审查并修复安全绑定后的前缀处理，再完成离线安全回归；本轮未修改代码。

## P6-E1B 与最小阻断处理

[P6-E1B 最终报告](E:/AllProject/data-analysis-agent/docs/P6-E1B-final-real-execution-acceptance.md)最后保留 BLOCKED：Windows TCP 原生状态验证和既有 PostgreSQL 进程身份未能可靠确认，先前管理员只读启动未产生可验证结果，PG-01～08 未执行。本轮没有重新测试或解除这些阻断；旧问题不因历史 055 演示成功而消失。

P6-E1B 是隔离 PostgreSQL 集成验收，其 BLOCKED **不应被误称为代码逻辑上禁止所有可能的只读数据库**。但对本次实际环境，尚未证明一个符合既有安全门、身份清楚且被明确授权的当前只读目标，因此本轮按要求停止数据库连接；也没有用其他业务库或替代环境绕过。

按影响顺序，最小待处理事项：

1. **当前数据库和来源身份**：沿用现有 P6-E1B 停止结论，先由环境负责人提供可安全核验、明确授权的目标及只读身份，或在既有安全门内解决当前阻断；然后才可另行执行限定范围的只读元数据/SELECT，核实 pgvector、数据版本、权限、现有索引和评论—标注—观点—有效向量关联。不得同步或补录。
2. **原问题语义**：单独解决“商品＋已验证 SKU”前缀与事实命题分离，保持未知 SKU、范围冲突和引用许可安全规则，完成离线回归；或由用户另行选择问题，但不能将改写问题称作原问题通过。
3. **本地模型与单次运行授权**：先验证已配置解释器/缓存的离线可用性及空间版本；若要执行，需单独明确批准本次指定问题、只读范围、一次本地查询编码与检索、独立任务结果保存和停止条件。外部收费 LLM 或第三方 Embedding 如需使用，必须另有明确供应商、数据范围与费用授权；本轮没有任何模型调用许可。

这些条件未齐之前，原问题的真实 RAG 接口与 Validator 来源契约均为 **NOT READY**。本轮不执行联调、不新建评测阶段或修改 P6-E1B、生产代码、Gold、Oracle 和数据库。
