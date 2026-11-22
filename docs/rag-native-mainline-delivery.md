# 商品评论 RAG 主链路交付记录

2026-11-21，使用本机 BGE-M3 和业务 PostgreSQL，通过正式 Agent 入口完成 5 项只读验收。当时交付向量 Top 5 与原文核验。每条公开引用都核对了所属商品、原文位置和事实许可。

## 验收范围

数据来源为 `reviews_csv`，数据集版本为 `reviews-csv`。评论日期为 UTC `2021-09-11` 至 `2022-08-26`，包含两端。有效观点按现有标注模型 `Pro/deepseek-ai/DeepSeek-V3.2` 和标注版本 `review-opinions-v1-e61de599683cba6703c2f61e85125d0be4627c2763440486aa717e4bc884036e` 计算。

| 商品 | 评论数 | 有效观点 | 可用向量 | 向量覆盖率 |
| --- | ---: | ---: | ---: | ---: |
| `100013588055` | 510 | 1,091 | 1,091 | 100% |
| `100013588053` | 1,699 | 3,591 | 200 | 5.57% |

可用向量经当前有效观点、标注身份、向量空间、原文构建版本和快照校验。两款商品在验收前后的清单摘要一致。

## 真实 Agent 验收

| 商品 | 问法 | 结果 |
| --- | --- | --- |
| 055 | 有没有送货慢的反馈？ | 从 5 条候选中引用评论 `9958`。 |
| 055 | 有没有同一条评论既说制冷快又说噪音小的反馈？ | 引用评论 `10200`，两个事实都绑定到该评论。 |
| 055 | 有没有同一条评论既说物流很快又说包装破了的反馈？ | 本次候选中没有一条评论同时通过两项核验，返回 0 条引用。 |
| 055 | 有没有制冷很慢的反馈？ | 慢冷规则完成核验，返回 0 条引用并提示本次证据不足。 |
| 053 | 有没有制冷速度比较慢的反馈？ | 检索到 5 条候选，0 条获得引用许可。回答明确提示当前范围的全部评论是否有相关反馈仍无法判断。 |

每项均检查了原问、工具顺序、Top 5、只读事务、查询范围、来源校验、引用绑定、公开回答和历史回放。全部通过。真实链路使用本地向量编码和确定性回答模板，外部模型调用为 0。

原文核验已覆盖明确的慢冷表述。多条件请求沿用同一评论的事实绑定，证据不足提示明确限定到本次检索。关键词补召回、耗时核验原型和新关键词排序留在离线评测目录。

## 测试与复验

- 主链路相关测试共 245 项。其中 228 项通过，17 项因未配置隔离测试库 `TEST_PGVECTOR_ADMIN_URL` 跳过。本机业务库的 5 项只读验收单独通过。
- 134 个 Python 文件通过源代码编译检查，Git 差异通过空白检查。
- 本记录覆盖本机后端和工作区代码。当前改动仍待提交，服务部署另行验收。

在项目根目录使用现有 PostgreSQL 配置及 BGE-M3 缓存复验。输出目录应使用新名称。

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/accept_review_rag_mainline.py --execute-authorized --output outputs/rag-retrieval-quality/native-mainline-recheck
```

[验收脚本](../scripts/accept_review_rag_mainline.py) · [真实运行记录](../outputs/rag-retrieval-quality/native-mainline-acceptance-20261121/acceptance.json) · [公开回答出口](../src/data_analysis_agent/agents/review_answers.py)

## 默认开启的多 Query 与 Rerank

评论分析入口默认开启多 Query 和 Rerank，所有候选继续按原问题和完整评论核验。要复现原先的纯向量排序，可把 `RAG_MULTI_QUERY` 和 `RAG_CITATION_RERANK` 都设为 `0` 后重启本地服务。 [配置示例](../.env.example)

| 开关 | 开启后的行为 |
| --- | --- |
| `RAG_MULTI_QUERY` | 仅扩展明确的单一慢冷问题。原问和最多两个固定同义问法共用 5 条召回预算，同一个只读快照内按观点键去重。所有候选仍按原问题核验。扩展编码失败时使用原问 Top 5。 |
| `RAG_CITATION_RERANK` | 完成原文核验后，根据问法与引文的主题和线索调整展示顺序。同一条评论的已许可引文成组展示。 |
| `RAG_COOLING_KEYWORD_FALLBACK` | 关键词补查试点保持为 `0`，等独立样本达到验收门槛再评估。 |

多 Query 正常生效时，任务记录各问法的候选键，回放按保存的问法、预算和去重规则复验。在同一个慢冷请求中同时设置多 Query 和关键词补查时，使用多 Query。Rerank 只读取核验通过的引文，在历史回放中复现当时的展示顺序。[慢冷补查现状](rag-cooling-keyword-pilot.md)

早期接入验收覆盖两项关闭时的 5 个真实 Agent 用例和开启时的 3 个真实页面用例。`053` 的慢冷问题分配 `2 + 2 + 1` 个召回名额，去重后核验 3 条候选，并在历史中恢复证据不足提示。`055` 展示原文并恢复历史引用。 [关闭时的对照](../outputs/rag-retrieval-quality/native-mainline-post-integration-20261122/acceptance.json) · [早期页面验收](../outputs/rag-retrieval-quality/native-page-multi-query-rerank-20261122/report.json)

### 默认开启后的实际回答对照

对 `055` 和 `053` 的 14 道固定问题，使用同一批数据分别开启和关闭两项能力。13 道证据题中，两组均有 5 道返回引文，共 9 条。4 道慢冷题的候选集合发生变化。最终引用、展示顺序和公开回答一致，统计题的指标一致。

本机预热后的单轮测量中，发生扩展的 4 道题每对耗时差的中位数为 **+193 ms**。这批问题的最终回答质量未观察到提升。 [对照摘要](../outputs/rag-retrieval-quality/rag-default-query-rerank-20261122/summary.json) · [逐题回答](../outputs/rag-retrieval-quality/rag-default-query-rerank-20261122/results.json)

以本机默认配置启动后的 3 个页面用例均通过，覆盖 `055` 的原文引用和同一评论多条件，以及 `053` 的慢冷扩展。两款商品的历史结果均可恢复。 [默认启动页面验收](../outputs/rag-retrieval-quality/rag-default-query-rerank-20261122/page/report.json)
