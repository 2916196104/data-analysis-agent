# 商品 386660：PostgreSQL 隔离写入验收

**结论：WRITE_BLOCKED。真实隔离 PostgreSQL 测试 NOT EXECUTED。** 本轮未连接任何 PostgreSQL，未创建容器、角色、数据库或 fixture；未生成 Embedding，也未写业务库。当前执行上下文只发现 Docker 客户端，查询引擎时返回 `permission denied`；没有已配置并经授权的独立测试库。该结果不证明用户交互式 PowerShell 中的 Docker 一定不可用。P6-E1B 原有 BLOCKED 保持不变。

## 目前可以审查和运行的测试代码

新增 [隔离测试入口](../tests/rag386660_isolated_pg.py)，默认无参数时仅输出 NOT_EXECUTED，数据库连接为 0；另有 [离线准入测试](../tests/test_rag386660_isolated_safety.py)，本轮 **4/4 PASS**。这些是本地门禁测试，**不是真实 PostgreSQL 测试**；已有 43 项离线回归本轮没有重复运行。

入口仅接受命名为 `rag386660_it_<16位随机标识>` 的独立数据库、独立高位端口、一次性合成密码和专用角色；不读取或回退到 `DATABASE_URL`。在发起数据库连接前还需核对本地 Docker 上同名容器、绑定 127.0.0.1 的端口、镜像、标签、无宿主机 bind mount 及容器内 system identifier。创建 fixture 前再次核对数据库/角色/PG 18/system identifier，要求 public 没有业务表、测试角色不存在，创建过程为单事务；pgvector 版本必须是 0.8.7。结果写 Git 忽略的 `.cache/rag386660-isolated-tests/`，不记录密码或原文。

此入口复用**原样的** `migrations/003_review_opinion_embeddings.up.sql`、`GuardedWriter` 的 SQL 守卫和实际 INSERT 语句；来源表是供外键使用的合成最小桩，向量是合成 1,024 维单位向量。它准备验证真实表约束、只读角色拒绝 INSERT、3 条提交、唯一冲突回滚、UPDATE/DELETE 拒绝、锁权限和另一个合成商品不变。测试还会在回滚事务中检查写入角色能否通过**绕过应用守卫的原生 SQL**插入另一合成商品；若允许，记录数据库层商品隔离 BLOCKED。

**重要限制：**该入口为使用独立库/端口而构造测试连接，未调用固定真实计划的 `execute_frozen_batch()`；不能声称完整生产执行器、真实来源认证、并发计划过期、RLS/触发器、超时、断线或提交结果未知已验证。它会始终保留 `WRITE_BLOCKED`，即使已实现的子项成功也不会解除生产执行门。要完成完整验收，仍需新增不会接受生产目标的合成计划与隔离目标适配，并用真实来源表和并发同步屏障测试；不能把当前 SQL 子路径结果当作完整执行器 PASS。

## 真实测试状态

| 项目 | 当前结果 |
|---|---|
| 真实隔离 PostgreSQL / pgvector | NOT EXECUTED |
| 真实 INSERT、向量约束、提交 | NOT EXECUTED |
| 唯一冲突与整批回滚 | NOT EXECUTED |
| 并发来源/向量变化与计划失效 | NOT EXECUTED；完整执行器合成适配尚未完成 |
| 只读与写入角色、锁、最小权限 | NOT EXECUTED |
| 数据库级商品隔离、RLS 与触发器 | NOT EXECUTED；当前表级 INSERT 本身不能证明商品级限制 |
| 事务超时、断连、提交未知与清理失败 | NOT EXECUTED |
| 053/055 非目标合成行保持不变 | NOT EXECUTED；入口当前只构造 055 合成对照行 |

因此尚不能判断真实锁权限是否冲突、最小权限是否充分、数据库是否可阻止跨商品插入，或故障下是否会留下部分结果。模拟测试不是这些问题的证据。

## 拟议的独立环境创建与测试命令

**以下命令目前未获执行授权，仅供审阅；不要在现有业务数据库执行。** 需要本人先明确批准：创建一次全新容器与匿名数据卷、在其中创建唯一测试库/角色/合成 fixture、连接隔离库、执行 SQL 和测试。不得沿用旧 P6-E1B 集群。若 Docker 引擎不可访问、镜像缺失、版本不匹配或任一核对失败，立即停止，不切换到业务库，不自动拉取镜像或清理资源。

前置：用户本机 PowerShell 5.1 中 Docker Desktop 的**本地 Linux 引擎**正常可用，已自行取得并核验 `pgvector/pgvector:pg18` 镜像；该镜像内 PostgreSQL 18 和 pgvector **0.8.7** 是否匹配仍需 bootstrap 时实际确认。以下生成的密码为**全新一次性合成密码**，不是业务凭据；Docker 容器环境变量可被有 Docker 管理权限的人读取，请在受信任的本机执行。

```powershell
Set-Location -LiteralPath 'E:\AllProject\data-analysis-agent'
docker version --format '{{.Server.Version}}'
if ($LASTEXITCODE -ne 0) { throw 'Docker 引擎不可用，停止' }
docker image inspect pgvector/pgvector:pg18 --format '{{.Id}}'
if ($LASTEXITCODE -ne 0) { throw '本地镜像不存在，停止；不要自动拉取' }

$env:RAG386660_IT_RUN_ID = [guid]::NewGuid().ToString('N').Substring(0,16)
$RagBytes = New-Object byte[] 32
$RagRandom = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try { $RagRandom.GetBytes($RagBytes) } finally { $RagRandom.Dispose() }
$env:RAG386660_IT_SYNTHETIC_PASSWORD = [BitConverter]::ToString($RagBytes).Replace('-','').ToLowerInvariant()
$RagName = 'rag386660-it-' + $env:RAG386660_IT_RUN_ID
$env:POSTGRES_USER = 'rag386660_it_admin'
$env:POSTGRES_DB = 'rag386660_it_' + $env:RAG386660_IT_RUN_ID
$env:POSTGRES_PASSWORD = $env:RAG386660_IT_SYNTHETIC_PASSWORD

docker run --detach --pull=never --name $RagName --label "rag386660.run_id=$env:RAG386660_IT_RUN_ID" --publish '127.0.0.1::5432' --env POSTGRES_USER --env POSTGRES_DB --env POSTGRES_PASSWORD pgvector/pgvector:pg18
if ($LASTEXITCODE -ne 0) { throw '隔离容器创建失败，停止' }
Remove-Item Env:\POSTGRES_PASSWORD
$RagInspect = (docker inspect $RagName | ConvertFrom-Json)[0]
$RagPort = $RagInspect.NetworkSettings.Ports.'5432/tcp'
if ($RagPort.Count -ne 1 -or $RagPort[0].HostIp -ne '127.0.0.1') { throw '端口绑定不安全，停止' }
$env:RAG386660_IT_PORT = [string]$RagPort[0].HostPort
docker exec $RagName pg_isready -U rag386660_it_admin -d $env:POSTGRES_DB
if ($LASTEXITCODE -ne 0) { throw '隔离实例尚未就绪，停止；等待后人工检查' }
$RagGetId = 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -AtX -c "SELECT system_identifier FROM pg_control_system()"'
$env:RAG386660_IT_SYSTEM_ID = ((docker exec $RagName sh -c $RagGetId) | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $env:RAG386660_IT_SYSTEM_ID -notmatch '^\d{10,30}$') { throw 'system identifier 获取失败，停止' }
```

以上只是创建目标；接下来经独立授权且本人核对容器名、镜像、端口、标签及 system identifier 后，以下命令才会创建该隔离实例中的专用角色和合成 fixture，并执行**已实现的部分真实 SQL 测试**：

```powershell
& '.\.venv\Scripts\python.exe' -B '.\tests\rag386660_isolated_pg.py' --bootstrap-authorized
if ($LASTEXITCODE -ne 0) { throw 'fixture 失败，停止，不自动重试' }
& '.\.venv\Scripts\python.exe' -B '.\tests\rag386660_isolated_pg.py' --run-authorized
```

测试命令即使部分子项成功也会返回非零并标记 WRITE_BLOCKED，因为完整执行器及并发等测试尚未实现。保留脱敏记录并先检查结果；不要重新运行已消费的 fixture。每次重新初始化需新的隔离实例与标识，默认不删除容器或卷。任何清理须另行确认容器/卷确属该唯一运行并取得删除授权，不能按名称批量删除或触碰业务实例。

真实写入生产商品 386660、使用真实 Embedding 和进入生产部署仍未授权、也未具备条件。
