# P7-B：多轮客服负面观点追问真实验收

**结论：PASS，限固定两轮opinion_breakdown业务目标。** State/Router/Planner、真实观点SQL、来源和引用、回答、Validator/task_acceptance及React/API/Worker完整闭环均已通过。完整语义主题分析未执行，不在本次PASS之内。

本轮已获得用户明确授权“批准，两轮各一次”，解除[预检报告](E:/AllProject/data-analysis-agent/docs/2026-11-13_P7-B-followup-preflight-report.md)的提交授权阻断。原BLOCKED预检记录保留，不覆盖；实际两题各提交一次，均无自动重试。首轮只用于建立本轮上下文，不重新进行A1独立验收。日期：2026-11-13。

## 1. 真实两轮记录

共同conversation_id：`7340550f-4ead-4c64-bddb-37b396ddeab4`。

| 项目 | 首轮上下文锚点 | 第二轮客服追问 |
|---|---|---|
| 用户问题 | 商品100013588055的差评主要集中在哪些方面？ | 客服方面具体有什么问题？ |
| task_id | e4ccab31d9d0483a92e9fd2a39b9b497 | 7cd2c141fdff40979591bf464f19a638 |
| task / result状态 | succeeded / ok | succeeded / ok |
| RequestGoal | statistics / negative / summary | opinion_content / service / negative |
| Router | aspect_summary / negative | opinion_breakdown / service / negative / drill_down |
| Planner | get_top_issues | get_opinion_breakdown(aspect=service,sentiment=negative,topic=null) |
| 实际Tools | get_analysis_context → get_top_issues | get_analysis_context → get_opinion_breakdown |
| 工具查询计数 | 2 | 2 |
| 任务内PostgreSQL语句 | 29 | 57 |
| Validator / task_acceptance | passed / passed，complete=true | passed / passed，complete=true |
| 模型、编码 | 0 / 0 | 0 / 0 |
| Agent业务阶段耗时 | 846.6ms | 3055.1ms |

SQL语句数包括范围、元数据和只读事务语句，不等同工具调用次数。两轮实际来源和商品均为reviews_csv / reviews-csv / 100013588055，UTC 2021-09-11—2022-08-26，既有标注Profile27。模型身份仅用于绑定既有标注，未调用模型。

## 2. State继承证据

第一轮result.analysis_state是执行前的空历史投影（ranking_basis=null，pending_goal=null）；任务成功落库并保存原生完整性摘要后，`restore_ranking`实际恢复可信负面排名。其分母为505条成功标注评论，客服165条，其他维度为效果66、物流53、质量33、价格30、包装4，维度可能重叠，不相加。

第二轮POST的context_task_id精确指向首轮，conversation_id相同。Worker在执行前从原生任务库恢复排名；第二轮analysis_state与记录的state-before-second逐字段一致，ranking_basis.source.task_id为首轮ID，scope/source/日期/标注身份一致，pending_goal=null。没有从浏览器重编码后的JSON或失败结果恢复排名。

证据：[B1原生任务](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/B1-task-native.json)、[可信排名恢复](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/B1-reviewed.json)、[第二轮执行前State](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/state-before-second.json)、[B2原生任务](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/B2-task-native.json)。两条原生任务保存的完整性摘要均已实际复核。

## 3. 实际观点内容、数量与引用

首轮范围510条评论、505条成功标注评论。第二轮SQL匹配**165条客服负面评论、234条观点**，与首轮客服负面去重评论数165一致。

SQL按观点原文+情感精确归组，返回前10组，公开回答展示前3组及其原文依据。不能据此称为10个语义主题；总不同原文组数并未查询，也没有编造该数量。

| 已发布原文组（不是语义主题） | 去重评论数 | 观点数 | 占匹配165条评论 | 代表review_id |
|---|---:|---:|---:|---:|
| 加长铜管，为什么要从我别的上面拆，还要收我的费用 | 3 | 3 | 1.8% | 9948 |
| 电话一大堆 | 2 | 2 | 1.2% | 10110 |
| 售后太差 | 2 | 2 | 1.2% | 9979 |

同一评论可进入多个组；比例分母为165条匹配客服负面评论，不是505条成功标注评论，也不是234条观点。原文“电话一大堆”只按原话发布，没有推断成电话骚扰、回复慢或其他未获支持的细分问题。

| review_id | annotation_id | 精确引用区间 | 源CSV行 |
|---|---:|---|---|
| 9948 | 5478 | [27, 51) | line=4292 |
| 10110 | 5641 | [21, 26) | line=4471 |
| 9979 | 5510 | [0, 4) | line=4323 |

三条引用的source=reviews_csv、dataset_version=reviews-csv、product_id=100013588055；文件SHA为`90d5061b67a724ee7584ea01e78d6115865294470f7a7ae29597c3a7a94acccf`。日期、模型标注身份、原文位置和引用中的annotation_id均与真实SQL原始结果相符。它们是已有标注下的买家原文，不是客观原因证明或人工确认主题。

见[原始工具输出](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/B2-tool-output.json)、[引用绑定](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/B2-reference-bindings.json)、[首轮SQL](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/B1-sql-executions.json)、[第二轮SQL](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/B2-sql-executions.json)。

## 4. React实际发布的第二轮回答

```text
当前所选商品的已标注评论中，买家对客服的差评提到“加长铜管，为什么要从我别的上面拆，还要收我的费用”、“电话一大堆”、“售后太差”。

观点原文“加长铜管，为什么要从我别的上面拆，还要收我的费用”：3 条评论、3 条观点，占本次匹配评论 1.8%（分母 165 条），负面；评论依据：评论 #9948。

观点原文“电话一大堆”：2 条评论、2 条观点，占本次匹配评论 1.2%（分母 165 条），负面；评论依据：评论 #10110。

观点原文“售后太差”：2 条评论、2 条观点，占本次匹配评论 1.2%（分母 165 条），负面；评论依据：评论 #9979。

本次分析范围共 510 条评论，基于 505 条成功标注评论。

现有标注只有六个上级维度；以上按观点原文和情感精确归组，同义表述未合并，不是完整细分主题排名。同一评论可有多条观点并进入多个组，评论数与观点数分别统计。
```

这是原始opinion_content目标的有界内容回答，且没有受控澄清或系统失败。它不是覆盖全部观点的完整主题归纳。原有RequestGoal就是观点内容，并未删去主题要求来使验收通过；另外用例证明完整themes目标不能由opinion_breakdown替代。

## 5. 分项结论

| 验收项 | 结论 | 实际依据 |
|---|---|---|
| State/Router/Planner | PASS | 同会话、首轮可信排名、负面/service/drill_down、观点工具计划 |
| 真实观点SQL | PASS | 原生get_opinion_breakdown执行，165评论/234观点，完整固定范围 |
| 来源与事实证据 | PASS | 原文区间、review_id/annotation_id、源文件位置及标注身份绑定；只发布原话与确定性计数 |
| 回答完整性 | PASS（限原opinion_content契约） | 前3组有依据的原话、计数与明确限制；没有伪称完整主题 |
| Validator及task_acceptance | PASS | 两轮均实际运行，第二轮原文/继承/范围/内容产物检查全部通过 |
| React/API/Worker | PASS | 真实Chrome两轮各POST一次，同会话，最终回答可见，页面和HTTP无错误 |
| 完整主题分析 | NOT_REQUESTED / NOT_EXECUTED | 未执行主题模型或完整语义归并，不在本轮PASS内 |

单主题与多主题、partial/failed/无数据保护仅作离线Mock验证。多topic fact的aspect_drilldown仍可能选择LLM，当前零付费运行器会在执行前阻断该工具，不通过改用少量示例绕开主题要求。没有运行B4。

## 6. 最小代码修改及回归

四个生产文件共7行新增、3行替换：review_answers.py为纯观点事实选择模板；review_results.py带出已有annotation_id；review_acceptance.py及review_semantics.py共同识别明确“具体有什么差评”的内容请求，修复正向历史后切换负面时的目标冲突。没有修改State、Planner、SQL口径、Validator、主题门槛、事实许可、人工裁决或模型身份。

- 专项20/20 PASS。
- 后端289项，287通过、2项既存失败、0新增失败；未修改HEAD同集为284通过/5失败，修复了本轮复现的3项。
- 两项既存失败仍为旧Amazon Worker样本被P1来源边界拒绝；保留失败断言与完整HEAD对照，不改写旧测试。
- 前端282/282 PASS（maxWorkers=2，超时未修改）；首次与构建并行运行的3条10秒超时日志保留。
- TypeScript/Vite构建PASS，保留原有大分块警告；git diff --check通过。

详见[预检与完整测试记录](E:/AllProject/data-analysis-agent/docs/2026-11-13_P7-B-followup-preflight-report.md)、[后端结果](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/offline.json)、[HEAD对照](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/baseline.json)、[前端结果](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/frontend-final.json)、[diff](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/changes.diff)、[修改文件SHA](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/source-files.json)。

## 7. 写入边界与资源清理

付费模型请求0，usage没有供应商响应，本批实际模型费用¥0；查询编码0、Embedding同步0、PostgreSQL业务写入0。所有已观测业务SQL为只读，模型构造器/外部HTTP/编码/写SQL调用前拦截未触发，观测异常0。没有启动Redis消费者、修改权限或调整连接额度。

本批必要写入仅为新SQLite初始化、两条任务/结果和Worker心跳状态，以及本地日志、截图及报告。旧A1、C1、P7-A任务库SHA全部保持一致。观察连接峰值5/8（50ms采样，含观察连接）。

API通过原生Uvicorn生命周期及Worker.stop正常退出0；Vite对本轮控制台发送Ctrl+C（中断退出码1），未强杀；观察器通过停止标记退出0。最终8011/5184监听0、本轮进程0、rag_audit_ro会话0、跨角色目标锁0，事务随连接关闭结束；连接额度仍为8，未自动恢复。

见[清理记录](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/cleanup.json)、[系统资源](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/resources-after.json)、[跨角色连接和锁](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/after-connections.json)。

## 8. 证据链与截图

| 证据 | 支持的结论 | 路径 |
|---|---|---|
| 两轮原生任务、首轮签名排名、执行前State | 可信同会话继承，无串商品/数据集/情感 | load_analysis_context → Router → Planner |
| SQL轨迹、工具结果与精确引用 | 真实只读观点查询、计数与证据 | get_opinion_breakdown → get_evidence |
| validation/task_acceptance、模板正文 | 原始内容目标完整通过、未伪造主题 | Validator → task acceptance → template/assembler |
| 浏览器POST、截图和回答 | 真实React/API/Worker闭环，两次授权已消费 | React → API → Worker → React |
| 关闭日志、资源与跨角色锁检查 | 本轮资源释放，历史任务保留 | Uvicorn/Worker.stop → connection.close |

![首轮可信上下文锚点](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/B1.png)

![第二轮客服观点回答](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/B2.png)

机器可读最终结果：[acceptance.json](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/p7b-followup-20261113T073748Z/acceptance.json)。本轮停止，不开展P7-C/P7-D、B4、向量补全或Amazon扩展。
