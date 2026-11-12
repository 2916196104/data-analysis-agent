# 商品 055：PostgreSQL 只读核验准入

## 本机真实执行与受限环境：状态对照（2026-11-12）

下方的受限环境 BLOCKED 是对 **Codex 当时无法独立核验实例身份或连接的记录**，不表示本机 PostgreSQL 连接或已完成的真实 RAG 检索失败。本次只读取已有记录，没有重新连接数据库或审计系统服务。

| 执行环境 / 范围 | 已记录结果 | 证据边界 |
|---|---|---|
| 本机 PowerShell，只读有效语料 | **COMPLETED**；预检 PASS、RR 只读；有效观点 **1,091**、认证可用向量 **200** | [有效语料记录](evaluation/agent-055-effective-corpus/effective_corpus-20261112T035403365378Z.json)，该记录 SHA-256 与下项引用一致；这是记录时点、指定范围和 profile 的结果，并非实时保证。 |
| 本机真实 RAG Top-5 | **COMPLETED / A**，5 个候选，3 个许可观点；来源认证与 Validator **PASS** | [真实检索记录](evaluation/agent-055-first-real-rag/run-20261112T043912972154Z.json)。记录中人工相关性为 NOT_REVIEWED，不能据此宣称人工质量或全量召回通过；有效观点的向量覆盖为 200/1,091，约 18.33%。 |
| Codex 受限上下文 | **BLOCKED**：当时无法独立核验该实例身份；这一观察不推翻本机记录 | 下方保留原始观察及其时间。 |
| P6-E1B 独立 PostgreSQL 验收 | **BLOCKED，PG-01～08 未完成** | 商品 055 本机结果不能替代独立环境、并发快照及 Worker 测试。 |
| 商品 055 完整 Agent 业务链路 | **NOT EXECUTED** | 首次 RAG 记录 `full_agent_runs=0`。State → Router → Planner → RAG Tool → Validator → 最终回答尚须一次独立授权的真实 Agent 执行。 |

已有 [一次性入口](../scripts/run_055_agent_e2e_once.py)调用生产 `run_review_agent()`，并对只读范围、本地编码、Top-5 和模型调用设置约束；不会启动持久 Worker。本机执行命令及边界见 [完整 Agent 验收记录](agent-055-first-agent-e2e-acceptance.md)。该入口实际执行、当前数据状态和最终回答尚未验证，本报告不授予执行权限。

---

## 2026-11-12 重新核验：BLOCKED

本节为本次重新检查结果；下方 2026-11-11 记录保留为历史证据，不能视为本次实时服务状态。本次未连接数据库或服务端口，没有发送 SQL、执行检索、加载模型或修改系统及数据库。

| 项目 | 本次观察与结论 |
|---|---|
| 服务 | `Get-Service` 无法打开服务控制管理器，`Get-CimInstance Win32_Service` 返回“无法从客户端中访问 CIM 资源”。**当前运行状态 UNKNOWN**；不能据此否定或沿用上次 Running 结果。 |
| 进程和监听 | `Get-Process` 在当前受限执行上下文中看到 0 个目标候选；`Get-NetTCPConnection` 报 CIM 资源不可访问；`netstat -ano -p tcp` 退出码 0，仅返回 4 行且配置端口无匹配。进程和网络表可见性未得到证明，空结果不能作为无进程、端口空闲或实例不存在的证据，也不能绑定 PID、映像、有效数据目录与监听地址。 |
| 配置 | 本进程无 `DATABASE_URL` 环境变量；项目 `.env` 存在该设置，可解析为 PostgreSQL、本机回环、端口 5432，未发现 URL 查询参数中的 host、hostaddr、port 或 service 覆盖。配置只代表当前进程上下文的静态候选，不证明实际服务或其他进程使用同一设置；未公开凭据和完整连接串。`core/config.py::read_settings` 的环境变量优先级已核对。 |
| 身份与权限 | **UNKNOWN**：没有经过独立验证的实例身份、数据库身份及专用只读角色；未登录数据库，不知道角色继承、RLS、ACL 和实际查询权限。不能把配置里存在用户名当成只读许可。 |
| 离线版本 | 对 `source-revision.055-sku.v1.json` 中列出的 88 个生产文件逐一校验 SHA-256，0 个不匹配；该清单本身 SHA-256 为 `c835259cf96a000a5af375cc0579a34844d18247c159b5c2597c0921bb855f93`。这只是本地文件一致性，不证明真实数据库或检索通过。 |
| 真实数据与向量 | 055 当前数据量、有效标注、观点、向量、pgvector、索引及 Validator 来源有效性均 **UNKNOWN**。未查询，历史数量不能冒充当前数据。 |
| P6-E1B | 原独立测试环境的 TCP 原生 state=0、进程身份不明和 PG-01～08 未执行等阻断没有新证据解除。它不等于自动禁止其他获单独授权的数据库只读检查，但不能替代该检查的身份与权限准入。 |

**最少需要负责人补充：**（1）通过可信渠道确认允许核验的实例、数据库、监听目标及其与配置的对应关系；（2）提供已存在且权限可验证的专用只读身份和受控凭据注入方式，不在聊天发送密码；（3）确认商品 055 对应的 `source`、`dataset_version`、UTC 日期和标注 profile，并在上述条件具备后另行明确批准一次限定范围的只读查询。若这些身份无法确认，则继续停止，不尝试连接、换库、提权或恢复 P6-E1B。

**后续执行门槛：** 先比对独立提供的实例证据，再使用只读角色单连接执行下方草案中的 `REPEATABLE READ READ ONLY`、UTC、`statement_timeout` 和 `lock_timeout`，核对身份与权限后才进行范围内聚合和来源检查，任何异常立即回滚、关闭并停止；只读查询仍会消耗资源，需负责人确认执行窗口。该 SQL 是审阅草案，不是本轮已执行的命令或授权。实际执行前必须确认可信 schema 并使用经安全引用的限定对象名，检查工作台版本为 1、全部引用区间及必要字段非 NULL，且核实 UTC 边界；查询结果中的元数据匹配数不能代替有效快照、完整来源或 Validator 认证。若 schema、权限、RLS、对象或统计口径无法确认，停止而不是猜测。

**下一步仅在条件和独立授权齐备后**进行一次指定实例与商品范围的只读核验；无需为了该步骤自动恢复 P6-E1B，也不得将本报告视为真实 RAG 联调授权。以上环境观察约为 2026-11-12 14:09（Asia/Taipei），只是当前受限上下文中的瞬时结果。

---

## 历史检查：2026-11-11

检查日期：2026-11-11，Asia/Taipei。**结论：BLOCKED；尚不存在本轮已验证的可信只读执行路径。** 本轮没有连接 PostgreSQL、发送 SQL、加载 Embedding、执行 RAG/Agent 或修改服务、权限及数据库。下面 SQL 是待审执行清单，并非已执行结果或执行授权。

## 当前能确认什么

| 检查 | 结果与限制 |
|---|---|
| 服务状态 | `Get-Service` 显示 `postgresql-x64-18` 为 `Running`、自动启动；这不证明它是配置目标或可以读取。 |
| 服务详细身份 | 在当前受限账户中，`Get-CimInstance Win32_Service` 返回“拒绝访问”；无法核对服务可执行文件、启动参数、服务账户与数据目录。 |
| 进程身份 | `Get-Process` 看到 1 个 pg_ctl 和 10 个 postgres 候选；本账户对它们的映像与启动时间均不可读取。进程名和数量不能证明有效数据目录、父子关系或与目标的关联。 |
| 配置目标 | `.env` 的 `DATABASE_URL` 非空，解析为 PostgreSQL、本机回环地址、显式端口 5432；数据库名和用户名均已配置，未发现查询参数中的 host/port/service 覆盖。未连接、未验证实际数据库名、角色或凭据；不公开完整连接串。 |
| 监听观察 | `Get-NetTCPConnection` 返回“拒绝访问”。`netstat` 命令退出码 0，但在当前上下文没有列出配置端口对应的 TCP 行；这可能受可见性、网络命名空间、瞬时状态或接口异常影响，**不能断言端口空闲、服务未监听或数据库不可用**。尚无法可靠绑定 IPv4/IPv6 监听者、PID 与配置实例。 |
| 专用只读身份 | **UNKNOWN**。配置中有用户名不代表该角色只读；未登录，也未检查实际角色、继承权限、所有权或数据库 ACL。 |
| SKU 修复基线 | 离线 SKU 版本清单中的 88 个生产文件摘要匹配；这不是数据库身份或实时来源证明。 |

[P6-E1B 最终报告](E:/AllProject/data-analysis-agent/docs/P6-E1B-final-real-execution-acceptance.md)保留的独立测试环境阻断仍未解除：原生 TCP 状态验证存在未定义 state=0，部分 PostgreSQL 候选无法证明映像和有效目录关联，PG-01～08 未执行。P6-E1B 针对原隔离测试实例；它的 BLOCKED 并非代码自动禁止所有其他数据库只读访问，但不能用它或历史演示替代本次目标实例与身份核验。

历史文档曾记录 055 有评论、标注和部分向量，但这不是当前查询；当前数量、pgvector、索引和关联一致性均为 **UNKNOWN**，不是 0。本次没有再次调用历史失败的管理员启动、Docker/WSL 或恢复程序。

## 最小阻断及用户需要补充的条件

1. **可信目标**：由数据库负责人确认本轮允许查询的具体实例和数据库，与配置目标建立可独立核验的对应证据。应由可信管理方在其正常权限范围提供服务/实例映像、有效数据目录或同等实例身份、监听地址/端口与期望数据库标识；不能只凭进程名、端口或待查询服务器自报身份。不要在聊天中发送密码或完整 DSN。若当前主机无法可靠提供证据，使用负责人明确批准且身份清楚的隔离只读副本是可选条件；本报告不授权自动切换目标。
2. **专用只读角色**：负责人提供一个已经存在或另行按其流程配置的只读身份，通过本地受控凭据方式注入；本轮不创建角色、不修改 ACL。后续检查须证实该身份既可读取必要表/元数据，也不能通过角色继承、对象所有权或写权限执行写操作；任何未知即停止。
3. **明确范围和一次性授权**：负责人确认 source、dataset_version、商品、UTC 日期范围和标注 profile，以及允许的实例、角色、只读 SQL、结果保存位置和截止时间。历史范围可供核对，但不得自动等同当前范围。取得以上条件后，用户可在后续独立消息中明确授权**一次**此处限定的只读核验；本轮提示不是执行授权。

不要求为这次只读核验自动完成 P6-E1B 的数据库恢复；也不允许把 P6-E1B 原安全门、未知来源或权限问题当成已解除。现阶段不执行连接尝试。

## 后续只读检查的 SQL 草案

以下是供负责人审阅的具体查询，**尚未执行**。使用 psycopg 参数绑定（`%s`），参数分别来自经确认的配置、范围和 profile；模型 identity 中的 base_url 只在私有参数中使用，不写日志或报告。若对象名、schema、扩展或权限与预期不符，停止并记录，不自动迁移、补表或改角色。所有 SQL 均放在**同一个连接和同一只读事务快照**中；事务和单条语句超时不能代替整个任务的硬截止。

```sql
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '3s';
SET LOCAL TIME ZONE 'UTC';

SELECT current_database() AS database_name,
       current_user AS database_user,
       current_setting('server_version') AS server_version,
       current_setting('transaction_isolation') AS isolation_level,
       current_setting('transaction_read_only') AS read_only,
       current_setting('TimeZone') AS timezone,
       inet_server_addr() AS server_address,
       inet_server_port() AS server_port;

SELECT r.rolsuper, r.rolcreatedb, r.rolcreaterole, r.rolreplication,
       r.rolbypassrls,
       pg_has_role(current_user, 'pg_write_all_data', 'MEMBER') AS member_write_all_data
FROM pg_catalog.pg_roles r WHERE r.rolname = current_user;

SELECT extname, extversion FROM pg_catalog.pg_extension WHERE extname = 'vector';
SELECT n.nspname AS schema_name, c.relname AS table_name,
       format_type(a.atttypid, a.atttypmod) AS embedding_type
FROM pg_catalog.pg_attribute a
JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %s AND c.relname = 'review_opinion_embeddings'
  AND a.attname = 'embedding' AND NOT a.attisdropped;

SELECT n.nspname, c.relname, c.relkind,
       has_table_privilege(current_user,c.oid,'SELECT') AS can_select,
       has_table_privilege(current_user,c.oid,'INSERT') AS can_insert,
       has_table_privilege(current_user,c.oid,'UPDATE') AS can_update,
       has_table_privilege(current_user,c.oid,'DELETE') AS can_delete,
       has_table_privilege(current_user,c.oid,'TRUNCATE') AS can_truncate,
       pg_has_role(current_user,c.relowner,'MEMBER') AS owner_role_member
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %s AND c.relname = ANY(%s::text[]);

SELECT n.nspname, c.relname AS table_name, i.relname AS index_name,
       x.indisvalid, x.indisready, pg_get_indexdef(i.oid) AS index_definition
FROM pg_catalog.pg_index x
JOIN pg_catalog.pg_class c ON c.oid = x.indrelid
JOIN pg_catalog.pg_class i ON i.oid = x.indexrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %s AND c.relname = ANY(%s::text[]);
```

表清单应至少包含 `products`、`import_runs`、`reviews`、`review_annotations`、`review_opinions`、`review_workbench_schema`、`review_human_reviews`、`review_human_opinions`、`review_opinion_embeddings`；还应检查目标 schema 的 `CREATE`、其他可写 schema、角色继承、数据库/对象所有权和受限可执行函数。`has_table_privilege` 仅覆盖列出的表及列出的权限，不能单独证明整个角色没有写能力。确认 `rolsuper/rolcreatedb/rolcreaterole/rolreplication/rolbypassrls` 均 false、不属于写入角色、非对象/数据库所有者，并核实角色继承和 schema 权限；不可验证即停止。RLS 或无权限导致计数不完整时不能当作 0。

确认本次范围后，下面按现有 [current_reviews_sql()](E:/AllProject/data-analysis-agent/src/data_analysis_agent/reviews/scope.py:116) 的“当前内容匹配、指定 identity、成功优先、最新成功/尝试、最新人工修订”规则做聚合初查。参数按顺序是 `source, dataset_version, product_id, date_from, date_to, requested_model, base_url, prompt_version, label_profile_key, embedding_space_id`；无实际授权范围前不填写真实参数。

```sql
WITH cfg AS (
  SELECT %s::text AS source, %s::text AS dataset_version, %s::text AS product_id,
         %s::date AS date_from, %s::date AS date_to,
         %s::text AS requested_model, %s::text AS base_url, %s::text AS prompt_version,
         %s::text AS profile_key, %s::text AS space_id
), scoped AS (
  SELECT r.* FROM reviews r CROSS JOIN cfg
  WHERE r.source=cfg.source AND r.dataset_version=cfg.dataset_version
    AND r.product_id=cfg.product_id
    AND r.published_at::timestamptz >= cfg.date_from
    AND r.published_at::timestamptz < cfg.date_to + INTERVAL '1 day'
), current_rows AS (
  SELECT r.*, a.id AS annotation_id, a.status AS annotation_status,
         h.id AS revision_id
  FROM scoped r CROSS JOIN cfg
  LEFT JOIN LATERAL (
    SELECT a.id, a.status FROM review_annotations a
    WHERE a.review_id=r.id AND a.content_sha256=r.content_sha256
      AND a.requested_model=cfg.requested_model AND a.base_url=cfg.base_url
      AND a.prompt_version=cfg.prompt_version
    ORDER BY (a.status='success') DESC, a.id DESC LIMIT 1
  ) a ON true
  LEFT JOIN LATERAL (
    SELECT h.id FROM review_human_reviews h
    WHERE h.annotation_id=a.id ORDER BY h.id DESC LIMIT 1
  ) h ON true
), effective AS (
  SELECT c.id AS review_id, c.annotation_id, c.revision_id,
         c.content_sha256, 'model'::text AS opinion_source,
         o.id AS model_id, NULL::integer AS human_id,
         o.quote, o.quote_start, o.quote_end, c.text AS review_text
  FROM current_rows c JOIN review_opinions o ON o.annotation_id=c.annotation_id
  WHERE c.has_text<>0 AND c.annotation_status='success' AND c.revision_id IS NULL
  UNION ALL
  SELECT c.id, c.annotation_id, c.revision_id, c.content_sha256,
         'human'::text, NULL::integer, o.id,
         o.quote, o.quote_start, o.quote_end, c.text
  FROM current_rows c JOIN review_human_opinions o ON o.human_review_id=c.revision_id
), vectors AS (
  SELECT e.* FROM review_opinion_embeddings e CROSS JOIN cfg
  WHERE e.source=cfg.source AND e.dataset_version=cfg.dataset_version
    AND e.product_id=cfg.product_id AND e.label_profile_key=cfg.profile_key
    AND e.embedding_space_id=cfg.space_id AND e.text_builder_version='quote-utf8-v1'
    AND e.invalidated_at IS NULL
    AND e.published_at::timestamptz >= cfg.date_from
    AND e.published_at::timestamptz < cfg.date_to + INTERVAL '1 day'
), matches AS (
  SELECT v.id AS vector_id, o.review_id, o.quote, o.quote_start, o.quote_end,
         o.review_text
  FROM vectors v JOIN effective o
    ON v.review_id=o.review_id AND v.annotation_id=o.annotation_id
   AND v.review_content_sha256=o.content_sha256 AND v.opinion_source=o.opinion_source
   AND v.quote_start=o.quote_start AND v.quote_end=o.quote_end
   AND ((o.opinion_source='model' AND v.model_opinion_id=o.model_id
         AND v.human_revision_id IS NULL)
     OR (o.opinion_source='human' AND v.human_opinion_id=o.human_id
         AND v.human_revision_id=o.revision_id))
)
SELECT (SELECT COUNT(*) FROM scoped) AS scoped_reviews,
       (SELECT COUNT(*) FROM current_rows WHERE has_text<>0
        AND annotation_status='success') AS successful_annotated_reviews,
       (SELECT COUNT(*) FROM effective) AS effective_opinions,
       (SELECT COUNT(*) FROM vectors) AS active_vectors_in_scope,
       (SELECT COUNT(DISTINCT vector_id) FROM matches) AS metadata_matched_vectors,
       (SELECT COUNT(DISTINCT vector_id) FROM matches
        WHERE quote_start<0 OR quote_end<=quote_start
           OR substring(review_text FROM quote_start+1 FOR quote_end-quote_start)<>quote)
         AS matched_vectors_with_bad_quote;

ROLLBACK;
```

上面是**初查**，不是生产认证：`matches` 尚未验证 `stable_versioned_opinion_key`、完整有效观点集合的 `effective_snapshot_sha256`、embedding 输入摘要和向量内容/归一化，也不能证明所有有效观点均已索引。后续获准时，应在同一 RR 只读事务中复用现有 `read_effective_corpus()` 和相应只读成员/摘要核验逻辑，逐项比对当前有效观点 key、revision、quote、全文、快照及向量元数据；仅输出计数、错误码与脱敏摘要，不输出评论原文、完整观点或凭据。需要访问范围外的数据、加载模型或进行向量相似度搜索时立即停止并另行申请。无 ANN 索引不自动判定失败：现有检索实现使用 pgvector 精确距离排序；检查相关范围/关联 B-tree 是否存在且 valid/ready，并根据实际规模另行评估性能。

## 成功、失败和影响控制

只有目标与负责人提供的独立实例证据一致、数据库和角色正确、只读事务/UTC/超时实际生效、角色权限可证明只读、所需表及 pgvector/维度正确、范围/profile 与批准一致，且当前计数、来源/修订/切片/向量 key 和快照核验无异常，才可记为**本次只读数据核验通过**。计数必须按指定范围报告，缺失向量、未标注或不完整覆盖应如实量化；即便通过也不代表 RAG 语义质量或端到端通过。

连接身份不符、权限未知、事务不只读、对象缺失、超时、来源/维度/摘要不匹配、超出预期范围或任何异常均停止，保留脱敏错误并 rollback/close；不自动重试、修复、重建索引或换库。数据库连接采用明确只读角色与受控凭据，不在命令行、报告或日志中暴露密码。只读事务和权限是双重约束；即使如此查询仍消耗连接、CPU 和读取资源，因此采用有限范围、语句超时、单次执行、只读结束及资源释放，并由负责人确认可接受的运行窗口。

**当前状态：BLOCKED / 未连接。** 用户需要提供的是可核验目标实例、具备足够且明确只读权限的身份及固定查询范围；这些到位后，再对一次只读核验作独立明确授权。本轮到此停止。
