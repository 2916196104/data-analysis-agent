# RAG Step 4B E1-A B2：版本化人工参考发布结果

日期：2026-11-05，Asia/Taipei。

**B2已完成：从原E0145派生并正式发布48份探索性人工参考，发布后独立校验48/48通过、失败0。** 原24份查询契约、48份人工判断、4组同输入配对及145条事件保持不变。本轮没有执行R0/L1预测、外部模型、数据库补读、B3/B4或历史可信E1验收。

发布的是 `e1a-reference-release-v1` 探索参考，不是历史可信来源集合。`history_unproven`、`independent_seal=false`、B上下文策略和预测未授权状态继续保留。

## 1. 授权与本轮读取范围

本轮用户明确授权：“现明确授权执行 **B2 范围内的本地参考发布操作**，不授权真实预测、外部模型调用或额外数据库读取。”该授权已捕获到私有authorization-basis.json，OperationAuthorization的action仅为publish_reference。

程序实际捕获时间与用户精确批准时间分开：后者没有可用独立时间戳，保留null；不编造批准人姓名、第二评审人、批准时间或来源独立封存。授权记录摘要用于本次操作绑定，不是历史可信来源pin。

读取并核对[E1-A协议](rag-step4b-e1a-exploratory-evaluation-plan.md)、[B1实施报告](rag-step4b-e1a-b1-offline-implementation.md)、现有发布器/协议代码、原始人工会话、bundle、原冻结run、探索输入和既存来源登记。旧报告中“尚未授权发布”是历史状态，本轮按新增B2授权执行，不修改旧报告。

沿用reviews_csv / reviews-csv、商品100013588055、UTC 2021-09-11至2022-08-26、原run/manifest/scope、BGE-M3/1024维、quote-utf8-v1及200条索引。未重新检索或改变222条候选排名，未读取新来源。

## 2. 发布身份、位置及摘要

私有版本目录：

`outputs/rag-retrieval-quality/e1a-b2-publication-20261105/release-v1/`

外层release_id为 `e1a-exploratory-reference-v1-20261105`，包含48条现有 `e1-human-reference-v1` 记录。发布时间UTC `2026-11-05T08:51:01.663791+00:00`，台北时间 **2026-11-05 16:51:01.663791**。这是实际发布程序时间，不是重新人工裁决时间。

| 对象 / 摘要类型 | SHA-256 |
|---|---|
| 原会话文件字节 | `b5e722b632e6639b6ebcedbe883edd0ca4f2699e5460ae8cf2b75c6c719b171b` |
| 原会话规范JSON | `a2d065b4d0de2cadb13d88d2412a2e9ec6a09ae938e2f349364f325c0e201a12` |
| bundle规范JSON | `f0ee9e6d2a0bcaa16baf17118d97fe7da9843098b919a6335160dd2729ef7b5a` |
| reference-release.json文件字节 | `3119110db89e0c9afc88f90865e73b907f6529b4045be359001ce682f2586d26` |
| ReferenceRelease规范JSON | `fece48317074ddf52b74ba1ec2692094b4b48d8d0b15ed9d1f95213cc5f3f6cc` |
| publication-manifest.json文件字节 | `4da5f61566ecfe4c15de33a6297acb139f093e3d68dcfef92be13a99de18a456` |
| case-validation-results.json文件字节 | `cf4b414996952e3955a16c1449a4aea4ed06fb8bf1c0e8840d5498d7964327b2` |
| 发布操作授权规范JSON | `d0bc6e9a6904e0595e8f698449b8e21122986e9517cd91bbce93dd2c3f41255a` |

文件字节摘要与规范摘要用途不同，均没有用作新的独立历史来源证明。普通私有目录也不被称为独立封存。

## 3. 发布前置核验与实际发布

| 前置核验 | 实际结果 |
|---|---:|
| 原会话字节摘要及规范JSON摘要 | 一致 |
| E0145、145条完整事件链、活动确认指针 | 通过 |
| 当前共享契约及两模式版本 | 24/24通过 |
| 人工CASE一致性 | 48/48通过 |
| 同输入配对一致性 | 4/4通过 |
| 已存pending与当前会话校验输出 | 48/48一致 |
| 48组输入、222条原候选、93个来源键及上下文 | 身份、版本和顺序一致 |
| 原B1实现文件摘要 | 与B1登记一致，未改发布逻辑 |

随后实际调用B1的publish_reference_release()，指定新目录、真实B2授权、固定输入摘要和实际发布时间。发布器从有效事件派生新confirmed记录，执行for_evaluation=True校验后暂存并移动到release-v1。没有手改原会话、pending或E0145，没有补造审批记录。

publisher-receipt.json保留发布当时的“已写入、等待独立校验”状态，后续不覆盖这份历史回执。当前完成状态由publication-manifest.json和独立校验报告共同记录为published_and_independently_validated。

## 4. 48份参考的独立校验结果

发布后的verify_published_reference.py从实际落盘的JSON读取数据；禁止调用发布构建函数或预测器，不用“重新构建结果与自己一致”代替人工原始记录对照。逐条执行：

1. `validate_human_reference(..., for_evaluation=True)`；对照批次从已保存SQL来源记录独立投影，不从待测候选复制来源。
2. 原question、query/mode、scope、活动契约内容/修订及独立目标核对。
3. batch/context规范摘要、B策略、原U/F过滤及Top-K/顺序核对。
4. state、逐事实support及rationale、必要关系、许可/缺失集合、context_observations逐字段与原CASE对照。
5. 每个原key和quote精确区间保持不变；新增完整来源身份与当前候选一致。
6. 契约/CASE确认事件摘要、原评审人、决定理由、原确认时间追溯；不增加评审人或独立裁决人。

| 发布后结果 | 数量 |
|---|---:|
| 正式发布并逐项验证通过 | 48/48 |
| 验证失败 / 缺项 / 重复query-mode | 0 / 0 / 0 |
| 两模式共享契约版本一致 | 24/24 |
| 原候选排名条目保持 | 222/222 |
| 当前来源键 | 93 |
| 事实证据引用 | 44条，区间及身份保持；不是222条候选的覆盖率指标 |
| 人工状态分布 | sufficient 19、limited 0、insufficient 29，与原会话相同 |
| 空列表 | Q17-filtered、Q22-filtered，均已验证，未跳过 |

**48组逐项结果**位于私有 `case-validation-results.json`；每项包含CASE/query/mode、原确认事件、契约修订、参考/批次/context摘要及各项检查。可读逐项表在 `independent-validation.md`，汇总在 `independent-validation-summary.json`。没有把48份同一数据的两次检查累计成96个样本。

这里的独立校验是另一个脚本、另一次执行及原记录/来源对照路径，不是第二名人工评审、独立来源保管批准或新的语义评分。它证明已发布内容忠实于已确认人工记录及本地探索来源政策，不证明人工语义判断绝对正确或历史快照可信。

## 5. 原始记录保留与文件清单

本轮Git范围只新增本报告；没有修改已有业务代码、测试、计划、原始会话或来源文件。

私有新目录 `outputs/rag-retrieval-quality/e1a-b2-publication-20261105/` 的新增文件分组如下：

| 文件 | 用途 |
|---|---|
| release-v1/reference-release.json、release-v1/manifest.json | B1发布器原样生成的正式探索参考及规范摘要manifest |
| publication-manifest.json | 本次授权、发布、来源政策、逐项验证结果和文件字节摘要汇总 |
| authorization-basis.json、operation-authorization.json、publication-control.json | 当前真实授权依据、仅发布权限及发布前固定控制参数 |
| source-session.original.json、confirmation-record-index.json | 原会话完整字节副本；145条真实事件、原评审备注及决定理由保留，索引绑定各活动确认和时间 |
| preflight.json、protected.before.json、source-code.before.json | 发布前48组检查和原文件保护清单 |
| publisher-receipt.json | 原发布回执，包含实际发布时间及发布物摘要 |
| case-validation-results.json、independent-validation-summary.json、independent-validation.md | 48项验证和独立校验报告 |
| protection-check.json、README.md、local-artifact-hashes.json | 保护结果、交接说明及本地完整性清单 |
| publish_local_reference.py、verify_published_reference.py | 本轮一次性发布调用和独立检查的实际脚本；已有结果不覆盖重跑 |

原review_notes和contract_review_note不属于HumanReference结构字段，因此原样保存在完整源会话副本中，由索引追溯；没有丢弃或重写。新记录的reviewer_decisions使用原本人决定理由。原pending仍保留未发布标记作为历史材料；当前版本以本次release及publication-manifest为准。

保护结论：**368个既有私有文件、105个既有Python源码文件的摘要全部不变**。其中包括原始人工会话、pending、冻结归档、222条候选输入、来源登记、strict准备接口和原模型代码。新输出受outputs规则忽略，没有私有评论/标签进入Git。

## 6. 实际执行与限制

主要实际命令：

```powershell
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/e1a-b2-publication-20261105/publish_local_reference.py
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/e1a-b2-publication-20261105/verify_published_reference.py
git diff --check
git check-ignore outputs/rag-retrieval-quality/e1a-b2-publication-20261105/release-v1/reference-release.json
git ls-files outputs/
```

发布和独立检查均退出0。真实网络、psycopg连接、R0入口/内核及strict批次准备调用被哨兵禁止。没有运行单元测试来代替真实发布验证；B1的28/238结果属于前阶段，本轮采用实际48项发布后验证。两项既有SQLite/PostgreSQL队列错误本轮未重跑、未计通过，业务逻辑未改。

以下状态保持：

- 57条历史快照全部history_unproven，独立封存false，历史可信批次0/48；没有因为参考发布而改变来源等级。
- 本次参考可供后续获准的探索性评价使用，不等于正式可信E1或语义质量验收通过。
- 真实R0/L1预测0、外部模型0、额外数据库读取0、新人工评审动作0。
- 原experiment登记未覆盖；本次只在新manifest记录“参考已发布”，prediction_execution_authorized仍为false。

## 7. B3所需准备与停止点

**B2已完成，B3尚未开始。** 下一阶段不需要重做24/48人工裁决或M1–M3，但需要：

1. 明确授予仅8项程序调试的运行权限，固定原计划清单、R0版本、实际代码摘要、超时/失败/重试政策；全48组和外部模型仍需各自授权。
2. 从当前release和原探索输入只投影runtime白名单，绑定问题/契约/修订、来源、batch/context与release摘要；参考和人工答案留在评价侧，不能交给预测worker。
3. 落实真实执行入口及worker的OS权限/目录隔离，验证只能读runtime、写raw输出，不能读reference；B1目前只证明函数与数据结构逻辑隔离。
4. 固定每列表主尝试、raw输出保存、缺输出/失败台账、独立输出审计及未评映射规则，再启动获准调试。R0未知/保守拒答不能通过删契约条件或改参考解决。
5. 正式质量阈值与独立泛化验证仍待后续明确，不能从描述性调试或本轮参考发布推出质量通过。

本轮到此停止，没有自动进入B3、B4或真实E1预测。
