# RAG工程集成P0：独立语义检索服务

日期：2026-11-05。

**P0已完成：统一入口已实现查询向量化、既有向量查询适配、当前证据补齐及来源校验。28项专项通过，含专项的回归311项通过。** 本轮使用fake encoder/repository验证，没有运行真实模型、连接真实PostgreSQL、下载模型、调用外部API或执行B3-B；未接入Agent。

## 1. 文件与统一接口

仅新增三个文件，不修改既有业务代码或依赖：

- [semantic_retrieval.py](../src/data_analysis_agent/reviews/semantic_retrieval.py)：输入/输出类型、服务、只读PostgreSQL适配器。
- [test_semantic_retrieval.py](../tests/test_semantic_retrieval.py)：28项完全合成测试。
- 本报告。

```python
from data_analysis_agent.reviews.semantic_retrieval import (
    SemanticRetrievalService, SemanticQuery, PostgresSemanticRepository,
)

# encoder由调用方提供：已初始化的LocalBgeM3Embedder或测试FakeEncoder。
# scope由调用方提供：当前已绑定的单商品范围及标注model/base_url/prompt_version。
# 本段为调用方式示例，本轮未对真实数据库执行。
service = SemanticRetrievalService(
    encoder=encoder,
    repository=PostgresSemanticRepository(db),
    scope=scope,
)
result = service.search(SemanticQuery(
    question="有没有制冷不足的反馈？", top_k=5,
    aspect="effectiveness", sentiment="negative",
))
payload = result.to_dict()
```

search也接受同字段dict或JSON；统一返回SemanticResult，统一抛出SemanticRetrievalError，错误码可从`.code`读取。异常不会伪装成空结果或insufficient，错误文本不回显问题、评论、DSN或底层异常。

| 类型 | 约定 |
|---|---|
| RetrievalScope | 复用AnalysisScope校验；固定source/dataset、单商品、UTC日期/as_of及标注三元组；复制、规范化后冻结 |
| SemanticQuery | 原问题1–4000字符，top_k默认5、范围1–100，沿用现有Aspect/Sentiment枚举；None表示不加对应过滤 |
| QueryEncoder | `.space: EmbeddingSpace`及`.embed_texts([question])`，直接兼容LocalBgeM3Embedder；服务不构造模型或选择设备 |
| RetrievalRepository / RetrievalSnapshot | 同一个稳定只读快照内search与load_sources；后者只接收key，不接收候选正文或候选对象 |
| SemanticEvidence | 原排名/距离、RagOpinionDocument、RagReviewState、完整正文及独立正文SHA-256；提供key/effective_snapshot_sha256 |
| SemanticResult | 请求、继承范围、事务观察、来源政策、证据tuple；`.to_dict()`输出可JSON序列化数据，排除标注base_url和重复embedding_input |

请求不能覆盖scope，也不能指定另一EmbeddingSpace。当前明确保留既有search_effective_opinions的单商品边界，不自动合并多商品。过滤仅接受明确传入值，不自动解析情感/主题；mixed和all均不能作为sentiment过滤传入。

## 2. 数据流和来源验证

1. 校验请求和固定BGE-M3 EmbeddingSpace，再将**原问题文本**交给注入的encoder。校验唯一向量的1024维、有限数值、归一化和编码器空间未漂移。
2. 编码完成后才进入repository.snapshot。生产适配器复用search_effective_opinions，保留原pgvector `<=>`查询、过滤、顺序和tie顺序，不另写向量SQL或重排。
3. 按返回key从**数据库有效语料**独立定位来源，不从待校验candidate复制来源。复用read_effective_corpus取得对应评论的全部有效观点、当前标注/人工修订状态及effective_snapshot_sha256。
4. 仅为命中的评论复用get_evidence，读取完整正文和导入文件指纹/行号。比较其来源、商品、review/annotation/revision、发布时间、记录摘要和引用位置与独立语料一致。
5. 复用_document重建稳定key、quote UTF-8哈希、embedding_input及引用；验证Unicode码点半开区间`[start,end)`和原文切片。复用_review_state对**完整有效观点集合**重算快照，核对数量和摘要。不能只用Top-K中出现的观点重算。
6. 候选必须与独立来源观点逐字段一致，输出仍沿用原候选顺序。缺来源、重复key、越界、版本/区间/哈希冲突均使整次查询失败，不静默丢弃或返回部分成功。

review_content_sha256沿用数据库的**评论记录摘要**，不错误地拿它与正文哈希比较。新增review_text_sha256是本次实际读取正文的UTF-8摘要；二者含义不同。对记录版本的信任来自同一数据库快照的独立来源读取，不宣称重新验证了原CSV或取得不可变历史证明。

输出保留quote_full_review_veto / 1：quote是支持片段，完整评论作为解释或否决上下文。这是对后续消费方的使用约束，本服务不执行语义否决判断。本模块只检索并校验来源，**不判断直接相关性、事实充分性或是否许可业务答案**。距离仅为诊断值，没有固定阈值、概率转换、人工评分或R0调用。

## 3. 事务与错误处理

PostgresSemanticRepository支持：

- 数据库连接目标：打开现有数据库的readonly连接，开始REPEATABLE READ READ ONLY事务，核验实际read_only/isolation，记录事务快照和观察时间；成功或失败均rollback/close，不commit。
- 已存在的PostgresConnection：要求已有只读REPEATABLE READ或SERIALIZABLE事务，核验后复用同一连接；不begin、commit、rollback或close调用方事务。

服务在进入repository上下文前完成编码；若调用方传入已经打开的事务，该外部事务本来就存在，生命周期和编码时机由调用方负责。所有检索、有效语料读取及正文补齐都使用同一连接/快照，运行中scope、EmbeddingSpace、构建版本或快照身份变化会拒绝。

错误主要分为invalid_retrieval_request/scope、encoder_embedding_space_mismatch/embedding_failed/invalid_query_vector、repository_failed，以及各项明确的source/version/span/snapshot错误码。空候选则正常返回空tuple，不表示全量数据不存在相关证据。

## 4. 实际测试

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_semantic_retrieval.py -v
.venv/Scripts/python.exe -B outputs/rag-semantic-retrieval/p0-20261105/run_confirmed_checks.py
```

| 测试 | 结果 |
|---|---|
| 新专项 | 28/28通过，0失败、错误、跳过 |
| 专项及适用回归 | 318项：311通过，7项既有AppContainer OS专项未重跑，0失败/错误；包含上述28项，不重复相加 |
| 两项既有队列测试 | 单独执行，0通过、2错误，仍为SQLite进入仅支持PostgreSQL的队列；未修原逻辑 |
| 真实PG连接、真实模型调用、业务写入、向量重建 | 全部0 |

合成覆盖正常/空检索、原问题及排名保持、同评论多观点、tie、显式过滤、无距离阈值、scope/UTC/as_of边界、空间/版本漂移、非法向量、来源缺失/重复、完整快照截断、quote区间/哈希、人工修订来源、导入身份冲突、统一错误和资源释放。生产适配器测试用FakePG并mock原查询函数，证明复用与同连接调用关系，**不冒充实际pgvector联调**。

回归包括现有RAG语料、Embedding契约/向量校验、查询契约、来源/历史快照、人工裁决及E1离线模块。测试驱动禁止真实网络和psycopg.connect；语料回归的SQLite仅为合成临时数据库。首轮驱动未加入仓库根路径，导致test_embeddings导入scripts失败；修正驱动后完整重跑，初次日志保留。

证据位于Git忽略目录`outputs/rag-semantic-retrieval/p0-20261105/`：specialist-confirmed、regression-confirmed、known-queue-errors-confirmed日志/汇总及执行驱动。

## 5. 当前限制和下一步

- 真实本地BGE-M3和PostgreSQL尚未联调，本轮不能声称真实检索质量已验证。来源政策仅为当前数据库观察，不升级Step4A历史来源或B2参考。
- 生产补齐为最小复用实现：额外读取一次范围内有效语料，并逐命中评论调用get_evidence。默认Top-K=5；大范围性能未测，后续可独立做批量补齐优化，当前不改统计SQL。
- 不提供自动QueryContract解析、Agent注册、API、前端、充分性判断或业务答案。下一步可将该服务接为独立Agent工具，并做候选→运行时事实契约适配；不需要新增人工裁决体系。
- 108份既有源码摘要复核全部不变，git diff --check通过；已有Agent、R0和strict接口保持不变；B2、人工会话、历史评分和冻结目录未写入。新增模块会使B3的全源码清单出现差异，旧冻结配置保持原样，未来真实预测应另版绑定届时源码；本轮不重新冻结或运行E1。

完成P0后停止。
