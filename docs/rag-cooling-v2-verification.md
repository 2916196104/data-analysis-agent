# 慢冷证据核验修复与回归结果

2026-11-22。在商品评论助手的正式检索工具中，新的慢冷查询使用 `reported_cooling_speed_v2`。保存的 v1 结果按原策略回放。关键词候选池补入了室温、室内温度和温度下降速度的常见表达。关键词补查和新排序仍在离线评测范围内，本轮未部署服务。

## 原 5 条漏例

| 评论 | 原问题 | 修复后的表现 |
| --- | --- | --- |
| 12783 | 明确写了制冷慢，同时评价没有省电模式，被上下文核验拦住 | 能识别独立的功能评价，并按原始观点区间引用慢冷反馈 |
| 12938 | “制冷慢”与效果评价连写 | 可核验连写的完整表述 |
| 12590 | “制冷慢”后接程度补语 | 可核验带程度补语的明确慢冷表述 |
| 9446 | 买家写了制冷时长，紧接着说仍未凉下来 | 引文同时绑定制冷操作、时长和结果。全文中另起一句询问他人体验不会覆盖此前的亲测陈述 |
| 7363 | 描述温度下降太慢，原关键词池未命中 | 能按温度下降表达召回，结合同一引文内的空调设置核验 |

同一批 94 条评论、274 个观点、相同盲评和 Top 5 预算下，关键词组及关键词加新排序组都引用了 5/5 条参考评论，修复前均为 0/5。纯向量组为 0/5，样本中上述 5 条均没有现成向量。每批均测试 5 个商品，每商品有 3 个慢冷问法和 2 个对照问法。[上次结果](rag-cooling-three-arm-evaluation.md) · [本次回归明细](../outputs/rag-retrieval-quality/cooling-v2-regression-final/results.json)

## 留出评论与使用边界

| 批次 | 向量组引用正例 | 关键词组候选命中 / 引用正例 | 加新排序后候选命中 / 引用正例 |
| --- | --- | --- | --- |
| 94 条已知回归评论，5 条参考正例 | 0/5 | 5/5、5/5 | 5/5、5/5 |
| 63 条新留出评论，1 条参考正例 | 0/1 | 1/1、0/1 | 1/1、0/1 |

两批评论按评论 ID 和完整正文去重。新批次在首次排名前完成原文盲评，共 164 个有效观点，1 条直接支持、2 条不确定。两批均由 Codex 判读，尚无独立人工确认。两批合计 20 个对照用例在三个组的候选、顺序和引用一致，按现有标签未观察到慢冷误引。

新留出正例为评论 8826。买家分别描述运行半小时和三小时后的降温表现，最终进入了 3 种慢冷问法的关键词候选。正式核验仍返回 `semantic_not_verified`。当前修复解决了已列出的 5 条漏例，多时间点的间接表述仍有漏引。基于目前的独立样本，建议继续保留关键词补查和新排序的离线状态。[留出批次结果](../outputs/rag-retrieval-quality/cooling-v2-holdout-final/results.json) · [封存盲评](../outputs/rag-retrieval-quality/cooling-v2-holdout-20261121/blind-labels.json)

## 兼容性与复跑

回放 212 个既有候选观点，v1 可许可的 5 个观点全部保留。另核对否定、转述、条件、制热、故障、其他设备和单独测量值等边界。4 类相关测试共 109 项通过。使用本机 BGE-M3 和 PostgreSQL 只读快照，通过正式 Agent 完成 5 项真实主链路验收，覆盖公开引用、同评论多条件、证据不足与历史回放。正式入口的默认检索仍为向量 Top 5。[主链路验收](../outputs/rag-retrieval-quality/cooling-v2-native-final/acceptance.json)

从项目根目录可直接复跑最终留出或已知回归批次。每次使用新结果文件名。脚本校验代码、样本、标签和向量指纹，原始评测保存在独立目录。

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/evaluate_cooling_three_arms.py run --output outputs/rag-retrieval-quality/cooling-v2-regression-final --result-name replay-local.json
.\.venv\Scripts\python.exe -B -X utf8 scripts/evaluate_cooling_three_arms.py run --output outputs/rag-retrieval-quality/cooling-v2-holdout-final --result-name replay-local.json
```
