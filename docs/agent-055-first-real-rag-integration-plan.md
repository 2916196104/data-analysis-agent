# 商品 055：首次真实 RAG 语义检索最小联调计划

日期：2026-11-12（Asia/Taipei）。状态：**静态准备完成；真实 Embedding 与检索 NOT EXECUTED，等待单独明确授权。** 本轮没有连接数据库、加载模型、启动编码进程或运行 Agent，仅读取本地代码、配置及已完成记录，执行无外部调用的规则/空间标识计算。

## 1. 直接沿用已完成的数据准入

已读取本机保存的 [COMPLETED 记录](evaluation/agent-055-effective-corpus/effective_corpus-20261112T035403365378Z.json)：510 条评论、505 条成功标注评论、1091 条当前观点（模型 1091 / 人工 0）、200 条未失效向量，key/元数据/快照及最终认证 U 均为 200；未覆盖 891，观点覆盖率 **18.33%**，替代及无法解释的来源异常均为 0。记录确认数据库 reviews、current_user/session_user 均为 rag_audit_ro、RR 只读事务、11 个对象和正常回滚关闭。

这是本机实际认证记录的结果，不是从更早报告沿用的历史估计。[原核验报告](agent-055-effective-corpus-verification.md)中 Codex CONNECT 失败和 UNKNOWN 属于此前观察；本计划以新 COMPLETED 记录为准，**不重跑连接/权限预检或独立有效语料全量审计**。

| 固定项 | 本次唯一取值 |
|---|---|
| 原问题（编码时原样保留） | 商品100013588055有没有送货慢的反馈？ |
| 账号 / 数据库 | rag_audit_ro / reviews；沿用已成功的本机地址及安全凭据机制 |
| source / dataset_version / 商品 | reviews_csv / reviews-csv / 100013588055 |
| 日期 | UTC `[2021-09-11T00:00:00Z, 2022-08-27T00:00:00Z)`；接口 date_to=2022-08-26，as_of=None |
| label_profile_key | `1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304` |
| embedding_space_id | `embedding-space-v1:11e6754662dbd13fc56c6e624bc9416c1c2766f962787267fd4a57b1fa8d622e` |
| 文本构造版本 | quote-utf8-v1 |
| Top-K / aspect / sentiment | **5 / None / None**；使用生产 retrieval_request() 的实际规则结果 |

不额外添加 logistics 或 negative 过滤；本次目标是在已认证的 200 条向量候选范围内检索。有效集合如在执行快照中发生变化，使用生产保护机制拒绝/排除失效来源并如实记录，不能强行套用 200。

## 2. 实际调用入口及意图绑定

| 层 | 复用入口与作用 |
|---|---|
| 意图/范围 | [resolve_review_request()](../src/data_analysis_agent/agents/review_intent.py) 的规则路径；绑定指定单商品及日期，不调用意图 LLM |
| 工具适配入口 | [execute_retrieval(context, bound_scope)](../src/data_analysis_agent/agents/review_retrieval.py)，工具标识 search_semantic_evidence；仅调用这个适配层，不启动完整 Agent |
| 查询编码与检索 | [SemanticRetrievalService.prepare() / search()](../src/data_analysis_agent/reviews/semantic_retrieval.py)，配合 PostgresSemanticRepository(existing_readonly_connection) |
| 真实查询编码器 | [PersistentEmbeddingProcess](../src/data_analysis_agent/reviews/embedding_process.py)，使用现有 RAG_PYTHON、RAG_MODEL_CACHE；本次独立实例，结束后 close() |
| 事实/传输验证 | 适配入口内部执行 require_source_validated_result()、assess_retrieval()、validate_retrieval()；不用手工拼装候选或许可结果 |

本轮已用指定 SKU 的最小目录夹具执行**纯函数规则检查**，得到：意图 semantic_evidence，工具 search_semantic_evidence，单商品绑定 100013588055，model_calls=0；请求保持原问题、Top-5、无维度/情感过滤。这是规则检查，不是一次新的真实目录/数据库验证。

当前 [evidence_question()](../src/data_analysis_agent/agents/review_task_contract.py) 在已绑定 scope 下将事实判断文本分离为“有没有送货慢的反馈？”，[target()](../src/data_analysis_agent/agents/review_reported_facts.py)识别 required_fact 为 **delivery_slow**。原始问题仍原样送入查询编码器，不用删掉 SKU 后的改写问句替代它。

现有私有配置中的标注模型三元组与 PROMPT_VERSION 已通过本地纯哈希计算匹配指定 profile。实际执行沿用此三元组；它与查询 Embedding 模型是两个独立身份，不用 BGE-M3 名称替换标注模型字段。endpoint 和凭据不写报告。

## 3. 查询 Embedding 空间与本地准备

| 参数 | 已检查的生产配置 |
|---|---|
| 模型 | BAAI/bge-m3 |
| 固定修订 | `5617a9f61b028005a4858fdac845db406aefb181` |
| 维度 / 池化 | 1024 / CLS |
| 精度 / 归一化 / 距离 | float32 / L2 / cosine |
| 实现 / 输入变换 | transformers-cls-eager-v1 / none；query_instruction 为空 |
| 输入上限 | 512 tokens；生产实现拒绝超限，不隐式截断 |
| 编码批次 | 仅一个问题、返回一个真实 1024 维查询向量；不编码评论 |

已通过 [EmbeddingSpace / SPACE_ID](../src/data_analysis_agent/reviews/embedding_store.py) 的纯计算确认空间 ID 与上述已认证记录完全一致。RAG_PYTHON 文件、RAG_MODEL_CACHE 目录及固定修订的 8 个必需缓存文件均存在；缓存 config.json 为 xlm-roberta、hidden_size=1024。

这证明**静态配置兼容、文件就绪**；尚未证明模型能成功加载或推理。获准后沿用 [LocalBgeM3Embedder](../src/data_analysis_agent/reviews/embedding.py) 的 local_files_only、固定修订、trust_remote_code=False、CLS/L2 及向量形状/有限值校验。编码进程限制离线和网络访问，不传数据库凭据；缓存缺失、空间/维度不符、加载失败即停止，不下载模型或改用其他 Embedding 服务。

## 4. 获准后只执行一次的最小流程

1. 在已完成认证的用户本机 PowerShell 环境执行；使用既有 rag_audit_ro 凭据建立本次检索所需的一个只读连接，不另跑试连、权限审计或 readonly_audit.py。不在已被拒绝的 Codex 环境自动重新探测。
2. 复用规则绑定固定问题和范围，建立真实编码器及 SemanticRetrievalService。先调用 **prepare(request) 一次**，在数据库业务事务开始前生成查询向量；不另跑 health 编码或测试问题。
3. 开始 **REPEATABLE READ READ ONLY**，设置 UTC、statement_timeout=15s、lock_timeout=3s。注入返回这个真实 service 的 semantic_service_factory，再调用 **execute_retrieval(context, bound_scope) 一次**；其 search() 消费已准备向量，不会再次编码同一问题。
4. 生产 [向量检索](../src/data_analysis_agent/reviews/embedding_store.py)限定范围/profile/space/构造版本/未失效及当前 key+快照，执行一次 pgvector cosine 精确排序，ORDER BY distance,id，LIMIT 5。不添加相似度阈值、不重排、不扩大 K、不关键词补搜。
5. 保留检索内部的 read_effective_corpus()、_eligible()、独立正文/导入身份装载和 _verified_sources()，以及全部持久化来源字段比较。**这是该次检索自带的当前成员/来源保护，不另做一轮审计报告或全量向量认证。** 不把已完成记录当作可绕过当前快照校验的永久凭证。
6. 接收适配入口的真实结果和生产许可判定，再记录脱敏结果。无论结果或异常均 ROLLBACK、关闭数据库连接及本次编码进程；失败不自动重试，不调用最终回答 LLM。

## 5. 来源认证、相关性与证据结论

来源通过必须同时满足：当前有效 key 和整条评论快照匹配、全部生产持久化来源字段一致、真实正文切片等于 quote、输入/key 摘要可重建、导入身份一致、同一 RR 快照不漂移、内存来源认证绑定及适配层 validate_retrieval() 通过。持久化 JSON 自报 passed 不替代生产认证。

认证通过只证明证据来源可信。业务支持还必须由 reported_experience_v2 对 **delivery_slow** 给出 permitted_keys，并通过全文上下文否决规则；配送负面标签、较近距离、单独“等了几天”、第三方转述或被否定/撤回的描述均不能直接替代明确支持。

每条实际 Top-5 记录：rank、distance、review_id、annotation_id、model/human opinion_id、opinion key、quote 起止位置/摘要、effective_snapshot_sha256、evidence_ref、来源/Validator 结果、规则支持结论。人工复核仅将真实候选分为“明确支持 / 主题相关但不足 / 无关或矛盾”，与自动规则判定分别报告；不调用相关性 LLM。正文只在内存参与生产校验，记录不导出全文、完整向量、用户身份、凭据或 endpoint。

**不能将 Top-5 无明确支持写成“商品没有送货慢反馈”。** 即使 5 条都不支持，也只证明本次返回候选未找到明确支持，不能证明全部 200 条已索引观点均无相关反馈，更不能覆盖剩余 891 条未索引观点或全部 510 条评论。空结果只记录“本次检索未返回候选”，不作全商品否定判断。

## 6. 权限、成本与验收

后续所需明确授权仅为：**该固定问题的一次本地真实查询编码、一次只读 Top-5 检索、生产来源/规则验证及脱敏记录**。本轮尚未获得或消耗此执行授权。无数据库写入、向量同步/重建、模型下载、最终回答 LLM、完整 Agent、Gold/Oracle 修改或 P6-E1B 状态变更。

预期外部 API 费用 **0**：使用已有本地模型，不发送外部请求。实际成本为本地模型冷启动、CPU/RAM、一个查询的编码及数据库读取；尚未实测耗时/内存，不承诺延迟。现有编码超时配置为 600 秒；数据库语句和锁上限如第 4 节，超时停止，不自动补调用。

| 验收项 | 通过或结果分类 |
|---|---|
| 技术链路 | 原问题与范围正确；真实编码恰好一次且空间/1024 维正确；Top-K 恰好调用一次；所有实际候选通过生产来源和适配层验证；回滚关闭完成 |
| 召回数量 | 无额外过滤、候选仍为 200 时预期返回 5 条；实际数量必须实记。0～4 条属于与基线预期有偏差，说明候选/快照或执行情况，本次停止，不自动加测 |
| 找到明确证据 | 至少一个生产 permitted_key 明确支持 delivery_slow，记录实际来源 ID、位置和支持理由；只许可“有买家如此反馈” |
| 未找到明确证据 | 来源/技术路径可以通过，业务结论为“不足”；如实区分无候选、仅主题相关和上下文无法许可，不伪造命中或否定总体 |
| 执行失败 | 编码空间/向量错误、超时、数据库错误、越界、来源/快照不符、Validator 拒绝或清理未完成：FAIL/BLOCKED，保留脱敏阶段与错误码，不记完整联调通过 |

本轮交付到上述静态计划；未生成查询向量、未执行 Top-K、未观察真实相关性或生产事实许可结果，准备完成后停止。
