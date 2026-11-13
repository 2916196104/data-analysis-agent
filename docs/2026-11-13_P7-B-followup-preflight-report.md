# P7-B：多轮客服观点追问修复与验收预检

**当前结论：BLOCKED（等待本轮两条真实任务的明确提交授权）。** 修复与离线验收通过；真实两轮尚未提交，不能宣称React/API/Worker真实端到端通过。预检日期：2026-11-13。

本报告依据本轮源码、隔离SQLite/固定Mock响应、未修改HEAD对照和真实只读环境预检。没有复用旧A1、C1、P7-A或RAG的PASS代替本轮验收。

## 1. 固定目标与实际定位

- 固定来源：reviews_csv / reviews-csv；商品100013588055；UTC 2021-09-11—2022-08-26。
- 第一轮：商品100013588055的差评主要集中在哪些方面？仅作为本轮可信上下文锚点，不重新进行A1独立验收。
- 第二轮：客服方面具体有什么问题？
- 原有RequestGoal即为`opinion_content / service / negative`；Router为`opinion_breakdown / drill_down`，Planner为`get_opinion_breakdown`。没有删除主题要求或把内容任务换成六维统计。
- `load_analysis_context`按会话、数据集、标注身份、范围和保存的完整性摘要恢复可信结果。`restore_ranking`仅允许通过Validator与任务验收的完整负面排名；失败/partial/篡改结果不供排名。
- 固定第二轮在修复前已能走零模型路径。新增专项进一步发现：公开引用丢失原有annotation_id；“详细分析”可触发回答模型；正向历史后明确问“具体有什么差评”时，Goal按统计、Router按内容解释而阻断。

## 2. 最小修改

| 文件 | 修改与必要性 |
|---|---|
| src/data_analysis_agent/agents/review_answers.py | 仅纯opinion_breakdown的opinion_group/empty事实选择模板，避免措辞隐式触发回答LLM；其他复杂分析策略保留 |
| src/data_analysis_agent/agents/review_results.py | 普通引用适配时带出原工具已返回的annotation_id，不新造标注身份或来源 |
| src/data_analysis_agent/agents/review_acceptance.py | 将明确“具体有什么差评”识别为观点内容，修复情感切换后的Goal/计划冲突；主题判据优先级不变 |
| src/data_analysis_agent/agents/review_semantics.py | 对同一句明确内容请求补齐content_detail判据，使Router与独立Goal保持一致；商品ID精确匹配逻辑未改 |
| tests/test_review_followup_p7b.py | 20项隔离SQLite与原生Worker/State/工具/Validator专项 |
| frontend/src/features/agent/followup.test.tsx | 新增2项追问原文、父任务和日期范围传递验证；不修改前端业务源码或已有测试预期 |

未修改State存储契约、Planner工具协议、统计SQL、数据库、标注Profile、Validator、主题覆盖门槛、事实许可、人工裁决、P0/P1/P6或模型配置。没有运行B4或向量工程。

## 3. 观点内容和完整主题的边界

`get_opinion_breakdown`在当前有效观点只读快照内按`quote + sentiment`精确归组：返回匹配评论总数、观点总数、前10组去重评论数/观点数/占匹配评论比例，前3组带代表评论与精确引用。代表评论通过真实review_id回读，原始结果包含annotation_id、来源/数据集/商品、日期、文件SHA与行号、原文区间。

这些是原始观点内容和出现次数；同义句没有归并，同一评论可进入多个组，不能把分组数相加当成总评论数，也不能称为完整二级主题。回答继续保留该限制，发布范围是原有opinion_content目标。

完整主题必须保持原有发现、独立归属复核、计数重算、引用校验及任务完成要求。单个已核验主题维持原模板策略；多个topic fact的aspect_drilldown仍可能选择LLM。本轮只在Mock离线输出上检测这条策略，不执行付费生成，更不把它降级为观点分组。partial/failed禁止完整发布；真实无匹配数据按insufficient_evidence受控返回；伪造“insufficient_data但仍有完整主题”的不一致结果被主题Validator拒绝。

## 4. 本轮测试结果

| 测试 | 实际结果 |
|---|---|
| P7-B专项 | 20/20 PASS；模型构造器、查询编码、真实PostgreSQL均拦截 |
| 后端扩展回归 | 289项，287通过、2项既存失败、0错误 |
| 未修改HEAD同一测试集 | 289项，284通过、5失败；本轮消除3个已复现缺口，无新增失败 |
| 前端全量最终运行 | 282/282 PASS，maxWorkers=2，未修改10秒测试超时 |
| 前端首次全量运行 | 279/282，3条10秒超时；与构建并行运行，失败文件及业务源码未改，原日志保留 |
| 前端生产构建 | TypeScript/Vite PASS，保留原有大分块警告 |
| git diff --check | PASS |

两项既存失败均为旧非MVP Worker样本被P1拒绝，与HEAD错误断言一致：

- `test_review_plan.QueryPlanTests.test_timeout_after_query_is_failed_and_diagnostics_preserved`
- `test_review_semantic_integration.SemanticAgentTests.test_p3b_worker_executes_two_tasks_using_default_service_factory`

专项覆盖：正常负面追问；正负面隔离及明确切换；缺失可信排名；篡改历史；商品/日期切换；跨会话/来源污染；引用及标注身份；观点分组不能冒充完整主题；单/多主题策略；partial/failed/无数据保护；明确主题目标不降级；模型策略预检拒绝。扩展回归覆盖A1/C1模板、P7-A、RAG语义与引用、预算拦截和P0/P1。

所有开发过程记录保留：`p7b-before.log`（14项/4失败，含一条新测试对正向工具参数的错误断言，后按工具真实契约校正）；`p7b-after.log`（14项/1失败）；`p7b-specialized.log`（18项/1失败，测试构造了未经过语义Validator的不一致insufficient_data对象，后增加完整入口校验）；`p7b-specialized-final.log`（20/20）。没有修改旧失败测试或冻结人工预期。

## 5. 分项验收与证明范围

| 验收项 | 离线结果 | 真实业务结果 |
|---|---|---|
| State/Router/Planner | PASS：两轮同会话、可信排名及范围继承、负面客服观点计划 | NOT_RUN |
| 观点SQL | PASS：原生SQLite工具执行，数据明确为合成数据 | NOT_RUN：未查询真实业务观点 |
| 来源与引用 | PASS：精确原文区间、review_id/annotation_id、文件引用与范围绑定 | NOT_RUN |
| 回答完整性 | PASS：原有opinion_content目标；不宣称完整主题 | NOT_RUN |
| Validator/task_acceptance | PASS：实际运行，失败与证据不足保护保留 | NOT_RUN |
| React/API/Worker | 前端传递测试和原生Worker离线链路通过 | NOT_RUN；没有本轮浏览器截图 |

隔离SQLite两轮快照中的task_id、conversation_id、State、RequestGoal、意图、计划、工具结果、引用、Validator和最终回答保存在`offline-two-turns.json`，顶层明确标记`ISOLATED_SYNTHETIC_SQLITE_NOT_REAL_POSTGRESQL`，不能把其中评论数、ID或引用冒充真实055业务结果。

## 6. 真实预检、待授权操作与清理

原生产API启动检查PASS；rag_audit_ro/read_only=on；CONNECTION LIMIT=8；跨角色目标Worker锁为空；该账号剩余连接为空；8011/5184端口空闲。预检只查询版本、角色及锁元数据，连接已关闭；未修改ACL或角色。

依照本次任务第四节“只有离线验证通过、真实执行边界确认安全，并获得覆盖本次操作的明确授权后，才允许实际提交”，尚需确认本轮新建两条任务的执行额度。之前A1、P7-A等一次性额度已经消费，不能自动复用。

待批准的具体操作仅为：

1. 新建本轮独立SQLite任务库`outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/tasks.sqlite3`，不覆盖历史库。
2. 启动真实API+内置Worker（8011）、React（5184）及专用浏览器；LABEL_EXECUTION_MODE=local，不启动run.py或Redis消费者。
3. 本轮固定首轮与第二轮各提交一次，第二轮仅在首轮完整成功后执行，并验证同一conversation_id及父任务引用。允许必要SQLite状态/心跳写入与只读业务SQL。
4. 付费模型、查询编码、Embedding及业务写入调用前阻断；禁止get_aspect_drilldown及额外任务，失败不重试。
5. 保存两轮原生任务、SQL、回答和截图后正常关闭服务，复核端口、连接、事务与跨角色锁。

当前未启动真实API、Worker或浏览器；未创建本轮真实任务库，真实提交0、付费模型0、查询编码0、业务写入0。只读预检连接均已释放，无本轮服务或锁需要停止。没有用旧截图或Mock浏览器结果代替真实验收。

## 7. 证据到结论

所有本轮产物位于：`outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/`。

| 证据 | 支持的结论 | 生产路径 |
|---|---|---|
| offline-two-turns.json、offline.log | 仅离线两轮上下文、真实工具代码及结果验收可用 | load_analysis_context → resolve_review_request → get_opinion_breakdown → Validator → template |
| baseline.json、offline.json、offline-verdict.json | 修复3项，无新增失败；2项旧样本失败未掩盖 | 同一测试集与内存HEAD源码对照 |
| frontend-final.json、frontend-tests.log、frontend-build.log | 前端原文/父任务传递、全量回归和构建检查 | resolveQuery → useAgentChat |
| production-startup-preflight.json、before-connections.json、resources-before.json | 只读启动条件具备，尚未启动真实系统 | 原生产检查、跨角色pg_locks、Windows监听检查 |
| changes.diff、source-files.json | 修改范围与实际源码摘要 | 四个生产文件与两处测试文件 |

机器可读结论见本轮`acceptance.preflight.json`。**本报告不能作为P7-B真实业务验收PASS；真实阶段当前BLOCKED于明确提交授权。**
