# reviews_csv MVP 必要读取权限：最小修复方案

**结论：当前七类MVP入口涉及13张业务表，其中12张已有完整SELECT；唯一新增需求是 `public.import_errors` 任意一列的SELECT，用于 `/quality` 的COUNT(*)。建议仅授权整数标识列 `id`，不授予整表SELECT，不读取错误载荷。** 本轮只检查并提供方案，未执行任何GRANT、REVOKE或ALTER ROLE，未启动Agent/模型/编码/服务或创建任务库。

## 1. 实际检查范围与结果

本轮使用真实数据库reviews、真实账号rag_audit_ro，在REPEATABLE READ READ ONLY事务中检查ACL、列权限、表存在性及读取许可；每条数据表探针均LIMIT 0，不返回评论、原文、向量或错误载荷。实际角色连接额度仍为8，public USAGE和数据库CONNECT已有，检查连接均回滚关闭。

| 实际依赖表（public） | 对应入口/必要性 | 当前SELECT | 新授权需求 |
|---|---|---|---|
| review_database_schema | 业务连接版本检查 | 已有 | 无 |
| review_labeling_schema | 统计、观点及RAG的标注schema检查 | 已有 | 无 |
| review_workbench_schema | options、统计与RAG的人工修订schema检查 | 已有 | 无 |
| review_monitor_schema | 现有API启动的监控版本检查 | 已有 | 无；不因此扩展监控业务表权限 |
| products | options目录/数据集摘要、quality元数据、Agent精确商品绑定 | 已有 | 无 |
| reviews | quality、统计分母/时间窗、正负分析、趋势和RAG原文来源 | 已有 | 无 |
| review_annotations | options标注Profile、当前成功标注及处理状态 | 已有 | 无 |
| review_opinions | 当前模型观点、六维/情感统计和证据 | 已有 | 无 |
| review_human_reviews | 最新人工修订选择与状态 | 已有 | 无 |
| review_human_opinions | 人工观点有效快照，覆盖或保留模型观点的既有规则 | 已有 | 无 |
| import_runs | quality导入次数；证据来源hash、行号/文件引用 | 已有 | 无 |
| import_errors | **quality的rejected_record_count，仅COUNT(*)** | **表级及全部列SELECT均无** | **仅SELECT(id)** |
| review_opinion_embeddings | 现有RAG向量距离、有效快照与来源关联校验 | 已有 | 无；不需要同步运行表或向量写权限 |

以上13表都存在；既有12表的 `SELECT * ... LIMIT 0` 全部通过，未见RLS开启或写权限。另一个NULL类型向量的零行距离表达式通过；public.cosine_distance的EXECUTE已有。它不是查询向量编码或真实Top-K检索，也不能替代RAG事实许可、覆盖和质量验收。

`/quality` 当前函数包含6条实际SQL；只读EXPLAIN检查5条通过，仅 `SELECT COUNT(*) FROM import_errors` 以InsufficientPrivilege / SQLSTATE42501失败。因此没有只停在第一处异常：其后product_sample等SQL也已检查规划与权限，未发现第二个明显读取阻断。EXPLAIN没有ANALYZE，不执行实际数据扫描。

证据：[真实ACL、列权限和零行探针](../outputs/mvp-real-acceptance/read-permission-review/current-select-permissions.json)、[quality六条SQL的实际规划检查](../outputs/mvp-real-acceptance/read-permission-review/quality-query-plans.json)、[静态SQL引用候选](../outputs/mvp-real-acceptance/read-permission-review/static-sql-reference-candidates.json)。静态候选中的attempts/current/effective_opinions是CTE，jsonb_to_recordset是函数，JSON是文本误命中，均不是待授权表；上表已经结合真实调用链人工筛选。

## 2. 七类入口的依赖闭合

| 范围 | 当前真实调用链与物理表集合（省略共同版本检查） |
|---|---|
| /options | workbench_options → current_reviews_sql + dataset_summaries：products、reviews、review_annotations、review_opinions、review_human_reviews、review_human_opinions。Profile来自review_annotations，不依赖label_profiles |
| /quality | get_quality_summary → _quality_summary：reviews、products、import_runs、import_errors |
| /health | 业务连接版本检查 + 独立SQLite queue_health；MVP模式不读生产PostgreSQL tasks/worker_state |
| Agent统计工具 | ReviewToolContext → analytics → current_reviews_sql：products、reviews、review_annotations、review_opinions、review_human_reviews、review_human_opinions；get_data_quality额外JOIN import_runs |
| 正负面观点分析 | get_positive_aspects/get_top_issues/get_opinion_breakdown用上述公共CTE；get_aspect_drilldown → get_aspect_opinions/get_evidence额外用import_runs，没有新增SQL表。主题归纳可能调用模型，但本轮未执行 |
| 趋势 | get_trend/get_aspect_trend用同一当前评论/有效观点CTE，无独立趋势表 |
| RAG只读检索 | PostgresSemanticRepository → read_effective_corpus + _search_effective_opinions + load_review_contexts：公共CTE表、import_runs、review_opinion_embeddings。同步函数不在只读检索调用链中 |

源码依据：[API路由](../src/data_analysis_agent/api/routes/reviews.py)、[全局quality](../src/data_analysis_agent/reviews/store.py)、[options](../src/data_analysis_agent/reviews/human_review.py)、[目录摘要](../src/data_analysis_agent/reviews/catalog.py)、[公共CTE](../src/data_analysis_agent/reviews/scope.py)、[分析工具](../src/data_analysis_agent/reviews/analytics.py)、[Agent适配](../src/data_analysis_agent/agents/review_tools.py)、[RAG语料](../src/data_analysis_agent/reviews/rag_corpus.py)、[只读检索](../src/data_analysis_agent/reviews/semantic_retrieval.py)、[向量只读查询](../src/data_analysis_agent/reviews/embedding_store.py)。

### health的非MVP条件分支

当前.env中的LABEL_EXECUTION_MODE为redis；已成功启动的MVP联调服务使用进程级 `LABEL_EXECUTION_MODE=local`，不修改.env，也不启动Redis标注Worker。后续继续沿用这一受限启动方式：

```powershell
$env:LABEL_EXECUTION_MODE = 'local'
```

如果改回redis模式，health还会调用queue_metrics读取label_tasks、label_outbox、label_workers。目前三表均无SELECT，但属于本轮明确排除的标注队列监控，不建议为了它们扩权；也不应把这三个条件依赖隐瞒成所有模式下health都只读版本表。label_profiles、label_requests等其他标注后台表同样不加入本次授权。

生产PostgreSQL的tasks、task_context、worker_state和监控运行/告警表也不加入授权；会话状态、排名和待办恢复继续读取独立SQLite任务库。

## 3. 敏感字段与为何只授权id

`import_errors` 的实际列为id、import_run_id、line_number、reason、raw_line。raw_line是原始错误记录载荷，reason也可能包含错误上下文；现有quality不需要读取这两列。当前所有列都不可读，没有该角色的直接列级ACL。

推荐SELECT(id)允许现有COUNT(*)，也允许枚举内部id；**它不是只能返回一个总数的聚合专用权限**。相较整表SELECT，它不开放raw_line、reason、行号或导入关联字段。

PostgreSQL18官方执行器对没有显式列引用的COUNT(*)，允许用任意一列的SELECT满足读取检查：[REL_18_STABLE / ExecCheckOneRelPerms](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/backend/executor/execMain.c#L653)。[GRANT官方语法](https://www.postgresql.org/docs/18/sql-grant.html)支持列级SELECT。**本库实际授权后的成功仍须由管理员实施后验证，本轮没有通过临时授权冒充已修复。**

受限计数视图可进一步隐藏内部id，但当前生产SQL固定读取import_errors；新建另一名称的视图不能自动满足这条SQL，还需修改接口查询或改变对象解析。当前优先不改生产业务代码，因此不创建视图、重命名表或改search_path。若连内部id也不允许开放，应单独选择“计数视图+最小接口调整”，不是授予整表原文权限。

其他已授权表中，reviews.text/raw_json/user_id、观点quote、人工修订reviewer/note、import_runs.file_path/summary_json等均有原文或内部元数据；本次不增加这些已有权限，不在输出中读取其值。现有RAG来源认证需要正文、quote区间、hash和有效快照，不能通过随意遮盖这些字段来宣称认证仍通过。

现有数据库角色的表级SELECT不是reviews_csv行级权限；Agent依赖既有source/dataset/product条件与P1入口约束。/options、/quality当前是全局目录/质量概览，COUNT(*)也保持现有全局元数据口径，不冒充055或CSV专属统计。本轮不修改RLS或开展Amazon业务。

## 4. 待审批SQL与回滚

由有权授权该表的管理员，在数据库reviews中手动执行。Agent仍使用rag_audit_ro。本轮不执行：

```sql
BEGIN;
GRANT SELECT (id) ON TABLE public.import_errors TO rag_audit_ro;
COMMIT;
```

文件：[精确GRANT](../scripts/agent_mvp_import_error_count_grant.sql)。不含整表SELECT、schema/ALL TABLES、转授权、写权限或角色属性修改。

对应回滚，由同一授权者执行：

```sql
BEGIN;
REVOKE SELECT (id) ON TABLE public.import_errors FROM rag_audit_ro RESTRICT;
COMMIT;
```

文件：[精确REVOKE](../scripts/agent_mvp_import_error_count_rollback.sql)。授权尚未提交时直接ROLLBACK；提交后仅撤销本次新增id列授权。如果期间其他管理员变更了ACL，先核对，不撤销别人的独立授权。

## 5. 管理员操作后的验证与恢复烟雾

以rag_audit_ro运行[只读验证SQL](../scripts/agent_mvp_import_error_count_verify.sql)：

- id的has_column_privilege为true；其余import_errors列仍为false。
- has_table_privilege(SELECT)应仍为false，这是列级授权的正常结果，不能为追求该布尔值变true而再授予整表SELECT。
- INSERT/UPDATE/DELETE/TRUNCATE权限仍为false。
- 原样SELECT COUNT(*) FROM public.import_errors能够成功。

然后才恢复真实启动检查与服务：原check_agent_api_startup.py → 新独立SQLite任务库 → 真实API/Worker锁和心跳 → 专用浏览器options/quality/health全部200并核对连接用量不超过8。任何失败都停止，不扩大授权、猜测数据或自动重提问题。

全部就绪后，严格只提交A1一次；真实统计、Validator、回答及页面全部PASS后，再新会话保留055提交D11一次。其他正负分析、趋势、多轮和RAG业务场景需两题通过后单独确认，不自动开展。旧库和本轮以往证据均保留。

## 6. 当前结论

从限定SQL依赖与当前权限看，**没有发现import_errors计数以外的明显MVP SELECT阻断**；RAG权限通路也已有所需SELECT和距离操作符EXECUTE，但未运行检索、编码或模型，不能把权限检查当作业务/RAG E2E通过。

距离核心演示的具体剩余事项是：管理员批准并实施这一列授权 → 验证COUNT和真实首屏200 → 完成A1统计与D11拒答的真实一次性验收。业务内容、证据许可及后续多轮质量仍以实际执行为准，不要求补向量或重构核心模块。

本轮SQL/业务库变更0、角色及ACL变更0、业务任务提交0、模型/Embedding调用0，未启动服务或创建新任务库；所有检查连接已关闭。到此停止，等待管理员操作。
