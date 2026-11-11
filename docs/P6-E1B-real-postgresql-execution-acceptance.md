# P6-E1B 真实 PostgreSQL 执行验收

日期：2026-11-10，Asia/Taipei。

**本轮执行：FAIL（步骤 3 初始化触发执行器超时）；P6-E1B 真实 PostgreSQL 集成验收：BLOCKED，PG-01～PG-08 执行数 0。不能标记 PASS。**

本轮已获得用户对全部限定操作的明确批准，并获准把批准人“本会话用户”、有效期 2026-11-11T23:59:00+08:00 及已批准范围写入授权文件。授权问题已解除；当前阻塞来自实际初始化未确认完成，不再以“未批准”描述当前状态。

新集群 initdb 调用在执行器设置的 **45 秒**等待上限触发 TimeoutExpired。按“任何步骤失败立即停止”，没有重试、换环境、删除或重置资源，没有启动实例、执行初始化 SQL、建立客户端数据库连接或运行 PG 测试。收费模型、Embedding 和生产数据库操作均为 0。

依据：[执行前复核](P6-E1B-environment-execution-review.md)、[环境准备](P6-E1B-environment-preparation.md)、[原 PG 验收](P6-E1B-postgresql-integration-acceptance.md)。此前授权等待状态的报告已留存到本轮本地证据目录；本文记录获准后的实际尝试。

## 1. 授权与固定目标

| 项目 | 本轮实际结果 |
| --- | --- |
| run_id | `c5412c7c4bed43fba4a721909eea1041` |
| 修订计划规范 JSON SHA-256 | `40a30688281cf0cdd2a73af02bd5c345e2671d9fcc672a84c434947b80a09e2b`；本轮执行前与当前源码匹配 |
| 固定实例目录 | 项目 `.cache/p6e1b-env/c5412c7c4bed43fba4a721909eea1041/cluster`，初始化前不存在，未复用其他目录 |
| 候选端口 | 55441；初始化前两次在 loopback 做独占 bind 检查并立即释放，均成功；没有改用其他端口 |
| 二进制 | postgres、psql、initdb、pg_ctl、pg_controldata 均实测 18.6；这不是已启动服务器版本证明 |
| 操作范围 | 创建/启动本轮新集群及引导管理员，四类运行角色、两库、schema/ACL/GUC/标记、测试连接、合成写入和 PG-01～08，用户已明确批准 |
| 批准人/有效期 | 用户明确确认“本会话用户”、2026-11-11T23:59:00+08:00，并允许记录到授权文件 |
| 授权文件 | 修订产物目录新增 authorization.approved.json；各操作许可和 synthetic_writes_authorized 按明确批准记录为 true；原 pending 未改 |
| 实际 system identifier | 尚未核验；授权中的 expected_system_identifier 仍为空，未造值 |
| 实际 DSN 注入 | 未执行；没有打印或写入报告任何密码、DSN 或连接凭据 |

授权及环境检查日志分别记录于 2026-11-10 12:14:33 和 12:14:47（UTC+08:00）。初始化超时记录为 12:15:54。时间取实际 UTC 日志换算，不使用其他实例的历史记录。

## 2. 分阶段结果

| 阶段 | 结果 | 真实证据与停止点 |
| --- | --- | --- |
| 1. 计划、授权及源码核验 | PASS | 指定 SHA 匹配；授权文件由用户两次明确回复支持；非空批准人和具体期限已记录 |
| 2. 端口、目录、程序 | PASS | 独占绑定 55441 后释放；目标不存在；祖先目录检查通过；五个程序为 18.6 |
| 本地凭据保护 | PASS（文件权限准备） | 新执行私有目录撤销继承，仅给当前用户完整权限，icacls 返回成功；测试专用随机凭据不输出，不改 .env |
| 3. initdb 新集群初始化 | **FAIL / TimeoutExpired** | 指定目录、指定新引导用户、UTF8、C locale、SCRAM；仅调用一次；执行器 45 秒超时 |
| 4. 核对真实控制身份 | NOT EXECUTED | 超时后未继续运行 pg_controldata，不生成 system identifier |
| 5. 重新核验并生成初始化 SQL | NOT EXECUTED | 没有调用 render；未生成本轮 provision.psql |
| 6. 启动实例及初始化对象 | NOT EXECUTED | 没有执行 pg_ctl start 或 psql；没有执行四运行角色、两库或业务表创建 SQL |
| 7. 注入五个 DSN | NOT EXECUTED | 凭据文件准备不等于 DSN 已配置；未注入到 Agent/Worker |
| 8. 实时身份、角色、ACL、GUC、标记检查 | NOT EXECUTED | 无客户端数据库连接 |
| 9. PG-01～PG-08 | NOT EXECUTED / BLOCKED | 实时安全预检尚未通过，不启动测试夹具或分类 |

initdb 自身会建立系统目录并可能写入引导管理员/系统库信息；本次超时后不能把这些内部步骤描述为完全未发生，也不能描述为已经成功完成。上表中“未执行角色/库创建”特指后续四运行角色和两个业务/任务测试库的初始化 SQL。

## 3. 超时的含义与现有证据限制

45 秒是**本次执行器设置的客户端进程等待上限**，不是已冻结业务契约或 PostgreSQL 自报的功能错误。仅凭此超时不能证明 PostgreSQL 初始化本身存在缺陷，也不能证明目录已经完整可用。没有通过延长上限或重新运行来制造成功。

实际失败链为 `.cache/p6e1b-exec/execute.py` 的 initialize → command → subprocess.run(initdb, timeout=45) → TimeoutExpired。使用结构化参数、显式新数据目录及新引导角色；口令通过受限本地文件提供，没有出现在命令参数值或报告中。子进程没有继承业务 DATABASE_URL、PG 服务覆盖或 API key。

失败诊断及资源观察保存在：

- [阶段日志](../.cache/p6e1b-exec/execution-journal.json)
- [初始化失败诊断](../.cache/p6e1b-exec/failure-initialize.json)
- [部分目录状态](../.cache/p6e1b-exec/partial-cluster-state.json)
- [汇总](../.cache/p6e1b-exec/execution-summary.json)

失败诊断经过凭据/DSN脱敏。执行器原本在正常子进程返回后才写 stdout/stderr，超时时没有另存部分缓冲输出，因此没有充分的 initdb 进度日志可用于定位超时原因；不能猜测是磁盘、杀毒、权限、管道或数据库故障。未为补取日志而重试。

## 4. 部分目录及资源释放观察

| 观察 | 结果 | 能说明什么 |
| --- | --- | --- |
| cluster 目录 | 已存在，保留原样 | 发生过实际初始化写入；不能再次当作全新空目录使用 |
| PG_VERSION | 存在，内容 18 | 只证明文件写入，不等于初始化成功 |
| global/pg_control | 存在 | 控制文件存在；本轮未读取其真实 identifier 或校验集群完整性 |
| postmaster.pid | 未见 | 不能单独作为所有进程退出证明 |
| 原定端口清单 | 超时后 netstat 未见 55441 条目 | 未见该端口监听；没有数据库连接探测 |
| 按命令行定位的进程查询 | CIM 查询失败，CimException | 不能从该接口提供本轮父子 PID 链 |
| 进程名称只读清单 | initdb=0、postgres=0、pg_ctl=0 | 观察时未见这些 PostgreSQL 程序进程；没有杀其他进程或操作业务服务 |
| 数据库连接/事务清理 | 无客户端连接，未运行事务 | 尚无真实 rollback、pg_stat_activity 或连接释放验收结果，不记 PG-07 PASS |

私有目录、随机凭据、部分集群和失败记录均保留；没有删除、重置、重新 initdb、自动换端口或自动换目录。进程观察不是 PostgreSQL 事务一致性验收。授权仍在有效期内，但本轮遵守失败即停，不自动恢复或继续。

## 5. PG-01～PG-08 实际验收

| 用例 | 结果及缺失证据 |
| --- | --- |
| PG-01 只读协议 | BLOCKED；无真实连接、数据库角色身份、RR/READ ONLY/UTC、写拒绝或事务释放证据 |
| PG-02 统计后并发新增 | BLOCKED；未创建合成数据，无实际 R/W Event 时序、提交、pid/snapshot |
| PG-03 维度间修订 | BLOCKED；未执行并发修订及三维一致性读取；没有以 Mock/SQLite 替代 |
| PG-04 历史排名过期 | BLOCKED；无真实 PG stale_ranking/禁止预读/分类 0 的验证结果 |
| PG-05 同计数内容变化 | BLOCKED；当前仍只承诺 D/六维计数新鲜度，未证明正文/修订版本无变化，也未虚构内容指纹 |
| PG-06 释放后冻结输入 | BLOCKED；未生成真实 PG 准备对象或检验事务释放后冻结输入 |
| PG-07 超时、异常与 Worker 停止 | BLOCKED；本次 initdb 进程超时不能替代 statement/lock/idle 超时测试。Worker 中途停止后分类 0 的原断言未改，风险尚未实测 |
| PG-08 Worker/任务库/API | BLOCKED；未运行。未来若 PG-07 失败仍必须停止，不得进入 PG-08 |

没有真实数据库身份/权限、快照、并发提交、Worker 状态、父子验收或公开 DTO 可报告，未填入模拟成功数据。

## 6. 回归、文件与调用边界

本轮未修改应用源码、既有测试、安全门、计划、Planner、供应商修复或冻结 RAG/人工评审资产。与已有保护摘要比对的 **2262 个既有文件全部未变**。新增本地执行器、授权文件、私有执行材料及诊断，更新本报告；此前报告版本已保存为本地历史证据。

初始化失败后没有继续运行全量离线回归。前次 27/27 和此前 603/603 是历史记录，不冒充本轮新执行结果；由于 PG 用例全未执行，本轮本来也不满足 PASS 前提。

| 项目 | 本轮实际结果 |
| --- | ---: |
| initdb 调用 | 1 次，超时失败 |
| pg_ctl start / psql 初始化 | 0 / 0 |
| 客户端 PG 连接 / PG 测试 | 0 / 0 |
| 业务合成评论/标注/修订写入 | 0 |
| Worker/主题分类/公开 API | 0 |
| 收费 LLM / Embedding | 0 / 0 |
| 生产数据库操作 / 生产部署 | 0 / 0 |
| 删除、重置、重试、自动切换环境 | 0 |

## 7. 最终结论与后续边界

**执行步骤 FAIL；真实 PG 集成验收 BLOCKED。** 已按失败即停要求结束，不继续 PG-01～PG-08、真实收费模型或部署。

后续恢复前需要先明确部分集群的完整性及处理方式，保留本次失败证据并重新核对当前授权/目标。不能直接再次 initdb 覆盖已有目录，不能借当前创建授权删除或重置数据，也不能更改指定 run/端口/计划来规避本次失败。真实 system identifier、权限、快照、并发、事务资源释放、Worker 全链以及真实模型费用和主题质量仍未验证。
