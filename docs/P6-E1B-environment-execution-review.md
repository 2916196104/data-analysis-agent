# P6-E1B PostgreSQL 独立测试环境执行前复核

日期：2026-11-10，Asia/Taipei。

**是否允许创建独立测试环境：BLOCKED。** 尚无本轮真实负责人明确批准，五个 DSN 和授权文件进程配置均缺失，端口可用性未确认。已完成静态审查、最小控制逻辑修复及离线测试；没有启动/连接 PostgreSQL、创建集群/角色/数据库、初始化表或写合成数据，没有调用收费模型或 Embedding。

依据：[环境准备方案](P6-E1B-environment-preparation.md)、[PG 集成验收](P6-E1B-postgresql-integration-acceptance.md)、现有准备器、安全门及真实 PG 测试源码。本文不把历史 603 项回归或本轮离线通过数当成真实数据库验收。

## 1. 本轮执行条件

| 条件 | 实际核验 | 结果 |
| --- | --- | --- |
| 独立端口可用 | 候选仍为 55441；只读 Get-NetTCPConnection 调用返回 MethodInvocationException；没有尝试绑定端口、发起连接或改用其他端口 | **未确认 / BLOCKED** |
| 数据目录为空且未被使用 | 固定的本轮 cluster 路径不存在，未发现该路径的现有目录/PID 文件；目录祖先无重解析转向检查通过。没有查询服务器或运行进程的数据库身份 | 当前无已有目录可覆盖；**实际实例身份/占用仍未实测**，创建前须再次检查 |
| PostgreSQL 18.6 二进制 | postgres、psql、initdb、pg_ctl 的 --version 本轮均返回 18.6 | 可用；不是运行中服务器版本证据 |
| 唯一 run_id | `c5412c7c4bed43fba4a721909eea1041` | 原目标保持不变，无自动换 run/目录/端口/库 |
| 原 plan_sha256 | `21fb4b525933d1f90ca75ebbbd1791998287a9bc874a9e335f81443199276a16` | 源码修复后**不再有效**，原文件保留，禁止直接执行 |
| 修订 plan_sha256 | `40a30688281cf0cdd2a73af02bd5c345e2671d9fcc672a84c434947b80a09e2b` | 与本轮最终准备器/入口源码匹配；仅待审阅草稿，不代表获准 |
| 负责人批准 | 原及修订 pending 文件 approved_by 为空、authorized=false，未发现该运行目录的 approved 文件；授权变量未配置 | **未批准 / BLOCKED**，没有代填批准人 |
| 五个连接配置 | CREATOR、TASK_CREATOR、READER、WRITER、TASK 五个 P6_E1B_*_DSN 均未配置 | **不完整 / BLOCKED**；不读取业务 DSN 作为后备 |
| 创建集群 | environment_preparation.authorized/create_cluster 均 false | 未授权 |
| 创建角色 | create_roles=false | 未授权 |
| 创建数据库 | create_databases=false | 未授权 |
| 初始化表结构 | initialize_schema=false | 未授权 |
| 实际测试连接 | test_connections=false | 未授权 |
| 合成写入 | synthetic_writes_authorized=false | 未授权 |
| 新集群控制身份 | expected_system_identifier 为空，未 initdb，未读取新控制文件 | 未确认；不使用假值或其他实例身份 |

固定数据目录仍为项目 `.cache/p6e1b-env/c5412c7c4bed43fba4a721909eea1041/cluster`。修订草稿只存放在独立的**审查产物目录**，不改变这个实例目标；两个库和四个角色名称与原计划完全相同。

修订产物：[计划](../.cache/p6e1b-env-review/c5412c7c4bed43fba4a721909eea1041/plan.json)、[默认未批准的授权模板](../.cache/p6e1b-env-review/c5412c7c4bed43fba4a721909eea1041/authorization.pending.json)、[配置注入脚本](../.cache/p6e1b-env-review/c5412c7c4bed43fba4a721909eea1041/inject.ps1)、[HBA 模板](../.cache/p6e1b-env-review/c5412c7c4bed43fba4a721909eea1041/pg_hba.template.conf)。旧草稿未覆盖。没有生成真实 provision.psql；只有临时单测夹具在内存中渲染过 SQL。

## 2. HBA 与 bootstrap

HBA 的首条规则为 `host all <本轮bootstrap> 127.0.0.1/32 scram-sha-256`，因此管理库 postgres 及两个新数据库的初始化重连均有匹配规则。四类运行角色随后按数据库分别列出，最后拒绝所有其他 IPv4/IPv6；没有 trust、外网放行或客户端可提交的角色列表。

初始化命令显式使用 127.0.0.1、计划端口、postgres 和本轮 bootstrap 用户；psql 的两次换库也显式指定同一主机/端口/用户。角色和数据库名长度均在 PostgreSQL 标识符范围内，且只包含固定 ASCII 前缀、十六进制 run_id 和后缀，不涉及截断或未受控插值。

bootstrap 由获准的新 initdb 创建，具备创建角色和数据库、读取 pg_control_system、SET ROLE 到对象所有者所需权限。它与四类低权限运行角色分离，不进入五个 DSN，不运行 Worker。当前尚未创建它，因此上述是生成规则/代码复核，**不是认证或权限连接成功证据**。

核对参考为本机安装的 PostgreSQL 18 文档：`C:\Program Files\PostgreSQL\18\doc\src\sgml\html\auth-pg-hba-conf.html` 和 `app-psql.html`；前者说明首条匹配规则生效且认证失败不回退，后者说明 --file、ON_ERROR_STOP、退出状态和 \quit 语法。本轮没有互联网请求或连接 PostgreSQL 以读取这些文档。

## 3. 十项静态检查

| 检查 | 复核结论 |
| --- | --- |
| 唯一实例目标 | make_plan 固定仓库内唯一 cluster 路径、loopback、非业务端口、精确 run 数据库/角色；checked_plan 完整比对当前源码摘要。新增重解析/符号链接/非目录祖先拒绝 |
| DATABASE_URL 回退 | draft/check/render 只处理显式计划及授权，target_values 直接构造专用目标；load_authorization 只读取调用者提供的五变量及授权文件，不访问默认业务库 |
| bootstrap/HBA | 同上：管理库与两个重连均匹配；不依赖业务超级用户或本地 trust |
| 四类运行权限 | creator 所有权/CREATE；reader SELECT 无序列 USAGE；writer 对七张入口表及 import_errors INSERT/UPDATE、业务序列 USAGE；task 仅 tasks/worker_state INSERT/UPDATE。无交叉 MEMBER、无全局高权限；均满足既有实时门的静态要求 |
| schema 与任务绑定 | 直接导出现有 BUSINESS_SCHEMA、LABEL_SCHEMA、HUMAN_SCHEMA、TASK_SCHEMA，并用现有 postgres_schema 转换；business/tasks 版本元数据、task_context 唯一业务身份、来源/评论/标注/人工修订/任务/心跳表完整 |
| 摘要/标记一致性 | identity_sha256、role_sha256、规范 JSON 授权摘要沿用旧函数；两个库标记各一行、同 run/purpose/最终批准文件摘要。修订 pending 的摘要不是未来批准摘要；实际批准后必须重新 render |
| 中途失败 | SQL ON_ERROR_STOP、所有 guard 失败产生 SQL 错误；换库失败不选择后备连接；Python 拒绝已有输出文件。CREATE DATABASE 不原子，中断后保留现场、不自动重跑补齐或删除 |
| 覆盖现有实例风险 | 不调用 initdb/pg_ctl/psql；draft 独占目录/文件；新增路径检查拒绝 junction/reparse/symlink/非目录和解析后转向。已有 cluster 在 check/render 阶段可以是普通目录，以支持 initdb 后操作，但这不授权复用；真实创建前仍须检查目标不存在及控制身份 |
| Event 与 idle 超时 | 修复提交等待/线程 join 共用较短截止，并加入读取失败时的写线程取消；详见第 5 节 |
| PG-07 原断言 | 停止注入仍在当前统计返回后；task failed 和 self.calls=[] 严格保留；新增脱敏停止结果事件，失败即停，不再进入 PG-08 |

reader 默认只读 GUC 不是权限屏障，Worker 会话锁连接设为读写模式时仍靠 ACL 拒绝写表；真正读取事务由旧工厂转换成 RR READ ONLY。schema public 属于两个本轮专用数据库，不是在业务库换 schema。当前仍不能凭这张静态表宣称实际 ACL、服务器身份和事务快照通过。

## 4. 已复现并修复的问题

先新增 8 项离线反例：**2 PASS / 6 FAIL**；没有 PG 连接。修复后补充 2 项取消与修订产物测试，共 10 项新增测试，加原 17 项，**27/27 PASS**。

| 编号 | 修改前根因 | 最小修复 |
| --- | --- | --- |
| ENV-R01 | guard 使用 `\quit 3`；本机 18 文档的 \quit 无退出码参数，不能据此保证外层收到失败。正常退出可能被 PowerShell 当作初始化成功 | 两类 guard 的失败分支改为 `SELECT 1/0 AS p6_e1b_guard_rejected;`，由既有 ON_ERROR_STOP 传播脚本错误；按文档应非交互退出 3。真实 psql 退出行为尚未实测 |
| ENV-R02 | R 在提交屏障 wait(10)，又可 join(10)，超过正常 idle 5 秒；原 finally ready.set 还可能在 R 提前失败后启动 W | 提交等待和线程 join 共用单一截止；预算取 min(2 秒, 预检 reader idle 上限的一半)。取消事件先于唤醒事件，未开始的写入不再启动；真实线程离线用例证明该取消顺序 |
| ENV-R03 | SafeResult 默认 failfast=false，PG-07 失败后套件仍可能运行 PG-08 | SafeResult 显式 failfast=true；断言失败和异常均停止后续用例。PG-07 记录 task_status/分类调用数以保存脱敏失败证据 |
| ENV-R04 | 目录仅按字符串固定，未检查 Windows junction/reparse、符号链接或非目录祖先 | make_plan 先执行 check_directory_scope，检查目标祖先及最终解析位置；draft/check/render 均通过它；发现异常失败关闭，不改用别处 |

修改位置：

- [scripts/prepare_p6e1b_environment.py](../scripts/prepare_p6e1b_environment.py)：目录检查、SQL guard、render_injection 的显式计划文件路径（供保持原目标的修订草稿使用）。
- [tests/p6e1b_postgresql_integration.py](../tests/p6e1b_postgresql_integration.py)：concurrent_commit 及两处清理调用、SafeResult、PG-07 脱敏诊断事件。
- 新 [tests/test_p6e1b_execution_review.py](../tests/test_p6e1b_execution_review.py)：10 项离线反例/边界测试。
- 环境准备文档只追加后续复核提示；本报告为新交付。未修改应用、供应商、Planner、分类、费用、前端、冻结 RAG 或人工评审资产。

没有降低角色超时限制、放宽授权、改变统计/来源口径或替换任何 PG 查询结果。测试中的临时批准对象均标为离线单测专用，并在临时目录结束后消失；没有写出真实 approved 授权文件。

## 5. 并发屏障与超时

PG-02/03 仍保留真正 R/W 连接和确定性 Event 顺序：R 完成统计/首维读取 → 发出 ready → W 执行并 commit → 发出 committed → R 继续原快照。没有用 sleep 猜时序。

修复后的时间划分：

- W 最初等待 ready 可达 10 秒，此时尚未进入写入 action、未打开 W 事务；它不是 R 已到屏障后的提交等待。
- R 到屏障后建立单一截止。正常角色 idle=5000ms 时取 2 秒；如果预检记录的 reader idle 更小，则取其一半。committed.wait 和后续 join 共用剩余时间，不各拿一份预算。
- R 退出准备上下文、事务已提交/回滚并关闭后，finally 调用 abort_and_join；先置 cancelled 再唤醒 W，防止“清理却启动写入”。已开始的 action 不能靠 Python 事件可靠取消，兜底 join 最长 10 秒仍未结束则断言失败。

这个边界小于正常角色 5 秒 idle 上限，但**不是整个准备过程硬截止的证明**；客户端调度、SQL/网络、已在执行的写入和关闭仍需真实 PG 验证。不会把线程 join、单语句 timeout 或取消事件宣称为底层数据库请求可靠终止。慢机器或角色漂移导致真实失败应保留证据，不扩大 timeout 以通过测试。

## 6. PG-07 与失败即停

仍保留以下原始逻辑：统计调用返回后设置 Worker stop_requested，再断言任务 failed，并严格要求分类调用列表为空。没有把停止时点移到任务入口，没有只断言任务最终 failed。8 个 PG 用例中原有 **43 条 assert 调用通过 AST 比对全部保留**。

本轮没有修复 Worker 到 P6 协调器的业务停止传递：源码风险仍在，真实 PG-07 未执行。未来若分类数非零，worker_stop_outcome 事件会保存状态和次数，原断言失败，SafeResult 停止剩余用例，不运行 PG-08。该场景必须记录 FAIL 并停止；不得改断言或将失败解释成成功。后续业务修复须保留该证据并单独复验。

错误或中止后仍执行必要连接/线程清理，不把清理理解为继续分析，不自动删库、删数据、改标记或补齐目标。套件只有八项全部实际通过才可 PASS；失败即停后的未执行项不算通过或跳过成功。

## 7. 获准后的阶段边界

当前没有下述授权，因此本轮不执行这些阶段：

| 阶段 | 先核验 | 限定操作与退出条件 |
| --- | --- | --- |
| 修订计划批准 | 同一 run/目标，修订计划摘要与源码匹配；真实负责人、到期时间及每项操作许可 | 负责人填写修订 pending 的获准副本。不能自动复用旧摘要或由脚本填批准人 |
| 创建集群 | 当前授权有效；端口可用；目标不存在；父目录无重解析且权限受控 | 只对精确 cluster 路径执行 initdb；失败保留现场，不复用旧 data |
| 确认控制身份 | 负责人核实新 pg_controldata system identifier，与计划目标匹配 | 填入最终授权，重新 check/render；模板空值不能通过。SQL只输出文件，不代执行 |
| 配置/启动 | 再次核验最终授权、目录/控制身份、HBA及端口 | 只复制本轮 HBA、配置本轮目录、启动该实例；不操作业务服务、不自动换端口 |
| 建角色/数据库/表 | 授权仍有效，管理连接显式指定，SQL guard 通过 | 四运行角色、两库、原 schema/任务上下文/标记/ACL；ON_ERROR_STOP 非零立即停止；跨库初始化非原子，不能自动重置 |
| 实时权限预检 | 五个 DSN、最终标记、身份、角色、ACL/GUC、无外来数据/遗留任务 | 只用被批准实例。任何不符立即停止；不能以静态通过替代实时预检 |
| PG-01～08 | 所有前置条件通过且有合成写入许可 | 固定供应商、真实 PG/Worker/任务库；每项保存脱敏结果，首个失败或异常停止，PG-07 不通过即不进入 PG-08 |

现有 --run 在 setUpClass 执行 verify_live_environment，全部通过后才进入夹具写入。此前不带 --run 的 READY_FOR_LIVE_IDENTITY_CHECK 仅指本地授权校验，不能误作已通过实时权限预检。新审查产物目录只保存文件；以后如采用它，需要将文档中的 p6e1bPlanFile/p6e1bFolder 显式绑定到修订文件，cluster 仍从同一计划字段取得，不能混用旧 plan 或旧 inject。

授权失效或中途失败后停止，资源保留以供审查；不自动 DROP、DELETE、重置、迁移或修复现有资源。只允许负责人根据精确身份另行决定停止/清理本轮集群。

## 8. 离线验证与证据

本轮命令（不运行 PG 套件）：

```powershell
$env:PYTHONPATH='.;src;tests'
.\.venv\Scripts\python.exe -m unittest test_p6e1b_execution_review test_p6e1b_environment_preparation test_p6e1b_safety -v
```

结果 **27/27 PASS，0 failure/error/skip**。原 8 项反例的修改前日志为 6 FAIL/2 PASS；新增 2 项补充测试后得到上述总数。所有测试均封锁 socket/psycopg/子进程启动并断言未调用；并发取消用例只使用 Python 线程及列表回调，不模拟 PG 行数据，不计为真实并发验收。

修订 plan 可以通过当前源码一致性检查；但其未批准模板的 check/render 都退出 2，真实初始化 SQL、集群目录均未生成。生成的 PowerShell 只做解析检查，不执行。原 PG-01～08 的 43 条 assert 全部保留，原单元测试未放宽。全量应用回归未在本轮重跑，应用源码未改。

证据目录 `.cache/p6e1b-env-review/`：before-hashes/源码副本、before-tests、after-tests、review-evidence、最终完整性检查；不包含密码或真实 DSN。旧计划/授权草稿/注入脚本/HBA均保留。缺少真实系统权限、事务快照、PG执行结果和负责人批准时，不产生虚构身份、提交时序或质量评分。

**结论：静态修复与离线复核完成；创建/连接/PG 集成执行仍 BLOCKED。** 负责人授权、端口、实际实例身份及实时 ACL/GUC 通过前，不创建环境。PG-07 业务停止传递、真实模型 tokenizer/费用/取消及主题质量边界仍待后续单独验证。本轮已停止，未启动数据库、模型或生产任务。
