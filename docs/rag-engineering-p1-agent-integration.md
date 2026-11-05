# RAG 工程集成 P1：Agent 接入与合成端到端验证

日期：2026-11-05。

本阶段将 P0 接入现有 `run_review_agent()`，建立独立的语义检索工具、来源复核、受限证据许可和带引用的回答路径。**这是一条可执行的、保守的本地回答链路；不是通用语义推理器，也不是 E1 或真实检索质量验收。** 未执行真实 BGE-M3、业务 PostgreSQL、外部模型或 B3-B 预测。

## 1. 实际运行链路

```text
run_review_agent(原问题、当前 scope)
  → 原商品目录与 BaselineScope 校验
  → resolve_review_request / build_query_plan
  → 现有 ReviewRegistry / ReviewTool
  → search_semantic_evidence（无模型可写参数）
  → SemanticRetrievalService.search(原始问题)
  → encoder.embed_texts → 原 pgvector search_effective_opinions
  → P0 独立来源补齐与校验
  → 本地明确自述许可 + validate_report
  → adapt_result / AnalysisResultCompressor
  → NaturalLanguageAnswerGenerator 的确定性分支
  → PublicAnswer / 原 answer 文本与精确来源引用
```

现有队列 Worker 调用 `run_review_agent` 的接口兼容，不改 API、前端或任务提交协议。语义计划沿用现有“确定计划直接执行”分支，仍经过同一注册表、调用预算、缓存、trace 和发布校验，不需要额外 LLM 规划。合成端到端测试实际执行上述 Agent 代码和 P0 服务，仅替换 encoder 与 repository；原 pgvector SQL 的复用和事务行为继续由 P0 适配器合成测试覆盖，未冒充真实 PostgreSQL 联调。

## 2. 路由、范围与过滤

- 识别“有没有/是否有……评论、反馈、抱怨、证据”“找/检索/搜索……评论、原文、证据”及显式语义检索请求。原始问题，包括空白，完整进入 encoder，没有改写成关键词。
- 数量、比例、趋势、排名、分布等问题继续走现有确定性 SQL 工具；普通 SQL 原文浏览与分页保留。首版不支持任意统计与语义子问的自动拆分。
- 固定继承 source、dataset_version、唯一商品、UTC 日期、as_of 和标注 model/base_url/prompt_version。多商品歧义、范围外商品、越界日期在编码前拒绝。显式日期只能缩小当前范围；相对日期、基线和语义分页明确不支持。
- 工具不接受 question、product_ids、日期、模型、top_k 或过滤参数。问题和范围由当前任务绑定，Top-K 固定为 5；工具不能自行改写或扩量。相同调用命中当前任务缓存，不重复编码。
- 只将明确、单一的一级分类与情感作为可选过滤；混合情感、多主题、转折、关系或不确定查询不强行单选。不推断“制冷”必定属于某个 SQL 维度。
- 既有情感识别器会把“有没有”中的“没”误当后续情感词的否定；适配器只在计算过滤提示时移除这个存在性提问前缀，原问题不变，不修改旧情感规则。
- 语义检索后的省略追问要求补全检索问题，不静默改成 SQL 时间排序；构造“已确认工具调用”不能绕过原问题绑定。

## 3. 候选、事实许可与引用

P0 结果保存原排名、距离、观点版本 key、annotation/revision、完整评论、精确 quote 区间、正文摘要、完整有效观点快照摘要、导入文件指纹及行号。消费端再次核对问题、范围、EmbeddingSpace、来源版本、正文哈希、quote 切片及 key；按当前政策重新计算许可，不信任持久化的 `sufficient` 标记。

新增政策 `literal_reported_statement_v1` **不调用也不修改 R0**。只处理少量完整明确自述，例如“有没有制冷不足的反馈？”或“这款商品有没有制冷不足的反馈？”。许可条件为：

1. 问题能识别为单个明确命题；quote 整句与该命题相同，不按关键词、同维度、向量距离或同义猜测升级。
2. 引文在真实评论的独立句中，完整区间保留。完整评论只能用于解释或否决。
3. 上下文必须属于当前能够确定处理的独立自述句集合；未知上下文、否定/纠正/转述/假设、同一对象的不同断言均保守拒绝。quote 外的其他事实绝不加入支持集合。
4. 仅许可“检索到的一位买家明确这样报告”，不许可客观性能、根因、总体发生比例、长期耐用性或睡眠影响等推论。

无法识别的改写、多条件、混合情感和关系问题记为 `query_semantics_uncovered` 或相应 unknown；这属于规则覆盖不足，**不等于真实证据一定不相关**。空候选单独记为 `no_candidates`，不表达为全量数据不存在。当前仅支持单事实许可，因此没有自动生成 limited 子结论。

只有许可的 quote 进入公开 `EvidenceReference`，新增可选的 opinion_key、annotation_id、effective_snapshot_sha256；review_id、文件指纹/行号、quote_start/quote_end 继续保留。不裁剪许可引文，不给回答模型完整评论或相似度。未许可候选保存在原始工具结果中，公开回答明确说明无法确认结论。执行成功的结果 `status=ok` 表示检索/校验完成，是否有回答许可看独立 assessment；错误则 `status=error`，不发布成功业务回答。

现有公开历史读取入口也重建校验与许可，篡改来源、区间、上下文、许可或策略会关闭公开回答。这里是任务结果一致性检查，不宣称重新取得历史独立可信来源。

## 4. 文件变更

| 文件 | 作用 |
|---|---|
| 新增 `src/data_analysis_agent/agents/review_retrieval.py` | 路由判断、最小过滤适配、本地服务工厂、明确自述许可、消费端校验与引用适配 |
| `agents/review_tools.py` | 注册独立工具，绑定原问题/服务工厂，保留安全错误码和执行统计 |
| `agents/review_plan.py`、`agents/review_intent.py` | 单商品语义计划、范围约束、省略追问保护；保留旧统计计划 |
| `agents/reviews.py` | 注入服务工厂，原调度内执行；语义结果不进入 SQL 补查循环，不调用外部回答模型 |
| `agents/review_validation.py` | 将语义来源与许可校验纳入原发布检查 |
| `agents/review_results.py` | 适配语义结果及可追溯观点字段，旧字段兼容 |
| `agents/review_compression.py` | 仅压缩许可引用，保留精确区间，支持历史读取重校验 |
| `agents/review_answers.py` | 沿用 PublicAnswer 与生成入口，确定性生成带引用答复或不足说明 |
| `core/config.py` | 可选 `RAG_MODEL_CACHE` / `AgentSettings.rag_model_cache`，默认未配置 |
| 新增 `tests/test_review_semantic_integration.py` | 28 项 P1 合成测试 |
| 本报告 | 实现、证据与真实联调边界 |

表内省略的源码前缀均为 `src/data_analysis_agent/`。未改 P0、向量存储、Embedding 实现、统计 SQL、R0、B2 参考、人工裁决、strict trusted 接口或历史审计材料。没有新增依赖或前端变更。

## 5. 验证与执行证据

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_review_semantic_integration.py -q
.venv/Scripts/python.exe -B outputs/rag-semantic-retrieval/p1-20261105/run_checks.py
git diff --check
```

测试证据保存在 Git 忽略的 `outputs/rag-semantic-retrieval/p1-20261105/`，最终日志/汇总使用 `specialist-release`、`regression-release`、`known-queue-errors-release`。全部数据为合成，SQLite 仅为现有临时夹具；没有读取私有真实评论制作测试。

| 实际执行范围 | 结果 |
|---|---|
| 新增 P1 合成端到端/边界测试 | 28/28 通过 |
| P1 + P0 专项 | 56/56 通过，包含上述 28 项 |
| 专项及适用回归 | 457/457 通过，0 失败、0 错误、0 跳过；包含上述 56 项，不重复相加 |
| 既有两项标注队列测试，单独执行 | 0 通过、2 错误；SQLite 夹具进入 PostgreSQL-only 持久标注队列，未修旧逻辑 |
| 真实 BGE-M3、业务 PostgreSQL、外部模型、向量重建、B3-B | 全部未执行 |

回归包含旧 Agent 的模拟 SDK/工具调度、SQL 统计、商品范围、意图/计划、压缩、回答、补查循环、主题分析及 RAG/E1 离线契约。两项旧错误分别为 `test_run_date_filters_incremental_skip_and_scoped_progress`、`test_run_retry_and_cutoff_forwarding_with_real_mocked_sdk`，均位于 `test_review_scope.ReviewScopeTests`。

`git diff --check` 通过；P0、Embedding、向量存储、analytics、evidence_judge 和 strict 来源接口的 Git diff 为空。`git ls-files outputs/` 为空，测试日志和驱动均在忽略目录；未暂存或提交代码。没有对 B2、人工会话、历史评分或旧冻结配置进行写操作，也未声称本次对这些私有材料重新作了完整字节审计。

覆盖正常与空检索、原问题进入 encoder、候选顺序不变、距离极小但不相关/距离较大却有明确支持、范围/标注版本继承、错误对象与主题、混合情感不硬过滤、否定/转述/未知上下文/同对象冲突、quote 外事实不补充支持、来源篡改、精确区间、历史回放、异常安全传播、缺少本地模型配置、不下载、工具参数越权、缓存不重复执行，以及 FakeAgent 执行旧 SQL 星级统计。

首轮专项发现工具错误对象的消息位于 `error.message`，旧直接执行分支只读取顶层 message；已仅为语义工具补齐安全错误传播。首轮广泛回归的网络拦截误伤了 Windows asyncio 的本地 socketpair；独立 3 秒诊断又确认沙箱自身使 socketpair 卡在 accept。获执行权限后同一最小程序正常结束，回归在保留真实 HTTP/数据库拦截下运行；未改系统 ACL 或权限配置。初始失败与中断日志保留，未计为通过。

## 6. 剩余限制与下一步真实联调

技术上已具备合成端到端执行能力。**真实可用覆盖率、过度拒答和 pgvector/BGE 性能仍未验证**；受限语句之外通常会保守不作结论，不能将这条工程链路称为通用 RAG 问答已完成。

待单独授权的最小真实联调是：

1. 核对现有固定 BGE-M3 本地缓存和运行 Python 环境中的依赖，设置 `RAG_MODEL_CACHE`；本阶段未安装依赖、下载或加载模型。缺配置/缓存/依赖明确报错，不回退外部模型。
2. 使用现有 PostgreSQL 配置和明确单商品 scope，调用同一个 `run_review_agent`，只读复用 Agent 的 REPEATABLE READ 事务。授权应明确允许本地模型处理原问题和读取命中评论上下文。
3. 选择少量真实业务问题核对原问题编码、pgvector 排序、来源区间、回答引用和不足说明；不写业务库、不同步向量、不扩索引，不作为 B3-B 或 E1 质量验收。
4. 分别记录模型冷启动、查询和来源补齐耗时。当前默认工厂每任务初始化模型，并在 Agent 已打开的只读事务中执行；P0 还会重读有效语料及逐评论补齐，尚未优化。这是性能待测项，不新增审批平台。

现有 SQL 分析能力继续可用。以后若需要更广泛的语义充分性判断，应独立扩展并验证运行时判断能力；不要通过修改 R0、人工金标准或把距离设阈值来掩盖规则覆盖不足。当前代码改变使旧 B3 全源码摘要不再代表本轮代码，旧配置保留不动，也不影响本次 P1 合成验证。

完成 P1 后停止，不自动执行真实联调。
