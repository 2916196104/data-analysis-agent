# P7-A：趋势确定性回答最小修复与真实验收

**结论：PASS（本次固定 B5 真实业务闭环）。** P7-A 专项19/19通过；扩展回归211项中209项通过、2项为未修改HEAD也会失败的旧样本问题，不能称为全量回归通过。验收日期：2026-11-13。

本轮真实任务只提交一次，无自动重试。使用此前未消费的B5零模型授权及本轮P7-A条件执行授权，见[授权核对](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7a-trend-20261113T065854Z/authorization.json)。没有执行B4、B6或P7-B/C/D。

## 定位及最小改动

原阻断有两处：`AnswerGenerationStrategy.choose()`将非空trend/aspect_trend送入LLM；生产执行入口对已编译趋势计划仍进入模型工具规划。原模板事实只突出峰值/相邻变化，缺少逐月完整数据。

| 文件 | 修改原因 |
|---|---|
| agents/reviews.py | 已确认trend/aspect_trend沿用现有直接执行分支，仍经过工具注册表和全部校验；不扩大其他任务类型 |
| agents/review_answers.py | 仅纯趋势且事实为trend/empty时选template；比较、复合和主题策略保留 |
| agents/review_answer_facts.py | 使用已核验事实逐期发布数量、分母、比例及来源；数量与百分点分开；保留时间窗口、低样本、空值和压缩省略说明；不附加趋势改善建议 |
| agents/review_validation.py | 复用SQL工具的UTC桶定义，增加时间桶完整性、顺序和边界检查；不改变SQL口径、阈值或事实许可 |
| tests/test_review_trend_template.py | 19项独立SQLite与原生产Agent链路测试，构造器/网络/编码均拦截 |
| tests/test_review_answers.py、test_review_insights.py、test_review_answer_stability.py | 更新趋势零模型预期；原模型异常测试改用仍需模型的比较任务，保留原异常与安全检查 |

源文件实际SHA见[source-files.json](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7a-trend-20261113T065854Z/source-files.json)，已跟踪代码差异见[changes.diff](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7a-trend-20261113T065854Z/changes.diff)。未修改统计SQL、P0/P1/P6、人工裁决、事实白名单、模型身份或数据库权限。

## 真实入口与执行

- task_id：`635f321b20d34490a669e6dfbd25ee7b`；task.status=`succeeded`，result.status=`ok`。
- 问题：商品100013588055的物流负面评价按月有什么变化？
- 范围：reviews_csv / reviews-csv / 100013588055；UTC 2021-09-11—2022-08-26；既有标注Profile27，基线2021-09-10（本题不查询基线）。
- Router：rules；trend / logistics / negative / month，无澄清。
- RequestGoal：statistics / trend；Planner：get_aspect_trend(aspect=logistics, period=month)。
- 实际工具：get_analysis_context → get_aspect_trend；query_executions=2，任务内真实PostgreSQL语句32条（含范围、元数据和只读事务语句，不等同于工具调用次数）。
- 数据库账号rag_audit_ro，所有观测连接只读；真实API、Worker锁与心跳、options/quality/health并发200及浏览器首屏均通过。未启动Redis标注消费者。
- SQL口径：按UTC月分桶，评论内多条负面观点按评论去重；物流分子不替代整体负面分子，分母为对应月份成功标注评论数。没有成功标注时比例为null，不当作0%。

原生数据见[B5-tool-output.json](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7a-trend-20261113T065854Z/B5-tool-output.json)，SQL文本、时序、参数摘要及只读标志见[B5-sql-executions.json](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7a-trend-20261113T065854Z/B5-sql-executions.json)。完整原生任务见[B5-task-native.json](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7a-trend-20261113T065854Z/B5-task-native.json)，没有以浏览器JSON重编码后的数值表示替代原生快照。

## 实际月度结果

| 月份 | 物流负面评论 | 成功标注评论分母 | 负面占比 |
|---|---:|---:|---:|
| 2021-09 | 1 | 2 | 50.0% |
| 2021-10 | 0 | 4 | 0.0% |
| 2021-11 | 5 | 33 | 15.2% |
| 2021-12 | 1 | 10 | 10.0% |
| 2022-01 | 0 | 9 | 0.0% |
| 2022-02 | 2 | 8 | 25.0% |
| 2022-03 | 1 | 9 | 11.1% |
| 2022-04 | 0 | 22 | 0.0% |
| 2022-05 | 0 | 10 | 0.0% |
| 2022-06 | 9 | 78 | 11.5% |
| 2022-07 | 25 | 110 | 22.7% |
| 2022-08 | 9 | 210 | 4.3% |

本窗口510条评论、505条成功标注评论；物流负面评论53条。上述12月分子合计53，分母合计505。7月到8月：数量25→9（-16条），比例22.7%→4.3%（-18.4个百分点）；不能把两种变化混为同一指标。

## 确定性回答与Validator

```text
2021-09-11 至 2022-08-26（UTC）的已标注样本：物流可比较的标注样本不足，暂不能判断稳定趋势。

物流已标注样本的占比峰值在 2021-09，为 50.0%（1/2 条成功标注评论）。最近可比较的相邻时段 2022-07 至 2022-08 变化 -18.4 个百分点。逐期数据（UTC）：2021-09：负面评论 1 条，分母 2 条成功标注评论，占比 50.0%；2021-10：负面评论 0 条，分母 4 条成功标注评论，占比 0.0%；2021-11：负面评论 5 条，分母 33 条成功标注评论，占比 15.2%；2021-12：负面评论 1 条，分母 10 条成功标注评论，占比 10.0%；2022-01：负面评论 0 条，分母 9 条成功标注评论，占比 0.0%；2022-02：负面评论 2 条，分母 8 条成功标注评论，占比 25.0%；2022-03：负面评论 1 条，分母 9 条成功标注评论，占比 11.1%；2022-04：负面评论 0 条，分母 22 条成功标注评论，占比 0.0%；2022-05：负面评论 0 条，分母 10 条成功标注评论，占比 0.0%；2022-06：负面评论 9 条，分母 78 条成功标注评论，占比 11.5%；2022-07：负面评论 25 条，分母 110 条成功标注评论，占比 22.7%；2022-08：负面评论 9 条，分母 210 条成功标注评论，占比 4.3%。上述相邻时段的负面评论数为 25 → 9 条，数量变化 -16 条；数量变化与占比变化分别计算，各期分母可能不同。

本次分析范围共 510 条评论，基于 505 条成功标注评论。

部分样本量较少，结论仅描述已标注样本。

首末月的查询区间未覆盖完整自然月，不宜直接按整月比较。

尚未核验各时段内的数据采集完整性；变化不能直接解释为已证实的原因。
```

上述正文已在React实际发布；脚本逐月核对真实工具结果中的数量和分母，并按显示精度核对比例，截图已实际查看。低样本月份、首末月不完整及采集完整性未知均保留限制，没有发布持续改善或因果结论。

- Native Validator：passed，所有检查通过；AnswerValidator、template_draft、AnswerAssembler实际运行。
- task_acceptance：passed，complete=true；工具计划、统计目标、范围、口径和实际产物均通过。
- intent/planning/topic/answer模型调用均为0；usage无供应商响应，因没有发出模型请求，实际新增模型费用为¥0。
- 本地查询编码0、Embedding同步0、PostgreSQL业务写入0。测试运行器对模型构造器、外部HTTP、查询编码与业务写入做调用前拒绝，实际boundary_blocked事件0。
- Agent总耗时约845.8ms；SQL工具累计约510.9ms；观察连接峰值5/8（50ms采样，包含观察连接）。

## 离线回归

专项19/19覆盖正常/空趋势、单月、低样本、分母变化、数量不变而比例变化、数量与比例反向、未标注、跨商品/日期污染、错误分母、桶缺失/乱序/越界、日度与不完整自然月、压缩省略、禁止无依据建议、整体趋势零模型、既有不支持边界及比较策略隔离。

扩展回归：211项，209通过、2失败、0错误。A1/C1模板、RAG语义与引用、P0/P1、预算拦截及回答异常回归均在本次运行中检查；**两条旧Worker用例使用非MVP来源，被P1拒绝**。在内存导入未修改HEAD的四个生产模块后，失败用例及完整断言栈完全一致，不是本修复新增失败。未放开P1、篡改预期或修改旧样本。

- [offline-final.json](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7a-trend-20261113T065854Z/offline-final.json) / [完整日志](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7a-trend-20261113T065854Z/offline-final.log)
- [baseline.json](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7a-trend-20261113T065854Z/baseline.json) / [基线日志](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7a-trend-20261113T065854Z/baseline.log)
- [专项与既存失败判定](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7a-trend-20261113T065854Z/offline-verdict.json)

修改前回答/压缩基线35/35通过。初次扩展运行另有测试运行器导入路径错误，已修正；最初日志保留，不计为最终回归结果。早期开发测试日志亦保留，未删除失败记录。

修改后前端生产构建通过（TypeScript + Vite，保留原有超过500kB的分块警告），见[构建日志](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7a-trend-20261113T065854Z/frontend-build.log)。前端源码未修改，本轮未重复前端280项全量测试；实际React首屏、单次提交和12个月回答展示已通过真实浏览器验证。

离线复查可使用项目虚拟环境，PYTHONPATH=src;tests 后执行 `python -B -m unittest test_review_trend_template test_review_answers test_review_compression test_review_answer_stability test_review_insights`。本次真实提交额度已消费，不应重跑browser.mjs B5；独占提交标记会拒绝重复提交。

## 清理与写入边界

API通过原生Uvicorn生命周期关闭，Worker.stop完成并释放会话锁；Vite对本轮控制台发送Ctrl+C停止（中断退出码1），未强杀；观察连接通过停止标记关闭。最终测试端口8011/5184监听0，本轮进程0，跨角色目标锁0，rag_audit_ro剩余会话0，业务事务随连接关闭结束。连接额度仍为8，未自动恢复或修改。

允许写入仅为全新SQLite任务库初始化、单条任务及Worker心跳/状态，以及本地日志、截图和报告；原有成功任务库SHA保持一致。业务库、评论、标注、向量、Redis队列、权限和模型配置未修改。

清理证据：[cleanup.json](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7a-trend-20261113T065854Z/cleanup.json)、[资源检查](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7a-trend-20261113T065854Z/resources-after.json)、[跨角色SQL检查](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7a-trend-20261113T065854Z/after-connections.json)。

## 证据到结论

| 证据 | 结论 | 生产路径 |
|---|---|---|
| 原生任务、SQL日志与逐月返回数据 | 真实固定范围查询成功，月度数值、分母与比例一致 | Router → Planner → get_aspect_trend |
| validation、task_acceptance、answer_generation | 经过全部验收后以模板发布，模型0调用 | validate_report → validate_task_completion → template_draft → AnswerValidator → AnswerAssembler |
| 浏览器POST、回答、截图与原生任务ID | 实际React/API/Worker闭环；单次提交，无Mock | 浏览器 → /tasks → 内置TaskWorker → React |
| 资源及跨角色锁检查 | 本轮服务、业务连接和锁已释放 | Uvicorn lifespan → Worker.stop → connection.close |

![真实B5回答](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7a-trend-20261113T065854Z/B5.png)

完整机器可读结论：[acceptance.json](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7a-trend-20261113T065854Z/acceptance.json)。本轮仅确认P7-A及该固定B5链路；未验收任意趋势问法、B4/B6、P6或其他后续阶段。
