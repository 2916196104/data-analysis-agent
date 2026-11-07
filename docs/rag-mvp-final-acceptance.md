# RAG MVP 最终集成与验收

日期：2026-11-07。状态：**BLOCKED**。

已按用户分别授予的A、B授权完成首次固定10场景真实验收：13个Agent任务、11次本地BGE查询编码、11次pgvector检索，零自动重试、零外部模型调用、零业务写入。主测商品053的9次检索均为空，未产生任何受支持回答；隔离商品055成功返回真实引用。因此未满足主商品至少一个受支持回答的全局门槛，不能封版。

本报告是唯一收尾报告。第1–7节保留首次运行前的实现审查、冻结标准与授权范围，第8–9节登记本次实际结果；其中“未执行/待授权”描述属于准备时点。当前真实验收次数1，阻断缺陷修复复测轮数0；本次授权仅首次运行，已用完，不自动修复或复测。

## 1. 复用现有能力及其边界

| 环节 | 当前实际实现 | 本轮判断 |
| --- | --- | --- |
| Agent入口与工具 | `agents/reviews.py::run_review_agent`、`review_intent.py`、`review_tools.py` 已有正式 `semantic_evidence` 路由及 `search_semantic_evidence` 工具 | 已集成，无需重写Agent或编排框架 |
| SQL统计 | 现有 analytics 工具完成数量、占比、趋势及维度汇总；语义检索不替代SQL分母 | 保留原分工；本轮测试正向主题汇总不触发编码或模型 |
| 检索 | `review_retrieval.py::create_semantic_service` 连接现有本地BGE-M3与 `PostgresSemanticRepository`，问题原文传给检索，Top-K固定5 | 真实数据库和本地模型当前可用性未复验 |
| 范围与来源 | `semantic_retrieval.py` 的只读快照、有效观点重建、全文切片核对、来源验证器及结果完整性检查 | 复用现有P0拥有者；补上Agent消费结果前的验证调用 |
| 语义许可 | `review_reported_facts.py` 的 `reported_experience_v2` | 有限业务事实规则，不具备任意自然语言蕴含能力；不提升P5实验映射 |
| 回答生成 | `review_answers.py::semantic_answer` 使用已许可评论，输出买家反馈、引用及证据不足说明 | 固定语义问题采用确定性回答，外部LLM调用预算0 |
| 前端 | `AnswerEvidence.tsx` 核对许可引用身份、原文及Unicode范围后展示；`useAgentChat.ts` 管理会话与范围变化 | 已有引用查看、范围、空结果与错误状态；本轮无需UI改造 |
| API/Worker | 已有API任务链、TaskWorker及可注入的独立本地任务队列 | 本轮未启动真实业务任务或重启服务；实际页面闭环待授权 |

代码定位：`src/data_analysis_agent/agents/reviews.py:122`、`agents/review_retrieval.py:142`、`agents/review_answers.py:390`、`reviews/semantic_retrieval.py:161`、`:220`、`:345`、`:368`（后三类路径均相对于 `src/data_analysis_agent/`）。

历史 `rag-engineering-p4-frontend-acceptance.md` 记录过商品100013588055的实际页面链路。这是可复用经验，不证明当前主测商品100013588053、当前标注身份和本轮10案例已通过。既有P5 Text Evidence合成实验也不等于真实评论质量验收。

### 当前可确认的缺口

1. Agent适配层此前只检查服务类型、scope及传输结构，没有显式要求服务返回值仍携带有效的P0来源证明。传输字段自洽不能替代P0证明；本轮已修复。
2. 固定请求M04、M06、M07及M09在当前正式规则中为 `retrieval_only`。检索可以返回候选，但不能据此宣称安装时序、长时间运行、早期故障或十年耐用已经得到语义确认。前3项可能成为真实验收的能力缺口；不能预先算通过。
3. 当前主测商品的数据、日期覆盖、当前profile对应向量和完整来源记录是否存在，尚未读取验证。配置及本地缓存路径存在，只能证明配置准备情况。

## 2. 本轮实际改动

| 文件 | 改动及原因 |
| --- | --- |
| `src/data_analysis_agent/agents/review_retrieval.py` | 导入并在 `service.search()` 后立即调用 `require_source_validated_result(result)`，在序列化、回答和引用发布前拒绝缺失或失效的来源证明 |
| `tests/test_review_semantic_integration.py` | 增加缺失证明、快照被篡改但保留旧证明两个拒绝子用例；修复前均能错误发布，修复后均返回error且无回答引用 |
| `tests/fixtures/rag_mvp_acceptance_v1.json` | 冻结10场景的原问题、范围、当前路由观察、验收门槛及调用上限；不包含真实评论或预填实际结果 |
| `tests/test_rag_mvp_acceptance.py` | 6项离线测试覆盖冻结完整性、正式路由、合成证据、范围切换、故障恢复、无结果和SQL分工；阻断真实外部连接和真实编码器 |
| `docs/rag-mvp-final-acceptance.md` | 本报告及后续实际验收的唯一结果载体 |

正式代码修改仅为上述来源校验导入及调用。未修改正式路由器、`reported_experience_v2`、B1/B2、P0签发器、实验Runtime规格、历史人工参考、27行标签或历史结果。前端、数据库结构、索引、Embedding空间和 `.env` 均未改动。工作区原有未提交P5材料保留。

## 3. 首次真实运行前的固定范围

冻结文件：`tests/fixtures/rag_mvp_acceptance_v1.json`。

规范化JSON SHA-256：`a50c41143fe142e4a0a7c3e83afc9fb620724124cdf44d874912f09f06782c29`。该摘要只用于冻结输入完整性，不是可信来源seal、人工确认或P0签发。

| 字段 | 冻结值 |
| --- | --- |
| source / dataset_version | `reviews_csv` / `reviews-csv` |
| 主测 / 隔离商品 | `100013588053` / `100013588055` |
| 当前日期窗口 | 2021-09-11 至 2022-08-26，按现有UTC日期语义 |
| 兼容范围字段 | baseline 2021-08-01 至 2021-09-10；固定语义请求不读取基期评论 |
| annotation model | `Pro/deepseek-ai/DeepSeek-V3.2`，仅选择已有标注，不调用该模型 |
| annotation prompt_version | `review-opinions-v1-e61de599683cba6703c2f61e85125d0be4627c2763440486aa717e4bc884036e` |
| annotation profile摘要 | `1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304`；含私有配置中既有base_url身份，运行时必须一致，不在报告公开该字段 |
| Top-K | 5；保留实际排序、距离和候选，不造历史rank，不扩大K追求通过 |
| 解析/回答 | 运行时 `rules_only`，语义路径确定性回答；持久配置不改；意外外部模型路径必须阻断 |
| P5 | 保持关闭；不发布人工参考，不启用 `semantic_verified` 或P5正式许可 |

所有请求使用上述共同范围，M01–M09只选择主测商品。M10按下表固定步骤变化商品范围，不修改问题。真实执行时若profile、商品或向量来源不满足要求，应记录阻断，不能换商品、补向量或换模型冒充成功。

## 4. 固定10案例及结果登记

下表“当前能力”来自首次运行前的离线路由检查，不是对真实评论的判断。问题、路径观察与验收规则未改；实际证据和逐项结果见第9节。

| ID | 用户问题 | 固定路径与当前能力 | 冻结验收规则 | 实际结果 |
| --- | --- | --- | --- | --- |
| M01 | 有没有配送很快的反馈？ | SQL范围摘要 + RAG；`delivery_fast` | 支持结论必须有真实快速配送quote；无充分候选时明确不足，不从安装速度推定收货速度 | PASS：仅空候选降级；见9.2 |
| M02 | 有没有配送很慢的反馈？ | SQL范围摘要 + RAG；`delivery_slow` | 只能据明确配送慢/延误内容回答，不能从安装慢推定配送慢 | PASS：仅空候选降级；见9.2 |
| M03 | 有没有客服态度差的反馈？ | SQL范围摘要 + RAG；`service_bad` | 必须明确客服角色及负面态度；安装师傅或未知对象不能代替客服 | PASS：仅空候选降级；见9.2 |
| M04 | 有没有到货后迟迟没人安装的反馈？ | SQL范围摘要 + RAG；当前 `retrieval_only` | 若回答支持，必须同时有到货、其后持续安装等待依据；无充分候选才可不足 | PASS：仅空候选降级；见9.2 |
| M05 | 有没有制冷效果好的反馈？ | SQL范围摘要 + RAG；`cooling_good` | quote直接支持制冷正向体验，制热、宣传、假设或被撤回内容不能冒充 | PASS：仅空候选降级；见9.2 |
| M06 | 有没有开了很久还是不凉快的反馈？ | SQL范围摘要 + RAG；当前 `retrieval_only` | 支持必须保留持续运行及仍未有效降温，不新增数值阈值或制冷模式字样要求 | PASS：仅空候选降级；见9.2 |
| M07 | 有没有使用不久就发生故障的反馈？ | SQL范围摘要 + RAG；当前 `retrieval_only` | 支持必须保留使用后不久及故障事实；泛质量不佳或配件问题不能推定主机早期故障 | PASS：仅空候选降级；见9.2 |
| M08 | 有没有同一评论既说制冷效果好又抱怨噪音很大的反馈？ | SQL范围摘要 + RAG；`cooling_good` AND `noise_high` | 必须是同一真实评论的两项直接证据，不能拼两条评论；无充分候选时明确不足 | PASS：仅空候选降级；见9.2 |
| M09 | 有没有空调使用十年仍然耐用的反馈？ | SQL范围摘要 + RAG；当前 `retrieval_only` | 不足时拒绝十年耐用结论，不将短期好评或购买年份外推十年使用；不得宣称全库绝无此评论 | PASS：证据不足；见9.2 |
| M10 | 有没有制冷效果好的反馈？ | 同M05；四个固定动作 | A切到055正常；B同时选择053/055，编码前拒绝多商品；C在055注入编码前服务构造故障，error且无回答/引用；D恢复055正常，无053或旧故障引用残留。四步全部符合才通过该案例 | PASS：四步符合；见9.3 |

实际工具路径、候选和私有回执指针、来源/Unicode切片、回答与幻觉检查、pass/fail及原因已记录于第9节。真实评论全文、候选key/rank、敏感来源及私有回执只留在Git忽略的独立目录，不提交本报告。

### 计分与最终门槛

- 10案例至少8个符合预期，并且**主测053至少有一个真实受支持的RAG回答**；不能靠全部拒答或只在隔离商品成功来达标。
- 必须真实使用PostgreSQL/pgvector及现有编码器，不能用FakeRepository/FakeEncoder计为真实连通。
- 对M01–M08，只有固定候选确实没有充分支持证据时，诚实的不足说明才能作为符合预期。若候选已有充分证据而正式语义规则不覆盖，该项记为能力缺口/FAIL，不能把 `query_semantics_uncovered` 等同于证据不存在。
- M09的不足必须符合实际候选，若发现意外真实长期使用依据，保留实际情况，不调整问题或制造“无证据”。
- 所有展示引用都要同时通过来源、位置和实际语义充分性检查。唯一切片匹配只证明位置；距离、标注情感、结构passed均不能代替语义裁决。
- 零伪造引用、零跨商品/跨数据集评论污染、无结果和故障安全降级、正式SQL无阻断性回归都是全局条件；不能用其他案例抵消安全失败。
- 不足回答限于“本次候选无法确认”，不能推导全体买家的比例、事实不存在或客观产品性能。检索漏召回单列，不冒充模型语义错误。

离线合成观察：M01/M02/M03/M05/M08在专门构造且明确的虚构文本上返回合法引用；M04/M06/M07/M09保持不许可；M10的范围拒绝、故障及恢复通过。此观察验证工程行为，**不为上述表格填入任何真实PASS，也不表明当前所有业务语义已实现**。

## 5. 数据、引用与范围保护

复用P0的可信快照和来源拥有者，核验当前有效标注及完整有效观点集合、来源文件身份/内容摘要和行号、全文与quote范围、当前scope/profile及检索结果完整性。新增适配层检查使“去掉来源证明”和“更改快照但保留旧证明”均失败关闭。

引用为完整评论内Unicode code point绝对区间，原文必须精确切片。前端使用 `Array.from` 按code point处理，只有匹配许可身份及原文的引用可显示。跨商品、数据集、标注版本、来源漂移或无效证明不能通过复制key/hash获得许可。

当前检索实现为保证完整有效观点与状态校验，会读取**选定商品、日期和profile下的有效语料集合**，并对命中评论加载完整来源。这不是“只读取Top-K五条”。整个数据集的商品目录元数据也会被用于现有商品实体绑定；授权范围必须据实包含该项。下节明确限制评论正文仍只属于两个指定商品及日期范围。

## 6. 离线验证结果

| 项目 | 结果 | 证明范围 |
| --- | --- | --- |
| 新MVP专项 | 6/6 | 冻结、既有路由、虚构证据、引用、错误范围、故障恢复、SQL分工 |
| Agent语义集成 | 37/37 | 含本轮两种来源证明缺失/篡改子用例；修复前复现失败、修复后拒绝 |
| semantic_retrieval / rag_corpus / rag_runtime | 28/28、21/21、12/12 | 合成检索、来源重建与运行资源行为 |
| reported_facts / task_contract / product_binding | 9/9、11/11、13/13 | 既有v2规则、契约、对象/范围约束 |
| review_plan / review_answers / review_agent | 13/13、17/17、16/16 | 既有规划、回答、SQL/Agent业务回归 |
| 后端合计 | **183项通过** | 新6项 + 适用既有177项；不包含真实业务服务连通 |
| 前端 | **25文件、221/221通过** | 组件/状态测试，包含引用与会话；无真实浏览器任务验收 |
| 前端构建 | 通过 | 存在既有大chunk提示，不影响构建产物生成 |

说明：后端早期测试保护同时拦截 `socket.socket.connect`，干扰Windows asyncio本地self-pipe，导致旧SDK Mock用例异常。该套测试改为拦截真实HTTPTransport、PostgreSQL和编码器等边界后16项全通过，未修改业务/测试逻辑来消除失败。前端首次高并发执行有1项超时；保持原10秒超时，限制为2个worker后全量221项通过。没有放宽校验或超时阈值。

本节离线验证使用虚构SQLite数据、Fake检索仓库或Mock API；本节结果不代表真实PostgreSQL/BGE连通。随后获授权执行的真实结果单列于第9节。已有其他P5专项不因本轮通过而升级为业务语义已验证。

可复核命令（均为本地离线）：

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_rag_mvp_acceptance.py -v
npm --prefix frontend test -- --maxWorkers=2 --reporter=dot
npm --prefix frontend run build
git diff --check
```

既有后端回归逐套运行时使用禁止真实连接的测试保护。上述新MVP专项自身带有连接/模型阻断；不运行现有真实联调脚本来替代授权。

## 7. 真实验收所需的分别授权

**本节范围已由用户分别授权A、B，并已执行首次验收。** 两轮复测未授权。无需SiliconFlow或任何付费API授权；两种本地资源授权与“将真实评论发到第三方”无关，本方案第三方发送量为0。

### A. 真实PostgreSQL最小只读范围

- 用途：固定案例的SQL范围/覆盖摘要、pgvector排名与真实P0来源验证。仅 `reviews_csv / reviews-csv`；评论及观点正文限定商品053/055、2021-09-11至2022-08-26和上述当前标注profile。
- 字段：review/product/source/dataset身份、日期、评论全文及用于统计的评分；当前标注ID、model/base_url/prompt_version、状态/修订版本、完整有效观点、quote及范围；来源导入文件摘要/行号/原文；现有向量空间与关联身份、距离及版本状态。仅核验所需关联记录，不读取其他商品评论正文。
- 为沿用现有实体解析，还需读取该数据集商品目录的 `product_id/title/brand`，以及事务、表结构和pgvector空间元数据。不能将“两个商品正文”误写成“数据库里只会读两个商品的所有字段”。
- 业务事务只读；不写业务库、任务表、标注或向量，不执行迁移、索引重建/重嵌入/重新标注。现有Worker可能使用只读SQL取得会话级advisory lock，应包含必要锁与连接元数据权限。
- API/Worker验收使用独立、Git忽略的本地SQLite任务队列及输出目录，避免业务PostgreSQL任务写入。真实私有结果与原目录隔离，不覆盖历史材料。
- 只读次数不能诚实压缩为一个SQL：正式路径含范围摘要、语料核验、向量查询、命中来源加载。按下表限制Agent提交/向量查询，不额外开展48组重检或全商品语料扫描。

### B. 现有本地BGE-M3运行范围与预算

- 初次验收最多11次**查询文本**编码，使用现有固定版本、本地缓存与既有1024维空间；仅编码冻结问题，不编码/重建全部评论向量，不下载模型，不做模型选型。
- 语义回答走既有确定性路径；运行时关闭语义LLM解析回退并阻断外部SDK/HTTP。若出现意外外部模型路径，记阻断，不能自动申请/消耗费用。
- 外部LLM请求0、外部Embedding请求0、发送SiliconFlow真实评论0、付费API预算 **¥0**。本地CPU/GPU、内存、数据库及等待时间有成本，当前未测；不据历史Shadow的7.6秒推定本链路时延。

| 固定运行量 | 首次验收上限 | 若另外明确授权最多两轮必要复测的保守总上限 |
| --- | --- | --- |
| Agent任务提交 | 13（前9案例 + M10四动作） | 39，仅原案例的阻断修复复测 |
| 本地查询编码 / pgvector检索 | 11（前9 + M10两次正常） | 33 |
| 排名候选出现次数 | 至多55，不等于55条独立评论或最多只读55条正文 | 至多165 |
| 外部LLM / 外部Embedding / 付费API | 0 / 0 / ¥0 | 0 / 0 / ¥0 |

M10多商品拒绝及服务构造故障在编码前终止；故障为本地注入，不制造第三方付费失败。自动重试0。用户本次仅授权首次13任务/11次编码/11次检索，现已全部使用；未使用任何复测额度。

授权后先检查现有来源/profile和向量就绪；若主测数据缺失、来源验证不通过或本地模型不可用，报告具体阻断，不自动回填、换主测商品或扩大窗口。已有前后端需要加载本轮代码后才能验证；当前没有擅自停止或重启既有服务。

## 8. 最终准入与停止状态

最终状态 **BLOCKED**。真实PostgreSQL、现有BGE及Agent后端链路连通，隔离商品可以用真实评论回答，但主测商品053没有可用候选、受支持回答为0，违反冻结的主商品全局门槛。不能把空结果安全降级或055成功替代053业务能力。

唯一已确定的必要处理项：核清并解决**053在冻结日期、profile、当前有效快照及既有向量空间下无可用向量命中**的问题。M08等无维度/情感筛选的查询也为空，因此不能仅归因于本轮某个问题词或情感提示。当前回执不足以区分“未索引”“旧快照/版本失效”或其他来源连接身份不匹配；不擅自判定数据库中绝无该商品向量，更不把空检索解释为评论没有相关事实。

本轮没有新增查询来追查该差异，没有修复、回填、重建向量或复测。若必要处理涉及向量写入/同步，必须另获明确授权，不能利用本次只读授权执行。M04/M06/M07的有限语义覆盖仍是待实际候选暴露后才能判断的风险，本次没有据此改规则或生成新金标准。

仅记录、不在本轮扩展：更广自然语言覆盖、P5类型化映射/四状态评审、历史48组复评、长期延迟分布及生产队列部署。这些不能替代本次真实门槛。正式P5许可继续关闭，`publication_allowed=false`、`semantic_verified=false` 的实验保护不变；已有v2合法评论回答能力保留。

本次首次固定验收授权已用完，执行结束后停止。没有开启修复轮次、再次检索、真实模型请求或新开发任务。

## 9. 首次固定真实验收回执

### 9.1 执行边界及真实连通

执行时间：2026-11-07 15:54:16–15:54:55（UTC+8；原回执为UTC）。冻结JSON规范化摘要仍为 `a50c41143fe142e4a0a7c3e83afc9fb620724124cdf44d874912f09f06782c29`，问题、scope、profile、Top-K、验收规则均未改变。

实际使用原 `create_app` 的POST任务/GET详情端点，经本机进程内ASGI客户端入队；独立SQLite队列通过原 `claim_task` 和 `TaskWorker._execute` 串行执行，内部为正式 `run_review_agent` → SQL范围摘要 → 本地BGE → PostgreSQL/pgvector → P0来源验证 → v2许可 → 确定性回答 → API回执。未替换仓库或编码向量，唯一故障注入为冻结M10C的编码前服务构造失败。

未启动浏览器或网络HTTP服务器，也未启动/接管现有常驻Worker、取得业务库Worker全局锁。因此本次证明API/Worker执行函数及真实后端链路，不宣称完成新的浏览器页面或生产队列部署验收。

| 资源/调用 | 实际值 |
| --- | --- |
| 场景 / API入队 / Agent任务 | 10 / 13 / 13 |
| 本地BGE查询编码 / pgvector `<=>` SQL | 11 / 11；逐次计数，达到授权上限后不再调用 |
| 自动重试 / 修复复测 | 0 / 0 |
| 真实业务连接 | 15，全部强制只读且已关闭 |
| 外部模型/Embedding/HTTP尝试 | 0；本地编码子进程另有离线与socket连接阻断 |
| 付费API / 模型token | ¥0 / 不适用（没有模型API请求） |
| 业务写入尝试 | 0；仅私有本地任务队列及回执有写入 |
| 私有结果目录 | `outputs/rag-semantic-retrieval/mvp-first-20261107-v1/`，Git忽略；独立一次性执行claim已占用，不能重用 |

私有 `receipt.json` 保存总计数、每个任务及检索回执；`M01.json`–`M09.json`、`M10A.json`–`M10D.json` 保存逐项范围、rank/距离/key、原文、回答、验证与耗时；`readonly-audit.json` 是仅基于已保存数据的复核结果，没有再次读取业务数据库。正文、敏感来源、标注端点和个人信息不提交Git；保存的JSON不是P0可信seal，P0验证发生在原运行中的拥有者校验路径。

执行回执文件SHA-256：`fec32503d733fe753edff73ee275075087e1a7b1a611e1df77899dd3bf9da939`，仅用于定位本次保存结果。

### 9.2 主测053的九个案例

九次均读取到同一冻结范围的有效语料：1699条评论、3591个有效观点；这不等于已索引1699条。SQL范围摘要为1699条有效正文、1660条标注成功、39条失败，分母及覆盖率仍走原SQL统计，不用Top-K替代。

九次pgvector检索实际均返回0候选，Agent返回 `ok`、正式结构校验 `passed`、`assessment.reason=no_candidates`，答案明确限定“本次没有候选，不能据此推断全量没有反馈”，无引用、无正向/负向事实断言。P0结果绑定/只读快照合法，但**没有命中评论可做引用来源核验**，不能称主商品引用或业务语义已验证。

| ID | 实际候选 / 引用 | 来源及引用核验 | 回答与幻觉/范围检查 | 固定逐项结果 | Worker执行秒数 | 私有回执 |
| --- | --- | --- | --- | --- | --- | --- |
| M01 | 0 / 0 | 空结果绑定合法；评论切片N/A | 只说明候选不足，没有断言配送快 | PASS，仅不足分支 | 22.269 | `M01.json` |
| M02 | 0 / 0 | 同上 | 没有断言配送延误或借用安装事实 | PASS，仅不足分支 | 1.600 | `M02.json` |
| M03 | 0 / 0 | 同上 | 没有将未知角色/安装人员断言为客服 | PASS，仅不足分支 | 1.319 | `M03.json` |
| M04 | 0 / 0 | 同上 | 没有补充到货、时序或安装等待事实 | PASS，仅不足分支 | 1.865 | `M04.json` |
| M05 | 0 / 0 | 同上 | 没有作出制冷效果好的结论 | PASS，仅不足分支 | 1.066 | `M05.json` |
| M06 | 0 / 0 | 同上 | 没有编造运行时长或降温不足 | PASS，仅不足分支 | 1.109 | `M06.json` |
| M07 | 0 / 0 | 同上 | 没有推定主机早期故障 | PASS，仅不足分支 | 1.381 | `M07.json` |
| M08 | 0 / 0 | 同上 | 没有拼接评论或宣称双事实成立 | PASS，仅不足分支 | 1.751 | `M08.json` |
| M09 | 0 / 0 | 同上 | 没有十年耐用或全库不存在的断言 | PASS，安全不足 | 1.233 | `M09.json` |

这些PASS严格指冻结规则允许的**候选为空时的诚实降级**，不是九项正向业务能力通过，也不是语义准确率。没有候选可判断目标事实、角色、时序或引用充分性；上述业务语义质量均为N/A。主商品连续空结果已在全局门槛单独判失败，不能通过9次拒答完成MVP。

### 9.3 M10商品切换、故障及恢复

| 固定动作 | 实际状态 | 编码 / pgvector | 证据、回答及范围 | 来源/切片检查 | 结果 / Worker秒数 |
| --- | --- | --- | --- | --- | --- |
| A：切到055正常 | task succeeded / Agent ok | 1 / 1 | Top-K 5，发布2条真实制冷正向引用；均为055，没有053引用 | 原P0与正式发布审计通过；5条候选全文切片精确、2条发布引用身份匹配 | PASS / 0.915 |
| B：同时选择053/055 | task succeeded / Agent unsupported | 0 / 0 | 明确要求单商品，无引用，无静默跨商品检索 | 编码/检索前终止，引用审计N/A | PASS / 0.210 |
| C：055服务构造失败 | task failed / Agent error | 0 / 0 | 错误 `business_error / mvp_injected_service_unavailable`；无业务答案或引用 | 冻结的本地故障注入；没有旧证据回填 | PASS / 0.281 |
| D：恢复055正常 | task succeeded / Agent ok | 1 / 1 | 再次Top-K 5、发布2条；是新的真实检索，未使用失败或053上下文引用 | 新快照P0与正式审计通过，引用及范围再次精确匹配 | PASS / 1.343 |

A/D接续上一任务的同一会话：切换scope不会继承主商品证据；错误任务不贡献旧结果。两次正常结果来自055范围510条评论、1091个有效观点，不能将它们移作053验收证据。

两次正常检索的rank和距离相同，保留原排序。5个独立候选中：rank1/2为直接制冷正向自述并获许可；rank3/5为制冷负向，rank4为制热反馈，均未获本题许可。回答只概述“有买家报告”并引用rank1/2，没有推广为客观性能或总体比例。只读查看这两条全文，未发现撤回、否定、转述或角色歧义使所引用的制冷正向陈述失效；这是本轮对实际答案的复核，不是新四状态人工金标准。

原始rank/key/距离、review/annotation身份、文件摘要/行号、完整上下文和code point绝对区间均保存在私有A/D回执，不将真实评论正文及敏感来源字段写入Git。

### 9.4 来源、引用及发布保护复核

- 实际11个检索结果均由原P0校验路径产生并通过Agent消费前检查；其中9个为空、2个含真实来源，不能混算为11批真实引用成功。
- 两批合计10次候选出现，去重5个；逐条核对全文SHA、quote原文、Unicode code point区间、商品/数据集/profile、标注及有效快照身份，10/10匹配。发布引用4次出现、去重2条，均属于实际候选及 `permitted_keys`，且文件身份/行号与原文切片一致，4/4匹配。
- 未发现伪造引用、跨商品/跨数据集评论或答案中的无依据扩展。实际正向回答仅出现于055；主商品事实支持率与四状态准确率均N/A。
- 11个正常检索任务正式结构/范围发布校验通过；多商品和注入故障按预期提前关闭。没有运行或修改Shadow B1/B2，也不把正式校验等同于新的P5语义验证。
- 冻结fixture、`.env`、正式适配层及 `reported_experience_v2` 的运行前后摘要一致。本次没有修改业务代码、历史实验或人工答案，保护状态继续 `publication_allowed=false`、`semantic_verified=false`，P5新增正式许可为空。

### 9.5 耗时、回收及最终门槛

整次执行39.433秒；首任务22.269秒，其中模型加载19.654秒、首次编码0.673秒。后续10个正常检索任务的Worker执行时间中位数1.331秒，范围0.915–1.865秒；注入拒绝/错误动作不混入该中位数。样本少且主要为空候选，不推导并发、P95或完整支持回答的生产时延承诺。

唯一启动的本地编码子进程已退出；共享编码进程缓存已清空，15个业务连接均已关闭，13个队列任务均已结束（12 succeeded、1预期failed）。执行过程没有创建常驻后台服务或修改既有Worker。实际浏览器布局/交互未在本轮重测，之前221项前端离线测试不能冒充本次页面实测。

| 冻结全局门槛 | 实际结果 |
| --- | --- |
| PostgreSQL/pgvector及本地BGE真实连通 | 通过 |
| 固定10项至少8项符合允许的证据/不足/隔离行为 | 10/10符合；9项仅为空结果安全降级，不能称业务语义10/10 |
| 主测053至少一个真实受支持回答 | **失败：0个** |
| 展示引用位置合法且支持对应陈述 | 已展示的4次引用通过；主商品无引用可评估 |
| 零伪造、零跨商品证据污染、安全降级 | 本次观察通过 |
| 正式SQL无阻断性回归 | 本次真实SQL范围摘要与原结构审计通过；第6节离线回归有效，未新增真实统计题目或宣称全SQL实测 |
| 最终状态 | **BLOCKED**，必要处理项见第8节 |

没有用主测空结果虚构支持，也没有将隔离商品替换为主测商品。保持冻结标准，结束本次验收，不自动修复、重新调用或开展下一阶段。

## 10. 053空检索的离线诊断与待授权SQL

2026-11-07，首次验收后的诊断补充。**状态仍为BLOCKED；本节没有连接数据库、启动编码器、执行向量检索或修改业务代码。** 首次验收及其私有回执保持原样，10个问题和全部冻结规则不变。

### 10.1 离线能够证明的范围

发现了直接相关的历史覆盖差异：`outputs/rag-pgvector/business-sync.json` 记载的实际业务同步只有商品055，第一次200条新编码、第二次200条复用；`business-acceptance.json` 的持久化身份组也只有055，200条向量、1024维。后来的锁协议复验仍为同一055范围、200条复用。历史报告明确“没有扩大到第二款商品”。这些证据证明**已记录的向量建设仅覆盖055，没有证明053完成过向量同步**，不能用它们证明当前数据库053行数必为0。

| 检查项 | 当前证据及结论 | 仍不能确定的部分 |
| --- | --- | --- |
| 053有效观点与对应向量 | 首次回执记录1699条评论、3591个观点；历史同步仅覆盖055的200个观点 | 053原始向量行数、失效行数和当前可关联行数未保存，不能从观点数推定向量数 |
| BGE空间与维度 | 首次11次查询编码均经过1024维、单位范数及固定空间校验；055同路径有真实命中；历史业务DDL为 `vector(1024)` | 未命中的053存储行是否存在、是否处于其他空间，离线未知 |
| profile及版本 | 当前配置与冻结profile摘要一致；053与055回执的profile、builder、空间完全相同 | 053存储行的profile、prompt对应标注及空间是否一致未知 |
| 修订/覆盖/快照 | key绑定正文内容身份、标注ID、profile、人工修订/观点ID、标签、范围和quote哈希；整评论快照还绑定全部有效观点及状态 | 053是否曾同步旧版本、是否有人工作业导致旧向量失效，不能由历史“人工修订0”推断现在仍为0 |
| 范围和可选过滤 | 用原 `_range()` 离线复现053/055参数，仅商品ID不同；053有8次 `aspect=null,sentiment=null` 的空结果，只有M03带service过滤 | 存储向量的来源/日期元数据是否与当前评论一致未知 |
| SQL关联与索引 | 精确 `<=> ORDER BY ... LIMIT 5`，没有距离阈值、最低相似度门槛或“低相关度删除”步骤；同实现055正常 | 当前索引目录状态未重新读取；没有证据支持SQL故障或索引损坏 |

053和055对照：当前有效观点3591 vs 1091；已记录的历史同步覆盖“未证明存在” vs 200；同一固定制冷问题M05与M10A的query均无可选过滤，结果0 vs 5。两者的空间均为 `embedding-space-v1:11e6754662dbd13fc56c6e624bc9416c1c2766f962787267fd4a57b1fa8d622e`，builder均为 `quote-utf8-v1`，profile均为第3节冻结值。

实际生产WHERE由 `review_scope` 生成，按现有TEXT日期口径为：商品053（或055）、`reviews_csv / reviews-csv`、`published_at >= '2021-09-11T00:00:00.000+00:00'` 且 `< '2022-08-27T00:00:00.000+00:00'`，`as_of=null`。诊断不将它换成另一日期口径来掩盖差异。

返回候选必须同时满足：

1. 上述存储范围条件。
2. 目标空间、builder、profile，且 `invalidated_at IS NULL`。
3. `stable_versioned_opinion_key` 与本次有效语料相同。
4. `effective_snapshot_sha256` 与该评论的本次完整有效集合相同。

SQL在第3、4项使用INNER JOIN，之后才排序；缺少HNSW/IVFFlat不会自动令合格行变成空集。仓库不会自动编码缺失观点，Agent只编码查询问题。这使“将主测商品换成053，但向量覆盖仍只有055”成为目前最有证据支持的解释。

代码定位：`embedding_store.py:180`（显式同步）、`:227`（当前key/快照集合）、`:244`（实际检索SQL）；`rag_corpus.py:83`、`:122`、`:141`（观点key、整评论快照、当前语料）；`scope.py::current_reviews_sql`（同内容同profile的成功优先标注、最新人工完整快照）；`semantic_retrieval.py:368`（只读仓库）。均相对于 `src/data_analysis_agent/reviews/`。迁移定义见 `migrations/003_review_opinion_embeddings.up.sql`。

### 10.2 原因分类与修复选择

| 分类 | 证据强度 | 当前选择 |
| --- | --- | --- |
| A 向量缺失 | **首要嫌疑**：只有055同步的持久回执，053所有无过滤检索也为空 | 先检查覆盖计数；尚不将缺失当作已实查结论，不执行回填 |
| B 向量版本/有效观点身份不匹配 | 实现上确实能产生空集；但无053旧向量或当前修订对照 | 保留为待排除项，不能改写旧hash、清除失效标记或降低关联条件 |
| C SQL过滤/关联错误 | 输入scope正确，055同实现成功；未发现确定代码缺陷 | 不改SQL、日期口径、商品绑定或必要JOIN |
| D 索引/空间状态异常 | 历史为固定空间CHECK、1024维、普通B-tree；查询向量校验已通过 | 不重建索引、不换空间；只有目录/计数证据异常才考虑此方向 |
| E 覆盖范围交接缺口 | **已证实**：历史获批建设对象055，本次主测053；首次准备没有取得053向量覆盖计数 | 这是验收就绪性缺口；与A的当前物理状态区分，不以换主测商品解决 |

当前可以确认的机制是“没有行满足完整范围、版本及当前集合关联”；最可能的具体原因是053没有被既有有限同步覆盖。**尚未完成当前数据库根因确认，所以本轮没有选定或实施数据库修复。**

### 10.3 最小只读诊断申请（尚未执行）

建议只授权以下D01–D03，至多3条数据/目录SELECT，各执行一次、失败或超时即停止、零重试。一个 `REPEATABLE READ READ ONLY` 事务，UTC会话，`statement_timeout=10s`、`lock_timeout=1s`，结束ROLLBACK/close；设置和事务控制不属于向量检索。需要现有表SELECT及系统目录可见性，不需要写、DDL、superuser或外部网络权限。

固定参数：第3节source/dataset、两个商品、原日期窗口、原profile、固定1024维空间和builder。不读取商品之外的评论正文、不返回quote、评论全文、用户字段、模型地址或向量坐标。为诊断“被过滤的版本”，仅对这两个商品日期范围内或关联这些评论ID的**派生向量元数据**统计其他profile/space的匹配与不匹配数量，不把这些记录用于回答或许可。这项对照属于本次拟申请的读取范围，不能省略后声称仅查询完全匹配行。

先D01；若053的存储范围行数和关联评论ID行数均为0，即可确认本次快照下A类覆盖缺失，停止，不再执行D02/D03。若存在行，按结果决定是否需要D02（同步沿革）或D03（结构/空间异常）；最多仍为三条，不编码问题、不执行 `<=>`、不跑Agent。物理读取/执行计划由数据库决定，不能保证只发生索引访问；statement timeout限制代价，不以 `LIMIT` 截断完整计数。

#### D01：两个商品的范围覆盖与身份过滤漏斗

读表：`reviews` 的id、完整商品身份、published_at、content_sha256；`review_opinion_embeddings` 的review_id、声明范围身份、profile、space、builder、invalidated_at、内容身份，以及库内 `vector_dims/vector_norm`。仅返回每商品一行聚合，**不返回向量坐标或正文**。

以下为参数化SQL方案；`:name` 是待绑定参数，不得字符串拼接凭据。`:lower/:upper` 使用10.1的原TEXT边界，`:profile/:space/:builder` 使用冻结值。

```sql
WITH products(product_id) AS (VALUES (:p053), (:p055)),
scoped AS (
  SELECT id, source, dataset_version, product_id, published_at, content_sha256
  FROM reviews
  WHERE source=:source AND dataset_version=:dataset
    AND product_id IN (:p053,:p055)
    AND published_at>=:lower AND published_at<:upper
), declared AS (
  SELECT product_id, count(*) AS declared_scope_rows
  FROM review_opinion_embeddings
  WHERE source=:source AND dataset_version=:dataset
    AND product_id IN (:p053,:p055)
    AND published_at>=:lower AND published_at<:upper
  GROUP BY product_id
), linked AS (
  SELECT s.product_id AS owner_product, e.*,
    e.source=s.source AND e.dataset_version=s.dataset_version
      AND e.product_id=s.product_id
      AND e.published_at>=:lower AND e.published_at<:upper AS scope_ok,
    e.review_content_sha256=s.content_sha256 AS content_ok,
    e.published_at=s.published_at AS published_at_equal
  FROM scoped s JOIN review_opinion_embeddings e ON e.review_id=s.id
)
SELECT p.product_id,
  (SELECT count(*) FROM scoped s WHERE s.product_id=p.product_id) AS reviews,
  coalesce(d.declared_scope_rows,0) AS declared_scope_rows,
  x.*
FROM products p LEFT JOIN declared d USING(product_id)
LEFT JOIN LATERAL (
  SELECT count(*) AS rows_linked_to_scoped_reviews,
    count(*) FILTER (WHERE NOT scope_ok) AS stored_scope_mismatch,
    count(*) FILTER (WHERE NOT content_ok) AS changed_content,
    count(*) FILTER (WHERE NOT published_at_equal) AS changed_timestamp,
    count(*) FILTER (WHERE scope_ok) AS f_scope,
    count(*) FILTER (WHERE scope_ok AND label_profile_key=:profile) AS f_profile,
    count(*) FILTER (WHERE scope_ok AND label_profile_key=:profile
      AND embedding_space_id=:space AND text_builder_version=:builder) AS f_space_builder,
    count(*) FILTER (WHERE scope_ok AND label_profile_key=:profile
      AND embedding_space_id=:space AND text_builder_version=:builder
      AND invalidated_at IS NULL) AS f_active_before_current_key_join,
    min(vector_dims(embedding)) AS min_dims,
    max(vector_dims(embedding)) AS max_dims,
    min(vector_norm(embedding)) AS min_norm,
    max(vector_norm(embedding)) AS max_norm
  FROM linked e WHERE e.owner_product=p.product_id
) x ON TRUE
ORDER BY p.product_id;
```

`declared_scope_rows` 和按真实评论ID关联的计数同时保留，避免“派生商品/来源字段写错”被误判成完全无向量。检查其他profile/space的仅是这些关联行的元数据，不拓展评论范围。`linked` 的 `e.*` 仅为查询内部简写，最终结果只有上述聚合；未来落地时可显式列出所需字段，禁止向客户端返回整行。

预计逻辑评论规模为1699+510=2209个ID；关联历史向量行数未知，历史只证明过055的200行。计数若显示评论规模或配置已漂移，应记录新旧差异，不将新快照伪装为首次验收时点。没有评论全文读取，坐标只在库内参与维度/范数聚合。

解释：双零支持A；`rows_linked`非零但scope/profile/space/builder/active漏斗归零，定位具体被排除层。`f_active_before_current_key_join>0` 仍不能宣称可用：key/完整集合快照未核对；此时才需要10.4的条件性来源身份诊断，不能直接回填或放宽JOIN。

#### D02：限定商品的同步记录

读 `rag_embedding_sync_runs` 的source/dataset/product、profile/space、scope_json日期、status、开始/结束时间及选中/发布计数。只返回聚合，不读取原始评论、向量或运行日志正文。

```sql
SELECT product_id,
  label_profile_key=:profile AS target_profile,
  embedding_space_id=:space AS target_space,
  scope_json->>'date_from'=:date_from
    AND scope_json->>'date_to'=:date_to
    AND coalesce(scope_json->>'as_of','')='' AS exact_window,
  status, count(*) AS runs, max(finished_at) AS last_finished_at,
  max((metrics->>'selected_opinion_count')::bigint) AS max_selected,
  max((metrics->>'published_count')::bigint) AS max_published
FROM rag_embedding_sync_runs
WHERE source=:source AND dataset_version=:dataset
  AND product_id IN (:p053,:p055)
  AND scope_json->>'date_from'<=:date_to
  AND scope_json->>'date_to'>=:date_from
GROUP BY product_id,target_profile,target_space,exact_window,status
ORDER BY product_id,target_profile,target_space,exact_window,status;
```

只读同商品且同步窗口与冻结窗口相交的历史元数据；目标profile/space固定，其他身份仅返回真假分组用于排错，不读取其正文。同步记录量未知，预计很小；无匹配记录不等于物理向量为0，应以D01为准。重复同步复用200行不能相加成400/600行覆盖。若metrics类型异常，停止而不自动改SQL/重试。

#### D03：仅派生表的结构、约束及索引目录

读 `pg_extension`、`pg_attribute`、`pg_constraint`、`pg_index/pg_class/pg_am`，仅限public的向量表与vector扩展。确认真实列类型、空间/builder CHECK、索引valid/ready及访问方法，不读业务正文。不存在单独的EmbeddingSpace注册表，不虚构查询该表。

```sql
SELECT jsonb_build_object(
  'vector_extension',
    (SELECT extversion FROM pg_extension WHERE extname='vector'),
  'embedding_type',
    (SELECT format_type(atttypid,atttypmod) FROM pg_attribute
     WHERE attrelid='public.review_opinion_embeddings'::regclass
       AND attname='embedding' AND NOT attisdropped),
  'checks',
    (SELECT jsonb_agg(pg_get_constraintdef(oid)) FROM pg_constraint
     WHERE conrelid='public.review_opinion_embeddings'::regclass AND contype='c'),
  'indexes',
    (SELECT jsonb_agg(jsonb_build_object('name',c.relname,'valid',i.indisvalid,
             'ready',i.indisready,'method',a.amname))
     FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
     JOIN pg_am a ON a.oid=c.relam
     WHERE i.indrelid='public.review_opinion_embeddings'::regclass)
);
```

预计一行JSON、一个扩展及一张派生表的目录项；历史为9个B-tree。若目录与历史不一致，保留异常，不执行REINDEX、CREATE INDEX、迁移或修复CHECK。普通B-tree缺失通常影响速度，不足以单独解释精确查询稳定返回0。

### 10.4 仅在存在目标空间向量时才需要的进一步身份检查

**不包含在上述D01–D03拟申请额度内，不自动执行。** 如果D01证明目标范围/profile/space下已有active行，但仍需区分B与C，必须再冻结以下元数据读取，不能擅自调用会读取全文的 `read_effective_corpus()`：

| 表 | 必要字段及用途 | 原文影响 |
| --- | --- | --- |
| `reviews` | 两商品原窗口内id、source/dataset/product、published_at、content_sha256、has_text | 不取text/title/user_id/raw_json |
| `review_annotations` | 上述review_id关联的id、content_sha256、requested_model/base_url/prompt_version匹配条件、status/error_code；按原“成功优先、id倒序”选择 | 地址仅绑定/比较，不输出；不读取response |
| `review_human_reviews` | 当前标注关联的最新id/action | 不取reviewer/note |
| `review_opinions / review_human_opinions` | 当前有效标注/修订的观点ID、标签、范围及quote的库内UTF-8 SHA-256 | 仅必要时在库内读取quote计算摘要，不向客户端返回quote，不取评论全文 |
| `review_opinion_embeddings` | 同批review_id关联的key、完整集合摘要、标注/修订/观点ID、内容和输入摘要、发布时间及状态 | 不输出embedding坐标 |

先聚合当前标注/内容/人工修订/观点ID是否相等；只有仍无法区分时，才输出完整当前集合的非正文元数据，在本地按已有规范化定义比较key及snapshot。规范化摘要比较仅供诊断，不构造 `RagOpinionDocument` 冒充原文校验、不签发P0证明、不授予语义许可。必须保留空人工快照及所有有效观点，不能只拿命中单观点重算整评论集合。

首次回执没有保存053全部key和完整集合，因此不能离线补造这批元数据。读取量需根据D01新计数再明确：历史上限参考为2209条评论状态、4682个有效观点及其关联派生历史行；实际行数与必要SQL在取得D01后另行确定，而不是本轮提供一个可自动连接业务库的入口。若需要全文做真实P0验证，必须另外说明并授权，不把诊断指纹当成P0来源绑定。

### 10.5 条件性最小修复建议与写入边界

本轮唯一推荐动作是先做获授权的D01计数；还不能提出已可执行的写入方案。若确认A，最小方向是复用 `sync_effective_opinions` 为**053的原source/dataset/日期/profile、固定BGE空间**建立一个有限批次，而非改Agent、降级校验、重新标注或全量回填。

- 未来候选批次可沿用现有确定性顺序及完整评论组选择，首批最多200个有效观点，不根据10个问题的答案挑选；实际N及完整review/key清单应在只读核对后冻结。不能把“3591个有效观点”当成已获批3591次编码，也不能声称200条必然使MVP通过。
- 若A确为无任何关联/声明向量，新增派生行上限N≤200、同步记录1条、源表写入0；本地观点新编码上限N，外部API费用¥0。这是条件预算，**当前实际N未知，不申请立即执行**。
- 现有同步不仅做N条UPSERT，还会失效化同范围/profile/space的全部过期key，并短时锁住向量表及7张源表。必须先得出待失效数I及待更新既有行清单，精确写入影响为N条发布/更新、I条失效化及1条同步记录。I未知时不得以“只写200行”取得许可；若B成立，应先评估能否复用未变输入的向量，不能一律重编码。
- 只调整完整集合摘要而key/输入未变时，可考虑现有缓存复用和受检重新发布；key或输入变化时才考虑对应新编码。不得手工复制旧向量到新key、清除失效标记、修补SHA或删除必要JOIN。
- 未来回退应在写前仅备份明确受影响派生行的完整前镜像（含向量、状态和关联run），不备份其他商品正文。事务失败回滚；提交后若需回退，只移除该批新增行、恢复受影响既有行前镜像，必须核对run/行版本以免覆盖后来写入，保留并说明同步记录。不删源评论、不重置标注或恢复整库。回退同样属于待授权写操作。
- 历史055的200条新观点编码约26.49秒、同步33.76秒，内存峰值约1.8GiB；本次查询进程冷加载约19.65秒。首批N≤200可暂按本地CPU数十秒至数分钟、约2–3GiB工作内存预留，不是实测承诺；N、超长拒绝、机器负载和锁等待均会影响成本。过长quote不得截断，源变化/锁冲突或失败立即停止，零自动重试。

若D01–D03指向B、C或D，则只按已确认的具体差异修复，不执行上述A类回填。尤其不因SQL无结果就重建索引或更换模型。写入范围、精确N/I、前镜像回退及本地观点编码必须单独授权；任何原10案例真实复测另需新的A/B授权，不能沿用已用完额度。

### 10.6 本轮离线验证及停止点

规范化fixture摘要、首次回执SHA及当前配置profile核对一致；使用原 `_range()` 复现两个商品的WHERE和参数，确认没有日期、source/dataset、空间或builder被本轮改写。新运行的既有离线测试31/31通过：`test_semantic_retrieval` 28项、`test_embedding_store.VectorValidationTests` 3项；真实PostgreSQL、外部HTTP/模型和编码器均设阻断。数据库型EmbeddingStore测试只读查阅，没有启动隔离数据库或执行其测试写入。

本轮仅更新本报告，未修改冻结fixture、实现、私有首次回执、P5材料或历史结果。已缩小到覆盖/身份关联门槛，当前物理缺失与版本失配仍需D01等只读计数区分。**等待单独授权D01–D03的限定只读诊断；在此停止，不执行修复、向量同步、编码或复测。**

### 10.7 单独授权D01的实际执行记录：连接失败，未发送查询

2026-11-07 18:57:31（UTC+8）。用户仅授权D01一次，不包括D02/D03、其他业务查询、编码或检索。本次在连接阶段即出现 `psycopg.OperationalError`，SQLSTATE为空，未取得可用连接；**D01没有发送、没有聚合结果**。按异常立即停止要求，未重试、未改SQL或配置绕过失败。

执行前离线核对通过：fixture规范化摘要仍为 `a50c41143fe142e4a0a7c3e83afc9fb620724124cdf44d874912f09f06782c29`，当前标注配置绑定冻结profile；从本报告D01代码块直接提取SQL，仅将 `:name` 替换为驱动的命名绑定标记，并验证可逆还原。原SQL模板SHA-256为 `62f15775b5ef4b8e841212453c1c853609856df467917470c12e66b41215a86c`，没有改写过滤或关联逻辑。

参数保持为原source/dataset、053/055、TEXT日期下界2021-09-11 UTC及开上界2022-08-27 UTC、原profile、固定1024维空间和 `quote-utf8-v1`。连接启动选项请求public search_path、UTC及默认只读；计划在该连接执行 `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY`、事务内10秒statement timeout和1秒lock timeout。但由于连接未成功，**这些事务控制尚未执行，不能声称实际只读事务或超时配置已核验**。

| 项目 | 实际结果 |
| --- | --- |
| 连接尝试 / 成功连接 | 1 / 0 |
| D01发送 / 完成 | 0 / 0 |
| D02 / D03 / 其他业务查询 | 0 / 0 / 0 |
| 事务创建、超时SET、ROLLBACK | 均未进入；没有可回滚的事务 |
| 连接回收 | 未获得连接对象；无本次已建立连接需要关闭 |
| 编码 / pgvector相似度检索 / 外部模型调用 | 0 / 0 / 0 |
| 业务写入 / 自动重试 / MVP案例复测 | 0 / 0 / 0 |
| 053与055的本次计数 | 均N/A；没有将未执行误记为0行 |

仅保存安全异常类别与SQLSTATE，不公开连接地址、凭据或底层异常正文。现有记录只能定位到“建立连接阶段失败”，不能确定是数据库服务、网络/运行权限还是连接配置原因，也不能据此断言数据库宕机或向量缺失。总尝试约2.7毫秒，连接失败未触发10秒SQL超时；这不是一次D01查询超时。

受限操作回执位于Git忽略的独立目录 `outputs/rag-semantic-retrieval/mvp-d01-20261107-v1/result.json`，包含冻结参数、模板摘要、零查询计数及连接失败状态，没有正文、quote、向量坐标或敏感配置。该目录的本次尝试claim已占用，不重用。首次验收回执、冻结fixture、配置及受保护实现摘要未改变。

**结论仍为BLOCKED，具体根因尚未通过D01确认。** 历史证据继续支持“已记录建设仅覆盖055”的首要解释，但本次无法区分053当前向量缺失与存储身份失配。下一步若继续，只能先明确连接障碍，再由用户重新授权D01尝试；不因本次查询发送数为0而自动延续额度。D02/D03、来源身份补查、向量同步/修复和真实复测均未授权，本轮到此停止。

### 10.8 D01连接失败的离线对照诊断

2026-11-07，仅读取源代码、既有回执、配置及Windows本机进程/服务/监听表/本地日志；**本轮新建PostgreSQL连接0，业务SQL0，网络连接尝试0，编码/向量检索/外部模型0**。只追加本节，不修改正式Agent、D01 SQL、连接设置或任何安全保护。

#### 问题1：两次连接有哪些实际差异？

成功路径依据为私有 `mvp-first-20261107-v1/run_once.py`、原 `reviews/store.py::connect_review_database`、`core/database.py::PostgresConnection` 及15次成功回执。D01依据为本聊天保留的实际执行单元及 `mvp-d01-20261107-v1/result.json`；D01没有另外保存执行脚本或完整客户端环境快照。

| 项目 | 首次成功路径 | D01失败路径 | 离线判断 |
| --- | --- | --- | --- |
| 配置来源 | `read_settings(ROOT/'.env')`；dotenv禁止插值，进程环境优先 | 同一函数读取工作区同一 `.env` | 文件摘要与首次预检一致；当前没有进程级DATABASE_URL覆盖 |
| DSN构造 | `database_target` 对PostgreSQL DSN原样保留，`database_identity`只做校验/绑定；原DSN传给psycopg | 配置返回的原DSN直接传给psycopg | 未发现地址重写、密码转义或URL拼接差异；两次都不是手工重组DSN |
| host/port/database | 首次本地SQLite队列保存不含凭据的目标身份 | 本轮仅在内存比较当前配置身份 | 三项与首次持久绑定一致，不展示实际值 |
| user/密码及其他敏感连接字段 | 来源为同一配置；历史进程环境未单独保存 | D01也未保存DSN指纹或历史环境副本 | 当前有效配置逐字段与未变更的文件一致；不能独立证明两个历史进程不存在瞬时环境覆盖，不公开值或密码摘要 |
| SSL及隐式PG环境 | 调用未显式设置SSL参数 | 调用同样未显式设置 | 当前DSN未显式覆盖SSL，当前相关PG环境未设置；没有已证实差异，也没有TLS握手证据；不能回填历史SSL状态 |
| `connect_timeout` | 10秒 | 10秒 | 一致；2.7毫秒不是该超时到期 |
| 连接启动 `options` | `-c search_path=public -c timezone=UTC` | 在前者之后增加 `-c default_transaction_read_only=on` | **确定存在的启动参数差异**；但没有参数拒绝错误证据，不能定性为根因 |
| 默认只读的设置时点 | connect成功后执行 `SET default_transaction_read_only = on`，再进入只读事务 | 默认只读放在连接启动参数，随后计划显式BEGIN READ ONLY | 两者意图均为只读；D01尚未走到BEGIN或SET LOCAL，不能把这些未执行操作当作失败原因 |
| 行对象工厂 | 项目 `_row_factory` | psycopg `dict_row` | 确实不同；用于结果行转换，当前没有证据表明它导致建立连接失败 |
| 连接后检查 | 正式封装会核验业务schema，首次驱动还SHOW核验只读 | 直接驱动避免执行D01以外的业务检查 | D01失败早于任何schema查询；不能由“没有走schema检查”解释本次connect异常 |
| 运行入口 | 工作区业务 `.venv` Python，以文件入口、`-B`运行 | 同一业务解释器，以stdin入口、`-B`运行 | 当前Python 3.12.10、psycopg 3.3.6、libpq 180004，模块来自业务venv；两次历史token/网络隔离状态未保存，不能仅因解释器路径相同就认定执行权限相同 |
| 测试Mock/连接保护 | 成功驱动拦截外部HTTP/LLM及业务写入；未Mock `psycopg.connect` 或socket连接 | D01执行单元没有额外Mock、socket monkeypatch或Python网络审计钩子 | D01冻结/config校验在connect前已通过；当前驱动/socket也不是Mock。未发现D01额外测试拦截导致失败的证据 |

当前配置比较只返回一致/不一致，不输出连接字符串、地址、数据库名、用户名、密码或敏感环境值。首次与D01均保留了未改配置的证据，但没有保存足够的历史运行环境指纹；上述“当前相同”与“两个历史进程已逐字段实测相同”必须区分。

#### 问题2：是否发现确定原因？

**仍未确定D01在18:57:31发生OperationalError的根因。** 获得了如下本机状态证据，不能再简单解释为“PostgreSQL服务宕机”：

- 19:22–19:27只读检查时，Windows PostgreSQL服务为Running/自动启动，主postgres进程及pg_ctl存在。主进程和pid文件启动时间均为2026-11-05 22:43:06，早于首次验收和D01；pid文件关联现存进程，端口与客户端配置比较一致，状态标记ready。服务日志中未找到15:40–19:30的PostgreSQL服务状态变化事件。这些不等于已验证数据库可连接或每一时刻均正常。
- **异常线索：**19:27的本机TCP监听枚举成功，共10个可见监听项，但配置端口没有监听项，postgres进程也没有可见TCP监听；`netstat -ano -p tcp`（使用`-n`避免DNS）交叉结果相同。该观测与Running/ready标记不一致。它提示应核对客户端可见监听端点及执行环境隔离，而不是先改SQL；它是当前工具环境的观测，不能直接回推18:57的历史监听状态，也不能在未核对网络隔离视图时断言宿主机全局没有监听。
- 当前默认工具Python的访问token为AppContainer。本轮默认权限读取Windows服务曾被拒绝，获准的提升权限只读检查才取得服务信息。说明环境权限确实需要区分；没有首次成功和D01失败时的token、网络compartment或回环访问记录，**不能据此直接认定“沙箱阻断”就是历史根因**。提升权限的本机状态视图也不能冒充受限客户端的实际连通结果。
- 定位的当前PostgreSQL本地stderr日志为0字节；可读配置文件启用了日志收集，时区为Asia/Taipei，未发现显式开启的连接记录配置。检查D01对应本地/UTC时间窗，没有可用的启动参数拒绝、认证失败或重启日志。空日志不证明连接曾到达服务器，也不证明客户端没有被拒绝。
- D01回执仅保留异常类型、空SQLSTATE和计时，没有异常正文、WinSock/OS错误码或libpq错误分类。已保存材料无法进一步区分连接被拒绝、访问权限/回环隔离、启动握手或其他客户端错误。SQLSTATE为空本身不能完成这种区分。

本轮没有用新的连接测试验证上述线索，没有执行ping、TCP探测、pg_isready、psql或D01。服务/监听表检查只读本机状态，不发起网络连接。

#### 问题3：已定位时的最小修复是什么？

由于根因尚未确定，**当前没有证据充分的配置或代码修复**。不移除启动只读设置、不改host/port/SSL、不关闭AppContainer/防火墙或其他保护，也不重启服务“试试看”。单独的启动options差异和当前未见监听均需要进一步证据，不能盲目择一修改。

已确定的观测缺口可以在下一次明确授权的单次尝试中最小补齐：保留安全的客户端错误分类及可取得的OS/WinSock错误码、实际失败阶段、执行权限上下文和库版本，而不是只保存异常类。原始错误若需保存，必须先脱敏，并只保存在既定Git忽略目录；不能把含DSN/用户/凭据的异常正文直接打印。此项是诊断可观测性建议，本轮未生成新连接入口或修改既有实现。

#### 问题4：再次授权连接前还需验证什么？

1. 在拟执行D01的**同一权限与网络隔离上下文**下，再只读确认配置端口的监听可见性及进程归属，区分“真实端点缺失”和“当前视图/回环访问受限”；不为了得到结果自动改变安全策略。若需修改服务或权限，先明确原因和单独授权。
2. 重新核对有效配置与原目标身份一致，并记录不含敏感值的配置一致性、解释器/psycopg/libpq版本、运行权限信息；不能仅检查 `.env` 文件而忽略进程环境覆盖。保留所有原冻结范围和安全设置。
3. 约定单次连接失败时的脱敏错误记录字段和停止条件，优先区分权限拒绝、端点拒绝、超时、TLS/认证或启动参数错误；仅记录当前异常类无法承担该判定。没有错误码时如实记未知，不猜测。
4. 只有用户重新授权后，才允许一次连接与同一D01。仍使用RR/READ ONLY、10秒statement timeout、1秒lock timeout，结束ROLLBACK/close，零自动重试。若选择复用封装，须避免 `connect_review_database` 自动增加schema业务查询；不得超出“仅D01”的查询授权。D02/D03、向量写入和MVP复测仍不包含在内。

本节完成后停止。连接失败原因仍标记“离线未定位”；053向量覆盖仍未确认，MVP状态继续 **BLOCKED**。原D01失败回执、首次验收结果及冻结SQL未修改。

### 10.9 最后一次受控连接诊断：WinSock 10013，D01未发送

2026-11-07 20:07:50（UTC+8）。本次用户另行授权最多一次真实连接，以及成功后执行原D01一次。实际仅调用 `psycopg.connect` 一次，**连接失败，取得了此前缺失的OS错误分类；未建立PostgreSQL会话，D01未发送**。没有自动重试或提升权限后再连。

#### 同一进程的环境与监听检查

在将要连接的同一业务venv Python进程内，先使用Windows本地API读取访问token、网络compartment和TCP监听表，再调用连接函数；没有额外TCP探测、DNS探测或数据库连接。冻结目标为单个数字地址及单个端口，没有增加备用地址或改动SSL设置。

| 检查项 | 本次实际观测 |
| --- | --- |
| 解释器/依赖 | 项目原 `.venv`，Python 3.12.10，psycopg 3.3.6，libpq 180004 |
| 执行权限 | `use_default`，AppContainer=true，token_elevated=false |
| 网络隔离标识 | 当前线程compartment ID=1；该数值本身不代表具有回环访问权限 |
| IPv4/IPv6监听表 | 同一进程两类读取均返回Windows错误5（ERROR_ACCESS_DENIED） |
| 目标监听端点 | **未知，无法在该权限环境下读取**；不是“已确认未监听” |
| 连接参数 | 保持原D01：autocommit=true、dict_row、connect_timeout=10，启动options仍为public search_path、UTC、default_transaction_read_only=on |
| 与首次成功环境对照 | 同一业务解释器和配置加载方式；首次未保存token/compartment，不能宣称两次网络权限相同。首次连接后设只读、D01启动时设只读的既有差异未被修改 |

监听读取使用 `GetExtendedTcpTable`，仅检查本机状态，不创建连接。此前10.8节另一检查上下文中的“可见TCP表没有目标端口”不能替代本次同进程检查；本次应明确记为权限拒绝、可见性未知，不能据此断言PostgreSQL没有监听或服务宕机。

#### 已取得的脱敏连接错误证据

| 字段 | 实际值及含义 |
| --- | --- |
| 失败阶段 | `psycopg_connect`，尚未进入事务设置和D01 |
| psycopg异常 / SQLSTATE | `OperationalError` / null |
| libpq错误类别 | `os_access_denied` |
| OS/WinSock错误码 | **10013 / WSAEACCES：套接字访问被拒绝**；提取自libpq异常的数值错误码，使用本地Python errno映射核对名称 |
| 可定位的子阶段 | socket或TCP访问阶段；现有分类不进一步猜测是创建socket还是执行connect的具体系统调用 |
| libpq连接状态 | 1（BAD），无成功会话 |
| 线程末次WinSock错误 | 0，仅作非权威观测；不覆盖libpq已保存的10013错误码 |
| 连接调用耗时 / 本次总耗时 | 2.354毫秒 / 6.322毫秒；不是10秒超时到期 |

**本次连接失败已定位到客户端/OS层的套接字访问权限拒绝，而非D01 SQL执行失败。** AppContainer和本地TCP状态读取受限与此现象同时出现，但不能仅凭这些证据锁定具体是哪条AppContainer、回环、防火墙或其他主机访问策略；本次没有读取或修改这些安全策略。也没有证据证明启动只读options被服务器拒绝，不移除该保护来盲试。

这比10.7节只有异常类型的记录更具体，但不能把本次10013事后补写成18:57历史失败中未曾保存的错误码。服务是否能够由其他已获许可的客户端连接，不在本次结果的证明范围内。

#### 执行数量、事务保护及停止结论

- 真实连接尝试1、成功0；D01发送0、完成0；D02/D03及其他业务查询0；TCP探测0；自动重试0。
- 原计划是成功后BEGIN RR READ ONLY、设置10秒statement timeout/1秒lock timeout，并SHOW确认只读及隔离级别后才执行D01。由于connect失败，BEGIN、SET LOCAL、SHOW均未执行；不能把计划中的保护写成服务器已验证。没有事务可ROLLBACK，也未取得需要close的成功连接对象。
- 053/055本次向量计数均为N/A，**未将无结果误记为0行**；向量缺失、版本失配或当前key/完整快照的判断仍需实际D01结果，不能由10013推导。
- 编码、pgvector相似度检索、外部LLM/Embedding、业务写入、同步/回填、索引/服务修改、MVP复测均为0。`publication_allowed=false`、`semantic_verified=false`保持不变。
- 只保存白名单结构化类别/错误码、权限与监听检查状态，没有保存或输出原始异常正文、DSN、凭据、地址、评论、quote、向量坐标或个人信息。

安全操作回执保存在Git忽略的独立 `outputs/rag-semantic-retrieval/mvp-d01-final-20261107-v1/result.json`，SHA-256为 `6bcc6fd4b02a14e1f18ac46f6bd4bd000d5a0545069e1cbb1f2a25e4c060263f`；独立claim已占用，不复用。规范化fixture摘要及原D01 SQL摘要与10.7节完全相同；配置、受保护实现、首次验收和此前D01失败回执均未改。

MVP继续 **BLOCKED**。下一步若由用户决定继续，应针对客户端允许访问既定本机数据库的执行权限处理，而不是先改检索SQL或写向量；本轮不切换为提升权限连接、不关闭任何安全保护、不自动发起新的授权阶段。已用完本次唯一连接尝试，在此停止，不执行修复或后续连接。

### 10.10 普通Windows PowerShell手动D01脚本准备（未执行）

用户提供了普通Windows PowerShell中 `pg_isready` 返回接受连接的信息。此为用户提供的端口就绪证据，**不是D01已执行或向量覆盖已确认**；Codex本轮没有重跑该命令、连接PostgreSQL或执行D01。

新增独立入口 `scripts/run_rag_mvp_d01.py`，以及仅使用合成配置、聚合行和连接替身的 `tests/test_rag_mvp_d01_script.py`。未修改正式Agent、连接封装、检索SQL、P5、冻结10题或历史回执。脚本不依赖外部模型，也不会自动修改本报告。

在**普通Windows PowerShell**中，检查脚本后由用户手动执行：

```powershell
Set-Location 'E:\AllProject\data-analysis-agent'
& '.\.venv\Scripts\python.exe' -B '.\scripts\run_rag_mvp_d01.py' --execute-once
```

不带 `--execute-once` 时只做离线预检，不连接PostgreSQL，也不占用执行名额；离线预检会读取本地私有队列的身份元数据。可选命令为：

```powershell
& '.\.venv\Scripts\python.exe' -B '.\scripts\run_rag_mvp_d01.py'
```

本轮Codex没有对真实配置/私有队列运行该入口；只执行了合成替身测试和代码检查。

安全边界：

- 从本报告10.3的D01代码块提取原SQL，校验固定SQL摘要及原fixture规范化摘要；只转换驱动绑定标记，不修改谓词、JOIN、日期或参数。profile、两商品、source/dataset及固定1024维空间完全沿用原冻结值。
- 使用项目原 `.venv`、`read_settings` 和psycopg，原DSN不拼接、不重写，启动options及10秒connect_timeout与原D01相同。对照首次私有队列 `task_context` 的数据库身份，仅以本地SQLite只读模式读取该行，不调用业务schema初始化。
- 同时核对 `.env` 与首次预检的文件摘要，要求有效DATABASE_URL与文件一致；未冻结的PG连接环境覆盖会使预检失败，不自动清空环境变量、改配置或选择其他目标。缺失首次私有身份文件也失败关闭。此严格检查意味着即便是其他配置字段修改，也需要先人工核对，脚本不会自行更新基线。
- 显式 `--execute-once` 才可能尝试连接。在 `outputs/rag-semantic-retrieval/mvp-d01-manual-v1/` 原子创建一次性目录/claim后，最多调用一次 `psycopg.connect`；没有重试循环、备用地址或可绕过claim的命令行参数。成功或失败均保留占用，**不要删除目录后补跑**。
- 成功后先BEGIN RR READ ONLY、设置statement_timeout=10s及lock_timeout=1s，通过两个SHOW确认只读与隔离级别，再执行原D01一次。没有D02/D03、额外业务查询、Embedding、向量相似度检索、同步或Agent调用。
- 成功或查询失败均在finally中ROLLBACK并close；连接未建立时没有成功连接可关闭。回滚或关闭异常会记为失败并尽力完成另一项清理，不掩盖资源问题。
- 对输出的两商品、列名及计数/维度/范数数值设白名单验证，拒绝额外正文列、其他商品或非法数值。异常只输出阶段、类型、SQLSTATE、错误分类和可识别的WinSock码，不输出异常原文、DSN、凭据或敏感配置。

正常stdout为一个JSON对象：包含两款商品的原D01聚合计数、维度/范数聚合（不是向量坐标）、初步诊断、调用次数与回滚/关闭状态。若没有聚合结果，不会补零。`f_space_builder`是原SQL的合并过滤层，不能自动细分是哪一个不匹配；active行仍不等于当前key/完整集合快照已通过。

同一安全JSON保存于上述Git忽略目录的 `result.json`；脚本不保存评论、quote、向量坐标或原始错误正文。若预检失败，stdout返回安全错误码、连接次数0，尚未创建一次性目录；若已经开始尝试，即使失败也不补跑。退出码0表示离线预检通过或D01执行/清理完成，**不表示MVP通过**；退出码1表示安全检查、连接、查询或清理失败。

离线专项 **10/10通过**，覆盖默认零连接、冻结/配置/目标漂移拒绝、单次D01、只读与隔离门槛、连接及查询失败不重试、原文列拒绝、回滚/关闭与脱敏。真实PostgreSQL、真实SQLite和socket连接均设阻断；本地身份读取测试也使用替身，没有读取真实评论或私有任务内容。未运行普通Windows环境中的真实脚本，故仍不能声称D01连接成功。

本轮仅准备交付，MVP继续 **BLOCKED**。等待用户检查并在本机手动执行；Codex在此停止，不自动调用 `--execute-once` 或开展后续操作。

### 10.11 手动D01结果确认与053首批同步准备

#### 已确认事实：D01成功，053覆盖缺失

已只读核对用户提供的 `outputs/rag-semantic-retrieval/mvp-d01-manual-v1/result.json`，与本轮说明一致。回执SHA-256为 `b5758634b629b51eef38845ec27ba0f192a41877867bec3338056a7312de42f5`。回执没有开始/结束墙钟时间，不补造执行时刻；这是用户在普通Windows PowerShell完成的真实执行，**不是Codex重跑**。

| 回执项 | 核对结果 |
| --- | --- |
| status | completed |
| 连接尝试 / D01尝试 / D01完成 / 自动重试 | 1 / 1 / 1 / 0 |
| transaction_protection_confirmed / rolled_back / connection_closed | 全部true |
| 053评论 / 声明范围向量 / 按真实review_id关联向量 | 1699 / **0 / 0** |
| 055评论 / 声明范围向量 / 按真实review_id关联向量 | 510 / 200 / 200 |
| 055 scope / profile / space+builder / active过滤后 | 200 / 200 / 200 / 200 |
| 055范围、内容、时间字段不匹配数 | 均0 |
| 055存储维度 | min=max=1024 |

据此确认：**在该D01快照及冻结范围内，053属于A类向量覆盖缺失，不是已有相关行在profile/space/active过滤后归零。** 055的200条通过上述元数据漏斗，但D01并未验证其全部当前key/完整集合快照；不能将这个计数升级为新的语义准确率或P0许可。

未编码、未做相似度检索、未写库的说明与用户陈述及手动脚本边界一致；回执显式记载的次数及事务/关闭状态如上。此前3591个有效观点来自首次验收，D01只重新计数评论及派生向量，未重建当前有效观点清单。第10.7–10.10节历史原样保留，之前的失败连接没有改记为成功。MVP仍为 **BLOCKED**，尚未同步053或复测原10题。

#### 复用入口与确定性选择

复用现有 `scripts/sync_opinion_embeddings.py` 的sync入口及 `embedding_store.py::sync_effective_opinions(..., limit=200)`，保留 `LocalEmbeddingProcess` → `local_embedding_worker.py` → 固定本地BGE-M3。不新建Embedding系统，不调用迁移/rollback子命令，不需要重建表或索引。

固定范围为 `source=reviews_csv`、`dataset_version=reviews-csv`、商品**仅053**、UTC 2021-09-11至2022-08-26、`as_of=null`；profile为第3节冻结值，固定BGE-M3修订、1024维、`quote-utf8-v1`均不变。这里将用户所称数据集reviews_csv落实为原source标识，不把dataset_version误改成reviews_csv。

原 `read_effective_corpus` 按review_id、quote起止、aspect、sentiment及观点来源ID排序；原 `_select` 依次处理每条评论的**全部有效观点组**。整组放得下剩余额度才选入，放不下则跳过并继续后组，不拆评论、不按业务主题/相似度/10题答案筛选。故首批选中数S满足0≤S≤200，**不能只凭3591总数认定S=200**。本轮仅用虚构组大小验证了该算法，没有对真实评论做选样或裁决。

#### N/U/I：哪些已知，哪些尚未核定

| 数量 | 定义及当前状态 |
| --- | --- |
| S | 原算法选中的完整评论组观点总数，≤200；缺少当前分组清单，**实际值未确定** |
| U | 选中key在固定space/builder唯一约束下已存在、将被UPSERT更新的行数；`_cached`不以日期/商品WHERE过滤，而按选中key匹配，**实际值未确定** |
| N | 成功发布时新增行数；要求整批输入均可用时N+U=S。首批目标是U=0、N=S≤200，但**尚非已核定写入预算** |
| I | 原 `_publish` 失效化UPDATE实际匹配的旧行数；需完整当前valid_keys及同快照派生行判断，**执行时数值未确定** |

D01证明的是该快照下053冻结存储范围及关联review_id均没有向量。在完全相同、无变化的那一快照，原失效化WHERE亦没有候选；但它不冻结后来数据库状态，不代表所有历史范围永远没有旧行，也不能替代同步发布时的I核验。

原失效化WHERE只覆盖053相同source/dataset、日期及as_of，再限定同profile、同space、active且key不属于**整个当前有效语料集合**；不是全表历史失效化，也不是仅与首批S比较。该UPDATE没有额外builder筛选，不能在预算核验时擅自加上条件来缩小I。范围之外的旧记录不应因这条UPDATE被修改。

另需核对全局唯一key的实际所有者：`ON CONFLICT(key,space,builder)`不是带商品WHERE的UPDATE。正常key包含商品等身份，但不能仅凭哈希定义代替持久化行核验。若选中key命中055或其他错误来源字段，必须阻断，不能让UPSERT更新该行。任何与D01预期不一致的U/I或所有者情况均应先报告，不自动复用、清除失效标记或扩大修复。

#### 精确预算所需的最小新增只读范围（尚未授权/执行）

要冻结实际N/U/I，需要以下受限读取，**本轮到此列出，不连接数据库**：

1. **053当前受检语料一次。** 在只读RR快照中复用 `read_effective_corpus`，逻辑范围仅上述053窗口/profile；读取必要product/import身份、评论全文/内容身份/日期、当前标注及完整有效人工/模型观点。历史规模1699条评论、3591观点，当前计数或来源变化须记录并停止复核，不能截断或补造。此拥有者接口确实读取该范围全文，不谎称只读200条；不读其他商品全文、原CSV或人工评审答案。随后仅在内存运行原 `_select(...,200)`，保存私有review/key/完整快照清单和S，不做编码。接口含必要schema/目录检查及受限语料SQL，不能宣称只有一条SQL。
2. **选中key的已有行元数据查询一次。** 条件精确复用 `_cached` 的 `key=ANY(选中keys) AND space=固定值 AND builder=固定值`，读取行ID、完整商品/来源身份、review/annotation/修订/观点ID、输入/内容/快照摘要及状态；不返回向量坐标。确定U、待编码P=S−U，并确认不存在055或错误所有者冲突。不能加商品过滤而把全局唯一键冲突藏起来；只读取选中key命中行，不扫描055正文。
3. **失效化候选计数及必要行ID一次。** 使用与 `_publish` 完全相同的WHERE、当前profile/space和**全部valid_keys**做SELECT计数；I>0时列出需备份的限定行ID/元数据，不执行UPDATE。不因D01已为0而跳过这一项，也不对全历史范围作宽泛失效化。
4. **055现有200行保护基线一次。** 严格限相同冻结source/dataset/日期/profile/space/builder，对既有055派生行在库内计算COUNT及覆盖完整行值的确定性摘要；客户端只接收聚合，不返回坐标、正文或个人字段。库内摘要会读取向量值，需明确包含在只读授权中。未来同步提交后对同范围再核对同一摘要；仅计数和范数相等不足以证明内容未改。

上述不包含D02/D03、不产生查询向量、不做相似度检索或同步记录写入。原文及候选身份如需暂存仅在Git忽略私有目录，Git只保留本汇总。取得这些结果后，才能给出精确S、N/U/I、完整组/行清单及写入授权请求；现在不能将“N=200、U=I=0”写成已知事实。

#### 实际写路径、锁及055保护

| 对象 | 现有实现的动作及风险边界 |
| --- | --- |
| `rag_embedding_sync_runs` | `_run`在独立事务先新增1条running记录，之后更新该行状态/metrics；模型失败也可能留下failed记录，不是整次运行所有表原子回滚 |
| `review_opinion_embeddings` | 发布事务N条INSERT、U条UPSERT更新、I条失效化；拿 `SHARE ROW EXCLUSIVE` 表锁，lock_timeout=3s、statement_timeout=15s |
| 7张源表 | `products/import_runs/reviews/review_annotations/review_opinions/review_human_reviews/review_human_opinions`，只重读不做DML，但发布阶段持有 `EXCLUSIVE NOWAIT` 表锁以阻止源变化 |
| 发布一致性 | 加锁后在新的READ COMMITTED快照重读全部053语料，要求等于编码前语料；变化/锁冲突即报错或retry_required，现有CLI不自动重试 |

原DML的商品/范围条件和正常key身份设计支持053隔离，但**不等于对055零影响的完整证明**：必须确认选中key冲突行所有者均为053，并在发布锁内核对待更新/失效化的实际影响没有超出批准清单；提交后055的COUNT及完整行摘要应与基线相同。若不一致，停止并按回退方案处理，不重新同步055。表锁覆盖全表，可能短时阻塞055等商品的写入或加锁读，普通SELECT不因此被同类锁直接阻断；不能承诺完全无运行影响。

现有CLI没有dry-run和精确N/U/I预算门控，`sync`会真实写入，**不是预检命令**。另外，原选择阶段虽保持完整评论组，编码阶段对单个超过512 tokens的quote会返回None，原发布器会跳过该观点、发布其他观点。这不满足本次完整组要求。因此不能直接把裸CLI命令当作已获准的安全执行方案。

最小可落地方式仍是复用原同步函数；在精确清单获批后，如需落实严格门槛，只补现有入口的默认dry-run/受检执行薄层和发布锁内影响检查，不复制同步器。编码输出中任一None/非法向量应在发布前让整批失败关闭，不截断、不拆分、不补选更有利样本。原函数的源快照重验、原文校验与空间检查均须保留。**本轮不实现这些改动、不新增入口，也不提供可直接写入的命令**，先等待上述只读范围授权。

#### 编码资源、失败回滚和提交后回退

- 若P>0，现有 `LocalEmbeddingProcess.encode` 一次本地子进程/一次模型加载处理P≤200条quote；内部CPU4线程、batch=8，最多25个推理小批次。不是200次付费API或200次模型加载。P=0时不启动编码子进程。只用既有本地缓存及固定修订，不下载、不向模型进程传数据库凭据，不对全部3591观点编码。
- 512-token门槛和完整组要求必须失败关闭；实际N在无超长/无错误且发布成功时才成立。历史055新编码200条约26.49秒、同步33.76秒、峰值约1.8GiB，本次查询模型冷加载曾约19.65秒；可暂预留本地数十秒至数分钟、约2–3GiB工作内存，非保证上限。外部LLM/Embedding调用和API费用均为0。
- 发布事务内失败：N/U/I向量变更一起ROLLBACK，源表不写；独立sync记录可能保留failed/retry_required，必须如实保留，不自动重试。源表/派生表锁随事务结束释放。不能声称模型失败后数据库完全没有任何新增日志行。
- 写入前仅为实际U/I及唯一冲突检查确定的行保存私有完整前镜像，必要时含向量及关联run；新插入行在提交回执中记录确切ID/key。若确认U=I=0，回退主体是本批新增N行，但仍须按实际清单操作，不删除整个053或整张表。
- 提交后回退需单独写入授权：锁内核对相关行仍为本run发布版本，再精确移除本批新增行、恢复U/I前镜像，保留并说明sync历史记录；不改055、源评论或标注。若有后续写入/提交确认丢失，先只读对账，不盲目删除或重跑，不恢复整库。

#### 本轮结论与下一步授权

已确认A类覆盖缺失；**实际S/N/U/I尚未冻结**。期望首批不超过200条完整组观点，成功也仅为有限覆盖；按此前3591规模仍至少留下3391条未覆盖，不能宣称已完成全量索引。实际剩余数以未来当前语料及提交后的有效回连计数为准，不以N替代来源或语义验收。

后续首先需要上述限定只读核验授权；然后依据明确清单，分别授权一次053派生表写入/同步记录写入、所需本地观点编码及必要锁范围。055保护基线和提交后核对也需在该范围中说明。原10场景真实验收仍需另获A/B授权，不能由“同步成功”自动开启。

本轮仅读取既有回执与源码、做纯内存的合成选择及WHERE范围检查，没有新数据库连接、真实BGE编码、写入、检索或复测；只追加本节。MVP继续 **BLOCKED**，`publication_allowed=false`、`semantic_verified=false`及正式语义许可边界不变。完成准备后停止。

### 10.12 053同步预算只读入口准备（待用户本机执行）

本轮按用户要求准备普通Windows PowerShell入口，**未授权Codex连接数据库，故实际S/N/U/I/P和055完整行摘要仍未取得，不能填入合成测试数字**。MVP继续BLOCKED。用户手动完成后，可只读分析保存结果并在本报告登记实际预算；此准备阶段不会自动等候文件后运行同步。

新增 `scripts/run_rag_mvp_sync_preflight.py`，默认只做离线配置/冻结检查。用户检查后在普通Windows PowerShell中手动运行一次：

```powershell
Set-Location 'E:\AllProject\data-analysis-agent'
& '.\.venv\Scripts\python.exe' -B '.\scripts\run_rag_mvp_sync_preflight.py' --execute-readonly-once
```

不带该参数不会连接PostgreSQL，也不占用批次；其离线目标校验会读取既有私有SQLite队列的身份行。本轮Codex未运行真实配置上的该入口，离线测试使用连接替身，不打开实际PostgreSQL或SQLite。

#### 读取与安全边界

- 复用原 `.venv`、配置加载器、首次私有数据库身份、未变化的 `.env`、原冻结fixture、profile/1024维space/builder。原语料/选择/范围实现及D01安全辅助脚本的摘要发生变化时，预检失败，不自行接受新版。显式核对Git忽略规则后才允许保存完整私有清单。
- 显式参数触发一个一次性批次，独立目录 `outputs/rag-semantic-retrieval/mvp-053-sync-budget-v1/` 原子占用；最多一条数据库连接，失败停止、零自动重试，不提供force/换目录补跑参数。保留claim，不删除后重跑。
- 使用原 `PostgresConnection` 行类型与桥接，以readonly=true连接；在唯一RR READ ONLY事务内设置statement_timeout=10s、lock_timeout=1s，并SHOW确认只读/隔离。业务schema校验、原语料内部SELECT、预算查询及055摘要都复用该连接。前后读取事务快照标识并要求一致，最终ROLLBACK/close。流程有必要内部schema/目录SELECT和会话核验，**不声称全程仅四条SQL**。
- 仅读取053原scope/profile的完整有效语料一次，并在内存调用原 `_select(limit=200)`；返回语料的评论/观点数必须为1699/3591，scope/profile必须一致。变化即记录安全计数或错误码并停止，不截断、补造或换范围。未保留历史全体key，不能声称相同总数就证明从未发生内容变化；本次完整语料摘要和当前key/整评论快照保存为后续重验依据。
- 选中key的派生行按原 `_cached` 条件读取，不增加商品过滤来隐藏全局唯一键冲突；校验实际商品、source/dataset、标注/修订/观点ID、区间、输入/内容身份等。跨商品或其他所有者不匹配立即停止，尚未完成的I/055字段为null。U>0但身份合法时只记录后续只读预算，最终阻断进入写入授权，不自动复用或更新。
- I查询使用原 `_range(corpus)` 加原profile/space/active/NOT ANY条件，传入**全部**当前valid_keys，不添加builder过滤，只SELECT必要行ID/元数据。I>0如实记录并阻断，不执行失效化。
- 055仅访问相同冻结日期、source/dataset、profile/space/builder的派生表行，不调用055语料/评论接口。库内对每行 `to_jsonb(整行)` 计算SHA-256，再按主键顺序拼接固定长度行摘要并计算集合摘要，覆盖向量及全部逻辑行字段；不排除invalidated行，不将向量坐标返回客户端。COUNT不为200时只记录异常并阻断。
- 入口仅允许SELECT/WITH/SHOW/SET/BEGIN，拒绝DML、DDL及 `<=>`；另由数据库只读事务兜底。没有迁移、同步函数、模型构造或编码调用。没有更改生产SQL、P0/P5、正式语义许可或原10个问题。

#### 输出和授权含义

终端只输出脱敏JSON：S/N/U/I/P、完整评论组数、跨商品唯一键冲突及所有者匹配标志、055 COUNT/完整行摘要、N+U=S、是否可进入写入授权，以及必要执行/清理状态和安全错误码。失败时未取得的字段保留null，不以0代替；不输出key、row/review ID清单、正文、quote、地址、DSN、凭据或向量坐标。

私有 `plan.json` 保存完整valid_keys/评论状态、选中完整组及去正文的观点身份、已有/失效化行清单、快照和语料摘要、预算及055基线算法；`summary.json`保存同一脱敏汇总，并绑定该私有文件摘要。原文和quote只在受检读取内存中使用，不序列化；055只有库内COUNT/摘要，没有取回坐标。文件均在Git忽略目录，不创建新审批或语义契约系统。

N是所有待编码输入有效、整批发布成功条件下的预计新增数；U是已存在的选中唯一key数量，P=S−U。若U非零，未额外读取/验证缓存向量，不能宣称缓存可直接使用；该情形只报异常预算。`can_enter_write_authorization=true`仅在源规模/身份一致、0<S≤200、U=I=0、无冲突、055 COUNT=200、N+U=S且清理完成时成立，表示预算可供下一步审核，**不是已经取得写入权或现有CLI已经可安全写入**。`write_authorized`始终false，`cli_safety_fixes_required`始终true。

当前实际预算登记：S/N/U/I/P、选中组数、跨商品冲突结论、055集合摘要均**待手动执行**；此前D01的055 COUNT=200不是新的完整行保护摘要。不得用离线测试的S=200等虚构数值填写真实结果。

#### 现有CLI两项阻断的最小修复建议（本轮未实施）

1. **精确预算门控。** 复用现有sync入口与函数，不复制同步器。受控路径默认dry-run，显式执行须绑定已核对私有清单及明确写入授权。选择后比对S、key/完整组、scope/profile/space/builder和完整语料摘要；在 `_publish` 既有向量表/源表锁内、任何向量DML之前，重新核算全局key所有者、N/U/I及影响行清单，与批准预算不符即退出。不能只在模型加载前检查I，也不能靠 `limit=200` 代替预算。保留原来源重读、空间和原文核验，不改变正式许可。
2. **整批失败关闭。** 原接口已在多数进程错误、错误空间、返回数量不符及非法向量时失败，但对None会计数后跳过，造成部分发布。仅补充发布前检查：必须恰好返回全部P条、无None/单项失败，每条通过既有 `validate_vector`，最终向量key集合与原选中S条完全相等且覆盖完整评论组；否则整批抛错，禁止进入 `_publish`。不截断超长quote、不拆分、不补选、不自动重试。独立sync日志可能保留failed状态，向量发布事务不得部分提交。

未来精确写入授权需明确本次实际N/U/I、同步记录1行及其状态更新、原表锁范围、055提交后同算法核对和前镜像回退；本地编码另授权P条以内、原模型/缓存、一次有限批次。实际编码/源变化失败不得扩大额度或重试。两项最小安全改动及必要离线验证完成、可供审核后，才有具体执行方案；本轮只提出，不实现或运行写路径。

#### 本轮离线验证与停止

新专项11/11通过：默认零连接、同一快照/只读门槛、源计数与身份漂移停止、全局跨商品key冲突、U/I及055数量异常、全部valid_keys失效化谓词、完整行摘要SQL、私有清单与终端脱敏、一次性保护及回滚/关闭（未确认close则禁止进入写入授权）。所有使用的评论、key和预算为合成数据；PostgreSQL、SQLite及socket连接均阻断。既有D01脚本10项离线回归通过，合计21项，不重跑D01或任何MVP案例。

本轮没有实际预算回执，不能提交精确N/I写入授权数值。交付本机命令后停止，等待用户手动执行并提供私有汇总；不自动连接、同步、编码或复测。MVP保持 **BLOCKED**。

### 10.13 手动只读预算实绩与053最终同步安全门控

本节登记用户在普通Windows PowerShell完成的真实只读预检，并记录随后获准的本地代码修改及离线测试。第10.12节仍是执行前准备记录，未覆盖；此前连接失败、D01及首次10场景验收历史亦未改写。**MVP总状态仍为BLOCKED；本轮交付状态为READY_FOR_WRITE_AUTHORIZATION，不代表已获写入或编码授权。**

#### 已保存回执核对

只读检查 Git忽略目录 `outputs/rag-semantic-retrieval/mvp-053-sync-budget-v1/` 内的 `summary.json`、`plan.json`，结果与用户提供的实绩一致：

| 项目 | 真实只读结果 |
| --- | --- |
| S / N / U / I / P | **200 / 200 / 0 / 0 / 200**；N+U=S |
| 完整选中评论组 | **114**，逐组观点数与完整评论状态一致，选中key去重后200 |
| 完整有效语料 | 1699条评论状态、3591个去重有效观点key |
| 跨商品唯一key冲突 | false；owner_identity_matches=true；已有选中行及失效化清单均为空 |
| 055保护基线 | COUNT=200，完整行值集合SHA-256见下 |
| 执行及清理 | 1次连接、零自动重试、19条语句、636.232ms；只读保护确认、ROLLBACK及关闭连接均成功 |
| 授权含义 | can_enter_write_authorization=true，write_authorized=false；预检没有编码或写入 |

核验的文件及身份摘要：

- `plan.json` 文件SHA-256与summary绑定值均为 `cc637bb5bf638c6a2883b66c99b28203c8abc952fd9baef42c2140906376ee71`。
- `summary.json` 本地文件SHA-256为 `b0c99f862abe0d344238caa8e7e08b5363f2e24171f960fc2d998ec3e299dbdb`。
- 055完整行集合SHA-256为 `ed56e2419a43a60975cee5dace107523d8aeb67160bfe5b1111e494d44b44cc1`。
- 完整053语料摘要为 `516c84eb6afe2793e52d00d455d87117464e72d43a65793c1ae8a7a522b46814`。本轮核对的是保存的摘要和清单，没有重新读取真实数据库来宣称当前仍未漂移。

固定身份保持source=reviews_csv、dataset_version=reviews-csv、商品100013588053、2021-09-11至2022-08-26完整UTC日期窗口、as_of=null、原标注profile、原1024维BGE-M3 space及quote-utf8-v1。离线配置检查确认原`.env`/数据库目标、fixture摘要、profile及未改动的来源拥有者代码摘要相符，没有输出地址或凭据。没有改写私有plan、summary或原只读入口的历史摘要。

#### 实际代码修改

| 文件 | 最小修改及用途 |
| --- | --- |
| `src/data_analysis_agent/reviews/embedding_budget.py`（新增） | 固定053预算校验器，绑定不可变plan原始字节摘要、scope/profile/space/builder、完整组/key/源语料摘要及055基线；只提供本地校验和受限查询，不签发P0或语义许可 |
| `src/data_analysis_agent/reviews/embedding_store.py` | 复用原sync、选择器及发布事务，新增默认dry-run、两个显式执行条件、编码整批检查和锁内预算核验；受控053发布只INSERT，禁止UPSERT更新/失效化 |
| `scripts/sync_opinion_embeddings.py` | 沿用现有CLI，不新增同步器；`sync`默认dry-run，实际执行同时要求`--execute`及`--authorize-local-encoding`，053自动加载唯一冻结plan并核对项目解释器/原本地模型路径 |
| `tests/test_embedding_sync_budget.py`（新增） | 14项合成专项，覆盖真实预算门槛对应的允许及拒绝分支、事务替身和CLI安全边界 |
| `tests/test_embedding_store.py`、`scripts/verify_pgvector_storage.py` | 既有隔离测试/验证器调用显式传入新的执行及编码参数，保持其原意；本轮未执行真实PG测试或该验证器 |
| `tests/test_rag_mvp_sync_preflight.py` | 仅为临时合成工程副本更新测试用owner摘要；不修改已消费的真实预检入口冻结值或私有plan |

原`_select(limit=200)`按review_id完整分组、容量不足跳过整组的确定性规则保留；没有按验收答案重新选样。原来源读取、quote切片核验、快照重验、检索SQL及P0/P5规则未修改。其他商品的旧同步行为只增加显式执行许可要求；既有非受控路径的缓存/部分覆盖语义保留，没有将其自动投入本次053同步。

#### 精确预算门控

1. **默认无执行。** sync默认返回dry_run，说明尚未重验在线预算，不读取真实语料、不写sync记录、不构造编码作业。直接调用053裸sync而不提供受检预算会被拒绝。两个CLI参数仅表达将来操作者已获许可，本轮没有使用它们执行真实操作。
2. **编码前重新验证。** 将来获准执行时，先重读053受检完整语料，校验完整1699/3591状态、整体摘要、选中200个具体文档身份、114个完整评论组及快照。不得补选、换范围或只比总数。另用只读连接核对已存在唯一key行、原失效化谓词及055摘要；确认关闭该事务后才进入编码。N/U/I/P必须与200/0/0/200一致。
3. **锁内、向量DML之前重验。** 保留原向量表`SHARE ROW EXCLUSIVE`及7张源表`EXCLUSIVE NOWAIT`锁，lock_timeout=3s、statement_timeout=15s。在新READ COMMITTED快照重读原语料并要求与编码前完全相等，再比固定plan。对选中key全局查询，不加商品过滤隐藏冲突；任何已有行都违反U=0，错误商品所有者亦明确阻断。I查询严格复用原范围/profile/space/active/NOT ANY全部3591有效key条件，**不加builder过滤**；必须为空。重新计算S/N/U/I/P并记录安全计数；变更集合只允许固定200个新增key，更新与失效化行清单必须仍为空。
4. **只新增批准行。** 受控发布不运行原向量UPDATE及UPSERT更新分支。INSERT使用DO NOTHING RETURNING id，若任一冲突未插入就抛错，事务中此前新增行一起回滚，不能静默少写或覆盖055。成功提交后才向私有回执提供本批新增行ID/key，用于未来精确对账和另行授权的回退；终端隐藏该清单，原sync日志只保留计数等指标。
5. **055完整行保护。** 编码前、发布锁内DML前及同锁内提交前，均使用已冻结的同一COUNT/整行SHA算法核对055。库内摘要包含向量和全部行字段，不取回055正文或向量坐标。提交前任何差异都会回滚本次向量事务。该代码检查不等于本轮已做真实提交后核验；未来回执核验及必要提交后只读对账仍应包含在实际执行授权范围。

表锁覆盖整表，可能短时阻塞055等其他商品写入；数据保护不等于没有锁竞争。若语料、来源实现、配置、锁内预算或055基线变化，立即停止，不接受新摘要、不更换观点、不自动调整预算。原只读预检脚本绑定旧同步代码摘要，本轮修改后不应解冻其保护重跑；已保存的真实预算仍完整保留，受控同步按上述锁内重验门控执行。

#### 整批失败关闭及回退边界

受控路径固定U=0，不读取缓存向量，P=200≤200；一次调用原本地编码进程。输出必须为正确space、恰好200条，且每条通过原1024维、有限数值、非零和归一化校验；任何None（包括超过512 tokens的标记）、模型错误、数量不符、非法向量或错误空间都会在发布前使整批失败。发布入口再次检查向量key集合与选中key集合完全相等。没有截断quote、跳过失败项、拆组、重试或补选。

正常发布中途异常会由原连接事务上下文ROLLBACK并关闭连接；源表不写。独立的`rag_embedding_sync_runs`仍可能新增1条并更新为failed/retry_required，序列号也可能消耗，不能说所有数据库变化都回滚。retry_required是原记录状态，**不触发自动重试**。COMMIT回执丢失时结果可能不确定，CLI保留一次性目录及failed_or_commit_unconfirmed状态，先停下对账，不以异常或published_count=0证明库内绝无已提交行。

053显式执行会原子占用Git忽略目录 `outputs/rag-semantic-retrieval/mvp-053-sync-execution-v1/`，已有目录即拒绝，不提供force/换目录重跑；失败不删除目录。本轮未创建真实执行目录。实际新增行成功回执留在该目录，主键/完整key不提交Git。提交后回退仍需单独写入许可，仅按本run确切新增行及其当前身份核对后处理；不删整个053、不改055、不恢复整库。U=I=0意味着本预算无需既有向量前镜像，但不能据此跳过发布前重验。

#### 离线验证结果

| 专项或回归 | 结果 |
| --- | --- |
| 新`test_embedding_sync_budget` | **14/14** |
| 原`test_embedding_store.VectorValidationTests` | 3/3 |
| 只读预算入口专项 | 11/11 |
| D01入口离线专项 | 10/10 |
| `test_semantic_retrieval` | 28/28 |
| `test_review_semantic_integration` | 37/37 |
| `test_rag_corpus` | 21/21 |
| 合计 | **124/124**，最终一次运行33.149秒 |

新专项验证：匹配预算允许进入模拟发布；编码前及锁内U/I漂移、跨商品key冲突、055完整行漂移阻断；源语料/选中key/输入/完整组漂移阻断；单条None或513-token超长标记、模型异常、非法维度/范数/NaN、数量增减、错误space及错误响应均整批失败；模拟第100条INSERT失败、冲突、提交前055变化及提交失败均不形成模拟部分提交。默认dry-run及缺少编码许可时零连接；固定plan/配置/fixture/来源实现漂移及裸053调用阻断；私有回执与终端脱敏、一次性目录限制通过。

测试使用虚构语料、FakeEncoder及事务替身；测试运行器阻断真实psycopg连接和socket，必要旧回归仅使用临时合成SQLite。**没有真实PostgreSQL连接/写入、本地BGE观点编码、向量检索、外部模型或10案例复测。** PostgreSQL真实事务/锁/提交兼容性未在本轮验证，不能把替身结果写成真实同步成功。正式Agent、reported_experience_v2、B1/B2、P0/P5、原10问题和人工评审材料均未因本轮改动而改变。

#### 下一步需要的两项独立授权

- **053数据库同步写入：** 对上述唯一plan及当前语料重验，最多新增200行`review_opinion_embeddings`，U=I=0；新增1条sync记录及必要状态更新，允许原有限锁、来源重读及055派生表聚合保护核验。任一漂移即停止，零自动重试，不自动回退或复测。
- **本地观点编码：** 仅该固定200观点，一次本地BGE-M3作业、原修订/1024维/quote-utf8-v1/CPU参数与本地缓存，不下载、不外发；任一失败停止整批，不扩大到其余3391观点。可预留原报告说明的约2–3GiB及数十秒至数分钟，非资源保证。

未来即使200条整批成功，原status=partial仍指完整3591观点未全部覆盖，不能解释为允许跳过本批失败项，也不能宣称全量索引或MVP通过。真实执行后的覆盖、055保护与原10场景验收须按各自授权据实记录。当前`publication_allowed=false`、`semantic_verified=false`及正式许可边界不变。本轮完成安全门控与离线验证后停止，等待用户分别授权实际写入及本地编码。

### 10.14 053真实同步完成及固定10场景复测入口准备

本节更新当前事实：**用户已在普通Windows PowerShell完成053首批200观点同步；本轮只核对其私有回执并准备复测，未执行真实复测。MVP仍为BLOCKED，复测准备用途状态为READY_FOR_RETEST_AUTHORIZATION。** 第9节首次真实验收、第10.7–10.13节连接诊断、预算及授权准备历史原样保留；此前的“未同步/待授权”是相应历史时点，未改记为成功。当前已执行真实MVP验收仍为1次，修复后的真实复测次数仍为0。

#### 已核对的真实同步结果

读取 `outputs/rag-semantic-retrieval/mvp-053-sync-execution-v1/result.json`，文件SHA-256为 `279431baf8cc0eadeafb4566e033021004d855c1d9b0a9e9b5d84db432603e99`，与本节冻结值一致。

| 项目 | 真实回执 |
| --- | --- |
| run_id / status | 4 / partial |
| selected / attempted / successful / published | 200 / 200 / 200 / 200 |
| reused / failed / overlength / invalidated | 0 / 0 / 0 / 0 |
| locked_budget | S=200、N=200、U=0、I=0、P=200 |
| indexed_current_after / not_indexed_count | 200 / **3391** |
| published_rows | 200个去重行ID、200个去重key；key集合与冻结plan的200个选中观点完全相同，覆盖原114个完整评论组 |
| 总耗时 / 发布耗时 | 39.776秒 / 0.902秒 |
| 本地模型加载 / 编码 | 8.153秒 / 26.334秒；CPU、batch=8、4线程；峰值约1.72GiB；出站连接尝试0 |

退出码0由用户提供。按现有CLI实现，完整成功回执在发布事务COMMIT返回后保存；本轮没有新建数据库连接来独立重读run 4的持久化行。status=partial只表示完整3591观点语料仍未全量索引，不能理解成本批只发布了一部分。

用户另行报告同步后独立只读核验055通过：COUNT=200，完整行集合SHA-256仍为 `ed56e2419a43a60975cee5dace107523d8aeb67160bfe5b1111e494d44b44cc1`。该结论的证据来源是用户提供的独立核验结果；当前同步目录只有result.json，未将其缺少的055提交后摘要字段或退出码字段虚构为已落盘回执，也未在本轮重新查询055。

因此，原053“完全没有冻结范围向量”的覆盖阻断已经完成首批修复；**不能由同步成功推定MVP通过、语义质量足够或已覆盖全部评论**。后续所有回答仍须通过真实P0、正式v2许可及原10题实际语义验收。

#### 最小复测入口及真实执行链路

新增 `scripts/run_rag_mvp_retest.py` 和离线专项 `tests/test_rag_mvp_retest.py`。不改正式Agent、API、Worker、SQL、P0/P5、许可规则、向量、人工记录或冻结fixture。

为避免另写一套执行编排，入口校验并复用首次私有执行脚本 `outputs/rag-semantic-retrieval/mvp-first-20261107-v1/run_once.py` 的原执行函数，其文件SHA-256固定为 `5d32350d2062d9ec43905229fb3234faa3ac95e04762eaba4e359869027925ae`。缺少该历史脚本或摘要变化即停止，不加载其他实现或用Mock代替。只有显式执行且两项授权参数齐备时，才编译并加载该受检文件，随后将回执目录改到新目录；原文件和旧结果不写入。仅本地conversation/idempotency标识更新为新批次，问题、范围、顺序和M10上下文连接方式不变。

真实执行仍是原 `create_app` → 进程内ASGI POST任务 → 独立SQLite队列 → 原`claim_task` → `TaskWorker._execute` → 正式Agent → SQL范围摘要 → 本地BGE-M3查询编码 → PostgreSQL/pgvector → 实时P0核验 → reported_experience_v2 → 原确定性回答 → GET任务详情。不是浏览器实测或新的网络HTTP服务器，也不启动/接管现有常驻Worker。

运行时的计数/只读/故障注入通过首次脚本原有方法包装记录；实际调用的仓库、编码器、P0及回答函数保持真实实现，不使用FakeRepository/FakeEncoder。唯一故障仍为冻结M10C的编码前服务构造故障。保留rules_only、模型重试0、回答重试0及外部LLM/HTTP调用阻断，不发送真实评论到第三方。

#### 冻结条件及一次性门控

- 固定使用原fixture及规范化摘要 `a50c41143fe142e4a0a7c3e83afc9fb620724124cdf44d874912f09f06782c29`，M01–M09主商品053，M10原A/B/C/D四步，合计13个动作。Top-K=5，原source/dataset、UTC日期、baseline兼容字段、annotation profile、space/builder、任务契约及业务验收规则全部保留。
- 默认只做文件、配置、身份和路径检查，零业务连接、零编码、零任务、不创建复测目录。校验原`.env`、首次保护文件摘要、私有plan及run 4回执/精确key集合、原标注profile、本地解释器/模型缓存路径。没有继承旧执行额度；显式执行必须分别具备本次只读验收及本地查询编码授权。
- 新独立私有目录固定为 `outputs/rag-semantic-retrieval/mvp-retest-053-v1/`，Git忽略。原子创建，目录存在即拒绝，无force或换目录参数；失败保留attempt.json、execution.claim及已有回执，不删除、不自动继续。
- 原执行函数在实际调用前限制最多13个Agent任务、11次查询编码、11次pgvector检索，每个固定动作最多一次编码/检索。新入口禁止优化模式，确保原冻结脚本的assert门槛生效；采集后再次核对额度。任何失败不自动重试、不重新启动已失败编码进程、不补题或换范围。
- 所有业务PostgreSQL连接强制只读，拒绝DML/DDL及批量写入；保留必要SELECT、事务和连接元数据检查。只读指业务库；独立本地SQLite队列和私有结果文件必须正常写入。内部来源和范围检查有多条SELECT，不声称整个批次只有11条SQL或11个数据库连接。
- 执行结束沿用原资源回收，记录编码进程退出、共享缓存清空、业务连接关闭、只读状态及保护文件未变。已有同进程共享编码器时拒绝接管。资源或批次未完整结束则BLOCKED，不自动修复/重跑。

#### 回执、语义复核及最终计分

每个M01–M09及M10A–D保存独立JSON；receipt.json保存原执行总回执，summary.json保存安全汇总。原始候选rank、距离、来源/标注/完整观点快照、原quote、Unicode code point绝对区间、全文上下文、正式许可/拒绝理由及实际答案引用保留在私有回执。终端仅沿用原脱敏执行汇总，不输出评论正文、quote或凭据。

新入口离线回读这些实际回执，检查正式v2传输/许可规则、实时P0已验证记录、候选与回答引用的精确绑定、原文切片、scope、来源文件身份及M10无编码降级。回读不签发或重造P0证明。引用集合必须与permitted_keys一致，不能截断许可quote、引用其他候选、跨商品或借用全文补事实。

`semantic-review.json`是**本次10题实际验收分析的最小旁栏**，指向各题完整候选上下文，保留正式候选判断与许可状态；不是新的P5评审、27行金标准或Judge系统。事实是否被原文直接支持、固定Top-K是否存在充分证据、是否有幻觉/范围越界、是否属于正式能力缺口及最终pass/reason均先为null，待真实回执产生后结合实际全文分析。不能用结构合法、候选数量或引用唯一定位自动填为正确。

M04/M06/M07/M09继续retrieval_only，不授予新许可。若固定候选确有充分证据但正式规则无法支持，应记FAIL及能力缺口；不能将此类拒答一概算作安全降级PASS。缺少充分证据才可按原规则记证据不足，且不外推全库不存在。M10四步全部符合才算该1个案例通过，不能把13个动作当成13个验收案例。

采集完成且结构检查无错误时，入口只标记PENDING_SEMANTIC_REVIEW、实际通过题数和经全文核对的053支持回答数均为null；结构/执行错误标记BLOCKED。退出码0表示离线预检通过或回执采集完成，**不是MVP已通过**。最终仍要求原10题至少8项符合预期、053至少1个真正受支持的RAG回答、零伪造/跨商品引用、无阻断性SQL回归及安全降级。不能把全部拒答或055支持计成053业务能力。即使最终通过，也必须注明053仍有3391个有效观点未索引，不宣称全量RAG覆盖。

#### Windows PowerShell命令及分别授权范围

仅本地离线预检，不连接数据库或占用批次：

```powershell
Set-Location 'E:\AllProject\data-analysis-agent'
& '.\.venv\Scripts\python.exe' -B '.\scripts\run_rag_mvp_retest.py'
```

用户分别授权以下A/B后，由用户在普通Windows PowerShell手动执行一次：

```powershell
Set-Location 'E:\AllProject\data-analysis-agent'
& '.\.venv\Scripts\python.exe' -B '.\scripts\run_rag_mvp_retest.py' `
    --execute-once `
    --authorize-postgres-readonly-acceptance `
    --authorize-local-query-encoding
$retestExitCode = $LASTEXITCODE
Write-Output "retest_exit_code=$retestExitCode"
```

- **授权A：本次真实PostgreSQL只读验收。** 仅原冻结053/055产品、source/dataset、日期/profile范围的API/Worker/Agent业务读取、原当前有效语料/全文来源核验、SQL范围摘要及最多11次Top-K=5 pgvector检索；最多13个固定任务，允许独立本地队列及私有回执写入。业务库不写、不迁移、不同步、不重标注、不补向量，不扩大问题或范围。
- **授权B：本次本地查询编码。** 仅上述冻结动作实际需要的最多11次查询编码，原本地BGE-M3/1024维空间、缓存及已有进程实现；不重新编码任何观点，不下载模型、不调用外部LLM/Embedding、不发送真实评论到第三方、外部API费用0。零自动重试；耗时、超时或服务异常据实保留，不能补跑。

失败或结果不完整时保留新目录，不重新运行入口、原首次脚本、同步器、D01或预算预检来补结果；先分析已有私有回执。无需发布参考、P5人工裁决或新的开发阶段。

#### 本轮离线结果与停止

新专项13/13通过，覆盖默认零连接/零任务、不齐全授权拒绝、冻结runner/fixture/同步清单/配置漂移阻断、一次性目录与失败保留、额度超限和资源回收异常拒绝、Unicode切片/来源/许可错误、retrieval_only不升级、M10B/C零编码与无答案、结构合法仍保留语义待核对及模拟采集不自动COMPLETE。合成事务/运行器只用于离线测试，不作为真实复测结果。

适用既有回归：semantic_retrieval 28/28、review_semantic_integration 37/37、rag_corpus 21/21；合计 **99/99**，最终一轮9.115秒。运行器阻断真实psycopg/socket；必要回归只用临时合成SQLite及替身。另在真实本地配置上、强制阻断PostgreSQL/SQLite连接、BGE和Worker执行的条件下，默认入口返回READY_FOR_RETEST_AUTHORIZATION，任务/编码/检索均为0且未创建真实复测目录。入口还以本地git check-ignore检查新私有目录确实被忽略；授权参数的整条命令已只解析核对，没有进入执行分支。

只读解码旧首次回执检查了新回读器的字段兼容性，未重新检索或运行旧案例。新独立复测尚未发生，没有填写任何真实新案例结果，没有更新首次结果，也未把离线测试计入10题通过数。当前publication_allowed=false、semantic_verified=false，P5新增正式许可为空。本轮完成准备后停止，等待用户分别授权A/B并在本机手动执行。

### 10.15 固定真实复测的最终离线语义裁决

**当前最终结论：BLOCKED / 冻结MVP验收失败。10案例确定PASS 5项、FAIL 3项、UNDETERMINED 2项；053真实发布且受原文支持的回答为0。** 两项未定即使全部计PASS也最多7项，仍低于8项门槛；主商品支持回答门槛亦独立失败。因此本轮全局结论已确定，不依赖再开展人工评审或新实验。不是RAG MVP Complete。

本节只分析 `outputs/rag-semantic-retrieval/mvp-retest-053-v1/` 的实际既存回执、冻结fixture及必要正式许可/回答代码。用户本机已执行的复测于2026-11-07 14:53:53至14:54:18 UTC完成，耗时25.329秒；13个固定动作、11次查询编码、11次pgvector检索，自动重试0、外部模型调用0、业务写入尝试0。15个业务连接只读并已关闭，编码进程已回收，保护文件未变；12个任务succeeded、M10C按预期failed。程序退出码0由用户提供，不等于语义验收通过。

本轮新增真实数据库连接、BGE编码、pgvector检索、Agent任务或模型调用均为0；没有重新同步、改规则、改SQL、改问题或扩大候选。第10.14节的待执行描述保留为准备时点；此次是修复后的第1次真实复测，第9节首次结果保持原样。

#### 文件、来源与引用核对

- 实际存在receipt.json、summary.json、semantic-review.json、13个Mxx动作JSON、原preflight/claim/attempt及本地队列文件。未假设不存在的字段；summary仍保留执行时的PENDING_SEMANTIC_REVIEW / NOT_YET_ADJUDICATED，不回写执行历史。
- receipt.json的SHA-256为 `a563badcfd487b53339d6e0427aa08021aafff9c2182bd54925428b292c0fb3d`；summary.json为 `44a5f8a58e0be5cb3465f7065aa681a5287c14248fe9c972483badcd17a76e0e`。13个动作文件逐项与receipt中相应对象一致。既有21个非裁决回执/队列文件保持字节摘要不变，仅补全私有semantic-review.json及本报告。
- 053九题各5条，合计45次候选、去重34条；055两个正常动作各5条，合计10次候选、去重5条。55/55候选全文摘要、quote原文切片、Unicode code point绝对区间、商品/数据集/profile、日期及完整评论快照字段匹配。11个实际检索动作保存的实时P0 validated均为true；M10B/C未检索，不能混记为P0失败。
- 055实际发布4次引用、去重2条，均来自同动作许可候选；来源文件引用、观点/标注身份、原文及绝对区间4/4匹配，全文没有否定、撤回或对象变更使对应正向陈述失效。未发现伪造引用、跨商品/跨数据集引用或回答中的无依据统计外推。
- 11份既存assessment用原纯规则函数离线重算后完全一致；这是回执原因复核，不是再次执行检索或Agent，不生成P0 seal，也不构成人工四状态金标准。

#### 原10案例逐项裁决

以下引用的rank与位置均指新复测目录下相应Mxx.json的 `#/retrieval/evidence/索引`，索引=rank−1；quote及全文只保留在私有文件。本表不复制真实评论或私有身份。

| 案例 | 候选数 | 固定候选内直接充分证据 | 正式许可及拒绝原因 | 引用数 | 裁决 | 原因及证据位置 | 需本人复核的歧义 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| M01 配送很快 | 5 | 有：rank1、3 | supported契约；许可0；no_verified_direct_report，5条均semantic_not_verified | 0 | **FAIL，B** | 两条quote自身足证所需配送方向；全文安装体验不撤回配送事实。完整表达未被词法规则提取，诚实拒答仍不满足有足证应回答的验收要求。M01.json rank1/3 | 无影响结论的歧义 |
| M02 配送很慢 | 5 | 无 | supported契约；许可0；no_verified_direct_report | 0 | **PASS，A** | 正向/中性配送、联络问题、错误完成标记和安装速度不能补成配送延误。尤其rank2没有延误时间或明显慢的陈述，不能由状态冲突独立推定长期未收货。M02.json rank1–5 | 不把未明示的延误补成事实 |
| M03 客服态度差 | 5 | **UNDETERMINED** | supported契约；许可0；no_verified_direct_report | 0 | **UNDETERMINED，A/B未决** | rank1/2是客服服务质量概述，rank3是明确客服角色的负面响应行为；是否已充分满足本题“负面态度”边界有歧义。rank4对象未知，rank5不能直接推出态度。M03.json rank1–5，重点rank3 | 服务响应/跟进缺失是否满足已冻结态度目标；不自动等价，不新增语义要求 |
| M04 到货后安装等待 | 5 | 有：rank1、2、4、5 | retrieval_only；许可0；query_semantics_uncovered | 0 | **FAIL，B** | quote内具备到货、经过数日及持续未安排/未完成安装；上下文无撤回。rank3则缺到货前提，不用它补足目标。正式关系/目标覆盖不足造成漏答。M04.json对应rank | 无影响结论的歧义 |
| M05 制冷效果好 | 5 | 有：rank3、5 | supported契约；许可0；no_verified_direct_report | 0 | **FAIL，B** | 两条quote自身有明确效果正向陈述，全文未否定。rank5仅被规则识别出速度事实，遗漏独立效果事实；不靠速度推出效果。M05.json rank3/5 | rank1标点含义未定，但裁决不依赖它 |
| M06 长时间运行仍不凉 | 5 | 无完整条件 | retrieval_only；许可0；query_semantics_uncovered | 0 | **PASS，A** | 快速降温、安装/客服、泛制冷不足和初期故障均不能替代“长时间持续运行后仍不降温”；购买/使用期限不能当作一次运行时长。M06.json rank1–5 | 无需补充不存在的运行条件 |
| M07 使用不久发生故障 | 5 | **UNDETERMINED** | retrieval_only；许可0；query_semantics_uncovered | 0 | **UNDETERMINED，A/B未决** | rank1–4为服务/安装问题；rank5有初期少量使用后的异常症状，但是否足以归入冻结的主机故障边界，还是安装相关问题，文本不能无歧义确定。M07.json rank5 | 不能擅断主机故障，也不能新增“内部原因已证实”的要求；保留该边界未定 |
| M08 同评论制冷好且噪音大 | 5 | 无完整双事实 | supported契约；许可0；no_verified_direct_report；same_review未满足 | 0 | **PASS，A** | 正向制冷候选全文描述低噪音；高噪音候选没有制冷正向事实。rank5识别出noise_high只是部分事实，不等于完整问题获得许可，不能跨评论拼接。M08.json rank1–5 | 无 |
| M09 十年使用仍耐用 | 5 | 无十年依据 | retrieval_only；许可0；query_semantics_uncovered | 0 | **PASS，A** | 安装/调试初期、普通体验或短期异常不能证明十年使用。不能将短期限、评论年份或产品品牌外推为十年耐用，也不外推全库不存在。M09.json rank1–5 | 无 |
| M10 四步范围/故障/恢复 | A/D各5；B/C各0 | A/D引用均有充分证据；B/C不适用 | A/D各许可2，explicit_report_only；B unsupported；C注入故障error | A/D各2；B/C各0 | **PASS** | A/D的rank1/2全文支持其许可事实，其他方向/对象候选未发布；B编码前拒绝多商品；C无答案/引用；D恢复后仅055引用，无053或故障残留。M10A–D.json | 无 |

M10的A/B/C/D只合算1个案例；055成功引用不计入053支持回答。M02/M06/M08/M09的PASS只证明本次固定候选不足时的降级行为符合标准，不证明相应业务目标已被系统普遍理解。M03/M07不猜PASS；它们即使都改判PASS，也不能改变本轮全局失败。

M06中存在的初期故障材料、M09中的短期异常，均不搬到M07作为其Top-5证据；同理不跨题补足M08双事实。全文只解释或否决quote，未用未引用全文增加正向支持事实。

#### 053零引用的原因分类与代码定位

| 原因类别 | 确定对应案例 | 结论 |
| --- | --- | --- |
| A：固定Top-5没有直接完整足证 | M02、M06、M08、M09 | 4题可接受证据不足；不由此断言全库无相关评论或Embedding模型有故障 |
| B：有足证而v2未许可 | M01、M04、M05 | 3题明确业务漏答，是本轮已证实的语义能力阻断 |
| C：已有许可但回答/引用传递丢失 | 无 | 053九题permitted_keys均为空且最终refs均为空，没有可定位的引用传递丢失 |
| D：其他已证实的053零引用故障 | 无 | 未发现另一个有证据支持的来源、SQL或发布故障；M10B/C是冻结的预期保护，不当作053异常 |
| A/B不能确定 | M03、M07 | 2题保留UNDETERMINED，不强行塞入A、B或D |

直接机制是053九题没有整题许可，回答层按许可集输出零引用；不能把所有零引用都当成同一种错误。已证实的漏答集中在以下位置：

- `src/data_analysis_agent/agents/review_reported_facts.py:10`、`:41`、`:66`：有限词法模式、完整子句匹配及quote事实提取。M01 rank1/3、M05 rank3/5的目标事实未被识别；保存的explicit_fact_ids及离线重算一致，拒绝发生在目标事实识别层，不能未经核验归因于上下文否决。
- 同文件`:54`、`:98`：M04目标及必要条件未被现有目标语法表达，返回query_semantics_uncovered；这是正式有限能力缺口，不能把尚未确认的P5关系映射升级进来。
- `src/data_analysis_agent/agents/review_retrieval.py:212`、`review_answers.py:390`：引用从permitted_keys构造，回答绑定要求与许可集相同。M10A/D可发布，053许可为空时零引用符合该接口契约。没有发现“已获许可但少传字段/少发布引用”的最小运行时缺陷。

词法识别缺口可以准确定位，但它不是一处连接、坐标或引用传递故障；现有闭合语法本就有限。仅定位当前首次拒绝阶段，也不能据此担保修改一条模式后能通过后续全文校验及全部关系目标。本轮未试改或试跑任何规则，不能把未验证的修复预期写成结论。

#### 全局门槛、私有裁决与停止

已补全私有semantic-review.json的13动作裁决和55次逐候选检查：保留实际rank、距离、quote/标签、上下文位置、原正式判断，新增独立语义理由及歧义；原正式candidate_judgments未改写。10案例结果为5 PASS / 3 FAIL / 2 UNDETERMINED，53支持回答0，M10四步均PASS但仅计1题。该文件明确标注Codex离线裁决，不冒充本人已确认的四状态金标准，formal_accuracy保持N/A语义，publication_allowed=false、semantic_verified=false，P5许可不变。

| 最终门槛 | 结果 |
| --- | --- |
| 真实PG/BGE/pgvector、P0与正式链路执行 | 既存回执证实通过，非本轮新连接 |
| 10题至少8项符合预期 | **FAIL：确定5项；即使两项未定全通过也最多7项** |
| 053至少1个真实发布且原文支持的回答 | **FAIL：0个** |
| 已发布引用合法、直接支持、无跨商品污染 | 055的4次已发布引用通过；不能替代053门槛 |
| 无结果/错误范围/故障安全降级 | 本次相应行为通过；有足证的漏答不混算为业务通过 |
| 阻断性SQL回归 | 未在本次正式统计/结构回执中发现；不扩大成全SQL功能验证 |
| 全量覆盖 | 不具备；已知同步快照仅200/3591观点，仍有3391未索引 |
| 总状态 | **BLOCKED / 冻结MVP验收失败** |

**唯一后续建议：按当前有限能力验收失败封存，保留这次回执和明确缺口，停止本轮RAG开发。** 当前证据不支持把全范围失败归结为一个可直接解除全部门槛的最小工程修复；不为追求通过再补样、扩向量、放宽许可或追加复测。本轮到此结束。
