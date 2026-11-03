# RAG Step 4B-2a：独立证据充分性判断器基础实现

日期：2026-11-04（Asia/Taipei）。**本阶段完成并停止；未执行真实 E1、未接入 Agent。**

本轮交付独立的来源校验、受限显式陈述识别、事实/关系约束判断和输出复核模块。新专项 38 项通过；组合回归 146 项中 144 项通过，另有两项既有 SQLite/PostgreSQL 队列错误。本实现只能对少量明确定义的中文陈述作保守判断，**不具备通用语义蕴含或任意自然语言理解能力**，不能凭这些合成测试宣称真实检索质量或 E1 已通过。

## 1. 阅读范围与文件变更

阅读了 [最终质量审查](rag-retrieval-quality-final-review.md)、[Step 4B 计划](rag-step4b-retrieval-strategy-plan.md)、[4B-0 协议草案](rag-step4b-phase0-readiness.md)、[4B-1a 契约报告](rag-step4b-phase1a-contract.md)，检查现有 `query_contract.py`、`rag_corpus.py` 的版本键与原文切片规则、`embedding_store.py` 的检索/来源校验、`retrieval_evaluation.py`、严格序列化工具及相关测试。

| 新增文件 | 实际内容 |
|---|---|
| [evidence_judge.py](../src/data_analysis_agent/reviews/evidence_judge.py) | 候选/输出结构、可信列表比对、来源校验、受限语义规则、联合关系约束和输出复核 |
| [test_evidence_judge.py](../tests/test_evidence_judge.py) | 38 项纯合成专项；用已有 `_document()` 构造兼容的合成版本键，无数据库/真实评测读取 |
| 本报告 | 实现能力、接口、限制、测试和真实 E1 前置事项 |

没有修改任何已有文件，包括 QueryContract、Agent/Supervisor/LangGraph、API、前端、检索排序/过滤、统计、模型、向量和数据库代码。私有检查材料位于 `outputs/rag-retrieval-quality/step4b-phase2a-20261104/`；没有提交或暂存私有数据。

## 2. 输入接口与来源安全

复用 4B-1a 的 `QueryContract` 和 `validate_query_contract()`，没有复制一套意图结构或改变其版本。

```text
judge_evidence(contract, batch, *, expected_scope, trusted_batch,
               independent_target_fact_ids=()) -> JudgeResult
validate_judge_result(value, contract, batch, *, expected_scope, trusted_batch,
                      independent_target_fact_ids=()) -> JudgeResult
```

contract/batch 可为对应模型、dict 或严格 JSON。模型输入也重新经过校验，不信任 `model_copy()` 或 `model_construct()` 绕过字段检查的结果。输入版本 `rag-evidence-judge-input-v1`；规则版本 `explicit-clauses-zh-v1`；输出版本 `rag-evidence-judge-result-v1`。

### 2.1 EvidenceBatch 与 EvidenceCandidate

| 结构 | 主要字段及约束 |
|---|---|
| EvidenceBatch | version、scope_ref、query_id、mode、candidates；mode 为 unfiltered/filtered，rank 必须连续且保持原列表顺序；最多 20 条为结构性资源上限，不执行或扩大 Top-K 检索 |
| 候选身份 | query_id、mode、rank、key、review_id、annotation_id、source、dataset_version、product_id、label_profile_key |
| 观点来源 | opinion_source、model_opinion_id、human_opinion_id、human_revision_id；校验模型来源与人工修订来源的互斥身份 |
| 原文与字符区间 | quote、quote_start、quote_end、review_text；区间为 Python 字符索引，不是 UTF-8 字节位置；quote 必须等于完整原文对应切片 |
| 哈希与版本 | review_content_sha256、embedding_input_sha256、effective_snapshot_sha256、text_builder_version=quote-utf8-v1、evidence_valid |
| 文件来源 | file_sha256、line_number、evidence_ref；要求与 `sha256:<file hash>#line=<line>` 精确一致 |

`expected_scope` 必须由调用方从可信冻结配置取得。`trusted_batch` 必须是另一条已核验来源路径提供的当前 query-mode 完整列表，不能从待测 batch 复制一份当作“可信证明”。模块不访问数据库、文件导入源或网络建立信任，只执行传入来源之间的确定性核对。

执行前逐项检查：

1. QueryContract 范围引用与独立 expected_scope 相等；输入批次和可信批次也必须使用相同引用。
2. 输入批次与可信批次完整、按序、逐字段相等，禁止插入、遗漏、替换、借用其他 query/mode 候选。
3. rank 连续、key 不重复，候选 query/mode 与所属批次相同，evidence_valid 必须为 true。
4. quote 字符区间及 UTF-8 SHA-256 正确，来源引用及观点身份合法；按现有 rag_corpus 的字段顺序重算 `rag-opinion-v1:` 稳定版本键。
5. 同一 source/dataset/product/review 的原文、内容版本、标注、快照、profile、文件及修订身份不得冲突。

review_content_sha256 和 effective_snapshot_sha256 必须与独立可信来源一致；本模块不把 review_content_sha256 误当作“仅对 review_text 字符串取哈希”，也不凭自报的 evidence_valid 证明在线新鲜性。没有独立可信批次或完整来源元数据时，不具备真实实验的来源准备条件。Step 4A review.json 没有这里所有来源字段，不能靠猜测补齐后直接使用。

### 2.2 独立子问题声明

QueryContract 的 required_facts 没有标记哪些原子条件可独立回答。为避免改变已交付契约，本模块接受查询侧的 `independent_target_fact_ids`，只允许无重复的已有事实 id；**默认空集合**。

这是人工/调用方对问题结构的显式声明，不是证据支持标签，更不是人工相关性评分。判断器不能自动证明其语义正确，真实 E1 前必须由人工确认。参与 before/same_review/contrast 复合关系的原子事实，本版本保守地不单独作为 limited 子答案；不猜测复合核心的哪一部分可以脱离关系独立成立。

## 3. 实际实现的判断能力

### 3.1 受限显式事实识别

模块包含与 query_id 无关的固定陈述规则，不包含冻结问题 ID、真实评论、0/1/2 标签、按题返回状态的答案表或距离阈值。下面是当前全部可识别的命题范围；它们是语言规则，而非对真实问题的预置答案：

| 对象 / 主题 | 可识别的完整陈述 |
|---|---|
| 客服 / response_time | 客服回复及时；客服回复不及时；客服没有拖延回复 |
| 配送 / delivery_time | 物流人员配送及时 |
| 安装 / installation_time | 安装人员上门及时；安装人员仍未上门 |
| 设备 / delivery | 设备已到货 |
| 设备 / cooling | 设备制冷良好；设备制冷不足；设备制冷并不良好 |
| 设备 / heating | 设备制热良好；设备制热不足 |
| 设备 / noise、sleep_impact、early_failure | 设备噪声很大；噪声影响睡眠；设备短期内发生故障 |

事实 description 必须精确表示其中一个命题，可带一个末尾中文句号；不会把包含关键字的更长描述当作同一命题。支持中文句号、分号及明确的“，然后”“，但是”“，但”连接。整条保存原文与 quote 都要能按这套受限文法完整解析，且 quote 中的原子陈述必须与完整原文中的原子边界相同。

因此“并非客服回复及时”中截取出的“客服回复及时”不会被许可，假设、转述、引号示例、未完成转折或未知上下文也不会因局部关键词命中而升级。完整原文仅用于上下文否决和边界核对，**不能把 quote 外的事实借入支持集合**。这意味着当前上下文政策不是已批准的真实 E1 quote-only 方案；真实实验前必须另行确认。

target_entity/subtopic 必须与识别命题的对象和主题相符；`multiple` 是调用方明确声明多个角色/主题的约定值，不会从文本推断。unknown 不匹配具体对象或主题。情感取显式事实方向或查询方向核对，正负事实不能覆盖相反的正负查询意图；不使用候选的模型 aspect/sentiment 标签推导事实支持。其标签只属于来源身份的一部分。

否定只处理规则内的明确表达，并核对 QueryContract 对该事实声明的 cue/scope。作用范围不一致保留未知，不按“出现否定词就翻转情感”。一般“不是……而是……”、双重否定、同义改写、隐含主语及跨句指代均没有自动蕴含能力，不能由当前规则可靠判定。

### 3.2 多条件与必要关系

| 条件 | 实际算法与保守边界 |
|---|---|
| all_of | 所有要求的事实及全部必要关系均得到支持，才可能 sufficient；只命中组成条件不自动许可复合核心 |
| any_of | 至少一个替代事实直接支持，且显式声明的必要关系均满足；未选中的替代项不当作缺失的必需事实 |
| same_review | 参与事实必须来自同一 source/dataset/product/review 且通过共同版本一致性校验；可以是该评论的不同有效观点，不拼不同评论 |
| before | 同一 quote 内有明确“，然后”，参与事实的实际字符顺序一致；仅叙述位置、不同 quote 的先后或时间猜测不证明 before |
| contrast | 同一 quote 的参与事实顺序符合 contrast_fact_ids，且相邻处有“，但是”或“，但”；分号不冒充转折 |
| 关系联合校验 | 对共享事实选用一致的证据赋值，再同时验证所有关系，不能在不同评论分别找到 A→B、B→C 就拼成同一个关系链 |

关系搜索超过 10000 组赋值时终止为 execution_failed，而非正常 insufficient。这是资源保护，不是语义质量阈值。无法获得一组一致赋值时，missing_relation_ids 列出未被联合确认的必要关系；即使各原子事实都有匹配，也不能输出 sufficient。

### 3.3 状态与禁止扩展

- sufficient：对 evidence 任务满足 all_of/any_of 和全部必要关系，且没有待澄清项。每个许可事实均有当前列表内的精确字符引用。
- limited：核心未完整支持，但有显式声明为独立、且不属于复合关系组成条件的目标事实被直接支持，只许可这部分。mixed 统计/证据任务最多许可明确的证据子答案，不从候选生成统计结论。
- insufficient：没有可许可的核心或独立子结论；包含规则无法确认的情况，不等于已证明业务数据无相关反馈。statistics、clarify 和有 uncertainty/clarification 的任务不获得证据型充分结论。

不做以下推断：噪声大⇒影响睡眠、短期故障⇒长期不耐用、局部实例⇒总体比例、没有候选⇒全量不存在、维度标签一致⇒主题直接支持、余弦距离小⇒充分性概率。重复来源不会增加许可事实或形成“证据越多越可信”的计数门槛；同 key 的重复排名作为来源列表错误拒绝，不静默删除。

## 4. 输出结构与失败处理

| 字段 | 含义 |
|---|---|
| status | completed 时为 sufficient/limited/insufficient；验证/执行失败时为 null |
| execution_status | completed / validation_failed / execution_failed，内部错误不冒充正常语义不足 |
| query_id / mode | 正常结果绑定的当前 query-mode |
| supported_target_fact_ids | 唯一许可陈述的事实 id，不包括仅命中的复合前提 |
| missing_required_fact_ids | 尚无直接匹配的必要事实；any_of 成功时不把未选替代项列为必需缺失 |
| matched_required_fact_ids | 规则观察到的原子事实，仅用于诊断，不自动许可陈述 |
| missing_relation_ids | 无共同一致见证的必要关系；本字段非空时不能因 missing_required_fact_ids 为空就认为核心充分 |
| evidence_refs | 对每个许可事实返回 fact_id/query_id/mode/rank/key/review_id/annotation_id/精确字符区间/快照/输入哈希/evidence_ref |
| condition_evidence_refs | 对观察到的原子事实提供诊断引用；即使核心 insufficient 也可解释命中哪部分，不能据此把前提当作子答案 |
| reason_codes / limitations | 支持、缺失、未知、澄清/统计等依据及候选范围、无不存在证明、非通用蕴含等限制 |
| input_sha256 与版本 | 绑定规范化 QueryContract、完整 batch、独立子问题声明、规则版本；另返回输入/规则/契约/结果版本 |

输入结构、范围、来源、切片、哈希或身份不合法时返回 validation_failed，status=null，且许可事实和引用均为空。未预期的执行错误或关系搜索耗尽返回 execution_failed，status=null，使用固定错误码，不泄露底层异常或原文。不能将这些失败计为正确拒答。

内部在返回前重新计算完整结果，核对状态、许可集合、引用、限制与输入哈希。公开的 `validate_judge_result()` 会重新校验输入来源并重算结果，不仅检查输出 JSON 字段或状态枚举；篡改哈希、引用、事实许可或状态会失败。

## 5. 合成测试与适用回归

| 检查 | 本次实际结果 |
|---|---|
| 新判断器专项 | 38/38 通过；最终版本也在组合回归中通过这 38 项 |
| 既有 QueryContract | 32/32 通过，无契约改动 |
| 既有 Agent 意图 | 19/19 通过 |
| 既有有效观点语料 | 21/21 通过 |
| 既有离线评分 | 25/25 通过 |
| 既有范围测试 | 9 通过、2 错误；未跳过或修改旧测试 |
| 合计 | 146 项：144 通过、0 断言失败、2 错误、0 跳过；专项已包含在其中，不重复相加 |

新测试逐一验证许可与缺失事实、执行状态、原因、限制，以及每条引用确实属于当前候选且精确对应命题字符区间，并覆盖：

- 充分、独立部分回答、仅满足复合核心一半、完全不相关、相反情感、制冷/制热和服务对象差异。
- 显式否定范围、转折、all_of/any_of、before、同评论关系、跨评论拼接，以及共享关系使用不兼容见证的情况。
- 后出现的完整 quote 可以提供共同见证，不因去重丢失；重复观点不产生额外许可事实。
- 无候选、来源失效、字符切片/哈希/稳定键/文件引用错误、来源身份冲突、范围/query/mode/rank 不符、候选替换或遗漏。
- 片段遗漏否定词、假设/转述/未知上下文、借用 quote 外内容、噪声到睡眠和故障到耐用性的越界推断。
- 非法字段（包括 grade、cosine_distance、reference_state）、非法独立事实声明、执行错误、资源耗尽和输出伪造。
- 序列化往返、输入不变，以及禁用网络/SQLite/PostgreSQL 连接时仍可运行。

两项错误仍为 `test_run_date_filters_incremental_skip_and_scoped_progress`、`test_run_retry_and_cutoff_forwarding_with_real_mocked_sdk`：临时 SQLite 走入既有持久标注队列，抛出“持久标注队列只支持 PostgreSQL”。与 4B-1a 记录相同；相关业务及测试文件未变化。已单独保存 `existing-queue-errors.log`，没有改原逻辑、连接真实 PostgreSQL 或把错误算作通过。

主要实际命令：

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_evidence_judge.py -v
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/private-archive/step4a-20261104-human6/audit.py --verify
git diff --check
git status --short
```

组合回归在仓库根目录用以下 unittest loader 加载六个指定模块执行；本轮保存日志后另检查仅有上述两项错误，但没有将原始回归结果标为成功：

```python
import sys, unittest
sys.path.insert(0, 'tests')
names = ['test_evidence_judge', 'test_query_contract', 'test_review_scope',
         'test_review_intent', 'test_rag_corpus', 'test_retrieval_evaluation']
suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(n) for n in names)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
```

日志位于本轮私有目录：`judge-tests.log` 为首版执行，含 1 项 JSON 测试辅助函数错误；修复辅助函数并补充边界用例后，`judge-tests-final.log` 为 38 项通过版本，最终增加诊断引用后以 `targeted-regression.log` 的最终 38 项结果为准。没有隐藏首版失败，也不以旧结果代替最终版本测试。

未执行全仓、真实数据库/pgvector、外部模型、向量生成、业务前端或真实 E1 质量实验。

## 6. 来源安全、隔离与未解决能力

本轮开始记录 182 个已有源代码、测试、评测文件、基线 JSON、归档和 RAG 文档的 SHA-256；收尾逐项比较一致。原归档 12 文件在开始和收尾均经原 `audit.py --verify` 通过，清单哈希仍为 `f6afd216f987314de08b089ffadd8e9e6dcc867d79a11f40dc4771b775bf7b47`。

新测试只用合成原文、合成身份、合成版本键和断言，不读取冻结真实问题/评论/相关性评分制作公开夹具。归档读取仅用于保护核验，不用于判断器质量评测。运行时结构拒绝人工分数和参考状态字段，判断逻辑没有人工评分访问路径。新文件未嵌入私有原文、逐条评分或备注；检查材料受 `outputs/` 忽略规则保护。

能力必须分开理解：

1. **已实现的确定性校验**：版本、可信列表、范围引用、原文区间、输入哈希、稳定键、来源身份、关系引用及输出一致性。
2. **可保守判断的显式事实**：仅上述受限句式中的完整命题、明确否定/转折连接和可追溯的同评论/先后关系；独立子答案还需要查询侧显式声明。
3. **尚无法自动判断**：任意用户问法到命题的映射、真实观点同义改写、复杂否定/时序/因果、隐含对象、讽刺、转述真实性、上下文含义及哪些子问题真正可独立回答。未知不补成支持，需后续模型或人工核验；本轮没有引入它们。

可信来源批次仍由外部数据流程保证真实性与当前有效性，本模块不是鉴权或在线新鲜性检查。语法匹配也不证明评论客观事实为真。现有 quote-utf8-v1、标签边界、Q13 过滤遗漏、18.33% 覆盖、备注解释和已跟踪历史标签风险继续保留。

## 7. 进入真实 E1 仍缺少的事项

当前只具备进一步审查基础规则与合成行为的条件，**不具备直接声称可执行或验收真实 E1 的条件**。尚需：

1. 人工确认 E0/E1 协议、三状态定义、limited 的独立目标边界、可见上下文政策和错误/未知计数方式；本实现读取完整原文作上下文否决，必须单独确认其与待测方案的关系。
2. 人工确认真实问题的 QueryContract、独立子问题声明，以及逐事实支持、必要关系和充分性参考；不得由当前规则输出反向生成未经确认的金标准。
3. 建立完整、独立校验的来源批次及范围/query 身份绑定；不能从待测候选自建可信批次，不能猜测补全 Step 4A 产物缺少的元数据。
4. 批准实验配置、数据边界、指标分母、语义质量/过度拒答门槛和独立验证安排。现有 24 题已用于诊断，不能反复调参后称泛化有效。
5. 若未来需要真实读取或模型辅助，另获明确授权；本轮没有这种调用，也不自动扩索引、改 builder 或更换 BGE-M3。

证据链：E01=保护哈希与原归档审计；E02=38 项合成专项和 146 项组合日志；E03=单列旧队列错误；E04=最终语法、文件边界及隐私检查摘要。F01（E01/E02）：基础判断器可在受限文法与来源边界下测试；F02（E02/E03）：两项既有回归错误仍在；F03（E02/E04）：通用语义与真实质量尚未验证。后续路径 P01：用户审核 4B-2a → 明确人工参考/上下文/实验审批 → 再决定下一步，当前立即停止。
