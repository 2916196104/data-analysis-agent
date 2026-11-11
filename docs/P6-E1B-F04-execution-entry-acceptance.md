# P6-E1B-F04 · 恢复执行入口最小实现与离线验收

日期：2026-11-10（Asia/Taipei）。本报告记录 F04“仅提案”之后的实际代码实现；此前报告、未批准草稿和失败证据均保留。

| 独立结论 | 本轮结果 |
| --- | --- |
| 离线恢复入口 | **PASS**，专项 53/53，适用后端回归 757/757 |
| 真实恢复执行 | **BLOCKED**，没有新的具体批准，未执行真实恢复 |
| PG-01～PG-08 | **NOT EXECUTED**；模拟阶段结果不作为真实 PG 验收 |
| 原部分集群完整性 | **UNDETERMINED**；不能启动或覆盖原目录 |

本轮只修改代码、运行受封锁的离线测试、更新未批准材料和做只读完整性核验。未移动原目录、运行任何 PostgreSQL 程序、启动/连接 PostgreSQL、执行真实 PG 测试、调用收费模型或 Embedding。临时合成目录中的 FakePopen、假 ACL/Job 和模拟阶段响应均标记 simulation=true。

## 1. 固定范围与入口

- run_id：`c5412c7c4bed43fba4a721909eea1041`。
- attempt_id：`a4b479a8d9154994a402f7b13f1a00a6`。
- route：B；端口 55441；原环境计划规范 SHA-256：`40a30688281cf0cdd2a73af02bd5c345e2671d9fcc672a84c434947b80a09e2b`。
- 原 cluster、精确存档位置、两个测试库、四类运行角色及 bootstrap 名称保持不变。无 DATABASE_URL 或其他环境后备。

实际新入口是 `.cache/p6e1b-exec/execute.py recover`，调用 `scripts/p6e1b_recovery.py` 的同一长生命周期协调器。旧入口的历史 FAIL 阻断及通用 command fallback 继续关闭；新分支不删除旧阻断、不复用旧账本，只消费新的固定位置批准文件。

本轮没有运行这个真实执行入口。只读检查入口为：

```powershell
.\.venv\Scripts\python.exe scripts/p6e1b_recovery.py check
```

当前返回 `PENDING_BINDINGS_VALID`、`real_recovery=BLOCKED`，不是实际执行许可。原只读准备器保留旧版本行为；v2 执行计划使用上述新检查入口，不能用旧 draft/check 自动升级成批准。

## 2. 独立 attempt 与不变审批

新私有执行目录固定为：

```text
E:\AllProject\data-analysis-agent\.cache\p6e1b-exec\private\attempts\a4b479a8d9154994a402f7b13f1a00a6
```

该目录本轮**不存在**。生产路径先验证批准文件位置、完整作用域、每项操作许可、真实批准人/期限、运行账户、policy、代码/二进制、原环境和 schema 源码、历史失败与原生证据摘要，之后才允许创建。

目录使用排他创建；已有目录无论 failed、completed、running、未知、空目录或损坏状态，都拒绝自动重用。重新构造协调器或 InitdbExecutor 不会重置失败。每个阶段有独立编号事件、UTC/monotonic 时间、验证结果、输出和摘要；初始化另有 `initdb/initialization-state.json`，并记录 attempt_id。进程中断留下未知/运行状态时，也因目录已存在而失败关闭。

原 `execution-journal.json`、`failure-initialize.json`、旧状态观察和旧 AUTH 只读取摘要并关联到新账本，不覆盖。新恢复批准与运行事实分离：批准文件始终只读；存档回执、initdb 结果、identity、新 `authorization.runtime.json`、SQL 和 PG 阶段结果只写新 attempt。不会把 archive_completed 或 system identifier 回写批准文件。

审批/策略文件在构造时记录原始文件 SHA，每个阶段及原生客户端调用前再次核验；重排/追加内容也会失效。JSON 拒绝重复键和 NaN/Infinity。代码、policy、二进制、原生凭据、原历史、schema 或原环境准备源码变化，均须重新复核，旧批准不能沿用。审批仍依赖服务端受控文件及负责人明确授权，不是密码学签名或客户端自报结果。

## 3. 存档安全门与回执

精确源继续是原 `.cache/p6e1b-env/<run_id>/cluster`，精确存档为：

```text
E:\AllProject\data-analysis-agent\.cache\p6e1b-exec\private\archives\c5412c7c4bed43fba4a721909eea1041\a4b479a8d9154994a402f7b13f1a00a6\cluster
```

实现顺序：

1. 单独的 archive、ACL 收紧许可必须为 true，所有后续操作也须先明确获准；没有许可不创建 attempt、不移动目录。
2. 重核固定源/目标、同盘且不重叠、祖先与每条目无重解析点、原路径集合/元数据/文件 SHA、全量 Owner/Group/DACL 及保全材料摘要。拒绝硬链接、额外数据流和越界相对路径。
3. OS 预检必须成功；未知进程/端口状态不能当作空闲。当前生产预检保守要求无可见 initdb/postgres/pg_ctl 进程且 55441 未被占用；同机存在业务 PG 时会阻断，**不会停止业务进程**。
4. 目标及其本次专用父目录不能是已有存档；检查后仅做精确同卷移动一次，没有覆盖、删除、跨盘复制后清除或自动搬回逻辑。
5. 移动后重新检查源已不存在、目标真实存在；收紧每个存档条目的 ACL，再独立读取内容与 ACL。访问主体限定当前批准运行账户、SYSTEM、Administrators，保护继承、检查 owner 和 FullControl；原 ACL 证据保留，不能把收紧后的 ACL 当成原 ACL。
6. 只有路径/长度/SHA 与原清单一致、全部私有 ACL 合格且新源目标不存在，才在新 attempt 中生成 `archive-receipt.json`。初始化前再次从文件系统复验，并核对回执与本进程观察对象一致。

JSON 自报 archive_completed 不会跳过任何动作，审批中的未来事实必须仍为 false/null。预存 attempt/伪回执不能解锁；部分移动、原内容或 ACL 变化、存档损坏、额外用户权限和目标残留均会阻断 initdb。当前真实存档和回执生成数为 0。

## 4. initdb 完成性与 WindowsJobProvider

`InitdbExecutor` 只增加可选固定 attempt_id 的日志路径及回执门，默认 F02 路径/业务行为保留。密码文件仍固定为原 private/bootstrap.pw，不把密码写入 argv。新初始化使用 `private/attempts/<id>/initdb` 独立账本；不能从旧共享状态推导新尝试成功。

真实路径明确使用现有 `WindowsJobProvider`，不是注入的 FakePopen 或自由 tree_probe。F03 的 `scripts/p6e1b_windows_job.py` 未改，摘要仍为 `63d27fa33658dbd791666175951310d9400f1a9dd26ea6a7ba420b028b39e2ee`。已有 10 项真实 Windows 测试凭据与当前平台/代码/测试程序仍匹配；本轮没有重新运行原生进程测试，也不把离线通过外推为 PostgreSQL 子进程已验证。

初始化只有以下条件全部成立才能 completed：真实返回 0、直接进程和 Job 后代结束、双路输出/读取线程结束、句柄释放确认、完整脱敏且未截断日志、完成提示、完整预期结构、无执行/日志/清理/状态错误，且未迟到。pg_control 存在、PG_VERSION=18 或 shut down 单独都不够。

实际回执、合法新 attempt 和空的新目标缺一不可。超时、输出未知、结构或完成证据缺失、Job 未清理，立即使父尝试失败，不继续 identity/start。未处理的 initdb 或客户端资源引用保留到同一协调器的清理路径，不能因直接父进程返回就宣称全部释放。

## 5. 后续受控链与实例生命周期

```text
archive verified → initdb completed → control identity observed
→ runtime authorization → render → start（继续持有服务器 Job）
→ provision → live identity/role check → PG01 … PG07 → PG08
→ 仅本次实例停机、Job/句柄释放 → completed
任一步失败 → failed，停止后续阶段；资源不明另记 cleanup_incomplete
```

| 阶段 | 已实现的控制 |
| --- | --- |
| identity | 固定 pg_controldata -D；先有 initdb 完成结果；控制文件摘要前后相同、路径匹配、identifier 格式及 shut down 状态有效；不是读取旧 identifier 后预填 |
| runtime authorization | 派生新的 E1B v1 兼容运行授权，绑定 attempt、批准摘要、新实际 identifier 及 simulation；旧 AUTH/旧 identifier 不改 |
| render | 复用冻结 SQL 模板；四角色/两库数量与连接范围验证；review SQL 无密码，private SQL 只展开固定四个密码指令和两处连接指令 |
| start | 固定 pg_ctl 参数和目录/日志；同一协调器持有服务器 Job，postmaster.pid 的 PID 必须属于该 Job；父 pg_ctl 退出不关闭 Job |
| provision | 短客户端 Job；固定 psql -X -w、目标、私有 SQL 文件及摘要；SQL 原有集群/新鲜度守卫继续生效 |
| live | 验证实际 server directory、端口、用户、版本和新 system identifier；复用既有五连接身份/角色/ACL/GUC/标记检查 |
| PG01～08 | 逐个调用原真实测试类/原断言，结果必须恰有一个测试、无 failure/error/skip；PG-07 失败立即退出，不调用 PG-08 |
| cleanup | 只处理本次持有的客户端、初始化资源和服务器 Job；优先受控 fast stop，不明时只终止自身 Job，不按名称或任意 PID 杀进程 |

客户端命令由对应阶段精确构造并再次白名单核验，通用 fallback 不开放。控制用 PowerShell 路径固定、二进制摘要绑定；没有任意 shell/参数或客户端允许列表。SDK 连接也限制为本轮五个精确 DSN；bootstrap 身份检查用固定 passwordless conninfo，不能回退 DATABASE_URL。

libpq 用新私有 passfile，argv 只含该文件路径，不含密码；stdin=DEVNULL、-w 禁止密码提示，也不依赖被过滤的 PGPASSWORD。旧 SQL 模板保持原样，私有操作 SQL 不发布；日志按已知原始/SQL 转义密码脱敏。每个客户端日志使用独立序号，缺失输出不能当作空输出。运行标记使用新 runtime authorization 的摘要。

服务器 Job 从 start 一直保留至 PG 流程结束/失败清理，不能由短生命周期 start CLI 关闭。假 Job 测试直接覆盖生产适配器的 start/provision/stop，而不只是模拟上层成功。任一步服务器失去归属立即拒绝后续 SQL/测试。

## 6. 限额、模拟隔离与真实未验证事项

当前未批准 policy：初始化 180 秒、终止 10 秒、清理 30 秒、每路安全日志 1 MiB；阶段 identity=10、client=60、start=60、stop=30、suite=600 秒。策略原有初始化四字段继续由 F02 解析，新协调器另外验证阶段字段有限且合法。

所有调用带有限等待或 SQL/连接限额，阶段和 PG case 返回迟到时拒绝并停止下一步。同步文件系统、Win32 和 psycopg 子调用不是硬实时取消；本实现没有用线程超时假装底层已停止。PG suite 截止在 case 边界/返回后检查，不能把它解释为能够立即终止任意阻塞 Python/SQL。相关真实超时、权限与释放行为仍须在获准的 PG 环境验证。

simulation 模式只接受工作区之外的临时根；真实根不能注入平台、假进程或模拟模式。生产 CLI 没有 simulation 开关。所有模拟回执/状态/结果带 simulation=true；真实完成校验拒绝它们，真实入口也不从任意结果 JSON 恢复或跳过阶段。模拟运行授权仅用于离线链路测试，不是新的负责人批准。

尚未实证：真实 pg_ctl/受限 Windows 后代能否按预期继承 Job，真实 PowerShell ACL 收紧、文件共享/杀毒影响，私有 passfile 与操作 SQL 的真实客户端行为，真实 PG 权限/快照/并发与取消。当前保守进程预检也可能因业务 PG 正在运行而阻断；不得为通过而结束该业务服务或放宽门。

## 7. 修改文件、最终摘要与未批准材料

| 文件 | 改动 |
| --- | --- |
| scripts/p6e1b_recovery.py | 新增固定范围验证、严格 JSON、独立账本、存档复验、串行协调器、生产适配器及只读检查/执行入口 |
| scripts/p6e1b_executor.py | 可选固定 attempt_id 的独立 initdb 账本、回执绑定、固定密码路径及结果标识；旧默认行为保持 |
| .cache/p6e1b-exec/execute.py | 新增 recover 薄分支，旧历史门与通用命令关闭规则保留 |
| tests/test_p6e1b_f04_execution_entry.py | 53 项正式离线测试，临时合成目录、FakePopen/假时钟/假 ACL/假 Job，真实服务封锁 |
| 本报告及未批准材料 | 保存旧版本，更新最终代码/policy/原生证据绑定与审批清单 |

| 对象 | 最终 SHA-256 |
| --- | --- |
| 新恢复协调器 | `62b7287941329e736693c6ac6c6b854fd211f795b023df951919d5ceb03cd35c` |
| initdb 执行器 | `b8982cb19cfa563805849ef1aae068dffdede6f8fdddcaa8911d48da8dd973fb` |
| 缓存入口 | `2f94881844cc2d0b5772b598a5f49e9449398289d2cc649bb95c85069f16dd5b` |
| recovery-plan.pending.json（规范 JSON） | `bbc65bf7baa555cf9abad25c86890bbe4dd8d5d9747e244d9f761e1a068147e5` |
| recovery-policy.pending.json（规范 JSON） | `5f596e4722bc97cd3d6b7892228f7f28c61c4019fced04eca1bd9773f0dece3b` |

材料仍位于 `.cache/p6e1b-f04/a4b479a8d9154994a402f7b13f1a00a6/`。新执行格式为 `p6-e1b-recovery-entry-v2`；原 pending 字节完整保存在 `*.pre-entry.pending.json`，本轮中间修订也保留。旧 F02/F03/F04 证据不覆盖。

最新版 approved_by、expires_at 为空，authorized 及全部 permissions=false，future_facts.archive_completed=false、new_system_identifier=null。没有创建 approved 文件、真实 attempt 目录、存档目录或 runtime authorization；原环境授权及期限未修改。

## 8. 离线验收结果与原始证据

| 验证 | 结果 |
| --- | --- |
| 本轮专项 | **53/53 PASS**，包括无授权/每项许可、用户身份、源内容/ACL、部分存档、伪回执、初始化超时/未知输出/资源/结构、错误身份、审批/代码/schema/原生摘要变化、Job 生命周期、PG-07 阻断 PG-08、模拟隔离及迟到拒绝 |
| 最终适用后端回归 | **757/757 PASS**，225.435 秒，0 failure/error/skip；原 704 个 ID 全保留 |
| 旧 F02/F03/F04 断言 | 原 32/24/26 项文件及断言未修改，包含在最终回归中 |
| 原 PG-07 | 测试源码摘要未改，停止后分类调用必须为 0 的断言仍在；本轮未实际运行该 PG case |
| 原集群/ACL | 970 文件与原清单一致；997 个 Owner/Group/DACL 描述符与 F04 一致 |
| 既有业务/冻结资产 | 2253 项摘要匹配，无 Agent、供应商费用、冻结 RAG 或人工评审资产修改 |
| 真实操作 | 原目录移动/重建、PostgreSQL 程序/连接、真实 PG case、收费模型、Embedding、生产操作均为 0 |

测试封锁真实 Popen/run、网络 connect/bind、psycopg.connect、OpenAI 和 HTTP；用于生产方法的 Job/连接替身仍是受控模拟。原回归中的临时 SQLite 不替代 PG 并发验收。13 项历史静态导入技术债未清理，不声明任意全仓测试发现均通过。

证据在 `.cache/p6e1b-f04-entry/`：`new-tests.log`、`functional-summary.json`/`functional-tests.txt`、`integrity.json`、`materials-final.json` 及旧源码/草稿摘要。`offline-simulation/` 提供带明确模拟标记的串行结果、存档/初始化/identity 回执样例；其中 PG07/PG08 的模拟 PASS 不是原 PG 测试的实际通过。

安全复验命令：

```powershell
$env:PYTHONPATH='.;src;tests'
.\.venv\Scripts\python.exe -m unittest test_p6e1b_f04_execution_entry -v
.\.venv\Scripts\python.exe .cache/p6e1b-f04-entry/run_checks.py functional
.\.venv\Scripts\python.exe scripts/p6e1b_recovery.py check
```

## 9. 仍需本人明确批准的操作

新清单 `execution-entry-approval-checklist.pending.md` 逐项列明：精确旧目录隔离存档及私有 ACL 收紧；原固定目标重新初始化；上述明确时限和日志限额；启动并持有本轮独立服务器 Job；固定四角色/两库/表/ACL/GUC/运行标记初始化；五个测试连接；私有测试凭据、passfile/操作 SQL 的受控使用；根据实际完成结果记录新 identifier；合成夹具及 PG-01～08；完成或失败时仅清理本次实例/Job。

批准必须有真实批准人和带时区期限，针对最终代码/计划/policy/原生凭据。当前材料不构成批准，也不会自行填写或延长旧授权。没有权限修改业务库、删除库/schema、部署或调用收费模型。

**最终：离线入口 PASS；真实恢复 BLOCKED；PG-01～08 NOT EXECUTED。** 本阶段停止，不自动执行 recover、移动目录、初始化数据库、启动实例/模型或部署。
