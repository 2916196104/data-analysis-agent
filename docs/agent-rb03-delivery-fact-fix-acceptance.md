# P2-C1：RB03 配送慢事实识别修复

日期：2026-11-11，Asia/Taipei。**本地事实识别修复及已执行的离线回归通过；完整 62 轮和原 762 项兼容回归本轮未执行。** 用户禁止运行完整 Agent 和 Worker，而这两组历史验收包含相应路径，不能把纯评分器或部分兼容测试称作完整回归。

## 根因与修改

[review_reported_facts.py](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_reported_facts.py:10) 的 `delivery_slow` 原先仅接受少数完整词形，不能识别带句末助词的物流慢、明确到货时间长，以及明确“等待若干天才到货”的表达。`quote_facts()` 逐句调用 `fact()`，因此原始观点一旦未命中，也不能形成 `assess()` 所需事实。

本轮仅修改该模块：

- 扩展 `delivery_slow` 有限完整词形，覆盖明确的物流、配送、送货、到货、快递到货主体，配送速度、到货时间显著长以及带明确到货对象和“才”的等待时长，并允许有限句末助词。等待时间是该买家的叙述，不是客观配送时效或整体统计。
- 保留 [context_ok()](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_reported_facts.py:71) 原有全文否定、纠正、相反事实和未知配送上下文的否决。新等待句式在故障注入中曾把退货/换货后的到货等待识别为初始配送慢，因此对这一等待句式增加针对退货、退回、退件、返修、寄回及换货语境的保守否决；其他配送词形不因该新增检查直接受影响。
- `target()`、`quote_facts()` 和 `assess()` 的实现未改；`quote_facts()` 继续按句识别，`assess()` 仍要求原观点和上下文通过。没有修改生产来源认证或 Validator。

未按商品、评论或候选编号添加规则。到货后安装慢、安装师傅来得慢、等待后才安装、无对象的等待、否定和纠正均不直接产生配送慢许可。

## 真实历史候选：修复前后

本段仅是 **EXPLORATORY_DEBUG**。使用原固定历史候选 EV08、EV15 的完整评论及三个原始观点，调用本地纯函数；没有调用生产 Validator、真实检索、数据库或外部模型。历史来源为 `history_unproven`；函数产生的 key 不等于经过来源认证的生产发布许可。

| 原始观点 | 修复前 `fact` | 修复前 `quote_facts` | 修复前 `context_ok` | 修复前判断 | 修复后 `fact` | 修复后 `quote_facts` | 修复后 `context_ok` | 修复后判断 |
|---|---|---|---|---|---|---|---|---|
| EV08-1 | `delivery_slow` | `delivery_slow` | true | `direct_report` | `delivery_slow` | `delivery_slow` | true | `direct_report` |
| EV15-1 | null | 空 | false | `unknown` / `semantic_not_verified` | null | `delivery_slow` | true | `direct_report` |
| EV15-2 | null | 空 | false | `unknown` / `semantic_not_verified` | `delivery_slow` | `delivery_slow` | true | `direct_report` |

EV15-1 是包含多个分句的原观点：`fact()` 对完整多分句仍返回 null，`quote_facts()` 分句后识别到配送事实；没有将多个评论拼接。两次总体 `assess()` 均为 `sufficient` / `explicit_report_only`：修复前由 EV08 支持，修复后三个原观点均被该纯函数标记 `direct_report`。总体 sufficient 不代表当前来源认证、生产发布或全体评论通过。

修复前的 EV15 两处 `context_ok()` 均为 false；在配送事实词形获得识别后，同一全文中的相关配送分句不再被当作未知配送语句否决，均为 true。故**实际存在由词形不一致引起的上下文保守误否决**。本轮没有删除或放宽原有未知语句检查；新增退换货等待检查反而在相应歧义场景收紧。

原始历史诊断的[修复前记录](E:/AllProject/data-analysis-agent/outputs/agent-stage2-real-business-readiness-20261111/rb03-exploratory-fact-debug-v1/raw-diagnostic.json)及[修复后记录](E:/AllProject/data-analysis-agent/outputs/agent-stage2-real-business-readiness-20261111/rb03-delivery-fact-fix-v1/after-exploratory.json)保存在 Git 忽略目录，未复制私人评论正文或精确身份到本报告。

## 测试先行与结果

新增 [test_rb03_delivery_facts.py](E:/AllProject/data-analysis-agent/tests/test_rb03_delivery_facts.py)。修复前 7 项测试中出现 8 个失败子例，复现语气词、到货时间、等待及上下文识别缺口；正则初改后，新增退货/换货故障注入暴露 2 个错误许可子例，加入局部否决后通过。最终专项 **8/8 PASS**，覆盖：

- 明确配送主体、语气词、时间长、配送阶段等待、配送速度、快递到货等正例；完整上下文与原观点分别检查。
- 安装等待、无配送对象、否定、正向配送与负向安装、客服转述及买家纠正、跨评论拼接等负例；对直接截取的目标短语也重新检查全文否决。
- 未知配送上下文、退货或换货后等待到货继续拒绝。该保守规则可能使含退换货经历的真实配送抱怨返回 unknown，属于有意保留的限制。

最终在阻断网络、PostgreSQL、模型、Embedding、完整 Agent 及 Worker 的测试进程中，执行以下离线子集，**182/182 PASS、0 skip**：

| 回归范围 | 实际结果 | 说明 |
|---|---:|---|
| 本次事实专项 | 8/8 | 合成数据及纯函数 |
| RAG 安全与来源相关子集 | 65/65 | 既有事实安全、结构化契约、来源及合成准备测试；含隔离 SQLite，不连接 PostgreSQL |
| Oracle v2 评分器子集 | 71/71 | 纯评分器/匹配器合成测试；**不是 30 组 62 轮 Agent 执行** |
| 后端兼容子集 | 38/38 | 纯 Router、评测工具和盲态输入/合成封存测试；不执行完整 Agent 或 Worker |

完整日志和各组测试入口见[离线回归记录](E:/AllProject/data-analysis-agent/outputs/agent-stage2-real-business-readiness-20261111/rb03-delivery-fact-fix-v1/offline-regression.final.json)。原 62 轮 Oracle v2 对话回归及原 762 项完整兼容集包含本轮禁止的完整 Agent/Worker，因此 **NOT EXECUTED**，不可声称本次仍为 62/62 或 762/762。已执行的安全反例没有出现新的错误事实许可；这不证明所有自然语言反例均已覆盖。

## 版本与保护

保留原 `source-revision.v6.json`，其自身摘要仍为 `3e4ff014bb6b2f81e40cf8196093c16e2a7cf9e173485eec2a24f82135410fd1`。v6 的 88 个生产文件中，87 个摘要不变，事实判断模块有意变更：

- 原文件 SHA-256：`3f060319041aa79d1e30e9a406429f9f6178da9b2075ec3048710e0a83ef057a`。
- 当前文件 SHA-256：`ecd2cbef41f4a2b24afb496415e7b223ca8a5893a0ebe9cc5cc893db60b0fa16`。
- 新增专项测试 SHA-256：`9066e5f4d49d4c5942f76ac1b4a6b1248da7d3259c075555b30f35b0657a42fc`。
- 独立[修复差异版本记录](E:/AllProject/data-analysis-agent/outputs/agent-stage2-real-business-readiness-20261111/rb03-delivery-fact-fix-v1/source-revision.p2-c1.json) SHA-256：`920eb16b42ab49571469613b93a6afc30b0376fa3705fe1c5ad7db9e8813d506`。

Oracle v2 文件保持冻结摘要；Gold Reference 四个绑定文件摘要均匹配。未修改历史来源和人工裁决。

## 结论与未验证事项

本地修复结果：**PASS，限已列观点、边界及离线测试**。有限词形仍不覆盖所有等待方式、复杂语义和所有纠正关系；模糊语境应继续 unknown。正式盲态评测仍因 P2-B3 的实际隔离条件未满足而 BLOCKED；真实 PostgreSQL、当前来源认证、检索、模型及生产发布均未验证。完整历史业务/兼容回归需在用户允许完整 Agent/Worker 执行的独立阶段再运行。
