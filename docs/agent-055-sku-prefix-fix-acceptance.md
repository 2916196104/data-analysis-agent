# 商品 055：SKU 前缀解析修复验收

日期：2026-11-11，Asia/Taipei。**离线修复及回归 PASS，可作为新的离线开发基线；真实数据库及当前来源准入仍 BLOCKED。** 本轮没有连接真实 PostgreSQL、执行真实检索、调用收费模型或真实 Embedding。

## 根因与修复

[evidence_question()](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_task_contract.py:18) 原来只在事实文本直接以 SKU 开始时分离前缀。精确问题“商品100013588055有没有送货慢的反馈？”以“商品”开始，因此原文本被保留，`target()` 无法识别完整事实问句。

修复只改 `evidence_question()`：当调用方传入的**已由上游目录和范围校验绑定的单商品 scope**与前缀 SKU 精确匹配、日期条件一致时，允许前缀为 SKU 或“商品 + 可选空格 + SKU”，SKU 后可有空格；余下文本仍必须整体通过原有 `target()` 有限语法，才返回事实问句。代码不硬编码 055，不删除日期、否定、比较或其他条件，不改变原始用户问题、事实许可、来源认证和 Validator。

该函数本身不认证商品目录，不能把任意客户端自报 scope 当作可信绑定；其既有契约依赖上游先校验商品目录和有效范围。目录缺失、未知 SKU、多 SKU 和范围冲突不会因这一规范化步骤获得许可。

| 精确问题 | 修改前 | 修改后，已绑定单商品 scope |
|---|---|---|
| 事实文本 | 保留“商品＋SKU＋问题” | `有没有送货慢的反馈？` |
| `target()` | null | `required_facts = delivery_slow` |
| 原始问题 | 原样保留 | 原样保留；规范化文本单独用于事实判断 |

题目没有日期时，函数不添加日期，也不修改传入 scope；具体真实商品/日期仍由请求时的有效选择和上游绑定决定，不能凭此使用历史冻结窗口。

## 安全与合成业务路径

新增 [SKU 前缀专项测试](E:/AllProject/data-analysis-agent/tests/test_055_sku_prefix_fix.py)：**8/8 PASS**。修复前测试先行复现 4 个失败子例，包括目标问句、空格写法、非 055 的合法 SKU 和同范围日期前缀。

- 三种指定写法均可在匹配的单商品 scope 下得到 `delivery_slow`；另一合成 SKU 同样通过，未硬编码 055。
- scope 缺失、空、多商品或与 SKU 不一致时不剥离；SKU 位于句中、SKU 后紧跟额外数字、多 SKU、异常标点及被否定的前缀保持原文，事实目标不被擅自确认。
- 不合法/未知 SKU 经目录绑定拒绝；显式比较、否定、数量和时间条件不能通过前缀删除而被吞掉。日期前缀只有和 scope 匹配时允许分离 SKU，冲突日期继续不能授权。

另使用既有内存 Worker、合成目录和数据替身执行目标问题的**离线路径检查**：原问题保留、解析事实为 `delivery_slow`、Router/Planner 选择 `search_semantic_evidence`，合成来源 Validator 返回 passed。带空格及直接 SKU 开头形式也走相同语义路径；无目录、未知 SKU 和多 SKU 情况未执行业务查询。该检查的三个相关结果保存在[合成验证记录](E:/AllProject/data-analysis-agent/.cache/agent-055-sku-fix/synthetic-integration.json)及[变体记录](E:/AllProject/data-analysis-agent/.cache/agent-055-sku-fix/synthetic-variants.json)。

这些是合成范围与测试替身下的 Router、Planner、Validator 结果；**真实商品目录、实时来源、向量检索、生产 Validator 和真实端到端回答均未运行验证**。合成 `passed` 不能冒充真实来源认证。

## 离线回归

| 项目 | 实际结果 |
|---|---:|
| SKU 前缀专项 | **8/8 PASS** |
| SKU、P2-C3 配送事实、P2-C2 边界及 RAG 来源安全合并专项 | **86/86 PASS，0 skip** |
| 冻结 Oracle v2，原 30 组/62 轮 | **62/62 PASS，0 FAIL，0 pending**；64/64 内存连接替身关闭 |
| 原兼容目标 | **762/762 PASS，0 fail、0 error、0 skip**；逐 ID 与原清单完全一致 |
| 原 RAG 安全回归 | **143/143 PASS**，包含在 762 项内，不重复计数 |

完整兼容执行器退出码 **0**；保护层拒绝事件及外部依赖尝试均为 **0**。真实 PostgreSQL、收费供应商及真实 Embedding 调用为 0。测试沿用隔离 SQLite、固定合成来源和模拟模型响应，保留 P2-C3 对 `platform._syscmd_ver` 的无害替身及其余网络、数据库、子进程拦截。

结果可查阅[专项日志](E:/AllProject/data-analysis-agent/.cache/agent-055-sku-fix/focused-tests.txt)、[完整兼容汇总](E:/AllProject/data-analysis-agent/.cache/agent-055-sku-fix/full/summary.json)、[原始兼容日志](E:/AllProject/data-analysis-agent/.cache/agent-055-sku-fix/full/tests.txt)、[保护层事件](E:/AllProject/data-analysis-agent/.cache/agent-055-sku-fix/full/blocked-child-attempts.json)和 [Oracle 评分](E:/AllProject/data-analysis-agent/.cache/agent-055-sku-fix/oracle-assessment.json)。测试中的故障和 partial 日志来自既有故障注入，不代表外部调用。

## 版本与后续边界

保留原 [P2-C3 版本](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-g25/source-revision.p2-c3.v1.json)，新建[SKU 修复版本](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-g25/source-revision.055-sku.v1.json)，其 SHA-256 为 `c835259cf96a000a5af375cc0579a34844d18247c159b5c2597c0921bb855f93`。88 个生产文件中，相对 P2-C3 仅 `review_task_contract.py` 改变：

- 修改前：`e2a0691a1db253b3b11c51fc6953a8c27a494b4c5651d5203559df2fe59211e3`。
- 修改后：`4b92cc87d5a33b7e40bbf7a07e4ea5d3058b5896f40c1cdd28772981efbc7720`。

Gold Reference 和 Oracle v2 摘要保持冻结值，未改生产 Validator 安全门。新版本状态是 `OFFLINE_DEVELOPMENT_BASELINE_NOT_PRODUCTION_CERTIFICATION`。

**离线基线：PASS。** PostgreSQL 的当前目标及只读身份、pgvector/向量与实时来源一致性仍未核验；P6-E1B 保持 BLOCKED。真实端到端联调需另行完成相关安全条件并取得明确执行授权，本轮未自动开始。
