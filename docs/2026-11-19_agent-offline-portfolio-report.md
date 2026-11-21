# 观数 Agent：冻结样例离线评测

**结果：首跑与复跑均为 15/15。** 本轮验证合成评论上的确定性查询、原文引用和拒答边界。没有访问业务库、读取项目 `.env` 或调用线上模型；**真实 RAG 未测**。

## 样例与复现

复用 [正向趋势](../tests/test_review_positive_trend.py)、[商品对比](../tests/test_review_positive_comparison.py)、[细分主题](../tests/test_review_topic_statistics.py)、[Phase 7 原文题](../scripts/phase7_cases.py)及原评测的越界题。两个 SQLite 样例共 34 条虚构评论，其中 22 条为题集使用的 `reviews_csv / reviews-csv` 来源，另外 12 条来自另一来源。标注由既有测试写入，不调用标注模型；商品 ID 只作标识，不能将样例结论用到真实商品。

从仓库根目录运行（使用项目现有 Python 环境；每次指定一个未存在的输出目录）：

```powershell
./.venv/Scripts/python.exe -B scripts/evaluate_agent_offline.py --output outputs/agent-offline-portfolio-repro1
./.venv/Scripts/python.exe -B scripts/evaluate_agent_offline.py --freeze outputs/agent-offline-portfolio-repro1/freeze.json --output outputs/agent-offline-portfolio-repro2
```

[题集与独立预期](../tests/fixtures/agent_offline_portfolio_v1.json)在首跑前确定。运行器先封存样例的评论、标注、观点和范围，再执行第一题。复跑在执行前对比冻结清单，不一致则中止；本次两轮冻结清单和数据逐字节一致，评分明细（含预期、实际与查询次数）逐题一致，未按结果回写预期。代码基线为 `18191b9` 加工作区已有修改；生产 Python 源码树摘要见 `evaluation.json`，不是干净提交的评测声明。

执行链为：样例库 → 真实任务参数校验、入队与领取 → 同步执行真实 Worker → SQLite 查询、校验与发布 → 独立比对。多轮题使用真实保存任务恢复历史，而不是把上一轮预期直接注入。网络、PostgreSQL、外部进程、模型及真实检索入口设有拦截；两轮无禁用入口触发，样例业务库前后文件摘要一致。只写临时任务库和本地评测产物。未测 HTTP、浏览器、并发和 Redis。

## 评分结果

| 指标 | 首跑与复跑（各轮） | 验收依据 |
| --- | --- | --- |
| 统计口径 | **7/7 题** | 校对月份、计数、分母名称与值、空分母和真实零值、商品隔离、窗口百分点差、主题统计的范围与正负向追问 |
| SQL 原文引用 | **2/2 题，5/5 条引用观点有支持** | 回查冻结语料：评论、商品、窗口、来源、标注版本、原文切片、方向及维度均须匹配独立预期 |
| 无匹配原文 | **1/1 题** | 查询后明确说明证据不足，不发布引用；不把没找到当作没有问题 |
| 越界拒答 | **5/5 题** | 未知商品、销量预测、客观故障根因、删除请求和超量原文均未执行分析工具查询，未发布统计或引用。删除请求的提示仍较泛，没有明确说明不能删数据 |
| 模型调用 | **0 次** | 意图、规划、主题、回答四类尝试均为 0；总计 20 次分析工具查询，不含入队/读取任务和产品目录校验等 SQL，也不是数据库语句总数 |

例如，1 月物流好评按含正向观点评论计为 **1/2 = 50.0%**，改用成功标注评论为分母后是 **1/3 = 33.3%**；3 月分别为**无占比**及 **0/1 = 0.0%**。这几种结果均单独核对，不能互换。引用指标只覆盖两道明确要求原文的固定题，不是开放式回答的普遍语义支持率。

## 耗时

| 口径 | 首跑中位 / P95 | 复跑中位 / P95 |
| --- | ---: | ---: |
| Agent 内部（路由、查询、校验、回答） | 78.4 / 142.8 ms | 72.0 / 96.1 ms |
| Worker（含恢复上下文、处理及保存结果） | 140.5 / 238.4 ms | 133.9 / 164.7 ms |

每轮顺序执行 15 题，指标含提前拒答；不计准备样例、提交排队、页面、真实模型和网络时间。P95 采用最近秩，这个样本量下即单轮最大值，只供本机离线基线参考。

## 未验证：真实 RAG

未运行 BGE-M3 实际查询编码、pgvector 检索、真实候选覆盖／召回、原文回连及人工语义评分；**无本轮真实 RAG 通过率、引用支持率或耗时**。历史 [055 单商品有限验收](2026-11-13_rag-mvp-055-minimal-real-acceptance-031600Z.md) 和 [冻结 RAG 整体报告](rag-mvp-final-acceptance.md) 的结论不变：后者记录 5 通过 / 3 失败 / 2 未判定，整体未通过。此轮也不验证线上模型表现，未做新旧版本对比。

## 留存证据

- 本机 `outputs/agent-offline-portfolio-20261119-run1/` 和 `outputs/agent-offline-portfolio-20261119-run2/` 各含 `freeze.json`（题集、数据及范围绑定）、`corpus.json`（合成语料）、`S01.json` 等原始任务结果、`evaluation.json`（逐项评分和耗时）。
- 首跑 `evaluation.json` SHA-256：`3c823f643fd6590e8e8e701c2253f2d998a06ae99f56478b1114aa013d2d778d`；复跑：`acd23ea9ff85ef721a8e3c5bc6ac64dc1abccdef83e103ccf5ab447e8084bae7`。
- 逻辑语料 SHA-256：`98bf7dd0dfa2a41682b1f000994f6083704dc50dd144d1628ffbfa8eb64e5620`。
