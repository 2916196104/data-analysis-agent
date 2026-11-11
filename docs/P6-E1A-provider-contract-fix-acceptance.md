# P6-E1A 真实供应商契约与共享费用账本修复验收

日期：2026-11-10，Asia/Taipei。依据 [P6-E0 准入预检](P6-E0-real-environment-readiness.md)、[P6-C](P6-C-budget-and-execution-acceptance.md) 及当前实际源码。

**P6-E1A：PASS（真实安装 SDK + MockTransport 的离线工程验收）。E0-F01、F02、F03、F04 的已复现阻塞已解除；P6-E0 整体真实运行准入仍为 BLOCKED。**

原 E0 脚本保持原字节、原13项断言：修复前9 PASS/4 FAIL，修复后13/13 PASS。原13项完整迁入正式测试，新增26项边界/Worker测试；合并原556项后共595/595 PASS，无删除、跳过、expectedFailure或放宽既有断言。这里只验证安装SDK的真实序列化、响应/错误处理与本地业务链，不宣称真实账户计费或主题语义质量达标。

本轮没有收费模型、Embedding、生产数据库读写、PostgreSQL并发测试或部署。未修改 `.env`、真实价格或费用授权；准备、执行、发布三个开关仍为false，`make_execution_budget()` 仍返回无费用授权的预算。当前未提交的P6-B/C/D实现原样保留。

## 1. 原始失败复现与根因

先原样执行 `.cache/p6e0/independent_preflight.py`，退出1，13项中4个原始FAIL、0 errors/skips。脚本SHA-256为 `839c7b95d02d068c7924039daa93736d379baf2e29140e4461688f693179793a`，修复后未变。原脚本默认会更新E0缓存结果，因此本轮执行前先保存旧结果和源码副本，再分别保存本次修复前/后的结果，未丢弃原失败证据。

| 原失败 | 当前源码下的实际失败路径 | 根因 |
| --- | --- | --- |
| E0-F01 缓存费用 | SDK返回prompt=100、completion=20、cached=80；旧预算只按两个总计乘单价，将actual_cost_complete设true | `P6SharedBudget.request()` 忽略缓存明细及价格类别，没有区分保守输入预留与精确账单 |
| E0-F02 响应模型 | SDK返回未授权model，主题解析仍通过；下游grouping_model使用settings.model | 传输响应没有与授权模型/受控别名核对；请求身份不能证明响应身份 |
| E0-F03 额外推理 | enable_thinking=false，但返回completion=120、reasoning=100；旧预算只看completion是否≤max_tokens | 未检查标准completion_tokens_details.reasoning_tokens，也未明确推理与输出总计的包含关系 |
| E0-F04 429 quota | SDK抛出HTTP429、code=insufficient_quota；预算没有进入halted | 旧逻辑仅按401/402/403停止，没有在统一边界区分额度错误与普通限流 |

修复前入口是 `review_topics._call → P6SharedBudget.request → SDK/provider`；旧request中的计量及错误分支是四项根因所在。新实现把响应和错误核验集中到同一P6传输适配模块，分类定义/提示词/wire schema/Planner均未改变。

## 2. 修改文件、函数与兼容性

| 文件 / 函数 | 本轮改动 |
| --- | --- |
| 新 `agents/review_provider.py` | `P6ProviderContract`、`parse_provider_response()`、`inspect_provider_response()`、`provider_error_audit()`；统一身份、usage、费用及错误合同检查，不解释主题语义 |
| `agents/review_budget.py` | P6CostAuthorization增加可选provider_contract；ensure_authorized校验合同；request保存响应审计、未知费用/精确费用和停止原因；沿用原attempt/reserve/deadline/state机制 |
| `agents/review_topics.py` | 仅P6共享预算分支使用SDK的with_raw_response，保留原usage JSON和HTTP审计头；无code的异常保留异常类安全诊断；旧单维默认传输路径保持原create调用 |
| 新 `tests/test_p6e1a_provider_contract.py` | 原13项类/fixture/断言保持AST完全一致，标准unittest入口；另有26项边界与真实Worker测试，可脱离.cache持续运行 |
| 本报告 | 原失败、合同、状态、测试和未解除阻塞的证据 |

`review_execution.py`、`core/config.py`已审计但不需修改：现有执行器已经把P6InputIntegrityError作为全局硬失败传播；费用工厂仍默认拒绝，不新增可从.env或API装载真实授权的通路。未修改Planner、RequestGoal、主题语义、两级Validator、发布器、前端、数据库或冻结RAG契约。

保留既有budget snapshot中的usage、actual_cost、actual_cost_total、actual_cost_complete和reserved_cost语义；新增字段只用于P6内部审计，不新增外层API/任务状态枚举。三维成功或partial仍由既有P6-D独立验收决定，不通过修改状态或降低目标放行。

## 3. 响应审计与原始usage

真实SDK返回对象经 `with_raw_response` 读取HTTP状态、`x-request-id`、可用的`x-siliconcloud-trace-id`；保存response.id、response.model。`raw_usage`来自收到的HTTP JSON，保留供应商原字段及缺失状态，不把SDK model_dump自动补入的null字段当作供应商已报告。

预算记录包括：

```text
attempt / aspect / stage / timeout / body_bytes / token上界 / reserved_cost
response_model / response_id / request_id / trace_id / http_status
identity_source / identity_verified
provider_contract_version / provider_contract_source / provider_contract_sha256
raw_usage
usage                       保持旧input_tokens/output_tokens字段兼容
billing_usage               prompt / cached / uncached / completion / reasoning / total
usage_status / cost_status / cost_reason / actual_cost
provider_error_code / provider_error_type / provider_error_class
provider_halt_reason / stop_after_response_reason / error_code
```

错误审计不保存供应商message、完整错误body、请求URL、凭据或评论正文；保留安全、有界的code/type、HTTP状态和请求标识，数字错误码保持数字。原始usage及预算snapshot返回副本，外部修改副本不改变内部账本。

只有显式的内部 `P6OfflineTransport` 可兼容旧固定响应缺少model字段的夹具，记录 `identity_source=offline_transport_request`；这不是供应商响应身份认证。真实SDK缺少model、model为null、未知/未授权模型均拒绝，不能借离线兼容分支补造身份。

## 4. 缓存与精确费用规则（E0-F01）

`input_price`、`output_price`继续是每token单价，费用预留公式不变：

```text
reserve = input_token_upper_bound * input_price
        + stage_max_output_tokens * output_price
```

整轮不释放任何预留。即使已知实际usage较少、主题失败或usage缺失，也不拿差额追加调用。

可用的缓存明细是 `prompt_tokens_details.cached_tokens`、`prompt_cache_hit_tokens` 和 `prompt_cache_miss_tokens`。缓存是prompt总计的子集，不能再次加到prompt总计。只有已知prompt总计且明细一致时，才由hit或miss推导其互补项；负数、bool、非整数、超过prompt、hit/miss相加不符或两种缓存字段冲突均拒绝并停止。

精确费用仅在必要计量合法且所有实际使用类别有价格合同时计算：

```text
uncached = prompt_tokens - cached_tokens
actual_cost = uncached * input_price
            + cached_tokens * cached_input_price
            + completion_tokens * output_price
```

缓存单价位于服务端受控P6ProviderContract，必须有来源引用、来源内容SHA-256、合同版本并绑定请求model/base_url。缓存单价须为有限非负Decimal，且不超过预留使用的输入单价；超过预留输入上界价格的合同在调用前拒绝，不能改变现有费用上界。

| 情况 | 费用状态 / 执行行为 |
| --- | --- |
| 无缓存使用，合法prompt/completion和已知两类单价 | 可以按两类input/output合同计算精确费用；缺失total不伪造total，但不妨碍两项合法计量的乘法 |
| 明确cached=0 | 不需要缓存折扣价；不重复计费 |
| cached>0且有固定版本/来源的缓存单价 | 按uncached/cached分项计算，保留原usage和合同来源 |
| cached>0但缓存单价未知 | actual_cost=null、cost_status=unknown、actual_cost_complete=false；本次已发生费用不抹除，停止后续供应商请求。已返回发现候选不能替代尚未完成的归属复核或发布完整分析 |
| usage缺失或必要token字段缺失 | 保留null/unknown及全额预留，不视为免费；无已知合同破坏时沿用P6-C的受保守预留保护行为，不靠缺usage回收费用 |
| 明确未知收费类别、额外收费token或上界破坏 | actual_cost=null，拒绝响应并halt；不把它归入“可精确input/output计费” |

没有缓存明细时，不补造供应商曾报告cached=0；billing_usage保留null。精确费用标志只表示**按已授权两类单价及收到的合法计量可计算**，不是外部账户账单已对账的证明。供应商若存在未报告的额外类别，仍需真实合同与账单核验，不能由字段缺失证明免费。

汇总 `actual_cost_total` 仅在所有已发生尝试都具有精确费用时给出，否则null；`reported_cost_subtotal`仅为已报告可计算部分的和，0不代表整轮免费。失败且usage缺失的尝试保持null，不从总预留中扣除。

## 5. 模型身份与合法别名（E0-F02）

默认只接受response.model与费用授权model精确相同。合法别名通过**服务端内部不可变**P6ProviderContract的response_model_aliases声明，不从问题、前端、请求参数或响应自报别名列表读取。

合同版本为 `review-p6-provider-contract-v1`，必须绑定model/base_url、非空source_ref及64位来源SHA-256。调用前验证版本、来源标记、别名结构和计价边界；未知合同版本、缺来源、端点/模型不符或无效价格在供应商入口前拒绝。任务参数模型对provider_contract、response_model_aliases、cost_authorization仍报extra_forbidden，正式测试以完整合法scope作为对照验证。

本轮只使用 `offline-synthetic-provider-contract-v1` 和合成来源摘要证明工程路径；未声明任何真实供应商别名已得到核实。source_ref/SHA是服务器持有的合同来源绑定，不是自动验证官网或账户权限的替代品；真实别名以后仍须有可追溯的人工/官方合同核实。

合法别名可以与请求模型字符串不同：预算保存实际response_model及合同，主题产物和目标继续绑定原请求身份，不篡改冻结RequestGoal。未知、缺失、null或未知重定向模型的响应记录实际attempt、原usage、请求ID与费用预留后，设置response_model_unverified并抛P6InputIntegrityError。

真实离线Worker反例：第一维成功2次，第二维发现响应变为未授权模型；总共3次供应商尝试后停止，Worker failed，无p6_publication、无partial成功子集或主题正文。全额预留0.8400 TEST保留。合法别名对照则可通过完整三维独立验收和P6-D发布，6次、1.8600 TEST预留，回答模型0次。

## 6. reasoning_tokens、总数及输出上界（E0-F03）

当前非思考P6主题路径不接纳非零 `completion_tokens_details.reasoning_tokens`。无论合同声明推理包含在completion内还是独立于completion，禁用后出现推理都不作为有效完整主题结果：保留原usage/诊断、actual_cost=null，设置unexpected_reasoning_tokens停止后续请求。

`completion_tokens_include_reasoning`用于绑定服务端合同的包含关系，未知保持null。它不授予思考模型或额外思考费用权限。本轮不添加reasoning_price，不把相同reasoning计入第二次输出费用。reasoning=0时只按completion总计计费；reasoning字段缺失保留null，不伪造供应商报告了0。

已报告total必须等于prompt+completion；reasoning大于completion、负数、bool、非整数或总数矛盾触发usage_contract_invalid。输出总计超1200/3000时，无论reasoning如何拆分，都触发既有usage_exceeded_authorized_bound。未知细分类别、非零音频/预测等不受当前文本费用合同覆盖的类别保持未知并停止，不靠忽略字段得到精确账单。

这没有解决真实供应商的隐藏计量问题：输出max_tokens是否在真实账户下覆盖全部消耗、enable_thinking=false是否确实执行、实际usage是否完整，仍须后续有授权的真实验证。本阶段只证明已知反例会被拒绝。

## 7. 429 / quota错误状态机（E0-F04）

错误映射只在 `review_provider.provider_error_audit()` 定义，由共享预算在传输异常边界调用；主题层仅保留原有失败展示，不再新增另一份额度推断表。只看结构化HTTP/code/type，不根据自然语言message猜测。

| 传输结果 | 分类 | 后续付费请求 / 本次记账 |
| --- | --- | --- |
| HTTP401/403 | authorization | 既有provider_401/provider_403，halt；本次attempt及reserve保留 |
| HTTP402 | insufficient_quota | 保持provider_402，halt；不管附带code是否是已知字符串 |
| 明确code或type=insufficient_quota，包括HTTP429 | insufficient_quota | provider_insufficient_quota，halt；后续调用在传输前拒绝 |
| 普通HTTP429 / 已知rate_limit标识 | rate_limit | 不改判欠费；本次不自动重试，stage失败。仅依既有P6规则及剩余预算考虑其他维度，不重发同一阶段 |
| HTTP500/502/503/504、明确transport timeout/connection error | 对应已知传输类别 | 本次不重试、保留预留；沿用P6-C的局部失败/剩余预算规则，不在本阶段改变取消契约 |
| 未识别code/状态/错误分类 | unknown | provider_error_unknown，halt；没有隐式重试或继续追加未知收费调用 |

显式内部P6OfflineTransport的固定异常保留离线夹具兼容标记，不代表真实传输被当作已知错误。普通429和quota429均通过真实SDK错误对象验证，HTTP数值、原provider code/type、request/trace ID与安全类别进入账本；不保存可能包含凭据的错误message/body。

429+insufficient_quota是兼容性故障注入，不因此声称当前SiliconFlow一定这样返回；本阶段未向真实账户制造额度错误。

## 8. 共享预算与token上界仍然fail-closed

保持所有P6-C上限：必做维度≤3；每维发现1次/复核1次；整轮≤6次；失败计入；SDK重试0；body≤16000 UTF-8字节；发现≤1200、复核≤3000输出tokens；单请求≤min(配置,20秒,剩余预算)，每维40秒、整轮分类120秒。原阶段顺序、重入阻断、截止和迟到拒绝未修改。

token_upper_bound实际收到的是**追加wire说明后的最终system prompt**及**包含schema的最终wire JSON body**，正式SDK测试与实际发送messages逐字对照。它不会自动收到或计算HTTP消息封套、供应商chat template或其他隐藏计费输入；服务端可信profile必须知道并覆盖这些固定协议开销。当前没有真实DeepSeek-V3.2可靠tokenizer/profile，不能用现有两个参数的字节数、bytes/4或任意倍率证明真实上界。

`make_execution_budget()`默认仍无authorization；本轮不增加真实费用profile加载、环境变量价格或账户授权。不把合成lambda=1000、TEST单价、合同source_sha256或allow_real测试对象当作可用于真实收费的授权。默认全部开关打开时仍在供应商前以cost_not_authorized拒绝。真实上界不存在，真实费用授权继续BLOCKED。

HTTP timeout仍不等于可靠远端取消或整请求wall-clock硬终止；本轮未用线程超时假装取消。没有改变远端迟到/超时可能继续计费的未验证风险。

## 9. 修改前后账本对比

以下全部为合成TEST币种：input/output单价0.0001/token，缓存合同示例0.00001/token，输入预留上界1000，发现输出预留1200。一次发现固定预留0.2200；不是实际供应商报价或真实token估计。

| 响应 | 修改前 | 修改后 |
| --- | --- | --- |
| prompt100/completion20/cached80，无缓存价 | actual=0.0120、complete=true，忽略缓存 | actual=null、unknown、complete=false；原usage完整保留，halt后续；reserve=0.2200不释放 |
| 同上，有固定来源缓存价 | 无分项合同能力 | actual=20×0.0001+80×0.00001+20×0.0001=0.0048；合同来源/版本绑定，reserve仍0.2200 |
| prompt50/completion20，无缓存使用 | 两项乘积0.0070，缺原始审计字段 | 合法两类计量仍0.0070；另有raw_usage、response.model/id和request/trace ID |
| usage缺失/不全 | 原本保留预留；没有原始usage视图 | 继续null/unknown，全额预留；不补零、不用已知小计冒充总费用 |
| 未授权model | 不检查，可能接受主题 | 记录尝试后全局身份硬失败，无主题发布；reserve保留、费用未知 |
| completion120包含reasoning100 | 可能接受并按0.0170标精确 | 保留两个字段，不把100再加一次；非思考合同违约，拒绝并halt、actual=null |
| quota429 | halted_reason=null | provider_insufficient_quota，后续传输0次 |
| 普通rate_limit429 | 失败、不重试 | 仍不重试，不误报欠费；安全code/HTTP类别可审计 |

实际合成审计对象位于 `.cache/p6e1a/provider-examples.json`，包括无缓存、缓存已定价/未知、usage缺失、模型失配、推理及两类429。只保存合成计量和身份，不含真实密钥、原始评论或人工标签。

## 10. 测试结果与保护核对

| 检查 | 结果 |
| --- | --- |
| 原E0独立SDK脚本，修改前 | 13项：9 PASS、4 FAIL、0 error/skip；原FAIL完整保存 |
| 原E0独立SDK脚本，修改后 | **13/13 PASS**；文件字节SHA不变 |
| 正式新测试模块 | **39/39 PASS**：原13项 + 新26项；原class/fixture/断言AST完全一致，非导入.cache执行 |
| P6-C / P6-D专项 | 44/44 PASS，保留预算和发布前独立验收；已计入原556项 |
| 全部适用后端合并 | **595/595 PASS**，最终208.922秒，0 failures/errors/skips；原556项全部保留 |
| 静态私有接口审计 | 独立运行仍为1个方法内13个历史失败子项，0 error/skip；未清理或放宽 |
| 实际SDK | openai1.109.1、httpx0.28.1；MockTransport捕获HTTP与原usage；合成价格和假时钟 |
| 真实模型/Embedding/生产数据库 | 0 / 0 / 0；原完整回归外层封锁socket、psycopg，新增SDK调用只在内存传输 |
| 既有源码/资产保护 | 核对2251个入场文件，只有review_budget.py、review_topics.py改变；旧测试、Planner/分类语义、配置及冻结RAG/人工资产摘要保持；新文件独立列出 |

新增26项边界覆盖：原usage及请求身份、已定价缓存、未知缓存价的停止、零缓存、hit/miss互补、不完整usage、缓存/total矛盾、reasoning缺失/零/非零/非法值、未知收费类别与输出上界、合法别名/未知/缺失/null/重定向模型、错误合同的传输前阻断、客户端extra_forbidden、普通429/quota/未知错误、失败留账、截断主题、最终token参数投影，以及真实Worker的三维别名成功和中途模型身份硬失败。

复现命令（项目根目录PowerShell，不连接外部服务）：

```powershell
$env:PYTHONPATH='.;src;tests'
.\.venv\Scripts\python.exe .cache/p6e0/independent_preflight.py
.\.venv\Scripts\python.exe -m unittest test_p6e1a_provider_contract -v
.\.venv\Scripts\python.exe -m unittest test_p6c_budget_execution test_p6d_publication -v
.\.venv\Scripts\python.exe .cache/p6e1a/run_checks.py functional
.\.venv\Scripts\python.exe .cache/p6e1a/run_checks.py static
```

最后一条仍应非零退出，单列历史13个私有导入问题。正式回归不依赖.cache探针；完整原556项清单沿用前阶段缓存清单，没有删除旧失败断言。没有新增收费测试、真实数据库测试或评审标签。

证据目录 `.cache/p6e1a/`：`original-*`保留入场E0源码和旧结果，`before-*`为本次原失败，`after-*`为原脚本修复后结果；`original-assertion-check.json`记录正式迁移一致性；`new-final-tests.txt`、`functional-summary.json`/`functional-tests.txt`为专项和合并结果；`static-summary.json`为独立历史状态；`provider-examples.json`、`runtime-safety.json`及`before-hashes.json`/最终保护核对记录账本、默认关闭配置与资产完整性。

开发期间没有调整主题定义来适配测试。SDK model_dump会补入null字段，已改为从收到的HTTP JSON保存原usage；测试夹具中HTTP外层与messages内wire层不同，已修正新夹具解包而非修改旧断言。全部完成后保持原E0脚本、原测试及人工结论不变。

## 11. 阶段结论与未验证边界

**E0-F01～F04：PASS，四项已复现的真实SDK准入失败解除。** 该结论限于真实安装SDK、MockTransport、合成账单与离线Worker的工程契约；未声称供应商实时合同已被外部验证。

| 后续准入项 | 当前状态 |
| --- | --- |
| 真实tokenizer与消息/模板计费上界 | BLOCKED；未实现或猜测，默认无授权 |
| 实时价格、真实别名与账户账单 | BLOCKED；未访问账户、未定价真实费用、未配置真实profile |
| 可靠取消、超时后远端计费 | 未验证，仍阻塞相应真实运行承诺 |
| 独立PostgreSQL并发/资源释放 | BLOCKED；未建立或连接测试库，未用生产库替代 |
| 真实主题语义/证据支持质量 | BLOCKED；保持E0对两模拟模型共同误判的限制，未修改人工资产或用工程通过率代替质量达标 |
| 生产数据库/部署 | 未执行，不属于本阶段授权 |

不改写E0历史报告的整体BLOCKED结论，不自动进入真实收费验证、PostgreSQL集成或部署。完成P6-E1A后停止。
