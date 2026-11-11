# P2-C3：配送等待时长错误许可修复

日期：2026-11-11，Asia/Taipei。**离线修复及适用回归 PASS；当前版本可作为后续离线开发基线。** 该结论不代表真实检索、当前数据库来源或端到端 RAG 已验收。

## 错误许可与修复

修复前重新执行了 P2-C2 的零天断言，实际复现 FAIL：`等了0天才到货` 被映射为 `delivery_slow` 并获得 `direct_report`、`sufficient`。根因是 P2-C1 的 [delivery_slow 模式](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_reported_facts.py:23) 将“等了数字时长才到货”直接视为慢，没有明确的慢或时间过长评价。

本轮删除该数字时长事实规则，没有引入天数阈值。纯时长的 `fact()` 返回 null，`quote_facts()` 不生成 `delivery_slow`；`assess()` 返回 unknown 且不许可。明确的“物流太慢”“配送速度很慢”“到货时间太长”仍可作为买家自述事实。多分句观点中的明确抱怨可由 `quote_facts()` 识别，单独的数字时长观点不能借用全文中其他观点获得支持。

为保持完整原观点与上下文的检查，[context_ok()](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_reported_facts.py:71) 对独立的、完整匹配的数字到货等待句仅作为中性上下文处理；它不生成事实、不进入 `permitted_keys`。原有全文否定、纠正、转述、反向事实、未知上下文，以及 P2-C1 的退换货等待保护保留。其他未知配送内容仍按原规则保守否决；来源认证及生产 Validator 没有修改。

P2-C1 旧专项曾要求单独的“等了八天才到货”获得直接支持，与本轮明确要求冲突。该测试场景没有删除：已从正例移至负例，改为断言不能单独许可；“到货时间太长，等了八天才到货”的完整多分句正例仍保留。P2-C2 原零天失败断言未更改。

## 事实及边界测试

| 场景 | 修复前 P2-C1 | 修复后 |
|---|---|---|
| 等了 0、1、2 天或八天才到货，只有数字时长 | 可错误直接支持 | 均 unknown、不许可，不判定配送快慢 |
| 物流太慢、配送速度很慢、到货时间太长 | 已支持 | 保持支持 |
| 明确到货时间太长，加数字等待分句 | 支持 | 由明确抱怨分句支持；数字分句本身不供给事实 |
| 到货后等安装、退换货后等到货、配送并不慢、送货快但安装慢 | 不应许可 | 测试通过，不许可 |
| 对否定或纠正中的目标短语单独截取 | 不应许可 | 全文检查仍阻断 |

[P2-C3 专项](E:/AllProject/data-analysis-agent/tests/test_p2c3_delivery_boundary.py)覆盖 `fact`、`quote_facts`、`context_ok` 和最终 `assess`，也验证其他观点的明确抱怨不能为单独数字观点补事实。本轮测试未发现新增错误许可；这是有限例子的结论，不证明所有语言形式均已覆盖。对复杂关系或不足以确认的配送语境，继续返回 unknown 或受控拒绝。

## 原始历史候选复核

仅以 **EXPLORATORY_DEBUG** 方式读取 P2-B1 已冻结的原始历史候选和完整评论，调用本地纯函数，未读取 Gold、未调用生产 Validator、未伪造当前来源：

| 原观点 | `fact` | `quote_facts` | `context_ok` | `assess` |
|---|---|---|---|---|
| EV08-1 | `delivery_slow` | `delivery_slow` | true | `direct_report` |
| EV15-1 | null | `delivery_slow` | true | `direct_report` |
| EV15-2 | `delivery_slow` | `delivery_slow` | true | `direct_report` |

EV15-1 是多分句原观点，其明确的“到货时间太长”分句提供支持，不依赖八天数字。整体结果为 `sufficient / explicit_report_only`，仅是纯函数结果；历史来源仍为 `history_unproven`。原始诊断在 [Git 忽略目录](E:/AllProject/data-analysis-agent/.cache/agent-p2c3/historical-exploratory.json)，不作为正式 Gold 评分。

## 离线回归与执行器状态

| 项目 | 实际结果 | 范围说明 |
|---|---:|---|
| P2-C1 专项 | **8/8 PASS** | 含上述依新业务契约修订的数字时长断言 |
| P2-C2 原零天断言 + P2-C3 新边界 | **5/5 PASS** | 原零天断言保留，未放宽 |
| 专项与 RAG 事实、来源安全合并回归 | **78/78 PASS，0 skip** | 含合成来源及隔离 SQLite，不连接 PostgreSQL |
| Oracle v2 | **62/62 PASS，0 FAIL，0 pending** | 原 30 组、62 轮实际执行；64/64 内存连接替身关闭，外部依赖尝试 0 |
| 原兼容回归 | **762/762 PASS，0 fail、0 error、0 skip** | 原测试 ID 精确比对，缺失和额外均为 0 |
| 原 RAG 回归 | **143/143 PASS** | 143 个原 ID 均包含于上述 762 项，不重复计入总数 |

完整兼容执行器**退出码 0**；保护层拒绝事件 **0**。此前 P2-C2 中 `platform._syscmd_ver` 曾触发系统版本探测子进程并导致执行器非零，本轮测试入口只将该函数替换为其失败时的默认返回值；未放宽任意子进程白名单、网络或 PostgreSQL 拦截。仍使用临时 SQLite、固定响应和离线 MockTransport；真实 PostgreSQL、收费模型、真实 Embedding 和真实检索调用均为 0。

独立专项首次汇总时因从 `tests` 目录启动导致 `scripts` 和 `experiments` 无法导入：45 项中 2 个导入 error，非业务断言失败。该日志保留在 `.cache/agent-p2c3/focused.txt`；设置正确项目导入路径并加外部依赖阻断后，实际重跑 **78/78 PASS**，最终日志为 `.cache/agent-p2c3/focused.final.txt`。未改旧 Oracle、评分规则或测试断言来掩盖该环境错误。

完整测试及执行器证据见 [兼容汇总](E:/AllProject/data-analysis-agent/.cache/agent-p2c3/full/summary.json)、[兼容日志](E:/AllProject/data-analysis-agent/.cache/agent-p2c3/full/tests.txt)、[保护层事件](E:/AllProject/data-analysis-agent/.cache/agent-p2c3/full/blocked-child-attempts.json) 和 [Oracle 评分](E:/AllProject/data-analysis-agent/.cache/agent-p2c3/oracle-assessment.json)。测试中的业务失败或 partial 日志属于已有故障注入，最终断言通过；真实外部调用没有发生。

## 源码与冻结资产

新建 [source-revision.p2-c3.v1.json](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-g25/source-revision.p2-c3.v1.json)，包含 88 个生产源码摘要，相对 v6 仅事实判断模块发生变化，版本记录 SHA-256：`8afbb5d3448c0e6cf6304c326f5f2c86c2adfcc14804a459741eaf0f65b10f77`。

- v6 事实模块：`3f060319041aa79d1e30e9a406429f9f6178da9b2075ec3048710e0a83ef057a`。
- P2-C1 事实模块：`ecd2cbef41f4a2b24afb496415e7b223ca8a5893a0ebe9cc5cc893db60b0fa16`。
- P2-C3 事实模块：`4e8cbb8c5986ba2687f8fc0d0e88ddb3813da552019cfbbc06e7f6c833eb8c97`。

v6 清单、P2-C1 差异记录、Oracle v2、原评论 CSV 和 Gold Reference 文件摘要均匹配原冻结值。生产来源认证和 Validator 源码未改变。版本状态明确为 `OFFLINE_DEVELOPMENT_BASELINE_NOT_PRODUCTION_CERTIFICATION`。

## 结论与边界

P2-C3 修复了零天和其他孤立数字时长的错误许可，保留了明确配送抱怨的本地识别；已执行的专项和完整离线回归通过，执行器退出码和保护层事件均符合要求。**可作为后续离线开发基线**。

复杂时间、退换货或对象指代仍可能被保守拒绝；本轮没有对全部自然语言形式或实时来源作质量声明。正式盲态隔离、真实数据库、真实 Embedding、真实检索和 P6-E1B 仍未验证或 BLOCKED；没有自动进入真实端到端联调。
