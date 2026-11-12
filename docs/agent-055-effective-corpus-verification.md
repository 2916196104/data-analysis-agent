# 商品 055：真实有效语料与向量一致性核验

编制日期：2026-11-12（Asia/Taipei）。项目：`E:\AllProject\data-analysis-agent`。

**最新恢复执行结论：第一阶段 BLOCKED / CONNECT_PERMISSION_DENIED。** 本轮只进行了 **1 次最小 Python 连接预检，成功建立会话 0 次，SQL 查询 0 条**；未执行第二阶段。用户已在本机 PowerShell 验证的 pgpass、版本表及 8 张业务表读取权限作为用户提供的新证据保留，不能与当前 Codex 执行环境的连接结果混同。实际有效观点数、200 条候选中的认证可用数 U、真实覆盖率仍为 **UNKNOWN / NOT EXECUTED**。

本文件记录最新恢复预检、前次历史记录和原执行方案。第 4～7 节中的未执行步骤不是运行证据。本轮更新报告、保存新脱敏记录，并提供可在用户本机 PowerShell 运行的只读脚本；不修改生产代码、数据库、权限、Gold Reference、Oracle 或 P6-E1B 验收状态。

## 0. 最新恢复执行：第一阶段 Python 连接预检

本轮执行时间：**2026-11-12 01:33:15（Asia/Taipei）**，对应 UTC `2026-11-11T17:33:15.753620+00:00`。子执行器记录耗时 `0.016` 秒，不含前置准备及解释器启动；120 秒外层总时限没有触发。

新脱敏记录：[preflight-20261111T173315753620Z.json](evaluation/agent-055-effective-corpus/preflight-20261111T173315753620Z.json)。本轮没有覆盖前次记录。

| 项目 | 本轮证据与结果 |
|---|---|
| 本机环境变化 | **USER_VERIFIED**：用户确认 `psql -w` 可登录，Windows 标准 pgpass.conf 已配置，3 张版本表及 8 张业务表均可 SELECT，200 条向量基础关联检查完成 |
| 本轮源码复核 | **10/10 SHA-256 匹配**；18 个来源比较字段与生产 AST 一致 |
| 凭据使用方式 | 交由现有 libpq 密码文件机制读取；执行器不打开、解析、复制、回显或保存密码，不改写凭据文件 |
| 登录目标 | 只请求用户授权的固定本机地址、reviews、rag_audit_ro；未改账号、地址或端口 |
| 最小连接尝试 | **1 次**，发生在本轮明确允许的第一阶段；不是前次失败后的自动重试 |
| 实际认证完成的会话 | **0 次** |
| 实际 SQL 查询 | **0 条**；`statements` 为空，连身份 SELECT 都未执行 |
| 失败阶段 | `connect_fixed_audit_identity`（CONNECT） |
| 安全错误分类 | `CONNECT_PERMISSION_DENIED` |
| 异常链 | `data_analysis_agent.core.database.DatabaseError` → `psycopg.OperationalError` |
| SQLSTATE | **null**：两层驱动异常均未暴露 SQLSTATE；不是有效 SQLSTATE 字符串，也未猜测补填 |
| current_user / session_user / current_database | **NOT EXECUTED**；请求的身份不能当作服务器已验证身份 |
| RR READ ONLY、statement_timeout=15s、lock_timeout=3s、UTC | **NOT APPLIED / NOT VERIFIED**；会话未建立，不能记 PASS |
| 第二阶段执行 | **NOT EXECUTED**；连接成功这一前置条件未满足，也未自动使用历史授权进入业务核验 |
| ROLLBACK / close | **不适用**：连接构造失败，没有返回可用连接对象，也没有事务；不伪称已回滚 |
| 后续尝试 / 环境绕行 | **0 次**；没有更换账号、扩大范围或尝试突破执行环境限制 |

本轮没有获取数据，故不能判定任何具体观点、向量、快照或导入身份是否异常，也不能把“无法解释的来源异常数”填成 0。完整当前有效观点数、模型/人工观点数、A、key 成员数、元数据/快照匹配数、U、过期分类、未覆盖数及真实覆盖率均保持 UNKNOWN。

**最小阻断仍是当前执行环境中的 CONNECT 权限拒绝。** 本轮没有读取表权限，因此不能据此否定用户在本机 PowerShell 验证的 11 个对象读取权限，不能认定需要 GRANT，也不能认定 pgpass 密码错误。异常原文未输出或保存；SQLSTATE 缺失使进一步服务器端归因无法完成。本轮不进行额外 OS/端口/账户审计或连接探测。

**真实语义检索准入仍为 UNDETERMINED / NOT READY。** 数据来源认证未完成；本次没有 Embedding、LLM、相似度检索或 Agent 执行，也不解除 P6-E1B 阻断。

### 0.1 本机 PowerShell 只读脚本

已提供：[readonly_audit.py](evaluation/agent-055-effective-corpus/readonly_audit.py)。它使用当前仓库生产函数和既定范围，凭据由运行它的本机用户的 libpq 密码文件机制提供；脚本内不填密码或连接串。

在用户自己的 PowerShell 中先运行第一阶段：

```powershell
Set-Location -LiteralPath 'E:\AllProject\data-analysis-agent'
& '.\.venv\Scripts\python.exe' '.\docs\evaluation\agent-055-effective-corpus\readonly_audit.py' --preflight
```

默认或 `--preflight` 只做一次连接、事务及身份设置检查。成功时输出 `PREFLIGHT_PASS`，随后 ROLLBACK、关闭连接并停止，**不会自动查询 11 张表或进入有效语料核验**。失败时保存脱敏 SQLSTATE、异常类型、失败阶段和实际执行数，不重试。

只有本机预检通过并另行取得第二阶段明确执行授权后，才可运行以下完整核验命令；**本轮未运行该命令**：

```powershell
& '.\.venv\Scripts\python.exe' '.\docs\evaluation\agent-055-effective-corpus\readonly_audit.py' --effective-corpus-authorized
```

完整模式在自己的唯一连接中重新预检，在同一个 REPEATABLE READ READ ONLY 事务中确认 11 个对象、绑定实际模型三元组，复用 `read_effective_corpus()`、`_eligible()`、`_verified_sources()` 及生产持久化来源字段进行认证，输出本报告规定的计数、覆盖和分类。每个命令各自只尝试一次连接；先前预检的快照不跨命令复用。全部成功才使用 `COMPLETED` 结果中的 U；`BLOCKED` 记录里的任何已观察局部计数不能当作整次认证通过。

两种模式均固定商品 055、profile 和向量空间，设置语句超时 15 秒、锁超时 3 秒及 UTC，外层进程监督总时限 120 秒，无自动重连或重试。常规成功/失败路径均 ROLLBACK/close；如果外层硬超时终止子进程，监督记录会明确标注显式清理无法认证，不能记为已回滚成功。

脱敏 JSON 保存在 `docs/evaluation/agent-055-effective-corpus/`，命令输出 `record_path`。本机运行生成的新记录才是该次实际结果；不会自动把当前报告的 UNKNOWN 改成历史数字。脚本不输出正文、quote、密码、endpoint、完整向量或连接串。

离线验证已完成：源码摘要及生产字段检查、第一阶段成功后阻断第二阶段的合成测试、成功时回滚关闭的合成测试、失败时 SQLSTATE/异常链保留和敏感异常文本抑制的合成测试。**这些测试没有真实数据库连接，也未保存合成执行记录；完整第二阶段数据库路径仍未执行验证。**

## 0.2 历史：前次单次核验（非本轮结果）

以下保留前次历史观察；当时未发现密码文件的结论不能沿用到用户已确认环境变化的本轮。

执行时间：2026-11-12 01:00:29（Asia/Taipei）；对应 UTC `2026-11-11T17:00:29.585883+00:00`。执行器记录耗时 `0.015` 秒，不含前置准备及解释器启动；外层 120 秒总时限未触发。

脱敏执行记录：[20261111T170029585883Z.json](evaluation/agent-055-effective-corpus/20261111T170029585883Z.json)。记录包含实际尝试次数、失败阶段、零查询数、未知统计、源码摘要、执行器摘要及清理适用性，不含密码、连接串、正文、quote、完整向量或标注 endpoint。

| 执行检查 | 实际结果 |
|---|---|
| 单次执行授权 | 已收到；本次仅用于指定商品、范围、profile、space 和只读核验 |
| 生产源码复核 | 10/10 SHA-256 与原方案一致；18 个来源比较字段与生产 AST 一致 |
| 审计依赖检查 | 已进行无连接的导入检查；未加载 torch、transformers、openai 等模型运行时 |
| 安全本地凭据发现 | 未发现审计角色专用连接配置、专用密码环境变量或标准 libpq 密码文件；没有复用应用读写账号凭据 |
| 固定目标连接尝试 | 1 次；目标仅为用户授权的本机地址、reviews、rag_audit_ro；未更换目标或登录名 |
| 实际建立数据库会话 | **0 次**；没有取得服务器认证后的只读连接 |
| 实际执行查询 | **0 条**；未执行表权限、版本、profile、语料或向量查询 |
| 11 张必需表/版本对象存在性与 SELECT 权限 | **NOT EXECUTED**；不能宣称全部存在或权限充足，也不能推断具体表缺权 |
| REPEATABLE READ READ ONLY、15 秒语句超时、3 秒锁超时、UTC | 方案已准备，**未应用、未核验**，因为尚未建立会话 |
| profile 唯一绑定 | **NOT EXECUTED**；未猜测三元组 |
| read_effective_corpus / 独立来源认证 | **NOT EXECUTED** |
| 未失效向量实读 / key、元数据、快照认证 | **NOT EXECUTED** |
| ROLLBACK / close | **不适用**：连接构造失败，没有返回可用连接对象，也没有事务；不记录为已执行回滚 |
| 自动重试、账号回退、扩大范围 | 均未发生 |
| 数据修改、Embedding、LLM、相似度检索、Agent、Worker | 均为 0 |

**本次真实最小阻断是连接阶段权限拒绝，不是缺少授权，也不是已经查明的表 SELECT 权限不足。** 执行器通用安全错误码为 `REQUIRED_READ_PERMISSION_DENIED`，发生阶段为 `connect_fixed_audit_identity`；应按“数据库会话建立前的权限拒绝”解释。底层异常原文未输出或保存，SQLSTATE 未留存，因此不能据此进一步认定为密码错误、某张表缺权、PostgreSQL 角色错误，或特定 Windows/沙箱故障。本轮按要求停止，没有补做 OS/端口/账户审计或第二次连接来定位。

前次没有发现本地安全凭据属于当时的准备观察，不能冒称服务端“密码认证失败”的实测结果。本轮用户已确认本机 pgpass 配置成功；当前阻断以最新第一阶段记录为准，不再把历史凭据缺失当作已证实的当前根因。

### 历史统计状态：前次没有数据库实测值

| 统计项 | 本次实际结果 |
|---|---|
| 当前成功标注评论数 | UNKNOWN / NOT EXECUTED |
| 当前完整有效观点数 | UNKNOWN / NOT EXECUTED |
| 当前模型观点数 | UNKNOWN / NOT EXECUTED |
| 当前人工观点数 | UNKNOWN / NOT EXECUTED |
| 当前未失效向量数 A | UNKNOWN / NOT EXECUTED；200 仅为用户已确认基线 |
| 属于当前有效观点的向量数 | UNKNOWN / NOT EXECUTED |
| 来源元数据完全匹配向量数 | UNKNOWN / NOT EXECUTED |
| 快照摘要完全匹配向量数 | UNKNOWN / NOT EXECUTED |
| 最终认证可用向量数 U | **UNKNOWN / NOT EXECUTED；不能推定 U=200，也不能推定 U=0** |
| 当前未建立可用向量观点数 | UNKNOWN / NOT EXECUTED |
| 实际有效观点覆盖率 | **UNKNOWN / NOT EXECUTED；没有当前分母或分子** |
| 被更新标注替代向量数 | UNKNOWN / NOT EXECUTED |
| 被人工修订替代向量数 | UNKNOWN / NOT EXECUTED |
| 无法解释的来源异常数 | UNKNOWN / NOT EXECUTED，不能记录为 0 |

**具体数据一致性异常：本次未读取数据，无法判定是否存在，更无法列出异常数据行。** 用户先前提供的基础关联异常为 0，不构成本次当前有效集合或来源快照的认证结果。

**受控真实语义检索条件：尚不能认定具备（NOT READY / 数据准入未完成）。** 完整有效观点和 U 尚未认证；本地查询编码运行条件及真实检索质量本次也没有验证。本次授权明确不包含真实检索，即使以后数据核验通过，也不能据此自动发起检索或解除 P6-E1B 阻断。

## 1. 当前可报告的统计与证据边界

以下基础结果由用户提供，本轮连接失败、查询数为 0，没有重新查询或独立复核：

| 基础项目 | 用户已确认结果 | 能证明的范围 |
|---|---:|---|
| 指定范围评论 | 510 | 基础评论总体 |
| 有正文评论 | 510 | 基础正文覆盖 |
| 未标记失效向量 | 200 | `invalidated_at IS NULL`，不等于当前有效 |
| 向量覆盖评论 | 105 | 基础向量行关联的不同评论 |
| 200 条向量的观点来源 | 全部为模型观点 | 不代表当前有效语料没有人工观点 |
| 评论关联缺失 | 0 | 基础外键/关联 |
| 标注关联缺失 | 0 | 基础外键/关联 |
| 评论与标注摘要异常 | 0 | 关联行的内容摘要比较 |
| 模型观点关联缺失 | 0 | 基础外键/关联 |
| 引用位置异常 | 0 | 已检查向量对应来源的引用范围 |
| 引用原文切片异常 | 0 | 已检查引用与关联正文切片 |
| 对应标注人工修订记录 | 0 | 仅限这批向量对应的标注，不涵盖所有当前标注 |

由用户数值可计算基础评论覆盖率 `105 / 510 = 20.59%`；它不是当前有效观点的向量覆盖率。200 条关联全部正常，也不能证明其中任何一条已完成本次生产等价认证。

| 本次核心指标 | 当前结果 | 获准后的统计依据 |
|---|---|---|
| 当前成功标注评论数 | UNKNOWN / NOT EXECUTED | 当前生产语料状态中 `processing_status == 'success'` 的不同评论数 |
| 当前有效观点数 | UNKNOWN / NOT EXECUTED | `len(corpus.documents)` |
| 当前模型观点数 | UNKNOWN / NOT EXECUTED | 当前文档中 `opinion_source == 'model'` |
| 当前人工观点数 | UNKNOWN / NOT EXECUTED | 当前文档中 `opinion_source == 'human'` |
| 本次同一快照内未失效向量数 | UNKNOWN / NOT EXECUTED | 固定范围、profile、空间的向量实读；200 仅为用户提供的基线 |
| 当前有效观点 key 成员匹配向量数 | UNKNOWN / NOT EXECUTED | 向量 key 属于当次完整有效观点集合 |
| 当前有效观点全部持久化元数据精确匹配向量数 | UNKNOWN / NOT EXECUTED | key 匹配并通过生产检索所用来源字段比较 |
| 被较新成功标注替代的向量数 | UNKNOWN / NOT EXECUTED | 有来源证据的旧标注与生产选中标注比较 |
| 被最新人工修订替代的向量数 | UNKNOWN / NOT EXECUTED | 生产选中标注的最新人工完整快照比较，包括删空 |
| key、quote/位置、正文关联或输入摘要异常向量数 | UNKNOWN / NOT EXECUTED | 分项诊断，禁止全部归为“旧版本” |
| 当前评论快照摘要不匹配向量数 | UNKNOWN / NOT EXECUTED | 对比重新生成的 `effective_snapshot_sha256` |
| 通过全部生产等价成员、快照和来源校验的向量数 | UNKNOWN / NOT EXECUTED | 第 6 节定义的集合 U |
| 当前有效语料中未有可用向量的观点数 | UNKNOWN / NOT EXECUTED | 当前观点 key 集合减去 U 覆盖的不同 key |
| 实际有效观点覆盖率 | UNKNOWN / NOT EXECUTED | U 覆盖的不同有效观点数 / 当前有效观点数 |

不继承历史 1091 条观点、505 条成功标注或 891 条缺失向量作为当前统计。即使本次恰好得到相同数字，也必须来自获准后的新快照。

## 2. 单次执行的固定范围

| 参数 | 固定值 |
|---|---|
| 数据库 | `reviews`；PostgreSQL 18.6 为用户已确认事实 |
| 登录身份 | 仅 `rag_audit_ro`；禁止使用 `postgres` 或 `review_agent` |
| source | `reviews_csv` |
| dataset_version | `reviews-csv` |
| product_ids | 仅 `["100013588055"]` |
| UTC 开始边界 | `2021-09-11T00:00:00.000+00:00`，包含 |
| UTC 结束边界 | `2022-08-27T00:00:00.000+00:00`，不包含；包含 2022-08-26 全日 |
| as_of | `None`；读取事务快照中的当前版本，不构造历史修订时点 |
| label_profile_key | `1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304` |
| embedding_space_id | `embedding-space-v1:11e6754662dbd13fc56c6e624bc9416c1c2766f962787267fd4a57b1fa8d622e` |
| 文本构造版本 | 生产常量 `quote-utf8-v1` |

profile 哈希不能反推出 `(model, base_url, prompt_version)`。执行时必须取得实际三元组，并使用生产 `model_identity()` / `analysis_scope()` 的规范化及 `content_sha256(list(identity))` 核对上述哈希。优先使用现有已核实的私有配置；若需要从数据库识别，只读取此商品和日期内关联标注的三元组，在内存计算哈希、去重，唯一匹配后才读取语料。没有匹配或匹配不唯一即停止，不从其他商品、历史报告或默认模型猜测。

审计凭据只从现有安全来源传入内存变量；不把密码、连接串或 `base_url` 写入命令行、日志、报告或异常输出，不回退到应用默认读写连接。

## 3. 已定位的生产规则与复用入口

本节为静态源码检查结论，不是数据库检查 PASS。

| 生产位置 | 已确认行为 | 本次用法 |
|---|---|---|
| [reviews/scope.py](../src/data_analysis_agent/reviews/scope.py)，`review_scope()`、`analysis_scope()` | 规范化范围与标注三元组；日期采用 UTC 次日开区间 | 原样生成范围参数，不改变统计口径 |
| 同文件 `current_reviews_sql()`，约第 116 行 | 标注必须匹配当前评论 `content_sha256` 及完整三元组；按 `(status='success') DESC, id DESC` 选择 | 唯一的当前标注/有效观点选择规则 |
| 同函数人工分支 | 对已选标注取 `MAX(review_human_reviews.id)`；有人工修订时完整替换模型观点 | 人工确认也使用人工观点 ID；人工删空绝不回退模型 |
| [reviews/rag_corpus.py](../src/data_analysis_agent/reviews/rag_corpus.py)，`read_effective_corpus()`，第 141 行 | 复用上述 CTE；返回范围内评论状态及完整有效观点；任何无效观点使整次读取失败 | 直接传已有只读连接，禁止另写有效观点选择器 |
| 同文件 `_document()`，第 83 行 | 检查来源类型、ID、成功状态、维度/情感、非空引用及 Python 字符切片；生成输入摘要和版本 key | 直接复用，不 trim、不更换 Unicode/位置口径 |
| 同文件 `_review_state()`，第 122 行 | 用评论状态和该评论全部有效观点 key 生成快照摘要；零观点状态也有摘要 | 保留成功无观点、人工删空、失败和待标注等状态 |
| [reviews/analytics.py](../src/data_analysis_agent/reviews/analytics.py)，`_snapshot()` | 对已有 `PostgresConnection` 检查标注结构及已有事务，既不提交也不新开连接 | 保证 `read_effective_corpus(connection, ...)` 复用外层事务 |
| [reviews/embedding_store.py](../src/data_analysis_agent/reviews/embedding_store.py)，`_eligible()`，第 286 行 | 从生产语料生成 `(key, effective_snapshot_sha256)` 成员集合 | 原样生成当前可匹配集合 |
| 同文件 `_search_effective_opinions()`，第 303 行 | 先匹配 key 和快照，再限定范围、空间、构造版本、profile、未失效；最后比较全部持久化来源字段 | 只复用其字段和准入条件，不调用搜索函数及距离运算 |
| [reviews/semantic_retrieval.py](../src/data_analysis_agent/reviews/semantic_retrieval.py)，`PostgresSemanticRepository.snapshot()`，第 421 行 | 接受已有 PostgreSQL 连接并验证只读稳定事务；共享连接由调用方负责结束 | 用现有连接进入 repository，不传 DSN 让它新开连接 |
| 同文件 `_PostgresSnapshot.corpus()` / `.load_sources()`，第 368 / 392 行 | 复用有效语料；独立从评论和导入表装载正文/导入身份及每条评论完整有效观点 | 在同一快照中重建来源，不能从向量行反造来源 |
| 同文件 `_verified_sources()`，第 220 行 | 核对 scope、profile、修订、来源 ID、导入摘要格式/身份、quote/输入摘要/key、完整观点集合及重算快照 | 对当次完整有效观点来源调用此纯校验，不构造搜索结果 |
| [agents/review_retrieval.py](../src/data_analysis_agent/agents/review_retrieval.py)，`validate_retrieval()`，第 171 行 | 对传输证据检查范围、原文摘要、quote、key、快照字段及事实许可；其注释说明不负责历史来源认证 | 仅审查职责边界；本次没有问题/Top-K，不调用 Agent 或伪造 `SemanticResult` |

**几个影响统计的细节：**

1. 最新失败尝试不会覆盖已有成功标注。不能用 `MAX(annotation_id)` 或 `status='success'` 的简单 JOIN 替代生产选择。
2. 人工修订只针对生产选中的标注。旧标注没有人工修订，不代表新选中标注没有人工修订。
3. “模型观点”只是来源类型。人工确认、纠正、删空、较新成功标注和正文版本变化，都可能使旧模型向量不再属于当前集合。
4. 某个观点 key 保持不变时，同一评论的另一个观点或状态变化仍可能使整条评论的快照摘要改变。key 匹配与快照匹配必须分开统计。
5. `read_effective_corpus()` 用 `JOIN import_runs` 读取来源；需要额外比较范围内原始评论 ID 集合与 `corpus.reviews`，防止缺失导入关联造成静默少行。

### 3.1 哈希含义

- `label_profile_key`：完整规范化标注三元组的项目规范 JSON 哈希。
- `opinion_text_sha256`、`embedding_input_sha256`：原样 quote 的 UTF-8 字节 SHA-256；首版输入就是 quote。
- `stable_versioned_opinion_key`：`rag-opinion-v1:` 加生产规范 JSON 哈希；绑定来源/数据集/商品/评论、评论内容版本、profile、标注与人工修订 ID、模型/人工观点 ID、维度、情感、区间及 quote 摘要。不能只比较观点 ID。
- `effective_snapshot_sha256`：绑定 `rag-effective-opinions-v1`、profile、完整评论状态及该评论全部排序后的有效观点 key；不能只对已索引或当前命中的子集计算。
- `review_content_sha256` 是导入规范记录的哈希，见 [store.py](../src/data_analysis_agent/reviews/store.py) `_store_review()` 第 159 行，**不是** `SHA256(review_text)`。全文 UTF-8 哈希单独计算，不能拿它与 `review_content_sha256` 直接比较。
- 向量表不保存 quote 正文、完整评论全文哈希、`file_sha256` 或 `opinion_text_sha256` 列；这些通过独立来源读取和生产文档重建校验，不能编造不存在的列。

## 4. 获准后的只读预检方案

### 4.1 连接和事务

只建立一次 `PostgresConnection(audit_target, readonly=True)`。连接前关闭 SQL 调试输出；不通过 CLI 启动服务，不使用会先在事务外检查结构的 `connect_review_database()` 高层入口。底层连接只做连接设置；所有数据库身份读取、表/函数权限预检、结构检查、业务读取和复核均在下面这一笔事务内完成：

```sql
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '3s';
SET LOCAL TIME ZONE 'UTC';

SELECT current_database() AS database_name,
       current_user AS effective_user,
       session_user AS login_user,
       current_setting('transaction_isolation') AS isolation_level,
       current_setting('transaction_read_only') AS read_only,
       current_setting('statement_timeout') AS statement_timeout,
       current_setting('lock_timeout') AS lock_timeout,
       current_setting('TimeZone') AS timezone,
       txid_current_snapshot() AS transaction_snapshot,
       transaction_timestamp() AS observed_at;
```

要求数据库为 `reviews`，`current_user` 与 `session_user` 都为 `rag_audit_ro`，隔离级别为 `repeatable read`，只读为 `on`，UTC 与两个超时设置实际生效。失败立即结束。不做角色切换、写入试探、Windows/端口检查或重复账户安全审计。

计划连接超时 10 秒、单条查询超时 15 秒、锁等待 3 秒，外层任务总截止 120 秒；外层取消/断开由执行器负责，不能把单条超时误称为总截止。仅一次尝试，无自动重连、重试或并发连接。正常和异常路径均 `ROLLBACK`，然后 `close()`；不使用成功退出时自动 `COMMIT` 的连接上下文管理方式。

### 4.2 所需 SELECT 权限和对象

| public 对象 | 必要性 | 缺失/不可读的处理 |
|---|---|---|
| `review_database_schema` | `check_review_schema()` 的业务库版本检查 | 阻断；不能跳过检查 |
| `review_labeling_schema` | `_snapshot()` → `check_labeling_schema()` 必读 | 阻断；即使业务表能 SELECT 也不能省略 |
| `products` | 商品所属数据集验证及生产 CTE | 阻断 |
| `reviews` | 当前正文、范围、内容版本及独立来源 | 阻断 |
| `import_runs` | 导入文件指纹、来源关联 | 阻断 |
| `review_annotations` | 当前标注及 profile 验证 | 阻断 |
| `review_opinions` | 模型观点来源 | 阻断 |
| `review_workbench_schema` | 人工表存在时的工作台版本检查 | 参与人工结构三表完整性检查 |
| `review_human_reviews` | 最新人工修订选择 | 同上；存在但无 SELECT 不能当不存在 |
| `review_human_opinions` | 人工完整观点快照 | 同上 |
| `review_opinion_embeddings` | 候选向量及其持久化元数据 | 阻断 |

生产规则允许人工结构三表全部不存在，三表部分存在则失败；如全部不存在，只能明确记录生产模型分支被使用，不能将拒绝访问伪装成“人工表不存在”。不申请或执行 GRANT，不迁移或初始化。

预检 SQL 模板（在上述事务中执行，尚未执行）：

```sql
WITH required(name) AS (VALUES
  ('review_database_schema'), ('review_labeling_schema'),
  ('products'), ('reviews'), ('import_runs'),
  ('review_annotations'), ('review_opinions'),
  ('review_workbench_schema'), ('review_human_reviews'),
  ('review_human_opinions'), ('review_opinion_embeddings')
)
SELECT required.name, c.oid IS NOT NULL AS object_exists,
       c.relkind, c.relrowsecurity, c.relforcerowsecurity,
       CASE WHEN c.oid IS NOT NULL
            THEN has_table_privilege(current_user, c.oid, 'SELECT') END AS can_select
FROM required
LEFT JOIN pg_catalog.pg_namespace n ON n.nspname = 'public'
LEFT JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid
                             AND c.relname = required.name
ORDER BY required.name;

SELECT has_schema_privilege(current_user, 'public', 'USAGE') AS can_use_schema;
```

发现 RLS、视图或其他可能隐藏行/执行额外逻辑的对象时，不能把可见子集称为全量；若不能在现有批准范围内证明读取完整及无额外副作用，则停止并报告具体对象，不关闭 RLS、不换账号。使用已有 `check_review_schema(connection)` 和语料路径的版本检查验证版本，不调用名称为 `init_*` 或迁移的函数。

函数依赖限于生产读取路径：目录/权限函数、窗口/聚合、`txid_current_snapshot()`、`transaction_timestamp()`、现有查询的日期转换，以及读取已有向量时的 pgvector 输出转换（`embedding::text`）。核对实际所需函数/类型是否可用；读权限不足或函数执行被拒绝时记录安全错误类别并回滚，不执行自定义管理函数探测，也不为测试权限调用 `<=>`、Embedding 或模型。

`rag_embedding_sync_runs`、`import_errors`、任务队列及其他业务表不属于本次必读范围。它们不能读取本身不阻断上述路径；不得为了补齐无关权限扩大角色能力。

### 4.3 范围及隐式行为检查

静态确认所选路径只有 SELECT、事务/会话设置及本地纯函数；不包含初始化、迁移、同步、DML、模型构造或网络客户端调用。禁止调用 `sync_effective_opinions()`、`migrate_embedding_store()`、`SemanticRetrievalService.prepare()/search()`、`snapshot.search()`、`create_semantic_service()` 或完整 Agent。也不以 dry-run 同步命令代替核验。

业务返回行必须限于第 2 节。人工版本必须属于此范围内选中标注；诊断旧来源只沿本次候选向量关联 ID 读取，并验证父评论仍在批准范围。发现父来源越界只计为异常并停止进一步沿该关联装载，不读取其他商品正文。

生产 `current_reviews_sql()` 的 `attempts` CTE 在内部按 identity 处理标注，商品/日期过滤由外层 `WHERE` 实施；`table_names()` 也读取 public 表名目录。**本方案承诺生产限定结果，不承诺 PostgreSQL 物理扫描只接触 055 的数据页。** 若批准范围连生产 CTE 的内部表级读取/目录元数据也不允许，则现有接口不足，本次停止，不通过重写选择器、创建视图、变更 RLS 或扩大范围绕过。

## 5. 单次生产等价核验步骤

全部步骤使用第 4 节同一个连接、事务和快照，不分页跨连接，不启动任何后台任务。

1. **完成预检及 profile 绑定。** 仅在权限、对象/版本、只读事务和固定三元组检查通过后继续。记录安全的快照标识、观测时刻、执行源码摘要与范围，不记录凭据、endpoint、正文或原始向量。
2. **读取独立范围基线。** 用固定商品、source、dataset 和 UTC 时间边界取得评论 ID/正文状态基线；使用生产 `review_scope()` 比较过滤结果，确保生产文本日期比较与实际 UTC 范围一致。只在内存处理正文。范围内评论 ID 必须与随后 `corpus.reviews` 完全一致，差异不能记为 0 或静默忽略。与用户 510/200 基线有差异时注明快照变化，不强行改成历史数字。
3. **读取当前完整有效语料。** 用 `PostgresSemanticRepository(connection).snapshot(scope)` 复用外层事务，调用 `snapshot.corpus()`；内部直接调用 `read_effective_corpus(connection, **scope.options())`。统计全部状态、成功评论、成功零观点评论、有效模型/人工观点及当前人工删空评论；不得只读有向量的 105 条评论。
4. **认证独立来源。** 获取全部有效观点 key；用 `snapshot.load_sources(keys)` 装载这些评论的正文、导入身份及每条评论完整观点集合，再调用生产 `_verified_sources(sources, keys, scope)`。校验失败整次不能宣称有效语料认证完成。无观点的评论状态仍保留在 corpus 中；空 key 集合不假造来源。
5. **读取候选向量 A。** SELECT 固定 source/dataset/商品/日期/profile/space 且 `invalidated_at IS NULL` 的行；第一层不预先丢弃错误 `text_builder_version`，将其作为诊断项。读取实际来源字段、key、快照摘要和已有 `embedding::text`，不用距离运算或查询向量。然后明确筛出生产要求的 `quote-utf8-v1` 子集。实际数量如不等于 200，报告当次实数及差异，不 `LIMIT 200` 掩盖。
6. **核对成员、元数据与快照。** 用 `_eligible(corpus)` 得到生产 key/快照对，并建立唯一 key → 当前文档、review_id → 当前状态映射。逐条执行第 6 节全部比较。相同 quote 或相同旧观点 ID 均不能单独认证；不计算余弦距离，不伪造 distance、Top-K、来源认证令牌或检索结果。
7. **解释不匹配原因。** 对非当前成员，仅沿 A 中已有来源 ID 做有界、只读诊断，比较原关联标注/观点/人工修订及生产已选状态。可用 `_document()` 重建被引用来源的 key，不能新增当前版本选择算法。只有旧来源自洽且确实被生产选中的较新版本替代，才记为 superseded；无法证明者记为 `nonmember_unexplained` 或具体来源异常。正文变化时不假装拥有历史全文来验证旧切片。
8. **统计覆盖并对账。** 按第 7 节输出当前实数、分项异常和未覆盖分布；重复 key、来源缺行或集合对账失败均必须显式报告。
9. **复核事务并结束。** 再读同一快照标识/时间、隔离级别及只读设置，确认未更换快照；`ROLLBACK`、关闭连接，再写入本地脱敏报告。出现错误、越界、超时或权限缺口即回滚关闭，无自动修复或重跑。

关键复用关系如下，仅为待执行骨架，不是本轮已运行脚本：

```python
# audit_target 和完整 options 由已获准的私有输入提供，禁止打印。
# 外层已完成 BEGIN RR READ ONLY、SET LOCAL、身份/权限/版本预检。
scope = _scope(options)  # 生产 RetrievalScope/analysis_scope 规范化
with PostgresSemanticRepository(connection).snapshot(scope) as snapshot:
    corpus = snapshot.corpus()  # 原生产 read_effective_corpus，同一连接
    keys = tuple(d.stable_versioned_opinion_key for d in corpus.documents)
    sources = snapshot.load_sources(keys) if keys else ()
    verified = _verified_sources(sources, keys, scope)
    eligible = _eligible(corpus)
    # 后续仅 SELECT 固定范围向量，并逐项比较原生产准入字段。
    # 此处不调用 snapshot.search / service.search / 编码器。
# connection 由外层 finally rollback/close，不能在这里提交。
```

## 6. 当前向量的认证口径

令 E 为 `corpus.documents` 的不同有效 key 集合，A 为本次范围内未失效候选向量行。U 必须同时满足：

1. 向量范围、profile、space 及 `text_builder_version` 与固定生产配置一致。
2. `stable_versioned_opinion_key ∈ E`，且 E 无重复 key。
3. `effective_snapshot_sha256` 等于该评论在此次完整有效语料中的摘要。
4. 与当前文档逐字段比较通过，字段清单严格取自 `_search_effective_opinions()` 的持久化来源校验：

```text
source, dataset_version, product_id, review_id, annotation_id,
label_profile_key, opinion_source, model_opinion_id, human_opinion_id,
human_revision_id, aspect, sentiment, quote_start, quote_end,
review_content_sha256, embedding_input_sha256, text_builder_version,
published_at
```

5. 相应来源已经第 5 节独立重建，并通过 `_verified_sources()` 的原文、quote、哈希、ID、修订及完整快照检查；向量行本身不能签发此来源证明。

**U 的大小是本次“通过生产等价有效成员和来源快照核验，可以作为当前检索来源候选的向量数”。** 不使用基础 JOIN 数或仅 `invalidated_at IS NULL` 数代替。这里不构造 `SemanticResult`，不签发 `require_source_validated_result()` 所需的实际检索结果绑定。

附加数值完整性检查可对已读取的持久化向量用生产 `validate_vector()` 验证 1024 维、有限值及归一化；仅是纯数值运算，无 Embedding、无相似度检索。必须单列检查数、通过数、失败数：该函数的 `1e-5` 范数容差属于生产编码/缓存验证路径，不能伪称检索 SQL 已对每条存储向量执行了同一检查。发现异常时报告准入风险，不自动删除、修复向量或改变 E。

必须分别列出：key 成员数、全部字段匹配数、key+快照匹配数、全部来源校验数 U、数值完整性结果。某一向量可以同时有多项错误，分项异常计数不要求相加等于 A。另设互斥最终处置分类用于对账，按以下次序归类：

1. 非当前 key：来源损坏/无法解释、可证较新标注替代、可证人工替代、正文版本变化；多原因时记录全部标志，并用固定顺序选一个主因。
2. 当前 key：来源字段不匹配 → 快照不匹配 → 来源核验不通过 → 全部通过 U。

每行恰有一个最终成员判定，`|A| = |U| + |A \ U|`。生产语料或独立来源整体认证失败时，不将剩余局部成功认证为整次 U：只保留诊断数，认证总数记 UNKNOWN/未完成。

若 key/快照能进入生产 SQL 但持久化元数据损坏，生产代码在命中该候选时会抛 `stored_provenance_mismatch`，不是自动跳过；即使另有若干正确行，也应单列此检索阻断风险。本次不运行检索验证它是否进入某个 Top-K。

## 7. 覆盖率、未覆盖范围和最终结果模板

令 `K_U` 为 U 覆盖的不同有效观点 key：

| 统计 | 精确定义 |
|---|---|
| 有效观点覆盖率 | `len(K_U) / len(E)`；E 为空时为 N/A，不记 100% |
| 未建立可用向量观点数 | `len(E - K_U)`；包括无向量及有行但陈旧/不一致的观点 |
| 完全无候选向量的观点数 | 当前 E 中，在 A 没有同 key 行的数量 |
| 有候选行但未通过认证的观点数 | 有 A 同 key 行但不在 `K_U` 的当前观点数 |
| 有效向量覆盖评论数 | U 中不同当前 `review_id` 数 |
| 有效观点评论覆盖率 | U 覆盖评论数 / 当前有至少一条有效观点的评论数；零分母 N/A |
| 全范围评论覆盖率 | U 覆盖评论数 / 当次范围内评论数；与基础 105/510 分开 |
| 完整/部分/未覆盖评论数 | 对每条有有效观点评论，比较其全部有效 key 与 `K_U`；零观点评论单列 |

未覆盖分布按 `opinion_source × aspect × sentiment` 分组输出当前总观点数、可用覆盖数和缺口数；另报日期分布和完整/部分/未覆盖评论数。可在本地报告列出必要的 review_id、来源行 ID、key/摘要及错误码，禁止输出用户 ID、评论正文、quote、原始 JSON、endpoint、密码、连接串或向量数组。只有经授权实读的 ID 才能出现在明细中。

获准执行后，应在本文件登记这六层结果：

| 判定层 | 本轮状态 | 获准后必须填写 |
|---|---|---|
| 1. 基础关联 | USER_PROVIDED / 本轮未复核 | 用户基线与当次同快照结果分别列明 |
| 2. 有效语料选择 | NOT EXECUTED | 当前成功、失败、pending、empty、零观点、模型/人工观点数及完整性结果 |
| 3. 当前向量成员 | NOT EXECUTED | A、成员数、全部字段匹配数、替代数及无法解释数 |
| 4. 快照和来源摘要 | NOT EXECUTED | key+快照匹配数、独立来源核验、分项摘要/切片异常及 U |
| 5. 当前可用向量 | NOT EXECUTED | U、附加数值完整性结果及任何会导致生产检索抛错的风险 |
| 6. 覆盖与缺口 | NOT EXECUTED | 有效观点覆盖率、未覆盖数及业务维度分布 |

一次只读数据核验完成后，也须区分“数据认证完成”“数据全部一致”“覆盖完整”“真实检索质量通过”。如果有历史向量被正常替代或部分观点未索引，报告实际缺口；不能仅据覆盖不足推断语义质量，也不能据来源正确认定端到端通过。

核验只到数据库当前正文、生产有效观点、导入指纹记录和持久化向量来源绑定；不读取原 CSV 重新认证物理文件、不重新编码向量，因此不证明数值确实由指定模型针对该 quote 生成。本轮也不验证召回、排序、事实许可、回答质量或完整 Agent。

## 8. 阻断、停止条件与本轮验证

**当前最小阻断：最新恢复预检在 CONNECT 阶段被权限拒绝，未建立数据库会话。** 本轮第一阶段尝试次数为 1，查询数为 0，失败后没有重试。用户本机的 pgpass 和 11 个对象权限证据已明确标为 USER_VERIFIED；不能把当前环境的连接失败改称数据库表缺权。第二阶段没有执行，其当前有效观点和 U 均不能认证。

获准后如存在必要表/函数不可读、profile 无法唯一绑定、对象/版本不符、事务不只读或快照变化、范围越界、生产语料认证失败或超时，立即回滚关闭并报告最小具体缺口。不得扩大权限、换写账号、改库、迁移、同步、补向量、调用模型或自动重试。

P6-E1B 是独立验收，本次不自动解除其阻断；也不因其既有状态重新开展 OS/端口排查。后续只读核验即使全部通过，仍不等于真实检索质量或 P6-E1B 验收通过。

本轮复核源码并运行无数据库连接的执行器合成检查，随后仅执行一次实际 Python 连接预检并在失败后停止，交付本机只读脚本及新执行记录。没有运行会建库/改数据的测试、PostgreSQL 集成测试、服务、前端或模型。文档校验、源码摘要和执行器合成检查不计入数据库核验通过项。本阶段完成后停止，不自动重试，不发起真实 RAG 检索。

原方案阶段已完成文档静态校验；本轮重新确认 10 个源码摘要及 18 个生产来源字段，并验证只读脚本的第一阶段边界、清理和 SQLSTATE 脱敏。依赖导入及合成检查没有创建模型或数据库连接。最新真实执行结果以第 0 节及 `preflight-20261111T173315753620Z.json` 为准；第 0.2 节为前次历史记录，不能混同。

### 8.1 源码观察基线

原方案检查时 HEAD：`1d201ed332fabf8dcded4142ce1dba610769cca7`。本次开始时原方案文档已经存在且为未跟踪文件，没有生产源码变更。以下 10 个 SHA-256 本次在唯一连接尝试前全部再次匹配；后续源码变化时仍需重审读取路径。

| 文件（相对 `src/data_analysis_agent/`） | SHA-256 |
|---|---|
| `reviews/scope.py` | `673b0dc117c8b35cdae77b61c3565dc13aad795a40c81756b6c0287d74d82b32` |
| `reviews/rag_corpus.py` | `ed0ba5349caefbc1478116128ad397956c7e41ec5e8eb217b4da13fdcb877a79` |
| `reviews/semantic_retrieval.py` | `993db794b78adb1e0ecbef10440855510d2ffe90be8e90ef61f5427f97453322` |
| `reviews/embedding_store.py` | `6ed1d03e612e8ec319da044296aa3972bd9f8a9d2284b3921c7611e3fd3f4cec` |
| `agents/review_retrieval.py` | `6c9c7cfd9ba6c2b07e5a800da9a27e87cdebbe83d918d1bda27faa79ee5b287a` |
| `reviews/analytics.py` | `2ae0ff9039387f887c99419dc8f0b52b25baefd26a5fa8ec99e29393f62d63b1` |
| `reviews/label_store.py` | `3aa23810b51f04185f88c3ce19086222df1769f0b2461318c4f3f37518af0b9d` |
| `core/database.py` | `911675daa20b476f29fead1a3516d4af0ee5fbd5fa5cbe8f56a89bb0801186a8` |
| `reviews/store.py` | `8442a8a90538bae67b4e69af43dcb219849f335c648c85dfdcefcf19ae2bbdf2` |
| `core/serialization.py` | `d2e6d6cf7680cdc4e359edf3ecd5ecd34e8f283efa5a7af9c71a1db12e9d26a6` |
