# P6-A 多维度负面主题下钻契约冻结验收

日期：2026-11-09。范围：任务契约、确定性选维、纯函数验收和离线测试。依据：P0-B、P1-A、P1-A 独立验收、F01 修复验收及 P6 实施准备五份报告和当前源码。

## 1. 阶段结论

**P6-A 契约冻结完成。P6-B 可以按本契约实施 Planner；多维度工具执行、真实分类及生产准入仍未开放。**

本阶段新增独立的 `review-request-goal-p6-v1`，冻结最多三个必做维度、完整历史排名与来源、选择规则版本和选择摘要。独立验收拒绝缺失、重复、替换、重排维度、错误能力、错误范围/情感及未经验证的完成声明。旧 RequestGoal v1、现有业务执行入口、任务状态枚举和冻结 RAG 契约均未修改。

目标对话在本阶段表现为**离线契约预览**：第一轮可信排名 → 第二轮“具体体现在哪里？” → 冻结 service、effectiveness、logistics 三个必做主题目标。没有将新模块接入 Router、Worker 或 `run_review_agent()`；实际线上/页面的第二轮仍走原有澄清行为，不能声称已上线多维度下钻。

## 2. 审计与信任边界

| 审计模块 | 已确认事实 / 本阶段处理 |
| --- | --- |
| `jobs/analysis_state.py` | restore_ranking 重新检查保存时摘要、P0-B 验收、数字/来源、单 SKU、negative、未限定 aspect、当前窗口和完整六维排名；复用，未改动 |
| `jobs/analysis_context.py` | 同会话、有界父链、标注身份与选择范围检查保留；不接受客户端排名作为认证输入；未改动 |
| `agents/review_intent.py` | F01 的 short_aspect / short_sentiment 承接待完成主题仍有效；本轮不接入 P6 触发分支 |
| `agents/review_plan.py` | 已支持多个同名工具的不同参数要求；复用精确参数匹配，不修改现有 Planner |
| `agents/review_acceptance.py` | RequestGoal 在编译前独立生成；v1 字段、asdict、业务语义和 Validator 保持原样 |
| `agents/reviews.py` | 当前请求冻结、工具受计划约束、最终任务验收及范围投影均保留；不启动 P6 工具循环 |

新入口 `freeze_p6_goal()` 消费**服务端任务库读取的原始 ranking_task**、当前 conversation_id、当前 Agent 身份、最终 scope 和受控 context。它不接受客户端 ranking_basis，也不将 context 中附带的一份排名字典作为可信来源。每次冻结和独立计划验收都会重新调用 restore_ranking；仅解析 P6 JSON 或计算一个自洽哈希不能授予来源资格。

任务原始参数中的会话和 Agent 身份、结果中的 Agent 身份均须与本轮一致；完整标注身份则包含在最终 BaselineScope 的精确比较中。旧任务缺少封存摘要、来源被篡改、业务验收不通过、过滤排名或正向结果均不得触发。

这里延续 P1-A 的信任边界：原始任务必须由服务端加载，不能让 API 调用者提交任意 task 字典。SHA-256 是完整性检查，不是签名，不能防御能够同时重写任务、结果和摘要的数据库特权写入者。本轮未改造这一认证机制。

## 3. 触发与回退契约

处理顺序固定为：

1. 独立读取本轮原始用户产物要求和受控历史条件，不访问 Planner。
2. 优先保留单维度、数量、原文、正向、趋势、比较、质量、评分等明确请求。
3. 仅对明确的负面具体问题/主题深入请求重新核验来源任务。
4. 要求最终范围与可信排名的完整 scope、scope_sha256 相同，单 SKU、negative、未限定维度、完整六维。
5. 按冻结规则选维并生成不可变 P6 目标；任何后续计划只能编译该目标。

允许的具体表达包括“具体体现在哪里？”、“这些差评具体有哪些问题？”、“深入分析这些负面主题”。对 P6 专用的“具体问题/主题深入”识别只存在于新入口，不改变旧 v1 的文本解析行为。短情感引用本身不等于明确主题深入请求，继续保留 F01 原路径。

`scope` 的含义是**已绑定的最终查询范围**，不是页面原始选择。P6-B 调用方必须先完成商品/日期绑定，再提供该范围；新模块不猜测商品别名、不扩展日期、不替代已有范围解析器。

| 内部 route | 条件 / reason | 下一步契约 |
| --- | --- | --- |
| p6_contract | historical_goal_frozen | 仅获得历史目标契约；不等于获得执行许可 |
| existing | explicit_goal_priority | 交回已有单维度/统计/原文/正向/其他目标路径；该路径不支持时沿用原有澄清或拒绝 |
| clarify | missing_trusted_ranking / ranking_scope_mismatch | 工具执行前澄清或要求刷新排名，不猜维度或扩大范围 |
| reject | session_or_agent_identity_mismatch / unverified_ranking_source / invalid_ranking_source | 受控拒绝，不能恢复跨会话、跨身份或篡改来源 |
| reject | no_negative_evidence | 无正计数维度，不创建空必做集合，不发生“空集合全部完成” |

这些是新模块内部决策，不是新增 API 状态。所有入口均为纯计算，不执行数据库查询、Router、工具或模型。

## 4. 确定性选维与当前数据门槛

选择规则版本：`negative-count-desc-aspect-asc-top3-v1`。

```python
required_aspects = tuple(
    row['aspect']
    for row in sorted(ranking, key=lambda row: (-row['negative_review_count'], row['aspect']))
    if row['negative_review_count'] > 0
)[:3]
```

选择器先验证完整六维集合、合法整数计数、计数不超过 D、统一 successful_reviews 分母，以及原历史排名的顺序和 rank=1…6。输入排序错误会被拒绝，不能在忽略历史异常后静默修好排名。选择最多三个；不足三个只保留实际非零项；并列时按 aspect 字符串升序；零项不补位。

合成示例：D=6；service=5、effectiveness=4、logistics=4、quality=2、price=1、packaging=0。期望为 `service, effectiveness, logistics`；后两项并列仍有唯一顺序。

选择摘要绑定以下完整对象的规范 JSON SHA-256：

```text
selection_basis = {
  selection_rule_version,
  ranking_basis: {source, scope, scope_sha256, sentiment, denominator, ranking[全部六行]},
  required_aspects[稳定顺序]
}
```

`check_current_ranking()` 接受服务端当前快照 `{scope, denominator, negative_review_counts}`，要求完整范围、D 和全部六个计数逐项相同，包括未入选的第四至第六维。缺少快照、范围失配、D 或任一计数变化均不能通过父级验收；不会基于当前数字重新选前三名。每个主题结果的全量匹配评论数 total 还须等于相应当前维度计数 M_a。

**当前阶段只实现比较函数，没有读取当前业务库。** P6-B 必须在工具/分类执行前读取同一只读快照并通过比较，再在同一快照预读观点；不能等分类结束后才发现排名过期。最终验收再次检查输入快照，但它本身不能证明调用方已实现同快照读取。

D 与六维计数一致仍不能证明所有正文或人工修订版本完全未变；本次分类只能声明使用本轮冻结观点，不宣称精确复现历史正文版本。

## 5. 最终 P6 RequestGoal 数据契约

`P6RequestGoal` 是独立版本的 frozen dataclass，继承原 RequestGoal 的字段而不修改原类。嵌套选择依据以规范 JSON 字符串封存，公开属性和 to_dict 返回副本，避免通过可变 dict/list 修改目标。

| 字段 | 固定含义 / 约束 |
| --- | --- |
| version | review-request-goal-p6-v1 |
| 原 v1 目标字段 | original_question 保留原问题；levels=[themes]；aspect=null；sentiment=negative；topic=null；statistic_families=[]；source=user_request |
| analysis_mode | multi_aspect_negative_drilldown |
| required_aspects | 一至三个、唯一、稳定有序；运行中不可缩减、替换、增加或重排 |
| ranking_source | task_id、result_id、task_sha256、result_sha256、validation_sha256、scope_sha256、integrity_version、finished_at、snapshot_at |
| selection_basis_sha256 | 上节完整选择依据的摘要 |
| per_aspect_level | themes；每一个必做维度均要求主题产物 |
| expected_scope | 完整 BaselineScope，含商品、来源/数据集、标注身份、当前日期、基线和 as_of |
| selection_basis | 完整六维排名、来源、分母、范围及选择规则版本和结果 |

`to_dict()` / `from_dict()` 只处理 P6 版本，拒绝字段/版本不一致或摘要失配；from_dict 只提供结构恢复，不授予信任。Validator 还须从原始任务重建期望目标，逐项比较整个 P6 目标，不能只校验传入哈希是否自洽。

本轮生成的真实离线契约预览（只摘列核心字段，完整数据见 `.cache/p6a/contract-example.json`）：

```json
{
  "version": "review-request-goal-p6-v1",
  "original_question": "具体体现在哪里？",
  "levels": ["themes"],
  "sentiment": "negative",
  "analysis_mode": "multi_aspect_negative_drilldown",
  "required_aspects": ["service", "effectiveness", "logistics"],
  "per_aspect_level": "themes",
  "selection_basis_sha256": "a57924501a9fea9dc67f50a9057ac102d8d94118c28f6c27e9a13f1bca0d68e2"
}
```

该示例的来源任务为 `6fb3fd3d2d5547b3993d5b9754c02a4a/r1`，是本轮临时 SQLite 产生的任务，不是生产来源。完整证据文件 SHA-256：`cb550f712c4287edc645decb84cdecf0700e1447d03bf289aea970e7b202cfeb`。

## 6. Planner 与独立验收契约

P6-B 编译目标时继续使用已有 `ReviewQueryPlan.analysis_type=aspect_drilldown`，新模式由 P6 RequestGoal.analysis_mode 区分。计划中须按 required_aspects 的顺序恰好生成 k 个 `get_aspect_drilldown` 要求，参数为最终单 SKU、该 aspect、sentiment=negative；不允许追加不同能力、可选工具、基线窗口、主题过滤或扩大/缩小执行范围。已有 overview 查询仍按原框架作为受控上下文查询处理，不加入必做主题集合。

`validate_p6_plan()` 从原始 ranking_task 独立重算期望目标；验证范围、情感、计划能力、精确维度集合和顺序，**不读取计划自报的集合来确定应该做哪些维度**。它没有修改现有 Planner，也没有编译可执行的多维任务入口。

`validate_p6_completion()` 要求完整 outcomes 列表，每项为 `{aspect, status, results}`。缺失、重复、替换维度直接失败。单个完成维度必须恰好包含一个正确的主题结果；重复同一结果也失败。

每个声称 complete / insufficient_evidence 的维度都独立构造精确单维度子目标和子计划，复用未经修改的 P0-B `validate_task_completion()` 与原数字/来源/引用/主题 Validator。检查工具、negative、范围、完整分类、全量匹配数、主题去重计数、S_a 分母、样本覆盖与原文支持。不接受单纯的 complete 字符串或统计结果作为完成证据。

错误引用、主题分母错误、分类 partial/失败使该维度不能发布；范围、身份、维度/情感来源或结构边界错误导致父级硬失败，不允许以部分发布绕过。受支持的原文位置并不证明主题语义正确，沿用模型候选、未经人工确认的标记。

父 complete=true 当且仅当：独立目标/计划通过、当前排名一致、全部必做维度恰好出现且每一个都独立完成并通过证据验收。未完成维度不能被从目标中移除。

## 7. partial 发布与外层状态映射

子级内部状态固定为以下四种；子级不接受 partial 作为已完成分析：

| 子级 status | 含义 / 发布资格 |
| --- | --- |
| complete | 完整主题产物及证据独立通过，可以进入可发布集合 |
| insufficient_evidence | 查询/分类结构正确但没有足够支持主题；独立验收必须确认 insufficient_evidence，不算完成、不发布主题结论 |
| failed | 工具/分类/验证失败；不发布该维度 |
| skipped_budget | 共享预算不足而未开始；不得靠删除目标或采用迟到产物补成完成 |

父级返回 required_aspects、完整 per_aspect 列表、publishable_aspects、plan_check、current_ranking_matches 和 outcome_coverage。required_aspects 始终保持原集合，可发布子集只是独立结果，不成为新目标。

| 父级内部 status | 条件 | complete | Agent 状态映射 | 任务库状态映射 |
| --- | --- | --- | --- | --- |
| complete | 所有必做维度独立完成 | true | ok | succeeded |
| partial | 至少一维独立完成，尚有其他必做维度未完成，且无全局边界错误 | false | partial | partial |
| insufficient_evidence | 全部维度均为正确验收后的证据不足 | false | unsupported | succeeded |
| skipped_budget | 全部维度均因预算未开始 | false | error | failed |
| failed | 全局校验失败，或无可发布维度且并非全部证据不足/全部预算跳过 | false | error | failed |

这是**内部验收结果和未来的映射约定**；本轮没有修改 Worker 的状态写入或 API 枚举。内部 complete/insufficient_evidence/skipped_budget 不作为外层新枚举。

P6-B 应把 complete=false 保留在 P6/task_acceptance 内部。不能机械地复制为 result 顶层 complete=false：当前通用 Worker 会据此将任务判为 partial，包括本应 unsupported/succeeded 的证据不足。真正父 partial 由现有 Agent.status=partial 映射到任务 partial；还需满足 Worker 原有 validation 检查。当前 UI 对 partial 的展示能力不因此自动完成。

## 8. 预算与阶段边界

本阶段 P6 纯函数的工具调用、数据库查询、主题分类、意图/规划/回答模型和真实 Embedding 均为零。测试只为创建来源排名运行旧单轮统计路径，使用临时 SQLite；之后封锁工具、Router 和分类入口，并在每个测试清理时断言从未调用。

P6-A 冻结后续预算边界，**未实现执行计量器或费用授权**：

| 项目 | P6-B 及后续实施约束 |
| --- | --- |
| 维度 / 工具 | k≤3；overview 1 + k 个主题工具，最多 4，不隐式增加工具 |
| 候选输入 | 每维最多 20 种表达、200 个观点，保留读取至多 100 条评论的现有边界及覆盖记录 |
| 主题模型 | 每维发现 1 + 复核 1，最多 2；整轮最多 6，失败尝试也计入；禁止自动补调/重试 |
| 其他模型 | P6 分支意图、规划、回答模型均为 0，确定性编译和模板输出 |
| 时间 | 单次≤20 秒且不超过配置和剩余预算；每维≤40 秒；共享分类预算≤120 秒；不等同含排队/SQL的硬任务耗时上限 |
| 字节 / 输出 | body≤16000 UTF-8 字节；发现≤1200、复核≤3000 输出 tokens；三维最多预留12600输出tokens，不静默截断输入 |
| 费用 | 必须另有明确授权额度 C、可靠输入 token 上界、模型单价/计费类别；未知则阻断；失败/缺失 usage 保留预留，不能按零费用释放 |
| 截止 / 失败 | 截止后不能启动或接纳迟到结果；鉴权/额度失败停止后续付费尝试；预算不足保留 skipped_budget 维度 |

上述数值来自 P6 实施准备，已作为下一阶段约束写入本报告，不代表已验证真实模型的延迟、取消能力或账单。未调用收费模型、实时价格接口或生产数据库。

## 9. 修改文件、验证与兼容性

新增文件：

- `src/data_analysis_agent/agents/review_multi_aspect.py`：P6RequestGoal/P6Decision、冻结、纯选择器、当前计数比较、独立计划与父/子完成验收。
- `tests/test_p6a_contract.py`：19 项离线契约测试。
- `docs/P6-A-contract-and-selection-acceptance.md`：本报告。

没有修改任何入场已有文件。五份输入报告、P0/P1/F01 测试、旧 RequestGoal/Planner/Worker、前端和冻结 RAG 源码与评审资产均保持原样。缓存证据单独保存在 `.cache/p6a/`。

| 验证 | 覆盖 |
| --- | --- |
| 0/1/2/3/4/5/6 个非零维度 | 零项受控拒绝；不足三项不补零；超过三项稳定截取 |
| 并列 / 错序 / 缺维 / 重复 / 无效数值 | 确定性 aspect 升序；损坏排名拒绝 |
| 来源及客户端注入 | 原始任务篡改、旧未封存、正向/过滤来源、业务目标错误拒绝；客户端式排名字典不能覆盖来源；自洽伪造选择哈希也不能通过独立来源验证 |
| 会话、Agent、标注身份和范围 | 跨会话、Agent 身份、SKU、日期、来源、数据集、模型、端点、提示词、基线、as_of 变化阻断 |
| 明确请求优先 | 单维度、数量、原文、正向、趋势、比较、混合目标在来源查询前返回原路径 |
| 目标不可变与独立 | frozen/副本隔离/JSON往返；构造目标不调用 Planner；错误 Planner 不能改变目标 |
| 缺失、重复、第四维替换、重排、仅统计 | 计划和实际产物两层均拒绝通过 |
| 逐维证据 / partial | 正确产物完整通过；引用/分母/分类错误不发布该维；首/中/末失败/证据不足/预算跳过均保留完整目标；全局边界错误不部分发布 |
| 数据新鲜度比较 | D 或任意一维计数变化阻断；total 与当前维度全量计数失配拒绝；不重新选维 |
| v1 / API 兼容 | v1 原字段和序列化不变；API 参数继续拒绝客户端排名、模式和必做集合 |

功能验收以 `.cache/p6a/functional-summary.json` 和完整日志为准：原 450 项清单，加独立 P1-A 11 项、F01 13 项和本轮 19 项，共 **493 个不同测试**；测试期间禁止 socket / psycopg 外部连接。静态私有接口审计单独执行，历史 13 个失败子项继续保留，不作为功能通过项，也不在本轮清理。

| 最终检查 | 结果 |
| --- | --- |
| 原 450 项适用回归 | 450/450 PASS |
| test_p1a_independent_validation | 11/11 PASS |
| test_p1a_f01 | 13/13 PASS |
| test_p6a_contract | 19/19 PASS |
| 上述合并完整运行 | **493/493 PASS**，146.055 秒，0 failure / error / skip |
| 其中原专项 | P0-A Router 10/10、P0-B Acceptance 14/14、P1-A trusted state 18/18，均已计入原 450 项 |
| 私有接口审计 | 独立 FAIL：1 个方法内 13 个历史失败子项，0 error / skip |
| Python 语法编译、git diff --check | PASS |
| 入场 1588 个已有文件哈希核对 | 全部未变、无删除；仅新增本报告列出的三份交付文件 |

本轮先运行新增契约测试，再运行合并套件；计划类型改为复用 aspect_drilldown、补充缺失范围的拒绝检查后，重新执行最终合并套件。没有修改或删除旧断言，没有 expectedFailure 或新增 skip。

复跑（仓库根目录 PowerShell）：

```powershell
$env:PYTHONPATH='.;src;tests'
.venv/Scripts/python.exe -m unittest test_p6a_contract -v
.venv/Scripts/python.exe -m unittest test_review_router_p0a test_review_acceptance_p0b test_trusted_analysis_state test_p1a_independent_validation test_p1a_f01 -v
.venv/Scripts/python.exe .cache/p6a/run_checks.py functional
.venv/Scripts/python.exe .cache/p6a/run_checks.py static
.venv/Scripts/python.exe .cache/p6a/probe.py
.venv/Scripts/python.exe -m compileall -q src/data_analysis_agent/agents/review_multi_aspect.py tests/test_p6a_contract.py
git diff --check
```

static 命令预期继续报告 13 个历史私有导入子项失败。probe 为离线契约和固定产物预览，不执行多维度工具。

## 10. P6-B 准入与停止点

**P6-B 的契约准入：PASS。** 原始用户目标独立性、可信排名入口、选择规则、不可变必做集合、逐维/父级验收及内部 partial/外层状态映射已明确，可据此实施确定性 Planner。

下一阶段仍须将最终范围和原始可信来源绑定到实际请求，接入精确计划授权和当前统计/同快照预读；分类前完成共享预算及执行顺序控制，并按完整目标验证。部分公开产物、引用公平分配、UI/复制/导出和真实质量验证仍是各自后续工作，不能由本次合成产物验收替代。

多维度执行及收费验证准入仍未开放：未有同快照执行闭环、共享预算执行器、真实费用授权或真实质量证据。本阶段未读写生产库、部署、实施前端、调用收费 LLM/真实 Embedding，未修改冻结人工评审资产。完成 P6-A 后停止，不自动进入 P6-B。
