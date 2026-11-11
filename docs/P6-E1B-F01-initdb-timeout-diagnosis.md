# P6-E1B-F01 initdb 超时只读诊断

日期：2026-11-10，Asia/Taipei。

**下一步准入：BLOCKED。** 本轮只读诊断已完成。已确认实际异常发生在 **Windows CPython 等待 stdout 读取线程结束**的位置，并确认执行器丢失了超时异常携带输出的保存机会；尚不能唯一确定是 initdb 本体耗时、后代进程持有管道、进程创建耗时或其他外部因素。

指定目录的控制文件现在可读且记录 shut down，但没有 initdb 成功退出/最终同步完成证据，**不允许据此启动集群或进入 PG-01～PG-08**。本轮未重跑 initdb、未启动 PostgreSQL、未建立数据库连接、未创建或删除数据库对象、未修改授权或延长有效期。

依据：[真实执行报告](P6-E1B-real-postgresql-execution-acceptance.md)、[执行前复核](P6-E1B-environment-execution-review.md)、[环境准备](P6-E1B-environment-preparation.md)，以及原缓存执行器/日志、本机 CPython 3.12.10 源码和 PostgreSQL 18 本地文档。历史报告保持原样；本报告记录新增只读观察。

## 1. 实际失败时间线

以下均为实际 UTC 文件时间或日志换算的 UTC+08:00，保留毫秒；文件时间不能代替进程单调时钟。

| 时间（2026-11-10，UTC+08:00） | 证据与事件 |
| --- | --- |
| 12:14:33.482 | execution-journal：authorize PASS，明确操作及元数据批准已记录 |
| 12:14:47.333 | inspect PASS：18.6 程序、固定目录原先不存在、55441 独占绑定后释放 |
| 12:14:53.854 | 本轮随机凭据文件落盘；本次只读取元数据，没有读取或输出内容 |
| 12:14:53.855 | 本轮受限口令文件落盘，长度 44 字节；源码在此后调用 initdb |
| 12:14:53.962 | 指定集群中的早期子目录创建/修改时间；初始化已经实际写文件 |
| 12:15:51.124 | global/pg_control 文件创建时间；不是对数据库服务进行连接的时间 |
| 12:15:53.221 | pg_control 最后修改；控制信息的最后 checkpoint 时间为 12:15:53 |
| 12:15:53.224 | 目录清单中最后的 pg_stat 修改时间 |
| 12:15:54.406 | journal 记录 initialize FAIL / TimeoutExpired；失败诊断落盘，后续阶段未执行 |

口令文件 mtime 到失败日志相隔 **60.551478 秒**。不能简单解释为“45 秒初始化 + 已确认的 15.55 秒清理”：没有记录 Popen 返回、communicate 开始、kill 或输出排空的时间；进程创建/调度、初始化工作及超时后的清理各占多久，无法从现有日志精确拆分。没有保存父子 PID、历史退出码或调用当时的单调时间。

## 2. 历史命令及控制参数

历史参数的脱敏还原如下，**不是本轮运行指令**。除口令文件路径作脱敏外，次序与失败诊断及 initialize() 一致：

```text
initdb.exe
  -D E:\AllProject\data-analysis-agent\.cache\p6e1b-env\c5412c7c4bed43fba4a721909eea1041\cluster
  -U p6_e1b_c5412c7c4bed43fba4a721909eea1041_bootstrap
  --encoding=UTF8
  --locale=C
  --auth-host=scram-sha-256
  --auth-local=scram-sha-256
  --pwfile <本轮已批准的受限本地口令文件>
```

实际程序路径为安装目录 `C:\Program Files\PostgreSQL\18\bin\initdb.exe`。参数以 Python 列表传入，shell 使用默认 false，不经 PowerShell/cmd 再拆分。没有 -W、--pwprompt、--no-sync 或 --no-clean。

本机 `app-initdb.html` 明确区分 --pwfile（读文件首行）与 -W/--pwprompt（交互输入）。因此不能把准备文档中的交互命令误当作实际执行命令。文件不存在、无权限或内容错误仍可能产生错误，但 **实际参数不要求用户输入密码**；此处不展示任何密码、完整 DSN 或敏感环境值。

| subprocess 项 | 实际值及含义 |
| --- | --- |
| input / stdin | command 默认 input=None，未传 stdin；run 不会因 input=None 将 stdin 改成 PIPE。Windows Popen 使用父标准输入句柄，若不存在则按其回退规则提供关闭写端的管道；实际句柄没有记录。不是显式 DEVNULL，也没有自动输入文本 |
| stdout / stderr | capture_output=True，两者都是 PIPE |
| 读取方式 | Windows communicate 为两路分别启动读取线程，线程调用 fh.read() 直到 EOF |
| text / encoding / errors | true / UTF-8 / replace，解码不应因非法 UTF-8 直接抛错；无损原始字节日志则不受此保证 |
| creationflags | CREATE_NO_WINDOW；隐藏窗口不等于关闭 stdin，也不等于进程树被可靠取消 |
| timeout | initdb 调用显式 45 秒，覆盖 command 的默认 30 秒 |
| 日志写入 | subprocess.run 正常返回之后才写 .stdout/.stderr，非零退出也会先写日志；TimeoutExpired 会直接跳过这两次写入 |

## 3. 已确认的超时机制与日志缺口

本机 `C:\Users\a7989\AppData\Local\Python\pythoncore-3.12-64\Lib\subprocess.py` 的行号与原 traceback 对上：

1. run 在 Popen 创建之后调用 communicate(input, timeout=45)，见 548～550 行。
2. communicate 在 1203～1209 行用单调时钟建立截止，再进入 _communicate。45 秒不从 initialize 入口、写口令文件或开始创建进程时计起；Popen 创建时间不在此计时范围内。
3. **原异常位于 1630 行**：stdout_thread.join(remaining_time) 之后，线程仍存活。此时只确认 stdout 读取线程尚未完成，不等于已经用进程句柄证明 initdb 主进程仍在运行，也没有线程栈能进一步区分等待 EOF、读取/解码或关闭句柄。stderr 是否也未结束没有记录；不能把它当成 1634 行的 stderr 超时。
4. run 捕获 TimeoutExpired 后调用 process.kill()。Windows kill 是 terminate 的别名，使用 TerminateProcess；若进程已退出会处理相应错误。它针对直接 Popen 进程，没有在本执行器中实现进程树终止。
5. Windows 分支在 kill 后再次调用 **不带 timeout 的 communicate()**，把返回结果放入 exc.stdout/exc.stderr，再重新抛出原异常。Popen 上下文随后执行关闭/等待。不存在“整个操作一定在 45 秒内完成”的保证。

如果 initdb 或其后代仍持有 stdout 写端，读线程就可能继续等待 EOF；父进程已结束也未必代表管道已关闭。现有执行器没有记录启动 PID/后代句柄或显式 Job Object，因此这是一项有源码支持的 **POSSIBLE** 机制，尚不能证明本次确实由某个特定后代进程造成。

两路 communicate 同时读取，不是典型的“只 wait、不读取两条满管道”的写法。不能据此排除所有 I/O 问题，但没有证据把原因归成普通 stdout/stderr 输出量填满缓冲。

**日志保存缺口为 CONFIRMED：** 外层只保存 sanitize(format_exception(...))。异常格式化不包含 stdout/stderr 字段，所以即便超时异常已带回部分内容，也不会落盘。`initdb-initialize.stdout` 和 `.stderr` 当前均不存在，已有 10 个 stdout/stderr 文件仅属于 inspect 的 --version 调用，不能冒充初始化日志。

当时 Windows run 的第二次 communicate 返回后，异常对象具有输出字段；其实际内容是否非空没有保存，现已无法事后恢复原字节。不能声称“真实输出必然有成功提示”或“没有输出”。本轮仅用合成输出验证保存缺口，没有重新初始化来补取日志。

## 4. Windows 子进程与清理边界

Python 外层实际使用 CreateProcess 和 CREATE_NO_WINDOW，标准流句柄经 Popen 配置。超时后源码路径会调用直接进程的 kill、排空输出和上下文等待；它没有保存当时 PID、kill 的独立结果、原进程退出码或后代终止结果。

initdb 初始化涉及后台 postgres 引导工作；当前目录在失败前仍有控制文件/checkpoint 写入，说明发生过实际后台初始化工作，但无法仅凭这些文件把写入归属到某个 PID。本轮没有创建新进程来重现这条链，也没有读取其他集群目录。

历史及本次名称清单均未见 initdb/postgres/pg_ctl；本次 netstat 没有 55441 条目。它们只说明观察时未见这些进程和监听，**不能重建历史父子进程退出顺序**。CIM 命令行/父 PID 查询在原执行时不可用，缺少进程树证据。本轮没有杀进程或执行 pg_ctl stop。

initdb 默认内部错误清理与外部 TerminateProcess 是两回事。没有 --no-clean 不能证明外部超时后目录已被完整清理；事实上目录仍存在。原执行器在异常后写失败诊断和 journal，然后退出；没有自动调用 start、provision 或 tests。此前 OS 只读观察也没有删除资源。

## 5. 指定部分集群的只读结果

检查严格限制在指定 cluster。先检查祖先的 lstat/重解析标志及最终路径，再递归且不跟随符号链接或重解析项，没有访问业务集群目录。

| 检查 | 实际结果 | 不能据此证明的事项 |
| --- | --- | --- |
| 文件与目录 | 970 个普通文件、26 个子目录；未见重解析项 | 文件数量不证明系统目录语义完整 |
| PG_VERSION | 18 | 不等于 initdb 成功退出 |
| global/pg_control | 8192 字节，允许的 pg_controldata 读取返回 0，stderr 为空 | 不能证明所有关系页、最终同步或 initdb 清理阶段完成 |
| system identifier | **7706391748735152184**，只作诊断记录 | 未写入授权文件，不代表批准启动 |
| 控制状态 | **shut down**；pg_control version=1800，catalog version=202506291 | 是控制文件/checkpoint 状态，不是完整安装验收 |
| checkpoint | 时间 2026-11-10 12:15:53，LSN 0/1942D68 | 不证明 initdb 的最终文件同步已结束 |
| base 子目录 | 1、4、5 均存在 | 未查询 pg_database，未确认实际数据库目录语义或目标业务/任务库 |
| postmaster.pid / postmaster.opts | 均未见 | 不能单靠缺少 PID 文件证明初始化完整 |
| 校验配置 | 控制字段的 data page checksum version=1 | 未做数据页校验；配置值不是校验结果 |
| ACL | 指定目录、PG_VERSION、pg_control 的 ACL 可读；owner 与当前用户一致，继承开启，所列 ACE 未见 Deny | 未基于历史受限子进程 token 计算有效访问权，不能排除过去某文件特定权限问题 |
| 当前进程/端口 | initdb=0、postgres=0、pg_ctl=0；55441 条目=0 | 不取代真实连接和事务释放验收 |

能够读取 control、看到干净关闭及多个 OID 目录，比“只有 PG_VERSION”提供了更多结构性证据；但**初始化成功退出码、最终成功输出、文件同步完成与系统目录检查仍缺失**。因此现阶段归类为“结构已明显形成、完整性未确认”，不简单宣布损坏，也不宣布可启动。

## 6. 候选根因与证据等级

等级说明：CONFIRMED 为本次证据或确切运行库路径确认；POSSIBLE 有具体机制但缺直接历史观测；RULED_OUT 仅排除表中限定解释；UNDETERMINED 表示现有证据不能判断。

| 候选解释 | 等级 | 结论及边界 |
| --- | --- | --- |
| 等待用户输入引导密码 | **RULED_OUT** | 实际 --pwfile 为本地文件路径，没有 -W/--pwprompt；不能因准备文档中的 -W 误判本次 |
| stdout 读取线程未在截止前结束 | **CONFIRMED** | traceback 1630 与本机源代码精确对应；确认的是 stdout 完成/EOF 等待 |
| initdb 主进程本身持续运行超过 45 秒 | **UNDETERMINED** | 无 PID 存活时间或当时 poll/退出码；文件工作跨约 60 秒不等于直接父进程的运行时长 |
| 初始化工作确实耗时，进程树/后代 stdout 写端延迟关闭 | **POSSIBLE** | 目录持续写入、stdout 等待、Windows 二次 communicate 都与之相容；不能确认具体后代或精确耗时分段 |
| 进程创建/调度耗时未包含在 45 秒内 | **POSSIBLE** | CPython 的计时起点在 Popen 之后；未记录创建耗时 |
| 两路管道无人读取导致普通满缓冲死锁 | **RULED_OUT（限定模式）** | 实际为 communicate 两路读取线程，不是 wait 后才读取；不排除 EOF/句柄或底层 I/O 延迟 |
| Windows 文件权限导致停滞/错误 | **UNDETERMINED** | 已有大量成功文件写入，能排除“完全不能写目标目录”；缺历史 stderr 和子进程有效 token 证据 |
| 防病毒或文件系统延迟/锁定 | **UNDETERMINED** | 没有针对本轮的安全产品事件、I/O 跟踪或延迟计数，不因 Windows/耗时较长就归因于杀毒 |
| initdb 内部报错 | **UNDETERMINED** | 原 stderr、退出码和进度没保存，不能确认也不能完全排除 |
| 超时处理缺乏整个进程树/清理硬上限 | **CONFIRMED（设计边界）** | 只针对直接进程 kill；Windows 后续 communicate 无 timeout；不是可靠树级取消 |
| 本次发生异常的父子退出/残留 | **UNDETERMINED** | 当前无残留，但缺历史 PID/句柄链；不能由设计边界反推历史一定泄漏 |
| 超时日志被执行器遗漏 | **CONFIRMED** | 输出文件写在正常返回后，异常只保存 traceback；5 项离线模拟验证相关路径 |
| PostgreSQL 安装损坏 | **UNDETERMINED，无正证据** | 45 秒超时不支持此结论；程序能写大量目录且 control 工具能读，但这也不是完整安装健康证明 |

目前能给出的根因层次是：**超时触发机制已明确，证据丢失原因已明确，具体外部耗时/句柄持有原因未唯一定位。** 不把上述 POSSIBLE 原因改写成已证实根因。

## 7. 恢复选项及授权

| 选项 | 当前判断 | 进一步条件与风险 |
| --- | --- | --- |
| A 保留原目录继续只读诊断 | **可继续作为后续只读工作**；本轮已完成所列检查后停止 | 保留文件清单/时间/摘要及脱敏日志，不改目录、授权、control 或配置；更多工具必须审查其是否真只读且锁定同一路径 |
| B 有完整成功证据后做身份检查 | **当前 BLOCKED** | 需要足以支持 initdb 完成的证据，例如可核验的成功退出/完成日志及同步结论，或另经批准的完整性评估。control 的 shut down 不足；任何需要启动/连接的步骤都须另明确允许，不能把本轮只读授权扩张为启动 |
| C 目录不完整时重新初始化 | **本轮禁止采用** | 需要针对现有目录存档/移动/删除等具体处置的新明确批准，先保全原证据及凭据，核验绝对路径和重解析边界，再审核重建目标/执行参数/新控制身份及授权。不能直接覆盖、清空或以换 run/端口/目录绕过失败 |

当前没有修改负责人批准、有效期或 expected_system_identifier。虽然本轮读到了真实 identifier，也不自动填入原授权并据此进入 start 阶段。最新任务明确只读，优先于先前创建/启动范围；旧批准不能使本轮自动执行 B/C。

## 8. 建议的最小代码改进（未实施）

| 位置 | 建议 |
| --- | --- |
| command 的 TimeoutExpired 分支 | 先保存 exc.stdout/exc.stderr（兼容 bytes/str/None），经凭据/DSN 脱敏后落在受限证据目录；另记是否可用/长度/截断。None 不能冒充空输出或成功 |
| initdb 的 stdin | 保留 --pwfile，明确 stdin=DEVNULL，以免其他非预期交互依赖父输入；密码不进 argv 或日志。不以关闭 stdin 代替检查参数 |
| 日志载体 | 可评估直接重定向到受限文件以避免 reader-thread EOF 等待和丢失输出；但后代可能继承文件句柄，仍需确认写入结束/截断及脱敏，不能只换文件就宣称根因解决 |
| 计时与错误分类 | 记录 UTC 和 monotonic 的 Popen 前后、communicate 开始、超时、kill、排空、退出及阶段结束；区分 client_process_timeout、真实 returncode/内部错误和 cleanup_incomplete，不合并成数据库故障 |
| 进程树 | 使用可观测 Popen PID/创建时间及限定本轮的后代身份；若需要可靠树级终止，再独立验证 Windows Job Object 等方案。禁止按名称杀全部 postgres，禁止把线程 join 当作底层取消 |
| 等待上限 | 把初始化 timeout 及清理上限放入独立审阅的执行配置，绑定执行器摘要/批准记录并设置经确认的有限上限。当前环境计划 pin 没有绑定缓存执行器或 45 秒值；不能只提高数字或无限等待，也不新增自动重试 |
| 阶段门 | 超时永久记录 failed/needs_manual_recovery，保留部分目录；禁止通过改 journal、补 system identifier 或看到 pg_control 就自动进入 render/start/preflight/tests |

本轮只提出建议，没有修改 execute.py、准备器、计划、授权、安全门、Worker 或原验收断言，没有延长 timeout 来重跑。

## 9. 本轮证据与离线验证

新增 [显式运行的离线诊断用例](../tests/p6e1b_initdb_diagnosis_offline.py)。它用 AST 只提取 command 函数，**不导入执行器顶层**；使用 FakePopen/合成输出，封锁真实 CreateProcess、数据库和网络。需要本轮缓存证据，因此不加入常规 test_ 自动发现，不用 skip 把缺证据包装成通过。

```powershell
$env:PYTHONPATH='.;src;tests'
.\.venv\Scripts\python.exe -m unittest p6e1b_initdb_diagnosis_offline -v
```

结果 **5/5 PASS**：核对 --pwfile/非交互参数；Windows run 超时后的直接 kill 与无时限二次 communicate；输出字段仍可用但 wrapper 没保存；stdin/双 PIPE/隐藏窗口参数；正常返回才保存两路日志。它们只证明控制路径和证据缺口，不证明原初始化已成功或某个外部原因。

证据目录 `.cache/p6e1b-f01/` 包含：cluster 文件摘要清单、control 脱敏输出、ACL观察、现有日志目录清单、口令输入文件元数据（无内容）、进程/端口观察、CPython 版本与源码摘要、离线测试及最终完整性核对。原 `.cache/p6e1b-exec/` 五项指定诊断与执行器均保留。控制输出中的非必要认证相关字段已脱敏，不写入报告。

本轮 PostgreSQL 程序调用仅为 **pg_controldata 对指定目录读取 1 次**；initdb/pg_ctl/psql 执行、服务启动、数据库连接、PG-01～08、收费模型、Embedding、生产数据库操作及集群资源删除/移动/重置均为 0。没有访问其他 PostgreSQL 数据目录，也没有修改冻结 RAG 或人工评审资产。

**最终：只读诊断完成；恢复执行准入 BLOCKED。** 继续保留目录与证据，不自动启动或重建。当前最重要的限制是历史输出/退出/PID证据缺失，不能把合理猜测或可读控制文件当成完整初始化成功。
