# P6-E1B-F02 initdb 执行器修复与恢复准备验收

日期：2026-11-10，Asia/Taipei。

| 独立结论 | 当前结果 |
| --- | --- |
| 执行器离线修复 | **PASS**（专项 32/32，合并适用回归 654/654） |
| 原集群完整性 | **UNDETERMINED** |
| 真实恢复执行准入 | **BLOCKED** |
| PG-01～PG-08 | **NOT EXECUTED** |

本阶段只修改执行器、增加离线测试和准备恢复文件，没有执行 initdb、pg_ctl、psql、pg_controldata，没有启动或连接 PostgreSQL，没有改动原授权、有效期、system identifier、部分集群、冻结 RAG 或人工评审资产。未执行恢复方案 A/B。

依据：[F01 只读诊断](P6-E1B-F01-initdb-timeout-diagnosis.md)、[真实执行报告](P6-E1B-real-postgresql-execution-acceptance.md)、[执行前复核](P6-E1B-environment-execution-review.md)。原失败诊断及历史报告保持原样，不把模拟完成当作真实初始化成功。

## 1. 修改前后

| 项目 | 修改前 | 修改后 |
| --- | --- | --- |
| 进程调用 | run(capture_output=True, timeout=45)，隐藏直接进程对象 | 显式 Popen；PID、UTC/monotonic 事件及有限剩余等待 |
| stdin | 未显式指定 | DEVNULL；argv 仍只传受控 --pwfile 路径，不传密码 |
| 超时输出 | Windows run 收集的异常 stdout/stderr 未保存 | 合并可获得的正常/异常/排空输出，脱敏后保存；None 保持未知 |
| 清理 | 直接子进程 kill 后无时限 communicate；没有进程树证据 | 有限终止等待、有限排空与树验证截止；未证实结束则 cleanup_incomplete |
| 状态 | 只记录阶段 PASS/FAIL | 初始化内部七状态、持久失败门、独立完成条件 |
| 完成依据 | 旧流程成功返回后直接继续，没有专门完成性判定 | 真实退出 0、直接进程退出、完整日志、完成提示、结构、资源证明及无错误同时成立 |
| 初始化限额 | 缓存执行器内硬编码 45 秒 | 版本化三类限额，精确摘要绑定新恢复计划；真实值默认全部未设置 |
| 原授权 | 原入口能够创建批准文件并自动填 system identifier | authorize 入口拒绝自动写授权；初始化不再生成/覆盖凭据或填身份 |
| 后续阶段 | 主要依赖旧 journal 失败记录 | render/start/provision/live 还须独立核验完成结果、当前配置/恢复计划/执行器绑定，模拟结果永远不能放行 |

修改文件：

- [scripts/p6e1b_executor.py](../scripts/p6e1b_executor.py)：可版本管理、可安全导入的执行控制器、限额/授权校验、脱敏日志及状态机。
- `.cache/p6e1b-exec/execute.py`：原入口接入控制器，移除旧 run 回退和 45 秒字面量，保留历史失败阻断；改为 main 保护，导入不会执行操作。
- [tests/test_p6e1b_f02_executor.py](../tests/test_p6e1b_f02_executor.py)：32 项正式离线故障用例。
- 本报告及 `.cache/p6e1b-f02/` 证据/未批准恢复草稿。

没有修改业务 Python API、Worker 状态枚举、Planner、分类器、费用控制、RAG 契约或既有测试断言。初始化新状态只属于环境执行器，未增加外层任务库/API 状态。

## 2. 输出与脱敏

优先评估了直接把 stdout/stderr 重定向到文件：它能绕开 Windows communicate 读取线程，但子进程会先把原始输出写到磁盘；即使目录 ACL 受限，也不符合“保存前先脱敏”的要求。因此本轮**没有采用未脱敏磁盘暂存**。

保留 PIPE，改为受控 Popen、显式有限 communicate 和清理。不宣称原来的 EOF/句柄问题已经消失；如果后代仍持有输出资源，有限排空失败并保持 cleanup_incomplete。当前没有独立验证的原生 Windows 树控制器，真实分支在创建进程前即关闭。

每次尝试在受控私有目录 `.cache/p6e1b-exec/private/f02` 下使用独占文件名；测试只在临时目录内产生模拟文件。执行器先脱敏、再做 UTF-8 字节限长、再写磁盘，不覆盖原始失败日志。没有打印 argv 中的密码、完整 DSN、API Key 或敏感环境值。

| 元数据 | 口径 |
| --- | --- |
| available | 是否获得该输出字段；false 不代表进程未输出 |
| raw_byte_length | bytes 的原始长度，或 str 的 UTF-8 长度；None 时为 null，不能当作 0 |
| saved_byte_length | 实际保存的安全文本字节数；未获得或写失败时为 null；已知空字符串为 0 |
| truncated | 脱敏后的文本是否因配置上限截断；脱敏替换本身不称为截断 |
| persisted / path | 输出是否成功保存及本次安全文件名；未知输出不创建伪空日志 |

脱敏覆盖已知密码/敏感环境值及 URI 编码形式、完整 PostgreSQL URI、conninfo 行、API Key/密码字段、Authorization/Bearer、常见 key 前缀，以及输出末尾被截断的已知秘密前缀。未知库存、异常类型或写入失败均不回退到原文保存。真实调用前仍须核实秘密库存完整，不能把规则脱敏宣称为任意未知字符串的完美识别。

已有较长输出不会被后续 None 覆盖。日志清单记录未知/字节长度/保存/截断状态；有截断、缺输出或日志未完成时，初始化不能 completed。安全日志写失败独立记 log_collection_failed，不冒充数据库内部错误。

## 3. 生命周期、时间与资源边界

Popen 之前建立执行截止；返回后若创建已迟到，也不会接受成功。创建、等待、超时、终止请求、退出观察、输出排空、树验证、状态变更和错误均记录 UTC 与 monotonic。

执行、终止及清理具有不同有限配置：

1. execution_seconds：受控进程执行/通信等待；从创建前开始核算，所有等待只拿剩余时间。
2. termination_seconds：终止请求后的直接子进程等待，且不得越过清理总截止。
3. cleanup_seconds：输出排空、资源/进程树验证共用截止；迟到的验证或日志完成也不能被接受为 completed。

超时后显式 terminate 并有限 wait，记录返回码及 direct_exited；终止异常、等待超时、输出未结束、树状态未知均单独留证。不使用 with Popen 的隐式无界 __exit__，不再调用无 timeout 的 communicate，不自动重试。读取线程未结束时不以可能阻塞的 close 假装资源已释放，而是保留待处理引用并失败关闭。

**进程树边界：** 本轮没有实现或使用 Windows Job Object，也没有创建真实父子进程证明原生取消。模拟树观察只允许在 simulation=True 的 FakePopen 测试路径使用；它不能让真实入口通过。真实路径缺少独立验证的树控制适配器时，在 Popen 之前拒绝。后续若实现 Job Object，需单独验证在进程运行前完成受控归属、嵌套作业兼容、后代句柄/退出以及超时处置；不能只设置一个 JSON 布尔值就当作证据。

配置截止和迟到拒绝已离线验证，但原生 CreateProcess/TerminateProcess、文件系统调用和句柄关闭仍可能受 OS 阻塞影响。这里没有线程超时冒充底层取消，没有已验证的 Windows 硬实时或整个进程树可靠中断承诺，也没有按名称批量杀 postgres 的代码。

## 4. 失败状态机与下游验收

```text
not_started → running → completed
                    ↘ failed → needs_manual_recovery
                    ↘ timed_out → needs_manual_recovery
                    ↘ timed_out → cleanup_incomplete → needs_manual_recovery
                    ↘ cleanup_incomplete → needs_manual_recovery
已有部分目录/授权或配置不符 → failed → needs_manual_recovery（不创建进程）
```

保留 failure_state、完整 history、错误类别及时间，不用最终 needs_manual_recovery 覆盖“曾经超时/清理不完整”的原因。running 状态必须在创建进程前成功持久化；否则禁止启动。新执行器实例也读取原状态账本，不能通过重建 Python 对象绕过失败或 running 状态，不自动重置账本。

completed 必须同时满足：

- 已观察到直接进程结束且返回 0，在执行截止内结束。
- 输出收集及安全日志清单完整，没有未知或截断输出。
- 受控英文完成提示和版本化目录结构校验同时成立；PG_VERSION/control 或 shut down 单独都不够。
- 输出资源及进程树结束已验证，未迟于清理截止。
- 没有等待、日志、终止、清理、结构或状态持久化错误。

require_completed 会重新核对这些字段、执行器身份及 simulation；离线 completed 永远不能授权真实数据库后续步骤。缓存入口还重新验证当前恢复授权、policy/recovery 摘要和适配器摘要。历史 journal 保留原 FAIL，authorize 不再自动填批准信息；原 journal、原 failure-initialize 和原授权未修改。

| 错误类别 | 初始化结果 |
| --- | --- |
| client_process_timeout | timed_out；有资源问题则再进入 cleanup_incomplete；最终需人工恢复 |
| process_nonzero_exit | failed，保留实际退出码，不能当作客户端超时 |
| log_collection_failed | failed 或保留更严重清理原因；不能完成 |
| resource_cleanup_failed / timeout / incomplete | cleanup_incomplete；直接进程退出不能替代树证明 |
| completion_contract_failed / check_failed | failed；不能凭文件存在继续 |
| recovery_precondition_failed | 不创建进程，需人工确认；不产生旧授权回退 |

## 5. 新限额与恢复计划绑定

原 45 秒来自 `.cache/p6e1b-exec/execute.py`，不是 PostgreSQL 参数，也不在原环境计划字段中。现已移除该入口硬编码值，没有改成 120 秒或其他真实默认限额。

草稿：[限额配置](../.cache/p6e1b-f02/initdb-policy.pending.json)、[恢复计划](../.cache/p6e1b-f02/recovery-plan.pending.json)。

- 配置版本 p6-e1b-initdb-policy-v1，三项秒数及日志上限都为 null；无法通过实际执行校验。
- 恢复版本 p6-e1b-initdb-recovery-v1，绑定固定 run/目录/原环境计划摘要、配置版本/规范 JSON SHA-256、版本管理控制器 SHA-256、缓存入口 SHA-256。
- route、批准人、期限、存档证据和树控制证据为空，authorized/archive_authorized/archive_completed/reinitialize_authorized/tree_strategy_reviewed 均 false。
- 正式执行须明确批准精确限额、代码摘要、恢复路径、期限、存档和重建范围；不能从旧创建批准推导出来，也不修改原批准的日期或 system identifier。

测试中的 3/1/2 秒只是假时钟故障参数，不是建议的真实初始化限额。profile 摘要、执行器/入口摘要、run/目标或批准信息不符立即拒绝；不读取 DATABASE_URL 或已有 reviews 作为后备。

## 6. 原目录保全与恢复方案

原目标继续固定为 `.cache/p6e1b-env/c5412c7c4bed43fba4a721909eea1041/cluster`。本阶段没有创建新 run、换端口或切换目标。970 个原文件的内容、长度和 mtime 与 F01 清单一致；原控制文件状态只是先前只读证据，不升级完整性结论。

| 方案 | 准备条件 | 授权与风险 |
| --- | --- | --- |
| A 后续获得充分成功证据后独立验证 | 保留真实退出/完成日志、同步和结构连续性证据；申请固定目录的独立完整性验证 | 当前证据不足。只读工具权限与任何必要启动/连接须分别明确；不能回填旧 initdb 返回 0 或把原 timed_out 改成 completed |
| B 无法确认完整性时先存档再重建 | 保全原目录、ACL、文件/配置摘要、失败日志和必要凭据；负责人指定存档位置，核验绝对路径、不重叠和重解析边界，存档后核对摘要 | 新明确批准必须覆盖旧目录隔离移动、保密与权限保留、同一固定目标下重建及具体限额。不能默认删除/清空；新进程树控制还需独立验收；旧创建授权不覆盖这些行为 |

本轮未执行 A/B，也不提供自动移动、删除或重建函数。生成的 existing-state.observation.json 仅描述“历史 timed_out，需人工恢复”，不是能解锁执行的完成凭证。当前不确定目录不得启动；读到的 system identifier 不写回旧授权。

## 7. 离线故障验证与兼容性

专项使用 FakePopen、假时钟和临时目录，封锁真实 Popen/run、socket connect/bind 和 psycopg.connect。没有真实 initdb/pg_ctl/psql、数据库对象或网络调用。

| 必需反例 | 验证内容 |
| --- | --- |
| 退出 0 / 非零 | 完整成功条件；非零单独归类；仅目录或提示不能完成 |
| TimeoutExpired 有/无输出 | bytes/str/None；None 与已知空字符串区分；后续 None 不覆盖已有输出 |
| 部分敏感输出 | 密码、编码形式、DSN、API Key、Authorization、末尾秘密前缀先脱敏后保存 |
| 终止失败 | 保留可获得输出和直接进程未知状态，cleanup_incomplete |
| 后代持有输出 | 有限排空，无无界 communicate；不把父进程退出当作资源完成 |
| 日志/状态写入失败 | 分类别记录；running 状态落盘失败时不得创建进程 |
| 清理超时/迟到 | 树验证或日志完成迟到均拒绝 completed |
| 已有部分目录 | 不创建进程、不覆盖或清理目录 |
| 自动恢复 | 同一对象和新对象均不能绕过失败账本 |
| 后续数据库步骤 | 失败、伪 complete 和模拟成功不能解锁；入口 7 项离线烟测证明无原生回退 |
| 业务库/越界/批准 | 固定目标、run/计划/代码/限额摘要、批准和证据校验；旧授权不替代新恢复许可，输入字典未改 |

执行命令：

```powershell
$env:PYTHONPATH='.;src;tests'
.\.venv\Scripts\python.exe -m unittest test_p6e1b_f02_executor -v
.\.venv\Scripts\python.exe .cache/p6e1b-f02/run_checks.py functional
```

| 本轮结果 | 状态 |
| --- | --- |
| 最终专项 | 32/32 PASS |
| 最终合并适用回归 | **654/654 PASS**，236.408 秒，0 failure/error/skip；原 603 项 ID 全部保留，合并既有环境/复核 19 项及本轮 32 项 |
| 缓存入口阶段门烟测 | 7/7 拒绝越权/未完成操作，原生/网络/数据库调用 0 |
| F01 历史断言 | 5/5 在原始执行器快照上复现原诊断；原测试及断言未修改，不计作新执行器正确性证明 |
| 原集群及旧证据 | 970 文件未变；2262 项既有代码/冻结资产保护核对未变；旧批准、期限、journal/failure 保留 |

F01 离线测试描述的是旧 bug，仍保留原断言；本轮显式给它提供 F01 保存的原始快照，未使用 skip/expectedFailure 或修改断言来制造通过。新增正式测试直接测试版本管理的新控制器，不依赖缓存执行器存在，适用于常规离线发现。历史 13 个私有导入问题未清理，不纳入适用功能回归。

证据位于 `.cache/p6e1b-f02/`：before-hashes/原执行器、new-tests、functional-summary/tests、历史 F01 结果、入口烟测、安全输出计数例子、未批准草稿及完整性核对。

## 8. 完成边界

执行器修复只完成离线控制语义及失败关闭验证；**没有真实原生 Windows 进程树取消、文件系统硬截止或 PostgreSQL 成功初始化的通过结论**。实际恢复仍缺新明确授权、已审阅有限配置、已验证树控制和原目录处置方案。

收费 LLM、Embedding、生产数据库、集群重建/移动/删除、服务启动和 PG-01～08 均为 0。本阶段结束后停止，不自动采用恢复方案，不部署或进入真实模型验收。
