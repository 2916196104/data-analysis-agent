"""从实际验收产物组装报告；只有最终后端和浏览器通过后才写入。"""
import json, re, statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
BROWSER=ROOT/'output/playwright/assistant-VJEXxY'
def load(path): return json.loads(path.read_text(encoding='utf-8'))
def pretty(obj): return json.dumps(obj,ensure_ascii=False,indent=2)
def artifact(path): return '../../'+path.relative_to(ROOT).as_posix()
def link(label,path): return f'[{label}]({artifact(path)})'
def table(headers, rows):
 return '| '+' | '.join(headers)+' |\n| '+' | '.join('---' for _ in headers)+' |\n'+''.join('| '+' | '.join(str(x).replace('|','/').replace('\n',' ') for x in row)+' |\n' for row in rows)
backend=(OUT/'backend-accepted.log').read_text(encoding='utf-8')
assert re.search(r'Ran 348 tests',backend) and '\nOK\n' in backend
browser=load(BROWSER/'acceptance.json'); assert browser['status']=='passed' and not browser['errors']
ui_observed={r['taskId']:r['submitToVisibleMs'] for r in browser['uiObservations']}
perf=load(OUT/'performance-accepted/performance.json')
live=load(OUT/'live/evaluation-revalidated.json'); assert live['passed']==12 and live['failed']==0
fixed=load(OUT/'offline-accepted/evaluation.json'); assert fixed['passed']==43 and fixed['business_tables_unchanged']
examples=load(BROWSER/'phase7-business-examples.json')
positive,trend=examples['positive'],examples['trend']
conversation=examples['conversation']
phase7=browser['phase7']
parts=[]
def add(s): parts.append(s.strip()+'\n')
add('''# 阶段 7：最终验收报告

验收日期：2026-10-30（Asia/Taipei）。项目：`E:\\AllProject\\data-analysis-agent`。

结论：可以作为本地项目演示。后端完整回归 348/348、前端 185/185、固定评测集 43/43、最终四宽度浏览器验收通过。真实供应商评测 12 题符合预期，其中 11 题实际经过模型规划，另 1 题在调用前说明相对日期未解析；真实解释请求有 1 次超时并安全降级。这不等于生产级稳定性或任意自然语言准确率认证。

本阶段只补测试、评测与有证据的局部修复。没有新增 Agent 框架、数据库组件或跨任务缓存，没有修改生产评论、标注或数据库结构。阶段 0～6 已有未提交改动全部保留。

## 1. 验收范围、环境与证据

已阅读阶段 0～6 报告，并以当前代码和本次执行结果重新验证。测试环境是 Windows 11、Python 3.12.10、Node 24.19.0、22 个逻辑 CPU、PostgreSQL 18.6、Redis 7.4.11。另起仅监听回环地址的 PostgreSQL 集群和独立 Redis 容器；所有可写夹具均位于随机 `agent_test_` 库。

真实模型身份为本地配置的 `deepseek-ai/DeepSeek-V4-Flash`，供应商主机为 `api.siliconflow.cn`。没有替换为其他生产模型来获得通过结果。外部只发送合成评论/标注及测试问题；真实业务库仅在本机进行只读计数、目录读取和 SQL 采样。

报告中的“美的空调”是固定验收问句。实际绑定的是 DEMO-A/DEMO-B 测试商品，不是生产美的商品；所有示例数字均不得当作真实品牌结论。
''')
add(table(['证据','产物与用途'],[
 ['E1',link('完整后端回归',OUT/'backend-accepted.log')+'；含原有 PostgreSQL/Redis 条件测试，无跳过'],
 ['E2',link('前端测试',OUT/'frontend-tests-serial.log')+'；'+link('TypeScript 与 Vite 构建',OUT/'frontend-build.log')],
 ['E3',link('43 题逐题完整结果',OUT/'offline-accepted/evaluation.json')+'；包括实际回答、参数、预期、检查与 oracle 错误列表'],
 ['E4',link('真实供应商逐题结果与调用账本',OUT/'live/evaluation-revalidated.json')+'；不是 Mock 延迟'],
 ['E5',link('最终浏览器验收',BROWSER/'acceptance.json')+'；'+str(len(browser['screenshots']))+' 张截图、实际 API/Worker/PostgreSQL/Vite'],
 ['E6',link('最终性能原始样本',OUT/'performance-accepted/performance.json')+'；每场景 50 次'],
 ['E7',link('PostgreSQL 专项复核',OUT/'postgres-final.log')+'；同维度重复观点、人工覆盖、快照、版本和 SQL 时限'],
 ['E8',link('最终局部回归',OUT/'last-regression.log')+'；37 项通过'],
]))
add('''## 2. 最终真实架构

```mermaid
'''+(OUT/'architecture.mmd').read_text(encoding='utf-8')+'''```

聊天分析任务保存在现有任务库，由 TaskWorker 领取和写终态；Redis 是现有标注队列与共享限流基础设施，不能把 Redis 标注测试描述成聊天任务通过 Redis 调度。确定性追问跳过规划模型，仍经过相同的工具、SQL、校验、压缩与回答组装。

复杂分析的解释器只读取 CompactAnalysisContext，没有数据库查询工具。解释超时或格式/事实校验失败时，使用已核验数据的模板；统计未通过校验时禁止发布正常 PublicAnswer。历史读取从冻结任务重建公开字段，不重新查询统计或调用模型。

## 3. 阶段 0～7 完成情况
''')
add(table(['阶段','已实现与本次核验'],[
 ['0','已有架构审查；本次重查执行入口、工具、统计、Worker、响应与前端'],
 ['1','正面六维聚合、正负面意图、独立分母；SQL 与模板回归通过'],
 ['2','工具计划、去重、证据每轮一页预算、失败/部分结果发布保护；补上直接路径 SQL 时限'],
 ['3','结构化结果、压缩上下文、公开白名单；保留 results/result_ids/scope'],
 ['4','受约束解释、事实验证与组装、模板/降级；真实供应商超时也未发布半成品'],
 ['5','现有 React 聊天、统计详情、证据、复制、导出、历史；四宽度实测'],
 ['6','冻结逐轮上下文、确定性追问、范围澄清、历史引用、性能字段；实际任务验证'],
 ['7','补齐 PostgreSQL/Redis、独立 oracle、43 题、真实模型、性能与最终 UI 验收'],
]))
add('''## 4. 本阶段修改文件与已证实缺陷

下列是本阶段文件，不把工作区中阶段 4～6 的既有未提交改动算作本阶段重写。
''')
add(table(['文件','本阶段职责'],[
 ['`src/data_analysis_agent/agents/review_prompts.py`','修复否定跨子句/转折，补充“问题”负向表达'],
 ['`src/data_analysis_agent/agents/review_plan.py`','评论总数问法、已选商品 ID 对比、单维度约束；复合请求按子句取维度，方向不明确先澄清'],
 ['`src/data_analysis_agent/agents/reviews.py`','只读事务设置至多 30 秒 statement_timeout，保留服务器更严格上限'],
 ['`tests/test_phase7_acceptance.py`','固定集、双向否定、付费请求预算与 P95 样本门槛自检'],
 ['`tests/test_phase7_postgres.py`','独立数值核验、混合/同维度多观点去重、快照并发、版本、空月与真实 SQL 超时'],
 ['`tests/test_public_analysis_api.py`','历史公开读取不重新统计、不调用模型、不写业务库或任务库'],
 ['`scripts/phase7_cases.py`','43 条手工固定预期；不由查询计划反推期望'],
 ['`scripts/phase7_evaluation.py`','真实/模拟评测分离、Python 原始记录 oracle、范围/完整商品检查与请求预算'],
 ['`scripts/phase7_fixture.py`','只向 agent_test_ 隔离库追加 8 条物流分页样本'],
 ['`scripts/phase7_performance.py`','9,600 条合成数据、50 次样本/场景和本机业务只读探测'],
 ['`scripts/phase7_services.py`','独立服务启动/停止与目录/容器范围检查'],
 ['`frontend/tests/assistant.e2e.mjs`','复用真实联调；新增物流翻页、趋势/优先级、真实失败后追问和完整示例快照'],
 ['`docs/agent-refactor/final-acceptance-report.md`','最终报告'],
]))
add('''证据 → 问题 → 修复路径：

- `plan-before.json` 证实总数/维度/双向否定/商品对比问法存在遗漏或错向；通过共享意图与计划入口局部修复，固定集和真实供应商记录复核。
- 第一次修改后，旧测试 `test_report_rejects_wrong_numbers_coverage_quotes_and_omitted_references` 暴露复合问题的维度串扰；修复为按排名子句取维度，原有全部数值/引文/遗漏引用断言保留并通过。
- 实际代码检查发现，确定性追问绕过框架的工具异步时限，而 PostgreSQL 默认 statement_timeout 可为 0；在只读分析事务内加 30 秒上限，真实 PG 检查默认 30s、服务器原有 2s 保持 2s、5ms 超时不发布成功回答。

没有改任务必填字段、数据库 schema、六维含义、已有负面字段、标注内容或 ChatMessage/AgentResults 的业务逻辑。没有增加索引或宣称未经对照证明的 SQL 加速。

## 5. 测试汇总与历史失败
''')
add(table(['检查','最终结果','边界'],[
 ['Python 完整回归','PASS：348/348，0 skipped','包含 PostgreSQL/Redis；'+link('日志',OUT/'backend-accepted.log')],
 ['真实 PG 最终专项','PASS：5/5','最后增补的同维度两条观点仍按一条评论计数也已单独运行'],
 ['前端','PASS：22 文件、185/185','限制 2 个测试 Worker，断言与 10s 超时不变'],
 ['TypeScript / Vite','PASS','仍有既有 >500KB chunk 警告'],
 ['固定评测集','PASS：43/43','32 个正常分析、10 个明确能力限制、1 个不存在商品的绑定校验拒绝'],
 ['真实供应商','PASS：12 题符合预期','11 个模型路由任务；1 次解释超时降级'],
 ['四宽度实际联调','PASS','1440/1024/768/390；模拟模型，SQL/API/Worker/Vite 真实'],
 ['性能','PASS：12 场景各 50 次','6 个 SQL + 6 个 Agent 场景；不表示生产 SLO'],
 ['脚本语法/差异检查','PASS','compileall、node --check、git diff --check'],
]))
add('''失败历史没有覆盖或删除：

- `frontend-tests.log` 首轮 182 通过、3 项在并行构建/其他验证期间超过原有 10 秒；`frontend-tests-serial.log` 控制 Worker 后 185 项全过，没有放宽断言。
- `backend-final.log` 为 345 项、1 error，来源是上述复合维度串扰。最终 `backend-accepted.log` 为 348 项全过。更早的 `regression.log` 曾因导入测试类重复收集同一失败，不能将重复执行算作更多独立覆盖。
- `offline/evaluation.json` 首轮 39/40；不存在的绑定商品实际被拒绝，而用例原期望正常分析。修正为明确 `error`、无正常 PublicAnswer 后，最终扩展为 43 项；拒绝规则未放宽。
- 浏览器早期 `assistant-kQsq4Y` 在新增分页用例中选中了另一标注版本，正确得到 0 条并澄清末页；`assistant-tpcnRg` 是日期未提交的自动化失败。保留两份 acceptance.json。日期输入改为填充并 Enter 确认，数据样本显式绑定其真实标注身份。
- 最终截图复核还发现测试从外部进程导入后目录仍是旧摘要；脚本在外部导入后刷新真实页面，旧任务仍读冻结快照。`assistant-rsQqjH` 是刷新步骤前的成功运行，最终证据使用本报告 E5。
- `harness-selfchecks.log` 曾受环境 SSLKEYLOGFILE 文件权限影响；预算检查改为创建传输之前执行，验收子进程清除该调试变量，最终自检通过。

## 6. PostgreSQL / Redis、Worker 与并发一致性

阶段 6 的 32 个外部条件跳过项，本次基线 338 项全部实际执行且通过；最终包含新增检查的 348 项没有跳过。验证了显式迁移、导入回滚、标注/人工复核读取、数据集/商品/时间过滤、多商品和空范围。SQLite 与 PostgreSQL 扩展统计结果一致。

Redis 测试使用独立容器。真实两 Windows Worker 进程共享 PG/Redis，验证批量排空、共享并发限制与去重；同时覆盖 outbox 重投、提交后 ACK 失败、数据库失败不 ACK、限流、认证暂停、有限重试、租约接管和过期 owner 不能提交。部分故障通过主动过期租约/受控异常注入，不能描述为生产宕机演练。

任务提交/领取/终态、幂等键与重试父任务、历史恢复由现有测试验证；实际 E2E 连续创建并轮询真实任务，还制造了真实 Worker 的规划格式失败，再从最近成功且同范围的任务继续追问。

新 PG 测试在只读 repeatable-read 事务内读取后，从另一连接导入评论、提交人工空修订、写入新标注版本。旧事务结果不变；新事务读到 7 条评论、4 条成功标注、人工覆盖后的 0 条负面，新版本单独只有 1 条成功标注。没有把旧缓存跨任务复用。

幂等是结果提交与任务处理层的保证，不是供应商严格只计费一次：模型响应后、数据库提交前崩溃，仍可能再次调用模型；既有 `test_stage1_crash_after_model_response_can_repeat_model_call` 明确覆盖这个窗口。

## 7. 固定语义集与真实模型评测

固定集为 39 条自然语言/边界请求和 4 条经会话确认的直接路径。受控 HTTP 返回手工预期的工具请求，真实运行 HelloAgents、PG、校验、压缩与回答组装；它验证应用计划和执行链路，不能当作真实模型理解准确率。原始逐题文件记录问题、预期方向/工具/参数、实际工具、实际 PublicAnswer、scope、校验、耗时及失败原因。

覆盖标准问句、口语/同义词、省略表达、双向否定、混合意图、两维趋势、未解析相对时间、细分维度限制、证据预算、零正向/未标注/空范围、不存在商品、确认追问与复合子句隔离。不存在商品用例验证的是绑定范围拒绝，不等于已验证任意商品名称自动消歧。
''')
add(table(['ID','用户问题','预期方向/能力','结果'],[(c['id'],c['question'],str(c['sentiment'])+' / '+(', '.join(t for t,_ in c['expected']) or c['status']), 'PASS') for c in fixed['cases']]))
add('''真实评测使用相同合成 PG 数据，最多 12 题/36 次 HTTP 请求/600,000 输入字节，每次输出至多 2,048 token，SDK 不重试；总最大输出预算 73,728 token，没有改生产模型身份。实际 24 次请求（22 规划 + 2 解释），总请求体 243,796 字节。

11 个真实规划任务全部满足固定工具、参数、完整所选商品、数据集/标注身份及独立原始记录 oracle；另 1 题相对日期在调用前说明限制，不计入模型路由成功分母。这是应用约束计划下 11/11 的小样本观察，不是开放式模型泛化准确率。
''')
add(table(['问题','实际规划/解释调用','完整 Agent 测量秒','生成方式'],[(c['question'],str(c['actual']['model_calls'])+'/'+str(c['actual']['answer_model_calls']),f"{c['actual']['performance']['total_duration_ms']/1000:.3f}",c['actual']['public_answer']['generation_status']) for c in live['cases']]))
add('''真实模型任务的端到端函数测量中位数 4.695 秒，范围 3.352～21.336 秒，不含 API 排队和 React。逐题 elapsed_ms 还包括验收器的独立 oracle 耗时，不把这部分当作供应商延迟。11 个规划最终 JSON 均合法；解释请求 1 个返回合法结果、1 个超时，未收到完成 JSON。生成方式：9 个模板、1 个真实解释、1 个安全降级、1 个不可用/能力限制。

23/24 次请求返回 usage：已知 prompt 63,639、completion 1,545、total 65,184 token。超时请求用量/是否计费未知，因此这些是已知下限。供应商未提供可信实际账单，实际费用为 null；请求/输入/输出额度可控，不把 token 推算写成实际费用。

真实供应商测试采集早于最后的复合子句与 SQL 时限修复；后续未追加付费请求。12 题对应的工具/参数预期未改变，最终固定集、PG 回归及模拟浏览器复测覆盖了交付代码；不声称又运行了一次最终代码的真实供应商全 UI E2E。

## 8. 统计正确性、证据与回答可信度

独立 oracle 直接读取 PG 原始 reviews、annotations、opinions、human revisions，在 Python 中按正文 hash、指定身份、成功优先/最新尝试和最新人工快照选择有效观点；不调用业务聚合 CTE、统计校验器或压缩器生成预期。逐结果对比计数、分母、占比、排序、时间桶与商品分组。另故意篡改正面维度数量，oracle 与原有 validate_report 都会拒绝错误。

- 正面：每维度 `COUNT(DISTINCT review_id)`，同一评论多维共同命中合法；同维度两条正向观点只加一条评论。正向提及/正向评论总数和正向提及/成功标注评论是两个独立比例，六位小数口径不变。
- 负面：总负面评论和各维度均去重，占比以成功标注为分母；混合正负评论分别进入对应集合，多维数量不能相加当总数。
- 人工修订完全替换原观点，空人工快照也有效；另一模型/版本及另一数据集不串用。没有从星级推断维度情感。
- 零成功、零正向和无评论分别保留数据状态；分母为零为 null。没有把缺失值写成 0%。
- 趋势：PG 实测补齐缺失月和 null 分母；既有压缩/回答测试还覆盖中间峰谷、相邻变化、平台期、稀疏月、未完整月、低覆盖、商品/窗口不混合。SQL 原值与摘要事实受校验。
- 证据：review ID、原文切片/quote offset、有效观点的 aspect/sentiment、来源引用受检查；实际浏览器两页各 3 条，6 个不同 ID，分页/范围/身份一致。截断与引用规则由既有压缩测试覆盖。
- 回答：数值来自绑定事实，不由解释模型自由填入；错误 JSON、虚构数字、错误方向/范围/因果或商业优先级触发拒绝/降级。单个原文不代替总体排名，普通回答不显示内部工具编号、API URL 或标注身份。

## 9. 多轮完整场景
''')
add(table(['场景','实际验证结果'],[
 ['A：好评→差评→物流','同 source/dataset/products/UTC/标注身份；情感 positive→negative→negative，后两轮规划调用 0'],
 ['B：3 条物流差评→下一页','真实 PG 查询 page=1/2，page_size=3，6 个不同 ID；第二轮规划调用 0'],
 ['C：两维月度趋势→优先解决哪个','前端明确缺少严重程度/业务影响/成本，产生澄清，不生成确定商业优先级'],
 ['D：切换范围','数据集/商品/日期/标注选择、确认与取消、迟到覆盖率响应、旧消息冻结、游标重置与历史恢复均覆盖'],
 ['E：失败后追问','真实 Worker 返回 failed，不显示 PublicAnswer；继续使用最近成功的同范围证据任务，直接执行并从 page=1 开始'],
]))
add('''外部进程直接写库后，目录摘要需要刷新页面；当前覆盖率和新分析可以读到新数据。最终 E2E 显式刷新目录再验证，不能据此宣称前端有数据库变更实时推送功能。

## 10. 性能与 SQL 观察

性能库总计 9,600 条合成评论、2 个商品，查询窗口 2023-01-01～2024-12-31（24 个月）。9600 是库总行数，不是所有单商品/日期子范围的分母。数据由测试夹具按月份复制，不能代表真实长尾文本或百万级数据分布。

每场景串行 50 次，P50 为中位数，P95 为 nearest-rank 第 48 项；共 600 次测量。采样期间同机还执行其他验收，属于有背景负载的绝对观察，非独占基准、非吞吐/并发容量测试。没有同环境改造前基线，不宣称相对加速。
''')
add(table(['场景','N','P50 ms','P95 ms'],[(k,v['samples'],v['p50_ms'],v['p95_ms']) for k,v in perf['measurements'].items()]))
add('''SQL 场景没有模型，SQL 函数耗时包含连接/快照等开销；Agent 场景使用受控 HTTP/模板，不包含真实供应商或 API 排队。真实供应商仅 11 个任务，前端观测也是有限串行任务，均不报告 P95。

原始 runs 保留 planning/query/validation/compression/answer/total、model_tool_attempts、query_executions、cache_hits、answer_model_calls、answer_input_bytes。实际 Worker 示例另保留 queue_duration_ms。这里的跨任务 cache_hits 为 0 符合设计；专门回归验证同事务 overview/相同月趋势可复用一次聚合，不存在无失效机制的跨任务缓存。

已检查筛选、有效观点优先级、COUNT DISTINCT、人工覆盖、时间桶与重复调用行为，没有仅凭耗时猜测新增索引。没有执行 SQL/索引优化，因此不提供虚构的优化前后对照，也未将本次采样称为 EXPLAIN ANALYZE 优化证明。

真实业务库本机只读探测：reviews=16,514、products=1,706、annotation rows=7,563、opinion rows=13,054。目录中选定的 reviews_csv/reviews-csv 有 9,573 条、24 商品。annotation 行数不是成功标注评论分母，不据此宣称覆盖率；没有将业务原文发送给供应商。每业务 SQL 仅 3 次采样，不报告 P95，也没有生产并发/标注质量验收。

## 11. 超时、失败与降级
''')
add(table(['故障','运行证据与发布结果'],[
 ['规划超时/预算耗尽','真实 HelloAgents/MockTransport 与同步注入回归；即使工具已有结果也不发布正常回答'],
 ['真实 PostgreSQL 查询超时','5ms statement_timeout + pg_sleep 取消真实 SQL；结果 error，PublicAnswer 非 ok'],
 ['直接追问查询时限','默认 30s；已有 2s 保持 2s，事务结束即撤销设置'],
 ['真实供应商解释超时','实际 15.392 秒解释阶段超时，统计不重查，generation_status=fallback'],
 ['模拟解释超时','真实 API/Worker/浏览器链路中成功显示可靠降级提示，不展示半成品'],
 ['格式不合法','真实 Worker 格式失败保存 failed；既有测试还验证一次格式纠正与耗尽'],
 ['数值/范围/方向校验失败','validate_report、AnswerValidator 与公开接口拒绝发布'],
 ['网络中断/失败重试','浏览器受控断网/任务状态拦截覆盖 UI；实际 Worker 失败场景单独验证'],
 ['Worker 中断','队列租约接管/旧 owner 拒绝提交等回归；不等于生产硬断电演练'],
]))
add('''没有调大现有模型超时掩盖问题；新增的是确定性直接路径缺少的 SQL 执行上限。SQL 时限为单语句而非整个任务的绝对总截止时间，多轮/多语句总耗时仍受工具/模型预算及现有任务流程约束。

## 12. 公开 API 与访问边界

公开路由只返回 task_id/status/public_analysis/public_answer 白名单；`--public-only` 下完整任务、options、health、reviews 及写路由不可绕过访问。Host/Origin 不可信被拒绝，未知任务返回 404；失败/partial/不明历史 shape 不会带出统计或证据。新测试用哈希与调用哨兵验证重复读取历史不写数据库、不重算统计、不调用模型。

默认完整任务 API 仍保留开发审计、原始结果与导出兼容性，定位是本机拥有者应用；UI 折叠并非网络权限控制。白名单本身也不构成用户/租户授权。当前没有新增登录与完整授权系统，公开部署前仍需任务所有权、数据范围授权、认证、限流、TLS、跨域/代理配置、日志与数据保留策略。

项目验收产物扫描未发现本地配置的真实 Key/Token/Password 字面值；最终扫描与哈希清单随报告保留。不输出 .env、完整带凭据 DSN 或 HTTP Authorization。

## 13. 四宽度界面与完整业务示例

真实 Vite/Chromium、实际 API/Worker/PostgreSQL，模型服务器明确为模拟。四宽度检查页面/消息横向溢出、输入区域和范围面板位置、对齐、表格滚动、日历边界、无对话框遮挡，以及复制、导出、历史和数据集切换。116 张截图、浏览器脚本异常 0。最终另外人工查看桌面、768px 趋势和 390px 证据截图。
''')
add('### 示例一：正面分析\n\n问题：美的空调好评主要集中在哪些方面？\n\n'+link('完整冻结任务',BROWSER/'phase7-business-examples.json')+'。实际绑定范围：\n\n```json\n'+pretty(positive['result']['scope'])+'\n```')
pr=positive['result']; ps=next(i['data'] for i in pr['results'].values() if i['kind']=='positive_aspects')
add(f"任务 `{positive['task_id']}`；结果引用 `{', '.join(pr['result_ids'])}`；工具为自动 overview 与 get_positive_aspects，未自动查询原文。共 {ps['summary']['review_count']} 条、成功 {ps['summary']['successful_reviews']} 条、正向评论 {ps['summary']['positive_review_count']} 条；使用效果正向提及 76，正向分母 76（100%），成功标注分母 96（79.1667%）。其他五维为 0，没有编造制冷/静音/安装数量。Agent {pr['performance']['total_duration_ms']}ms，排队 {pr['performance']['queue_duration_ms']}ms，浏览器提交至可见 {ui_observed[positive['task_id']]}ms。")
add('完整 PublicAnswer：\n\n```json\n'+pretty(pr['public_answer'])+'\n```\n\n!['+'正面分析 1440px'+']('+artifact(BROWSER/'phase6-positive-1440.png')+')')
add('### 示例二：包装与物流月度趋势\n\n问题：按月查看包装和物流问题变化。\n\n为证据分页追加 8 条物流差评后，刷新目录并继续使用同一测试数据集和标注身份。此快照共 104 条，示例一旧任务仍保持原来的 96 条；这两个分母属于不同冻结快照，不能合并。\n\n实际范围：\n\n```json\n'+pretty(trend['result']['scope'])+'\n```')
tr=trend['result']; buckets={}
for item in tr['results'].values():
 if item['kind']=='aspect_trend':
  aspect=item['parameters']['aspect']
  for b in item['data']['buckets']:
   row=buckets.setdefault(b['bucket'],{'count':b['review_count'],'denominator':b['successful_reviews']})
   row[aspect]=b['aspects'][aspect]
add(table(['月份','评论 / 成功分母','包装负面 / 占比','物流负面 / 占比'],[(k,f"{b['count']} / {b['denominator']}",f"{b['packaging']['negative_review_count']} / {b['packaging']['negative_rate']}",f"{b['logistics']['negative_review_count']} / {b['logistics']['negative_rate']}") for k,b in buckets.items()]))
add(f"任务 `{trend['task_id']}`，结果 {tr['result_ids']}，分别调用 get_aspect_trend(packaging,month) 与 get_aspect_trend(logistics,month)，无原文查询。Agent {tr['performance']['total_duration_ms']}ms，排队 {tr['performance']['queue_duration_ms']}ms，浏览器提交至可见 {ui_observed[trend['task_id']]}ms。两个时间桶只支持相邻变化/峰值描述，不足以断言长期稳定趋势；2 月为未完整月，不据此证明原因或商业优先级。\n\n完整 PublicAnswer：\n\n```json\n"+pretty(tr['public_answer'])+'\n```\n\n![月度趋势 768px]('+artifact(BROWSER/'phase7-monthly-trend-768.png')+')')
add('### 示例三：好评→差评→物流\n\n三轮均使用示例一的同一冻结范围（2023-01-01～2023-02-24、DEMO 全部两款、SIMULATED-frontend、SIMULATED-ANNOTATIONS/fixture-v1），未追加分页样本前执行。'+link('三轮完整任务与 PublicAnswer',BROWSER/'phase7-business-examples.json')+'。')
add(table(['轮次','任务 / result IDs','方向 / 工具','规划/解释调用','Agent ms / 排队 ms'],[(i+1,f"`{t['task_id']}` / {t['result']['result_ids']}",t['result']['sentiment_intent']+' / '+', '.join(v['kind'] for v in t['result']['results'].values() if v['kind']!='context'),str(t['result']['model_calls'])+'/'+str(t['result']['answer_model_calls']),str(t['result']['performance']['total_duration_ms'])+' / '+str(t['result']['performance']['queue_duration_ms'])) for i,t in enumerate(conversation)]))
add('''第二轮从正面切为负面，第三轮只收敛 logistics；均保存结果引用和 scope。后两轮规划模型调用为 0，但没有跳过校验、压缩或回答组装。此时物流负面是 0，回答明确数据不足，不展示包装作为物流结论。

'''+link('两页物流证据完整任务',BROWSER/'phase7-evidence-pages.json')+'；'+link('真实 Worker 失败与安全追问',BROWSER/'phase7-failure-recovery.json')+'。\n\n![物流证据 390px]('+artifact(BROWSER/'phase7-logistics-next-page-390.png')+')\n\n![优先级澄清 1024px]('+artifact(BROWSER/'phase7-priority-limit-1024.png')+')')
add('''## 14. BLOCKED / SKIPPED 与能力边界

- BLOCKED：超时供应商请求的 usage/实际账单不可可靠取得，不能确认完整费用。
- SKIPPED：没有追加真实供应商的 API→Worker→React 一体化付费 E2E；当前证据是后端真实供应商 + 独立模拟供应商全链路，两者明确分开。
- SKIPPED：生产品牌商品答案、生产标注准确率、长时间真实配额/宕机、百万级与多用户压力、硬断电演练，均未验证。生产库只做本机只读探测。
- SKIPPED：Mermaid SVG 导出，环境缺少 mmdc；报告保留可编辑 Mermaid 架构图，未为此新增依赖。渲染失败日志保留。
- 不支持：正向趋势/复杂正向比较、制冷/静音/安装独立数量、销售/营收/库存、从评论证明因果、无业务依据的确定优先级。
- 有限能力：规则处理明确六维/情感/时间/分页；模糊指代先澄清。品牌名称不会自动验证为目录商品，外部写库需刷新目录。当前会话状态在浏览器，默认 API 不是多租户隔离服务。
- 仍有既有打包 chunk 警告与测试依赖弃用提示；未以删测试、放宽数字或增加模型超时消除这些现象。

## 15. 演示与公开部署前置条件

可以在本机用明确选择的真实数据范围或标注清楚的 DEMO 数据演示：好评/差评维度、两维月度统计、只读证据分页、冻结历史、确定性多轮、失败隐藏与解释降级。演示前检查数据库、Worker、模型额度及数据覆盖率，避免把少样本/缺标注解释成业务总体。

尚不具备直接公开部署的完整条件。需另阶段补齐认证/授权/任务所有权、请求与模型费用预算、数据访问控制、并发/容量与长期故障验收、供应商账单/监控、备份恢复及日志/数据保留。当前结果不构成生产稳定性承诺。

复现（在项目根目录的同一 PowerShell 会话；真实模型会消耗额度，仅在明确需要时运行）：

```powershell
$env:PYTHONUTF8 = '1'
$env:SSLKEYLOGFILE = $null
.venv\\Scripts\\python.exe -m scripts.phase7_services start --output output/agent-refactor/phase7-new
$phase7Services = Get-Content output/agent-refactor/phase7-new/services.json -Raw | ConvertFrom-Json
$env:TEST_POSTGRES_ADMIN_URL = $phase7Services.admin_url
$env:TEST_REDIS_URL = $phase7Services.redis_url
$env:LABEL_EXECUTION_MODE = 'local'
$env:PYTHONPATH = "$PWD;$PWD\\src;$PWD\\tests"
.venv\\Scripts\\python.exe -m unittest discover -s tests -v
.venv\\Scripts\\python.exe -m scripts.phase7_evaluation --output output/agent-refactor/phase7-new/offline
# 下行显式使用当前配置的真实供应商；有 12 题/36 请求等硬预算。
.venv\\Scripts\\python.exe -m scripts.phase7_evaluation --live --output output/agent-refactor/phase7-new/live
.venv\\Scripts\\python.exe -m scripts.phase7_performance --output output/agent-refactor/phase7-new/performance
npm --prefix frontend test -- --maxWorkers=2
npm --prefix frontend run build
.venv\\Scripts\\python.exe -m scripts.phase7_services stop --output output/agent-refactor/phase7-new
```

浏览器复现：在独立集群中创建一个名称以 `agent_test_` 开头的空库，把其连接放在进程环境 E2E_DATABASE_URL，设置 E2E_PHASE7=1，执行 `node frontend/tests/assistant.e2e.mjs`。脚本自行启动现有 Vite/API/Worker 与受控模型，结束时关闭自身子进程，结果写入 output/playwright。不要把该变量指向业务数据库。

本次原始产物位于 output/agent-refactor/phase7-acceptance，截图/真实任务位于 E5 目录；其中可能包含业务统计，保持本机审计用途。当前阶段结束，停止后续功能开发。
''')
(ROOT/'docs/agent-refactor/final-acceptance-report.md').write_text('\n'.join(parts),encoding='utf-8')
print('report assembled from checked artifacts')
