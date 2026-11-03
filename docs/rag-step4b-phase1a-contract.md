# RAG Step 4B-1a：最小查询语义契约

日期：2026-11-04（Asia/Taipei）。状态：**本阶段开发完成，停止于独立契约与合成测试；没有进入 4B-2、4B-1b 或 Agent 集成。**

已按本轮授权实现结构化查询契约、范围引用校验和开发者人工契约回放格式。新专项 32 项通过；适用组合回归 108 项中 106 项通过、2 项既有范围测试报错，详见第 5 节。Step 4A 冻结归档和现有业务文件未变化。E0/E1 协议、充分性人工参考及数值质量门槛仍待人工确认；这些结构性测试不证明检索语义质量通过。

## 1. 阅读范围与实际文件变更

实施前完整阅读了 [最终质量审查](rag-retrieval-quality-final-review.md)、[Step 4B 计划](rag-step4b-retrieval-strategy-plan.md)、[4B-0 前置报告](rag-step4b-phase0-readiness.md)，并检查现有 `retrieval_evaluation.py`、`embedding_store.py`、`scope.py`、`opinions.py`、`core/validation.py`、`core/serialization.py`、`agents/review_intent.py` 及对应范围、意图、语料、评分测试。

| 本轮新增文件 | 内容 |
|---|---|
| [query_contract.py](../src/data_analysis_agent/reviews/query_contract.py) | 不可变 Pydantic 数据模型、独立契约校验和人工回放校验 |
| [test_query_contract.py](../tests/test_query_contract.py) | 32 项完全合成的专项测试，无真实评论、冻结问题集或人工评分读取 |
| 本报告 | 数据模型、接口、验证结果、兼容性与停止边界 |

没有修改其他已有文件。私有检查材料保存在被 Git 忽略的 `outputs/rag-retrieval-quality/step4b-phase1a-20261104/`，包括开始时的保护文件哈希、测试日志、旧错误复现日志及最终检查摘要，不作为业务实现或公开夹具。

## 2. 数据模型与字段含义

复用已安装的 Pydantic、项目 `Input`/`Text`、`Aspect`/`Sentiment`、严格 JSON 和规范哈希工具，没有新增依赖。没有修改既有 `ReviewIntent`：其 query_type、all 情感、商品/日期处理属于当前 Agent 路径；新契约只提供未来离线策略所需的表示，不替换或编译旧路径。

模型使用 `strict=True`、`extra='forbid'`、`frozen=True`。所有集合字段采用 tuple，内部也使用冻结模型；输出 JSON 的数组/对象是独立副本。规范化只补明示的默认值、生成确定性的结构和不可变集合，不从问题文本推断事实或主题，不隐式改写输入文字。

| 字段 | 结构与意义 |
|---|---|
| version | 必须为 `rag-query-contract-v1`，不自动接收其他版本 |
| task_type | `statistics / evidence / mixed / clarify`；这里 mixed 指统计与证据任务混合，不等于混合情感 |
| aspect_candidates | `AspectCandidate(aspect, rationale)` 序列；aspect 复用 quality/effectiveness/price/packaging/logistics/service，理由必须为非空文字，候选维度不得重复 |
| sentiment_intent | 复用 positive/negative/neutral/unknown，并允许查询层的 mixed；不提供旧 Agent 的 all 别名 |
| target_entity | 显式填写的对象或角色，如 customer_support、delivery_staff、installer；未定为字面值 `unknown`，不由模块猜测 |
| subtopic | 显式填写的细分主题，如 cooling/heating；未定为 `unknown`。不新建大规模主题枚举或自动分类器 |
| required_facts | `RequiredFacts(operator, items, relations)`；operator 为 all_of/any_of；每项有唯一 id、description、可选的目标 sentiment；关系支持 before/same_review |
| polarity_structure | negations 逐项声明 fact_id、cue、scope；contrast_fact_ids 表示有顺序的转折关联事实。模块保存否定范围，不自行翻转情感或判定双重否定 |
| proposed_filters | 只允许现有检索所支持的 aspect、sentiment，可为空；这是人工提出的建议，不执行或自动生成 SQL 过滤 |
| uncertainty / clarification | 非空文字序列，分别记录未确定事项和澄清问题；clarify 任务必须有 clarification |
| scope_ref | 冻结的 `ScopeRef(run_id, manifest_sha256, scope_sha256)`，三个值均为 64 位小写 SHA-256 十六进制引用；不接收商品、数据集或日期覆盖字段 |

除 clarify 外必须有目标事实。事实 id、关系引用及转折/否定引用均校验存在性与重复；before 必须引用两个不同事实。all_of/any_of 只支持一层事实集合及附加关系，本版本没有实现嵌套布尔表达式或时间/因果约束求解器。

sentiment_intent=mixed 在本版本用于同评论正负共现：要求 all_of，并存在连接明确 positive/negative 目标事实的 same_review 关系。不接受把两个不同评论拼成混合关系的默认表示；模块只校验关系声明，不验证真实评论是否满足它。

### 2.1 范围与建议过滤的边界

`validate_query_contract()` 必须接收调用方单独提供的可信 `expected_scope`。任何 run、manifest 或 scope 引用变化均报 `scope_ref_mismatch`，不静默回填；不允许从待校验契约的 scope_ref 构造 expected_scope。调用方应从已经验证的冻结配置获得可信值，模块不会联网或读数据库证明这些哈希的来源。

本阶段没有把真实范围硬编码到新模块或测试。真实冻结配置仍为 `reviews_csv/reviews-csv`、商品 `100013588055`、UTC `2021-09-11` 至 `2022-08-26`、固定 200 条索引、BGE-M3/1024 维/quote-utf8-v1，均未改变。run 引用绑定保存运行，scope 引用沿用原范围身份。

建议过滤采用保守的结构规则：

- statistics/clarify 不携带检索过滤；evidence/mixed 任务也只有在无 uncertainty、无 clarification、恰有一个候选维度、对象/主题/情感均非 unknown 时才允许非空建议。
- 建议 aspect 必须等于唯一候选维度；建议 sentiment 必须等于明确的 sentiment_intent。
- mixed 不是现有 `Sentiment` 枚举，不能放入 proposed_filters.sentiment；查询 mixed 也不能偷偷改用 positive 或 negative。条件充分时可单独声明 aspect 建议，不生成单向情感过滤。
- 发现不安全建议时明确拒绝输入，不删除歧义、不替开发者选择维度、不把字段留空后继续执行查询。

这些规则只证明结构自洽，并不证明人工提出的过滤语义完整。Q13 所示跨标签相关证据遗漏风险仍然存在，未通过契约结构测试消除。

## 3. 关键接口与人工回放输入

### 3.1 独立校验接口

```text
validate_query_contract(value, *, expected_scope: ScopeRef) -> QueryContract
validate_manual_replay(
    value, *, expected_scope: ScopeRef, expected_query_id: str,
    expected_question: str, expected_data_kind: Literal['synthetic', 'frozen_query'],
) -> ManualReplay
```

value 接受 dict 或严格 JSON 字符串。拒绝重复 JSON 键、NaN/Infinity、非法类型、未知字段和未支持版本。必须通过这两个边界函数做完整校验：直接调用 Pydantic 模型构造器只校验字段结构，不能替代外部范围比对及跨字段规则；Pydantic 的 model_construct/model_copy 也不能视为已校验输入。

失败抛 `ContractError`，使用固定错误码，不回显问题或人工理由。常见错误包括 `invalid_contract_structure_or_json`、`scope_ref_mismatch`、`required_facts_missing`、`invalid_fact_references`、`mixed_requires_same_review_polarities`、`uncertain_or_non_evidence_filters_forbidden` 和 `replay_contract_hash_mismatch`。未知字段和非法枚举作为结构错误拒绝，不把底层输入内容写进错误信息。

### 3.2 人工回放的身份隔离

`ManualReplay` 必须包含：

| 字段 | 校验方式 |
|---|---|
| version / input_kind | 固定 `rag-query-replay-v1 / developer_contract`；不接受 runtime_prediction、human_reference 等输入种类 |
| data_kind | synthetic 或 frozen_query，必须与调用方预期相等；测试输入不能静默冒充冻结问题输入 |
| replay_id / query_id | 显式回放标识；query_id 必须等于调用方从预期问题来源取得的值 |
| query_sha256 | `content_sha256(expected_question)`，对未经语义改写的问题字符串计算规范 JSON 哈希；不是直接对原始 UTF-8 字节取哈希 |
| contract_sha256 | 对已经 `validate_query_contract()` 规范化后的 `model_dump(mode='json')` 计算 `content_sha256()`，包括默认字段和范围引用 |
| contract | 必须再经过完整查询契约校验，使用独立传入的 expected_scope |

没有相关性分数、证据状态、充分性参考答案、业务答案或 metrics 字段，这些字段出现在回放或契约层都会被拒绝。未来 E1 的人工参考应由独立评估器保管，不加入本输入。数据种类与哈希检查不是数字签名，不能证明声明者身份或人工语义正确；自由文字中夹带参考答案也无法靠结构模型完全发现，需要离线数据管理和人工审查。

本轮只测试格式，没有为真实 24 题生成契约、充分性参考或回放评分，没有执行 E1。

### 3.3 可运行的纯合成示例

在已安装项目的 Python 环境中运行以下示例；全部身份由合成字符串生成，不读取真实数据：

```python
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.query_contract import (
    ScopeRef, validate_query_contract, validate_manual_replay,
)

scope = ScopeRef(
    run_id=content_sha256('example-synthetic-run'),
    manifest_sha256=content_sha256('example-synthetic-manifest'),
    scope_sha256=content_sha256('example-synthetic-scope'),
)
question = '[合成示例] 请进一步说明希望查看的对象与任务。'
contract = validate_query_contract({
    'version': 'rag-query-contract-v1',
    'task_type': 'clarify',
    'sentiment_intent': 'unknown',
    'uncertainty': ['合成示例：目标角色与任务未定'],
    'clarification': ['合成示例：需要数量统计还是具体实例？'],
    'scope_ref': scope.model_dump(mode='json'),
}, expected_scope=scope)
normalized = contract.model_dump(mode='json')
item = {
    'version': 'rag-query-replay-v1',
    'input_kind': 'developer_contract', 'data_kind': 'synthetic',
    'replay_id': 'example-replay', 'query_id': 'example-query',
    'query_sha256': content_sha256(question),
    'contract_sha256': content_sha256(normalized), 'contract': normalized,
}
validated = validate_manual_replay(
    item, expected_scope=scope, expected_query_id='example-query',
    expected_question=question, expected_data_kind='synthetic',
)
assert validated.contract.subtopic == 'unknown'
assert validated.contract.proposed_filters.sentiment is None
```

## 4. 合成测试覆盖

| 类型 | 具体检查 |
|---|---|
| 正向、负向、中性、未知 | 合法情感保留，不自动推断或翻转；所有现有 aspect 枚举可表示 |
| 任务区分 | statistics/evidence/mixed 与 clarify；统计与证据混合不等于情感 mixed |
| 对象与主题 | 客服、配送人员、安装人员分别保留；制冷与制热不合并；未知主题/对象显式 unknown |
| 否定与关系 | 否定词、目标事实及作用范围原样保留；到货与等待安装的多事实 all_of/before；any_of；同评论正负共现及转折 |
| 歧义与过滤 | 澄清必须有问题；多维度、unknown 或未消歧输入拒绝硬过滤；合法枚举但与意图矛盾的过滤也拒绝 |
| 非法输入 | 非法任务、维度、情感、过滤字段、事实操作符、缺事实、重复 id、悬空/重复关系、空白文字、异常 JSON |
| 范围与不可变性 | 三个范围引用均不能替换；不能注入商品/日期/source；嵌套字段冻结，修改输入或序列化副本不改变已验证模型 |
| 回放 | JSON 往返、默认值规范化与哈希确定性；版本/query/scope/契约变更拒绝；合成与冻结输入、预测与参考格式隔离；没有 0/1/2 决策输入 |
| 无外部调用 | 对网络、SQLite、PostgreSQL 连接设置失败哨兵时，契约及回放校验仍可完成 |

新测试不读取真实评论或人工标签，也不读取冻结问题集制作夹具。现有回归沿用其原有合成/临时数据库和模拟模型传输，不调用真实供应商处理真实评论。

## 5. 实际测试、命令和结果

| 检查 | 实际结果 |
|---|---|
| 新契约专项 | 32/32 通过；最终版本在组合回归中再次通过 |
| 现有 Agent 意图 | 19/19 通过 |
| 现有有效语料 | 21/21 通过 |
| 现有离线检索评价 | 25/25 通过 |
| 现有查询范围 | 11 项中 9 通过、2 报错，未跳过或隐瞒 |
| 组合回归 | 总计 108 项：106 通过、0 断言失败、2 错误、0 跳过；不报告为全绿 |
| 冻结归档 | 开始与收尾均运行原 `audit.py --verify`，12 文件哈希及评分复算一致 |
| 文件与隐私检查 | 核对开始时记录的 174 个已有源文件/测试/评测文件/基线 JSON/归档/三份前序报告哈希；新文件只包含本模块、合成测试及报告；检查序列化示例、引用、私有文本与 Git 空白/忽略状态 |

两项现有错误分别为 `test_run_date_filters_incremental_skip_and_scoped_progress` 与 `test_run_retry_and_cutoff_forwarding_with_real_mocked_sdk`。它们用临时 SQLite 调用已有 `run_labeling()`，现有分派进入持久标注队列后抛出“持久标注队列只支持 PostgreSQL”。新模块未接入这条路径，已有相关文件哈希未改。另起进程禁止导入新 query_contract 模块后，单独运行这两项仍复现相同错误，确认不是本阶段的导入或接线回归。遵守本轮限制，没有修改 Agent、队列、标注逻辑或旧测试来使结果变绿。

实际运行的主要命令如下。组合回归通过同一 unittest loader 加载五个指定模块，精确内容见紧随其后的 Python 代码，完整日志保留在私有目录。

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_query_contract.py -v
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/private-archive/step4a-20261104-human6/audit.py --verify
git diff --check
git status --short
```

精确的组合复现使用下面的 Python 内容从仓库根目录运行：

```python
import sys, unittest
sys.path.insert(0, 'tests')
names = ['test_query_contract', 'test_review_scope', 'test_review_intent',
         'test_rag_corpus', 'test_retrieval_evaluation']
suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(n) for n in names)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
```

没有重跑全仓测试、真实 PostgreSQL/pgvector 集成、模型编码、前端测试/构建或浏览器验收；本轮无对应实现改动，亦未将旧通过结果计为本次测试。日志位置：`contract-tests.log`、`targeted-regression.log`、`existing-errors-reproduction.log`，均在本轮私有目录。

## 6. 兼容性、未完成项与下一阶段条件

新模块只依赖共享枚举、基础结构校验和纯序列化工具；没有被任何现有 Agent、API、CLI、检索或统计业务路径导入。proposed_filters 只是数据，未应用到 SQL 或现有排序。原始评论、人工分数/备注、数据库、Embedding 模型/空间/向量、quote-utf8-v1 和 Step 4A 归档均保持不变。

结构校验不会确认人工意图是否忠实于自然语言，也不会发现文本字段中的所有隐含歧义。ScopeRef 的可信来源必须由调用方保证，哈希校验不是鉴权；Pydantic 冻结防常规字段赋值，不是对主动绕过 Python 内存机制的安全隔离。关系仅作显式声明与引用校验，不计算证据是否满足条件或时间关系是否存在矛盾。

仍未开发：自然语言自动解析、自动过滤、真实数据库检索、候选合并或重排、证据充分性判断器、业务回答、E1 实验、真实 24 题的人工查询契约、充分性金标准与独立泛化验证。既有备注解释、Q13 标签/过滤边界、18.33% 索引覆盖及历史标签 Git 跟踪问题沿用前序报告，未自动修正。

**具备为 4B-2 提供结构化输入的技术条件，但不代表获准执行 4B-2 或 E1。** 下一阶段仍需用户授权，确认 E0/E1 协议、可见上下文、充分性人工参考与质量门槛；两个既有范围测试错误作为回归限制保留。测试通过不等于上述人工事项已批准，也不等于语义质量已通过。

证据链：E01=本轮保护哈希与原归档审计；E02=新专项和组合回归日志；E03=禁用新模块后的旧错误复现日志；E04=最终文件/隐私/示例检查摘要。F01（E01/E02）：独立契约边界可验证且无既有文件改动；F02（E02/E03）：既有范围队列错误仍在；F03（E04）：交付不包含私有评论/逐条标签。后续路径 P01：用户审核本阶段 → 单独授权 4B-2 及其人工评价边界 → 再实施，当前停止。
