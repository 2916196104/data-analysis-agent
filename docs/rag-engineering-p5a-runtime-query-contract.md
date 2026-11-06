# P5-A：统一运行时查询契约与任务完成校验

2026-11-06。已在原 Agent 链路接入运行时目标契约，修复通用商品指代、细分占比/原因任务误路由、日期前缀阻断事实判断的问题。本轮仅运行合成测试、隔离 SQLite 夹具和本地合成子进程；没有读取业务 PostgreSQL、加载 BGE-M3 或调用外部 LLM。合成改善不代表真实业务效果已经改善。

## 接入方式

沿用现有流程：

```text
原问题 + 页面/会话范围
  → 原 ReviewIntent / 商品目录匹配 / 日期绑定
  → 原 ReviewQueryPlan + RuntimeQueryContract
  → 原工具注册表执行 SQL 或 SemanticRetrievalService
  → 原数值、来源、引用校验 + task_matches
  → 原压缩器与回答生成器
```

新增 `review_task_contract.py`，复用 `AnalysisScope / BaselineScope`、离线 `query_contract.py` 的 `ContractModel / TaskType / SentimentIntent / RequiredFact / RequiredFacts / FactRelation`。没有复制离线 QueryContract，也没有伪造其 run_id、manifest 或 ScopeRef。

`build_task_contract(question, bound_scope, plan)` 在已有范围解析、意图/计划确定后生成目标快照；范围绑定、目标表示与工具执行分别处理。目标来源是用户问题和已有查询计划，不从工具结果反推。`resolve_review_request()` 绑定最终原问题与有效 scope，`run_review_agent()` 保存 `query_contract`，计划保存 `task_contract`。

| 字段 | 实际含义 |
|---|---|
| version | `review-runtime-contract-v1` |
| original_question | 原问题原样保留；仍作为 Embedding 输入 |
| evidence_question | 仅剥离明确的前置 ISO 起止日期，作为事实目标；不能任意改写 |
| scope / scope_sha256 | 商品、source、dataset_version、日期、as_of、标注身份及基线；不可变模型，使用已有范围校验，UTC |
| task_type / operation | statistics/evidence/mixed/clarify；SQL、事实检索、观点汇总、原因核查等操作类别 |
| target_entity / subtopic / sentiment_intent | 已解析对象、主题和方向；无法确定时保留 unknown |
| required_facts | 原 v2 已识别事实 ID；复用 all_of/any_of、same_review/before 结构，不因此增加许可能力 |
| statistics | 工具、指标名、取值路径、分子路径、分母定义及工具 definitions 字段 |
| support_status / unsupported_reason / uncertainty | supported、retrieval_only、unsupported；说明未覆盖或不能计算的原因 |

支持状态描述当前任务的处理能力，不是证据充分性状态。统计任务中的对象/主题从现有工具请求提取，例如 `review_opinion / packaging`；不把观点归组当作客观故障原因。对未覆盖的简单事实问题可检索，但不能许可确定结论；不能可靠处理的复合关系明确 unsupported，要求拆分或澄清。

## 修复及可复现证据

下表为合成回归，原 P5 的 15 个问题仍是已见案例。

| 问题类型 | 本轮实际行为 | 验证位置 |
|---|---|---|
| P05“这款商品有哪些优点和缺点？” | 不再把“这款商品有哪些”当名称；使用当前商品，执行正负观点汇总并回答 | 新专项测试、15 探针 |
| 通用指代和完整名称 | 支持“这台机器”“当前产品”“所选机型”等；目录中的完整名称优先作为整体，不按名称内的“与/和”拆分；未知商品和越界单商品继续要求确认范围 | 新专项、原商品绑定回归 |
| P10 制冷不足占全部评论多少 | 明确不支持：没有完整细分分子，不执行数据质量查询，也不返回上级维度或 Top-K 比例 | 新专项、15 探针 |
| 其他细分比例 | 客服态度差、包装破损、配送迟缓等同类表达停止错误统计；既有一级维度情感统计继续使用原口径 | 新专项、SQL 回归 |
| P12 压缩机是否导致制冷不足 | 返回原因核查不支持，不执行观点文本匹配替代因果证明 | 新专项、15 探针 |
| 普通内容归组 | 优缺点、维度内问题、物流环节归组继续使用原工具；“不要解释原因，只统计好评主要方面”不会被当成因果任务 | 新专项、证据补查回归 |
| P11 日期前缀 | 日期收窄后，原问题进入编码；无日期的事实目标进入原 v2；明确支持的合成引文得到引用回答 | 新专项、15 探针 |
| 日期越界/结果漂移 | SQL 与 RAG 均不能通过问题悄悄扩大当前日期；前置日期与检索 scope 不一致时拒绝，包括结果回放 | 新专项、范围回归 |
| 多事实/时间/否定 | 原 v2 能识别的同评论双事实保持支持；未覆盖的任一、先后、复杂否定明确 unsupported，不能猜一项条件执行 | 新专项、RAG 回归 |

原 15 探针现在均完成预期链路：10 个返回正常结构（其中可包含明确证据不足），5 个返回 unsupported；不能称为 15/15 语义准确。P01/P07/P11 的合成证据获得许可；P02/P13 的语义缺口、P04 的过宽上下文否决、P14 的转述保护和 P15 的空候选边界仍保留。

日期适配没有修改 `reported_experience_v2`：只将已绑定范围与目标命题分开。旧历史结果未存 `assessment_question` 时，仍按原问题与原许可策略验证，不升级旧结论。

## 任务匹配如何阻止答非所问

`validate_report()` 增加“查询目标匹配”，`build_analysis_artifacts()` 在发布前再次检查；历史公开读取重建并核对目标契约，而非信任已保存的通过标志。

- SQL：既有 `plan.satisfied()` 仍核对工具、参数及执行范围；新检查要求相应指标/分子路径存在，并核对工具的分母定义。数值/分母的实际算术仍由原校验器验证，零分母继续保留空值。
- 正向维度 `positive_rate` 的分子是该维度正向评论数、分母是范围内含正向观点评论数；`positive_success_rate` 的分母是成功标注评论数。负面率沿用成功标注评论分母。缺失正文率沿用评论总数，覆盖率沿用有效正文数，不互相替代。
- 专项测试将正向统计的分母定义改为 `successful_reviews`，数值本身仍合法，原数值检查通过，而新增目标检查失败；拿 overview 替代所需统计也失败。
- RAG：继续重算来源和原策略 assessment；要求目标事实 ID、all_of 与必要关系一致。未知目标不能带许可集合。候选排名/相似度不提供事实证明，也不能作为总体比例分母。
- 原因、已识别的细分占比和未覆盖复杂目标在执行前停止，避免发布“工具成功但回答了别的问题”的结果。
- 自动证据补查可以扩展执行计划，但保留 `query_goal_plan` 中的原始目标依据；不会因为补查增加工具就扩大用户问题含义。历史回放保留此区分。
- 没有新契约的旧历史继续按旧数值/来源流程读取，不补造“查询目标匹配已通过”。

这仍是确定性、有限覆盖的目标/结果一致性检查，不是通用自然语言理解或因果验证器。

## 实际文件变更

| 文件（src 均位于 data_analysis_agent/agents） | 变更 |
|---|---|
| 新增 review_task_contract.py | 运行时模型、能力边界、日期目标拆分、任务匹配 |
| review_intent.py、review_plan.py | 在原范围和计划解析后绑定契约；不支持目标提前停止；计划 JSON 序列化 |
| review_semantics.py | 通用指代/完整商品名处理、日期范围边界 |
| reviews.py | 将契约写入任务结果，保留现有 Worker 调用入口 |
| review_retrieval.py | 日期目标适配、scope 对齐、结果回放验证；不改许可规则 |
| review_results.py、review_answers.py | 传递并核对 assessment_question，保留事实/引文绑定 |
| review_validation.py、review_compression.py | 发布和历史读取的目标匹配 |
| review_evidence_loop.py | 补查时保留初始目标依据 |
| 新增 tests/test_review_task_contract.py | 11 项专项测试及多组表达/篡改反例 |
| tests/test_review_semantic_integration.py | 更新未覆盖复杂问题的预期：执行前 unsupported，不调用 Encoder |
| tests/test_assistant_semantics.py | 日期切换先明确绑定；增加旧行为扩大日期的拒绝断言 |
| 本报告 | 实现、测试和限制 |

未改 API、React、Worker 协议必填字段或 SQL 计算。结果仅增加可序列化字段；旧结果仍可读取。原先已暂存的 P5 报告保持原样。

## 测试与保护检查

可从仓库根目录运行：

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p test_review_task_contract.py -q
.venv/Scripts/python.exe -B outputs/rag-semantic-retrieval/p5a-20261106/run_checks.py
.venv/Scripts/python.exe -B outputs/rag-semantic-retrieval/p5a-20261106/probe_capabilities.py
npm --prefix frontend test -- --reporter=dot --maxWorkers=2
npm --prefix frontend run build
git diff --check
```

| 实际验证 | 结果 |
|---|---|
| 新契约专项 | 11/11 通过 |
| 后端适用回归 | 509/509 通过；包含上述 11 项、RAG/SQL、Agent、Worker 合成进程、API/历史公开读取及原离线协议，不重复累加 |
| RAG/运行时专项子集 | 85/85 通过，已包含在 509 中 |
| 原 P5 已见探针 | 15 个全部回放；预期 unsupported 也保留并检查 |
| 前端 | 25 文件、221/221 通过；TypeScript/Vite 构建通过 |
| 既有标注队列错误 | 单独重现 2/2 错误：SQLite 夹具调用已要求 PostgreSQL 的持久标注队列，未修改该业务逻辑，未计为通过 |

初轮回归暴露历史缺省情感/补查计划兼容问题，修正后通过；旧日期越界测试按本轮要求增加明确范围切换。前端首次与构建并行时一项渲染测试触发原 10 秒超时，未改代码或超时阈值，限制测试并行数后全量通过。保留 jsdom 伪元素、依赖弃用和现有 bundle 大小提示。

测试与探针脚本阻断真实 PostgreSQL 和外部 HTTP；合成服务使用 FakeEncoder/FakeRepository/FakeAgent 或 SDK MockTransport。私有脚本、合成输出、初始失败及最终日志位于 Git 忽略的 `outputs/rag-semantic-retrieval/p5a-20261106/`。没有重跑浏览器真实验收；前端未重构，本轮不将单元测试/构建描述为新的 P4 实测。

`review_reported_facts.py`、R0 `evidence_judge.py`、严格 `e1_preparation.py` 和离线 `query_contract.py` 与 HEAD 无差异。没有修改 B2、人工裁决、原评分、来源数据、向量或标注，也没有新增依赖、审批或审计平台。

## 限制与 P5-B 接入位置

自由改写、长期耐用、任一条件的实际许可、时间先后/复杂否定、局部上下文纠正仍未获得通用理解能力；细分主题总体比例缺完整分子，客观故障原因缺因果证据。200 条向量覆盖不变，本轮没有验证真实召回或语义效果提升。

P5-B 可沿当前契约和原工具入口增加独立版本的结构化证据策略：仅接收已明确目标、不可变 scope、当前候选 quote/区间与否决上下文，输出逐事实/关系及精确引用；由后端校验来源、区间、许可集合和任务完成情况。不能把模型结果冒充 v2 assessment 或关闭原重算校验。目标本身仍 unknown 时需要可靠解析或澄清，不能让证据模型自行改题再许可；SQL 比例与因果限制继续保留。超时、格式错误、未知关系保留不许可，不能默认放行。

本轮到此结束，未实施 P5-B，也未运行真实模型。
