# P6-E1B-F04 · PostgreSQL 原集群恢复准入与执行准备

日期：2026-11-10（Asia/Taipei）。**准备材料已完成；实际恢复准入：BLOCKED。**

本轮已采集原目录全量文件清单及 ACL，确定方案 B 的精确存档位置、建议限额、待批准计划和失败停止顺序，并独立检查实际入口。原执行代码仍存在新 attempt 与后续阶段衔接缺口；依照本任务允许“提出或实施最小必要修复”的范围，本轮交付具体提案，**没有继续扩展执行器或放开旧命令入口**。这既不是数据库恢复成功，也不是仅把 pending 中 authorized 改成 true 就能执行的授权。

未移动、复制、删除、清空或重新初始化原集群；未运行 initdb、postgres、pg_ctl、psql、pg_controldata；未启动/连接 PostgreSQL、执行 PG-01～08、调用收费模型或 Embedding。旧授权、有效期、原失败记录与 F01～F03 报告均保持原样。

## 1. F03 可复用的能力与限制

F03 的真实 Windows 无害程序测试为 10/10 PASS，证明本机版本下的挂起创建、Job 归属后恢复、多级后代终止、管道排空及句柄释放；异常状态不会被模拟成功覆盖。本轮没有重跑原生测试，只重新核验已有凭据与当前代码、测试程序、Windows build 26300、CPython 3.12.10 的绑定，结果仍匹配。

它不能外推以下结论：原 initdb 已成功、原超时根因已消失、PostgreSQL 特有子进程已经验证、服务器可在独立 CLI 退出后继续运行，或 PG 并发/模型质量验收通过。F03 只允许固定 initdb 的 WindowsJobProvider 与通用内部 OwnedJobProcess 也不是同一个授权入口。

依据：F03、F02、F01 及原真实执行报告；本轮额外证据保存在 `.cache/p6e1b-f04/` 和原执行 private 目录内的独立准备子目录。

## 2. 固定目标、实际状态与保全范围

| 项目 | 本轮核验结果 |
| --- | --- |
| run_id | `c5412c7c4bed43fba4a721909eea1041`，未改变 |
| 原环境计划规范 SHA-256 | `40a30688281cf0cdd2a73af02bd5c345e2671d9fcc672a84c434947b80a09e2b`，当前仍匹配 |
| 原固定目标 | `E:\AllProject\data-analysis-agent\.cache\p6e1b-env\c5412c7c4bed43fba4a721909eea1041\cluster` |
| 固定端口 | 55441；本轮不 bind、不连接，不能宣称当前可用，实际执行前必须再检查 |
| 新准备 attempt_id | `a4b479a8d9154994a402f7b13f1a00a6`；只是恢复尝试标识，不是新 PostgreSQL run_id |
| 原目录 | 存在；970 个普通文件、26 个子目录，加源根目录共 997 个条目；41,636,404 字节 |
| 内容与元数据 | 文件长度/摘要与 F01 一致；F04 开始及结束的内容、mtime/ctime/属性清单一致；重解析项 0 |
| 全量 ACL | 997 条 Owner、Group、DACL/继承控制 SDDL；两次采集一致；未改原权限 |
| 完整性 | **UNDETERMINED**；仍缺 initdb 真实成功退出及完整日志，不能启动原部分目录 |
| 存档、新执行目录 | 计划存档位置及新执行 attempt 目录均尚未创建 |

两个测试库保持为 `p6_e1b_c5412c7c4bed43fba4a721909eea1041_business`、`p6_e1b_c5412c7c4bed43fba4a721909eea1041_tasks`。四类运行角色保持原前缀加 `creator`、`reader`、`writer`、`task`；bootstrap 仍为原前缀加 `bootstrap`。没有业务 reviews 库、备用端口或备用集群路径。

### 2.1 清单与权限证据

私有准备目录：

```text
E:\AllProject\data-analysis-agent\.cache\p6e1b-exec\private\recovery-preparation\a4b479a8d9154994a402f7b13f1a00a6
```

| 文件 | 内容 |
| --- | --- |
| source-files.json | 根及全部子条目、相对路径、种类、长度、文件 SHA-256、mtime/ctime 纳秒、Windows 属性 |
| source-acl.raw.json / source-acl.json | 全量原 SDDL；后者按路径排序、绑定文件清单、逐条 SDDL 摘要 |
| source-acl.after.raw.json | 本轮结束时重新采集，用于独立比较 |
| archive-manifest.pending.json | 全部 997 条目、原内容/元数据、原 ACL 摘要、精确源/目标及“未存档/未验证”事实 |

文件清单规范 SHA-256：`3958cca5dd6da9b120695380a02526bd9c5e76ca8d3329b358f419295493c72c`。

ACL 清单规范 SHA-256：`2e7c868c4bb71dda151e256aea4e042723d17b970aac2006110476aadcda99cb`。

待存档清单规范 SHA-256：`5dc1cff5a25e256fd67b6b80eb2b2e398e4cafea2cc94b1d940da7bd3bfcac89`。

只读 ACL 脚本检查固定目录和祖先的重解析点，输出文件使用 CreateNew，不运行 PostgreSQL、不设置 ACL。私有准备目录的现有继承权限已观察为只向当前用户授予访问，没有更改原 private 或源目录权限。报告不公开原 SDDL 中的账户标识。

此清单覆盖普通文件的默认数据流及访问控制描述符，不是磁盘镜像或实体备份；未采集 SACL 审计策略、有效访问测试或另行证明 ADS/硬链接等文件系统特性。实际存档前若发现额外流、链接、锁或内容变化，须停止并更新保全范围，不能忽略差异继续。

### 2.2 精确方案 B 存档位置

```text
E:\AllProject\data-analysis-agent\.cache\p6e1b-exec\private\archives\c5412c7c4bed43fba4a721909eea1041\a4b479a8d9154994a402f7b13f1a00a6\cluster
```

源与存档同属 E:，互不包含；存档路径当前不存在，不能覆盖、合并或自动选新位置。实际执行时先重新核验清单、ACL、目录使用者及端口，再按明确批准进行一次同卷移动。失败立即停止，不跨盘复制后删除，不自动搬回、清空或换目录。

原 ACL 全量证据必须保留。存档实体在移动后按单独批准的私有策略收紧：保护继承，仅批准的当前执行账户、SYSTEM、Administrators 具有所需访问；各文件/目录逐项检查，不仅检查父目录。保留原 ACL 证据与验证新的私有权限是两件事，不能要求收紧后 DACL 仍等于原宽权限。

存档后核验相对路径集合、970 个文件长度/摘要、目录数量及新 ACL；只有通过后才能产生真实存档回执。原固定目标应当**不存在**，不预先 mkdir cluster；当前执行器连已存在的空目录也会拒绝，不在任何已有目录重复 initdb。旧 journal/failure/control 观察等失败证据继续保留。

## 3. 有限策略与待批准计划

建议值不是获准参数，也不是 PostgreSQL 性能结论：

| 字段 | 建议 | 依据、范围与不足 |
| --- | --- | --- |
| execution_seconds | 180 秒 | 给一次受控初始化保留有限的进程创建、文件写入/同步、输出等待余量；未对真实 initdb 做基准，不能保证足够 |
| termination_seconds | 10 秒 | 发出 Job 终止后的有限直接进程等待；不是承诺内核一定在 10 秒返回 |
| cleanup_seconds | 30 秒 | 包含上述终止等待，以及树归零、EOF/线程、句柄、安全日志和完成校验；大于 termination，不另加 10 秒 |
| max_log_bytes | 1,048,576 | 每路脱敏后 UTF-8 日志上限，两路最多约 2 MiB；截断必须明确且不得 completed |
| policy version | p6-e1b-initdb-policy-v1 | 沿用 F02 实际解析规则，没有更换费用/业务合同 |

原 45 秒是客户端等待值；F01 无法唯一定位根因，文件时间也不是可靠的进程运行时间。180 秒只是待审阅的一次尝试上限，不由“45 秒超时”推算精确耗时，更不替代 F02/F03 的日志和生命周期修复。执行加清理的名义等待窗口约 210 秒，OS/文件系统同步调用并非硬实时；迟到或清理不明继续失败关闭。后续客户端、启动及 PG 测试的阶段截止也必须由最终入口明确绑定，不能把 statement_timeout 当作整个任务限额。

材料目录：

```text
E:\AllProject\data-analysis-agent\.cache\p6e1b-f04\a4b479a8d9154994a402f7b13f1a00a6
```

其中包含 `recovery-plan.pending.json`、`recovery-policy.pending.json`、`recovery-operations-approval.md`、`recovery-steps.md`、`entry-minimal-change-proposal.md` 以及只读 ACL 采集脚本。

| 当前材料 | 规范 JSON SHA-256 |
| --- | --- |
| recovery-plan.pending.json | `2d6f572a13cb35771ce2a47a68700b7548ea5372848fc114f6a51b3f6d7f4f2d` |
| recovery-policy.pending.json | `0c7c32eb075f4065441beee98d922ae26cfb5b30ce48a2aeb3a4494275d79550` |

plan 绑定固定身份、库/角色、attempt、存档位置、文件/ACL 摘要、历史失败摘要、代码与入口、原生凭据及限额。批准人/到期时间为空，authorized 和操作许可全部 false；archive_completed=false，archive_evidence_sha256 为空，新 system identifier=null。后面三项是尚未发生的运行事实，不能要求负责人提前批准为“已经完成”，必须由真实证据推导后写入**新尝试产物**。

旧 F02 草稿原样保留，其代码摘要已过时；没有覆盖或升级成授权。本轮仅修订自己的未批准准备草稿，前一修订及原因留在同目录。当前 plan 的入口状态明确写为 BLOCKED，不能当作现有 check_grant 的有效执行 grant。

### 3.1 当前版本与原生凭据

| 对象 | 当前 SHA-256 |
| --- | --- |
| scripts/p6e1b_executor.py | `7b446a13ab121db61ed5bc32dbd72f95ed89e70fa6d2b52919eab1860b99cffa` |
| .cache/p6e1b-exec/execute.py | `0879a9d11ae2c35335c6412685ed6e9b19cd6428e23b6e329f5ed5087f54663e` |
| scripts/p6e1b_windows_job.py | `63d27fa33658dbd791666175951310d9400f1a9dd26ea6a7ba420b028b39e2ee` |
| F03 native-summary.json | `216a1866ed3f273bbc5f519240fc6f00e8652f3cd37d4acb92c92e7a93b22887` |
| F03 native-evidence.json | `0c83be8c9e79bf55592726d4762d106e32b39ff079bb0f2ff2e284ce411a2724` |

这些对象本轮未修改，F03 凭据仍匹配。实施入口最小提案后执行器/入口摘要必然变化，必须生成最终修订再明确复核；不能自动沿用本草稿批准。若改 Job 实现、原生测试、Python 或 Windows，也不能外推 F03 旧 PASS。

## 4. 实际执行通路审计与最小提案

| 检查项 | 当前源码结论 | 最小必要处理（提案，未实施） |
| --- | --- | --- |
| WindowsJobProvider 调用 | 已接入 InitdbExecutor；验证授权和原生凭据后可创建固定 initdb，但当前前置门会先拒绝 | 复用现有实现，不建新进程树框架 |
| 历史 FAIL | main 无条件拒绝，command 也强制 previous_state=needs_manual_recovery | 只为明确批准的新 attempt 建立分支，核验并引用原失败摘要；旧分支与旧历史保持 |
| attempt 独立账本 | 当前 private/f02、initdb-state.f02.json、execution-journal.f02.json 仍共享，日志随机 nonce 不是批准的 attempt_id | 可选 attempt_id + 固定 private/attempts/id；默认 F02 行为不变，失败不得复用 |
| 存档事实 | 当前 grant 要求 archive_completed=true，但没有实际移动和回执通路 | 将不变的批准与执行观察分开，真实存档核验后生成运行 grant，不回填批准文件 |
| render / identity | 仍依赖旧 AUTH 的空 expected_system_identifier | completed 后才受控观测新身份，派生新的 attempt 运行授权、SQL和摘要；旧 AUTH 不变 |
| start / provision | command 的非 initdb 后备全部禁止，不能执行旧 pg_ctl/psql 参数 | 新分支提供各阶段的精确参数白名单和有限控制，保留通用入口关闭 |
| 启动 Job 生命周期 | 关闭 KILL_ON_JOB_CLOSE Job 会终止存活服务器；pg_ctl 退出不能要求 Job 已清空 | 在同一恢复协调进程持有服务器 Job，完成 provision/live/PG 后受控停机清理；不新增独立自由执行框架 |
| psql 凭据 | 旧代码依赖 PGPASSWORD/口令输入，现有 Job 过滤环境且 stdin=DEVNULL，不可直接套用 | 私有 passfile + 不含密码的连接参数；私有操作 SQL 展开固定密码/连接指令，不开放任意环境或交互 |
| live / PG | 五 DSN、身份门和 SafeResult 可复用，但受旧授权/共享状态阻断 | 新运行授权和运行标记绑定 attempt/摘要；实时预检通过后才运行既有 PG 测试 |

passfile 参数方案已查阅本机安装的 PostgreSQL 18 官方 `libpq-pgpass.html`、`libpq-connect.html`，本轮只读文档，没有连接测试；具体操作 SQL 及各次连接的传参仍须在最终入口中验证。不能把密码放进 argv、报告、普通日志，或依赖当前被过滤的 PGPASSWORD 自动生效。

真实 initdb 完成仍须：返回码 0、直接进程与 Job 后代结束、双路 EOF/读取线程结束、句柄确认释放、完整脱敏且未截断日志、完成提示、预期目录结构、状态落盘和所有检查均无错误且未迟到。pg_control/PG_VERSION/shut down 单独不足。之后才允许读真实 identifier；不预填原部分集群的控制值。

详细提案列出了函数、状态隔离和阶段产物要求。本轮选择按任务允许提交提案，**未将执行通路未完成包装为 READY**。提案实施、离线验证、最终摘要复核和明确批准均属后续同一次恢复任务的前置工作，不自动增设 F05 或其他基础设施审查阶段。

## 5. 所需本人批准与获准后的步骤

原创建授权确实存在，期限 `2026-11-11T23:59:00+08:00` 原样保留；本轮没有声称它不存在，也没有延长它。它不能自行覆盖旧目录隔离、重建、新限额和新入口。

需负责人明确批准：精确源/存档位置的隔离移动及私有权限收紧；原固定目标重新初始化；180/10/30 秒及日志上限；启动本轮独立实例；固定四角色/两个测试库与表、ACL/GUC/标记初始化；五个测试连接；合成夹具和 PG-01～08；私有测试凭据的受控使用；新 identifier 的事实记录；结束或失败时仅清理本次持有的实例/Job。批准须有真实标识和带时区期限。不得授权数据库/schema 删除、业务库、其他环境或收费模型。

获准后的顺序已经列入 recovery-steps.md：

1. 最小入口适配及最终摘要复核、明确批准；固定身份、无重解析点、目录使用者和端口检查。
2. 重核原清单/ACL/历史，按精确位置一次隔离移动；失败即停，不覆盖或自动补救。
3. 存档数据及私有 ACL 核验；固定新目标必须不存在，生成独立尝试的真实存档回执。
4. 新 attempt 账本先落盘，使用既有 WindowsJobProvider 与批准限额初始化；任何超时/错误即停。
5. 完成后观测真实 system identifier，生成新运行授权及 SQL，旧证据不改。
6. 同一协调进程持有服务器 Job，启动并执行受控初始化，任一步失败保留新尝试结果并清理本次资源。
7. 五 DSN 注入仅本轮进程，完成实时身份、角色、ACL、GUC、运行标记检查。
8. 执行既有 PG-01～08；保留 PG-02/03 Event 屏障，PG-05 如实记录计数新鲜度边界。PG-07 停止后分类调用必须为 0；失败立即停止，PG-08 不运行。
9. 保存新尝试结果、受控停止本次实例并核验资源；PG 全通过和适用回归通过前不能标记 E1B PASS。

这是执行顺序和停止契约，**不是本轮已执行操作**。当前源目录、存档位置、权限或进程身份不明时继续 BLOCKED，不自动选择其他实例。

## 6. 修改、离线验证与证据

新增 `scripts/prepare_p6e1b_recovery.py`：只提供 snapshot/draft/check；固定 run/路径，拒绝重解析点和已有产物覆盖，生成清单/ACL采集脚本、建议策略和未批准计划，检查当前摘要。它没有 archive/approve/initialize/start/provision/live/tests 执行分支，不调用 Popen、数据库或网络。

新增 `tests/test_p6e1b_f04_recovery_preparation.py`：26 项正式离线测试，覆盖固定/不重叠路径、非法 attempt、无业务库后备、全量清单及内容变化、ACL 缺项/重复/范围/Owner/Group/DACL、未批准字段、有限策略、历史/版本绑定、拒绝覆盖、只读快照、不可运行命令及旧入口仍关闭。封锁 Popen/run、网络 connect/bind、psycopg.connect，没有 skip/expectedFailure。

| 验证 | 最终结果 |
| --- | --- |
| 合并适用后端回归 | **704/704 PASS**，160.306 秒，0 failure/error/skip；F03 原 678 个 ID 全保留 |
| F04 专项 | 26 项包含于上表；F02 原 32、F03 原 24 断言均未修改 |
| F02/F03/F04 专项合并 | 82/82 PASS；最后全量回归也覆盖最终 ACL Group 反例 |
| 原执行代码/旧授权/计划/历史/旧测试 | F04 开始摘要清单全部匹配，无修改 |
| 既有代码及冻结资产 | 2253 项全部匹配，无 Agent、供应商费用、RAG 或人工评审资产改动 |
| 原集群 | 970 文件内容与 F01 一致；F04 前后清单、997 个 ACL 描述符均一致 |
| 存档/新初始化/新运行授权 | 未执行、未创建；archive 和 private/attempts/id 均不存在 |
| 原生 Windows 测试 | 本轮执行数 0；仅核验 F03 已有真实凭据，不能把离线测试当原生证明 |
| PostgreSQL 命令/连接、PG 测试、收费模型、Embedding、生产库 | 均为 0 |

原有离线回归中的临时 SQLite 合成数据不能替代真实 PostgreSQL 并发验证。13 项历史私有导入问题未清理，不声明全仓任意发现均通过。

安全复核命令（只读材料检查及受保护离线测试）：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_p6e1b_recovery.py check --attempt-id a4b479a8d9154994a402f7b13f1a00a6
$env:PYTHONPATH='.;src;tests'
.\.venv\Scripts\python.exe -m unittest test_p6e1b_f04_recovery_preparation -v
.\.venv\Scripts\python.exe .cache/p6e1b-f04/run_checks.py functional
```

证据：`functional-summary.json`/`functional-tests.txt`、`final-integrity.json`、`private-metadata-acl-observation.json`、本轮独立材料及私有清单。前一未批准草稿也已保留；没有改原失败账本。

## 7. 准入结论

**BLOCKED**，原因是新的恢复批准及有限参数批准尚缺，真实入口的 attempt/存档回执/运行身份/后续阶段衔接也尚未实施。准备材料和离线检查已完成，不能把它们表述为实际执行 READY。

原集群完整性仍 UNDETERMINED，PG-01～08 为 NOT EXECUTED，PG-07 风险未被本轮消除。没有自动开展恢复、创建 F05、延长原授权或进入真实模型/生产部署。只有最终入口和摘要完成复核、负责人明确批准具体恢复计划后，才能在上述固定范围内进入实际执行。
