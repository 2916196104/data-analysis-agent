# P3-A 意图识别与证据许可优化

2026-11-05。本阶段完成代码和合成端到端验证，未执行真实数据库查询、真实模型编码、外部模型调用或 B3-B。**合成引用回答已通过；真实业务效果是否改善，尚未验证。**

## 定位依据

核对 P2 报告及已保存执行结果：5 个语义入口中，长期耐用问题在商品识别阶段退出；另外 4 个进入检索，均未形成公开引用。首问相关自述被拒绝，主要来自旧许可规则的两个约束：quote 必须与问句命题逐字相等；完整评论的每句话都必须属于旧的有限语句集合。效果与速度、单事实与同评论复合事实的区别仍须保留，不能把另外两问的拒答直接认定为错误。

`product_mentions()` 原来把存在性前缀和“空调”合并成商品名，并可能把属性描述追加到名称。`_literal_target()` / 原 `assess_retrieval()` 仅能处理字面陈述。P0 的编码、检索和来源验证不需要为本轮修改。

## 修改文件与实际链路

| 文件 | 作用 |
|---|---|
| `src/data_analysis_agent/agents/review_semantics.py` | 分离存在性提问前缀、通用品类否定及属性描述；先识别目录中的完整名称/ID；目录名称内部的“和、与”等不作实体分隔符。保留已有范围绑定和未知商品错误。 |
| `src/data_analysis_agent/agents/review_reported_facts.py`（新增） | `reported_experience_v2`：完整子句识别、有限同义归一、有方向的蕴含、上下文否决、同评论事实分组、事实与原始引用绑定。 |
| `src/data_analysis_agent/agents/review_retrieval.py` | 新请求使用 v2；识别“有无/是否存在”入口；保留旧 v1 实现，历史结果按记录的政策重算，不自动升级旧许可。来源校验逻辑未放宽。 |
| `src/data_analysis_agent/agents/review_answers.py` | 回答前重验事实集合、同评论关系、引用 key/评论 ID/区间/quote 摘要；继续输出准确原文，保留产品、日期、来源校验。 |
| `tests/test_review_reported_facts.py`（新增） | 9 项合成规则测试，多组正例、未见组合和反例。 |
| `tests/test_review_semantic_integration.py` | 新增 5 项真实 Agent 控制流的合成测试，使用 FakeEncoder/FakeRepository，未改已有 28 项断言。 |
| 本报告 | 实现、测试、能力边界和后续最小真实回归。 |

运行路径仍为 `run_review_agent → scope/plan → search_semantic_evidence → SemanticRetrievalService → 编码/检索/独立来源补齐 → assess_retrieval → 传输验证 → public analysis → semantic_answer`。生产编码和仓库没有替换；合成测试只注入已有 fake 接口。

私有测试驱动及日志在 `outputs/rag-semantic-retrieval/p3a-20261105/`，已验证被 Git 忽略。没有新增依赖、审批步骤或审计平台。

## 许可含义与边界

本实现只许可“本次检索中存在买家明确作出的这类自述”，不能据此断定客观性能、故障原因、发生频率、长期寿命或总体质量。

- 支持来自 quote 的完整主谓/体验子句，使用 `fullmatch`，不因出现关键词或向量距离小就许可。可识别明确第一人称框架，如“我感觉制冷不给力”；同义表达归为 `cooling_poor` 等具体事实。客服与安装师傅、制冷与制热分别建模。
- 有方向的规则仅包含“明确不制冷/不能降温 → 制冷体验不佳”和对应制热关系。不反推完全失效，也不推出速度慢、睡眠影响或故障原因。
- 全文只能否决，不能增加支持事实。quote 外的外观、配送等无关句子不再要求全部匹配旧语法。疑似转述、假设、否定作用范围、纠正、反讽、时序变化、同对象冲突及未识别的同主题限定仍保守拒绝。否决词扫描有误拒风险，不代表完整的上下文理解能力。
- 只实现“同一评论既说 A 又抱怨/说 B”的明确双事实共现；每项都需要候选 quote 支持，以相同 `review_id` 分组，不能拼接不同评论。仅存在于完整评论、未被候选 quote 覆盖的第二事实不能补足条件。其他 all/any、先后、因果、转折解释及独立子问题组合尚不自动许可。
- 此版本不输出部分许可 `limited`；完整目标未获支持则保留 insufficient，逐候选判断仍为 unknown。`query_semantics_uncovered`、`context_unresolved`、`semantic_not_verified` 分别解释覆盖不足与无法验证，不能解释为事实不存在。
- 检索空返回 `no_candidates`；服务错误仍为 error，且不生成正常答案。正常执行但许可不足时沿用既有 `PublicAnswer.status=ok`，回答明示无法确认；充分性状态在 `assessment.status`，不混同执行状态。

每项许可记录 `fact_bindings`：事实 ID、opinion key、review_id、原 quote 起止位置及 SHA-256；关系记录评论 ID。公开证据继续携带 annotation_id、有效快照摘要及文件/行来源。自然语言仅引述准确原文，并限定为买家自述；不从上下文生成额外正向结论。

旧 `literal_reported_statement_v1` 的语义判断保留，用于历史回放；R0 的 `evidence_judge.py`、B2、人工裁决及既有审计结果均未修改。

## 实际测试

执行命令：

```powershell
$env:PYTHONPATH='src;tests'
.venv/Scripts/python.exe -B -m unittest test_review_reported_facts test_review_semantic_integration -q
.venv/Scripts/python.exe -B outputs/rag-semantic-retrieval/p3a-20261105/run_checks.py
git diff --check
git check-ignore outputs/rag-semantic-retrieval/p3a-20261105/run_checks.py
```

| 检查 | 结果 |
|---|---|
| 最终专项：P3-A + P1 + P0 | 70/70，通过，无跳过 |
| 适用回归（包含上述专项） | 471/471，通过，无跳过；不能与 70 相加算不同测试 |
| 两项既有 SQLite/PostgreSQL 队列测试 | 0/2，2 errors，未算通过、未改队列业务 |
| 外部 HTTP、真实 PostgreSQL | 驱动禁止连接，未调用 |

两项遗留错误分别是 `test_run_date_filters_incremental_skip_and_scoped_progress`、`test_run_retry_and_cutoff_forwarding_with_real_mocked_sdk`，仍报 `ReviewDataError: 持久标注队列只支持 PostgreSQL。`。回归涉及 SDK 的本机 socketpair，需要在现有 Windows 执行权限下运行；没有关闭驱动对外部 HTTP/真实数据库的拦截。

复验中曾发现新增目录标题保护对 `sqlite3.Row` 使用 `.get()` 的兼容性错误，已改为原有字段下标访问；失败日志另存 `regression-before-row-fix.log/json`，表中为修复后的最终结果。不是忽略失败后计算通过率。

重点验证结果：

1. 使用全新合成上下文的同义体验，经 Agent、P0 fake、许可、压缩和回答全链路生成原文引用，包含事实绑定，模型调用数为 0。
2. 长期耐用问题不再被误认成商品名，原问句到达 fake encoder；因长期事实仍未覆盖而明确不许可。没有为了增加答案数把短期描述当长期证据。
3. 同评论的两个 quote/完整复合 quote 可支持共现；跨评论、只满足一个条件、第二事实只在上下文中的情况不许可。
4. 否定、转述、假设、纠正、相反事实、错误服务对象、错误细分主题、仅主题相关、未知措辞均有反例。无关外观/配送上下文不阻断明确事实。
5. 篡改事实 ID、review_id、quote、区间、摘要或来源被拦截；旧 v1 历史记录不因新规则而自动获得许可。
6. SQL 统计、实体绑定、范围与日期约束、检索空/服务失败、P0 来源校验及 R0 等适用回归通过。

## 仍未覆盖及最小运行时扩展建议

此规则集仍很有限：自由叙事、隐含主语、长距离指代、时间变化、纠正后有效事实、细粒度故障原因、复杂否定、任意命名商品出现在问句命题中等，可能得到 unknown。实体提取也仍是目录与规则方式，未声称具备通用 NER。

若真实回归显示这些表达是主要阻塞，最小下一步是在现有 `assess_retrieval` 位置接入一个显式启用的结构化运行时判断调用，复用项目已有模型接口，输入仅为原问题、目标事实、经 P0 校验的 quote 和否决上下文。输出逐事实 support/contradicted/unknown、精确 quote 引用、必要关系及理由；确定性代码继续校验来源、区间和同评论约束，错误或超时不许可，最后仍用原文模板回答。不得让模型从上下文补足支持，也不得将结构通过称为语义已证明。**本轮没有实现或调用此模型路径**；是否适用及效果需另行验证，无需新建审批或历史可信平台。

## 最小真实回归方案（尚未执行）

建议单独批准后沿用 P2 已验证的本地模型子进程注入入口和原限定 scope：reviews_csv / reviews-csv、商品 100013588055、UTC 2021-09-11 至 2022-08-26、原标注身份/EmbeddingSpace、200 条已有向量。只读来源补齐仍可能读取范围内完整有效语料，不能声称只读 Top-5。默认业务 Python 的模型依赖/缓存配置问题未在本阶段解决。

最多重跑 P2 相同的 5 个语义入口与 1 个 SQL 问题，不增索引、不改评分、不调用外部 LLM：

1. 制冷不足：核对是否出现真实许可与准确引用，逐条看是否仅表达买家反馈。
2. 十年耐用：核对进入检索而不是商品错误；证据无长期支持时必须不许可。
3. 制冷不快：效果差不应自动许可为速度慢。
4. 同评论制冷好/噪音大：两项候选事实和同评论关系均须成立。
5. 原缩小日期的空候选场景：仍不声称全量不存在。
6. 原 SQL 好评维度统计：数值与既有口径兼容。

每问保存独立新结果、原始排名、事实许可/拒绝原因、引用及分阶段耗时；区分检索、许可、回答、性能失败。真实来源若校验失败立即停止，不以跳过来源校验换取答案。本阶段在代码与合成验证完成处停止，等待该真实回归的单独授权。
