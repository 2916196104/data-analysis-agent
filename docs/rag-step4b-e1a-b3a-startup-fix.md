# AppContainer 启动修复与 RAG 接入缺口

日期：2026-11-05。

**启动阻塞已修复。7项实际OS合成测试通过，包括固定8组合成输入的完整运行器。B3-B的技术启动条件已满足，真实预测仍为0、未获本轮授权。** 本轮只改启动器和测试，Agent链路仅作源码检查。

## 1. 根因与最小修复

不是根据退出码猜测：使用无CRT、无Python依赖、仅调用`KERNEL32!ExitProcess(23)`的合成原生PE，复现同样的`0xC0000142`。对自己创建的合成子进程临时开启loader诊断并观察调试事件，得到：

- 父进程：Session 8，WinSta0 / Default，AppContainer=0，elevated=0；没有提升管理员权限。
- 子进程：OS确认AppContainer=1、Package SID匹配，零网络能力、禁止子进程、单进程Job均启用。
- loader明确记录：`KERNELBASE.dll`的初始化函数在`DLL_PROCESS_ATTACH`失败，早于Python及项目依赖加载。
- 原生调用跟踪：打开`\Device\ConDrv\Server`成功，随后控制台初始化的`NtDeviceIoControlFile`返回`0xC000049D`。本机Windows SDK的`ntstatus.h:10061`将它定义为`STATUS_CHILD_PROCESS_BLOCKED`。
- 仅改变控制台启动方式，保持隔离及其他权限不变：原生GUI程序退出23；原生控制台程序使用`DETACHED_PROCESS`后退出23；Python最小输出和标准库/Pydantic/psycopg导入均退出0。

**原因是`CREATE_NO_WINDOW`路径仍初始化控制台，其console-host创建被禁止子进程策略阻断，导致KERNELBASE初始化失败。** 不需要修改DLL、Window Station/Desktop ACL、系统账户或服务。

实际生产改动只有[src/data_analysis_agent/reviews/e1a_windows_worker.py](../src/data_analysis_agent/reviews/e1a_windows_worker.py)：把`CREATE_NO_WINDOW (0x08000000)`替换为`DETACHED_PROCESS (0x00000008)`，并在回执记录实际creation_flags。标准输入/输出仍使用原显式管道，AppContainer、RX授权、子进程限制、Job、超时及权限探针都保留。

Microsoft的[进程创建标志说明](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags)区分这两种控制台行为；实际因果判断以本机原生调用跟踪和单变量对照为依据。调试标志和断点只作用于本次合成子进程内存，没有设置持久IFEO/gflags、修改注册表配置或系统DLL文件。

## 2. 实际验证

| 检查 | 实际结果 |
|---|---|
| 旧启动方式复现 | 最小原生PE和Python均为0xC0000142；旧回执保留 |
| 令牌/身份及启动 | AppContainer及SID核对成功；新flags=0x0008040C |
| runtime访问 | 读成功，写打开返回Win32错误5 |
| reference及额外私有目录 | 读、写打开均返回错误5；内容未改变 |
| 隔离worker输出 | 成功输出PredictionOutput JSON；与合成R0结果一致，输入/输出无人工参考泄漏字段 |
| 异常、缺输出、非法JSON | 分别记录execution_failed、missing_output、invalid_output，保留原始字节 |
| 实际超时终止 | 0.7秒预算触发终止，实测约0.76秒，退出124；没有延迟输出 |
| 禁止子进程 | 实际尝试被拒绝，Win32错误367；未放松限制 |
| 固定8组运行器 | 8个实际AppContainer、8份raw及8份独立审计通过；含1个空候选列表、2组U/F配对；全部为合成夹具 |

实际OS测试为两次执行的**6项权限/进程测试 + 1项完整合成运行器测试，共7/7通过**。默认回归中的7项OS测试另行执行，未将跳过计为通过。非OS适用回归254项通过（包含16项原专项）；两项既有SQLite/PostgreSQL队列测试仍为2错误，未修业务逻辑、未计通过。

最初新增访问测试用Python`os.open`读`OSError.winerror`，得到None（CRT异常映射），因此该次断言失败。保留日志，改用`CreateFileW/GetLastError`直接核验，最终实际返回5；未调整权限或降低断言。原启动失败及这次测试诊断记录均未覆盖。

复现合成OS测试：

```powershell
$env:E1A_RUN_OS_TEST = '1'
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_e1a_b3_runtime.py -k actual_ -v
Remove-Item Env:E1A_RUN_OS_TEST
```

主控须从正常用户进程启动；预测子进程始终是AppContainer。工具自身处于AppContainer时不能嵌套启动，本轮通过正常执行权限申请在既有非管理员用户上下文执行，没有扩大系统权限。

## 3. 文件、冻结与保护

代码只改2处文件：上述启动器、[tests/test_e1a_b3_runtime.py](../tests/test_e1a_b3_runtime.py)；后者扩展实际OS测试。另新增本报告。R0规则、strict准备接口、B2参考、E0145、人工裁决、Agent/API/数据库代码均未修改。

原始诊断、实际测试和新配置保存在Git忽略目录：

`outputs/rag-retrieval-quality/e1a-b3a-repair-20261105/`

关键证据为`native-original.json`、`python-original.json`、`native-loader.json`、`console-syscalls.json`、`native-detached.json`、`python-detached.json`、`dependencies-detached.json`、`actual-os-suite-final.log`、`actual-synthetic-runner.log`、`regression-confirmed.log`及对应汇总。本轮实际执行的诊断脚本也保留在该目录，无新增生产诊断平台。

新的`prepared-runtime-v3/`绑定本次源码。8份runtime只做原字节复制及摘要核验，没有解析真实评论或人工参考、没有运行预测。旧v1/v2及原失败日志保持不变；旧v2绑定旧源码，后续不要将其作为新代码配置运行。

| 对象 | SHA-256 |
|---|---|
| 新RunConfig规范JSON | 251cf51d0d151749e7554be2154b58e538c2e0fb3223d88a9a4b313e229d725c |
| config.json字节 | ac442d108ad21dacf3a40dfa1693ba128335f7674a48d5d0f7c7b9ba0ac364ac |
| 108份源码清单规范JSON | b2048e0e97a8cf22986280d8d25004f253b3557ad544936f4bf17feb0a7a1e49 |

新配置保留R0原版本、原8项顺序、候选、契约修订、quote_full_review_veto/1、history_unproven、independent_seal=false、一次主尝试和不自动重试。预测授权仍为false，没有生成真实执行permit或执行占用记录。本地摘要不改变历史来源等级，也不是独立封存证明。

保护核验：开始时工作区干净，155份登记文件中153份摘要不变，仅启动器和专项测试发生预期修改；原报告、旧失败日志、旧v2输入保持不变。B2 manifest登记的15份发布文件字节摘要全部匹配，人工参考仅做字节级完整性核验，没有解析或重新裁决。结果见`protection-result.json`；`git diff --check`通过，所有私有材料仍受Git忽略。

## 4. RAG到Agent的实际链路与工程任务

当前在线链路已存在：`POST /tasks` → `jobs.worker`的analysis分支 → `run_review_agent()` → `resolve_review_request()` → `create_review_registry()` → SQL统计/观点/原文工具 → `validate_report()` → 上下文压缩与自然语言答案。另有一次受限SQL补查与有限的本地语义关系解释，不能将系统描述为完全没有证据校验。

**缺口在向量语义检索尚未接入这条链路。** 当前`get_evidence()`按维度/情感过滤后，以`published_at DESC,id DESC`分页；不是对用户问题做向量检索。源码调用搜索确认`search_effective_opinions()`和`LocalBgeM3Embedder.embed_texts()`尚无在线Agent调用点，`ReviewIntent`也未转换为4B的QueryContract。

按功能价值推进以下任务即可，不需要新增人工审批或裁决系统：

| 优先级 | 可独立实施的任务 | 现有代码与最小验收 |
|---|---|---|
| P0 | **独立语义检索服务：问题→查询向量→候选** | 复用`embedding.py:39`及`embedding_store.py:238`；绑定scope/EmbeddingSpace/合法过滤。先以fake encoder、fake repository合成验证顺序、空结果、越界和错误传播；不调用sync或扩大200条索引 |
| P0 | **候选→当前可追溯证据适配** | `OpinionMatch.document`有quote/来源键，却不包含完整review_text及effective_snapshot_sha256。应在同一只读事务中补齐当前候选的上下文和有效版本，复用已有区间/哈希校验；不要从候选自造历史可信来源。`analytics._snapshot()`已支持复用现有事务 |
| P1 | 查询意图→QueryContract | `review_intent.py:24/233`与`query_contract.py:77`尚无适配；补task、对象/主题、事实及否定关系。不能由现有单aspect/sentiment猜出复杂事实；mixed/不确定条件不能硬转SQL过滤 |
| P1 | 将语义检索注册为Agent工具 | 在`review_tools.py:309`、`review_plan.py`接入独立工具并保留统计SQL；让问题文本真正进入语义检索。使用合成Agent工具调用测试验证scope继承、结果引用与预算；不是重写Agent或统计逻辑 |
| P1 | 证据许可接入答案流程 | 适配`review_validation.py`、`review_compression.py`、`review_answers.py`，将可许可事实/精确quote引用送入答案，未知和不足明确返回。当前R0只有15条受控陈述，不能宣称通用语义蕴含；保留基线，不靠改句式提高已见集成绩 |

建议下一项先实现前两个P0，形成可测试、可复用的检索服务和证据出口，再接现有工具注册表。它们可用合成数据独立开发，**不以继续补历史快照或扩展人工审计为前提**。真实数据库/模型联调和B3-B运行仍遵守各自已有授权边界。

本轮结论：技术启动阻塞已解决，B3-B可在用户明确授权后进行；真实语义质量尚未验证。当前停止，未运行真实8组或48组预测，也未实现上述Agent接入任务。
