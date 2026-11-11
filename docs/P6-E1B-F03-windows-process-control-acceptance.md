# P6-E1B-F03 · Windows 进程树控制与恢复准入验证

日期：2026-11-10（Asia/Taipei）。范围：执行器、无害原生子进程测试、恢复条件只读复核。

**Windows 进程树控制实现与本机验证：PASS。真实数据库恢复准入：BLOCKED。** 本轮没有运行 PostgreSQL 程序、初始化或恢复集群，也没有执行 PG-01～PG-08。原集群完整性仍为 UNDETERMINED。无害 Python 进程测试不能证明原 initdb 超时根因已经消失，也不能证明 PostgreSQL 初始化成功。

## 1. 修改前行为与本轮边界

F02 已通过 32 项执行器故障测试和 654 项适用后端回归，但只有 FakePopen 与假时钟证据。真实分支在创建进程前拒绝，缺少独立验证的 Windows 进程树控制器；模拟的树状态不能授权真实执行。

本轮按用户授权，仅创建专用无害 Python 测试进程及其后代，产生本轮测试日志与 ready 标记。未调用 initdb、postgres、pg_ctl、psql、pg_controldata，未建立 PostgreSQL 连接，未运行收费模型或 Embedding。原有离线回归使用的临时 SQLite 合成数据不作为 PostgreSQL 验收证明。没有修改 Agent State、Intent Router、Planner、Tools、Validator、供应商费用合同和主题分类代码。没有删除、移动或重置原部分目录，也没有改写批准人、到期时间或 system identifier。

原环境固定身份继续为：

- run_id：`c5412c7c4bed43fba4a721909eea1041`。
- 原计划规范 JSON SHA-256：`40a30688281cf0cdd2a73af02bd5c345e2671d9fcc672a84c434947b80a09e2b`。
- 原目标：`E:\AllProject\data-analysis-agent\.cache\p6e1b-env\c5412c7c4bed43fba4a721909eea1041\cluster`。
- 候选端口仍为 55441。本轮没有绑定、连接或重新确认该端口可用，恢复前必须另行核验。

无害进程的唯一随机 token 仅隔离 `.cache/p6e1b-f03/native/` 下的测试文件，不是新的 PostgreSQL run_id 或恢复目标。

## 2. Windows 控制设计与调用关系

```text
独立恢复批准 + 限额 + 当前代码/原生测试证据摘要
    → InitdbExecutor 验证固定目标、旧状态及授权
    → WindowsJobProvider 验证本机原生测试凭据
    → 创建匿名 Job，设置 KILL_ON_JOB_CLOSE
    → CREATE_SUSPENDED | CREATE_NO_WINDOW 创建直接进程
    → AssignProcessToJobObject → IsProcessInJob 确认
    → 确认该 PID 只有一个初始线程 → ResumeThread
    → 有限 communicate / wait
    → 必要时 TerminateJobObject（仅本次 Job）
    → Job ActiveProcesses=0 + 直接进程退出 + 双路 EOF/读取线程结束
    → 关闭并核验句柄 → 原有日志、结构及 completed 验收
```

### 2.1 归属与执行竞态

不先运行再尝试归属。`Popen` 创建时初始线程挂起，Job 归属和成员检查通过后才恢复。CPython 关闭了创建时的线程句柄，因此按本次直接 PID 枚举线程，要求恰有一个初始线程，打开该线程并要求 ResumeThread 返回原挂起计数 1。线程不唯一、归属失败、成员查询失败或恢复计数异常，均终止受控资源并拒绝继续；不会为了兼容宿主而开启 breakaway。

Job 不命名、不向后代传递 Job 句柄，设置 KILL_ON_JOB_CLOSE，未设置 BREAKAWAY_OK 或 SILENT_BREAKAWAY_OK；正常创建的后代继承 Job 归属。当前宿主已实际通过归属测试，其他 Windows 构建、Python 版本或外层 Job 限制仍需重新验证，归属失败不回退到普通 Popen。规则依据：[Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)、[AssignProcessToJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject)、[Nested Jobs](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs)。

### 2.2 超时、终止和资源证据

`terminate()` 针对本对象持有的 Job 调用 TerminateJobObject。没有按名称杀 postgres 的代码，也不根据任意 PID 列表结束进程。仅在创建失败、进程尚未恢复等安全收尾中，允许终止本次 Popen 返回的直接进程句柄；不会影响端口拥有者或其他实例。

父进程退出不能证明后代退出。通过 QueryInformationJobObject 的 Accounting 和 ProcessIdList 分别记录活动进程数及成员；在截止内观察 ActiveProcesses=0，再核对直接返回码。然后收集 stdout/stderr 至 EOF、确认 CPython 两个读取线程已结束、关闭流和 Job/直接进程句柄，并核验句柄已失效。参考：[TerminateJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-terminatejobobject)、[QueryInformationJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-queryinformationjobobject)。

当前 Windows 对已关闭句柄的检查会抛出 `STATUS_INVALID_HANDLE (0xC0000008)`。代码只识别这一精确状态，或普通 FALSE + ERROR_INVALID_HANDLE=6；其他错误不能冒充“句柄已释放”。该差异由首轮真实失败定位，失败摘要和事件仍保存在 `native-initial-failure-*.json`，不是删除断言后通过。

终止失败、Job 未清空、读取线程仍活跃或句柄关闭失败，均保持 `cleanup_failed`，对应 `cleanup_incomplete`。测试收尾可再次针对该对象执行安全清理，但不会清除此前失败状态。创建途中清理失败时，异常保留安全诊断及受控对象引用，执行器保留 pending_resources，不能因直接 Popen 尚未返回就丢失残留资源事实。

所有生命周期事件记录 UTC 与 monotonic。等待和清理使用有限截止；没有线程超时冒充 OS 取消。CreateProcess、Win32 控制和文件系统调用仍非硬实时保证，调用迟到或状态未知不允许 completed。

### 2.3 执行器接入和授权门

`InitdbExecutor` 只有显式传入精确的内部 `WindowsJobProvider` 才可走原生路径，禁止混入 FakePopen、外部 tree_probe、假时钟或 simulation。Provider 只允许现有固定 initdb 完整参数序列；`stdin=DEVNULL`、`--pwfile`、固定私有密码文件路径及固定目标均保留，没有命令参数明文密码。

Provider 在执行前重复核验恢复授权与有限 policy，并要求负责人批准的恢复合同绑定：

| 字段 | 含义 |
| --- | --- |
| executor_sha256 / adapter_sha256 | 当前执行器和缓存入口版本 |
| tree_provider_sha256 | 本次 Windows Job 实现版本 |
| tree_validation_sha256 | `native-summary.json` 文件摘要 |
| tree_evidence_sha256 | `native-evidence.json` 文件摘要 |
| policy_version / policy_sha256 | 有限执行、终止、清理和日志限额 |
| 原有 run/plan/target、批准/期限、存档及 route 字段 | F02 独立恢复授权仍全部必需 |

凭据还必须匹配当前 Windows/Python、当前测试程序和测试套件摘要，10 项要求的测试 ID 必须齐全、无失败/错误/跳过，证据必须标为真实 Windows 且安全收尾完成。摘要契约不是防止具有本机文件修改权限者伪造批准的密码学签名；仍依赖服务端受控文件和负责人独立审阅，不能接受客户端自报 PASS。

本轮只保存可供审阅的证据摘要，没有生成已批准恢复文件。F02 `pending_recovery()` 增加两个空证据字段；旧 pending 文件原样保留，不自动刷新代码摘要或填写授权。缓存入口仍保留历史 FAIL 阻断、原部分目录阻断和后续阶段验收。7 项闭门测试证明 initialize/render/start/provision/live/authorize/旧命令后备均不能越权执行。

当前 `command()` 的非 initdb 命令后备继续禁止，不能据此声称完整 PG 执行链已可恢复。后续在独立授权下还需审查历史失败状态的处理，以及启动/建库/测试步骤的具体受控执行方式，本轮不解除这些屏障。

## 3. 实际原生测试与模拟测试

原生环境：Windows build 26300，CPython 3.12.10，64 位 AMD64。原生测试显式运行 `tests/p6e1b_f03_native.py`，不加入普通离线自动发现；在非 Windows 环境直接 BLOCKED，不使用 skip 或 FakePopen 替代。

专用程序 `tests/fixtures/p6e1b_safe_child.py` 仅在固定测试子目录写独占 ready 文件，输出固定标记、创建同一程序的有限后代或有限等待，不包含网络、数据库或业务程序逻辑。父级 Popen 只允许该程序的精确前缀，子级环境只保留必要 Windows 变量，不传递数据库和模型凭据。通过 ready 文件的 token/PID 和 Job 成员查询同步；0.05/0.01 秒仅用于制造等待超时，不用 sleep 猜测进程树已形成。

| 真实 OS 用例 | 实际证据与结果 |
| --- | --- |
| 正常退出 | 恢复前 ready 不存在、直接进程未退出且已属 Job；返回 0、双路固定输出、句柄失效：PASS |
| 直接子进程超时 | 原生 communicate 超时；终止本次 Job，活动数归零、输出 EOF、读取线程结束：PASS |
| 父退出、后代继续 | 直接返回 0 时后代 PID 仍在本次 Job；清理后归零：PASS |
| 后代持有 stdout/stderr | 父已退出但 communicate 实际超时；Job 终止后两路均排空并关闭：PASS |
| 创建后归属失败 | 向真实 AssignProcessToJobObject 注入无效 Job 句柄，OS 返回错误；未 Resume、未写 ready，直接进程及两个控制句柄清理确认：PASS |
| 终止控制失败 | 向真实 TerminateJobObject 注入无效句柄；保留 cleanup_incomplete，测试最终仅清理自己持有的真实 Job：PASS |
| 等待截止 | 活进程的真实 wait 超时，有限关闭不能虚报成功，保留 cleanup_incomplete：PASS |
| 句柄关闭失败 | 对真实 CloseHandle 注入无效句柄；正常流程拒绝完成，随后安全收尾仍保留失败：PASS |
| 多级进程树 | 四层逻辑测试程序均属同一 Job，实际成员包含 Python 启动器，共观察 12 个 PID；终止后活动数 0：PASS |
| 不影响其他进程 | 分别创建两个独立 Job，终止第一个后第二个 ready PID 仍在运行，随后各自清理：PASS |

**10/10 原生测试 PASS，0 failure/error/skip，2.188 秒。** 归属、终止和关闭故障是对真实 Win32 API 参数的受控故障注入，不是声称本机自然出现权限故障，也不是 FakePopen。等待超时、持管道和存活后代均由真实 OS 行为产生。其他宿主的自然 ACL/嵌套 Job 拒绝、内核挂起及 PostgreSQL 特有后代行为尚未实测。

新增 24 项正式离线测试验证空/越界命令、未知故障、非法限额、未经验证的 Provider、原生与模拟混用、缺失/失败/跳过/过期版本/缺项的原生凭据、不同 Windows 构建、来源或清理状态不符、未知句柄错误、默认无批准及无原生回退。其合成凭据只检查哈希契约，不能作为真实 Windows 证明。首次离线运行还发现 `platform.platform()` 会隐式启动 `ver`；改用不创建进程的 `sys.getwindowsversion()`，保留禁止 subprocess 的断言，相关初始失败日志已保留。

## 4. 修改文件、函数与兼容性

| 文件 | 本轮改动 |
| --- | --- |
| `scripts/p6e1b_windows_job.py` | 新增 Kernel、OwnedJobProcess、checked_validation、WindowsJobProvider；归属前挂起、独立 Job、有限清理、句柄/读取线程验证、审阅凭据绑定 |
| `scripts/p6e1b_executor.py` | InitdbExecutor 可选 native_provider；原生/模拟严格分离；原生完成证据与启动失败诊断；pending_recovery 空证据字段 |
| `.cache/p6e1b-exec/execute.py` | 内部接入 WindowsJobProvider；历史失败、授权和下游阶段门保持关闭 |
| `tests/fixtures/p6e1b_safe_child.py` | 专用无害后代程序及固定工作目录约束 |
| `tests/p6e1b_f03_native.py` | 显式运行的 10 项真实 Windows 测试及安全证据输出 |
| `tests/test_p6e1b_f03_process_control.py` | 24 项正式离线准入及错误回归 |
| 本报告及 `.cache/p6e1b-f03/` | 独立证据、回归清单、只读保全和恢复准入复核 |

F02 原 32 项测试文件摘要仍为 `a4afa8b344995c8c891c4eb27f91a433ab8b80e11c90bba607dbfb31f199c65b`，未改断言。旧单维度、P6-A～D、E1A、RAG 及 Worker 行为没有本轮代码修改。

| 回归 | 结果 |
| --- | --- |
| F02 原断言 + F03 离线准入 | 56/56 PASS，其中原有 32、本轮 24 |
| 合并适用后端回归 | **678/678 PASS**，230.383 秒；0 failure/error/skip；原 654 项 ID 无缺失 |
| 真实 Windows 原生测试 | 10/10 PASS，独立于上述离线数量 |
| 缓存入口闭门检查 | 7/7 PASS，真实程序、网络和数据库调用 0 |
| 原部分集群保全 | 970 文件、26 目录与 F01 清单一致；文件摘要/长度/mtime 未变；重解析项 0；三处脱敏 ACL 观察一致 |
| 既有业务与冻结资产 | `.cache/p6e1b/before-hashes.json` 中 2253 项全部匹配；本轮初始摘要清单仅执行器和缓存入口两项按计划改变 |

历史 13 项私有接口导入技术债未清理，也不把“适用功能回归通过”描述成全仓所有测试发现通过。真实 PostgreSQL 测试未运行。

复验入口（当前虚拟环境及已批准的无害原生测试范围）：

```powershell
$env:PYTHONPATH='.;src;tests'
.\.venv\Scripts\python.exe -m p6e1b_f03_native
.\.venv\Scripts\python.exe -m unittest test_p6e1b_f02_executor test_p6e1b_f03_process_control -v
.\.venv\Scripts\python.exe .cache/p6e1b-f03/run_checks.py functional
```

重新运行原生测试会产生新的凭据摘要，不能直接复用已经审阅的旧摘要。

## 5. 恢复准入重新核验

| 前置条件 | 当前事实 / 判断 |
| --- | --- |
| 原目录保留 | 未修改、移动、删除或覆盖；完整初始化成功证据仍不足：UNDETERMINED |
| 原环境计划及目标 | 规范计划摘要与固定 SHA 一致，无换 run、端口、目录或业务库后备 |
| 原批准 | 文件及到期时间 `2026-11-11T23:59:00+08:00` 原样保留；原创建批准不覆盖存档/重建及新执行策略 |
| 独立恢复批准 | `recovery-plan.approved.json` 不存在；本阶段请求没有授权实际恢复：BLOCKED |
| 有限执行配置 | `recovery-policy.approved.json` 不存在；F02 草稿三项时限与日志上限仍为 null：BLOCKED |
| 原目录处理许可与保全 | 尚无负责人指定存档位置、权限保留、移动/重建许可及实际存档核对：BLOCKED |
| 当前代码与证据绑定 | 已有可审阅摘要；旧 F02 草稿代码摘要已不匹配，不能自动更新成批准状态：BLOCKED |
| 历史失败账本 | 原 timed_out/FAIL 保留；入口不自动恢复。如何在明确恢复授权后处置该状态仍需独立审查 |
| PG-07 Worker 停止传递 | 原“停止后分类调用为 0”断言仍在；本轮未执行 PG-07、未修改 Agent，风险未解除 |

恢复方案 A 仍只允许在取得足够成功证据后，申请固定目录的独立完整性验证；不能凭 pg_control 或 shut down 宣称完整。方案 B 必须先获得原目录保全、隔离存档、指定位置及同一目标重建的明确批准，并审阅有限配置和本轮摘要。两种方案本轮均未执行。

当前摘要（完整清单位于 recovery-readiness.json，未写入任何授权文件）：

| 对象 | SHA-256 |
| --- | --- |
| 执行器 | `7b446a13ab121db61ed5bc32dbd72f95ed89e70fa6d2b52919eab1860b99cffa` |
| 缓存入口 | `0879a9d11ae2c35335c6412685ed6e9b19cd6428e23b6e329f5ed5087f54663e` |
| Job 实现 | `63d27fa33658dbd791666175951310d9400f1a9dd26ea6a7ba420b028b39e2ee` |
| 原生结果摘要文件 | `216a1866ed3f273bbc5f519240fc6f00e8652f3cd37d4acb92c92e7a93b22887` |
| 原生事件证据文件 | `0c83be8c9e79bf55592726d4762d106e32b39ff079bb0f2ff2e284ce411a2724` |

## 6. 证据链与完成结论

| 证据 | 观察与结论 | 后续路径 |
| --- | --- | --- |
| E01 `native-summary.json`、`native-evidence.json`、`native-final.log` | 真实 Job 归属、后代终止、EOF、读取线程及句柄释放通过；故障保持失败关闭 | 仅作为指定 OS/Python/代码版本的独立审阅材料 |
| E02 `offline-new-tests.log`、`functional-summary.json`、`functional-tests.txt` | 56 项专项、678 项适用回归均通过，原 654 ID 全保留 | 不降低 F02 或业务验收要求 |
| E03 `adapter-gates.json` | 未满足恢复条件时七个入口仍关闭 | 不自动初始化、启动或连接 |
| E04 `recovery-readiness.json`、`acl-observation.json`、`before-hashes.json` | 原目录、批准与既有资产未变；恢复批准/限额/存档许可缺失 | 继续 BLOCKED，等待独立恢复审查 |

证据均位于 `.cache/p6e1b-f03/`；旧 F01/F02 及原执行失败证据保留。报告未包含密码、DSN、API Key 或敏感环境值。

| 最终项 | 结论 |
| --- | --- |
| F03 Windows 受控进程树实现与本机验证 | **PASS** |
| F02 原断言及适用后端回归 | **PASS** |
| 原集群完整性 | **UNDETERMINED** |
| 真实恢复执行准入 | **BLOCKED** |
| PG-01～PG-08 | **NOT EXECUTED** |
| PostgreSQL 命令/连接、收费模型、Embedding、生产库操作 | **均为 0** |

本阶段停止。未自动执行方案 A/B，未延长或补造批准，未启动 PostgreSQL、运行 PG 测试或进入真实收费模型验收。
