# 商品055：最小真实RAG验收

验收日期：2026-11-13（Asia/Taipei）。**业务链路 PASS，最终资源释放检查 PASS。**

本轮只运行一次生产 `run_review_agent()`，真实本地查询编码1次、PostgreSQL Top-5距离查询1次、付费LLM调用0次。首次测试运行器退出码为1：收尾脚本对Agent已经关闭的连接再次回滚，造成一条 `DatabaseError` 观测异常。原生Agent结果、来源认证和Validator均已成功；随后仅以只读查询确认实际连接、事务及进程已释放，没有重试编码或检索。原始FAIL记录及执行脚本原件完整保留。

## 范围与执行入口

- 问题：**商品100013588055有没有送货慢的反馈？**
- 来源/数据集：reviews_csv / reviews-csv；商品：100013588055。
- 日期：2021-09-11至2022-08-26，UTC；标注Profile：`1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304`。
- 只读角色：rag_audit_ro；业务库：reviews。标注Profile中的DeepSeek模型身份只是版本绑定，本轮没有调用该LLM。
- 直接调用现有生产Agent业务入口；未启动API、Worker、React或Redis，不计为本轮浏览器/队列验收，也没有任务数据库写入。
- 基于[上一轮范围记录](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/core-zero-20261113T015208Z/acceptance.json)固定范围；本轮向量与有效语料数量均来自当前只读快照，不把历史值直接当作当前数据。

本次已消费单次授权。实际执行命令如下，仅用于追溯，**不应再次执行**；同目录独占claim文件会拒绝重复运行。

```powershell
.venv/Scripts/python.exe -B outputs/mvp-real-acceptance/rag-minimal-20261113T031600Z/run_minimal_rag.py --execute-authorized
```

## 验收结果

| 检查项 | 结果 | 实际证据 |
|---|---|---|
| 本地缓存及模型加载 | PASS | 固定修订的8项缓存文件存在；独立进程以离线模式实际加载，约12.025秒；health不编码查询 |
| 向量空间兼容 | PASS | `BAAI/bge-m3`，修订`5617a9f61b028005a4858fdac845db406aefb181`，1024维、CLS、L2、float32、CPU、cosine；无查询指令或额外文本变换；最大512 tokens且拒绝截断 |
| 实际检索 | PASS | Query Embedding恰好1次，Top-5恰好1次；SQL使用`<=>`精确cosine距离排序，无索引重建或向量写入 |
| 来源与引用 | PASS | 当前有效观点与快照重新关联，候选回读完整评论及全部有效观点；核对review/annotation/版本、文本hash、引用位置及来源 |
| 事实许可 | PASS | 目标delivery_slow；5个候选中许可3个观点，来自2条不同评论；完整评论只作否决，不借其他文本补充事实 |
| Validator与最终回答 | PASS | 原生Validator实际执行并通过，包括查询目标匹配及5处候选原文引用检查；最终只发布3个许可引用，模板回答、0模型调用 |
| task_acceptance | N/A | 原生semantic_evidence路径没有旧式request_goal/task_goal或task_acceptance字段；以RuntimeQueryContract和原生Validator查询目标检查验收，未伪造passed对象 |
| 最终清理 | PASS | 两个业务会话PID消失，事务结束；编码进程正常退出0，项目端口及跨角色目标Worker锁为空 |

Query向量维度为1024，L2范数为1.0000000434；实际查询编码约524.306毫秒，原生Agent业务阶段约1.357秒。模型加载时间单列，不计成第二次编码。向量只用于本次查询，保存其摘要而未写入业务向量表。

## 覆盖边界与候选

当前窗口共510条评论、505条成功标注评论、1091个有效观点。认证可用向量200个，观点覆盖约18.33%；仍有891个观点未覆盖。当前兼容向量未发现过期快照，范数范围为0.9999998928至1.0000001019。这不是全评论检索或总体发生率分析。

空间ID：`embedding-space-v1:11e6754662dbd13fc56c6e624bc9416c1c2766f962787267fd4a57b1fa8d622e`；文本构造版本：`quote-utf8-v1`。

| Rank | review_id / annotation_id | 精确观点原文 | cosine距离 | 1−距离 | 字符区间 | 事实许可 |
|---|---|---|---:|---:|---|---|
| 1 | 9958 / 5489 | 送货很慢 | 0.114922 | 0.885078 | [0, 4) | 许可 |
| 2 | 9993 / 5524 | 物流太慢了 | 0.162946 | 0.837054 | [47, 52) | 许可 |
| 3 | 9949 / 5479 | 送货到时挺快的 | 0.208482 | 0.791518 | [5, 12) | 不许可 |
| 4 | 9959 / 5493 | 物流送货态度太差 | 0.256435 | 0.743565 | [0, 8) | 不许可 |
| 5 | 9993 / 5524 | 商品到货时间太长了，等了八天才到货 | 0.256801 | 0.743199 | [0, 17) | 许可 |

排名第3的“送货到时挺快的”不能支持送货慢。排名第4的观点仅描述送货态度，不能借完整评论另一部分的“时效性太慢”替该观点获得许可。这两项均保留为unknown/semantic_not_verified，不作反向事实推断。

[candidates.json](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/rag-minimal-20261113T031600Z/candidates.json)保存稳定观点key、来源/数据集/商品、annotation及人工修订身份、日期、精确引用、文件SHA与行号、评论文本SHA、快照SHA、相似度和逐候选许可判断。相似度不等于事实支持；本次3/5许可数不作为人工相关性评分或召回率。

## 原生最终回答

> 在本次检索到的评论中，有买家明确报告了以下情况。
> 
> 评论 #9958（2022-07-13）写道：“送货很慢” [r1]
> 
> 评论 #9993（2022-07-27）写道：“物流太慢了” [r1]
> 
> 评论 #9993（2022-07-27）写道：“商品到货时间太长了，等了八天才到货” [r1]

- 仅描述本次检索且可明确核验的买家自述，不代表客观性能、总体比例或全部评论。
- 完整评论仅作上下文否决；相似度不代表事实支持。未识别表达属于语义覆盖不足。

样本说明：语义Top-K候选不作为统计样本或总体分母。

## 证据 → 结论 → 调用路径

| 证据 | 结论 | 路径/定位 |
|---|---|---|
| E1：[preflight.json](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/rag-minimal-20261113T031600Z/preflight.json) | 缓存可离线加载，当前向量空间及来源范围匹配 | LocalBgeM3Embedder → 当前有效语料与向量元数据 |
| E2：[execution-native.json](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/rag-minimal-20261113T031600Z/execution-native.json) | 真实Agent、Router、Planner、Tools、Validator和回答已运行 | 用户问题 → semantic_evidence → search_semantic_evidence → 本地编码 → PostgreSQL → 来源认证 → 事实许可 → Validator → 模板回答 |
| E3：[candidates.json](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/rag-minimal-20261113T031600Z/candidates.json) | 仅3个许可观点进入最终回答 | 完整评论否决、精确引用及permitted_keys绑定 |
| E4：[cleanup-final.json](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/rag-minimal-20261113T031600Z/cleanup-final.json) | 本轮资源已释放，源码及历史任务库摘要未变 | PostgreSQL会话/事务、Windows进程/端口、跨角色Worker锁复核 |

关键生产代码：[Router](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_intent.py:326)、[真实检索及证据判断](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_retrieval.py:142)、[pgvector排序](E:/AllProject/data-analysis-agent/src/data_analysis_agent/reviews/embedding_store.py:303)、[独立来源验证](E:/AllProject/data-analysis-agent/src/data_analysis_agent/reviews/semantic_retrieval.py:220)、[Validator](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_validation.py:106)、[回答绑定](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_answers.py:394)。生产代码、模型配置、权限、索引、事实许可、Oracle/历史测试数据均未修改。

## 收尾观测异常与限制

[首次记录](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/rag-minimal-20261113T031600Z/acceptance.initial.json)保留FAIL及DatabaseError；[实际执行脚本原件](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/rag-minimal-20261113T031600Z/run_minimal_rag.executed.py)保留首次代码。异常来自记录器重复回滚已关闭连接，不是业务数据库查询、来源认证或Validator失败。已仅在测试运行器中增加closed检查，**没有进行修复后真实重跑**。最终结论依据原生关闭标记及独立只读复核，不删除原始失败记录。

[最终JSON](E:/AllProject/data-analysis-agent/outputs/mvp-real-acceptance/rag-minimal-20261113T031600Z/acceptance-final.json)分别记录业务PASS、首次运行器FAIL、最终资源检查PASS，以及实际调用次数。原生只读业务阶段使用1条连接、2个顺序Repeatable Read/Read Only快照；预检另用1条只读连接。最终诊断连接也已关闭。没有新增Embedding、同步、业务写入或收费请求。

本次只证明该固定问题和当前Top-5链路可用；不证明未索引观点已被检索、任意语义问法有效或全量RAG质量。本轮结束，不自动执行其他问题。
