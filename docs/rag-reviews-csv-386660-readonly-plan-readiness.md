# 商品 100019386660：只读候选规划准备

**结论：READY_FOR_READONLY_PLAN（仅指入口与离线检查；实际只读规划尚未执行）。真实写入仍 BLOCKED。** 本轮无 PostgreSQL 连接、Embedding、LLM、Agent 或数据库写入，没有修改 053 的 `Frozen053Budget`。

## 当前交付

[只读规划入口](../scripts/plan_reviews_csv_386660.py)默认只输出 `NOT_EXECUTED`。取得本人对一次只读规划的明确授权后，在已验证能连接的本机 Windows PowerShell 5.1 执行：

```powershell
Set-Location -LiteralPath 'E:\AllProject\data-analysis-agent'
& '.\.venv\Scripts\python.exe' -B '.\scripts\plan_reviews_csv_386660.py' --plan-authorized
```

固定 `reviews_csv / reviews-csv / 100019386660`、已确认 profile、生产 BGE-M3 空间及 `quote-utf8-v1`。使用 `rag_audit_ro`、单连接、RR READ ONLY、UTC、语句 15 秒、锁 3 秒、事务 110 秒；先核验身份、表权限及 RLS，复用生产 `PostgresSemanticRepository`、`read_effective_corpus()`、`_verified_sources()` 和 `_eligible()`。商品日期范围取自当前数据，不复制历史日期；来源/快照/观点键及向量认证异常阻断。SQL 只读，不执行相似度检索、Embedding 或数据库写入。事务结束显式 rollback 和 close，确认关闭后才写本地文件。

在当前有效观点中排除已认证可用 key；如果某个缺失观点在对应空间及文本版本下已有任何存储行，则为避免冲突更新而跳过其整个评论的待补观点。按 review ID 的稳定顺序选择完整的待补评论分组，最多 50 条；已覆盖的观点不会再次进入候选，末组无法容纳时跳过。证据不确定时停止；不从 Amazon 或 053/055 获取候选。

本轮没有实际读取，**候选数量 UNKNOWN（0～50）**。成功时输出的 `.cache/rag-386660-plan/plan-<UTC时间>.json` 含当前 E/U、候选 key、来源身份与摘要、评论快照、完整语料摘要、相关代码摘要、事务快照和 `plan_sha256`；不保存评论正文、embedding 输入、私有标注 endpoint、数据库凭据或向量。`plan_sha256` 使用 `content_sha256` 计算除该字段以外的完整 JSON 内容；不是签名，不代表写入授权。`.cache` 已验证被 Git 忽略，但忽略规则不是操作系统 ACL，使用者仍须保护本地目录权限。成功创建文件不代表获准写入，也不保证未来数据未变化。

## 真实写入与复核

现有 `sync_effective_opinions()` 普通路径会在插入外 UPDATE 或使旧行失效，且选择阶段不是“先去掉当前认证可用向量”；053 有独立 200 条冻结预算。故此处没有生产写入命令，也没有声称已实现安全写入。最小后续修改应新增仅消费冻结计划的 insert-only 路径：编码前重验当前已覆盖 key，按冻结 key 最多一次本地 BGE-M3 编码（最多 50 条；实际批次数和时长由编码器实现决定）；提交前持有现有来源与向量锁，重验语料、范围、所有 key 和来源/快照；只 INSERT 本商品最多 50 行，冲突即整体回滚，不 UPDATE、不失效其他行，也不修改 053/055。必须确保对应连接失败、并发写入及清理可安全停止；不能通过读写账户逃避权限检查。

未来需要独立受限写入身份，最低包括本批目标向量表 INSERT、必要的只读来源和事务锁权限；具体 PostgreSQL 表锁及角色继承所需权限必须先在隔离环境实测，不能凭本报告直接创建角色或扩大生产权限。希望的业务变更边界为 INSERT 最多 50、现有向量 UPDATE/失效 0；如需要写 `rag_embedding_sync_runs`，须额外明确其 INSERT/UPDATE 授权。最终语句、写入数量、账号、窗口、备份与回退必须在真实执行前经用户独立批准。每 50 条 1,024 维 float32 原始数值约 200 KiB，不含 PostgreSQL 行/索引开销；实际运行时间和资源未知。

目前没有明确授权的独立 PostgreSQL 测试环境，**隔离库测试 NOT EXECUTED**。对编码重试、唯一约束冲突、DB 事务回滚、并发过期、跨商品/Profile/空间提交等真实写入安全性均不能声称通过；生产写入命令按要求不提供。

如果未来完成授权同步并另获一次只读复核授权，可再次使用上述只读规划命令观察 E/U、来源认证与剩余候选；这会新建一个本地只读计划，不执行再次同步。还应独立复核新增 key、055 与 053 的隔离指纹及事务日志，未完成前不得认定同步验收通过。

## 离线测试

- 新规划专项 **8/8 PASS**：默认不连接、按评论分组不超过 50、已有 key 排除、冲突组跳过、跨商品及 Amazon 范围拒绝、重复 key 拒绝、认证失败时无计划及关闭连接。
- 现有来源/向量分类专项 **7/7 PASS**；053 冻结预算专项 **14/14 PASS**。这些均为合成替身，不能替代实际 PostgreSQL 隔离并发或写入回滚测试。
- 本地配置 profile 与生产来源字段静态校验 PASS；只读入口和计划摘要的数据库真实运行均 NOT EXECUTED。P6-E1B 维持 BLOCKED。
