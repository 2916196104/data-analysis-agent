# P6-E1B 独立 PostgreSQL 测试环境准备

日期：2026-11-10，Asia/Taipei。

后续状态：请先阅读 [执行前复核](P6-E1B-environment-execution-review.md)。复核修正了 SQL 失败退出、并发等待/取消、测试失败即停及目录重解析检查；本文原草稿因源码摘要变化已经失效，作为历史记录保留。复核提供保持相同隔离目标的未批准修订草稿；未经负责人核实、批准，不能执行本文初始化步骤。

**方案及离线准备：READY；创建、连接及 PG-01～08 执行：BLOCKED，等待负责人明确授权。** 本文是待执行方案，不是已建环境证明。没有连接 `DATABASE_URL`、独立测试库或任何 PostgreSQL 服务，没有创建集群、角色、数据库、表或合成评论，没有调用模型或 Embedding。

依据：[E1B 验收](P6-E1B-postgresql-integration-acceptance.md)、[E0 预检](P6-E0-real-environment-readiness.md)、[E1A 供应商修复](P6-E1A-provider-contract-fix-acceptance.md)。不改写这些历史结论。E1A 的身份、费用及 quota 保护保留；真实 tokenizer、费用授权和主题质量仍未获准。

## 1. 本次核验及交付

| 证据 | 实际观察 | 对应行动 |
| --- | --- | --- |
| E-01：本机二进制 `--version` | `C:\Program Files\PostgreSQL\18\bin` 下 postgres、psql、initdb、pg_ctl 均报告 **18.6** | 可规划使用该程序目录；没有查询运行中服务器版本，不能据此声称实例已就绪 |
| E-02：现有安全门及测试入口源码 | 五个显式 DSN、两个精确命名数据库、四种互不隶属角色、运行标记和有限角色超时 | 使用下面的严格匹配方案，不回退业务库或通用管理员 URL |
| E-03：初始化源码及工厂 | PostgreSQL v1 命名空间元数据、业务/标注/人工修订/任务表各有现成定义；只读 BEGIN 转换为 RR READ ONLY | 导出当前定义，不手写另一套业务表，不迁移现有数据 |
| E-04：当前 Worker/P6 源码 | 停止检查位于 Worker 进度回调，P6 准备和执行入口未显式接收 stop_requested | 单列 PG-07 风险，保持原“中途停止后分类 0 次”断言 |
| E-05：本轮离线检查 | 新 9 项与原安全门 8 项，共 **17/17 PASS**；网络、psycopg 连接和子进程启动均被封锁并断言未调用 | 只证明准备器和安全门；不计为 PostgreSQL 集成通过 |
| E-06：配置键存在性和已安装驱动 | DATABASE_URL 已配置但未连接；六个 E1B 进程变量均缺失；psycopg 3.3.6 | 仍无实际可用的明确授权环境，不输出或复用业务 DSN |

本轮新增：

- [离线准备器](../scripts/prepare_p6e1b_environment.py)：只读源码并生成文件；没有连接、启动程序或执行 SQL 的入口。
- [准备器测试](../tests/test_p6e1b_environment_preparation.py)：授权默认拒绝、逐项权限、身份摘要、源码变化、SQL 权限、配置注入和禁止覆盖等 9 项检查。
- 本文档。

原 `tests/p6e1b_environment.py`、`tests/p6e1b_postgresql_integration.py`、`tests/test_p6e1b_safety.py` 和所有业务源码、旧断言保持不变。未启动服务或查询端口所属进程；示例端口 **55441 仅为候选，尚未验证可用**。旧测试目录、旧报告和已存在的本地 PostgreSQL 服务均不构成本轮授权。

可审阅的本轮草稿：[plan.json](../.cache/p6e1b-env/c5412c7c4bed43fba4a721909eea1041/plan.json)、[默认未批准的授权模板](../.cache/p6e1b-env/c5412c7c4bed43fba4a721909eea1041/authorization.pending.json)。它们没有密码，也没有获准状态。缓存未纳入版本库，丢失后可重新生成新 run；不能凭报告中的链接认定已有实际数据库。

## 2. 隔离实例与权限模型

采用 Windows 当前用户启动的独立 PostgreSQL 18 集群，使用现有程序二进制，**独立数据目录、端口、认证文件、日志和数据库**。不接管 Windows PostgreSQL 服务，不操作安装目录中的 data，不复用任何旧集群或业务库，不使用 SQLite 替代。

离线准备器产生随机 32 位小写十六进制 `run_id`。本轮路径为项目 `.cache/p6e1b-env/<run_id>/`，真实数据目录固定为该目录下尚不存在的 `cluster`。只监听 `127.0.0.1`，不使用业务常用 5432 或已知其他服务候选 55432；准备器拒绝这两个端口。负责人须先确认候选端口空闲及本地目录权限，不能终止占用端口的其他进程来腾位置。

数据库名称必须精确为：

- `p6_e1b_<run_id>_business`：仅本轮合成业务数据，schema 使用现有工厂固定的 public。
- `p6_e1b_<run_id>_tasks`：独立任务库和任务绑定元数据，同样使用专用库内的 public。

这不是在现有业务库里新建 schema。两个数据库均为空库起步，禁止从业务库备份、复制、迁移或导入真实评论。

| 角色 | 对象/权限 | 明确不授予 |
| --- | --- | --- |
| creator | 两个专用库及其对象所有者；public USAGE/CREATE；创建表和监测会话；两个 DSN 共用同一登录角色 | SUPERUSER、CREATEDB、CREATEROLE、REPLICATION、BYPASSRLS、其他三种角色成员资格 |
| reader | 业务库 CONNECT；public USAGE；现有业务、来源、标注、人工修订及元数据表 SELECT；角色默认只读 | 任意表写、序列 USAGE、schema CREATE、任务库 CONNECT、修改标记 |
| writer | 业务库 CONNECT；public USAGE；现有表 SELECT；七张入口检查表及 import_errors INSERT/UPDATE；业务序列 USAGE | DELETE、TRUNCATE、schema CREATE、任务库 CONNECT、元数据/标记写、角色成员资格 |
| task | 任务库 CONNECT；public USAGE；任务库现有表 SELECT；tasks/worker_state INSERT/UPDATE | DELETE、TRUNCATE、schema CREATE、业务库 CONNECT、task_context/版本/标记写、角色成员资格 |

四种角色名分别为 `p6_e1b_<run_id>_creator/reader/writer/task`，均 LOGIN、NOINHERIT，连接数上限 8。NOINHERIT 不能代替成员关系隔离；方案不授予相互成员资格，现有实时安全门还会用 `pg_has_role(...,'MEMBER')` 检查。

初始化还需要**集群引导管理员** `p6_e1b_<run_id>_bootstrap`，由全新 initdb 创建。它不属于上述四类运行角色、不进入五个 DSN，不运行 Worker；只用于本次获准的新集群建角色/库及初始化。四个运行角色本身不能创建数据库或其他角色。引导管理员不得复用业务管理员账号。

撤销 postgres/template0/template1 及两个新库的 PUBLIC 数据库权限；只授予目标角色所需 CONNECT。撤销 public 的 PUBLIC 权限和所有表/序列的 PUBLIC 权限，不给运行角色 TEMP/CREATE、额外扩展或未来对象的通配写权限。creator 的所有者权限较大，因此只用于初始化/监测，不能用于业务分析。

现有入口要求 writer 对七张业务表均具有 INSERT/UPDATE，包括只追加使用的人工修订表；本方案遵守这个检查，不为追求更窄 ACL 修改旧断言。运行标记不在写授权中。**这是按当前测试契约的最小授权，不是逐行 RLS 隔离**；合成范围还由唯一数据库、运行标记和 dataset_version 检查共同约束。

所有四类角色配置：statement_timeout=5000ms、lock_timeout=1000ms、idle_in_transaction_session_timeout=5000ms、timezone=UTC。三个 timeout 均满足现有门要求的 `0 < value <= 5000`。故障测试在事务或会话内进一步缩短；不通过放大角色超时让断言变绿。

reader 的默认只读 GUC 不能代替 ACL：会话能够改变默认值，现有 Worker 会话锁连接也会设为读写模式，但只执行锁/读取操作，仍没有表写权限。真实查询事务的 RR/READ ONLY 由现有工厂 BEGIN 转换与 PG-01 检查共同保证。

## 3. 五个 DSN 的生成与注入

| 变量 | 登录角色 | 数据库 |
| --- | --- | --- |
| P6_E1B_CREATOR_DSN | creator | business |
| P6_E1B_TASK_CREATOR_DSN | creator（同一密码） | tasks |
| P6_E1B_READER_DSN | reader | business |
| P6_E1B_WRITER_DSN | writer | business |
| P6_E1B_TASK_DSN | task | tasks |

另有 `P6_E1B_AUTHORIZATION_FILE` 指向负责人填写并确认的授权 JSON。准备器根据 run_id 计算数据库及角色名；`inject.ps1` 交互读取四个密码，用 `Uri.EscapeDataString` 编码，再构造五个 URI。只设置当前 PowerShell **进程**环境，不改 `.env`、用户/系统环境或已有应用配置，不回退 `DATABASE_URL`、TEST_POSTGRES_ADMIN_URL、PGSERVICE。

生成的 HBA 仅允许上述登录角色通过 IPv4 loopback、SCRAM-SHA-256 访问各自目标；其余 IPv4/IPv6 拒绝。仅本地合成实例使用 sslmode=disable；不将此配置用于远程或真实数据。密码由负责人设置、隐藏输入，不进入报告、命令参数或授权文件。进程环境仍含敏感信息，须使用专用终端、不启用终端转录、不打印环境；SecureString/BSTR 清理不保证所有托管字符串已从内存可靠抹除。

## 4. 授权文件及身份摘要

完整结构模板如下。**这是默认不可执行的结构说明；实际使用 draft 自动生成的非空 run_id 和摘要，不能直接把本模板的空值当成有效配置。** 负责人只在核实范围后填写批准人、带时区到期时间及明确允许的操作；准备器不会代填这些值。

```json
{
  "version": "p6-e1b-authorization-v1",
  "run_id": "",
  "purpose": "synthetic-p6-postgresql-integration",
  "authorized": false,
  "synthetic_writes_authorized": false,
  "approved_by": "",
  "expires_at": "",
  "allow_database_or_schema_drop": false,
  "identity_sha256": {
    "creator": "", "task_creator": "", "reader": "", "writer": "", "task": ""
  },
  "role_sha256": {
    "creator": "", "task_creator": "", "reader": "", "writer": "", "task": ""
  },
  "environment_preparation": {
    "plan_sha256": "",
    "authorized": false,
    "create_cluster": false,
    "create_roles": false,
    "create_databases": false,
    "initialize_schema": false,
    "test_connections": false,
    "expected_system_identifier": ""
  }
}
```

| 字段 | 含义与填写规则 |
| --- | --- |
| version / purpose | 上述固定值，不能换成旧版本或一般业务分析授权 |
| run_id | draft 生成的 32 位小写十六进制；绑定目录、库名、角色和合成数据范围 |
| authorized | 负责人明确允许该运行范围后才改 true；不是脚本执行成功的标志 |
| synthetic_writes_authorized | 仅允许本次隔离库的合成夹具和模拟人工修订，不覆盖真实数据 |
| approved_by | 真实负责人标识，不能由脚本编造姓名、沿用单测批准人或填机器人默认值 |
| expires_at | 明确的未来 ISO-8601 时间，必须带时区；例如格式为 YYYY-MM-DDTHH:mm:ss+08:00，需负责人填写实际日期时间 |
| allow_database_or_schema_drop | 必须 false；本授权不包含清库、删 schema、删数据库 |
| identity_sha256 | 五个连接对应的无凭据身份 SHA-256；密码和登录名不参与该项，不能直接对完整 DSN 求哈希 |
| role_sha256 | 五个 URI 的登录角色名 SHA-256；creator/task_creator 必须相同，四种角色必须不同 |
| environment_preparation | 本轮准备器附加的环境操作授权；原 E1B v1 安全门不解释这些操作字段，但整份 JSON 会参与授权摘要 |
| plan_sha256 | 固定 plan.json 的规范 JSON 摘要；计划还固定初始化源码、准备器及 E1B 入口源码摘要，变化后拒绝沿用 |
| 额外 authorized 及五项操作许可 | 须逐项明确为 true 才能通过准备器 check；包括新建集群/角色/数据库、初始化 schema、实际测试连接，没有隐含批准 |
| expected_system_identifier | 新集群 initdb 后，由负责人从该新数据目录的 pg_controldata 核实并填入。check 的初始创建阶段可以为空；render 和凭据注入必须非空 |

摘要计算沿用源码：

```text
identity_sha256[role] = SHA256(UTF8(database_identity(dsn)))
role_sha256[role] = SHA256(UTF8(uri_username))
authorization_sha256 = content_sha256(完整已批准 JSON 对象)
plan_sha256 = content_sha256(plan.json 对象)
```

`database_identity` 固定规范 scheme/小写 host/显式 port/数据库路径，并剔除用户名密码；本方案角色名全是 ASCII，无 URI 解码差异。`content_sha256` 使用项目 canonical_json：sort_keys、紧凑分隔、ensure_ascii=false、allow_nan=false，再对 UTF-8 求 SHA-256。**不能使用授权文件原始字节 SHA-256 代替规范 JSON 摘要。** 授权文件不包含自己的摘要，避免自引用。

两个库各建 `public.p6_e1b_test_scope(run_id TEXT PRIMARY KEY, purpose TEXT NOT NULL, authorization_sha256 TEXT NOT NULL)`，附本轮 run_id/purpose/64 位摘要约束，插入完全相同的一条记录。creator 所有；reader/writer/task 只能 SELECT，不给写权限。实时安全门要求整表恰好这一条，摘要与当前授权 JSON 一致。任何授权值修改都会使旧标记失配；不能自动更新标记、延长授权或重算历史结果来绕过复核。

这些摘要用于绑定范围，**不是批准人的签名或身份认证**。负责人需审阅 plan/授权及待执行 SQL，保护文件权限；单测内的临时批准夹具只用于验证拒绝分支，不是实际授权。

## 5. 初始化及安全预检顺序

下面除 5.1 的离线生成外，均是**获得相应明确授权后的待执行步骤，本轮未运行**。每一阶段失败立即停止；不自动选择其他数据库、端口、角色或集群补救。

### 5.1 生成离线草稿（不连接）

在项目根目录、新的专用 PowerShell 中：

```powershell
$env:PYTHONPATH='.;src;tests'
$p6e1bPython='.\.venv\Scripts\python.exe'
$p6e1bDraft=& $p6e1bPython scripts/prepare_p6e1b_environment.py draft --port 55441
if ($LASTEXITCODE -ne 0) { throw '离线草稿生成失败' }
$p6e1bPlanFile=($p6e1bDraft | ConvertFrom-Json).plan_file
$p6e1bPlan=Get-Content -LiteralPath $p6e1bPlanFile -Raw | ConvertFrom-Json
$p6e1bFolder=Split-Path -Parent $p6e1bPlanFile
```

输出 plan.json、authorization.pending.json、inject.ps1、pg_hba.template.conf；不会生成集群目录、密码或已批准授权。目录已存在即拒绝，不覆盖既有文件。程序目录存在不代表候选端口空闲。

### 5.2 负责人批准与全新集群初始化

负责人审阅计划，将 pending 文件另存为 authorization.approved.json，手工填写上节许可、批准人和有效期；此时 expected_system_identifier 可以为空。确保输出目录仅本人/获准管理员可访问，不放进版本库或聊天。不得使用业务凭据或环境变量作为测试密码。

```powershell
$p6e1bAuth=Join-Path $p6e1bFolder 'authorization.approved.json'
& $p6e1bPython scripts/prepare_p6e1b_environment.py check --plan $p6e1bPlanFile --authorization $p6e1bAuth
if ($LASTEXITCODE -ne 0) { throw '没有有效的环境创建授权，停止' }
$p6e1bPgBin='C:\Program Files\PostgreSQL\18\bin'
$p6e1bCluster=$p6e1bPlan.cluster_directory
if (Test-Path -LiteralPath $p6e1bCluster) { throw '目标数据目录已存在，禁止复用或自动清理' }
$p6e1bListeners=@(Get-NetTCPConnection -State Listen -ErrorAction Stop |
    Where-Object { $_.LocalPort -eq $p6e1bPlan.port })
if ($p6e1bListeners.Count -ne 0) { throw '候选端口被占用，停止并重新审定计划' }
& "$p6e1bPgBin\initdb.exe" -D $p6e1bCluster -U $p6e1bPlan.bootstrap_role `
    --encoding=UTF8 --locale=C --auth-host=scram-sha-256 --auth-local=scram-sha-256 -W
if ($LASTEXITCODE -ne 0) { throw '新集群初始化失败，保留现场，不自动删除' }
& "$p6e1bPgBin\pg_controldata.exe" -D $p6e1bCluster
if ($LASTEXITCODE -ne 0) { throw '新集群控制信息读取失败' }
```

initdb 的 `-W` 让负责人隐藏输入**新引导管理员**密码；不在参数或文件里写密码。pg_controldata 只读取指定新目录的控制文件，不连接数据库。负责人核实并填写该集群 system identifier；不要拿现有服务的 identifier 或自动生成假值代替。JSON 须保持 UTF-8 无 BOM。

此后重新审阅最终授权，离线导出初始化 SQL：

```powershell
& $p6e1bPython scripts/prepare_p6e1b_environment.py render --plan $p6e1bPlanFile --authorization $p6e1bAuth
if ($LASTEXITCODE -ne 0) { throw '最终授权/集群身份不完整，停止' }
```

render **不执行 SQL**，仅独占创建 provision.psql。重新导出不能覆盖旧 SQL；遇到计划/源码/授权变化应停下审阅，不直接修改旧摘要以掩盖变化。SQL 在初始管理连接和两次换库后均核对 system identifier、新目录、loopback/端口、引导角色、数据库名、PG18 和授权有效期；初始还要求集群只有默认三个数据库且四个目标角色尚不存在。

### 5.3 启动已批准的新实例并初始化对象

先审阅生成的 HBA 和 SQL。以下命令仅能针对上一步新建且标识核实过的目录：

```powershell
Copy-Item -LiteralPath (Join-Path $p6e1bFolder 'pg_hba.template.conf') `
    -Destination (Join-Path $p6e1bCluster 'pg_hba.conf') -ErrorAction Stop
@("listen_addresses='127.0.0.1'", "port=$($p6e1bPlan.port)", 'ssl=off',
  'max_connections=30', "password_encryption='scram-sha-256'", "log_statement='none'",
  "log_min_error_statement='panic'") |
    Add-Content -LiteralPath (Join-Path $p6e1bCluster 'postgresql.conf') -Encoding ascii
& "$p6e1bPgBin\pg_ctl.exe" -D $p6e1bCluster -l (Join-Path $p6e1bFolder 'postgresql.log') -w start
if ($LASTEXITCODE -ne 0) { throw '新实例启动失败，停止；不切换到已有服务' }
```

使用专用终端，移除继承的 libpq 服务/连接覆盖，不触碰 `.env`：

```powershell
foreach ($p6e1bVar in @('PGSERVICE','PGSERVICEFILE','PGOPTIONS','PGDATABASE','PGHOST',
                      'PGHOSTADDR','PGPORT','PGUSER','PGPASSWORD','PGPASSFILE')) {
    [Environment]::SetEnvironmentVariable($p6e1bVar,$null,'Process')
}
& "$p6e1bPgBin\psql.exe" -X -h 127.0.0.1 -p $p6e1bPlan.port `
    -U $p6e1bPlan.bootstrap_role -d postgres -W -v ON_ERROR_STOP=1 `
    -f (Join-Path $p6e1bFolder 'provision.psql')
if ($LASTEXITCODE -ne 0) { throw '初始化未完成，保留现场；不重置库或自动继续' }
```

`-X` 禁用用户 psqlrc，连接参数全部明确指定。SQL 用四次 `\password` 提示设置四类运行角色密码，不带明文密码常量。CREATE DATABASE 不能包在普通事务中，故初始化并非整个集群原子操作；中途失败可能留下已创建角色/空库，脚本拒绝把这种现场当成全新环境继续，不自动 DROP 或补修。

初始化来源与顺序：

1. 新建四种运行角色，设置有限超时；新建两个空数据库，撤销默认权限并分配 CONNECT。
2. business：`review_database_schema(namespace='business',version=1)` → `reviews.store.SCHEMA`（import_runs/products/reviews/import_errors）→ `label_store.LABEL_SCHEMA`（版本表、review_annotations/review_opinions）→ `human_review.SCHEMA`（版本表、review_human_reviews/review_human_opinions）。
3. tasks：`review_database_schema(namespace='tasks',version=1)` → `jobs.store.SCHEMA`（task_context/tasks/worker_state）→ 唯一 task_context 行绑定 reader 的规范业务库身份。
4. 两库分别创建运行标记并写最终授权摘要；按第 2 节授予 ACL。每库建表/元数据/授权置于独立事务。

准备器直接读取这些现有 Python 常量，用现有 `postgres_schema()` 转换 identity/BLOB/REAL；保留原约束、索引及外键。元数据和 task_context 的写法对应现有 `init_postgres()/init_tasks()`，SQL 导出便于**先审阅后执行**，并非调用迁移或从 SQLite 建立测试替身。导出会固定源码摘要，后续源码变化拒绝继续。没有初始化 RAG/Embedding、冻结人工评审资产或无关监控、Redis 队列设施。

### 5.4 注入配置并做本地入口检查

```powershell
. (Join-Path $p6e1bFolder 'inject.ps1') -AuthorizationFile $p6e1bAuth
& $p6e1bPython tests/p6e1b_postgresql_integration.py `
    --output (Join-Path $p6e1bFolder 'local-preflight.json')
if ($LASTEXITCODE -ne 2) { throw '本地入口返回值不符合预期，停止' }
```

inject 先清除可能残留的六个 E1B 进程变量，再检查计划/完整授权/集群标识并交互读取四个密码；中途失败也会清除这些变量。上述**不带 --run** 的测试入口只核对本地授权，仍不连接；预期 READY_FOR_LIVE_IDENTITY_CHECK、退出 2、cases=0，不能当成实时连接通过。

### 5.5 实时预检与 PG-01～08

只有负责人已允许实际测试连接和合成写入、实例身份及最终 SQL 复核完成，才运行下节命令。现有 `--run` 先调用 `verify_live_environment()`，全部检查通过后才插入夹具：

- 实际 current_database/current_user 与批准目标一致，五个身份/角色摘要匹配；记录 server_version_num。
- 全局高权限均为 false；四角色无交叉 MEMBER；public CREATE 仅 creator；runtime 无另一数据库 CONNECT。
- 规定表 SELECT/INSERT/UPDATE/DELETE/TRUNCATE 和全部序列 USAGE 符合边界，runtime 无标记写权限。
- 两库标记精确匹配最终授权；三项角色 timeout 均在范围内。
- 业务表没有其他 dataset_version，任务表没有外来范围或 queued/running 遗留任务。

**实时检查尚未运行。** 现有入口将实时检查失败记录为套件错误/FAIL（可能 cases=0）；本地授权缺失则 BLOCKED。二者都不能解释成成功，更不能绕过后直接插入数据。

## 6. PG-01～08 兼容性及运行步骤

本轮逐项对照 [现有真实 PG 测试](../tests/p6e1b_postgresql_integration.py)。结构及 ACL 方案与入口匹配；以下均是待实跑断言，不是 PostgreSQL 通过记录。

| 用例 | 环境依赖与验证点 | 当前结论 |
| --- | --- | --- |
| PG-01 | reader BEGIN 由工厂转成 REPEATABLE READ READ ONLY；UTC、ACL 写拒绝、rollback、连接释放 | 静态兼容，未执行 |
| PG-02 | 两个真实连接、Event 屏障；writer 可写来源/评论/标注并分配 ID；旧 R 的 D=6、三维 M=5/4/4，新事务 D=7 | 静态兼容，未执行；不能用 sleep 排序 |
| PG-03 | writer 可追加完整合成人工修订；service 预读后提交对 effectiveness 的修订，原三维同一 snapshot/pid | 静态兼容，未执行 |
| PG-04 | count 变化后真实 Worker/task 库；stale_ranking 阻断，模型 0、观点预读 0、不重选前三 | 静态兼容，未执行 |
| PG-05 | 合成人工引用由“客服回复缓慢”改成同一原文中的“回复缓慢”，D/六维不变 | 原断言保留：允许旧排名进入新准备，承认仅统计计数新鲜度；不是全量内容版本控制 |
| PG-06 | 关闭 R 后继续合成写；冻结对象独立存在；分类中连接/analytics 被封锁，固定响应供应商消费冻结输入 | 静态兼容，未执行；不允许缓存未命中回库 |
| PG-07 | 服务端语句/锁/idle 超时、断连、回滚、监测 pg_stat_activity；中途 Worker 停止后分类 0 | 环境提供所需权限/GUC；**停止传递风险独立待验** |
| PG-08 | 两个 PG 库、后台 Worker 会话锁、真实任务持久化/封存、6 次固定供应商响应、公开路由重读 DTO | 静态兼容，未执行；当前代码测 API 路由处理器，不包含 HTTP 网络或浏览器 |

获得执行授权后，从第 5 节同一专用终端一次运行全部八项，保留真实结果：

```powershell
& $p6e1bPython tests/p6e1b_postgresql_integration.py --run `
    --output (Join-Path $p6e1bFolder 'postgresql-result.json')
$p6e1bSuiteExit=$LASTEXITCODE
if ($p6e1bSuiteExit -ne 0) { throw 'PG 验收未通过；保留证据并停止，不重置数据或放宽断言' }
```

脚本只创建 `p6-e1b-<run_id>-PGxx-<随机后缀>` 的合成 dataset_version，不从业务库取数。用现有导入、标注校验及人工修订入口创建固定夹具；不把文件中的模拟“人工修订”写进冻结评审资产。PG-02/03 包装真实 analytics 只做屏障调度，仍查询真正 PostgreSQL。

证据应保存隔离实例的脱敏身份/版本、角色布尔权限、run/授权摘要、真实 backend pid/txid_current_snapshot、读写提交事件、SQLSTATE、连接释放及任务/公开 DTO 摘要。不得把完整 DSN、密码、授权人私密信息、原始异常栈或连接日志粘贴到验收报告。SQL模板和离线输出不能冒充已经执行的 SQL。

## 7. PG-07 专项风险与回退

**R-07：Worker 停止信号可能迟于 P6 分类。** `jobs/worker.py` 的 report_progress 检查 stop_requested；`review_preparation.prepare_p6_analysis` 和 `review_execution.execute_p6_prepared` 没有显式停止参数。在统计/预读中置停止事件，可能直到分类后的进度回调才被观察。这个判断来自源码，真实 PostgreSQL 路径尚未证明。

原 PG-07 会在真实当前统计返回时设置事件，要求最终 failed 且模拟分类调用列表为空。**保留该严格检查；不把事件移到任务最初、不只检查最终 failed、不因环境准备完成就宣称风险解除。** 若实跑失败，先记录 FAIL，再另行最小修复信号传递并复验；本轮不改 Worker/P6 业务逻辑。

其他边界与回退：

- statement_timeout 是单条 SQL 上限；lock_timeout 是锁等待上限；idle_in_transaction 是事务空闲上限。它们不是整个请求的硬 deadline。Event 等待最长 10 秒而正常角色 idle 上限 5 秒，慢机器可能触发真实失败；须分析时序，不提高超时或取消断言来制造成功。
- 资源清理依赖客户端 close 与服务端会话实际消失两层检查。测试会先断言应用没有泄漏，再兜底关闭，不把兜底清理算作正常释放。
- 初始化发生部分失败、授权过期、角色/标记失配或存在未结束任务时停止。保留文件及合成记录，不清空旧任务、不自动重写标记、不 DROP 库/schema、不迁移或重建业务表。
- 可在确认路径、pg_controldata/system identifier 及进程确属本轮获准集群后，由负责人决定用该目录的 pg_ctl 正常停止；禁止操作 Windows 业务服务或根据端口猜测杀进程。当前授权不包含目录/数据库删除，清理需另行精确授权。
- 测试结束先停止测试 Worker、关闭连接，再退出专用 PowerShell（清除进程 DSN）。集群是否继续保留由负责人决定。日志和目录只保存在本轮隔离路径，不上传凭据。
- 同计数内容变化不会触发完整内容过期；若未来业务需要此能力，应另立版本指纹契约，不伪造现有能力。真实收费模型、Embedding、供应商取消和质量验收继续 BLOCKED。

## 8. 本轮验证与可执行前置条件

实际执行了二进制版本检查、draft、未批准模板的 check/render 拒绝检查、生成 PowerShell 的纯语法解析及以下离线测试：

```powershell
$env:PYTHONPATH='.;src;tests'
.\.venv\Scripts\python.exe -m unittest test_p6e1b_environment_preparation test_p6e1b_safety -v
```

结果：**17/17 PASS**，没有 skip/expectedFailure/旧断言修改；未批准 check/render 均退出 2，未生成 provision.psql；生成的 inject.ps1 语法检查通过但未执行。SQL 在临时单测夹具中只渲染并检查内容，**尚未交给 psql/服务器解析或执行**。此前 E1B 的 603/603 离线回归是历史证据，本轮没有重新跑全量，也不把它改写成新结果。

本轮结果在 `.cache/p6e1b-env-preparation/tests.txt` 和 `summary.json`。与上轮保护摘要比对的 **2257 个既有文件均未变化**，包括 E1B 入口和冻结评审资产；没有清理历史私有导入问题。

真实执行的必备条件：负责人明确批准本轮实例、目录、候选端口、两个数据库、四个角色、建库/建角色/初始化/连接/合成写入范围及有效期；确认二进制、目录权限和端口；新集群控制身份固定；最终授权/SQL复核；运行账号及 ACL/GUC/标记实测通过。任一项缺失都不能进入夹具写入或 PG-01～08。

**最终：环境方案、配置模板及离线生成工具 READY；实际测试环境与 P6-E1B 集成验收仍 BLOCKED。** 本轮数据库连接/创建/合成写入、真实收费模型、Embedding 和生产操作均为 0。完成准备后停止，不自动启动 PostgreSQL、运行 PG 套件或进入收费模型验收。
