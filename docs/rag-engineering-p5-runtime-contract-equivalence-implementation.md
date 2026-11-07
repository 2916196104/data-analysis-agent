# P5 Runtime 契约隔离适配实施与离线验收

2026-11-07。已按批准设计实现八目标有限语义规格、原确认身份/依据校验、结构覆盖、确定性描述投影及往返验证。**八目标离线工程检查通过，不代表真实评论语义已验证，也不建立本次 P0 来源绑定。当前可执行真实请求仍为0。**

本轮仅新增三个文件，未修改正式 Agent、build_query_plan、reported_experience_v2、Runtime/B1/B2 Schema 或旧记录。真实数据库、向量检索、Embedding、外部 LLM 调用均为0；没有创建模型批次、来源 seal、历史距离或排名。

## 1. 新增文件与接口

| 文件 | 实现内容 |
|---|---|
| [text_evidence_runtime_contract.py](E:/AllProject/data-analysis-agent/experiments/text_evidence_runtime_contract.py) | 37个有限语义条件、八目标规格、原确认加载、覆盖/结构校验、无损投影、不可变受检返回值及执行阻断 |
| [test_p5_runtime_contract_equivalence.py](E:/AllProject/data-analysis-agent/tests/test_p5_runtime_contract_equivalence.py) | T01–T16的20个专项测试方法，全部使用自编合成来源/评论/事件；包含批量变异、原协议兼容及正式路由检查 |
| 本报告 | 实际结果、隔离范围、待人工核对映射及下一阶段门槛 |

模块导入不读取文件、不构造客户端；没有 CLI、模型发送函数或数据库入口。主接口为：

| 接口 / 类型 | 行为与边界 |
|---|---|
| ExperimentGoalSpec | 严格、冻结模型；保留原完整QueryContract、问题/版本/revision/事件/摘要、历史范围提示、事实树、覆盖依据、否定范围和澄清状态 |
| load_confirmed_origins() | 只读加载四份固定字节摘要的本地材料：既有映射、原bundle、原session、已发布参考；验证原确认链和发布关联，不使用旧候选标签生成新答案 |
| propose_spec(origins, query_id) | 为八目标生成待人工核对的类型化解释；其他目标失败关闭 |
| compile_target(origins, spec) | 原身份→范围/profile比较→许可/澄清→语义树/覆盖→确定性投影→无损检查，全部通过才返回CheckedExperimentTarget |
| CheckedExperimentTarget / require_checked | 仅编译路径创建；冻结并绑定内部内容完整性。普通JSON、自报hash、伪造声明不能恢复受检对象；Python进程内拥有者模式不是密码学签名或OS隔离 |
| validate_projection(target, value) | 重新校验投影及描述往返，拒绝篡改、遗漏、额外字段和非规范描述 |
| require_executable(target) | 本阶段始终拒绝，错误为p0_binding_and_execution_not_implemented；不尝试自动获取来源或调用模型 |

源数据身份来自固定本地加载边界，不从待审spec反推“可信”摘要。历史来源类型必须经过固定摘要加载器；测试专用合成来源明确标记synthetic_fixture，不能声明为历史人工确认，更不能成为P0。

## 2. 语义覆盖和投影实现

### 2.1 校验不是描述字符串复制

八目标使用受限atom/and/or语义树，叶节点只允许有限词表。验证器同时比较原事实ID/方向/数量、顶层all_of、内部连接、角色/时间/对象条件、否定作用域、关系、上下文权限和空独立许可。缺项、额外条件、未知谓词、连接反转、错误依据及任何未解决澄清均拒绝。

每个条件都有原确认rationale的指针及内容摘要，且加载器已核对事件/revision、原问题/契约与发布参考。指针和摘要证明依据未被替换；**它们不自动证明中文解释正确**。typed mapping仍待用户核对，semantic_coverage_complete只表示相对于本次固定类型化规格的工程覆盖。

原QueryContract整体保留，包括aspect_candidates、proposed_filters、scope_ref、polarity_structure、uncertainty和clarification。filters保持null；未把维度候选转换成自动过滤。原独立许可仍为空，不改变任何目标事实数量。

### 2.2 描述投影与往返

投影在既有RequiredFact.description中使用固定前缀和规范JSON文本，包含原事实、可读中文条件树及分支否定范围；不增加LLM输出Schema或B1字段。decode_description执行严格JSON解析、字段/词表检查、连接恢复与规范格式检查；validate_projection再与受检语义树及原否定逐项比较。

描述不得超过现有Text的1000字符限制，不截断、不删条件。原fact ID、sentiment、RequiredFacts.operator及FactRelation顺序保持原样。内部OR留在同一旧fact内，不拆成多个必需事实；Q18仍为f0/f1及same_review、before(f0,f1)。

澄清处理保留原文本与原依据：Q05/Q09/Q12/Q18使用resolved_by_existing_confirmation；Q11使用constraint_note。unresolved、缺少解决记录或无依据改变状态均拒绝。原记录不清空，也没有填新人工确认字段。

### 2.3 非执行性是结果的一部分

RuntimeProjection只有兼容的目标字段，**不含scope/scope_sha256，不能直接解析成完整RuntimeQueryContract**。其support_status固定为本阶段不合格的unsupported，原因offline_only_no_p0_binding，并保留来源未绑定的阻断说明。没有按QID硬编码supported，也没有把正式unsupported/retrieval_only改成支持。

CheckedExperimentTarget.report始终给出：p0_binding_present=false、executable=false、mapping_human_confirmed=false、publication_allowed=false、semantic_verified=false、permitted_target_fact_ids=[]。摘要仅用于原资料定位及内部完整性，模块不输出可被宣称为可信签发的trusted_contract_sha256。

## 3. 八目标实际离线结果

使用受保护本地原确认材料只读加载，对最终实现逐项编译。原session验证通过，已发布ReferenceRelease可解析；映射的原契约、问题、revision和确认依据保持一致。该过程不读取数据库或生成真实评论答案。

| 目标 | 旧事实ID / 方向 | 条件数 | 描述字符数 | 覆盖 / 往返 | P0 / 执行 |
|---|---|---:|---|---|---|
| Q02 | f0 negative | 4 | 174 | 通过 / 通过 | 无 / 禁止 |
| Q04 | f0 negative | 3 | 190 | 通过 / 通过 | 无 / 禁止 |
| Q05 | f0 positive | 3 | 142 | 通过 / 通过 | 无 / 禁止 |
| Q06 | f0 negative | 4 | 247 | 通过 / 通过 | 无 / 禁止 |
| Q09 | f0 negative | 8 | 247 | 通过 / 通过 | 无 / 禁止 |
| Q11 | f0 positive | 6 | 207 | 通过 / 通过 | 无 / 禁止 |
| Q12 | f0 negative | 4 | 218 | 通过 / 通过 | 无 / 禁止 |
| Q18 | f0 neutral、f1 negative | 5 | 65、231 | 通过 / 通过 | 无 / 禁止 |

合计9个原事实、37个条件、11个逻辑连接点；八目标顶层all_of和空独立许可保持不变。Q18的双关系完整投影。当前最大描述247字符，未触及1000上限；**没有准备真实完整请求，因此没有新的实际输入token预算结论**，既有候选数、调用和费用上限均未扩大。

## 4. 专项与既有回归结果

| 测试集合 | 通过 | 隔离 / 范围 |
|---|---:|---|
| 新test_p5_runtime_contract_equivalence | 20/20 | T01–T16；仅自编合成夹具，数据库/网络/编码及真实检索入口拦截 |
| 既有test_query_contract | 32 | 原QueryContract、scope、手工回放及非法结构 |
| 既有test_p5b1_structured_evidence_design | 19 | 另1个数据库夹具用例被连接保护阻断，未计通过 |
| 既有test_review_structured_shadow | 21 | 另1个数据库夹具用例预先隔离；包含真实本地Mock Worker超时、取消和回收 |
| 既有test_shadow_text_evidence_prototype | 24 | 精确文本定位、原B2、来源/歧义/语义隔离 |
| 既有test_semantic_retrieval | 28 | 受控FakeRepository/FakePG/固定向量夹具；没有实际数据库连接、向量索引查询或BGE计算 |
| 既有test_p5_real_data_review_preparation | 15 | 旧离线准备不变，未运行其私有目录生成入口 |
| **通过合计** | **159** | **新20项 + 既有139项** |

专项批量变异包括：37条件各删除/替换，共**74个语义树变体**；37条依据各删除/篡改，共**74个覆盖依据变体**；**11个连接反转**。这159次核心变异检查全部拒绝变体，另有角色、方向、关系、额外条件、scope/profile、JSON、许可等边界检查。变异检查次数与测试方法数量是不同统计，不能混作业务样本数。

分别验收结果：

| 层次 | 观察 |
|---|---|
| 规格覆盖 | 八目标/37条件完整；内部OR、主机/配件、天气/时长及直接收货边界均保留 |
| 变异拒绝 | 必要条件删除/替换、OR↔AND、before反转、缺事实、方向变化、额外条件、依据缺失/漂移均失败关闭 |
| Runtime投影 | 9个描述往返一致，范围65–247字符；超过1000、短标签替代完整目标、增加scope或supported声明均拒绝 |
| B1兼容 | 合成测试可验证目标字段装入既有Runtime及JudgeInput结构；原prepare_input拒绝离线不合格目标。即使测试故意自报hash并伪造eligible字段，仍被原P0检查拒绝 |
| B2兼容 | 合成协议专用envelope验证双事实、same_review、before证据及部分覆盖；跨评论关系、缺时间依据拒绝。该envelope仅在测试内直接构造，不是P0来源或可发送业务批次 |
| 引用与语义 | 原定位器拒绝quote越界、歧义、身份及哈希漂移；同评论context_veto范围保留。合法位置或关系引用仍可能语义不足，结果一直semantic_verified=false |
| 正式路由 | 新专项逐项重现八原句：6个unsupported、Q06 retrieval_only、Q18 opinion_summary；另测已有supported/unsupported/retrieval_only规则句、v2政策及保存兼容。正式源码没有导入新模块 |
| 失败/来源隔离 | 不可信origin/checked JSON、自报摘要、假seal、scope/profile漂移、发布true及非空独立许可均拒绝；模块没有P0发行者或发送入口 |

### 数据库夹具隔离说明

首次带sqlite3.connect禁止保护运行时，既有`test_p5b1_structured_evidence_design.StructuredDesignTests.test_shadow_cannot_replace_v2_or_change_current_and_historical_answer`尝试初始化临时SQLite库，连接被拦截。没有放开限制、修改旧测试或将其计为通过。

同时预先隔离`test_review_structured_shadow.ShadowTests.test_default_off_agent_sql_rag_and_legacy_history_stay_compatible`及整个`test_review_task_contract.RuntimeContractTests`的11个数据库夹具方法。合计**13个数据库相关方法未获通过结论**：1个保护阻断、12个未执行。新专项T15以纯计划/规则输入验证当前路由与保存兼容；它不代替完整Agent SQL/RAG数据库回答回放。

### 复现命令

新专项：

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_p5_runtime_contract_equivalence.py -v
```

以下保护执行方式选择同一159项纯合成集合，排除两个数据库夹具单项，不加载RuntimeContractTests数据库套件。临时Mock Worker只运行本地虚构Judge，无外部LLM。

```powershell
$offlineTests = @'
import sys, unittest
from contextlib import ExitStack
from unittest.mock import patch
sys.path[:0] = ['.', 'src', 'tests']
modules = ['test_p5_runtime_contract_equivalence', 'test_query_contract',
    'test_p5b1_structured_evidence_design', 'test_review_structured_shadow',
    'test_shadow_text_evidence_prototype', 'test_semantic_retrieval',
    'test_p5_real_data_review_preparation']
excluded = {
    'test_p5b1_structured_evidence_design.StructuredDesignTests.test_shadow_cannot_replace_v2_or_change_current_and_historical_answer',
    'test_review_structured_shadow.ShadowTests.test_default_off_agent_sql_rag_and_legacy_history_stay_compatible'}
def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite): yield from flatten(item)
        else: yield item
with ExitStack() as stack:
    for name in ('socket.socket.connect', 'socket.create_connection', 'psycopg.connect',
        'sqlite3.connect', 'httpx.HTTPTransport.handle_request',
        'httpx.AsyncHTTPTransport.handle_async_request', 'openai.OpenAI',
        'data_analysis_agent.reviews.embedding.LocalBgeM3Embedder'):
        stack.enter_context(patch(name, side_effect=AssertionError('offline only')))
    cases = [c for m in modules for c in flatten(unittest.defaultTestLoader.loadTestsFromName(m))
             if c.id() not in excluded]
    result = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(cases))
    if not result.wasSuccessful(): raise SystemExit(1)
'@
$offlineTests | .venv/Scripts/python.exe -B -
```

这些是合成工程测试，不是新的模型实验，不提供真实业务准确率，不能开启semantic_verified。

## 5. 仍未解决的差异与兼容边界

1. **中文到类型化树的解释仍待人工核对。** 固定图和原确认来源已进行工程验证，但代码不能自行证明自然语言蕴含或把Codex解释升级为人工确认；report的mapping_human_confirmed一直false。
2. **当前正式路由仍不等价。** 没有覆盖或改写旧plan；新模块是明确隔离的原确认目标适配，不是正式规则能力扩展。
3. **真实P0和可信范围尚缺。** historical_scope_hint、scope_ref和profile仅作历史身份比较；RuntimeProjection故意缺少当前scope及其绑定。未实现限ID来源适配、完整有效观点读取或拥有者签发，require_executable始终失败。
4. **B1/B2前提未放宽。** 未修改not_eligible、source_policy、prepare_input、audit_output或Text Evidence输出契约；没有以内部resolve_output代替业务输入来源检查。测试内直接构造的合成envelope不供业务使用。
5. **成功路径留存和真实预算仍为后续前提。** 未顺带修改旧T组合成结果或成功正文留存；未冻结真实发送输入，描述长度通过不能推断真实请求token合格。

本轮没有为未来来源接口模拟真实签发，也没有接受Synthetic/Fake资料冒充真实评论。P0拥有者限ID核验、当前完整观点集合、范围桥接及历史选择provenance依赖仍按原设计保留阻断。

## 6. 需要人工核对的新增类型化映射

这是新表示的差异核对清单，**不是要求重新审批48组旧评审，也不是新四状态答案**。无需重复回答原确认已解决的问题；只需确认这些显式编码没有遗漏、扩大或收窄原含义。

| 目标 | 相对旧短事实描述新增的显式表示 |
|---|---|
| Q02 | 客服角色与内部“不耐烦或态度差”分开；普通回复慢、机器人回复和未解决问题不自动推出态度差 |
| Q04 | 购买后问题、持续无人跟进、已处理但未解决三者区分；不把所有安装问题一概排除 |
| Q05 | 实际收货与下单之间的速度、直接收货依据，以及安装日期不能替代收货日期 |
| Q06 | 未收货和明显延迟两个分支；否定限定在未收货分支；普通未收货、错误签收标记、发货与安装等待不能自动混同 |
| Q09 | 主机、开始使用、早期、损坏或故障；配件、使用前损坏、泛质量评价明确不补齐目标 |
| Q11 | 明确炎热、开机、制冷、室内降温、快速、同次体验六要素；不由夏季推天气，不设分钟阈值 |
| Q12 | 较长持续运行后室内仍无有效降温；不以短时、制热或冷风单一要素代替，不设小时阈值 |
| Q18 | f0中性到货、f1负面安装等待、same_review和before；分类仅解释主题；无独立子事实许可 |

还需核对描述采用规范JSON文本嵌入description的表示方式：它保持现有Schema和完整条件，但并未通过真实LLM验证其理解效果。不得为此重跑历史D/E/T案例或改写旧标准。

## 7. 下一阶段人工评审准入与停止状态

当前新目标报告状态为**pending_typed_mapping_review**：八目标离线工程检查已完成，可提交上述映射供用户核对，但没有自动开启人工裁决。

- 原私有review-sidecar仍为27行空四状态，human_confirmed=false、review_enabled=false；原review-groups无请求正文且不可执行。没有更改其历史pending_contract_equivalence状态。
- 用户核对新表示后，才可在独立任务中准备/执行对应四状态人工评审；本轮不填写27行答案或Q18关系裁决，不把已有direct种子升级。
- 最终真实来源核验必须先有P0拥有者限ID路径和专项验收，再取得限定范围的只读授权。真实评论发送、模型次数和实际费用另行授权；本轮没有这些执行能力。
- 所有路径继续保持publication_allowed=false、semantic_verified=false、正式许可集合为空，真实可执行请求数为0。

## 8. 文件与材料保护验证

开始记录的**400个既有src/scripts/tests/docs/experiments文件**在结束时聚合路径/字节摘要一致。新代码语法检查、空白检查及git diff --check通过。正式Agent、v2、既有B1/B2及历史实验文件没有代码改动。

原私有目录11文件在最终只读验收后聚合摘要与本轮开头一致；27行expected_status仍全部null。四份原确认资料的固定字节摘要由新加载器核验通过，原session/发布参考只读，没有重发或覆盖。没有将真实评论、候选ID/key、敏感来源配置或私有逐评论判断写入Git。

实施与离线验收到此停止；不自动进入人工金标准裁决、数据库来源核验、模型评估或正式集成。
