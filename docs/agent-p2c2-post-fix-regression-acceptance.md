# P2-C2：配送事实修复后的离线回归

日期：2026-11-11，Asia/Taipei。**结论：FAIL，当前源码不能收口为通过的后续开发基线。** 原 Oracle v2 的 62 轮和原 762 项兼容测试已实际重跑且断言通过；但新增合成安全反例发现，P2-C1 会把“等了0天才到货”许可为配送慢。旧 v6 对同一输入返回 unknown。该失败未被修复或改写。

## 版本及资产核验

对照冻结的 [source-revision.v6.json](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-g25/source-revision.v6.json) 与 [P2-C1 差异记录](E:/AllProject/data-analysis-agent/outputs/agent-stage2-real-business-readiness-20261111/rb03-delivery-fact-fix-v1/source-revision.p2-c1.json)：88 个生产文件中，87 个与 v6 一致，唯一有意变化为 [review_reported_facts.py](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_reported_facts.py:23) 的配送慢词形及局部上下文限制；本轮未修改该生产文件。

- 原 v6 文件摘要：`3e4ff014bb6b2f81e40cf8196093c16e2a7cf9e173485eec2a24f82135410fd1`。
- 事实模块：v6 为 `3f060319041aa79d1e30e9a406429f9f6178da9b2075ec3048710e0a83ef057a`；当前 P2-C1 为 `ecd2cbef41f4a2b24afb496415e7b223ca8a5893a0ebe9cc5cc893db60b0fa16`。
- Oracle v2 摘要保持 `81306475371fd769b61f8132cf97dbc8dcdc82975a67a0982018575c08bed198`；Gold Reference 清单绑定的 4 个文件以及原评论 CSV、两份历史来源观察摘要均匹配。
- 为执行旧测试新增的[候选源码绑定](E:/AllProject/data-analysis-agent/.cache/agent-p2c2/source-revision.p2c2.pending.json)摘要为 `99cf8173190ac62c5901981ba7f02f8f2b3f80b8acfc4181c80af47331babe78`。它只描述本次测试输入，状态为 `PENDING_REGRESSION_NOT_ACCEPTED`；**不是通过的正式生产版本或新基线**。原 v6 和人工参考均未覆盖。

## 执行前隔离

原 62 轮使用既有内存任务与查询替身、合成评论、固定分类/编码响应。Harness 阻断 SQLite 和 PostgreSQL 真实连接、网络连接、子进程、真实 OpenAI 客户端和真实语义服务；Worker 调用业务流程，但不连接真实数据库。

原 762 项包含临时 SQLite、模拟 SDK 传输、模拟供应商响应及本地合成进程测试。沿用原离线执行器，将 SQLite 目标限定在本次独立临时目录或内存；PostgreSQL、网络及真实 Embedding 入口受阻断。两个 CLI 子进程仅允许指定模块和该临时目录内的数据库；合成编码进程仅允许既有固定测试脚本。子进程继承最小环境和只允许测试目录写入、阻断网络/PostgreSQL/再次启动子进程的保护。这里的合成进程不是实际 Embedding 模型。

原入口将生产源码严格绑定 v6，因此本轮使用独立版本绑定器：先核验原冻结 Oracle、人工裁决、评分器、Harness、v6 及 P2-C1 的唯一差异，再将完整 88 文件摘要绑定到测试过程。没有修改旧测试断言、Oracle 或原 Harness。

## 实际回归

| 范围 | 实际结果 | 说明 |
|---|---:|---|
| P2-C1 原事实识别专项 | **8/8 PASS，0 skip** | 合成正例、安装、退换货、否定、纠正、反向评价及上下文保护 |
| Oracle v2 | **62/62 PASS，0 FAIL，0 pending** | 30 组、62 轮实际执行；64/64 连接替身关闭，Harness 外部依赖尝试 0 |
| 原兼容回归 | **762/762 测试断言 PASS，0 error，0 skip** | 与原清单按 ID 比对：缺失 0、新增 0；临时 SQLite 连接 13,960 次 |
| 原 RAG 回归 | **143/143 PASS** | 143 个原 RAG 测试 ID 全部包含在上述 762 项且通过，不额外重复计入总数 |
| 新增零天等待安全断言 | **0/1 PASS，1 FAIL** | 新发现的错误事实许可；不属于原 762 项 |

因此原测试覆盖实际完成，未执行的原 62/762/RAG 测试数均为 0；**整体验收仍为 FAIL**。原测试全绿不覆盖新增反例，不能宣称没有新的事实许可回归。

## 两次测试保护层记录

首次完整运行实际执行 762 项，3 项失败、0 error、0 skip。两项 CLI 子进程在保护层过早替换 `subprocess.Popen` 后无法导入 psycopg；另一项既有合成编码子进程被过窄白名单拒绝。失败与完整堆栈保留在[首次日志](E:/AllProject/data-analysis-agent/.cache/agent-p2c2/full/tests.txt)。只修正本轮新增的测试保护层，未修改业务代码或旧断言；三项单独重跑 3/3 通过，第二次及第三次原 762 项断言均全部通过。

第二次和第三次各记录两次被保护层拒绝的额外子进程尝试。第三次保留了调用栈：均由 Python `platform.py` 的 `_syscmd_ver` 通过 `subprocess.check_output` 发起，未能启动、未访问外部服务；它们并非 PostgreSQL、收费模型或业务数据写入。由于保护层把任何未白名单子进程尝试列为异常，第三次执行器退出码仍非零；必须与 **762 条 unittest 断言全部通过** 分开报告。诊断记录在[子进程阻断日志](E:/AllProject/data-analysis-agent/.cache/agent-p2c2/full3/blocked-child-attempts.json)。不声称所有阻断尝试为零。

第三次结果：PostgreSQL/网络受保护入口的外部尝试列表为空；测试替身统计真实 PostgreSQL 连接、收费供应商请求和真实 Embedding 调用均为 0。以上是本次隔离执行范围的证据，不代表已验证生产网络或 PostgreSQL。首次及后续日志均保留。

## 新增首个业务安全失败

[独立回归测试](E:/AllProject/data-analysis-agent/tests/test_p2c2_delivery_boundary.py:7) 使用纯合成文本“等了0天才到货”，原始断言要求不得许可配送慢。实际修复后结果为 `sufficient`、`direct_report`，并返回了合成观点 key；测试 **FAIL**。完整堆栈见[失败日志](E:/AllProject/data-analysis-agent/.cache/agent-p2c2/zero-boundary-failure.txt)。

以 SHA-256 确认与冻结 v6 完全一致的旧事实模块，在同一合成输入下返回 `insufficient`、`unknown` / `semantic_not_verified`；修复后返回 `sufficient`、`direct_report`。前后原始结果见[对照记录](E:/AllProject/data-analysis-agent/.cache/agent-p2c2/zero-boundary-comparison.json)。首个偏离是新增正则接受包含 `0` 的等待天数，并把带“才到货”的时长直接作为 `delivery_slow`；`context_ok()` 未否决该输入。即使没有零天边界，**短时长本身是否足以证明“慢”也需要审慎业务判断**，不能简单以任意数字推出延迟。

这是纯事实判断函数中的错误许可风险；本轮未构造真实生产来源或测试最终发布，不能声称真实用户已看到错误结果。它与 P2-C1 增加的规则有关，属于新引入的安全回归。根据本阶段范围，本轮不再修改生产规则，也不调整旧期望。

原专项覆盖的物流慢、安装等待、退换货等待、否定、纠正、反向配送及 `context_ok()` 安全检查仍通过；新增反例说明这些测试不足以覆盖所有“等待时长”条件。来源身份和生产 Validator 代码未变化，但本次新增失败仍阻止对该修改整体宣称安全。

## 交付与后续边界

- 旧业务用例：Oracle **62/62**、原兼容 **762/762 断言**及包含的 RAG **143/143** 已实际重跑；三次执行器日志及保护层归因均保留。
- 新增安全用例：**FAIL**；不批准将当前修复后源码作为通过验收的下一阶段基线，也没有生成正式通过版源码摘要。
- 下一步应单独修复等待时长推断边界，并保留零天及短时长反例，随后重跑受影响的事实安全测试与既有完整离线回归；本轮未自动实施。
- 正式盲态隔离、当前真实来源认证、P6-E1B PostgreSQL、真实 Embedding/模型质量及端到端联调仍未验证或受阻；本轮没有解除这些阻断，也没有连接真实 PostgreSQL 或调用收费模型。
