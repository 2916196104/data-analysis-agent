# P7-C · RAG 真实浏览器端到端验收预检报告

**最终结论：BLOCKED。** 已完成只读预检、离线集成验证和具体执行方案；尚未获得本轮真实任务提交、本地Query编码与只读Top-5检索的明确授权，因此真实React→API→Worker→Agent→PostgreSQL→Validator→React尚未执行。新增测试通过不等于真实闭环通过。报告日期：2026-11-13（Asia/Taipei）。

仓库 `E:/AllProject/data-analysis-agent`，HEAD `a83962601c28fffe0146c8fd8e4e990de303098b`。起始Git干净；本轮只新增后端测试、前端测试/合成夹具与本报告，未修改生产代码、配置、权限、事实许可或旧测试。不实施P7-D、B4、主题模型、向量补全，也未执行历史真实验收。

## 1. 固定范围与真实调用

问题：**商品100013588055有没有送货慢的反馈？**

范围：`reviews_csv / reviews-csv / 100013588055`；UTC `2021-09-11`—`2022-08-26`。200/1091（约18.33%）仅沿用用户和055直接入口报告给出的基线，本轮未重新查询语料/向量覆盖数量。

| 实际操作 | 本轮次数 |
|---|---:|
| 真实业务任务提交 / Query Embedding / PG Top-5 | 各0 |
| 付费LLM / Embedding同步 / PG业务写入 / 执行重试 | 各0 |
| 本地模型加载与health预检 | 1（initialize + health，无texts、无encode） |

PG只读连接仅查询角色权限、会话和锁元数据并立即关闭，未进行真实业务检索。真实task_id、conversation_id、任务终态、编码耗时、Top-5、真实Validator/public_answer、真实浏览器展示和任务耗时均为 **NOT_RUN/null**，见[机器结果](../outputs/mvp-real-acceptance/p7c-rag-20261113T084141Z/acceptance.preflight.json)。真实截图为空；jsdom DOM和旧截图均不代替本轮Chrome证据。

## 2. 十项只读预检

| 检查 | 结果与证据 |
|---|---|
| React提交范围 | 固定问题原样POST，source/dataset/product/date、selection_scope和conversation_id保留；postLock及Idempotency-Key防重复。5项React专项实测 |
| API语义任务创建 | 原生/tasks接收analysis.question，不提前编码；重放同key复用任务、不同载荷409；原生TestClient+临时SQLite验证 |
| Worker RAG配置 | 默认.env已配置RAG_PYTHON和RAG_MODEL_CACHE；Worker按配置构建Agent，本地服务工厂默认路径未替换为新RAG实现 |
| 本地模型/生命周期/超时 | 固定BGE-M3修订8文件SHA齐全，torch 2.9.1+cpu、transformers 4.57.6等匹配；真实离线模型加载 **10.885秒**，health通过；600秒RPC上限，崩溃/超时锁定失败、不自动重启 |
| 冷启动与心跳/锁 | 独立5秒心跳；模型工作在子进程。合成慢编码期间，原生Worker持续心跳、任务保持running、第二个Worker无法取得锁；完成/停止后可重新取得锁 |
| PG连接/事务 | rag_audit_ro、read_only=on、非管理角色、连接上限8；6项目标表只有SELECT无写权限；无遗留角色连接或目标锁。Agent编码前rollback，编码后新建只读RR快照；来源认证在同一快照 |
| 隐式收费 | 固定问题在规则路由优先命中evidence，semantic_direct执行工具；semantic_evidence回答直接选模板。离线规划/收费SDK调用前阻断，未触发收费构造器。模型名称作为标注身份不等于模型调用 |
| 事实契约与Validator | 原生RuntimeQueryContract：fact_search / delivery_slow / 原问题与UTC范围；Validator实际运行含查询目标匹配。非法span、许可列表和来源绑定被拒绝；不伪造task_acceptance |
| 公开引用运输/渲染 | 原生任务落库后经HTTP JSON读取，public_answer与原生一致；React仅显示许可观点，绑定review/annotation/key/快照/文件/区间，高亮按Unicode字符切片；损坏来源公开API拒绝 |
| 完全离线缓存 | local_files_only=True、trust_remote_code=False、固定revision；子进程offline环境和socket.connect拒绝hook。health RPC拒绝encode/texts，真实加载通过且缓存摘要未变 |

代码定位：[src/data_analysis_agent/agents/review_intent.py:365](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_intent.py:365)；[src/data_analysis_agent/agents/reviews.py:262](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/reviews.py:262)；[src/data_analysis_agent/jobs/worker.py:151](E:/AllProject/data-analysis-agent/src/data_analysis_agent/jobs/worker.py:151)；[src/data_analysis_agent/reviews/embedding.py:56](E:/AllProject/data-analysis-agent/src/data_analysis_agent/reviews/embedding.py:56)；[src/data_analysis_agent/agents/review_validation.py:106](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_validation.py:106)；[frontend/src/features/agent/AnswerEvidence.tsx:5](E:/AllProject/data-analysis-agent/frontend/src/features/agent/AnswerEvidence.tsx:5)。

超时边界必须准确解释：600秒是编码RPC边界，**Worker没有单独的整任务墙钟deadline**；PG连接上限10秒，Agent SQL单语句不超过30秒。API的30秒HTTP超时不用于等待整个业务任务；React正常每1.5秒GET状态，读取失败后5秒再读状态，不会自动重新POST或重新执行编码/检索。真实阶段应增加独立观察期限，不能把HTTP超时或600秒当成任务总时限。

模型健康检查用生产PersistentEmbeddingProcess.close显式kill/wait，子进程退出码1；这是主动结束而非模型加载失败，PID `25360`、reader和对象句柄均已清理。没有声称该进程优雅退出0。

## 3. 离线集成与回归结果

| 检查 | 本轮结果 |
|---|---|
| P7-C新专项 | 11/11 PASS（backend-regression.log中也全部通过） |
| 后端扩展回归 | 229项，226通过、2失败、1错误；不宣称全绿 |
| 三项失败独立复核 | 同样2失败、1错误；生产代码和旧测试与HEAD摘要一致，未放宽/跳过断言 |
| React新专项 | 首次5/5；最终类型修正后再次5/5 |
| 前端全量 | 287/287 PASS，maxWorkers=2、原10秒超时不改 |
| 最终TypeScript/Vite构建 | PASS；保留原有大分块警告 |
| git diff --check | PASS |

P7-C专项覆盖：正常任务创建/原生状态/完整性摘要；重复提交及冲突；编码失败与超时且不检索；数据库错误后快照/连接释放；来源annotation不匹配；无候选/无许可；原生Validator拒绝非法区间和许可；公开API拒绝损坏来源；异常Worker恢复仅标记failed不自动重试；冷启动心跳/锁；React正文、精确引用、限制和失败/加载状态。扩展回归覆盖A1/C1确定性正负面回答、P7-A趋势、P7-B固定追问、RuntimeQueryContract、语义来源和进程超时/崩溃清理。

扩展集三项旧夹具失败均源于Amazon来源被现有CSV-only MVP边界提前拒绝，其中两项已在P7-B报告中记录，第三项为本轮扩大到task_submission后观测到的旧断言冲突：

- `test_p3b_worker_executes_two_tasks_using_default_service_factory (test_review_semantic_integration.SemanticAgentTests.test_p3b_worker_executes_two_tasks_using_default_service_factory)`
- `test_timeout_after_query_is_failed_and_diagnostics_preserved (test_review_plan.QueryPlanTests.test_timeout_after_query_is_failed_and_diagnostics_preserved)`
- `test_optional_selection_metadata_preserves_old_payload_and_checks_identity (test_task_submission.SubmissionTests.test_optional_selection_metadata_preserves_old_payload_and_checks_identity)`

没有修改这些旧测试或生产范围策略来使它们通过；它们是剩余回归问题，不能从报告中删除。

全部离线业务数据为新建临时SQLite中的合成CSV，编码器和检索仓库为明确替身；生产Agent、API、Worker、事实许可、Validator和React代码实际运行。Python网络audit及真实PG/模型构造器阻断；前端Node socket连接阻断。所谓Top-5只在合成仓库发生，不消费真实Top-5额度。

### 合成运输样本（不是055真实数据）

合成task_id：`475ce9b331b045228fb7b527d587d019`；conversation_id：`p7c-SYNTHETIC-conversation`；原生终态succeeded；Router=evidence、Planner=semantic_evidence、Tools=get_analysis_context→search_semantic_evidence；原生query_contract=review-runtime-contract-v1/fact_search/delivery_slow；Validator=passed；模板回答；完整性摘要 `544741138a8e5248500c2b3db9a76c7722271c845d0cbe92d17db08f74ee5e4e`。

| Rank | 合成review_id / annotation_id | 合成观点原文 | 区间 | 判断 |
|---|---|---|---|---|
| 1 | 7 / 5 | 送货很慢 | [0, 4) | 许可：explicit_entailment |
| 2 | 8 / 6 | 物流太慢了 | [18, 23) | 许可：explicit_entailment |
| 3 | 9 / 7 | 送货到时挺快的 | [0, 7) | 拒绝：semantic_not_verified |
| 4 | 10 / 8 | 物流送货态度太差 | [0, 8) | 拒绝：semantic_not_verified |
| 5 | 8 / 6 | 商品到货时间太长了，等了八天才到货 | [0, 17) | 许可：explicit_entailment |

这3/5仅用于验证许可名单与正文引用对应，不是相关率、召回率或总体发生比例。完整原件：[offline-native.json](../outputs/mvp-real-acceptance/p7c-rag-20261113T084141Z/offline-native.json)，包括任务原件、任务HTTP响应和公开HTTP响应；空证据和编码失败原件单列保存。

## 4. 原始失败记录

- `preflight.initial.json`：初始包元数据探针错误地把可选sentencepiece列为必需，返回1；当前use_fast/tokenizer.json路径所需依赖核对后修正，随后实际离线模型加载通过。未安装/下载任何包或模型。
- `specialized-initial.log/json`：新测试把AnalysisParameters完整对象误作selection_scope，API正确422拒绝。另有初始运行器对子测试重复计数，原JSON的passed=-2是计数器错误；实际10个测试方法均失败，原件保留。
- `specialized-after-payload.log/json`：9/10通过，剩余为新测试猜测“证据不足”措辞而未匹配生产“没有返回候选…不表示全量不存在”。修正为验证生产实际安全语义，未改变生产回答。
- `build.log`：新增测试未对宽类型opinions作数组检查导致TS2488；仅新增测试改为Array.isArray，最终专项和构建通过。`build-final.log`另存，不覆盖首次错误。
- 后端三项旧夹具失败的完整trace和独立复核均保留，不改判PASS。

## 5. 待授权真实执行

详细可审查方案见[execution-plan.md](../outputs/mvp-real-acceptance/p7c-rag-20261113T084141Z/execution-plan.md)。只允许本轮新任务1、本地Query Embedding1、只读PG Top-5检索1；付费LLM、同步、PG业务写入和执行重试各0。使用真实Chrome、真实React/API/Worker和rag_audit_ro；另建独占SQLite任务库与本轮证据目录，禁止复用历史任务库。

执行前必须装配并验证一次性调用闸门、PG只读约束和观测器；计划中明确指出Worker锁会话默认readonly参数问题，需要在专用运行边界强制只读，不能只凭角色名字假定事务只读。实际发生前仍需用户明确授权。任何失败保留原生状态、原始失败与截图，既不重试也不借旧PASS补齐。

真实阶段将记录task/conversation/终态、路由规划工具、加载/编码/总耗时、全部Top-5及来源、逐候选许可与拒绝、精确引用、Validator、public_answer/Chrome展示、费用/调用/写入计数与最终清理。当前没有伪造这些尚未执行的结果。

## 6. 清理与完整性

[cleanup.json](../outputs/mvp-real-acceptance/p7c-rag-20261113T084141Z/cleanup.json)实测：本轮模型/项目Python与Node进程0，目标API/前端端口0，rag_audit_ro其他会话0、目标跨角色Worker锁0；诊断连接关闭，事务随连接结束。连接额度仍为8。原有PostgreSQL PID33352/端口5432和Redis PID26272/端口6379保留。

初始清单699个源文件/旧测试/文档摘要无变化，8个模型缓存文件SHA无变化，12个历史任务SQLite摘要无变化。新增合成任务均在测试临时目录，正常清理；原生JSON和摘要证据保留。没有启动本轮真实API、Worker、React或Chrome验收服务。

## 7. 分项结论与证据路径

| 分项 | 结论 | 证明范围 |
|---|---|---|
| API/Worker集成 | PARTIAL | PASS，11项专项及原生API/Worker合成闭环；BLOCKED：未提交真实任务 |
| 本地编码与检索 | PARTIAL | 本地离线加载/health PASS；编码/检索只使用合成替身；BLOCKED：真实Query编码和Top-5均0次 |
| 事实许可 | PARTIAL | PASS，5个合成候选仅许可3个；BLOCKED：尚无本轮真实候选 |
| Validator | PARTIAL | PASS，原生RuntimeQueryContract与Validator；篡改拒绝；BLOCKED：未运行本轮真实任务 |
| React展示 | PARTIAL | PASS，jsdom专项5/5、全量287/287、最终构建通过；BLOCKED：无真实Chrome任务与截图 |
| 资源清理 | PASS | 模型进程/测试Worker已关闭；会话、事务、目标锁与项目端口无遗留；未启动真实API/Worker/React服务 |

| Evidence | Finding | Path |
|---|---|---|
| E-01 preflight.json + model-health.json | F-01：当前固定缓存可离线加载，真实编码尚未执行 | 配置→PersistentEmbeddingProcess initialize/health→close |
| E-02 backend-regression.json/log + offline-native.json | F-02：限定合成链路可用，扩展集保留3项旧失败 | 原生API→SQLite任务→Worker→Agent→合成编码/仓库→来源/许可→Validator→public_answer |
| E-03 frontend-all.json + frontend-specialized-final.json + build-final.log | F-03：HTTP运输与React/jsdom展示通过，不能代替真实Chrome | HTTP JSON→api.task/useAgentChat→ChatMessage→AnswerEvidence |
| E-04 cleanup.json | F-04：本轮资源清理和历史完整性复核通过 | 进程/端口→角色会话/事务/锁→SHA对照 |

复现与原始证据见本目录保存的运行器、日志和JSON。`integrity-manifest.json`保存本轮交付文件SHA。**本轮结论保持BLOCKED，尚不能称P7-C真实浏览器端到端PASS。**
