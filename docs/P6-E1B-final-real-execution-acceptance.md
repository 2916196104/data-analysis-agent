# P6-E1B · 真实 PostgreSQL 恢复与最终集成验收

日期：2026-11-10（Asia/Taipei）。本次停点：**只读执行前预检；未执行恢复。最终结论 BLOCKED。**

存在两个独立阻断：① 端口表及 PostgreSQL 进程查询实际返回 CimException，无法确认端口/进程状态；② 当前恢复计划未获本轮明确批准，批准人、期限及十二项许可缺失。没有把 PENDING_BINDINGS_VALID 当作授权，也没有调用 recover。

依照“有其他阻断不得进入正式审批”和“没有授权立即停止”的边界，本轮止于只读预检及缺项登记，不请求批准一个尚未通过 OS 核验的执行环境。未创建 F05/F06，未修改实现、安全门、旧批准或失败证据。

## 1. 固定目标及实际检查

| 项目 | 当前值 |
| --- | --- |
| run_id | `c5412c7c4bed43fba4a721909eea1041` |
| attempt_id | `a4b479a8d9154994a402f7b13f1a00a6` |
| route / 端口 | B / 55441 |
| 原环境计划规范 SHA-256 | `40a30688281cf0cdd2a73af02bd5c345e2671d9fcc672a84c434947b80a09e2b` |
| 最新恢复计划规范 SHA-256 | `bbc65bf7baa555cf9abad25c86890bbe4dd8d5d9747e244d9f761e1a068147e5` |
| 最新 policy 规范 SHA-256 | `5f596e4722bc97cd3d6b7892228f7f28c61c4019fced04eca1bd9773f0dece3b` |

实际执行的已有只读检查：

```powershell
.\.venv\Scripts\python.exe scripts/p6e1b_recovery.py check
```

退出码 0，输出 `status=PENDING_BINDINGS_VALID`、`real_recovery=BLOCKED`、固定 attempt_id。它只表示未批准材料与当前摘要相符，不表示已获准或数据库检查通过。

| 要求 | 实际证据与结论 |
| --- | --- |
| 1. 最终代码/计划/policy | PASS：当前八项代码、六项二进制、原环境/schema 源码等绑定检查通过；未修改代码或材料 |
| 2. F03 原生进程证据 | 绑定 PASS：原 10 项真实 OS 测试凭据与当前实现、测试程序和平台仍匹配；本轮没有重跑原生测试，不外推为 PG 已通过 |
| 3. 原部分集群 | PASS（保全）：970 个文件和含源根目录在内的 997 条清单与 F04 一致；新采集 997 个 Owner/Group/DACL 描述符与原快照一致；初始化完整性仍 UNDETERMINED |
| 4. 存档与新 attempt | PASS：精确存档目标和新私有 attempt 目录均不存在；没有创建或移动 |
| 5. 原失败记录 | PASS：原 journal、failure-initialize、partial-cluster-state、execution-summary 的摘要均与计划匹配；旧环境授权摘要也未变 |
| 6. 与业务库隔离 | 配置隔离 PASS：只允许固定两库/四角色和 loopback:55441，未使用 DATABASE_URL；实时服务、数据库、角色和 ACL 身份尚未验证 |
| 7. 候选端口 | **UNDETERMINED**：Get-NetTCPConnection 查询失败；没有做 bind 或连接，不认定空闲，也没有证据认定被占用 |
| 8. PostgreSQL 进程 | **UNDETERMINED**：Get-CimInstance Win32_Process 查询失败；不能认定无进程，亦不能认定存在相关进程；不满足当前保守进程预检 |
| 9. 真实负责人批准 | **BLOCKED**：authorized=false；无本轮批准人及明确批准；新 approved 文件不存在 |
| 10. 许可/限额/有效期 | 限额数值已配置且合法，但未批准；十二项 permissions 全 false，expires_at 为空；不能执行 |

运行账户 SID 的摘要与计划相符，仅说明当前账户身份匹配，不代表 CIM 访问权限或数据库权限已经验证。

## 2. OS 查询失败证据

查询时间：2026-11-10T12:15:17.2398744Z，即本地约 20:15:17（UTC+08:00）。只读取本机 TCP 表和进程元数据，不读取业务数据库，不采集命令行/凭据。

| 查询 | 实际返回 |
| --- | --- |
| Get-NetTCPConnection -ErrorAction Stop，筛选 LocalPort=55441 | status=UNDETERMINED；error_type=MethodInvocationException；error_id=CimException |
| Get-CimInstance Win32_Process -ErrorAction Stop，筛选 initdb/postgres/pg_ctl | status=UNDETERMINED；error_type=CimException；error_id=Microsoft.Management.Infrastructure.CimException,Microsoft.Management.Infrastructure.CimCmdlets.GetCimInstanceCommand |

没有把失败查询的空结果当作 count=0。失败原因尚未确定，不能据此认定端口占用、PostgreSQL 安装损坏或服务异常。本轮未提升权限重试、改变 WMI/CIM 服务、安全门或进程状态，也未使用其他实例代替。

现有真实入口的 NativePlatform.preflight 同样采用严格的端口/CIM 查询，所以该问题是执行前的独立阻断，不能仅补授权后直接调用恢复。

## 3. 原目录与存档事实

原固定源：

```text
E:\AllProject\data-analysis-agent\.cache\p6e1b-env\c5412c7c4bed43fba4a721909eea1041\cluster
```

计划存档位置（**未创建、未移动；不是本轮授权**）：

```text
E:\AllProject\data-analysis-agent\.cache\p6e1b-exec\private\archives\c5412c7c4bed43fba4a721909eea1041\a4b479a8d9154994a402f7b13f1a00a6\cluster
```

原文件清单规范摘要：`3958cca5dd6da9b120695380a02526bd9c5e76ca8d3329b358f419295493c72c`。

全量 ACL 清单规范摘要：`2e7c868c4bb71dda151e256aea4e042723d17b970aac2006110476aadcda99cb`，本轮重新采集结果相同。覆盖 Owner/Group/DACL 与继承控制，不宣称采集了 SACL 或做过完整磁盘备份。

archive receipt 未生成，原源目录仍存在，新初始化目标没有被清空或覆盖。未来事实保持 archive_completed=false、new_system_identifier=null；没有填写任何 PG 结果。原 timed_out/FAIL 证据保留。

## 4. 授权缺项登记

**本节是缺项登记，不是正式执行审批请求。** 由于 OS 核验仍阻断，尚未进入用户要求的第二部分正式审批。

当前缺少：负责人针对上述最终计划及策略的后续独立明确批准、批准人标识、含时区的有效期，以及固定材料目录中的 `recovery-plan.approved.json`、`recovery-policy.approved.json`。

十二项未批准许可为：原部分目录隔离移动、存档 ACL 收紧、原目标重新初始化、明确时限及日志上限、启动本轮独立实例、固定四角色及两库创建、测试 schema 初始化、测试连接、本轮合成写入及 PG-01～08、新实际 identifier 的独立事实记录、私有测试凭据受控使用、仅本次持有实例/资源的清理。全部仍为 false，没有代填。

未批准策略数值仍为：execution=180 秒、termination=10 秒、cleanup=30 秒、每路日志 1048576 字节；identity=10、client=60、start=60、stop=30、suite=600 秒。数值存在不代表批准，阶段截止也不是任意底层调用的硬实时取消保证。

两个配置目标仍为 `p6_e1b_c5412c7c4bed43fba4a721909eea1041_business` 和 `p6_e1b_c5412c7c4bed43fba4a721909eea1041_tasks`；角色仍用该 run 的 creator/reader/writer/task 和 bootstrap。未连接它们，不能确认真实数据库/角色已存在或权限正确。现有 reviews 业务库未访问。

过去初次创建时的真实批准记录仍保留，未修改其期限；它不等于本次 route B 隔离存档、重建及新时限的恢复批准。本轮没有从旧批准推断新许可。

## 5. 恢复和服务阶段结果

| 阶段 | 本轮实际结果 |
| --- | --- |
| 恢复授权核验 | BLOCKED，缺失上述明确批准和文件 |
| 旧集群隔离存档、ACL 收紧与 archive receipt | NOT EXECUTED |
| 新 attempt 执行状态创建 | NOT EXECUTED，私有 attempt 目录仍不存在 |
| initdb / WindowsJobProvider 实际执行 | NOT EXECUTED；无新 PID、输出或生命周期证据 |
| 真实 system identifier 观察 | NOT EXECUTED；不以旧 pg_control 观察替代 |
| 新运行授权、SQL 生成与执行、实例启动 | NOT EXECUTED |
| 两库/四角色/schema/ACL/GUC/标记初始化 | NOT EXECUTED |
| 五 DSN 注入、实时服务身份和权限预检 | NOT EXECUTED |
| 本次实例/Job/连接清理 | NOT APPLICABLE：本轮没有启动或连接资源；不是资源释放验收 PASS |

## 6. PG-01～PG-08

| 用例 | 本轮结果 |
| --- | --- |
| PG-01 RR/READ ONLY、事务协议与释放 | NOT EXECUTED |
| PG-02 统计后真实并发新增及同快照预读 | NOT EXECUTED |
| PG-03 维度间并发修订及一致性 | NOT EXECUTED |
| PG-04 计数变化后 stale_ranking 阻断 | NOT EXECUTED |
| PG-05 同计数正文/修订变化的能力边界 | NOT EXECUTED；不升级已有新鲜度能力 |
| PG-06 事务释放后的冻结输入 | NOT EXECUTED |
| PG-07 超时、异常、Worker 停止与资源释放 | NOT EXECUTED；原严格断言未改 |
| PG-08 后台 Worker、真实任务库、API 与发布 | NOT EXECUTED |

本轮没有 Worker 任务或数据库事务，不能提供新的 Worker 中止/连接释放通过证据。全局模型调用为 0 是因为没有开展分析，**不能替代“Worker 中途停止后分类调用必须为 0”的真实验证**。以后若 PG-07 失败，仍须保留失败、停止 PG-08，不擅自修复业务逻辑重跑。

## 7. 回归、证据和风险

本轮未运行回归：用户规定真实 PG-01～08 全通过后才运行适用回归，该前提尚未达到。F04 既有离线基线为专项 53/53、回归 757/757，当前实现摘要仍匹配；这是历史离线证据，不是本轮真实集成通过。未使用 SQLite/Mock 代替 PG，未清理历史静态导入问题或修改冻结 RAG/人工评审资产。

本轮脱敏证据目录：

```text
.cache/p6e1b-final/readonly-preflight-20261110T121517Z/
```

包括已有 check 的输出/退出码、`os-readonly-observation.json`、当前账户身份摘要、`artifact-preflight.json`、全量 ACL 只读采集、`preflight-final.json` 和 `missing-authorization.md`。只有诊断/报告新文件写入，执行代码、批准、原失败记录和原目录未改。无密码、DSN 或敏感环境值写入本报告。

尚未解除的风险：CIM 查询失败的原因和实际端口/进程状态；真实 Windows/PostgreSQL 后代 Job 行为、passfile/操作 SQL、权限、快照并发、取消与释放；原集群完整性未知；本轮恢复未授权。不得为通过而结束业务进程、换端口/目录/实例、改安全断言或把未执行阶段标为成功。

## 8. 停止结论

**P6-E1B：BLOCKED。** 本轮未进入正式执行审批，也未执行恢复。应先使本机端口/进程只读核验获得可靠结果，再重新核对最终范围，并等待后续独立消息中的明确批准。期间不创建 approved 文件，不写未来运行事实。

本轮原目录移动、initdb、PostgreSQL 启动/连接、真实 PG 用例、收费模型、Embedding 和生产数据库操作均为 0。已停止，不自动进入任何恢复、部署或新增准备阶段。


## 9. 本轮追加：Windows OS 预检故障诊断与替代接口验证

日期：2026-11-10，观察窗口约 12:34～13:04 UTC（本地 20:34～21:04）。**OS 预检仍为 BLOCKED；没有修改或接通恢复执行安全门。** 已定位原 CIM 故障与工具的受限执行上下文有关，并非已有证据证明 Winmgmt 损坏。普通账户的非受限上下文能够查询 CIM，但既有 PostgreSQL 进程身份仍不可读取；独立原生接口的监听状态验证也有两项真实失败。因此，本轮只新增独立诊断与测试，保留阻断，不启用未经证明的后备通路。

本节为追加记录，第 1～8 节原文及其历史证据保留。未新建 F05/F06，未执行恢复、目录移动、数据库命令、数据库连接、服务重启、安全策略调整或已有进程终止。本轮只运行诊断工具与无害 Python 测试；原生专项的监听夹具子进程由专属 Windows Job 管理并清理，另一次本进程临时 socket 诊断使用上下文管理器关闭监听。

### 9.1 原始故障及账户/系统环境

复现使用 `Get-NetTCPConnection -ErrorAction Stop` 筛选 LocalPort=55441，及 `Get-CimInstance Win32_Process -ErrorAction Stop` 筛选 initdb/postgres/pg_ctl。异常必须保留为 ERROR/UNKNOWN，不能转为空列表成功。

| 项目 | 受限工具上下文的实际结果 |
| --- | --- |
| PowerShell / Windows | PowerShell 7.6.9 Core；Windows 10.0.26300、64 位；进程 64 位 |
| 运行账户 | 当前普通账户，有效管理员令牌=false；原恢复计划的 operator_sid 绑定仍通过；未启用调试特权或改变账户权限 |
| Get-NetTCPConnection | MethodInvocationException，FQID=CimException；`Exception calling "ProcessRecord" with "1" argument(s): "无法从客户端中访问 CIM 资源。"`；外层 HRESULT=-2146233087，内层=-2146233088 |
| Get-CimInstance Win32_Process | CimException，`无法从客户端中访问 CIM 资源。`；NativeErrorCode=2、HRESULT=-2146233088 |
| Get-CimInstance Win32_OperatingSystem | 同样 CimException、NativeErrorCode=2，说明问题并不限于 PostgreSQL 筛选表达式 |
| Get-Service Winmgmt | NoServiceFoundForGivenName；**不能据此认定服务不存在** |
| sc.exe query Winmgmt | 退出码 5，OpenService：Access is denied |
| 原生 SCM 接口 | OpenSCManager 成功，OpenService(Winmgmt) 返回 Win32 5 |
| 原生 TCP 表 | IPv4、IPv6 GetExtendedTcpTable 均返回 Win32 5；不是“没有监听” |
| 自身进程身份接口 | OpenProcess 与 QueryFullProcessImageName 成功，说明并非全部 OS 接口失效 |
| Get-Process / netstat | 命令可执行，但受限 Get-Process 观察为 0 个相关进程；后续普通上下文实际看见 11 个，证明该空列表不能作为全机无进程证据。netstat 退出 0、无 55441 行同样不能单独解锁 |

这里的 CimException NativeErrorCode=2 属于 **MI_Result**，其含义为访问被拒绝，不能误按 Win32 错误 2 解读成找不到文件；也不同于 MI 的错误查询/参数代码。[Microsoft MI_Result](https://learn.microsoft.com/en-us/windows/win32/api/mi/ne-mi-mi_result)

随后通过工具允许的只读非受限执行，对比同一普通账户（管理员令牌仍为 false）：CIM 查询成功、Winmgmt=Running、目标端口无匹配行、PG 相关进程=11。Windows PowerShell 5.1.26100.9818 Desktop 的同一查询也表现为受限上下文拒绝、普通上下文成功。没有使用管理员 UAC、修改服务/安全策略或连接数据库。

| 候选原因 | 证据等级及界限 |
| --- | --- |
| 查询访问权限/执行上下文限制 | **CONFIRMED**：MI 访问拒绝、SCM/TCP 原生错误 5，以及同账户不同执行上下文的对照相符；未进一步证明具体是哪条令牌限制或 ACL |
| CIM 提供程序本身损坏 | **无充分证据**：同机普通上下文可正常查询，不能因原异常认定损坏；不需要重启 Winmgmt 来试错 |
| 查询命令/筛选语法错误 | **RULED_OUT（本次复现）**：同一查询在普通上下文成功，Core/Desktop 都复现上下文差异 |
| 55441 实际已被 PostgreSQL 占用 | **未发现证据**：成功观察未发现该端口条目；不是永久可用保证，正式恢复前仍须重新核验 |
| 目标进程无法识别 | **CONFIRMED**：11 个候选进程均无法取得所需可执行文件/有效数据目录身份 |
| 原生监听状态为何为 0 | **UNDETERMINED**：真实夹具能绑定并返回匹配地址/端口/PID，但原生表的 state=0，不足以证明 LISTEN；保留失败，不推断安装损坏或修改断言 |

### 9.2 独立 OS 方法、端口与进程的实际观察

独立诊断通过 IP Helper 的 `GetExtendedTcpTable` 读取 AF_INET/AF_INET6 的 OWNER_PID_ALL 表，通过 Toolhelp 枚举候选进程，再用 OpenProcess、QueryFullProcessImageName、GetProcessTimes 与非阻塞存活检查绑定 PID/创建时间/映像。读取两次身份及前后快照；进程消失、PID 复用、任何权限/接口错误均保持 UNKNOWN/BLOCKED。句柄必须关闭；没有数据库和网络连接。

该 TCP API 按地址族返回表和错误码；错误码非零不能当作零条目。创建时间可用于防止仅以 PID 错配进程，但两次观察仍不能消除所有竞态。[GetExtendedTcpTable](https://learn.microsoft.com/en-us/windows/win32/api/iphlpapi/nf-iphlpapi-getextendedtcptable)、[GetProcessTimes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes)

| 方法 | 普通上下文实测 | 可以得出的结论 |
| --- | --- | --- |
| CIM TCP / netstat -ano | 退出正常，55441 无匹配行 | 观察时未发现端口条目；不证明未来可用 |
| 原生 TCP IPv4/IPv6 | 两族读取返回 0；55441 前后两次均无条目 | `NO_ENTRY_AT_OBSERVATION`；检查所有本地地址，不只 127.0.0.1；与监听夹具测试的失败分开记录 |
| Get-Process / Toolhelp / CIM 进程表 | 均看见 11 个 PG 相关候选 | 存在候选，不等于它们属于本轮，也不能直接宣称它们属于业务实例 |
| CIM 身份字段 | ExecutablePath、CommandLine 均为 null | 身份/数据目录未知；没有输出任何业务命令行或凭据 |
| 原生 OpenProcess | 对 11 个 PID 均返回 5（Access denied）；独立仅请求 QUERY_LIMITED_INFORMATION 的探针也被拒绝 | 不是仅因请求 SYNCHRONIZE 权限过宽导致；不能获取身份就继续拒绝 |
| 自身及本轮无害子进程 | 映像、创建时间、存活检查成功 | 证明 API 对拥有权限的进程可用，不外推为对全部进程都有访问权 |

另一次临时端口诊断中，Python 已执行 bind/listen，原生表返回匹配 PID/端口，而 netstat 的匹配输出仍为空。该反例进一步说明不能仅凭 netstat 的空输出放行；此差异原因也未确定。

观察的候选 PID：pg_ctl=23136；postgres=33444、25992、32376、38288、8808、10804、19904、31336、9248、1488。该列表只是当时快照，后续必须重新观察，不能永久允许或按 PID 停止它们。普通上下文的只读诊断最终返回 **BLOCKED / process_identity_unknown**；受限上下文返回 **BLOCKED / tcp_2 / code=5**。

### 9.3 现有安全门缺陷与本轮最小处理

`NativePlatform.preflight()` 当前要求全机 `initdb.exe/postgres.exe/pg_ctl.exe` 数量为 0，确实会将已确认无关的业务实例一并视为冲突。CIM 异常仍会阻断，没有发现异常转 PASS 的分支。

理想的最小调整应为：目标端口有条目则拒绝；候选进程须绑定可重复核验的 PID、创建时间、映像及**有效数据目录/文件身份**；仅在能证明与固定目标、存档及 attempt 均不重叠时视为 OTHER；TARGET 或 UNKNOWN 均拒绝。单有进程名、可编辑 JSON 中的 OTHER、自报 -D 参数或一次 PID 观察不足以成为证明，也不能要求先停止业务服务。

**本轮未接入该调整。** 实际 11 个候选身份仍被拒绝，原生监听状态验证还存在失败，没有达到“足够可靠替代证据”的修改前提。`scripts/p6e1b_recovery.py`、`scripts/p6e1b_windows_job.py`、`scripts/p6e1b_executor.py` 及历史入口均未修改。新增 `scripts/p6e1b_os_readonly.py` 只是独立诊断，不被恢复入口导入，不接受审批文件，不授予恢复权限；即使诊断的纯测试输出 PASS，也固定 `recovery_authority=false`。

需要人工处理的是：由具备现有进程只读身份访问权限的负责人，在允许的上下文中取得这些候选与有效数据目录的可核验关联证据；同时定位原生监听状态异常。在证据形成前保持阻断。本轮不更改业务服务 DACL、系统策略、服务账户或调试特权，不请求停止现有 PostgreSQL。

### 9.4 测试结果：离线通过不替代原生失败

| 测试层 | 实际结果 |
| --- | --- |
| 新增纯离线故障测试 | **21/21 PASS**，无 skip/expectedFailure；封锁真实 Popen/run、socket connect/bind、psycopg.connect、OpenAI |
| 适用后端离线回归（包含上述 21 项） | **778/778 PASS**，261.026 秒，0 failure/error/skip；原有 757 项全部保留 |
| 其中 F02 / F03 / F04 准备 / F04 入口 | 32/32、24/24、26/26、53/53 PASS；未放宽旧断言；PG-07 失败阻断 PG-08 的原测试仍通过 |
| 本轮显式真实 Windows OS 诊断 | **7 项：5 PASS、2 FAIL、0 error、0 skip**；只使用本轮 Python 夹具，不运行 PostgreSQL |
| F03 原生 Job 旧凭据 | 当前代码/平台绑定仍通过；本轮没有重跑旧 10 项，不能混入本轮 7 项计数 |

21 项故障测试覆盖空闲/IPv4/IPv6/不同地址、占用 PID、身份成功/权限不足、CIM 无法作为成功后备、原生接口失败/未知异常、PID 消失/复用、未知目录、策略层已证明 OTHER/TARGET、过期证明及畸形表。OTHER/TARGET 是纯合成策略反例，**不是已完成真实进程目录认证**。

原生 7 项中，双栈表可读、55441 测试前无条目、自身身份、拥有的子进程身份及既有候选的只读诊断共 5 项通过。IPv4 127.0.0.1:55441 和 IPv6 ::1:临时端口两项保留原断言失败：表中地址、端口、PID 匹配，但 state=0，`listening=null`，未满足 LISTEN。原生 TCP6 状态定义含 LISTEN=2；0 在本契约中不受支持，不能改写成成功。[MIB_TCP6ROW_OWNER_PID](https://learn.microsoft.com/windows/win32/api/tcpmib/ns-tcpmib-mib_tcp6row_owner_pid)

第一次原生失败证据另存 `native-initial-*`，没有删除或包装成通过。最终 7 项测试的三个监听 Job（含失败用例）均留下 `all_exited=true`、`handles_released=true`、`output_drained=true` 的清理凭据；初次测试的两个 Job 也有相同清理证据。只清理本次持有资源，不按名称终止任何已有进程。未实际验证“既有业务实例目录已认证”，也未验证真实目标冲突；这两类只有保守策略故障注入证据。

离线回归入口封锁真实 PostgreSQL 可执行程序、socket.connect 和 psycopg.connect。原有 13 项历史静态导入技术债没有被删除、修复或作为本轮通过项；适用范围沿用既有 757 项清单。真实 PG、收费模型、Embedding、生产数据库操作均为 0。

### 9.5 修改文件、证据与可复现入口

本轮新增文件均用于诊断/测试；Agent、业务配置、恢复入口和旧测试未改。

| 新文件 | SHA-256 |
| --- | --- |
| scripts/p6e1b_os_readonly.py | `b5d669bc1ce81f3e04732ab13a49da05d804a095b22faf3e86e2612d1f09b1c7` |
| tests/test_p6e1b_os_readonly.py | `5a7350c400a4db50ec01f905e64711353123fb55aa9018743623c2bf0d2a59a9` |
| tests/p6e1b_os_native.py | `4f05add19f828f42fa64edfc167fc585399505fe22a875a5ee434df602617c40` |
| tests/fixtures/p6e1b_os_listener.py | `92ce224abf367bb2a31f29c7d59b1d4e8775bcdd8c3cffb0185d22b08583f6c6` |

主要证据目录为 `E:\AllProject\data-analysis-agent\.cache\p6e1b-os-preflight`。`cim-reproduction.json`、`context-comparison.json`、两份 `windows-powershell-*.json` 保存原错误与上下文对照；`native-probe.json`、`native-normal-context.json`、`get-process-netstat-normal.json` 保存独立接口；`diagnostic-*.json` 为最终只读诊断；`native-tests.log`、`native-tests-evidence.json` 和 `native-tests-summary.json` 保存真实失败与清理；`functional-summary.json`、`functional-tests.txt` 保存完整 778 项结果。最终 `evidence-index.json` 保存本轮产物摘要。

证据→结论→控制链：CIM 对照错误及原生错误 5 → 访问上下文相关故障、进程身份 UNKNOWN → 预检保持关闭；原生监听夹具失败 → 不能启用替代通路；清单/ACL/历史摘要匹配 → 证据保全有效，但不授予恢复许可。日志不包含密码、完整 DSN、API Key 或敏感环境值。

在上述项目目录中可重新运行纯离线命令（不会执行数据库操作）：

```powershell
$env:PYTHONPATH='src;tests;.'
.\.venv\Scripts\python.exe -m unittest test_p6e1b_os_readonly -v
.\.venv\Scripts\python.exe .cache/p6e1b-os-preflight/run_checks.py functional
.\.venv\Scripts\python.exe scripts/p6e1b_recovery.py check
```

显式原生诊断命令为 `.\.venv\Scripts\python.exe -m p6e1b_os_native`，**会临时绑定本机无害测试监听端口**，不属于纯离线命令；本轮已获本任务限定范围并执行，保留两项失败。不能将它用于自动放行恢复。该诊断入口写固定输出名，未来若重跑必须先保全本轮证据并分别记录，不能以重跑覆盖本轮失败；本轮已停止，不再运行。

### 9.6 恢复材料与证据保全复核

原部分集群仍是固定 run_id 的原路径，970 文件/997 条清单及 997 条 Owner/Group/DACL 全量重采集结果均匹配；没有重解析点越界读取。原清单摘要仍为 `3958cca5dd6da9b120695380a02526bd9c5e76ca8d3329b358f419295493c72c`，ACL 摘要仍为 `2e7c868c4bb71dda151e256aea4e042723d17b970aac2006110476aadcda99cb`。未采集 SACL，也不宣称初始化完整。

原存档位置和新私有 attempt 目录仍不存在；原四份失败账本和旧环境授权 SHA 全匹配。既有基线中 1825 个匹配 rag/human/review_asset 的保护文件摘要全部一致；未修改冻结 RAG/人工评审资产。`preservation-verification.json` 保存核对结果。

本轮新增诊断不在真实执行调用链中，真实恢复绑定的八项代码/二进制/原生凭据未改变；原待批计划**没有因本轮诊断失效，也没有获得批准**。重新按当前代码生成的两份未批准副本置于上述证据目录，和原待批文件规范内容完全一致；原文件逐字节保留。

| 绑定 | 当前规范 SHA-256 / 状态 |
| --- | --- |
| 恢复计划 | `bbc65bf7baa555cf9abad25c86890bbe4dd8d5d9747e244d9f761e1a068147e5` |
| 有限 policy | `5f596e4722bc97cd3d6b7892228f7f28c61c4019fced04eca1bd9773f0dece3b` |
| 恢复入口 | `62b7287941329e736693c6ac6c6b854fd211f795b023df951919d5ceb03cd35c` |
| Windows Job | `63d27fa33658dbd791666175951310d9400f1a9dd26ea6a7ba420b028b39e2ee` |
| Initdb 执行器 | `b8982cb19cfa563805849ef1aae068dffdede6f8fdddcaa8911d48da8dd973fb` |
| 历史 execute.py | `2f94881844cc2d0b5772b598a5f49e9449398289d2cc649bb95c85069f16dd5b` |
| 已有只读 check | PENDING_BINDINGS_VALID / real_recovery=BLOCKED；不是授权 |

run_id、attempt_id、route B、55441、源/存档路径、两库四角色及原环境计划 SHA 全不变。两份 approved 文件仍不存在，authorized=false、十二项许可全 false、approved_by/expires_at 为空；未来事实仍 archive_completed=false、new_system_identifier=null。没有修改旧授权期限或提前写入 archive receipt/PG 结果。

待 OS 证据充分后，仍须本人在后续独立消息中明确批准固定计划、策略及原有十二项操作，含原目录隔离存档和 ACL 收紧、原目标重建、批准时限、实例启动、两库四角色/schema、测试连接/合成写入/PG01～08、实际身份记录、私有凭据使用及仅本 attempt 资源清理。本任务的诊断许可不等于这些操作许可，旧初次创建批准也不能沿用。

### 9.7 本轮最终停点

| 项目 | 最终状态 |
| --- | --- |
| OS 预检 | **BLOCKED**：CIM 权限上下文问题已定位；候选身份未知和原生监听验证失败尚未解除 |
| 恢复计划一致性 | **PASS**：当前代码/策略/原生凭据/原目录及历史绑定有效；未批准材料一致 |
| 正式恢复授权 | **NOT APPROVED**：无本轮明确批准，未生成 approved 文件 |
| PG-01～PG-08 | **NOT EXECUTED** |
| P6-E1B 真实集成总验收 | **BLOCKED** |

本轮停止于诊断和离线验证。没有调用恢复入口的 run 分支，没有移动旧目录、运行 initdb/pg_ctl/psql、启动/连接 PostgreSQL、修改服务或结束已有进程。没有以 778 项离线通过覆盖两项原生失败，更没有将未执行的真实 PG 描述为通过。


## 10. 本轮追加：OS 预检最终收口与最小修复

日期：2026-11-10。**结论仍为 BLOCKED，不能进入正式恢复授权审批。** 两项原生 LISTEN 失败已进一步定位为本机 API 在文档状态字段处实际返回 0，并非目前发现的 ctypes 字段错位。Python 和独立 C#/.NET 见证均复现；系统内部为什么返回该值仍未确定。本轮没有把 0 当作 LISTEN，也没有删除、跳过或放宽原断言。

现有 11 个 PostgreSQL 候选的 PID/创建时间和关联服务可读，但实时映像仍被拒绝；配置目录不能当作有效数据目录认证。已向本人请求单次管理员只读核验许可，本轮交付时尚未收到明确批准，未进行管理员查询。两个问题没有同时解除，因此没有修改 `NativePlatform.preflight()` 或任何实际恢复调用链。

### 10.1 原始字节、ABI 与时序的核验结果

观察环境：Windows build **26300.9818**，Python 3.12.10 AMD64；普通账户有效管理员令牌=false。受限工具外的只读运行仍是同一普通账户，不是 UAC 提权。

| 核验项 | 实测/审查结果 |
| --- | --- |
| TableClass | 3=OWNER_PID_LISTENER，5=OWNER_PID_ALL；两类都返回 owner-PID 行。IPv4/IPv6 均用正确地址族 2/23，Reserved=0。没有用 BASIC、OWNER_MODULE 或 TCPROW2 的结构解释结果 |
| DWORD 与对齐 | 明确使用 uint32；IPv4/IPv6 行均 4 字节对齐，表头计数为 4 字节 |
| IPv4 行 | 大小 24；state/local/port/remote/remote_port/pid 偏移依次 0/4/8/12/16/20 |
| IPv6 行 | 大小 56；local/local_scope/port/remote/remote_scope/remote_port/state/pid 偏移依次 0/16/20/24/40/44/48/52 |
| 状态解析 | 原始 `dwState` 是主机序 DWORD；LISTEN 只接受 2。0 保持 `listening=null`，不做任何成功归一化 |
| 地址与端口 | IPv4 地址按原始 4 字节解释，IPv6 按 16 字节解释；端口低 16 位使用 ntohs。样本地址/端口与自己创建的套接字一致 |
| 就绪时序 | 先 bind/listen，再读取 SO_ACCEPTCONN=1，随后发布 ready。Python 自身同步探针、夹具和 .NET 同步探针均复现，不依赖 sleep 推测启动完成 |

字段、枚举与状态定义依据 [TCP_TABLE_CLASS](https://learn.microsoft.com/en-us/windows/win32/api/iprtrmib/ne-iprtrmib-tcp_table_class)、[IPv4 owner-PID 行](https://learn.microsoft.com/en-us/windows/win32/api/tcpmib/ns-tcpmib-mib_tcprow_owner_pid)、[IPv6 owner-PID 行](https://learn.microsoft.com/en-us/windows/win32/api/tcpmib/ns-tcpmib-mib_tcp6row_owner_pid)。[API 参数/返回码契约](https://learn.microsoft.com/en-us/windows/win32/api/iphlpapi/nf-iphlpapi-getextendedtcptable)与调用一致。

**本轮真实原始观察：** Python 自身探针 PID=23596，IPv4 127.0.0.1:55281，SO_ACCEPTCONN=1；3/5 两类表最终返回码均为 0，对应行原始十六进制如下：

```text
000000007f000001d7f1000000000000000000002c5c0000
^^^^^^^^ state offset=0，4 字节=00000000，直接解码结果=0
```

同一探针的 IPv6 ::1:55282 行大小 56，state offset=48；该处同样为 `00000000`。完整匹配行原始字节、端口、PID、类号、缓冲区大小和解析值保存在 `tcp-abi-original.json`，没有保存无关进程/远端的整表字节。

为排除 Python 解析器单独导致异常，实际运行了 C# P/Invoke + CLR 结构和 .NET Socket 见证：PID=16176，IPv4 50730、IPv6 50731，两者 SO_ACCEPTCONN=1；每族连续两次采样均返回 0，行大小/状态偏移分别为 24/0、56/48，与 Python 一致。证据为 `clr-tcp-witness.ps1` 和 `clr-tcp-witness.json`。这是独立运行时/解析实现的对照，**不是 Windows SDK C++ 头文件编译或内核跟踪证据**；没有据此声称已定位唯一系统组件。

根因等级：**直接失败原因 CONFIRMED**——API 数据调用成功，但文档状态字段真实为未定义值 0，严格 LISTEN 断言因此拒绝；常见类号、字段类型/偏移、端口字节序及 ready 采样时序问题未解释此现象。**底层系统原因 UNDETERMINED**——未确认是否特定更新、驱动或其他系统实现所致。

Microsoft Q&A 有相同 build 的独立报告及支持讨论，亦提醒不要把该异常行当作可靠身份依据；它只能作为旁证，不能证明本机已确认某 KB 的因果或已有官方修复。[同 build 的独立报告与讨论](https://learn.microsoft.com/en-us/answers/questions/6061425/windows-11-26h2-26300-9818-gettcptable2-returns-st)

### 10.2 实际最小修复

只修改四个诊断/测试文件，没有增加基础设施框架、依赖或恢复后备通道：

1. `scripts/p6e1b_os_readonly.py`：修正另行发现的 **非零 IPv6 scope ID 网络字节序**，使用 ntohl。旧代码对非零 scope 会误读；本次失败样本 scope=0，因此这项修复不解释或掩盖 LISTEN 失败。提取有界 TCP 原始表读取，显式白名单 3/5 类；新增只保存本轮指定 PID/端口行的原始字节证据方法，保留错误码阻断及原有状态判断。
2. `tests/fixtures/p6e1b_os_listener.py`：先完整写入本轮夹具的 ready.pending.json，再在同一唯一测试目录发布 ready.json，避免父进程读取到半写 JSON；附带 SO_ACCEPTCONN。没有移动任何 PostgreSQL 目录或历史文件。
3. `tests/p6e1b_os_native.py`：两项旧 LISTEN 断言原样保留，在其之前采集 3/5 类原始字节和就绪证据；每次执行以时间+随机标识保存新证据，防止覆盖历史失败。
4. `tests/test_p6e1b_os_readonly.py`：原 21 个测试方法 AST 完全不变，新增 6 项，验证双栈 ABI、非零 scope 字节序、类号白名单、原始 0 不重写、原始证据过滤和 IPv6 状态偏移。

`NativePlatform.preflight()` 的全局进程限制仍存在，但本轮没有足够证据把任何候选认证为 OTHER，也未获得正常原生 LISTEN 结果。依照“两个条件均满足才能修改”的边界，本轮保持原安全门，不以配置路径、自报 JSON、类 3 成员资格或受限环境空结果放行。

### 10.3 11 个候选进程的只读身份核验

普通账户当前可使用 CIM 读取名称、PID、父 PID、创建时间，可读取 Win32_Service 配置，也可读取已配置目录的精确文件元数据；不能读取这些候选的实时进程映像。新一轮原生查询中，对每个候选分别请求 **QUERY_LIMITED_INFORMATION（0x1000）** 和加 SYNCHRONIZE（0x101000），两种均返回 Access denied=5。因此不是仅移除 SYNCHRONIZE 就能解决。

| 候选 | 可确认的关系 | 尚缺证明 / 当前结论 |
| --- | --- | --- |
| pg_ctl PID 23136 | CIM 创建时间 2026-11-10T05:43:45.655669Z；Win32_Service 显示 postgresql-x64-18 Running，服务 PID=23136 | 实时映像不可读；服务配置并非当前执行映像证明；UNKNOWN |
| 主 postgres PID 33444 | 父 PID=23136，CIM 创建时间 2026-11-10T05:43:45.863649Z | 实时映像/有效数据目录关联未认证；UNKNOWN |
| postgres PID 25992、32376、38288、8808、10804、19904、31336、9248、1488 | 当前父 PID=33444，各自 CIM 创建时间已保存 | 只有父子关系不足以继承可信数据目录身份；逐个映像不可读；全部 UNKNOWN |

服务配置命令在内存中只提取 `-D` 的配置目录，未输出完整命令行：`C:\Program Files\PostgreSQL\18\data`。它是**配置候选路径**，不能认定为已认证的有效 data_directory。精确目录/`postmaster.pid` 元数据可读，目录属性为普通 Directory、文件长度 71 字节；本轮没有读取其内容、其他数据库文件或配置密钥，也没有把这些元数据转换成 OTHER。

普通查询证据为 `ordinary-service-process.json`、`ordinary-configured-directory.json`、`ordinary-open-process.json`。配置命令只保存 SHA-256。没有调用任何 SQL、PostgreSQL 客户端或数据库连接。候选不是被认定为本轮冲突，也不是被认定为业务实例；结论是**隔离关系仍不可充分证明**。

### 10.4 需要本人批准的具体只读许可

已提出单次管理员只读核验请求；**尚未收到明确批准，未触发 UAC、启动管理员进程或启用 SeDebugPrivilege**。这与普通账户离开工具受限上下文的诊断严格分开。

拟许可仅限：重新核对上述候选 PID 和创建时间；以只读句柄获取实时进程映像/创建时间/存活情况；读取关联 `postgresql-x64-18` 的服务身份配置；只有路径明确后，仅读取对应 `postmaster.pid` 及目录/文件身份，核对 PID、启动时间、有效目录和固定目标/存档/attempt 的关系。若 PID 或身份发生变化、权限仍不足、存在重解析疑点或无法充分关联，立即停止并保留 UNKNOWN，不扩展到其他进程或目录，不接受人工自填 OTHER。

风险是管理员只读查询可能接触本机真实路径、服务账户和进程参数；输出必须过滤完整命令行、密码、DSN、API Key，只保存必要身份字段及摘要。许可不包含读取评论/表数据、配置密钥、进程内存、连接数据库、修改 ACL/服务/系统策略、停止进程、更新系统或任何恢复操作；也不赋予十二项正式恢复许可。

即便今后这次只读身份核验通过，**两项原生 LISTEN 原断言仍必须先原样通过**，不能据管理员批准就解锁恢复。本轮不卸载更新、重启系统、禁用安全软件或自动进入其他准备阶段。

### 10.5 本轮实际测试与保全

| 层次 | 结果 |
| --- | --- |
| OS 纯离线专项 | **27/27 PASS**（原 21+新增 6）；封锁真实进程/网络/数据库/模型操作 |
| 适用后端离线回归 | **784/784 PASS**，232.608 秒，0 failure/error/skip；包含原 778 项 |
| 原生 Windows 专项重跑 | **7 项：5 PASS、2 FAIL、0 error/skip**；失败仍为 IPv4/IPv6 的旧 LISTEN 断言，未伪装通过 |
| 独立 C#/.NET 见证 | 双栈各两次原始 0；用于诊断，不记作原生 LISTEN 通过项 |
| 旧安全断言 | 原生程序原有 14 条 assert 全保留；F02 32、F03 24、F04 准备 26、F04 入口 53 项全部通过；PG-07 失败阻断 PG-08 的断言不变 |
| 原部分集群与失败记录 | 970 文件/997 条清单匹配，997 条 Owner/Group/DACL 重采集匹配；存档和实际 attempt 目录仍不存在；旧失败账本/授权不变 |
| 旧诊断及冻结资产 | 上轮索引的 47 份证据逐字节核对无变化；既有 1825 份匹配 RAG/human/review_asset 保护基线文件摘要无变化 |

本轮原生专项三个夹具 Job 均保留 all_exited/handles_released/output_drained=true；只清理本轮持有进程，不按名称结束任何既有 PostgreSQL。同步 Python/.NET 临时 socket 均有上下文管理或 finally Dispose。原 13 项历史静态导入问题不在适用清单，没有清理、隐藏或改变其原状态。

新证据目录：

```text
E:\AllProject\data-analysis-agent\.cache\p6e1b-os-preflight\closeout-20261110T132538Z
```

真实原生专项独立目录：

```text
E:\AllProject\data-analysis-agent\.cache\p6e1b-os-preflight\native-runs\20261110T133032Z-b28a4c463b844e86b8a3de6a677bba3a
```

完整回归记录在新证据目录的 `functional-summary.json` / `functional-tests.txt`，原生失败及原始字节在上述 native-runs 目录。`evidence-index.json` 保存本轮文件摘要；原第 9 节和旧证据未覆盖。

### 10.6 最终代码摘要与恢复材料

| 本轮变更文件 | SHA-256 |
| --- | --- |
| scripts/p6e1b_os_readonly.py | `8d13267f715bc425d7609c94af2703124faff8a297469cb4e26e956bc149c0ae` |
| tests/p6e1b_os_native.py | `605542a79f7078ff3c37b344195c71b9d040439ac75c2fe74cce06f6c1e66e61` |
| tests/fixtures/p6e1b_os_listener.py | `0d434967f8bc87e2a3ba6f73dfa5346b747fa2d56cb87be798e2af59799e375a` |
| tests/test_p6e1b_os_readonly.py | `cf10149e7f9a25783e8d5a53fa7041211b14e37c6f9f267c278f0c9edf161526` |

实际恢复调用链未修改：恢复入口 SHA 仍为 `62b7287941329e736693c6ac6c6b854fd211f795b023df951919d5ceb03cd35c`，Job 为 `63d27fa33658dbd791666175951310d9400f1a9dd26ea6a7ba420b028b39e2ee`，执行器为 `b8982cb19cfa563805849ef1aae068dffdede6f8fdddcaa8911d48da8dd973fb`，历史 execute.py 为 `2f94881844cc2d0b5772b598a5f49e9449398289d2cc649bb95c85069f16dd5b`。诊断模块不被恢复入口调用，因此本轮变化没有使绑定失效，也没有产生恢复权限。

已用当前代码重生成未批准副本并与原版本比较，规范内容相同：恢复计划 SHA=`bbc65bf7baa555cf9abad25c86890bbe4dd8d5d9747e244d9f761e1a068147e5`；policy SHA=`5f596e4722bc97cd3d6b7892228f7f28c61c4019fced04eca1bd9773f0dece3b`。副本保存在本轮证据目录，原 pending 逐字节保留；若以后修改实际调用链，则必须另行重新绑定，不能沿用旧审批。

固定 run_id、attempt_id、55441、route B、原环境计划 SHA、目标/存档/两库四角色均不变。已有 `check` 再次返回 PENDING_BINDINGS_VALID / real_recovery=BLOCKED；authorized=false、十二项许可全 false、批准人/期限为空，两份 approved 文件不存在，未来事实仍 archive_completed=false/new_system_identifier=null。

### 10.7 收口结论

| 项目 | 当前结论 |
| --- | --- |
| OS 预检 | **BLOCKED**：两项真实 LISTEN 断言仍失败；11 个候选的实时映像/有效目录关系仍 UNKNOWN |
| 恢复材料 | **PASS**：未批准版本与当前恢复调用链/策略/原生 Job 凭据一致；不代表运行准入 |
| 正式恢复授权 | **NOT APPROVED** |
| PG-01～PG-08 | **NOT EXECUTED** |
| 能否进入正式恢复授权审批 | **否**：OS 阻断未解除；管理员只读许可亦不能替代恢复审批 |

本轮没有 recover、旧 cluster 移动、initdb、pg_ctl、psql、PostgreSQL 连接/对象操作、已有服务/进程修改、收费 LLM 或 Embedding。没有将离线 784 项通过用来覆盖真实原生失败。已停止，不自动新增其他准备阶段或执行恢复。


## 11. 本轮追加：已批准的单次管理员只读身份核验

日期：2026-11-10。本人已在后续独立回复中明确批准“上述一次管理员只读核验”。**这次只读许可已经 APPROVED；正式恢复仍 NOT APPROVED。** 本轮仅发起一次管理员启动请求，启动/等待封装返回失败，没有查询 PID、管理员结果文件或可验证的身份结论。没有自动重试，OS 预检继续 BLOCKED。

### 11.1 批准范围与普通预检

许可仅覆盖原 11 个候选 PID 的创建时间、实时映像和关联服务配置，以及路径明确后对应 postmaster.pid 与目录/文件身份。没有获准读取评论/表数据、连接数据库、修改服务/ACL/安全策略、停止进程、运行 PostgreSQL 程序或恢复环境。没有将本次批准写入原恢复授权文件，没有设置恢复计划 authorized=true。

普通账户预检先发生一次保守停止：PowerShell ConvertFrom-Json 已将 ISO 时间转为 DateTime，再传给 Parse 时通过字符串转换丢失了小数秒。以 PID 23136 为例，精确时间为 `13:43:45.6556690+08:00`，错误转换得到 `13:43:45.0000000+08:00`，差 6556690 个 100ns ticks（0.655669 秒）。该次没有启动管理员进程，不能据此认定真实进程发生变化。

使用不经过字符串的精确 DateTimeOffset 转换后，重新核对原 PID、名称及创建时间，11 个候选均与原基线一致。只读私有证据目录继承既有受限 ACL；没有调整任何既有目录权限。上述初始失败及修正记录保存在 `precheck-evidence.json`，没有覆盖原恢复或 initdb 失败账本。

### 11.2 一次性只读查询准备与实际启动

本轮只在私有证据目录准备一次性 `observe.ps1`，不修改仓库恢复入口、Agent 或系统配置。脚本包含：

- 管理员令牌检查；原 11 个 PID/创建时间及父 PID 的前后核对；查询仅过滤这些固定 PID。
- 以只读句柄获取映像、创建时间和存活信息，保持进程句柄至最终复查，防止仅凭重复 PID 错配身份。
- 复核已发现服务 postgresql-x64-18 的 PID、状态及既有配置摘要；不输出完整配置命令行。
- 路径明确且无重解析点时，仅对已确定的安装映像、数据目录及精确 postmaster.pid 获取文件身份；控制文件最多 4096 字节，只保留所需 PID/目录/启动时间及摘要。
- 不包含数据库客户端/连接、模型调用、系统服务修改或进程终止；不输出 OTHER 或恢复权限。正常完成也只是观察证据，不是 OS 放行。

PowerShell 语法检查、C# 声明编译和 Windows PowerShell 5.1/.NET Framework 兼容编译均通过；这些检查未执行原生身份方法，**不构成管理员核验通过**。准备脚本 SHA-256：`4b149bca7df4bb1886fab7e3e0ea182c6c97b8a9e68ad2853df2401ee4bca672`。

实际采用 Windows 系统 PowerShell、`-NoProfile -NonInteractive -File`、`Start-Process -Verb RunAs -WindowStyle Hidden -PassThru` 发起系统管理员启动请求；未设置 ExecutionPolicy，也未代点 UAC。参数只含本地只读脚本路径，无密码、DSN 或 API Key。

| 时间（UTC） | 实际事实 |
| --- | --- |
| 13:54:54.5838804 | 写入 launch-start.json 后，发起唯一一次管理员启动请求 |
| 13:56:56.9491020 | 约 122.365 秒后，启动/等待封装返回 System.InvalidOperationException；进入失败停止分支 |
| 13:58:52.1007193 | 普通账户按该精确脚本标识查询，没有可见匹配；observation.json 仍不存在；未终止任何进程 |

`launch-failure.json` 的 NativeErrorCode=null。没有 `launch-process.json`，也没有管理员 `observation.json`。因此**不能确认管理员查询成功启动、完成或完成句柄清理**，也不能将没有输出当作 11 个身份都正常。

本次启动记录仅保留异常类型、时间及空的原生错误码，未保存底层错误文本；具体启动失败原因仍 UNDETERMINED。不能推断为用户取消 UAC、系统权限策略拒绝或管理员读取失败。后续普通账户的空匹配也不能作为无残留进程的充分证明。没有为了重新获取错误而再次发起管理员查询，也没有按名称终止进程。

### 11.3 证据、兼容性与未执行事项

私有证据目录：

```text
E:\AllProject\data-analysis-agent\.cache\p6e1b-exec\private\os-identity-readonly-20261110T134949Z
```

`approval-evidence.json` 记录本人的真实只读批准范围；`baseline.json`、`precheck-evidence.json` 记录基线及普通预检；`offline-source-check.json`、`windows-powershell-compile.txt` 记录纯编译检查；`launch-start.json`、`launch-failure.json` 记录唯一启动请求；`post-launch-readonly-state.json` 保留普通上下文观察及其局限。`delivery-summary.json` / `evidence-index.json` 保存本轮状态与摘要。

实际恢复调用链及四个已测试的 OS 诊断/测试源文件摘要均未变，本轮不重复后端回归。第 10 节的 784/784 离线通过和原生 5 PASS/2 FAIL 是此前已完成、当前代码仍匹配的证据，**不是本轮重新执行结果**。两项 LISTEN 失败未重跑，也未修改断言。

重新验证：原部分集群文件清单不变，原存档/实际 attempt 目录仍不存在，旧失败记录未变。恢复计划规范 SHA 仍为 `bbc65bf7baa555cf9abad25c86890bbe4dd8d5d9747e244d9f761e1a068147e5`，policy 为 `5f596e4722bc97cd3d6b7892228f7f28c61c4019fced04eca1bd9773f0dece3b`，未批准绑定验证仍通过，未来事实没有回填。

没有连接数据库、运行 initdb/pg_ctl/psql/PostgreSQL、移动旧 cluster、修改既有服务或进程、调用收费 LLM/Embedding。是否发生了目标管理员身份读取本身**未经结果证实**，不记作“零次读取”或“核验成功”；查询进程清理状态也没有伪造为 PASS。

### 11.4 停止结论

| 项目 | 当前结果 |
| --- | --- |
| 单次管理员只读许可 | **APPROVED**；仅本次限定读取，不包含恢复 |
| 本次管理员启动/核验 | **BLOCKED**：唯一启动请求失败，未取得管理员结果；无自动重试 |
| 11 个候选身份 | **UNKNOWN**：未新增可验证的实时映像/有效目录结论 |
| OS 预检 | **BLOCKED**：原生 TCP 状态失败仍在，进程身份仍未完整核验 |
| 恢复材料 | **PASS**：未批准绑定有效，不代表允许执行 |
| 正式恢复授权 | **NOT APPROVED** |
| PG-01～PG-08 | **NOT EXECUTED** |

本轮已停止。没有以聊天中的只读批准代替 Windows 系统确认，没有把批准推导成恢复权限，也没有自动进行第二次管理员启动或新增准备阶段。

### 11.5 本人补充的窗口观察

本人随后明确回复：**“没有看到系统授权窗口”**。此观察已另存 user-window-feedback.json。不能将原启动失败归因为本人取消 UAC，也不能据此证明管理员查询已启动或完成；底层失败原因继续为 UNDETERMINED。

没有重复启动、提权、终止进程或数据库操作。单次管理员只读许可仍为 APPROVED，OS 预检仍 BLOCKED，正式恢复授权仍 NOT APPROVED，PG-01～08 仍 NOT EXECUTED。


## 12. 最终阻塞处理：保留 BLOCKED，建议暂时搁置当前真实集成

日期：2026-11-10。**当前不具备可靠通过 OS 预检的条件，建议暂时搁置本机 P6-E1B 的真实恢复和数据库集成。** 管理员启动的底层失败原因没有被充分留存；TCP API 原始状态异常仍在。继续重复启动 UAC、采样同一接口或增加准备阶段，不能解决这两个证据缺口。本轮没有再次申请 UAC、提权、运行 TCP 夹具或执行任何数据库操作。

### 12.1 管理员启动失败：能定位到哪里

审查依据为第 11 节的账本、本人“没有看到系统授权窗口”的回复，以及本会话上次实际执行的启动命令块。完整启动器当时没有另存为仓库脚本；本轮未伪称存在原始启动器文件。关键调用顺序如下（代码节选，仅用于审查，不执行）：

```powershell
SaveNew 'launch-start.json' @{utc=[DateTime]::UtcNow.ToString('o');script_sha256=$expectedHash;scope='single approved administrator read-only identity query';recovery_authority=$false}
$process=Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList @('-NoProfile','-NonInteractive','-File',$script) -Verb RunAs -WindowStyle Hidden -PassThru -ErrorAction Stop
SaveNew 'launch-process.json' @{utc=[DateTime]::UtcNow.ToString('o');pid=$process.Id}
$watch=[Diagnostics.Stopwatch]::StartNew()
```

此后才进入最长 150 秒的 WaitForExit 循环。已有 start 与 failure 的 UTC 差值为 **122.365 秒**；没有 launch-process.json 或 observation.json。该数值是两条墙上时钟时间戳之差，**不是某个调用的单调计时**。

| 判断 | 证据等级 / 结论 |
| --- | --- |
| 自定义 WaitForExit 150 秒循环导致失败 | **不支持**：正常调用顺序要求先成功记录 PID 才进入循环；没有该记录。不能将 122 秒称为此循环超时 |
| 只读脚本 90 秒截止导致失败 | **未证实**：没有脚本启动/结果事实，不能以两个时限相近来推断 |
| 时间主要耗在启动阶段 | **有力推断**：最可能为 Start-Process/ShellExecute；但返回对象的 Id 访问及首个 PID 账本写入之间没有独立标记，不能断言具体 Win32 调用等待了 122 秒 |
| 用户取消 UAC | **未证实**：本人明确未见窗口；没有取消错误码，不得归因于本人取消 |
| 日志丢失了可用于诊断的异常信息 | **CONFIRMED**：原 catch 只保存 GetBaseException 后的类型、可选 NativeErrorCode 和固定文本，未保存 Message、异常链、HRESULT、FQID 或失败语句 |
| 管理员进程是否曾创建、是否清理完成 | **UNKNOWN**：没有可验证 PID；普通上下文的空匹配不是不存在的证明，不自动终止或重试 |

PowerShell 7.6.9 的公开源码中，ShellExecute 路径可把 Win32Exception 的消息包装为没有原生异常内链的 InvalidOperationException；PassThru 无可返回进程对象也能产生同类异常。因此只有异常类型不能区分原因，GetBaseException 也不能恢复已经被上层丢弃的 Win32 码。原日志又省略了消息，当前无法追溯恢复。源码说明这种可能性，**不证明本次一定走了其中某一分支**。[PowerShell 7.6.9 Process.cs](https://raw.githubusercontent.com/PowerShell/PowerShell/v7.6.9/src/Microsoft.PowerShell.Commands.Management/commands/management/Process.cs)

### 12.2 RunAs、Hidden、NonInteractive 与执行上下文

`-Verb RunAs`、`-WindowStyle Hidden`、`-PassThru` 属于合法的 Windows ShellExecute 参数组合；没有参数集冲突证据。Hidden 控制新进程窗口显示，不能仅凭它判定 UAC 被隐藏。`-NonInteractive` 位于子 PowerShell 的参数列表，用于阻止子 PowerShell 自身交互；它不是启动器的 UAC 开关，也不能合理地被当成“子进程尚未启动时取消了 UAC”的确证。[Start-Process 文档](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/start-process?view=powershell-7.5)、[PowerShell.exe NonInteractive](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_powershell_exe?view=powershell-5.1)、[ShellExecute 参数](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/ns-shellapi-shellexecuteinfow)

本轮只读观察当前普通上下文：PowerShell 7.6.9、ConsoleHost、SessionId=7、UserInteractive=true、WinSta0/Default，管理员令牌=false，标准输入/输出均被重定向；exe 的 verbs 包含 runas。当前 LocalMachine 执行策略为 RemoteSigned，其他列出作用域为 Undefined，未作更改。

这些事实**不能证明过去启动时的全部上下文相同，也不能证明 UAC 通道可交互**。当前没有证据把环境直接等同于 Session 0/无桌面服务；但工具宿主、令牌/代理、Shell/AppInfo 交互链或策略限制仍为 POSSIBLE。没有可用原生错误码，不能进一步选定原因。普通账户能运行系统 PowerShell 的编译检查，也不代表能够可靠执行交互式管理员启动。

所以，本轮不通过去掉 Hidden、改 NonInteractive、增加超时或再次调用 RunAs 来试错；不改系统服务、策略、安全软件或权限。

### 12.3 手动方式：已准备，但不建议现在为推进集成而执行

若本人以后只希望补充身份诊断，**可见的本机管理员 PowerShell 手动执行**比当前工具再次请求 RunAs 更容易审阅和定位失败；它仍不能修复 TCP 异常，也不保证所有子进程有效数据目录均得到独立证明。当前建议搁置集成，而非立即开展这项可选操作。

已复用原只读观察器，准备以下未批准材料，没有重写执行框架：

- [手动步骤与安全限制](E:/AllProject/data-analysis-agent/.cache/p6e1b-exec/private/os-identity-manual.pending-20261110T143329Z/README.pending.md)
- [待批准的只读脚本](E:/AllProject/data-analysis-agent/.cache/p6e1b-exec/private/os-identity-manual.pending-20261110T143329Z/observe.pending.ps1)

脚本 SHA-256：`cb6b6914b90e03af4f8b779bb01bb8057739e25c2a06634ca02acc6eff8a61df`。基线摘要：`b5b241f0658c8cf0e05518390c195d271bc108fde10b8cc2f581bf88149d3c8b`。原脚本、原失败账本和基线均未覆盖。

新副本仅更换独立证据输出目录、添加默认关闭的手动参数和更完整的安全异常链。默认只输出 PENDING_NEW_USER_APPROVAL_NO_QUERY，不运行查询；不包含 RunAs、Start-Process、策略调整、服务/ACL 修改、进程终止、数据库或模型调用。未传执行参数时不会创建 manual-start.json/observation.json。该参数只是防误触，不是服务器授权。

**以下步骤只能在本人后续针对本脚本摘要与范围作出新明确批准后进行，本轮未执行：**

1. 先确认原启动请求/授权窗口已结束；若无法确认潜在查询状态，停止，不自动清理。
2. 用户在本机 Win+R 中输入 `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile`，以 Ctrl+Shift+Enter 手动打开并亲自确认系统窗口，保持控制台可见。未出现可见管理员窗口或身份检查不通过就停止。
3. 按 README 在可见窗口核对脚本 SHA 和管理员身份，然后只运行一次 `& $script -ExecuteApprovedReadOnly`。不使用 Hidden/NonInteractive/RunAs 包装，不更改执行策略；策略阻止时停止，不使用 Bypass 或 Unblock-File。
4. 范围仍是原 11 个 PID、原创建时间/父 PID、唯一服务 postgresql-x64-18、已确定安装映像与 `C:\Program Files\PostgreSQL\18\data\postmaster.pid`。任何变化、权限不足、重解析/文件身份异常或结果已存在，停止；不刷新基线、换路径、删除结果或重试。
5. 仅读取脱敏 observation.json。成功也只是实时映像、文件身份和主进程控制文件观察；子进程目录关联仍有推断边界，不自报 OTHER，不写入恢复授权，不自动开始数据库步骤。

输出限于该新私有材料目录。脚本保存异常链类型、HRESULT、可用 Win32 码及标准系统文本，已知失败理由使用白名单；任意消息与 FQID 只留指纹，不转储完整异常对象/命令行/凭据。90 秒为检查点截止，不是底层系统调用可靠取消。本轮没有请求新的手动执行授权，也没有提供执行参数来运行它；原单次自动启动批准不自动沿用。

### 12.4 TCP state=0：最终判断与不再追加测试的理由

本轮只读复核旧 Python 原始字节、C#/.NET 见证及失败记录；未重跑监听夹具。当前只读版本记录仍为 Windows **26300.9818**。

已排除的常见解释包括：owner-PID 类号选错、将其他结构当 owner-PID 行、32 位 DWORD 与常见对齐/行大小/状态偏移错误、测试端口和地址解码错误，以及仅因夹具尚未 listen 的简单时序问题。依据为两族正确的原始行、SO_ACCEPTCONN=1、同步自身探针和独立 CLR 解析；不是简单地把不同工具返回一致当作内核真值。非零 IPv6 scope 的旧字节序问题已另行修复，失败样本 scope=0，因此无因果解释。

不能排除或确认的系统原因包括 IP Helper/网络状态枚举路径、网络栈/过滤或拦截组件、更新相关回归及其他宿主差异。现有 C# 见证不等于本机 SDK C++ 头文件编译、内核状态追踪或更新前后对照，因此不宣称唯一根因或断言某 KB 必然导致它。

**可信公开信息的等级：** 同 build 的 Microsoft Q&A 有相符的实测报告和署名支持回复，提醒异常行不能用于可靠的归属/安全判断；这支持谨慎处理，但不等于产品组发布的已确认缺陷或修复承诺。[相符案例及支持讨论](https://learn.microsoft.com/en-us/answers/questions/6061425/windows-11-26h2-26300-9818-gettcptable2-returns-st)

本轮又核对了相关官方 KB5127936 发布说明；其已知问题部分未列本症状，也未给出该 API 异常的修复。本结论仅限已核对的公开材料，不声称微软内部没有追踪，也不证明本机更新安装损坏。[官方更新说明](https://support.microsoft.com/en-us/servicing/os/windows-11/2026/10/kb5127936-windows-11-25h2-26h2-update)

**最终为 BLOCKED。** state=0 仍不等于 LISTEN；类 3 中出现一行也不被转成有效 LISTEN/身份授权。继续重复 GetTcpTable2、netstat、CIM 或相同合成监听，只能增加相符观察，不能建立新的可信通过条件。当前范围内没有预计值得继续执行、能可靠解除阻塞的一次性只读验证；本轮不再增加 TCP 测试，不启动全系统跟踪或修改平台来试错。

### 12.5 验证、恢复边界和最终建议

本轮新材料仅进行 **2 项纯离线检查，均通过**：默认关闭参数确实不查询/不写运行事实，以及模拟异常链能保留内部 Win32 码并遮蔽合成密码。另完成 PowerShell 语法检查、原生声明编译、Windows PowerShell 5.1 的编译与默认关闭检查；都没有执行管理员身份方法。没有新增大型测试集，也没有将这些准备检查记作真实 OS 通过。

实际恢复调用链、既有诊断/测试源文件与断言未改。历史 784/784 离线回归保留，本轮未重跑；真实原生 5 PASS/2 FAIL 原证据完整保留，本轮未重跑或改断言。PG-07 失败必须阻断 PG-08 的原要求不变。

固定 run_id=`c5412c7c4bed43fba4a721909eea1041`、attempt_id=`a4b479a8d9154994a402f7b13f1a00a6`、route B、55441、原集群/存档/数据库/角色目标不变。原部分集群清单匹配、原存档及实际 attempt 目录不存在；旧失败、授权及未批准材料未变。恢复计划/策略规范 SHA 仍为 `bbc65bf7baa555cf9abad25c86890bbe4dd8d5d9747e244d9f761e1a068147e5` / `5f596e4722bc97cd3d6b7892228f7f28c61c4019fced04eca1bd9773f0dece3b`。绑定验证通过，不是执行授权；未来事实不回填。

本轮证据目录：`E:\AllProject\data-analysis-agent\.cache\p6e1b-os-preflight\final-blockers-20261110T143329Z`，包含启动静态审查、当前上下文/版本、手动材料摘要与离线检查、边界复核及新证据索引。第 1～11 节原文逐字节保留，所有历史失败未改。

| 项目 | 最终结论 |
| --- | --- |
| 原管理员启动唯一根因 | **UNDETERMINED**；日志缺失已证实，启动区间可定位，具体系统失败未证实 |
| TCP 原生契约 | **BLOCKED**；真实 state=0 与两项原断言失败保留 |
| OS 预检 | **BLOCKED**；没有可靠通过条件，不修改安全门 |
| 恢复材料 | **PASS（仅绑定一致）** |
| 新手动只读操作 | **PREPARED / NOT APPROVED / NOT EXECUTED** |
| 正式恢复授权 | **NOT APPROVED** |
| PG-01～PG-08 | **NOT EXECUTED** |
| 是否继续推进当前环境恢复 | **暂时搁置**；不继续追加准备阶段或自动重试 |

恢复推进至少需要系统问题有可信解决证据、两项真实原生断言原样通过、身份关系得到充分核验及全部既有安全门重新通过；此后才谈正式恢复批准。它们当前均不能被手动批准、离线通过或日志文件存在代替。本轮零 UAC 启动、零管理员查询、零数据库/恢复操作及零收费模型/Embedding 调用，已停止。
