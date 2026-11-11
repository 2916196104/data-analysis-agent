# P6-B Planner 接入与一致性快照验收

日期：2026-11-09。基线提交：`84ad89e`（契约与选维）。本轮范围为实际请求链中的目标冻结、确定性 Planner、当前统计核验和同快照观点准备；不执行多维度主题分类。

## 1. 结论和启用边界

**P6-B：PASS（离线工程验收范围）。P6-C 开发准入：PASS；多维度分类/收费执行准入：BLOCKED。**

实际 Worker → Agent 已接入 P6 准备路径，复用 P6-A 目标、选维和独立校验。当前统计和全部目标观点共享已有只读事务，形成与连接无关的不可变运行时输入。排名过期、计划错误、范围/来源异常、输入不完整均失败关闭。没有新增 API 状态枚举或业务库 schema。

服务端设置 `AGENT_P6_PREPARATION_ENABLED=1` 时启用此准备路径；默认 `0`。它不是前端或任务参数，API 继续拒绝客户端可信排名、原始任务、必做维度及准备对象。保留默认关闭，是为了完整保留旧 P1-A/F01 未启用 P6 时的澄清和零查询契约；本轮真实 Worker 测试明确启用该设置。未改写用户 `.env`、启动生产任务或部署。

准备成功仍返回 Agent `unsupported` / Worker `succeeded`，说明“已识别并冻结输入，但分类执行尚未启用”。`task_acceptance.complete=false`、status=failed、evidence_status=not_executed；没有成功 highlights、主题目录、主题频次或最终分析结论。Worker succeeded 表示该受控请求已处理，不表示主题任务完成。

## 2. 修改前基线

审阅了 P6-A、P6 实施准备、F01、P1-A 和 P0-B 报告，以及可信状态、Router、Planner、Agent、工具、analytics 和数据库桥接源码。

- P6-A 只有独立纯函数和离线契约，`run_review_agent()` 未调用 freeze_p6_goal / validate_p6_plan。
- 原实际第二轮“具体体现在哪里？”在负向缺维度处澄清，没有多维计划或准备产物。
- `PostgresConnection.execute('BEGIN')` 已转换为 `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY`（readonly 连接）；不能再将其误认为默认 READ COMMITTED。
- SQLite 连接以只读 URI 打开，Agent 显式 BEGIN；analytics 的共享连接分支要求已有事务并直接复用它。单独传数据库路径则会另开事务，因此 P6 预读必须始终传同一个连接。

先新增三个真实 Worker 测试，再执行原代码：**3 项，1 通过、2 errors、0 skip**。缺失 request_goal.version 和 p6_preparation 的 KeyError 证明实际入口尚未接入，并非网络或环境错误。日志 `.cache/p6b/before-tests.txt`，SHA-256：`ded884e0a5fc62166523987e61b30331ff7f9a42205e87e34d1a8d773d787f68`。

## 3. 实际调用链和来源

启用模式后的候选请求顺序：

```text
Worker 从任务库加载同会话受控上下文
  → 读取当前商品目录并建立只读事务快照
  → 判断是否为 P6 候选（显式其他请求先返回原路径）
  → bind_review_scope：复用原商品、日期和继承规则，形成最终范围
  → freeze_p6_goal：原始用户目标及服务端原始排名重新核验
  → 冻结 P6RequestGoal
  → build_p6_query_plan
  → validate_p6_plan（失败时零准备查询）
  → prepare_p6_analysis（同连接、同事务）
  → 只保留安全诊断并退出事务、关闭连接
  → unsupported：分类执行未启用
```

加载器仅在 restore_ranking 成功后提供内部 ranking_task 和 conversation_id。原始任务由任务库读取，不从客户端或结果中自报的 ranking_basis 恢复来源资格。最终范围即使改变，也会被 freeze_p6_goal 的完整范围比较阻断；不能拿页面默认双商品范围直接编译。

显式单维度、数量、原文、好评、趋势、比较等请求先被 P6-A 的优先级契约排除，继续原路径。F01 的短情感承接不改动。没有可信来源时执行前澄清；没有正计数维度时不创建空目标；来源/身份/范围异常不会猜测前三名。

## 4. 修改文件和函数

| 文件 / 函数 | 原因与实现 | 兼容性 |
| --- | --- | --- |
| core/config.py：AgentSettings / from_values | 服务端准备开关，严格 0/1，默认关闭 | 不进入任务/API 参数，不改模型身份 |
| core/database.py：PostgresConnection._execute | 统计 SELECT/WITH/SHOW 读取语句数，不保存参数 | 不更改 SQL、隔离级别或连接权限 |
| jobs/analysis_context.py：load_analysis_context | 经复核后附加服务端原始排名任务及会话供运行时使用 | 不修改封存摘要、排名计数或持久化结构 |
| agents/review_intent.py：bind_review_scope | 公共的无模型范围绑定入口，复用原函数 | 原 Router 和 F01 分支原样保留 |
| agents/review_plan.py：ReviewQueryPlan / build_p6_query_plan | 按冻结顺序生成 k 个精确工具要求；P6 元数据只在启用模式序列化 | 旧计划输出不新增 None / 空字段；复用原 RuntimeQueryContract 构建器 |
| agents/review_multi_aspect.py：validate_p6_plan | 有 P6 元数据时，额外核对模式和自报集合 | P6-A 纯契约测试与版本均保留 |
| agents/review_topics.py：_units / prepare_topic_input | 公共纯候选准备函数；复用既有来源 ID 和选择逻辑，以 20 种表达、200 观点为上限 | 旧调用默认上限与动态常量行为不变，不调用发现/分类 |
| agents/review_tools.py：ReviewToolContext.execute | P6 主题工具在访问缓存/数据库之前硬拒绝 | 原单维/正向工具不变，防止旧循环误执行新计划 |
| agents/reviews.py：run_review_agent | 接入候选判断、最终范围、目标冻结、独立计划校验和准备；在旧工具循环前退出 | 仅 P6 准备模式走新分支，公开状态仍用 unsupported |
| 新增 agents/review_preparation.py | P6PreparedAnalysis、P6PreparationError、prepare_p6_analysis 与输入完整性校验 | 内部运行时对象，无公开反序列化或跨任务恢复入口 |
| 新增 tests/test_p6b_planner_snapshot.py | 19 项独立方法，包含多组故障和并发子用例 | 未删除、跳过、放宽旧断言 |
| 本报告 | 验收证据与 P6-C 边界 | 不改写五份前置报告 |

没有新增依赖、平行 Planner、主题分类工具、状态数据库、迁移或前端功能；未清理无关私有导入。

## 5. 实际生成的目标和 Planner

以下来自临时 SQLite 的真实 Worker 请求，不是生产统计。历史排名来源任务为 `5463fcbadf3747c880145c9cf1a144c6/r1`。

RequestGoal 核心字段摘录：

```json
{
  "version": "review-request-goal-p6-v1",
  "original_question": "具体体现在哪里？",
  "levels": ["themes"],
  "sentiment": "negative",
  "analysis_mode": "multi_aspect_negative_drilldown",
  "required_aspects": ["service", "effectiveness", "logistics"],
  "per_aspect_level": "themes",
  "selection_basis_sha256": "697fe2a902dc0ee9024c59aa0f830e8a99fc7284fb9f52258f02cf3019fa6377",
  "ranking_source": {
    "task_id": "5463fcbadf3747c880145c9cf1a144c6",
    "result_id": "r1",
    "integrity_version": "analysis-task-integrity-v1",
    "task_sha256": "b8e957af605e281f0ac875c637f5e1aaeb671a67c9e415d96fa4f90c7333a643",
    "result_sha256": "d6b8fc92a75cd001d5501285f760e34fe6c5345a72d49366d5d99f5b4866897d",
    "validation_sha256": "0f64f98298ebe9cea187c488670ffd951f8e97c64352306493e2bf7fe1fd859d",
    "scope_sha256": "8e94b4164db0a010d05f63a246de604b53c362691ace3b6f3a50fa4aa0f8061c"
  }
}
```

完整目标另含完整六维 selection_basis、expected_scope、来源时间等字段；上面仅摘列关键字段。最终范围：单 SKU 100013588053，amazon_reviews_2023 / independent-state，标注身份 independent-labels / labels.invalid / independent-v1，当前 2023-02-01—2023-02-28，基线 2023-01-01—2023-01-31，as_of=null。查询规范化范围另外显式保留 timezone=UTC。

真实编译的 requirements：

```json
[
  {"tool": "get_aspect_drilldown", "parameters": {"product_ids": ["100013588053"], "aspect": "service", "sentiment": "negative"}},
  {"tool": "get_aspect_drilldown", "parameters": {"product_ids": ["100013588053"], "aspect": "effectiveness", "sentiment": "negative"}},
  {"tool": "get_aspect_drilldown", "parameters": {"product_ids": ["100013588053"], "aspect": "logistics", "sentiment": "negative"}}
]
```

计划 analysis_type=aspect_drilldown、analysis_mode=multi_aspect_negative_drilldown、sentiment=negative；scope 绑定上述完整范围，附带原构建器生成的 task_contract。未增加 baseline/topic 筛选或额外工具。validate_p6_plan 的 independent_goal、scope_and_mode、p6_metadata、exact_required_aspects、per_aspect_capability 全为 true。

删除第三维、重复首维、替换为第四维 quality、交换顺序、改统计工具、改情感/商品/日期/数据集/标注身份、增加工具、伪造计划元数据的 Worker 故障注入均被拒绝，准备查询为 0，原冻结目标不变。

安全证据 `.cache/p6b/worker-preparation.json` 包含完整 RequestGoal、Planner、独立检查和准备诊断，不含完整评论正文；SHA-256：`35e69e359c6547510995257605b979c0039106da91a2e4935886f66e3bee53ce`。

## 6. 当前统计核验和过期阻断

复用原 analytics.get_top_issues，同一范围、无基线；先验证原业务 definitions、完整六维、数字/分母，再调用 P6-A check_current_ranking。没有新增统计 SQL 或改变 distinct_reviews / successful_reviews 口径。

| 项目 | 历史 | 当前同快照 | 修改后新请求 |
| --- | ---: | ---: | ---: |
| D 成功标注评论 | 6 | 6 | 7 |
| service | 5 | 5 | 5 |
| effectiveness | 4 | 4 | 4 |
| logistics | 4 | 4 | 4 |
| quality | 2 | 2 | 2 |
| price | 1 | 1 | 1 |
| packaging | 0 | 0 | 1 |
| 完整 scope / 身份 | 原范围 | 相同 | 相同 |
| current_ranking_matches | 历史输入 | true | false |

修改后新请求 reason=stale_ranking，返回“历史排名已过期，需要刷新统计”，只执行 1 次统计查询（本样例 4 条准备阶段业务读取语句），**未预读观点、未重新挑选前三名**。独立故障测试还单独改变未入选维度、删除一个维度、删除统计行、修改分母定义或计数单位，均阻断准备。

过期证据 `.cache/p6b/stale-ranking.json`，SHA-256：`30477a4dea0eb16bd180d95b3edafae5a2f33a52186d4923cd88a2a1ce465147`。

## 7. 同快照依据与冻结输入

SQLite：使用 Agent 现有 readonly 连接、显式 BEGIN，并在准备阶段开启连接级 query_only；统计及三维 get_aspect_opinions 均传入同一连接。原 analytics._snapshot 共享事务分支不提交、不重连。SQLite WAL 测试通过另一连接真实提交：

1. 统计完成后导入并标注一条包装负评，后续三维预读仍见原 D=6；请求结束后的新查询看到 D=7。
2. service 预读后将临时库的 effectiveness 观点改为 positive；后续 effectiveness 冻结仍为原 negative 输入，事务释放后新统计看到该维负面计数为 0。
3. 请求退出后，捕获的连接已关闭；冻结对象仍能 validate/get_input。之后再导入数据，已冻结输入不变。

这证明当前 SQLite 路径没有把两个读取时刻混用，不仅是“传入了同一个连接”的断言。

PostgreSQL：复用原工厂 BEGIN → REPEATABLE READ READ ONLY；准备前进一步 SHOW transaction_isolation / transaction_read_only，非 repeatable read/on 时在统计前拒绝。适配协议测试验证实际 SQL 转换，并注入 READ COMMITTED 和可写事务以确认拒绝。**本轮未获独立 PostgreSQL 测试数据库授权，未运行真实 PostgreSQL 并发集成，不能由 SQLite 结果推导其已通过。**

snapshot_audit 保存 backend、isolation、readonly、读取时间和 logical_transaction_id。后者只是本次事务的应用审计关联号，不是数据库原生快照 ID，也不凭这个字符串证明一致性。

准备对象版本 `review-p6-prepared-v1`，包含冻结 goal、计划 JSON、完整当前快照、按必做集合排列的输入、每维摘要、覆盖/截断、事务审计和总摘要。输入保留最终范围及实际 UTC 查询范围、完整统计 summary、M_a/观点总体数、候选评论、观点与稳定派生 opinion_id、原文位置、文件摘要/行引用、annotation/revision 和来源信息。来源 ID 复用原 _units 的计算方式，不冒充新的数据库行主键。

| 维度 | 全量匹配评论 M_a | 候选评论 / 观点 | 表达数 | 评论 / 观点覆盖 | 截断 | input SHA-256 |
| --- | ---: | --- | ---: | --- | --- | --- |
| service | 5 | 5 / 5 | 1 | 1.0 / 1.0 | false | 7af6babc6fa30dc0d055929dba59abecd7f63d5d9652f400dd0b54a7db05befd |
| effectiveness | 4 | 4 / 4 | 1 | 1.0 / 1.0 | false | cf0989823db72fafe9b6a02d077b16c7cbcc9035a3baf8518da92df7c242a967 |
| logistics | 4 | 4 / 4 | 1 | 1.0 / 1.0 | false | be11dacd3c1efc478f4164a57749680dc7bbbff9f79a88b63878a6fd5f0a6194 |

该样例整包输入摘要为 `572daf1b43348ee4d2044a39baa20dfb15131306955daf2d354e925377c3acf8`。准备逻辑执行 4 次 analytics 调用，SQLite 跟踪到 37 条 SELECT/WITH 读取语句（含内部 schema/统计/证据查询）；不计更早的任务库或商品目录加载。主题、意图、规划、回答和真实 Embedding 调用均为 0。

另有真实临时语料测试：service 全量 30 条，超过 20 种表达后受控截断，明确记录 fetched、selected、coverage 和 truncated。读取最多 100 条评论、至多 20 种表达/200 观点；不把选中样本当全量。原文过长不会被静默截断成新证据，后续请求字节/模型预算仍需 P6-C 拒绝超限输入。

## 8. 内部消费、安全和未完成状态

P6PreparedAnalysis 是 frozen dataclass；正文封存在隐藏 repr 的 JSON 字符串，方法返回副本。get_input(aspect) 先检查整包摘要、目标和确定性计划、当前排名一致性、完整必做集合、每维摘要、观点/评论/单位关联、原文范围、summary 和覆盖率。缺输入、错 scope、漏观点或错误摘要均失败关闭；没有数据库参数、连接、游标、惰性查询或回补分支。

运行时原对象只在本次调用内准备和检查。Agent 任务结果只保存安全诊断：范围、D/六维计数、必做集合、覆盖率、摘要、查询次数和原因。**完整冻结输入不写入任务 JSON 或公开 API，未设计持久恢复入口。** 当前请求受控结束后丢弃正文；未来 P6-C 应在同一后端请求内结束事务后消费冻结对象，不能拿历史诊断 JSON 或 hash 取得执行资格。若要跨进程/任务复用，须另行设计安全存储、有效期、版本和重新认证。

双重执行屏障：run_review_agent 在旧 registry/工具循环之前退出；即使错误调用旧 ReviewToolContext.execute，P6 主题工具也在缓存检查前拒绝。因此缓存未命中不能引发第二次数据库读取，缓存命中也不能越过分类未启用边界。

没有调用 analyze_aspect_topics，未生成 catalog/assignments/主题结论，未调用 validate_p6_completion 将准备当分析完成。旧 P6-A 固定产物的纯验收回归照常运行，不是在 P6-B 执行分类。

## 9. 测试、证据和保护资产

新增 19 个测试方法通过表驱动及真实 Worker 路径覆盖附件 A—E：真实触发、无可信来源、跨会话、显式范围变化、前端伪造、0—6 非零和并列排名、12 类计划故障、当前 D/未入选维变化、统计不完整、WAL 新增/观点修改、事务关闭后验证、摘要损坏、维度预读失败、有界候选、缓存硬拒绝和 PostgreSQL 事务协议。

Planner 的合成排名矩阵经服务端临时任务封存后走真实 Worker，验证计划集合；与实际临时业务数据不符时按预期在新鲜度门槛停止，不把该矩阵声称为准备完成或分类成功。

明确请求优先由真实 Worker 和 Router 调用验证。准备轮后的 F01 正向追问使用真实 Router + 独立 RequestGoal 检查能力一致性，避免在本轮启动旧正向主题函数；旧 F01 的完整 Worker 回归另行全部重跑。所有新 P6-B 测试封锁分类、LLM 和 Embedding 入口并断言未调用；P6 准备请求统一检查 total_model_calls=0、tool_calls=0、trace=[]。

| 最终检查 | 结果 |
| --- | --- |
| 修改前新增基线 | 3 项：1 PASS、2 errors，暴露尚未接入 |
| P6-B 新增 | 19/19 PASS，含并发、故障注入及多组子用例 |
| P6-A | 19/19 PASS |
| F01 / P1-A 独立 | 13/13、11/11 PASS |
| 原适用回归 | 450/450 PASS，其中 P0-A 10、P0-B 14、可信状态 18 均包含在内 |
| 功能合并完整运行 | **512/512 PASS**，194.929 秒，0 failure / error / skip |
| 额外连接、任务提交/日志、包结构 | 16/16 PASS，0 failure / error / skip |
| 私有接口静态审计 | 独立 FAIL：原 13 个子项，0 error / skip，无新增模块失败 |
| Python 编译 / git diff --check | PASS |

测试中使用临时 SQLite，真实 WAL 写入仅发生在测试夹具；功能合并运行封锁 socket 和 psycopg 外部连接。PostgreSQL 协议使用假驱动，无生产库读取、写入、迁移或部署。调试时补强了漏观点拒绝边界，并为旧正向追问改用 Router/Goal 检查，未放宽旧测试。

入场对 1591 个已有文件计算 SHA-256；本轮只改变第 4 节列出的九个既有源码文件，无删除。所有旧测试、前置文档、前端、冻结 RAG RuntimeQueryContract 及人工评审资产保持原摘要。新增准备模块、测试和本报告；安全证据在 `.cache/p6b/`。完整变更核对见 change-audit.json，不把输入哈希当语义质量证明。

历史静态失败仍来自 agents/reviews、review_retrieval、review_shadow_siliconflow、review_structured_evidence，以及 reviews/e1a_b3_runtime、e1a_b3_worker、e1a_prediction、e1_preparation、embedding_store、exploratory_e1、historical_snapshot、rag_corpus、semantic_retrieval。未为本阶段清理或放宽规则。

复跑（仓库根目录 PowerShell）：

```powershell
$env:PYTHONPATH='.;src;tests'
.venv/Scripts/python.exe -m unittest test_p6b_planner_snapshot test_p6a_contract -v
.venv/Scripts/python.exe .cache/p6b/run_checks.py functional
.venv/Scripts/python.exe .cache/p6b/run_checks.py static
.venv/Scripts/python.exe -m unittest test_dependency_boundaries.ConnectionContractTests test_task_submission test_task_logging test_package_layout -v
.venv/Scripts/python.exe .cache/p6b/probe.py
.venv/Scripts/python.exe -m compileall -q src/data_analysis_agent/agents/review_preparation.py tests/test_p6b_planner_snapshot.py
git diff --check
```

static 预期继续独立失败 13 子项。probe 只持久化安全诊断，不输出冻结正文；每次复跑的临时 task_id、事务审计号、时间和对应哈希会变化。

## 10. P6-C 准入和停止点

| 项目 | 判定 |
| --- | --- |
| 实际 Agent/Worker 接入与确定性 Planner | PASS，服务端准备模式启用时有效，默认旧行为保留 |
| 独立目标、来源与计划能力校验 | PASS |
| 当前完整统计与历史比较 | PASS，变化时拒绝、不换前三名 |
| 同快照观点预读 | PASS（SQLite 实际并发）；PostgreSQL 仅协议验证，真实并发未验收 |
| 连接释放后可验证的冻结运行时输入 | PASS，不作为客户端权限或跨任务存储 |
| 共享模型预算 / 费用授权 | 尚未实现，收费执行 BLOCKED |
| 多维主题分类 | 仍被硬阻断，未执行真实或模拟多维分类流程 |

**P6-B PASS 不等于完整 P6 上线。** P6-C 可在以上可信输入接口之上实施共享预算和受控执行，但必须先完成额度、调用/时间/字节边界及失败处理，才能讨论模型执行授权。真实 PostgreSQL 集成、主题质量、公开部分产物和 UI/复制/导出仍未完成。

本阶段完成后停止，不进入 P6-C，不实施共享模型预算，不执行真实多维度分类，不改前端或生产环境。
