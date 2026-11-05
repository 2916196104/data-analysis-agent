# P2 真实环境联调：执行结果

2026-11-05。用户在预检后明确授权，现已完成本轮真实联调。**真实编码、pgvector 检索、来源补齐及 Agent 回答链路已执行，但尚未获得一个带原文引用的正常 RAG 答案。** 不能据此宣称 RAG 问答质量验收通过。未改业务代码、.env、模型、数据库、向量、R0、B2 或人工裁决。

## 实际结果

共执行 5 个语义问题入口及 1 个 SQL 问题；其中一个在商品识别阶段返回 unsupported，故实际仅 4 次本地查询编码。无自动重试，无外部模型调用，无 B3-B。首问初始化、来源校验成功后才继续；第二问意图不支持时驱动先停下，确认非初始化/来源失败后仅继续剩余用例，没有重跑或改写第二问。

| 用例与原问题 | 候选数 | 实际结果 | 总耗时 |
|---|---:|---|---:|
| 01 有没有制冷不足的反馈？ | 5 | 前三名已有制冷效果差/失效相关自述，后两名为正面制冷评价；严格字面及上下文政策全部拒绝，公开引用为 0 | 19.866 秒 |
| 02 有没有空调使用十年仍然耐用的反馈？ | 未检索 | 将“有没有空调”误认成商品名称，返回 unsupported；未覆盖预期的长期事实充分性检查 | 0.178 秒 |
| 03 有没有制冷不快的反馈？ | 5 | 召回主要是效果差、不制冷及正面评价，不能直接等价于速度慢；没有许可，公开引用为 0 | 13.569 秒 |
| 04 有没有同一评论既说制冷好又抱怨噪音大的反馈？ | 5 | 返回片段没有直接证明同评论中的两个条件；没有跨评论拼接。复杂语义也超出本地许可覆盖 | 13.231 秒 |
| 05 有没有制冷不足的反馈？（日期缩小为 2021-09-11） | 0 | 该日没有索引向量，但有效语料有 1 条评论/1 个观点；明确说明未返回候选，不声称全量没有反馈 | 9.052 秒 |
| 06 所选商品好评主要集中在哪些方面？ | 不适用 | 旧 SQL 统计及模板回答通过，分母为含正向观点评论数；无 LLM 调用 | 0.532 秒 |

01 是明确的实用性失败案例：已有相关候选，但 `_literal_target` / `assess_retrieval` 要求 quote 与问题命题完全相同，而且完整评论每句都必须属于很小的语句集合，导致拒答。此处是本轮人工查看结果的技术分析，**没有生成或修改充分性金标准**。03 的“制冷效果差”与“制冷速度慢”不能互换，不将拒答自动判为错误；04 也不以主题相关代替关系成立。

最终回答：01/03/04 均明确表示候选不能确认所问结论；05 明确表示未返回候选不代表全量不存在；02 要求提供可识别商品；06 正常返回可验证的统计说明。所有语义用例最终公开引用均为 0，因此真实“许可事实→引用答案”路径本轮没有正例，不能用结构通过掩盖此缺口。

### 分阶段实测耗时

下表单位毫秒；检索总计包含有效语料读取及 pgvector，补齐总计包含再次读取有效语料和逐评论上下文，不应重复相加子项。

| 用例 | 模型加载 | 编码 | 模型子进程总计 | 检索总计 | 其中 pgvector execute | 来源补齐 | 回答生成 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 01 | 8291.61 | 468.18 | 12852.94 | 252.56 | 17.29 | 5879.89 | 0.128 |
| 03 | 4482.27 | 340.38 | 7887.72 | 941.40 | 16.01 | 4393.36 | 0.163 |
| 04 | 4624.52 | 307.21 | 8414.01 | 194.50 | 10.54 | 4075.74 | 0.111 |
| 05 | 4728.27 | 314.54 | 8522.05 | 162.22 | 7.56 | 未调用 | 0.069 |
| 06 SQL | 不适用 | 不适用 | 不适用 | SQL 工具总计 285.67 | 不适用 | 不适用 | 1.824 |

CPU 为 4 线程；各模型进程峰值 RSS 约 1.68–1.69 GiB。模型进程记录的外连尝试均为 0。01/03/04 每次两次读取范围内 510 条评论、1091 条有效观点；本轮实际确认了这些数量。来源补齐约 4.08–5.88 秒，模型加载约 4.48–8.29 秒；单纯 pgvector 排序不是当前主要耗时。不同进程/缓存状态下的单次结果不等于稳定性能基准。

### 失败分类与可复现性

- 检索层：存在跨情感或相近主题候选，03 对速度的匹配不够精确；没有全范围相关性判断，不能声称已证明某个应召回观点被漏掉，也不计算 Recall。
- 证据层：01 相关自述被闭合语句规则拒绝，是当前最直接的功能阻塞；03/04 保守拒绝尚不能判为误拒。
- 回答层：忠实执行当前许可结果，未发现虚构引用；没有许可正例，正常引用生成仍未验证。
- 意图层：02 商品提取器将存在性提问前缀吞入商品名称，尚未进入 Embedding。
- 性能层：反复模型初始化及逐评论上下文补齐主导耗时；当前共享只读事务在编码时已打开。
- 每问题只运行一次，**未声称重复运行复现**。保留原问题、范围、代码摘要、结果与计时，可按相同配置再复现；未来重跑仍需另行授权。

### 执行证据与保护检查

实际命令（经执行权限批准）是：

```powershell
.venv/Scripts/python.exe -B outputs/rag-semantic-retrieval/p2-live-20261105/run_live.py
.venv/Scripts/python.exe -B outputs/rag-semantic-retrieval/p2-live-20261105/run_live.py --continue-remaining
```

私有目录 `outputs/rag-semantic-retrieval/p2-live-20261105/130353/` 保留前两问；`130514/` 保留余下四问、合并 summary 和 offline-check。逐问题 result 文件包含原始排名、quote、完整上下文、key/区间/版本、最终答案及实际引用，timings 文件包含各阶段实测。没有把真实评论/逐条引用复制进公开报告。

只读连接使用 REPEATABLE READ，实际检查只读模式；设置 statement/lock timeout，每次在同一事务确认范围评论不超过 510、向量数仍为 200。私有驱动将原连接退出时的 commit 改为 rollback，未改变业务数据。子进程不接收数据库凭据或真实评论；HTTP/LLM 在驱动内明确拦截。

P0/P1 校验通过 4 个语义执行结果和 1 个 SQL 结果；对实际保存的 15 个候选排名条目额外离线核验顺序、原文切片、quote/正文哈希与快照引用一致，全部通过。110 份源码前后哈希全部一致；git diff --check 通过，私有目录被 Git 忽略。本阶段没有重跑合成测试，不把原 457 项测试当作本次真实质量证据。旧两项队列错误未涉及也未修复。

## 下一步最值得优化的三个问题

1. **实际问答可用性**：修复存在性提问的商品实体误识别，并使证据判断能区分独立支持片段与无关上下文；增加真实表达对应的合成反例，不能针对本轮问题硬编码答案或放宽来源校验。
2. **模型运行边界与热复用**：将本轮验证成功的既有子进程适配作为正式可配置入口，减少每次 4–8 秒模型重载；默认 Worker 的进程内加载配置仍未解决，注入式成功不等于默认入口就绪。
3. **来源补齐性能**：优先测量并批量补齐当前逐评论读取，减少重复全范围语料读取；保留同事务、有效版本、精确区间和哈希检查。

本轮到此停止，不自动改规则、扩大测试集或再次运行真实预测。以下保留预检和原授权方案，供核对执行边界。

## 已实际核验

| 项目 | 实际结果 |
|---|---|
| 仓库初始状态 | clean；读取 P1 报告、P0/P1 源码、Worker 配置入口 |
| RAG_MODEL_CACHE | 当前有效配置未设置，默认 Agent 工厂会报 local_encoder_not_configured |
| 模型缓存 | `.cache/embedding-eval/hf/hub` 下固定修订 `5617a9f61b028005a4858fdac845db406aefb181` 的 8 个要求文件存在；权重 2,271,145,830 字节，tokenizer.json 17,098,108 字节。仅检查文件存在/大小，未加载模型或声称校验了权重完整性 |
| 业务 .venv | psycopg 3.3.6、hello-agents 1.0.1、pydantic 2.13.5；torch、transformers、sentencepiece 缺失 |
| 独立 Embedding Python | `.cache/embedding-eval/venv/Scripts/python.exe`；torch 2.9.1+cpu、transformers 4.57.6 实际导入成功；psycopg、hello_agents、pydantic、sentencepiece 无法导入。尚未验证 fast tokenizer 实际加载，不能仅因 sentencepiece 缺失判定必然失败 |
| PostgreSQL | 使用已有 DATABASE_URL；REPEATABLE READ READ ONLY，实际 read_only=on；15 秒 statement timeout、3 秒 lock timeout，结束 rollback/close |
| pgvector / 列 | 0.8.7 / vector(1024) |
| 索引 | 9 个 B-tree 均 valid、ready；没有 HNSW/IVFFlat。现有检索为 `<=>` 精确排序，200 条规模不要求新增 ANN 索引 |
| 指定范围向量 | 200 条，invalidated_at 为空的 200 条；单一空间、标签 profile 和文本构建版本一致 |
| 指定范围评论 | 510 条；当前内容匹配的指定版本 annotation 行为成功 505、失败 11，**是标注行数，不能相加当去重评论数** |
| 预检耗时 | 成功 PostgreSQL 元数据采集 0.191 秒，不是检索/模型性能 |

冻结使用范围：`reviews_csv / reviews-csv`、商品 `100013588055`、UTC 2021-09-11 至 2022-08-26。

标注 model=`Pro/deepseek-ai/DeepSeek-V3.2`；prompt_version=`review-opinions-v1-e61de599683cba6703c2f61e85125d0be4627c2763440486aa717e4bc884036e`；当前配置 base_url 与上述三元组计算的 profile=`1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304` 一致。该标注身份与 Agent 的模型配置不混用。

EmbeddingSpace=`embedding-space-v1:11e6754662dbd13fc56c6e624bc9416c1c2766f962787267fd4a57b1fa8d622e`，BAAI/bge-m3、1024、quote-utf8-v1。元数据相符不代表当前有效快照回连必然得到全部 200 条，须实际只读检索验证。

首个沙箱连接尝试报安全包装的 DatabaseError，未输出凭据；获执行权限后同一只读脚本成功。没有改服务配置或绕过权限。回执与 SQL 在忽略目录 `outputs/rag-semantic-retrieval/p2-preflight-20261105/`；成功回执为 `postgres-metadata-125028918039.json`。没有返回评论正文或向量值。

## 预检时的执行方案（授权后已执行，保留记录）

默认生产入口 `run_review_agent → ReviewToolContext → create_semantic_service` 在业务进程内构造 LocalBgeM3Embedder。**仅设置缓存路径仍不够，业务 Python 缺少模型依赖。**

建议先做一次注入式真实联调：在私有测试脚本中将现有 LocalEmbeddingProcess 的 encode() 适配到 P0 所需的 `.space/.embed_texts()`，校验子进程返回 space_id；使用 P1 已有 semantic_service_factory 注入点和真实 PostgresSemanticRepository。子进程仍调用既有 LocalBgeM3Embedder，只传查询文本，不传 DSN、评论或人工参考。不安装依赖、不改默认业务入口，不把注入入口成功称为默认 Worker 配置已就绪。

授权后的首个真实调用等价于下面命令（实际使用上方带计时与调用限制的私有驱动）：

```powershell
@'
from pathlib import Path
from data_analysis_agent.core.config import read_settings, AgentSettings
from data_analysis_agent.reviews.retrieval_evaluation import APPROVED_SCOPE, PROFILE_KEY
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.embedding_process import LocalEmbeddingProcess
from data_analysis_agent.reviews.embedding_store import SPACE
from data_analysis_agent.reviews.semantic_retrieval import SemanticRetrievalService, PostgresSemanticRepository
from data_analysis_agent.agents.reviews import run_review_agent
v = read_settings()
class Encoder:
    space = SPACE
    def embed_texts(self, texts):
        result = LocalEmbeddingProcess('.cache/embedding-eval/venv/Scripts/python.exe', '.cache/embedding-eval/hf/hub').encode(texts)
        if result['space_id'] != self.space.space_id: raise ValueError('embedding_space_mismatch')
        return result['vectors']
scope = dict(APPROVED_SCOPE, base_url=v['LLM_BASE_URL'], baseline_from='2021-09-01', baseline_to='2021-09-10')
assert content_sha256([scope['model'],scope['base_url'],scope['prompt_version']]) == PROFILE_KEY
def factory(connection, scope):
    return SemanticRetrievalService(encoder=Encoder(), repository=PostgresSemanticRepository(connection), scope=scope)
result = run_review_agent(v['DATABASE_URL'], '有没有制冷不足的反馈？', scope=scope,
    settings=AgentSettings.from_values(v), semantic_service_factory=factory)
print(result['status'])  # 正文和详细来源仅写入新建、Git 忽略的私有结果目录
'@ | .venv/Scripts/python.exe -B -
```

正式执行时在同一私有驱动增加 HTTP 调用硬拦截、输出保存和分段计时，不改判断规则。Agent 当前性能字段合并了编码/检索/补齐；需包装现有 search_effective_opinions、load_sources 和回答入口分别记录，不靠总耗时猜测。既有独立模型 worker 已返回 model_load_ms、encode_ms、CPU 时间和峰值 RSS，直接保存这些指标，无需改模型代码；尚未实际加载，不能虚构测量值。

### 申请读取范围

- 上述单商品、日期、source/dataset 与标注身份内的评论、有效观点、当前人工修订关联、原文、字符区间、哈希及导入来源元数据；不读原始 CSV/历史备份/人工评分。
- **P0 read_effective_corpus 会在内存中读取该范围全部有效语料及关联完整评论**，不能只授权 Top-5 或旧 57 条评论。当前范围最多涉及上述 510 条评论；有效观点数量本次未重数，不把历史 1091 条当作当前已核验数。
- 向量检索仍限已有 200 条派生向量和当前有效版本回连，不扩容；每问题 Top-5、原排名不改。结果仅保留候选和实际引用上下文。
- Agent 的产品绑定目前读取当前 dataset 的产品目录 ID/title/brand，以识别用户提到的商品；正文读取仍限所选商品。overview/统计读取同范围聚合，不读取 baseline 正文。需要一起授权这些必要目录及元数据读取。

### 首轮问题与资源

| 类别 | 原问题/操作 | 注意 |
|---|---|---|
| 普通自述 | 有没有制冷不足的反馈？ | 尝试得到正常带引用回答，不预设一定成功 |
| 证据不足 | 有没有空调使用十年仍然耐用的反馈？ | 若返回候选，单独核对语义覆盖与事实支持，不能按距离许可 |
| 否定 | 有没有制冷不快的反馈？ | 保留完整否定作用范围 |
| 复杂关系 | 有没有同一评论既说制冷好又抱怨噪音大的反馈？ | 不硬过滤，不把不同评论拼接 |
| 空候选 | 在原范围内通过向量日期计数找到无索引记录的一天，缩小日期后询问同一普通问题 | 先明确记录缩小的 scope；找不到此日期则记“未覆盖”，不伪造空结果或更换商品/模型 |
| SQL 兼容 | 所选商品好评主要集中在哪些方面？ | 沿用确定性 SQL 与模板，禁止外部 LLM |

串行最多 5 次本地查询编码及 1 次 SQL Agent 调用，不自动重试、不调用 E1/B3-B。权重约 2.27 GB，CPU 环境建议预留约 4–8 GB 可用内存；这是规划预算，不是测量值。已有子进程超时 600 秒/次；5 次模型阶段最坏约 50 分钟，数据库与校验另计，首次先跑 1 问，初始化或来源失败即停止。默认桥接每次重载权重，预计较慢，实际耗时待测；不以未知机器速度承诺完成时间。

## 效果记录口径

每问题保存原问题、scope、候选数量/原排名/key/quote/完整上下文及区间、许可理由、最终文本/引用、各阶段耗时和错误码。真实相关性由查看召回内容判断，不能把程序的 unknown 或旧人工分数当成新的金标准。

预检时没有实际问答案例；实际案例和阶段耗时现已记录在报告开头。仍没有完整相关性集，不能把未召回等同于全范围不存在。

## 最值得优化的三个工程问题

1. **模型运行环境接通和复用**：主环境与模型环境分离，默认入口目前不能加载；先用既有进程边界验证，再确定长期适配与热模型复用，避免每问题重载和长事务等待。
2. **证据许可的真实覆盖**：P1 要求整段上下文都属于小型明确语句集合，普通长评论容易被拒绝。应先看真实召回后定位误拒，不能为了个别问题修改答案或 R0。
3. **来源补齐的读取量和计时**：P0 重读范围内完整有效语料并逐候选评论补齐，可能是主要 SQL/内存开销；先拆分测量，再按结果考虑批量补齐，保留版本与区间校验。

上述范围已获用户明确授权并执行，实际结果见报告开头。本轮已结束，不自动继续其他实验。
