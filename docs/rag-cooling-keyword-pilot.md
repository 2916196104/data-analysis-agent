# RAG 默认链路与慢冷关键词试点

## 当前交付状态

本地提交 `4925ca4` 已通过「向量召回＋原文核验」页面验收，可以用于日常评论问答。`055` 验证了原文引用、同一评论多条件和历史回放。`053` 验证了证据不足提示。默认候选上限为 5，关键词补查开关 `RAG_COOLING_KEYWORD_FALLBACK` 保持为 `0`。 [页面验收](../outputs/rag-retrieval-quality/native-page-release-v4-20261122/report.json) · [开关配置](../src/data_analysis_agent/core/config.py)

慢冷核验回归覆盖 34 条人审引文，3 条正例全部通过，31 条反例全部拦截。本地开关对照在商品 `100019386660` 的 2021-03-20 至 2021-09-10 范围内，验证了 5 条关键词候选可以补回 `10769`、`12621`、`12886` 三条已确认的引用。这三条保留为回归样本。 [核验回归](../outputs/rag-retrieval-quality/cooling-keyword-boundary-v4-20261122/comparison.json) · [开关对照](../outputs/rag-retrieval-quality/cooling-keyword-top5-dual-20261122/toggle-verification.json)

本机 24 个空调商品中，符合当前规则的 20 条有效候选都已参与过评测，独立新增候选为 0。现有原文盲审包有 13 条，其中 1 条通过直接核验，仍需真实召回和人工确认。关键词试点在取得新样本后恢复评测。 [候选盘点](../outputs/rag-retrieval-quality/cooling-keyword-independent-20261122/pool.json) · [原文复验](../outputs/rag-retrieval-quality/cooling-keyword-independent-20261122/raw-summary.json)

## 记录实际漏召回

当页面提示证据不足，而你在所选商品和日期范围内找到了明确支持原问题的评论时，复制下方模板，将记录保存在本机。任务 ID 或引用链接有助于回看当时的范围和候选。

```text
原问题：
商品 ID：
查询日期范围：
任务 ID 或引用链接（可选）：
页面答复或提示：
评论 ID（如有）：
评论发布时间：
完整评论原文：
支持原问题的原文片段：
```

复验时先按评论 ID、全文和原文片段查重，冻结商品、日期、标注版本与问法。对同一批请求分别关闭和开启补查，每组最多 5 个候选，使用同一版原文核验。人工确认最终引文是否支持买家报告本商品制冷慢。扩大启用的门槛为 **至少新增 2 条来自不同评论的正确引用、反例零误引、既有正确引用零丢失**。

## 早期试点记录

### 人工裁决

用户确认评论 8826 可作为带时间的间接慢冷证据。公开回答应保留买家亲自报告的运行时间和降温结果。该条许可引文的原文区间为 `[87, 121)`，维修师傅的后续表述在区间之外。

> 空调开16度，开了大半个小时，房间没明显制冷效果，三小时降温效果一般

新版核验器要求同一引文明确关联本机设置、先后两个运行时间点以及各自的降温结果。完整评论用于复核转述、否定、条件和设备指向。旧的 `reported_cooling_speed_v2` 对 8826 的判定仍按历史规则回放。真实原文已单独通过 `reported_cooling_speed_v3` 的原始观点、原文区间和引用许可核对。

用户同时确认商品 `100019386660` 的评论 12590 可作为明确慢冷反馈，认可的完整引文为“制冷慢的一塌糊涂”。以下正式 Agent 验收按这条人审引用通过。

### 正式 Agent 请求

在商品 `100019386660` 的完整现有日期范围 2021-03-20 至 2022-08-26 运行“有没有制冷很慢的反馈？”。完整范围含 4,338 条评论、2,457 个当前有效观点和 0 条可用向量。验收前后的只读存量指纹一致。

| 实验条件 | 正式结果 |
| --- | --- |
| 关闭补查 | 0 条候选，0 条引用 |
| 开启补查 | 5 条候选，5 条通过正式核验；人工确认的评论 12590 排第 3 |
| 从磁盘读取任务 JSON 再回放 | 公开回答与引用逐字段一致 |

12590 的来源字段为 `keyword`，无向量距离，原文区间为 `[0, 8)`。验收对照的是人工批准的这一条引用。其余 4 条通过程序核验。8826 单独验证了有资格引用的规则和原文跨度。验收的正式请求以 12590 为目标。[验收结果](../outputs/rag-retrieval-quality/cooling-keyword-pilot-20261122/acceptance.json) · [任务回放记录](../outputs/rag-retrieval-quality/cooling-keyword-pilot-20261122/task.json)

### 接入方式与复跑

`RAG_COOLING_KEYWORD_FALLBACK` 默认是 `0`。设置为 `1` 时，只有单一慢冷问法，且向量候选完成核验后未产生引用，才扫描同一只读快照内的当前有效观点。候选按引文是否符合慢冷表述筛选，再按评论发布时间倒序取样，使用稳定观点键解决时间并列。仍以 5 条作为最终候选上限。结果记录向量基线、通道和通道内排名，并重算许可后生成引用。

早期试点阶段的 111 项相关测试和默认关闭时的 5 条真实主链路用例通过。以下命令复跑该试点请求，结果写入指定的空目录。

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/accept_review_rag_keyword.py --output outputs/rag-retrieval-quality/cooling-keyword-pilot-replay
```

[正式验收脚本](../scripts/accept_review_rag_keyword.py) · [既有主链路回归](../outputs/rag-retrieval-quality/cooling-keyword-pilot-native-20261122/acceptance.json)
