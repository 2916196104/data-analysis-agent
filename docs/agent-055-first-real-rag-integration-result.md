# 商品 055：首次真实 RAG Top-5 联调结果

状态：**COMPLETED**；分类：**A**。记录时间 UTC：2026-11-12T04:39:12.972154+00:00。

技术链路成功，找到生产许可的 delivery_slow 明确证据。

问题：商品100013588055有没有送货慢的反馈？

## 实际执行与调用次数

| 项目 | 本次记录 |
|---|---|
| 用户执行授权参数 | True |
| 数据库连接尝试 | 1 |
| 查询编码尝试 / 成功 | 1 / 1 |
| Top-5 距离 SQL 尝试 / 执行完成 | 1 / 1 |
| execute_retrieval 调用 | 1 |
| 实际召回数 | 5 |
| 来源认证 / Validator | PASS / PASS |
| ROLLBACK / 关闭连接 / 关闭编码器 | True / True / True |
| 最终回答 LLM / 完整 Agent / 数据库修改 | 0 / 0 / 0 |

未创建连接或编码器时，清理值为 null/None，表示不适用，不是已完成回滚。距离 SQL 完成计数表示驱动 execute 已返回；来源认证和最终许可仍独立判断。清理失败会归 C，来源或 Validator 失败归 D 并隐藏候选。

无实际执行错误。

## A/B/C/D 判定口径

| 类别 | 条件 |
|---|---|
| A | 技术链路成功，生产事实许可明确支持 delivery_slow |
| B | 技术链路成功，Top-5 未找到足够支持，包括无候选或上下文无法许可 |
| C | 技术链路失败，记录配置、连接、编码、检索或清理的具体失败阶段 |
| D | 来源认证或 Validator 失败，禁止发布检索候选 |

未执行时不归入上述任何一类。技术链路成功不等于总体统计或完整 Agent 验收通过。

## 候选与事实许可

| rank | distance | review_id | annotation_id | opinion_id | 来源 | Validator | delivery_slow | permitted_key |
|---|---|---|---|---|---|---|---|---|
| 1 | 0.11492211 | 9958 | 5489 | 10751 | PASS | PASS | True | True |
| 2 | 0.16294603 | 9993 | 5524 | 10808 | PASS | PASS | True | True |
| 3 | 0.20848217 | 9949 | 5479 | 10734 | PASS | PASS | False | False |
| 4 | 0.25643544 | 9959 | 5493 | 10756 | PASS | PASS | False | False |
| 5 | 0.25680122 | 9993 | 5524 | 10807 | PASS | PASS | True | True |

每条候选的完整 stable_versioned_opinion_key、effective_snapshot_sha256、来源 ID、quote 位置/摘要及许可原因仅从生产真实返回值投影，见[脱敏记录](evaluation/agent-055-first-real-rag/run-20261112T043912972154Z.json)；不保存完整评论、quote 正文、查询向量、密码、连接串或 endpoint。人工相关性默认 NOT_REVIEWED，不能冒称已人工判定。D 类不会输出未经认证的候选。

## 前置数据与结论边界

前置 [COMPLETED 有效语料记录](evaluation/agent-055-effective-corpus/effective_corpus-20261112T035403365378Z.json)：510 条评论、505 条成功标注评论、1091 条有效观点、U=200、891 条未覆盖、覆盖率 18.33%。这些是已完成核验的基线，**不是本次重新审计的实测统计**。不另跑权限、全量向量认证或同步；保留检索内部的生产当前成员/快照/来源保护。

Top-5 不足不能证明全部 200 条已索引观点、891 条未覆盖观点或全部 510 条评论没有送货慢反馈。

## 本机唯一执行命令

[一次性脚本](../scripts/run_055_real_rag_once.py)默认不执行真实操作。用户明确授权后，在已能访问本机数据库的 PowerShell 手动触发：

```powershell
Set-Location -LiteralPath 'E:\AllProject\data-analysis-agent'
& '.\.venv\Scripts\python.exe' '.\scripts\run_055_real_rag_once.py' --execute-authorized
```

固定 reviews_csv / reviews-csv / 100013588055，UTC [2021-09-11, 2022-08-27)，Top-K=5、aspect=None、sentiment=None。使用既有 BGE-M3 固定修订、1024 维 CLS/L2 和本地缓存；空间/profile 不符即停止，不下载、不切换模型。

固定模型修订：5617a9f61b028005a4858fdac845db406aefb181。固定 Embedding 空间：embedding-space-v1:11e6754662dbd13fc56c6e624bc9416c1c2766f962787267fd4a57b1fa8d622e。固定标注 profile：1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304。它们是不同身份，不用查询模型名称替换标注模型三元组。

实现复用同一 SemanticRetrievalService 的 prepare() 缓存，随后通过生产 execute_retrieval() 消费；编码器入口和实际距离 SQL 分别阻止第二次调用。RR READ ONLY、statement_timeout=15s、lock_timeout=3s、UTC；编码超时取现有 1～600 秒配置；不自动重试、增大 K 或关键词补搜。每次手动运行保存独立 JSON，并更新本报告；无授权的默认调用不会覆盖已有报告。仅状态 COMPLETED 且分类 A/B 表示技术链路完成；它不等于总体反馈结论或完整 Agent 验收。

当前任务到独立脚本、脱敏记录及本报告为止，不自动进入完整 Agent、最终回答、Gold/Oracle 修改或 P6-E1B 解除。
