# RAG Step 2A：有效观点语料契约与来源追踪

日期：2026-11-02（Asia/Taipei）。本阶段先完整阅读 [第一阶段审查报告](rag-data-structure-audit.md)，再核对当前源码，实现内部只读语料接口。没有向量表、Embedding 请求、模型调用、业务库写入或助手链路改动。

## 1. 交付与入口

| 文件 | 改动与职责 |
|---|---|
| `src/data_analysis_agent/reviews/scope.py` | `current_reviews_sql(..., include_opinion_provenance=False)` 增加默认关闭的来源列选项；保留有效标注选择规则和默认六列输出 |
| `src/data_analysis_agent/reviews/rag_corpus.py` | 新增 `RagOpinionDocument`、`RagReviewState`、`RagCorpus`、`RagCorpusIntegrityError` 和 `read_effective_corpus` |
| `tests/test_rag_corpus.py` | 隔离数据库实际执行测试，复用既有 `WorkbenchFixture` 创建合成数据及标注，不请求外部模型 |
| 本文件 | 接口、来源、哈希、验证与尚未覆盖的边界说明 |

内部 Python 入口：

```python
from data_analysis_agent.reviews.rag_corpus import read_effective_corpus

# db 由调用方提供：已有数据库路径/DSN，或已有事务的应用数据库连接。
# 以下身份、商品与日期是隔离夹具示例，不代表实际业务配置。
corpus = read_effective_corpus(
    db,
    model="SIMULATED-model-A",
    base_url="https://offline.invalid/v1",
    prompt_version="prompt-A",
    source="amazon_reviews_2023",
    dataset_version="amazon-reviews-2023",
    product_ids=["P1"],
    date_from="2023-01-01",
    date_to="2023-01-06",
)
```

这是内部函数，不增加 HTTP 路由、CLI 命令、队列任务或自动执行步骤。未修改现有 API 的参数/响应 Schema、统计公式、标注或人工保存路径；既有调用方不传新选项，继续得到原来的观点输出。

## 2. 唯一有效选择来源

`read_effective_corpus` 调用现有 `analysis_scope` 规范化身份/范围，并调用：

```python
current_reviews_sql(connection, identity, include_opinion_provenance=True)
```

这里 `identity = (model, base_url, prompt_version)`。有效标注仍完全由原 CTE 决定：

1. 标注三元组必须匹配，标注内容哈希必须等于当前评论哈希。
2. 同一评论按成功优先、再按尝试 ID 倒序选择；不是取全局最大标注 ID。
3. 对已选标注采用最新人工修订；有人工修订时完整替代模型观点，空人工列表也不回退。
4. 无人工表时继续支持模型观点；人工表不完整时沿用原错误，不能静默忽略。

新增选项仅在原来的两个 SELECT 分支上补列：

| 分支 | `opinion_source` | `model_opinion_id` | `human_opinion_id` |
|---|---|---|---|
| 模型 | `'model'` | `review_opinions.id` | `NULL` |
| 人工 | `'human'` | `NULL` | `review_human_opinions.id` |

导出查询将 `current` 与这个 `effective_opinions` LEFT JOIN，取得 `annotation_id`、`revision_id`、评论身份和真实来源行。没有另外查询 MAX 标注或重新选择人工版本。评论状态上的 `opinion_source` 仅解释已经选中的状态，不参与标注选择。

默认关闭新选项时，`effective_opinions` 仍严格为 `review_id,aspect,sentiment,quote,quote_start,quote_end`；模型与人工分支数量、过滤和替代关系都不变。兼容性测试检查了默认列名、公共观点返回字段及读取前后的统计结果。

## 3. 数据结构与范围

`RagCorpus` 包含：`scope`、`label_profile_key`、`documents` 元组、`reviews` 元组及 `contract_version="rag-effective-opinions-v1"`。

### 3.1 每条观点：`RagOpinionDocument`

| 字段组 | 字段与类型 | 来源/意义 |
|---|---|---|
| 数据身份 | `source,dataset_version,product_id: str`；`review_id: int` | 真实评论身份，没有 `dataset_id` |
| 标注配置 | `requested_model,base_url,prompt_version,label_profile_key: str`；`annotation_id: int` | 显式请求的规范化三元组、配置哈希及当前有效标注 ID |
| 来源版本 | `opinion_source: Literal["model","human"]`；`model_opinion_id,human_opinion_id,human_revision_id: int \| None` | 真实来源行，两个观点 ID 必须二选一；人工必须有当前修订 |
| 观点内容 | `aspect: Aspect`；`sentiment: Sentiment`；`quote: str`；`quote_start,quote_end: int` | 沿用六维度、四种情感及 Python 字符区间 |
| 内容/版本校验 | `review_content_sha256,opinion_text_sha256,embedding_input_sha256,stable_versioned_opinion_key: str` | 评论哈希、实际原文哈希、输入哈希和版本化来源键 |
| 输入 | `embedding_input: str`；`text_builder_version: str` | 首版直接等于 `quote`，构造版本 `quote-utf8-v1` |
| 时间 | `published_at: str` | 保留当前数据库 UTC ISO 文本，不改为另一种统计时间口径 |
| 原文证据 | `import_run_id,line_number: int`；`file_sha256,evidence_ref: str` | 文件指纹、起始物理行及既有证据格式 |

数据类为 frozen。没有输出完整评论正文、用户 ID、原始 JSON、服务器文件路径或复核人；`quote` 本身仍是原始内容，接口仅供内部本机处理，不能据此默认授权向外部服务发送。`base_url` 在对象 repr 中隐藏，但作为内部身份字段和 scope 仍保留；它没有密钥，序列化时也不应当作可公开配置随意记录。

### 3.2 每条评论：`RagReviewState`

保留 `source,dataset_version,product_id,review_id,review_content_sha256,published_at`，以及 `annotation_id,human_revision_id,processing_status,human_status,opinion_source,error_code,opinion_count,effective_snapshot_sha256`。配置身份由所属 `RagCorpus` 绑定，也纳入摘要计算。

| 真实情况 | `processing_status` | `opinion_source` | `opinion_count` / 版本 |
|---|---|---|---|
| 模型成功且无观点 | success | model | 0，有标注 ID，无人工修订 |
| 人工纠正为空 | success | human | 0，有最新人工修订，不回退模型 |
| 标注失败 | failed | `None` | 0，保留所选失败标注及错误码 |
| 当前配置/内容尚无标注 | pending | `None` | 0，没有匹配标注 ID |
| 空正文 | empty | `None`（正常数据） | 0，沿用既有状态 |

有效集合被删空时仍返回评论状态和摘要，避免只读观点列表无法发现失效。返回的 `reviews` 是范围内所有评论；`documents` 仅包含有效且通过原文校验的观点。一条评论可以对应多条文档，不把文档数作为评论数。

### 3.3 范围与顺序

- 必须指定非空 `source`、`dataset_version` 和完整模型三元组，起止日期沿用 `analysis_scope` 的必填与有效性校验；无效参数在打开数据库前拒绝。
- `product_ids=None/[]` 表示该数据集全部商品；非空集合沿用现有去空格、去重和排序，并验证每个商品确实属于指定数据集，越界不静默返回其他商品。
- 日期和 `as_of` 沿用 `review_scope`：UTC 完整当天边界，`as_of` 只过滤评论发布时间，不表示人工修订截止时间。
- 稳定排序为评论 ID、观点开始/结束位置、维度、情感、模型/人工来源行 ID。版本键本身不依赖顺序、分页位置、商品筛选顺序或任务内临时主题 ID。
- SQL 参数绑定，函数只执行读取；不读取模型密钥，不调用初始化/迁移函数。

## 4. 原文一致性与失败契约

构造文档前检查来源 ID 与分支一致、状态为成功、标注 ID 非空、维度与情感有效、原文非空白，以及：

```python
type(quote_start) is int
type(quote_end) is int
0 <= quote_start < quote_end <= len(review_text)
review_text[quote_start:quote_end] == quote
```

这里的区间是 Python 字符串左闭右开区间，不是 UTF-8 字节位置或浏览器 UTF-16 索引。没有重新用 `find` 猜测位置，也不改写原文。严格 UTF-8 编码失败也拒绝导出。

发现不一致时抛出 `RagCorpusIntegrityError`，整次读取不返回语料对象，避免静默丢行后误称完整成功。不回退旧模型观点。异常只包含评论 ID 和错误类别，不包含 quote 或正文。隔离测试直接构造了损坏的模型原文和人工区间，实际运行验证两者都被拒绝。

## 5. 确定性哈希与版本键

固定使用 SHA-256 完整小写十六进制结果，不截断。定义 `H_json(value)` 为项目已有 `content_sha256(value)`：`json.dumps(...,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)` 后取 UTF-8 字节哈希。这里不更改已有评论哈希算法。

1. **标注配置键**：`label_profile_key = H_json([requested_model,base_url,prompt_version])`，与当前队列配置哈希约定一致；使用现有 `model_identity` 规范化后的三元组，不额外合并不同服务地址。
2. **观点原文哈希**：`opinion_text_sha256 = SHA256(quote.encode("utf-8"))`。
3. **Embedding 输入哈希**：首版 `embedding_input=quote`，所以 `embedding_input_sha256=opinion_text_sha256`。没有 trim、大小写转换、换行转换、空白压缩、Unicode NFC/NFD 规范化，也不拼接标签、商品名、评分或用户身份。
4. **版本化来源键**：前缀 `rag-opinion-v1:` 加下列数组的 `H_json`：

```text
[
  source, dataset_version, product_id, review_id,
  review_content_sha256, label_profile_key, annotation_id, human_revision_id,
  opinion_source, model_opinion_id, human_opinion_id,
  aspect, sentiment, quote_start, quote_end, opinion_text_sha256
]
```

来源键表示这一版来源记录，不是跨修订稳定业务观点 ID。新人工修订、模型身份、评论哈希、原文区间或标签变化都会改变它；即使有人绕开正常保存入口就地修改维度/情感，来源行 ID 不变，也能识别版本变化。相同文本跨多评论可以有相同输入哈希，但必须保留不同来源键。

5. **评论有效集合摘要**：`H_json([CORPUS_CONTRACT_VERSION,label_profile_key,state,sorted(versioned_opinion_keys)])`。`state` 是 `RagReviewState` 除摘要本身以外的全部字段。它覆盖状态、所选标注、人工修订、计数及完整观点集合，空集合也有摘要；它不是提交顺序水位。

测试固定了“好用”的原文哈希、合成配置键和完整版本键常量，并验证包含 emoji、CRLF、前后空白、制表符以及组合/分解重音字符的文本逐字保留。重复导出和缩小商品范围不会改变相同来源记录的键或摘要。

## 6. 合成语料样例

以下 JSON 来自自动化隔离夹具的 `dataclasses.asdict(corpus.documents[0])`，不是业务库评论；ID 只在这个临时数据库中有意义。示例服务地址是不可用的测试地址。

```json
{
  "source": "amazon_reviews_2023",
  "dataset_version": "amazon-reviews-2023",
  "product_id": "P1",
  "review_id": 1,
  "annotation_id": 1,
  "requested_model": "SIMULATED-model-A",
  "base_url": "https://offline.invalid/v1",
  "prompt_version": "prompt-A",
  "label_profile_key": "6b27e4e29ab093c0930d55e17d92bcd83deac9c1452c4ad60b4f70b44818d9fd",
  "opinion_source": "model",
  "model_opinion_id": 1,
  "human_opinion_id": null,
  "human_revision_id": null,
  "aspect": "effectiveness",
  "sentiment": "positive",
  "quote": "好用",
  "quote_start": 0,
  "quote_end": 2,
  "review_content_sha256": "f41a90a957832e5687dc825936a55cd65ffe16b9276fb45c19e23932a3e45dd9",
  "opinion_text_sha256": "c5c9ba1747a15ad4c6cacdc9cd0bb94e94bd31b1cf3c8b73b93938bf8d219bb6",
  "embedding_input": "好用",
  "embedding_input_sha256": "c5c9ba1747a15ad4c6cacdc9cd0bb94e94bd31b1cf3c8b73b93938bf8d219bb6",
  "stable_versioned_opinion_key": "rag-opinion-v1:8b09a1d4b00a98fa0398229260c79622d57105b21752779eba9dc5937e3b47b2",
  "published_at": "2023-01-01T00:00:00.000+00:00",
  "import_run_id": 2,
  "line_number": 1,
  "file_sha256": "481537f8c1a469de361b9340ebc04d2d9af768cbd137e4159aaec5e1cad15107",
  "evidence_ref": "sha256:481537f8c1a469de361b9340ebc04d2d9af768cbd137e4159aaec5e1cad15107#line=1",
  "text_builder_version": "quote-utf8-v1"
}
```

这条合成评论实际有三条观点，示例仅展示其中一条；评论状态的 `opinion_count` 为 3。人工确认后输入文本哈希可保持相同，但 `opinion_source`、人工行 ID、修订 ID、来源键及集合摘要都会更新。

## 7. 自动化测试覆盖

新增测试使用 `TemporaryDirectory` 下的独立 SQLite，真实执行现有导入、标注保存、人工复核与新读取函数；模型输出由合成数据直接构造，没有外部模型调用。损坏数据和就地变更也只发生在临时数据库。

| 必验场景 | 测试方法名（`tests/test_rag_corpus.py`） |
|---|---|
| 一条评论多个观点、不同维度/情感 | `test_multiple_opinions_keep_real_source_ids_dimensions_and_sentiments` |
| 请求模型、服务地址、提示版本分别隔离 | `test_model_endpoint_and_prompt_configs_never_mix` |
| 成功与失败共存，成功后更大 ID 失败仍不覆盖 | `test_success_wins_even_when_a_later_failure_exists` |
| 成功空观点、失败、待标注、空正文区分 | `test_successful_empty_failed_pending_and_empty_body_are_distinct` |
| 人工确认的真实人工来源行 | `test_human_confirmation_uses_human_rows_and_preserves_text_hashes` |
| 人工改选文本、采用最新完整快照 | `test_human_text_correction_and_latest_revision_replace_complete_set` |
| 人工改维度/情感，覆盖中性与未知 | `test_human_dimension_and_sentiment_changes_only_invalidate_version` |
| 人工空快照、不回退，再次空修订也改变摘要 | `test_human_empty_snapshot_is_visible_and_never_falls_back` |
| 评论哈希变化隔离旧模型和人工修订 | `test_content_hash_change_excludes_old_model_and_human_versions` |
| 不同商品、来源、数据集及 UTC 边界 | `test_products_sources_datasets_and_utc_dates_are_isolated` |
| Unicode、换行、空白和实际字符区间 | `test_unicode_whitespace_offsets_and_raw_utf8_hashes_are_preserved` |
| 重复读取、缩小商品范围、数据库文件不变 | `test_repeat_export_order_subset_and_readonly_file_are_stable` |
| 固定哈希和来源键常量 | `test_hash_contract_has_fixed_golden_values` |
| 来源 ID 未变的标签变化仍被识别 | `test_in_place_metadata_changes_are_detected_even_if_ids_are_unchanged` |
| 损坏模型原文、人工越界区间拒绝导出 | `test_invalid_model_spans_fail_whole_export_without_leaking_quote`、`test_invalid_human_spans_do_not_fall_back_or_silently_drop` |
| 默认 CTE 和公共接口字段/统计不变 | `test_default_cte_and_public_api_shapes_remain_unchanged` |
| 共享事务必需、调用不提交事务 | `test_shared_transaction_is_required_and_not_committed` |
| 并发人工修订不会混入已打开的读快照 | `test_shared_snapshot_does_not_mix_a_concurrent_human_revision`（隔离 SQLite WAL） |
| 可选人工表缺失兼容、不完整表拒绝 | `test_missing_human_tables_work_and_partial_schema_fails` |
| 非法范围在开库前拒绝 | `test_invalid_scopes_fail_before_opening_database` |

多项测试还用默认 `current_reviews_sql` 独立计算有效观点数量，要求与文档数、评论状态观点数之和相同。人工分支是实际运行测试，不是只读源码后推断为通过。

新增专项执行命令：

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_rag_corpus.py -v
```

结果：**21 项全部通过**。三个新增/修改的 Python 文件也通过语法解析检查。

相关评论回归与新专项合并执行：**共 321 项，318 项通过、3 项因未配置隔离 PostgreSQL 环境跳过，0 失败、0 错误**。范围为 `test_review*.py` 和 `test_rag_corpus.py`，覆盖既有导入、标注、人工复核、统计、证据、主题及助手相关用例。[最终回归记录](../output/rag-step2a-regression-local.log)

复现时须在测试进程内指定 `LABEL_EXECUTION_MODE=local`：本机业务配置使用 Redis，直接运行临时 SQLite 标注测试会误入 PostgreSQL 专用队列，首轮出现 1 个失败及 23 个错误；进程级隔离后同一套测试通过。没有修改 `.env` 或业务运行模式。Windows 默认受限环境的本地异步套接字初始化也曾阻塞，最终回归使用正常本机权限，所有模型传输仍为模拟。

以下 Python 代码从项目根目录执行，等价地构造本轮最终回归集合：

```python
import os
import unittest

os.environ["LABEL_EXECUTION_MODE"] = "local"  # 仅当前测试进程
loader = unittest.TestLoader()
suite = loader.discover("tests", pattern="test_review*.py")
suite.addTests(loader.discover("tests", pattern="test_rag_corpus.py"))
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
```

另将修改前 HEAD 中的 `current_reviews_sql` 与当前函数在隔离库中生成的默认 SQL 及参数逐字比较，结果完全相同。新选项的扩展列只由新内部接口启用。

## 8. 真实 PostgreSQL 与原 SQL 一致性

本轮再次只读连接真实 `reviews`，快照时间 **2026-11-02 23:50:23 +08:00**，实测 `transaction_read_only=on`、隔离级别 `repeatable read`。

在同一事务中遍历两组实际数据集及两组实际标注三元组，执行新接口，并使用默认关闭来源选项的原 CTE 查询有效观点。检查不仅比较数量，还在本机对 `(review_id,aspect,sentiment,quote,quote_start,quote_end)` 多重集合逐项比较，保留重复次数；同时要求所有版本键唯一，所有评论状态观点数之和等于 SQL 数量。真实正文/quote 只参与本机比较，没有输出到报告或外部服务。

| 来源 / 数据集版本 | 请求模型 | 范围内评论 | 导出观点 | 原 SQL 观点 | 多重集合完全一致 |
|---|---|---:|---:|---:|---|
| amazon_reviews_2023 / amazon-reviews-2023 | deepseek-ai/DeepSeek-V4-Flash | 6,941 | 41 | 41 | 是 |
| amazon_reviews_2023 / amazon-reviews-2023 | Pro/deepseek-ai/DeepSeek-V3.2 | 6,941 | 4,169 | 4,169 | 是 |
| reviews_csv / reviews-csv | deepseek-ai/DeepSeek-V4-Flash | 9,573 | 0 | 0 | 是 |
| reviews_csv / reviews-csv | Pro/deepseek-ai/DeepSeek-V3.2 | 9,573 | 10,676 | 10,676 | 是 |

其中 CSV + V4-Flash 的 9,573 条评论全部为该配置下待标注，返回 0 文档但保留全部评论状态，不能误报为“成功标注且没有问题”。V3.2 的两个数据集分别有 2,251 和 5,117 条成功评论；它们对应的观点数与评论数不同。

人工修订/人工观点实测仍为 **0 / 0**。因此真实 PostgreSQL 检查证实模型路径、SQL 兼容和读取契约；人工确认、纠正和删空的运行证据来自第 7 节的隔离数据，没有向真实库插入人工案例。

## 9. 版本、并发与规模边界

1. **读快照不是同步提交协议。** 直接传路径/DSN 时复用 `analytics._snapshot` 建立只读事务；PostgreSQL 的 `BEGIN` 沿用桥接层转换成只读重复读。传已有应用连接时必须已有事务，函数不提交/回滚该共享事务；调用方负责该事务只读及隔离级别。不能将一个普通 READ COMMITTED 连接误称为重复读。
2. **函数返回后数据仍可能变化。** 人工修订、标注或删除可在下一个快照中改变有效集合。摘要可用于识别差异，但本轮没有写入前重验、锁协议、向量发布或同步 Worker；后续发布/检索仍须重验当前身份及来源。
3. **不提供持久化修订历史快照或增量水位。** `as_of` 仍只管评论发布时间；集合摘要不是可按提交顺序扫描的游标。删除评论后它会从结果消失，未来同步需对账，不能只扫描新增观点。
4. **身份稳定范围是同一个业务库。** 来源键包含数据集、商品、评论/观点行及内容版本，不承诺跨数据库重新导入后保持同一个键，也没有新增跨库/租户身份。未来若合并多个业务库，应先补充明确的语料库命名空间。
5. **不提供跨修订语义观点合并。** 人工确认或重新保存相同文本会产生新来源键，但输入哈希可保持相同，为未来复用向量提供依据。当前不建缓存、不做同义去重，也不把两个来源折成一条证据。
6. **现有模型版本语义保持原样。** 服务商同名模型漂移和标注提示哈希覆盖范围问题仍按审查报告列为边界；本轮没有更改标注身份算法或强制重标注机制。
7. **首版按范围在内存返回完整元组。** 未实现流式导出、分页游标或持久化文件。当前真实规模已完成只读对账；大规模语料应在后续单独设计受同一快照约束的分批接口，不能把多次独立分页当作一致快照。
8. **校验失败采用整次拒绝。** 当前库如出现脏观点，需要调用方处理完整性错误，不能通过静默跳过来维持表面数量一致。本轮不修复或写回原数据。

隔离 PostgreSQL 管理连接未配置，既有 PostgreSQL 集成测试的三个用例按环境跳过；未声称已在隔离 PostgreSQL 上运行人工写入或并发发布测试。本轮也未做压力、Embedding 效果或 ANN 查询验证。

## 10. 完成范围

交付到内部有效观点读取契约、来源追踪、确定性版本/输入哈希、自动化测试和真实库只读对账为止。没有新增依赖、数据库迁移、向量表、真实向量、数据回填、LangGraph 或助手工具；既有评论与标注仅在隔离测试夹具中构造和修改。

本轮没有前端变更，所以没有运行前端构建或浏览器页面验收。下一步向量建设与 Embedding 接入需要另行开发指令，本阶段停止于此。
