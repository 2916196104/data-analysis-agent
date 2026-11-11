# Agent 第一阶段：10 轮人工裁决工作表

准备日期：2026-11-11。**全部 10 轮仍为 PENDING_HUMAN，没有任何人工批准结果。** 本工作表提出建议和备选解释，不发布正式 Oracle v2，不执行评测、不改变旧分数。

## 使用方式

先确定业务含义，再核对源码约束和实际轨迹。**当前 Agent 恰好输出某个选项，不是选择该选项的理由。** 每个候选方案的 RequestGoal、Router、Planner、验收和终态在各案例中列出。推荐项没有预勾选；你可以保留待裁决，或给出另一个符合安全约束的含义。

填写方式：在各案例末尾填写选项编号、理由和裁决人；也可在后续独立消息逐行回复 `Gxx-Tn：选项 A/B/C；理由：…`。只填案例意思不表示已批准修改代码、数据库操作或费用授权。若替换建议，需要说明同样的目标、工具、验收与终态，不以“让当前测试通过”为理由。

### 固定基线和证据分离

- [原 Oracle v1](E:/AllProject/data-analysis-agent/tests/fixtures/agent_stage1/oracle.v1.json)、[原合成语料](E:/AllProject/data-analysis-agent/tests/fixtures/agent_stage1/corpus.v1.json)、[原 freeze-manifest](E:/AllProject/data-analysis-agent/tests/fixtures/agent_stage1/freeze-manifest.json) 保持原样。
- [源码 v3](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-p0p1/source-revision.v3.json) 与 [源码 v4](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-remaining-p1/source-revision.v4.json) 均保留。当前源码与 v4 绑定核对，未修改业务代码。
- 观察依据为既有 `.cache/agent-stage1-remaining-p1/run-02.json`，并非本轮重跑。历史结果 **51 PASS / 1 FAIL / 10 PENDING_HUMAN** 仅作基线引用，不是本轮新评分。
- [观察事实 JSON](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-human-review-observations.json) 只保存已有输出，明确标为非标准答案；[v2.pending 草案](E:/AllProject/data-analysis-agent/tests/fixtures/agent_stage1/oracle.v2.pending.json) 的候选标准由原问题、v1 与源码契约另行编写。
- 本轮语料均为原先虚构合成数据，没有查真实 053/055 评论、向量或数据库，没有真实模型质量结论。

### 重要勘误（不修改旧材料）

原 REVIEW 说明把 G10/G11/G12 的问题描述为“冻结预期写了 negative/单商品”。**直接读取 oracle.v1 后，G10-T2、G11-T2 已经是 sentiment=null、页面两商品；G12-T1 是本轮明确的单商品、sentiment=null；G12-T2 也是 sentiment=null。** 原说明中的 negative 表述不准确。

因此这几项不能被包装成“修正错误 negative 后才通过”。真正需要裁决的是澄清时 Goal 的形成方式、用户条件与可信事实的边界、G12-T2 的 SKU 承接，以及原 `not_completed` 验收标签的含义。旧排除说明、旧分数和轨迹全部保留，没有借本轮重解释而改分。

## 状态与字段口径

| 场景 | RequestGoal/执行 | task_acceptance | Agent 外层 | Worker |
|---|---|---|---|---|
| 条件不足、先澄清 | 可保留部分已知 Goal，或尚未冻结；不执行业务工具 | 可省略；若显式记录失败必须 complete=false | unsupported，needs_clarification=true | succeeded 表示澄清已返回，不表示分析完成 |
| 多维全部完成 | 全部必做维度通过独立证据验收 | passed、complete=true；P6 parent_status=complete | ok | succeeded |
| 多维部分成功 | 保留完整必做目标与所有子状态，仅允许发布成功维度 | partial、complete=false；parent_status=partial | partial | partial |
| 全部证据不足 | 正确查询且均不足，无支持主题 | insufficient_evidence、complete=false | unsupported | succeeded |
| 全部预算跳过/全局硬失败 | 不发布未验收主题 | failed、complete=false；P6 内部可为 skipped_budget/failed | error | failed |
| stale_ranking（准备阶段） | 已冻结主题目标，但当前统计使旧排名过期；观点预读和分类不得开始 | failed、complete=false、evidence_status=not_executed；不是新外层状态 | unsupported，要求用户刷新可信统计 | succeeded，受控阻断已正确返回 |

`not_completed` 不是现有 task_acceptance.status；可用作证据/执行阶段描述，不能新增 API 枚举来适配错误预期。`partial` 也不能改成 ok 或 failed 来迎合显示逻辑。

工作表中的 **analysis_intent=not_finalized** 是“无最终执行计划”的审查投影，不是要求新增 API 枚举。现有原始 JSON 可能含候选路由 `aspect_drilldown/negative`；必须同时查看 Goal、needs_clarification 和 query_plan，不能把候选值当已确定业务事实。

所有选项中的“已知约束”“尚未冻结”等是审查说明，不是新增服务端状态。允许缺省的验收表示在 pending JSON 中列为候选规则，尚不能直接作为正式评分器输入。

## 裁决总览（所有最终选择均未填写）

| 轮次 | 问题性质 | 建议项（未选定） | 人工最终选择 |
|---|---|---|---|
| G10-T2 | 条件不充分；澄清/Goal 表示待裁决 | A | 未填写 |
| G11-T2 | 条件不充分；澄清/Goal 表示待裁决 | A | 未填写 |
| G12-T1 | 条件不充分；澄清/Goal 表示待裁决 | A | 未填写 |
| G12-T2 | v1 范围约束陈旧；语言条件仍不充分 | A | 未填写 |
| G17-T2 | 原预期状态定义错误（契约唯一） | A | 未填写 |
| G18-T2 | 原预期状态定义错误（契约唯一） | A | 未填写 |
| G19-T2 | 原预期状态定义错误（契约唯一） | A | 未填写 |
| G20-T2 | 原状态标签错误；计划/执行阶段需分离 | A | 未填写 |
| G24-T2 | 真正的省略语义歧义；Goal 默认层次需审查 | A | 未填写 |
| G30-T3 | 真正的层次歧义；原完成性预期自相矛盾 | A | 未填写 |

## 逐轮工作表

## G10-T2 · 标注身份变化导致状态失效

**分类：条件不充分；澄清/Goal 表示待裁决。当前裁决状态：PENDING_HUMAN。**

### 1）用户问题与必要历史

1. “100013588053的差评主要集中在哪些方面？”
2. “具体体现在哪里？”；本轮页面/身份变化：`{"model":"changed-label-profile"}`

P6 开关：该组开启离线准备/执行/发布能力。页面初始可选 053/055；时间窗口 2023-02-01～02-28 UTC，后续范围变化以上述本轮元数据为准。

### 2）原冻结预期与理由

生成器显式传入 sentiment=None、product=None、status=clarify，意在阻止新标注身份承接可信排名；因此冻结预期本来就不是 negative。

v1 实际已写 sentiment=null、澄清、零工具；旧 REVIEW 说明中的“预期 negative”不符合冻结文件。not_completed 仍是待修正的验收标签。

<details>
<summary>查看 v1 的完整逐字段预期（原样）</summary>

```json
{
  "expected_analysis_intent": "not_finalized",
  "expected_analysis_state": {
    "pending_goal": "absent",
    "ranking_basis": "absent"
  },
  "expected_final_status": {
    "agent": "unsupported",
    "worker": "succeeded"
  },
  "expected_pending_goal": "absent",
  "expected_query_plan": {
    "analysis_type": null,
    "aspects": [],
    "blocked": true,
    "sentiment": null
  },
  "expected_request_goal": {
    "aspect": null,
    "levels": [
      "themes"
    ],
    "sentiment": null
  },
  "expected_required_tools": [],
  "expected_scope": {
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "product_ids": [
      "100013588053",
      "100013588055"
    ]
  },
  "expected_task_acceptance": {
    "complete": false,
    "status": "not_completed"
  },
  "expected_user_intent": {
    "needs_clarification": true
  }
}
```

</details>

### 3）源码契约依据

- **C01**：[analysis_context.py:11](E:/AllProject/data-analysis-agent/src/data_analysis_agent/jobs/analysis_context.py:11) — 同会话、同标注身份和合法范围才恢复；用户原文条件与可信事实分离。
- **C02**：[analysis_state.py:37](E:/AllProject/data-analysis-agent/src/data_analysis_agent/jobs/analysis_state.py:37) — 排名与待办分别严格验收、精确范围投影；当前 pending 恢复仅负向、无单维度、单商品的合法澄清任务。
- **C06**：[reviews.py:122](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/reviews.py:122) — 缺可信排名先澄清；stale 准备错误走 unsupported；Goal 已有但未完成时任务验收 complete=false。
- **C08**：[review_intent.py:115](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_intent.py:115) — 未明确任务不执行；RAG 省略追问先澄清；当前短情感追问可承接待完成 themes，但该代码不裁决业务语义。
- **C09**：[review_acceptance.py:41](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_acceptance.py:41) — 原始 Goal 在 Planner 前独立形成；not_completed 不是任务验收状态，未完成不能 complete=true。

独立建议理由：推荐根据本轮明示信息形成有限内容 Goal，缺条件就澄清；依据 C01/C08，而非实际输出碰巧相同。

### 4）实际输出与执行轨迹（只作证据）

<details>
<summary>查看实际九字段/范围及计数（不是标准答案）</summary>

```json
{
  "scope": {
    "model": "changed-label-profile",
    "base_url": "https://labels.stage1.invalid/v1",
    "prompt_version": "stage1-labels-v1",
    "product_ids": [
      "100013588053",
      "100013588055"
    ],
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "as_of": null,
    "source": "reviews_csv",
    "dataset_version": "stage1-synthetic-v1",
    "baseline_from": "2023-01-01",
    "baseline_to": "2023-01-31"
  },
  "analysis_state": {
    "ranking_basis": "absent",
    "pending_goal": "absent"
  },
  "post_state": {
    "ranking_basis": "absent",
    "pending_goal": "absent"
  },
  "request_goal": {
    "original_question": "具体体现在哪里？",
    "levels": [
      "themes"
    ],
    "aspect": null,
    "sentiment": null
  },
  "user_intent": {
    "needs_clarification": true,
    "clarification_question": "当前没有同会话、同身份且范围一致的可信负面排名，请先刷新统计。",
    "resolved_product_ids": [
      "100013588053",
      "100013588055"
    ],
    "effective_time_range": {
      "date_from": "2023-02-01",
      "date_to": "2023-02-28"
    }
  },
  "raw_analysis_intent": "aspect_drilldown",
  "raw_sentiment_intent": "negative",
  "query_plan": null,
  "task_acceptance": null,
  "final_status": {
    "agent": "unsupported",
    "worker": "succeeded"
  },
  "query_executions": 0,
  "classification_model_attempts": 0
}
```

</details>

实际流程：用户请求 → 范围/State → 独立 Goal → 路由澄清或受控阻断 → unsupported / Worker succeeded。

普通 Registry 工具：`[]`；业务查询计数 0；固定分类响应尝试 0。P6 通过内部协调器执行，不应把 Registry 空数组误认成零分类。

P6 准备诊断：status=`blocked`，reason=`missing_trusted_ranking`。其中 classification_not_enabled 是准备阶段遗留描述，不能覆盖后续 execution/publication 的真实结果。

实际回答：

> 当前没有同会话、同身份且范围一致的可信负面排名，请先刷新统计。

### 5）人工候选方案与安全风险

**选项 A：承认内容意图、澄清缺失条件（建议，未批准）**

新标注身份；当前页面含 053/055，不能从旧身份拿排名或默认单 SKU/negative。 将“具体体现”视为主题内容意图，Goal 仅部分明确，尚无执行授权。

| 需要形成的业务契约 | 候选预期 |
|---|---|
| RequestGoal | `{"aspect":null,"levels":["themes"],"sentiment":null}` |
| Router / user_intent | `{"needs_clarification":true}` |
| Planner / analysis_intent | `"not_finalized"` |
| query_plan | `{"analysis_type":null,"aspects":[],"blocked":true,"sentiment":null}` |
| required_tools（计划要求） | `[]` |
| task_acceptance | `{"representation_rule":"absent_or_explicit_failure","permitted_representations":[null,{"status":"failed","complete":false}],"complete_true_forbidden":true}` |
| Agent / Worker 终态 | `{"agent":"unsupported","worker":"succeeded"}` |
| 轮后 pending_goal | `"absent"` |

执行约束：`{"business_query_executions":0,"classification_attempts":0,"must_not_publish_evidence":true,"catalog_reads_before_planning_are_not_business_queries":true}`。

- 风险：不得默认 negative、恢复旧排名或执行任一主题工具。
- 风险：Worker succeeded 只表示已正确回复澄清，不代表主题完成。

与当前实现的关系：待人工选定后才可形成正式预期；当前不判 PASS。

**选项 B：先澄清完整任务、暂不冻结 Goal（备选，未批准）**

新标注身份；当前页面含 053/055，不能从旧身份拿排名或默认单 SKU/negative。 先确认业务层次及缺失条件，原问题仅作为未决请求保留。

| 需要形成的业务契约 | 候选预期 |
|---|---|
| RequestGoal | `null` |
| Router / user_intent | `{"needs_clarification":true}` |
| Planner / analysis_intent | `"not_finalized"` |
| query_plan | `{"analysis_type":null,"aspects":[],"blocked":true,"sentiment":null}` |
| required_tools（计划要求） | `[]` |
| task_acceptance | `{"representation_rule":"absent_or_explicit_failure","permitted_representations":[null,{"status":"failed","complete":false}],"complete_true_forbidden":true}` |
| Agent / Worker 终态 | `{"agent":"unsupported","worker":"succeeded"}` |
| 轮后 pending_goal | `"absent"` |

执行约束：`{"business_query_executions":0,"classification_attempts":0,"must_not_publish_evidence":true,"catalog_reads_before_planning_are_not_business_queries":true}`。

- 风险：不能因 Goal=None 而进入无约束 Planner 或工具。
- 表示说明：Goal=None 在此表示未冻结，不表示用户提出了 RAG 请求。
- 表示说明：两方案安全执行边界相同，只在 Goal 形成时点及澄清范围上不同。

与当前实现的关系：若选择不冻结 Goal，现有提前建立 themes Goal 的代码可能需要后续独立对齐；本轮不修改。

### 6）仍可能暴露的问题与人工填写

原始 result.analysis_intent=aspect_drilldown、sentiment_intent=negative 只是 P6 候选路由遗留；若业务/API把它当最终负向意图，可能误导。需裁决是否只允许在诊断层保留。

最终选择：**未填写**。理由：**未填写**。裁决人/时间：**未填写**。

---

## G11-T2 · 跨会话不继承可信排名

**分类：条件不充分；澄清/Goal 表示待裁决。当前裁决状态：PENDING_HUMAN。**

### 1）用户问题与必要历史

1. “100013588053的差评主要集中在哪些方面？”
2. “具体体现在哪里？”；会话变为 `new-session`

P6 开关：该组开启离线准备/执行/发布能力。页面初始可选 053/055；时间窗口 2023-02-01～02-28 UTC，后续范围变化以上述本轮元数据为准。

### 2）原冻结预期与理由

生成器显式传入新 conversation_override、sentiment=None、product=None、status=clarify，意在隔离会话。

v1 实际已写 sentiment=null、澄清、零工具；旧 REVIEW 说明中的“预期 negative”不符合冻结文件。not_completed 仍是待修正的验收标签。

<details>
<summary>查看 v1 的完整逐字段预期（原样）</summary>

```json
{
  "expected_analysis_intent": "not_finalized",
  "expected_analysis_state": {
    "pending_goal": "absent",
    "ranking_basis": "absent"
  },
  "expected_final_status": {
    "agent": "unsupported",
    "worker": "succeeded"
  },
  "expected_pending_goal": "absent",
  "expected_query_plan": {
    "analysis_type": null,
    "aspects": [],
    "blocked": true,
    "sentiment": null
  },
  "expected_request_goal": {
    "aspect": null,
    "levels": [
      "themes"
    ],
    "sentiment": null
  },
  "expected_required_tools": [],
  "expected_scope": {
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "product_ids": [
      "100013588053",
      "100013588055"
    ]
  },
  "expected_task_acceptance": {
    "complete": false,
    "status": "not_completed"
  },
  "expected_user_intent": {
    "needs_clarification": true
  }
}
```

</details>

### 3）源码契约依据

- **C01**：[analysis_context.py:11](E:/AllProject/data-analysis-agent/src/data_analysis_agent/jobs/analysis_context.py:11) — 同会话、同标注身份和合法范围才恢复；用户原文条件与可信事实分离。
- **C02**：[analysis_state.py:37](E:/AllProject/data-analysis-agent/src/data_analysis_agent/jobs/analysis_state.py:37) — 排名与待办分别严格验收、精确范围投影；当前 pending 恢复仅负向、无单维度、单商品的合法澄清任务。
- **C06**：[reviews.py:122](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/reviews.py:122) — 缺可信排名先澄清；stale 准备错误走 unsupported；Goal 已有但未完成时任务验收 complete=false。
- **C08**：[review_intent.py:115](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_intent.py:115) — 未明确任务不执行；RAG 省略追问先澄清；当前短情感追问可承接待完成 themes，但该代码不裁决业务语义。
- **C09**：[review_acceptance.py:41](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_acceptance.py:41) — 原始 Goal 在 Planner 前独立形成；not_completed 不是任务验收状态，未完成不能 complete=true。

独立建议理由：推荐根据本轮明示信息形成有限内容 Goal，缺条件就澄清；依据 C01/C08，而非实际输出碰巧相同。

### 4）实际输出与执行轨迹（只作证据）

<details>
<summary>查看实际九字段/范围及计数（不是标准答案）</summary>

```json
{
  "scope": {
    "model": "stage1-labels",
    "base_url": "https://labels.stage1.invalid/v1",
    "prompt_version": "stage1-labels-v1",
    "product_ids": [
      "100013588053",
      "100013588055"
    ],
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "as_of": null,
    "source": "reviews_csv",
    "dataset_version": "stage1-synthetic-v1",
    "baseline_from": "2023-01-01",
    "baseline_to": "2023-01-31"
  },
  "analysis_state": {
    "ranking_basis": "absent",
    "pending_goal": "absent"
  },
  "post_state": {
    "ranking_basis": "absent",
    "pending_goal": "absent"
  },
  "request_goal": {
    "original_question": "具体体现在哪里？",
    "levels": [
      "themes"
    ],
    "aspect": null,
    "sentiment": null
  },
  "user_intent": {
    "needs_clarification": true,
    "clarification_question": "当前没有同会话、同身份且范围一致的可信负面排名，请先刷新统计。",
    "resolved_product_ids": [
      "100013588053",
      "100013588055"
    ],
    "effective_time_range": {
      "date_from": "2023-02-01",
      "date_to": "2023-02-28"
    }
  },
  "raw_analysis_intent": "aspect_drilldown",
  "raw_sentiment_intent": "negative",
  "query_plan": null,
  "task_acceptance": null,
  "final_status": {
    "agent": "unsupported",
    "worker": "succeeded"
  },
  "query_executions": 0,
  "classification_model_attempts": 0
}
```

</details>

实际流程：用户请求 → 范围/State → 独立 Goal → 路由澄清或受控阻断 → unsupported / Worker succeeded。

普通 Registry 工具：`[]`；业务查询计数 0；固定分类响应尝试 0。P6 通过内部协调器执行，不应把 Registry 空数组误认成零分类。

P6 准备诊断：status=`blocked`，reason=`missing_trusted_ranking`。其中 classification_not_enabled 是准备阶段遗留描述，不能覆盖后续 execution/publication 的真实结果。

实际回答：

> 当前没有同会话、同身份且范围一致的可信负面排名，请先刷新统计。

### 5）人工候选方案与安全风险

**选项 A：承认内容意图、澄清缺失条件（建议，未批准）**

新会话；旧会话任务仍可作为审计背景展示，但不能作为当前用户事实或条件。 将“具体体现”视为主题内容意图，Goal 仅部分明确，尚无执行授权。

| 需要形成的业务契约 | 候选预期 |
|---|---|
| RequestGoal | `{"aspect":null,"levels":["themes"],"sentiment":null}` |
| Router / user_intent | `{"needs_clarification":true}` |
| Planner / analysis_intent | `"not_finalized"` |
| query_plan | `{"analysis_type":null,"aspects":[],"blocked":true,"sentiment":null}` |
| required_tools（计划要求） | `[]` |
| task_acceptance | `{"representation_rule":"absent_or_explicit_failure","permitted_representations":[null,{"status":"failed","complete":false}],"complete_true_forbidden":true}` |
| Agent / Worker 终态 | `{"agent":"unsupported","worker":"succeeded"}` |
| 轮后 pending_goal | `"absent"` |

执行约束：`{"business_query_executions":0,"classification_attempts":0,"must_not_publish_evidence":true,"catalog_reads_before_planning_are_not_business_queries":true}`。

- 风险：不得默认 negative、恢复旧排名或执行任一主题工具。
- 风险：Worker succeeded 只表示已正确回复澄清，不代表主题完成。

与当前实现的关系：待人工选定后才可形成正式预期；当前不判 PASS。

**选项 B：先澄清完整任务、暂不冻结 Goal（备选，未批准）**

新会话；旧会话任务仍可作为审计背景展示，但不能作为当前用户事实或条件。 先确认业务层次及缺失条件，原问题仅作为未决请求保留。

| 需要形成的业务契约 | 候选预期 |
|---|---|
| RequestGoal | `null` |
| Router / user_intent | `{"needs_clarification":true}` |
| Planner / analysis_intent | `"not_finalized"` |
| query_plan | `{"analysis_type":null,"aspects":[],"blocked":true,"sentiment":null}` |
| required_tools（计划要求） | `[]` |
| task_acceptance | `{"representation_rule":"absent_or_explicit_failure","permitted_representations":[null,{"status":"failed","complete":false}],"complete_true_forbidden":true}` |
| Agent / Worker 终态 | `{"agent":"unsupported","worker":"succeeded"}` |
| 轮后 pending_goal | `"absent"` |

执行约束：`{"business_query_executions":0,"classification_attempts":0,"must_not_publish_evidence":true,"catalog_reads_before_planning_are_not_business_queries":true}`。

- 风险：不能因 Goal=None 而进入无约束 Planner 或工具。
- 表示说明：Goal=None 在此表示未冻结，不表示用户提出了 RAG 请求。
- 表示说明：两方案安全执行边界相同，只在 Goal 形成时点及澄清范围上不同。

与当前实现的关系：若选择不冻结 Goal，现有提前建立 themes Goal 的代码可能需要后续独立对齐；本轮不修改。

### 6）仍可能暴露的问题与人工填写

原始 result.analysis_intent=aspect_drilldown、sentiment_intent=negative 只是 P6 候选路由遗留；若业务/API把它当最终负向意图，可能误导。需裁决是否只允许在诊断层保留。

最终选择：**未填写**。理由：**未填写**。裁决人/时间：**未填写**。

---

## G12-T1 · 没有可信历史的具体追问

**分类：条件不充分；澄清/Goal 表示待裁决。当前裁决状态：PENDING_HUMAN。**

### 1）用户问题与必要历史

1. “100013588053具体体现在哪里？”

P6 开关：该组开启离线准备/执行/发布能力。页面初始可选 053/055；时间窗口 2023-02-01～02-28 UTC，后续范围变化以上述本轮元数据为准。

### 2）原冻结预期与理由

问题本轮写出 053，生成器显式情感 None、clarify、next_pending=False；没有同会话排名依据。

v1 实际已写 sentiment=null、澄清、零工具；旧 REVIEW 说明中的“预期 negative”不符合冻结文件。not_completed 仍是待修正的验收标签。

<details>
<summary>查看 v1 的完整逐字段预期（原样）</summary>

```json
{
  "expected_analysis_intent": "not_finalized",
  "expected_analysis_state": {
    "pending_goal": "absent",
    "ranking_basis": "absent"
  },
  "expected_final_status": {
    "agent": "unsupported",
    "worker": "succeeded"
  },
  "expected_pending_goal": "absent",
  "expected_query_plan": {
    "analysis_type": null,
    "aspects": [],
    "blocked": true,
    "sentiment": null
  },
  "expected_request_goal": {
    "aspect": null,
    "levels": [
      "themes"
    ],
    "sentiment": null
  },
  "expected_required_tools": [],
  "expected_scope": {
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "product_ids": [
      "100013588053"
    ]
  },
  "expected_task_acceptance": {
    "complete": false,
    "status": "not_completed"
  },
  "expected_user_intent": {
    "needs_clarification": true
  }
}
```

</details>

### 3）源码契约依据

- **C01**：[analysis_context.py:11](E:/AllProject/data-analysis-agent/src/data_analysis_agent/jobs/analysis_context.py:11) — 同会话、同标注身份和合法范围才恢复；用户原文条件与可信事实分离。
- **C02**：[analysis_state.py:37](E:/AllProject/data-analysis-agent/src/data_analysis_agent/jobs/analysis_state.py:37) — 排名与待办分别严格验收、精确范围投影；当前 pending 恢复仅负向、无单维度、单商品的合法澄清任务。
- **C06**：[reviews.py:122](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/reviews.py:122) — 缺可信排名先澄清；stale 准备错误走 unsupported；Goal 已有但未完成时任务验收 complete=false。
- **C08**：[review_intent.py:115](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_intent.py:115) — 未明确任务不执行；RAG 省略追问先澄清；当前短情感追问可承接待完成 themes，但该代码不裁决业务语义。
- **C09**：[review_acceptance.py:41](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_acceptance.py:41) — 原始 Goal 在 Planner 前独立形成；not_completed 不是任务验收状态，未完成不能 complete=true。

独立建议理由：推荐根据本轮明示信息形成有限内容 Goal，缺条件就澄清；依据 C01/C08，而非实际输出碰巧相同。

### 4）实际输出与执行轨迹（只作证据）

<details>
<summary>查看实际九字段/范围及计数（不是标准答案）</summary>

```json
{
  "scope": {
    "model": "stage1-labels",
    "base_url": "https://labels.stage1.invalid/v1",
    "prompt_version": "stage1-labels-v1",
    "product_ids": [
      "100013588053"
    ],
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "as_of": null,
    "source": "reviews_csv",
    "dataset_version": "stage1-synthetic-v1",
    "baseline_from": "2023-01-01",
    "baseline_to": "2023-01-31"
  },
  "analysis_state": {
    "ranking_basis": "absent",
    "pending_goal": "absent"
  },
  "post_state": {
    "ranking_basis": "absent",
    "pending_goal": "absent"
  },
  "request_goal": {
    "original_question": "100013588053具体体现在哪里？",
    "levels": [
      "themes"
    ],
    "aspect": null,
    "sentiment": null
  },
  "user_intent": {
    "needs_clarification": true,
    "clarification_question": "当前没有同会话、同身份且范围一致的可信负面排名，请先刷新统计。",
    "resolved_product_ids": [
      "100013588053"
    ],
    "effective_time_range": {
      "date_from": "2023-02-01",
      "date_to": "2023-02-28"
    }
  },
  "raw_analysis_intent": "aspect_drilldown",
  "raw_sentiment_intent": "negative",
  "query_plan": null,
  "task_acceptance": null,
  "final_status": {
    "agent": "unsupported",
    "worker": "succeeded"
  },
  "query_executions": 0,
  "classification_model_attempts": 0
}
```

</details>

实际流程：用户请求 → 范围/State → 独立 Goal → 路由澄清或受控阻断 → unsupported / Worker succeeded。

普通 Registry 工具：`[]`；业务查询计数 0；固定分类响应尝试 0。P6 通过内部协调器执行，不应把 Registry 空数组误认成零分类。

P6 准备诊断：status=`blocked`，reason=`missing_trusted_ranking`。其中 classification_not_enabled 是准备阶段遗留描述，不能覆盖后续 execution/publication 的真实结果。

实际回答：

> 当前没有同会话、同身份且范围一致的可信负面排名，请先刷新统计。

### 5）人工候选方案与安全风险

**选项 A：承认内容意图、澄清缺失条件（建议，未批准）**

本轮明确 053，可以绑定 SKU；没有任何历史，正/负情感和具体方向须问清。 将“具体体现”视为主题内容意图，Goal 仅部分明确，尚无执行授权。

| 需要形成的业务契约 | 候选预期 |
|---|---|
| RequestGoal | `{"aspect":null,"levels":["themes"],"sentiment":null}` |
| Router / user_intent | `{"needs_clarification":true}` |
| Planner / analysis_intent | `"not_finalized"` |
| query_plan | `{"analysis_type":null,"aspects":[],"blocked":true,"sentiment":null}` |
| required_tools（计划要求） | `[]` |
| task_acceptance | `{"representation_rule":"absent_or_explicit_failure","permitted_representations":[null,{"status":"failed","complete":false}],"complete_true_forbidden":true}` |
| Agent / Worker 终态 | `{"agent":"unsupported","worker":"succeeded"}` |
| 轮后 pending_goal | `"absent"` |

执行约束：`{"business_query_executions":0,"classification_attempts":0,"must_not_publish_evidence":true,"catalog_reads_before_planning_are_not_business_queries":true}`。

- 风险：不得默认 negative、恢复旧排名或执行任一主题工具。
- 风险：Worker succeeded 只表示已正确回复澄清，不代表主题完成。

与当前实现的关系：待人工选定后才可形成正式预期；当前不判 PASS。

**选项 B：先澄清完整任务、暂不冻结 Goal（备选，未批准）**

本轮明确 053，可以绑定 SKU；没有任何历史，正/负情感和具体方向须问清。 先确认业务层次及缺失条件，原问题仅作为未决请求保留。

| 需要形成的业务契约 | 候选预期 |
|---|---|
| RequestGoal | `null` |
| Router / user_intent | `{"needs_clarification":true}` |
| Planner / analysis_intent | `"not_finalized"` |
| query_plan | `{"analysis_type":null,"aspects":[],"blocked":true,"sentiment":null}` |
| required_tools（计划要求） | `[]` |
| task_acceptance | `{"representation_rule":"absent_or_explicit_failure","permitted_representations":[null,{"status":"failed","complete":false}],"complete_true_forbidden":true}` |
| Agent / Worker 终态 | `{"agent":"unsupported","worker":"succeeded"}` |
| 轮后 pending_goal | `"absent"` |

执行约束：`{"business_query_executions":0,"classification_attempts":0,"must_not_publish_evidence":true,"catalog_reads_before_planning_are_not_business_queries":true}`。

- 风险：不能因 Goal=None 而进入无约束 Planner 或工具。
- 表示说明：Goal=None 在此表示未冻结，不表示用户提出了 RAG 请求。
- 表示说明：两方案安全执行边界相同，只在 Goal 形成时点及澄清范围上不同。

与当前实现的关系：若选择不冻结 Goal，现有提前建立 themes Goal 的代码可能需要后续独立对齐；本轮不修改。

### 6）仍可能暴露的问题与人工填写

原始 result.analysis_intent=aspect_drilldown、sentiment_intent=negative 只是 P6 候选路由遗留；若业务/API把它当最终负向意图，可能误导。需裁决是否只允许在诊断层保留。

最终选择：**未填写**。理由：**未填写**。裁决人/时间：**未填写**。

---

## G12-T2 · 没有可信历史的具体追问

**分类：v1 范围约束陈旧；语言条件仍不充分。当前裁决状态：PENDING_HUMAN。**

### 1）用户问题与必要历史

1. “100013588053具体体现在哪里？”
2. “具体体现在哪里？”

P6 开关：该组开启离线准备/执行/发布能力。页面初始可选 053/055；时间窗口 2023-02-01～02-28 UTC，后续范围变化以上述本轮元数据为准。

### 2）原冻结预期与理由

生成器对第二轮显式 product=None，回到页面两商品；这里没有区分显式用户 SKU 条件与已完成事实。

v1 scope=053/055，与后来已验收 P0-01“用户条件不依赖分析成功”不一致；sentiment 原本就是 null，并非 negative。

<details>
<summary>查看 v1 的完整逐字段预期（原样）</summary>

```json
{
  "expected_analysis_intent": "not_finalized",
  "expected_analysis_state": {
    "pending_goal": "absent",
    "ranking_basis": "absent"
  },
  "expected_final_status": {
    "agent": "unsupported",
    "worker": "succeeded"
  },
  "expected_pending_goal": "absent",
  "expected_query_plan": {
    "analysis_type": null,
    "aspects": [],
    "blocked": true,
    "sentiment": null
  },
  "expected_request_goal": {
    "aspect": null,
    "levels": [
      "themes"
    ],
    "sentiment": null
  },
  "expected_required_tools": [],
  "expected_scope": {
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "product_ids": [
      "100013588053",
      "100013588055"
    ]
  },
  "expected_task_acceptance": {
    "complete": false,
    "status": "not_completed"
  },
  "expected_user_intent": {
    "needs_clarification": true
  }
}
```

</details>

### 3）源码契约依据

- **C01**：[analysis_context.py:11](E:/AllProject/data-analysis-agent/src/data_analysis_agent/jobs/analysis_context.py:11) — 同会话、同标注身份和合法范围才恢复；用户原文条件与可信事实分离。
- **C02**：[analysis_state.py:37](E:/AllProject/data-analysis-agent/src/data_analysis_agent/jobs/analysis_state.py:37) — 排名与待办分别严格验收、精确范围投影；当前 pending 恢复仅负向、无单维度、单商品的合法澄清任务。
- **C08**：[review_intent.py:115](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_intent.py:115) — 未明确任务不执行；RAG 省略追问先澄清；当前短情感追问可承接待完成 themes，但该代码不裁决业务语义。

独立建议理由：单 SKU 由此前用户原文及同会话校验独立确定；未执行事实仍为空。

### 4）实际输出与执行轨迹（只作证据）

<details>
<summary>查看实际九字段/范围及计数（不是标准答案）</summary>

```json
{
  "scope": {
    "model": "stage1-labels",
    "base_url": "https://labels.stage1.invalid/v1",
    "prompt_version": "stage1-labels-v1",
    "product_ids": [
      "100013588053"
    ],
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "as_of": null,
    "source": "reviews_csv",
    "dataset_version": "stage1-synthetic-v1",
    "baseline_from": "2023-01-01",
    "baseline_to": "2023-01-31"
  },
  "analysis_state": {
    "ranking_basis": "absent",
    "pending_goal": "absent"
  },
  "post_state": {
    "ranking_basis": "absent",
    "pending_goal": "absent"
  },
  "request_goal": {
    "original_question": "具体体现在哪里？",
    "levels": [
      "themes"
    ],
    "aspect": null,
    "sentiment": null
  },
  "user_intent": {
    "needs_clarification": true,
    "clarification_question": "当前没有同会话、同身份且范围一致的可信负面排名，请先刷新统计。",
    "resolved_product_ids": [
      "100013588053"
    ],
    "effective_time_range": {
      "date_from": "2023-02-01",
      "date_to": "2023-02-28"
    }
  },
  "raw_analysis_intent": "aspect_drilldown",
  "raw_sentiment_intent": "negative",
  "query_plan": null,
  "task_acceptance": null,
  "final_status": {
    "agent": "unsupported",
    "worker": "succeeded"
  },
  "query_executions": 0,
  "classification_model_attempts": 0
}
```

</details>

实际流程：用户请求 → 范围/State → 独立 Goal → 路由澄清或受控阻断 → unsupported / Worker succeeded。

普通 Registry 工具：`[]`；业务查询计数 0；固定分类响应尝试 0。P6 通过内部协调器执行，不应把 Registry 空数组误认成零分类。

P6 准备诊断：status=`blocked`，reason=`missing_trusted_ranking`。其中 classification_not_enabled 是准备阶段遗留描述，不能覆盖后续 execution/publication 的真实结果。

实际回答：

> 当前没有同会话、同身份且范围一致的可信负面排名，请先刷新统计。

### 5）人工候选方案与安全风险

**选项 A：继承显式 SKU，继续澄清情感/层次（建议，未批准）**

同会话同身份、未改选择；上一轮未完成不抹掉用户明确写出的 053。只继承条件，不继承排名、统计、情感或完成事实。

| 需要形成的业务契约 | 候选预期 |
|---|---|
| RequestGoal | `{"aspect":null,"levels":["themes"],"sentiment":null}` |
| Router / user_intent | `{"needs_clarification":true}` |
| Planner / analysis_intent | `"not_finalized"` |
| query_plan | `{"analysis_type":null,"aspects":[],"blocked":true,"sentiment":null}` |
| required_tools（计划要求） | `[]` |
| task_acceptance | `{"representation_rule":"absent_or_explicit_failure","permitted_representations":[null,{"status":"failed","complete":false}],"complete_true_forbidden":true}` |
| Agent / Worker 终态 | `{"agent":"unsupported","worker":"succeeded"}` |
| 轮后 pending_goal | `"absent"` |

执行约束：`{"business_query_executions":0,"classification_attempts":0,"must_not_publish_evidence":true,"catalog_reads_before_planning_are_not_business_queries":true}`。

- 风险：失败条件不能升级为可信事实。
- 风险：不能回退到页面两商品并执行合并分析。

与当前实现的关系：待人工选定后才可形成正式预期；当前不判 PASS。

该项没有把违反既有契约的 ok/complete=true 或错误范围列成“同等合法备选”。可以暂不裁决；若希望变更现有契约，应另行明确提出，不在此表中伪造多个正确答案。

### 6）仍可能暴露的问题与人工填写

当前无业务查询；候选路由 raw negative 与未定情感的表示仍需审查，不能直接当最终语义。

最终选择：**未填写**。理由：**未填写**。裁决人/时间：**未填写**。

---

## G17-T2 · 多维中间维度失败后继续单维

**分类：原预期状态定义错误（契约唯一）。当前裁决状态：PENDING_HUMAN。**

### 1）用户问题与必要历史

1. “100013588053的差评主要集中在哪些方面？”
2. “具体体现在哪里？”；离线故障/数据模式：`fail_effectiveness`

P6 开关：该组开启离线准备/执行/发布能力。页面初始可选 053/055；时间窗口 2023-02-01～02-28 UTC，后续范围变化以上述本轮元数据为准。

历史合成负面排名来源：053，D=8；service=5、effectiveness=4、logistics=3、quality=2、price=1、packaging=0；冻结必做顺序 service → effectiveness → logistics。这是原合成夹具，不是真实商品频次。

### 2）原冻结预期与理由

明确冻结三维任务，注入 fail_effectiveness；turn() 把 status=partial 映成 task_acceptance.failed。

v1 已要求 Agent partial/Worker partial，却把 task_acceptance.status 写为 failed；是生成器状态映射错误。

<details>
<summary>查看 v1 的完整逐字段预期（原样）</summary>

```json
{
  "expected_analysis_intent": "aspect_drilldown",
  "expected_analysis_state": {
    "pending_goal": "absent",
    "ranking_basis": "present"
  },
  "expected_final_status": {
    "agent": "partial",
    "worker": "partial"
  },
  "expected_pending_goal": "absent",
  "expected_query_plan": {
    "analysis_type": "aspect_drilldown",
    "aspects": [
      "service",
      "effectiveness",
      "logistics"
    ],
    "blocked": false,
    "sentiment": "negative"
  },
  "expected_request_goal": {
    "aspect": null,
    "levels": [
      "themes"
    ],
    "required_aspects": [
      "service",
      "effectiveness",
      "logistics"
    ],
    "sentiment": "negative",
    "version": "review-request-goal-p6-v1"
  },
  "expected_required_tools": [
    "get_aspect_drilldown"
  ],
  "expected_scope": {
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "product_ids": [
      "100013588053"
    ]
  },
  "expected_task_acceptance": {
    "complete": false,
    "status": "failed"
  },
  "expected_user_intent": {
    "aspect": null,
    "query_type": "aspect_drilldown",
    "sentiment": "negative"
  }
}
```

</details>

### 3）源码契约依据

- **C03**：[review_multi_aspect.py:108](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_multi_aspect.py:108) — P6 必做维度不变；全部完成才 complete=true，有已验收成功维且未全完成则 partial。
- **C04**：[review_publication.py:99](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_publication.py:99) — 只公开 publishable_aspects，父 partial 对外 partial、complete=false，逐维原因保留。
- **C07**：[worker.py:188](E:/AllProject/data-analysis-agent/src/data_analysis_agent/jobs/worker.py:188) — 受控 unsupported 可 succeeded；P6 通过校验的 partial 为 Worker partial；error/校验失败为 failed。
- **C11**：[freeze_agent_stage1_evaluation.py:31](E:/AllProject/data-analysis-agent/scripts/freeze_agent_stage1_evaluation.py:31) — 原作者将 partial 映成 failed；not_completed 为预期生成标签；G30 原本已声明待人工裁决。

独立建议理由：C03/C04 明文规定部分成功的父级及公开状态；不以实际当前 partial 反推规范。

### 4）实际输出与执行轨迹（只作证据）

<details>
<summary>查看实际九字段/范围及计数（不是标准答案）</summary>

```json
{
  "scope": {
    "model": "stage1-labels",
    "base_url": "https://labels.stage1.invalid/v1",
    "prompt_version": "stage1-labels-v1",
    "product_ids": [
      "100013588053"
    ],
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "as_of": null,
    "source": "reviews_csv",
    "dataset_version": "stage1-synthetic-v1",
    "baseline_from": "2023-01-01",
    "baseline_to": "2023-01-31"
  },
  "analysis_state": {
    "ranking_basis": "present",
    "pending_goal": "absent"
  },
  "post_state": {
    "ranking_basis": "present",
    "pending_goal": "absent"
  },
  "request_goal": {
    "original_question": "具体体现在哪里？",
    "levels": [
      "themes"
    ],
    "aspect": null,
    "sentiment": "negative",
    "required_aspects": [
      "service",
      "effectiveness",
      "logistics"
    ],
    "version": "review-request-goal-p6-v1",
    "analysis_mode": "multi_aspect_negative_drilldown",
    "per_aspect_level": "themes"
  },
  "user_intent": {
    "query_type": "aspect_drilldown",
    "sentiment": "negative",
    "aspect": null,
    "sub_intent": "drill_down",
    "needs_clarification": false,
    "resolved_product_ids": [
      "100013588053"
    ],
    "effective_time_range": {
      "date_from": "2023-02-01",
      "date_to": "2023-02-28"
    }
  },
  "raw_analysis_intent": "aspect_drilldown",
  "raw_sentiment_intent": "negative",
  "query_plan": {
    "analysis_type": "aspect_drilldown",
    "requirements": [
      {
        "tool": "get_aspect_drilldown",
        "parameters": {
          "product_ids": [
            "100013588053"
          ],
          "aspect": "service",
          "sentiment": "negative"
        }
      },
      {
        "tool": "get_aspect_drilldown",
        "parameters": {
          "product_ids": [
            "100013588053"
          ],
          "aspect": "effectiveness",
          "sentiment": "negative"
        }
      },
      {
        "tool": "get_aspect_drilldown",
        "parameters": {
          "product_ids": [
            "100013588053"
          ],
          "aspect": "logistics",
          "sentiment": "negative"
        }
      }
    ],
    "required_tools": [
      "get_aspect_drilldown"
    ]
  },
  "task_acceptance": {
    "complete": false,
    "publishable_aspects": [
      "service",
      "logistics"
    ],
    "required_aspects": [
      "service",
      "effectiveness",
      "logistics"
    ],
    "status": "partial",
    "parent_status": "partial"
  },
  "final_status": {
    "agent": "partial",
    "worker": "partial"
  },
  "query_executions": 4,
  "classification_model_attempts": 5
}
```

</details>

实际流程：用户请求 → 范围/State → 独立 Goal → P6 冻结计划/准备/串行模拟分类 → partial / Worker partial。

普通 Registry 工具：`[]`；业务查询计数 4；固定分类响应尝试 5。P6 通过内部协调器执行，不应把 Registry 空数组误认成零分类。

P6 准备诊断：status=`ready`，reason=`classification_not_enabled`。其中 classification_not_enabled 是准备阶段遗留描述，不能覆盖后续 execution/publication 的真实结果。

| 维度 | 子状态 | M_a | S_a | 实际原因 |
|---|---|---:|---:|---|
| service | complete | 5 | 5 | 已通过逐维证据验收 |
| effectiveness | failed | 4 | 4 | 主题模型请求超时，结果未被验收 |
| logistics | complete | 3 | 3 | 已通过逐维证据验收 |

实际可发布维度：`["service","logistics"]`。失败维保留，完整 required_aspects 未缩减。

### 5）人工候选方案与安全风险

**选项 A：按既有 P6 契约判为 partial（建议，未批准）**

完整目标仍为三个维度；effectiveness 分类失败，其余维度独立通过。发布成功子维度，父任务不完整。

| 需要形成的业务契约 | 候选预期 |
|---|---|
| RequestGoal | `{"aspect":null,"levels":["themes"],"required_aspects":["service","effectiveness","logistics"],"sentiment":"negative","version":"review-request-goal-p6-v1"}` |
| Router / user_intent | `{"aspect":null,"query_type":"aspect_drilldown","sentiment":"negative"}` |
| Planner / analysis_intent | `"aspect_drilldown"` |
| query_plan | `{"analysis_type":"aspect_drilldown","aspects":["service","effectiveness","logistics"],"blocked":false,"sentiment":"negative"}` |
| required_tools（计划要求） | `["get_aspect_drilldown"]` |
| task_acceptance | `{"status":"partial","complete":false,"parent_status":"partial"}` |
| Agent / Worker 终态 | `{"agent":"partial","worker":"partial"}` |
| 轮后 pending_goal | `"absent"` |

执行约束：`{"frozen_order":["service","effectiveness","logistics"],"classification_attempts":5,"failed_aspect":"effectiveness","child_statuses":{"service":"complete","effectiveness":"failed","logistics":"complete"},"publishable_aspects":["service","logistics"],"all_required_aspects_retained":true,"failed_aspect_topics_published":false}`。

- 风险：不得将成功子集替换 required_aspects。
- 风险：不得改成 ok/complete=true，也不得把局部失败误称全局来源失败。

与当前实现的关系：待人工选定后才可形成正式预期；当前不判 PASS。

该项没有把违反既有契约的 ok/complete=true 或错误范围列成“同等合法备选”。可以暂不裁决；若希望变更现有契约，应另行明确提出，不在此表中伪造多个正确答案。

### 6）仍可能暴露的问题与人工填写

本例未见全局完整性失配；没有依据把它提升为全局 failed。所有源码证据仍须按既有 Validator 校验，候选主题不等于人工确认。

最终选择：**未填写**。理由：**未填写**。裁决人/时间：**未填写**。

---

## G18-T2 · 多维第一维失败保留全部目标

**分类：原预期状态定义错误（契约唯一）。当前裁决状态：PENDING_HUMAN。**

### 1）用户问题与必要历史

1. “100013588053的差评主要集中在哪些方面？”
2. “具体体现在哪里？”；离线故障/数据模式：`fail_service`

P6 开关：该组开启离线准备/执行/发布能力。页面初始可选 053/055；时间窗口 2023-02-01～02-28 UTC，后续范围变化以上述本轮元数据为准。

历史合成负面排名来源：053，D=8；service=5、effectiveness=4、logistics=3、quality=2、price=1、packaging=0；冻结必做顺序 service → effectiveness → logistics。这是原合成夹具，不是真实商品频次。

### 2）原冻结预期与理由

明确冻结三维任务，注入 fail_service；同一个 partial→failed 生成器映射造成冲突。

v1 已要求 Agent partial/Worker partial，却把 task_acceptance.status 写为 failed；是生成器状态映射错误。

<details>
<summary>查看 v1 的完整逐字段预期（原样）</summary>

```json
{
  "expected_analysis_intent": "aspect_drilldown",
  "expected_analysis_state": {
    "pending_goal": "absent",
    "ranking_basis": "present"
  },
  "expected_final_status": {
    "agent": "partial",
    "worker": "partial"
  },
  "expected_pending_goal": "absent",
  "expected_query_plan": {
    "analysis_type": "aspect_drilldown",
    "aspects": [
      "service",
      "effectiveness",
      "logistics"
    ],
    "blocked": false,
    "sentiment": "negative"
  },
  "expected_request_goal": {
    "aspect": null,
    "levels": [
      "themes"
    ],
    "required_aspects": [
      "service",
      "effectiveness",
      "logistics"
    ],
    "sentiment": "negative",
    "version": "review-request-goal-p6-v1"
  },
  "expected_required_tools": [
    "get_aspect_drilldown"
  ],
  "expected_scope": {
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "product_ids": [
      "100013588053"
    ]
  },
  "expected_task_acceptance": {
    "complete": false,
    "status": "failed"
  },
  "expected_user_intent": {
    "aspect": null,
    "query_type": "aspect_drilldown",
    "sentiment": "negative"
  }
}
```

</details>

### 3）源码契约依据

- **C03**：[review_multi_aspect.py:108](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_multi_aspect.py:108) — P6 必做维度不变；全部完成才 complete=true，有已验收成功维且未全完成则 partial。
- **C04**：[review_publication.py:99](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_publication.py:99) — 只公开 publishable_aspects，父 partial 对外 partial、complete=false，逐维原因保留。
- **C07**：[worker.py:188](E:/AllProject/data-analysis-agent/src/data_analysis_agent/jobs/worker.py:188) — 受控 unsupported 可 succeeded；P6 通过校验的 partial 为 Worker partial；error/校验失败为 failed。
- **C11**：[freeze_agent_stage1_evaluation.py:31](E:/AllProject/data-analysis-agent/scripts/freeze_agent_stage1_evaluation.py:31) — 原作者将 partial 映成 failed；not_completed 为预期生成标签；G30 原本已声明待人工裁决。

独立建议理由：C03/C04 明文规定部分成功的父级及公开状态；不以实际当前 partial 反推规范。

### 4）实际输出与执行轨迹（只作证据）

<details>
<summary>查看实际九字段/范围及计数（不是标准答案）</summary>

```json
{
  "scope": {
    "model": "stage1-labels",
    "base_url": "https://labels.stage1.invalid/v1",
    "prompt_version": "stage1-labels-v1",
    "product_ids": [
      "100013588053"
    ],
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "as_of": null,
    "source": "reviews_csv",
    "dataset_version": "stage1-synthetic-v1",
    "baseline_from": "2023-01-01",
    "baseline_to": "2023-01-31"
  },
  "analysis_state": {
    "ranking_basis": "present",
    "pending_goal": "absent"
  },
  "post_state": {
    "ranking_basis": "present",
    "pending_goal": "absent"
  },
  "request_goal": {
    "original_question": "具体体现在哪里？",
    "levels": [
      "themes"
    ],
    "aspect": null,
    "sentiment": "negative",
    "required_aspects": [
      "service",
      "effectiveness",
      "logistics"
    ],
    "version": "review-request-goal-p6-v1",
    "analysis_mode": "multi_aspect_negative_drilldown",
    "per_aspect_level": "themes"
  },
  "user_intent": {
    "query_type": "aspect_drilldown",
    "sentiment": "negative",
    "aspect": null,
    "sub_intent": "drill_down",
    "needs_clarification": false,
    "resolved_product_ids": [
      "100013588053"
    ],
    "effective_time_range": {
      "date_from": "2023-02-01",
      "date_to": "2023-02-28"
    }
  },
  "raw_analysis_intent": "aspect_drilldown",
  "raw_sentiment_intent": "negative",
  "query_plan": {
    "analysis_type": "aspect_drilldown",
    "requirements": [
      {
        "tool": "get_aspect_drilldown",
        "parameters": {
          "product_ids": [
            "100013588053"
          ],
          "aspect": "service",
          "sentiment": "negative"
        }
      },
      {
        "tool": "get_aspect_drilldown",
        "parameters": {
          "product_ids": [
            "100013588053"
          ],
          "aspect": "effectiveness",
          "sentiment": "negative"
        }
      },
      {
        "tool": "get_aspect_drilldown",
        "parameters": {
          "product_ids": [
            "100013588053"
          ],
          "aspect": "logistics",
          "sentiment": "negative"
        }
      }
    ],
    "required_tools": [
      "get_aspect_drilldown"
    ]
  },
  "task_acceptance": {
    "complete": false,
    "publishable_aspects": [
      "effectiveness",
      "logistics"
    ],
    "required_aspects": [
      "service",
      "effectiveness",
      "logistics"
    ],
    "status": "partial",
    "parent_status": "partial"
  },
  "final_status": {
    "agent": "partial",
    "worker": "partial"
  },
  "query_executions": 4,
  "classification_model_attempts": 5
}
```

</details>

实际流程：用户请求 → 范围/State → 独立 Goal → P6 冻结计划/准备/串行模拟分类 → partial / Worker partial。

普通 Registry 工具：`[]`；业务查询计数 4；固定分类响应尝试 5。P6 通过内部协调器执行，不应把 Registry 空数组误认成零分类。

P6 准备诊断：status=`ready`，reason=`classification_not_enabled`。其中 classification_not_enabled 是准备阶段遗留描述，不能覆盖后续 execution/publication 的真实结果。

| 维度 | 子状态 | M_a | S_a | 实际原因 |
|---|---|---:|---:|---|
| service | failed | 5 | 5 | 主题模型请求超时，结果未被验收 |
| effectiveness | complete | 4 | 4 | 已通过逐维证据验收 |
| logistics | complete | 3 | 3 | 已通过逐维证据验收 |

实际可发布维度：`["effectiveness","logistics"]`。失败维保留，完整 required_aspects 未缩减。

### 5）人工候选方案与安全风险

**选项 A：按既有 P6 契约判为 partial（建议，未批准）**

完整目标仍为三个维度；service 分类失败，其余维度独立通过。发布成功子维度，父任务不完整。

| 需要形成的业务契约 | 候选预期 |
|---|---|
| RequestGoal | `{"aspect":null,"levels":["themes"],"required_aspects":["service","effectiveness","logistics"],"sentiment":"negative","version":"review-request-goal-p6-v1"}` |
| Router / user_intent | `{"aspect":null,"query_type":"aspect_drilldown","sentiment":"negative"}` |
| Planner / analysis_intent | `"aspect_drilldown"` |
| query_plan | `{"analysis_type":"aspect_drilldown","aspects":["service","effectiveness","logistics"],"blocked":false,"sentiment":"negative"}` |
| required_tools（计划要求） | `["get_aspect_drilldown"]` |
| task_acceptance | `{"status":"partial","complete":false,"parent_status":"partial"}` |
| Agent / Worker 终态 | `{"agent":"partial","worker":"partial"}` |
| 轮后 pending_goal | `"absent"` |

执行约束：`{"frozen_order":["service","effectiveness","logistics"],"classification_attempts":5,"failed_aspect":"service","child_statuses":{"service":"failed","effectiveness":"complete","logistics":"complete"},"publishable_aspects":["effectiveness","logistics"],"all_required_aspects_retained":true,"failed_aspect_topics_published":false}`。

- 风险：不得将成功子集替换 required_aspects。
- 风险：不得改成 ok/complete=true，也不得把局部失败误称全局来源失败。

与当前实现的关系：待人工选定后才可形成正式预期；当前不判 PASS。

该项没有把违反既有契约的 ok/complete=true 或错误范围列成“同等合法备选”。可以暂不裁决；若希望变更现有契约，应另行明确提出，不在此表中伪造多个正确答案。

### 6）仍可能暴露的问题与人工填写

本例未见全局完整性失配；没有依据把它提升为全局 failed。所有源码证据仍须按既有 Validator 校验，候选主题不等于人工确认。

最终选择：**未填写**。理由：**未填写**。裁决人/时间：**未填写**。

---

## G19-T2 · 多维最后一维失败保留全部目标

**分类：原预期状态定义错误（契约唯一）。当前裁决状态：PENDING_HUMAN。**

### 1）用户问题与必要历史

1. “100013588053的差评主要集中在哪些方面？”
2. “具体体现在哪里？”；离线故障/数据模式：`fail_logistics`

P6 开关：该组开启离线准备/执行/发布能力。页面初始可选 053/055；时间窗口 2023-02-01～02-28 UTC，后续范围变化以上述本轮元数据为准。

历史合成负面排名来源：053，D=8；service=5、effectiveness=4、logistics=3、quality=2、price=1、packaging=0；冻结必做顺序 service → effectiveness → logistics。这是原合成夹具，不是真实商品频次。

### 2）原冻结预期与理由

明确冻结三维任务，注入 fail_logistics；同一个 partial→failed 生成器映射造成冲突。

v1 已要求 Agent partial/Worker partial，却把 task_acceptance.status 写为 failed；是生成器状态映射错误。

<details>
<summary>查看 v1 的完整逐字段预期（原样）</summary>

```json
{
  "expected_analysis_intent": "aspect_drilldown",
  "expected_analysis_state": {
    "pending_goal": "absent",
    "ranking_basis": "present"
  },
  "expected_final_status": {
    "agent": "partial",
    "worker": "partial"
  },
  "expected_pending_goal": "absent",
  "expected_query_plan": {
    "analysis_type": "aspect_drilldown",
    "aspects": [
      "service",
      "effectiveness",
      "logistics"
    ],
    "blocked": false,
    "sentiment": "negative"
  },
  "expected_request_goal": {
    "aspect": null,
    "levels": [
      "themes"
    ],
    "required_aspects": [
      "service",
      "effectiveness",
      "logistics"
    ],
    "sentiment": "negative",
    "version": "review-request-goal-p6-v1"
  },
  "expected_required_tools": [
    "get_aspect_drilldown"
  ],
  "expected_scope": {
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "product_ids": [
      "100013588053"
    ]
  },
  "expected_task_acceptance": {
    "complete": false,
    "status": "failed"
  },
  "expected_user_intent": {
    "aspect": null,
    "query_type": "aspect_drilldown",
    "sentiment": "negative"
  }
}
```

</details>

### 3）源码契约依据

- **C03**：[review_multi_aspect.py:108](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_multi_aspect.py:108) — P6 必做维度不变；全部完成才 complete=true，有已验收成功维且未全完成则 partial。
- **C04**：[review_publication.py:99](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_publication.py:99) — 只公开 publishable_aspects，父 partial 对外 partial、complete=false，逐维原因保留。
- **C07**：[worker.py:188](E:/AllProject/data-analysis-agent/src/data_analysis_agent/jobs/worker.py:188) — 受控 unsupported 可 succeeded；P6 通过校验的 partial 为 Worker partial；error/校验失败为 failed。
- **C11**：[freeze_agent_stage1_evaluation.py:31](E:/AllProject/data-analysis-agent/scripts/freeze_agent_stage1_evaluation.py:31) — 原作者将 partial 映成 failed；not_completed 为预期生成标签；G30 原本已声明待人工裁决。

独立建议理由：C03/C04 明文规定部分成功的父级及公开状态；不以实际当前 partial 反推规范。

### 4）实际输出与执行轨迹（只作证据）

<details>
<summary>查看实际九字段/范围及计数（不是标准答案）</summary>

```json
{
  "scope": {
    "model": "stage1-labels",
    "base_url": "https://labels.stage1.invalid/v1",
    "prompt_version": "stage1-labels-v1",
    "product_ids": [
      "100013588053"
    ],
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "as_of": null,
    "source": "reviews_csv",
    "dataset_version": "stage1-synthetic-v1",
    "baseline_from": "2023-01-01",
    "baseline_to": "2023-01-31"
  },
  "analysis_state": {
    "ranking_basis": "present",
    "pending_goal": "absent"
  },
  "post_state": {
    "ranking_basis": "present",
    "pending_goal": "absent"
  },
  "request_goal": {
    "original_question": "具体体现在哪里？",
    "levels": [
      "themes"
    ],
    "aspect": null,
    "sentiment": "negative",
    "required_aspects": [
      "service",
      "effectiveness",
      "logistics"
    ],
    "version": "review-request-goal-p6-v1",
    "analysis_mode": "multi_aspect_negative_drilldown",
    "per_aspect_level": "themes"
  },
  "user_intent": {
    "query_type": "aspect_drilldown",
    "sentiment": "negative",
    "aspect": null,
    "sub_intent": "drill_down",
    "needs_clarification": false,
    "resolved_product_ids": [
      "100013588053"
    ],
    "effective_time_range": {
      "date_from": "2023-02-01",
      "date_to": "2023-02-28"
    }
  },
  "raw_analysis_intent": "aspect_drilldown",
  "raw_sentiment_intent": "negative",
  "query_plan": {
    "analysis_type": "aspect_drilldown",
    "requirements": [
      {
        "tool": "get_aspect_drilldown",
        "parameters": {
          "product_ids": [
            "100013588053"
          ],
          "aspect": "service",
          "sentiment": "negative"
        }
      },
      {
        "tool": "get_aspect_drilldown",
        "parameters": {
          "product_ids": [
            "100013588053"
          ],
          "aspect": "effectiveness",
          "sentiment": "negative"
        }
      },
      {
        "tool": "get_aspect_drilldown",
        "parameters": {
          "product_ids": [
            "100013588053"
          ],
          "aspect": "logistics",
          "sentiment": "negative"
        }
      }
    ],
    "required_tools": [
      "get_aspect_drilldown"
    ]
  },
  "task_acceptance": {
    "complete": false,
    "publishable_aspects": [
      "service",
      "effectiveness"
    ],
    "required_aspects": [
      "service",
      "effectiveness",
      "logistics"
    ],
    "status": "partial",
    "parent_status": "partial"
  },
  "final_status": {
    "agent": "partial",
    "worker": "partial"
  },
  "query_executions": 4,
  "classification_model_attempts": 5
}
```

</details>

实际流程：用户请求 → 范围/State → 独立 Goal → P6 冻结计划/准备/串行模拟分类 → partial / Worker partial。

普通 Registry 工具：`[]`；业务查询计数 4；固定分类响应尝试 5。P6 通过内部协调器执行，不应把 Registry 空数组误认成零分类。

P6 准备诊断：status=`ready`，reason=`classification_not_enabled`。其中 classification_not_enabled 是准备阶段遗留描述，不能覆盖后续 execution/publication 的真实结果。

| 维度 | 子状态 | M_a | S_a | 实际原因 |
|---|---|---:|---:|---|
| service | complete | 5 | 5 | 已通过逐维证据验收 |
| effectiveness | complete | 4 | 4 | 已通过逐维证据验收 |
| logistics | failed | 3 | 3 | 主题模型请求超时，结果未被验收 |

实际可发布维度：`["service","effectiveness"]`。失败维保留，完整 required_aspects 未缩减。

### 5）人工候选方案与安全风险

**选项 A：按既有 P6 契约判为 partial（建议，未批准）**

完整目标仍为三个维度；logistics 分类失败，其余维度独立通过。发布成功子维度，父任务不完整。

| 需要形成的业务契约 | 候选预期 |
|---|---|
| RequestGoal | `{"aspect":null,"levels":["themes"],"required_aspects":["service","effectiveness","logistics"],"sentiment":"negative","version":"review-request-goal-p6-v1"}` |
| Router / user_intent | `{"aspect":null,"query_type":"aspect_drilldown","sentiment":"negative"}` |
| Planner / analysis_intent | `"aspect_drilldown"` |
| query_plan | `{"analysis_type":"aspect_drilldown","aspects":["service","effectiveness","logistics"],"blocked":false,"sentiment":"negative"}` |
| required_tools（计划要求） | `["get_aspect_drilldown"]` |
| task_acceptance | `{"status":"partial","complete":false,"parent_status":"partial"}` |
| Agent / Worker 终态 | `{"agent":"partial","worker":"partial"}` |
| 轮后 pending_goal | `"absent"` |

执行约束：`{"frozen_order":["service","effectiveness","logistics"],"classification_attempts":5,"failed_aspect":"logistics","child_statuses":{"service":"complete","effectiveness":"complete","logistics":"failed"},"publishable_aspects":["service","effectiveness"],"all_required_aspects_retained":true,"failed_aspect_topics_published":false}`。

- 风险：不得将成功子集替换 required_aspects。
- 风险：不得改成 ok/complete=true，也不得把局部失败误称全局来源失败。

与当前实现的关系：待人工选定后才可形成正式预期；当前不判 PASS。

该项没有把违反既有契约的 ok/complete=true 或错误范围列成“同等合法备选”。可以暂不裁决；若希望变更现有契约，应另行明确提出，不在此表中伪造多个正确答案。

### 6）仍可能暴露的问题与人工填写

本例未见全局完整性失配；没有依据把它提升为全局 failed。所有源码证据仍须按既有 Validator 校验，候选主题不等于人工确认。

最终选择：**未填写**。理由：**未填写**。裁决人/时间：**未填写**。

---

## G20-T2 · 当前计数与历史排名冲突必须阻断

**分类：原状态标签错误；计划/执行阶段需分离。当前裁决状态：PENDING_HUMAN。**

### 1）用户问题与必要历史

1. “100013588053的差评主要集中在哪些方面？”
2. “具体体现在哪里？”；离线故障/数据模式：`stale_counts`

P6 开关：该组开启离线准备/执行/发布能力。页面初始可选 053/055；时间窗口 2023-02-01～02-28 UTC，后续范围变化以上述本轮元数据为准。

历史合成负面排名来源：053，D=8；service=5、effectiveness=4、logistics=3、quality=2、price=1、packaging=0；冻结必做顺序 service → effectiveness → logistics。这是原合成夹具，不是真实商品频次。

### 2）原冻结预期与理由

冻结原三维主题任务并注入 stale_counts；生成器将 stale 的验收标签写为 not_completed。

v1 task_acceptance.status=not_completed 无对应状态；主题工具是计划要求，并不表示过期后允许执行。原工具产物匹配逻辑还可能把零主题产物视为失败，需正式 v2 规范审查。

<details>
<summary>查看 v1 的完整逐字段预期（原样）</summary>

```json
{
  "expected_analysis_intent": "aspect_drilldown",
  "expected_analysis_state": {
    "pending_goal": "absent",
    "ranking_basis": "present"
  },
  "expected_final_status": {
    "agent": "unsupported",
    "worker": "succeeded"
  },
  "expected_pending_goal": "absent",
  "expected_query_plan": {
    "analysis_type": "aspect_drilldown",
    "aspects": [
      "service",
      "effectiveness",
      "logistics"
    ],
    "blocked": false,
    "sentiment": "negative"
  },
  "expected_request_goal": {
    "aspect": null,
    "levels": [
      "themes"
    ],
    "required_aspects": [
      "service",
      "effectiveness",
      "logistics"
    ],
    "sentiment": "negative",
    "version": "review-request-goal-p6-v1"
  },
  "expected_required_tools": [
    "get_aspect_drilldown"
  ],
  "expected_scope": {
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "product_ids": [
      "100013588053"
    ]
  },
  "expected_task_acceptance": {
    "complete": false,
    "status": "not_completed"
  },
  "expected_user_intent": {
    "aspect": null,
    "query_type": "aspect_drilldown",
    "sentiment": "negative"
  }
}
```

</details>

### 3）源码契约依据

- **C05**：[review_preparation.py:111](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_preparation.py:111) — 统计 D/六维 M 变化先报 stale_ranking，未预读观点、未分类；不重新选前三。
- **C06**：[reviews.py:122](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/reviews.py:122) — 缺可信排名先澄清；stale 准备错误走 unsupported；Goal 已有但未完成时任务验收 complete=false。
- **C07**：[worker.py:188](E:/AllProject/data-analysis-agent/src/data_analysis_agent/jobs/worker.py:188) — 受控 unsupported 可 succeeded；P6 通过校验的 partial 为 Worker partial；error/校验失败为 failed。
- **C09**：[review_acceptance.py:41](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_acceptance.py:41) — 原始 Goal 在 Planner 前独立形成；not_completed 不是任务验收状态，未完成不能 complete=true。

独立建议理由：C05 在观点预读前验证排名；C06 用 unsupported 保留原因，C07 可映射 succeeded。

### 4）实际输出与执行轨迹（只作证据）

<details>
<summary>查看实际九字段/范围及计数（不是标准答案）</summary>

```json
{
  "scope": {
    "model": "stage1-labels",
    "base_url": "https://labels.stage1.invalid/v1",
    "prompt_version": "stage1-labels-v1",
    "product_ids": [
      "100013588053"
    ],
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "as_of": null,
    "source": "reviews_csv",
    "dataset_version": "stage1-synthetic-v1",
    "baseline_from": "2023-01-01",
    "baseline_to": "2023-01-31"
  },
  "analysis_state": {
    "ranking_basis": "present",
    "pending_goal": "absent"
  },
  "post_state": {
    "ranking_basis": "present",
    "pending_goal": "absent"
  },
  "request_goal": {
    "original_question": "具体体现在哪里？",
    "levels": [
      "themes"
    ],
    "aspect": null,
    "sentiment": "negative",
    "required_aspects": [
      "service",
      "effectiveness",
      "logistics"
    ],
    "version": "review-request-goal-p6-v1",
    "analysis_mode": "multi_aspect_negative_drilldown",
    "per_aspect_level": "themes"
  },
  "user_intent": {
    "query_type": "aspect_drilldown",
    "sentiment": "negative",
    "aspect": null,
    "sub_intent": "drill_down",
    "needs_clarification": false,
    "resolved_product_ids": [
      "100013588053"
    ],
    "effective_time_range": {
      "date_from": "2023-02-01",
      "date_to": "2023-02-28"
    }
  },
  "raw_analysis_intent": "aspect_drilldown",
  "raw_sentiment_intent": "negative",
  "query_plan": {
    "analysis_type": "aspect_drilldown",
    "requirements": [
      {
        "tool": "get_aspect_drilldown",
        "parameters": {
          "product_ids": [
            "100013588053"
          ],
          "aspect": "service",
          "sentiment": "negative"
        }
      },
      {
        "tool": "get_aspect_drilldown",
        "parameters": {
          "product_ids": [
            "100013588053"
          ],
          "aspect": "effectiveness",
          "sentiment": "negative"
        }
      },
      {
        "tool": "get_aspect_drilldown",
        "parameters": {
          "product_ids": [
            "100013588053"
          ],
          "aspect": "logistics",
          "sentiment": "negative"
        }
      }
    ],
    "required_tools": [
      "get_aspect_drilldown"
    ]
  },
  "task_acceptance": {
    "status": "failed",
    "complete": false,
    "evidence_status": "not_executed"
  },
  "final_status": {
    "agent": "unsupported",
    "worker": "succeeded"
  },
  "query_executions": 1,
  "classification_model_attempts": 0
}
```

</details>

实际流程：用户请求 → 范围/State → 独立 Goal → 普通计划/工具/验收 → unsupported / Worker succeeded。

普通 Registry 工具：`[]`；业务查询计数 1；固定分类响应尝试 0。P6 通过内部协调器执行，不应把 Registry 空数组误认成零分类。

P6 准备诊断：status=`blocked`，reason=`stale_ranking`。其中 classification_not_enabled 是准备阶段遗留描述，不能覆盖后续 execution/publication 的真实结果。

实际回答：

> 历史排名已过期，需要刷新统计。

实际仅重新验证当前统计 1 次；观点预读 0 次、分类 0 次。当前排名可能仍以历史来源留在诊断 State，已由新鲜度检查明确阻断。

### 5）人工候选方案与安全风险

**选项 A：拒用过期排名，服务受控返回 unsupported（建议，未批准）**

原 themes/三维 Goal 和已编译计划不降低；当前 D/M 不匹配只阻断准备，要求用户重新生成可信统计，不自动刷新或分类。

| 需要形成的业务契约 | 候选预期 |
|---|---|
| RequestGoal | `{"aspect":null,"levels":["themes"],"required_aspects":["service","effectiveness","logistics"],"sentiment":"negative","version":"review-request-goal-p6-v1"}` |
| Router / user_intent | `{"aspect":null,"query_type":"aspect_drilldown","sentiment":"negative"}` |
| Planner / analysis_intent | `"aspect_drilldown"` |
| query_plan | `{"analysis_type":"aspect_drilldown","aspects":["service","effectiveness","logistics"],"blocked":false,"sentiment":"negative"}` |
| required_tools（计划要求） | `["get_aspect_drilldown"]` |
| task_acceptance | `{"status":"failed","complete":false,"evidence_status":"not_executed"}` |
| Agent / Worker 终态 | `{"agent":"unsupported","worker":"succeeded"}` |
| 轮后 pending_goal | `"absent"` |

执行约束：`{"preparation_reason":"stale_ranking","current_ranking_validation_reads":1,"aspect_prefetch_reads":0,"classification_attempts":0,"public_topics":0,"planned_tools":["get_aspect_drilldown"],"completed_theme_artifact_required":false,"required_aspects_retained":["service","effectiveness","logistics"]}`。

- 风险：task_acceptance.failed 不是 Worker.failed。
- 风险：不得新加 not_completed 或 stale_ranking 外层 API 枚举。
- 风险：不得用新统计偷偷替换旧前三或把统计成功冒充主题完成。
- 表示说明：not_completed 可作证据阶段描述，不能作为 task_acceptance.status。
- 表示说明：计划存在但执行受阻，不能用“无主计划”代替真实轨迹；也不能要求其交付已禁止生成的主题。
- 表示说明：未来 v2 的工具产物指标须区分 stale 受控阻断与已执行完成；本轮不改 v1 评分。

与当前实现的关系：待人工选定后才可形成正式预期；当前不判 PASS。

该项没有把违反既有契约的 ok/complete=true 或错误范围列成“同等合法备选”。可以暂不裁决；若希望变更现有契约，应另行明确提出，不在此表中伪造多个正确答案。

### 6）仍可能暴露的问题与人工填写

诊断仍保留旧 ranking_basis 及未执行目标，后续每次必须重新复核新鲜度；“候选可信来源存在”不等于“当前数据未变”。

最终选择：**未填写**。理由：**未填写**。裁决人/时间：**未填写**。

---

## G24-T2 · 053 RAG 覆盖缺口不伪造证据

**分类：真正的省略语义歧义；Goal 默认层次需审查。当前裁决状态：PENDING_HUMAN。**

### 1）用户问题与必要历史

1. “100013588053有没有物流很慢的反馈？”；离线故障/数据模式：`rag_gap`
2. “客服呢？”；离线故障/数据模式：`rag_gap`

P6 开关：该组开启离线准备/执行/发布能力。页面初始可选 053/055；时间窗口 2023-02-01～02-28 UTC，后续范围变化以上述本轮元数据为准。

上一轮 053 返回空候选，回答明确“暂时无法回答，不表示全量不存在”。该检索覆盖缺口不能替代客服命题或负面情感条件。

### 2）原冻结预期与理由

组标题为 053 RAG 覆盖缺口，第二轮仍指定 kind=rag、mode=rag_gap、status=clarify；因此把上一轮 RAG 产物契约带给尚未执行的新请求。

v1 在澄清轮强制 Goal=None 且 task_acceptance 为 frozen_RAG_separate；但这一新问题没有完整命题，也没有实际执行 RAG，不能直接套用上一轮已执行检索验收。

<details>
<summary>查看 v1 的完整逐字段预期（原样）</summary>

```json
{
  "expected_analysis_intent": "not_finalized",
  "expected_analysis_state": {
    "pending_goal": "absent",
    "ranking_basis": "absent"
  },
  "expected_final_status": {
    "agent": "unsupported",
    "worker": "succeeded"
  },
  "expected_pending_goal": "absent",
  "expected_query_plan": {
    "analysis_type": null,
    "aspects": [],
    "blocked": true,
    "sentiment": null
  },
  "expected_request_goal": null,
  "expected_required_tools": [],
  "expected_scope": {
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "product_ids": [
      "100013588053"
    ]
  },
  "expected_task_acceptance": {
    "contract": "frozen_RAG_separate",
    "permitted_evidence": false
  },
  "expected_user_intent": {
    "needs_clarification": true
  }
}
```

</details>

### 3）源码契约依据

- **C01**：[analysis_context.py:11](E:/AllProject/data-analysis-agent/src/data_analysis_agent/jobs/analysis_context.py:11) — 同会话、同标注身份和合法范围才恢复；用户原文条件与可信事实分离。
- **C08**：[review_intent.py:115](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_intent.py:115) — 未明确任务不执行；RAG 省略追问先澄清；当前短情感追问可承接待完成 themes，但该代码不裁决业务语义。
- **C09**：[review_acceptance.py:41](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_acceptance.py:41) — 原始 Goal 在 Planner 前独立形成；not_completed 不是任务验收状态，未完成不能 complete=true。

独立建议理由：由明确的 service 和有限历史提问，选择最少假设的澄清；两方案都禁止在补全前执行。

### 4）实际输出与执行轨迹（只作证据）

<details>
<summary>查看实际九字段/范围及计数（不是标准答案）</summary>

```json
{
  "scope": {
    "model": "stage1-labels",
    "base_url": "https://labels.stage1.invalid/v1",
    "prompt_version": "stage1-labels-v1",
    "product_ids": [
      "100013588053"
    ],
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "as_of": null,
    "source": "reviews_csv",
    "dataset_version": "stage1-synthetic-v1",
    "baseline_from": "2023-01-01",
    "baseline_to": "2023-01-31"
  },
  "analysis_state": {
    "ranking_basis": "absent",
    "pending_goal": "absent"
  },
  "post_state": {
    "ranking_basis": "absent",
    "pending_goal": "absent"
  },
  "request_goal": {
    "original_question": "客服呢？",
    "levels": [
      "statistics"
    ],
    "aspect": "service",
    "sentiment": null
  },
  "user_intent": {
    "query_type": null,
    "product_mentions": [],
    "sentiment": null,
    "aspect": "service",
    "sub_intent": "overview",
    "topic": null,
    "time_range": null,
    "period": "month",
    "needs_clarification": true,
    "clarification_question": "上轮为语义检索，请补充完整的检索问题；省略追问不能自动改为按时间浏览原文。",
    "resolved_product_ids": [
      "100013588053"
    ],
    "effective_time_range": {
      "date_from": "2023-02-01",
      "date_to": "2023-02-28"
    }
  },
  "raw_analysis_intent": null,
  "raw_sentiment_intent": null,
  "query_plan": null,
  "task_acceptance": null,
  "final_status": {
    "agent": "unsupported",
    "worker": "succeeded"
  },
  "query_executions": 0,
  "classification_model_attempts": 0
}
```

</details>

实际流程：用户请求 → 范围/State → 独立 Goal → 路由澄清或受控阻断 → unsupported / Worker succeeded。

普通 Registry 工具：`[]`；业务查询计数 0；固定分类响应尝试 0。P6 通过内部协调器执行，不应把 Registry 空数组误认成零分类。

实际回答：

> 上轮为语义检索，请补充完整的检索问题；省略追问不能自动改为按时间浏览原文。

### 5）人工候选方案与安全风险

**选项 A：先问客服要查什么、统计还是具体反馈（建议，未批准）**

只确定 service 和 053；不推断正负、具体命题或业务层次。尚未形成可执行目标。

| 需要形成的业务契约 | 候选预期 |
|---|---|
| RequestGoal | `null` |
| Router / user_intent | `{"needs_clarification":true}` |
| Planner / analysis_intent | `"not_finalized"` |
| query_plan | `{"analysis_type":null,"aspects":[],"blocked":true,"sentiment":null}` |
| required_tools（计划要求） | `[]` |
| task_acceptance | `{"representation_rule":"absent_or_explicit_failure","permitted_representations":[null,{"status":"failed","complete":false}],"complete_true_forbidden":true}` |
| Agent / Worker 终态 | `{"agent":"unsupported","worker":"succeeded"}` |
| 轮后 pending_goal | `"absent"` |

执行约束：`{"business_query_executions":0,"classification_attempts":0,"must_not_publish_evidence":true,"catalog_reads_before_planning_are_not_business_queries":true}`。

- 风险：不能把物流很慢的“慢”机械替换成客服响应慢。
- 风险：不能以 053 空候选推出没有客服问题。
- 表示说明：已知约束放在澄清上下文，RequestGoal 暂不冻结；这不是已执行的 RAG 任务。

与当前实现的关系：待人工选定后才可形成正式预期；当前不判 PASS。

**选项 B：保留检索意向，但请用户补全客服命题（备选，未批准）**

承接上一轮“检索相关反馈”的意向，不承接物流谓词或情感；先让用户说清客服要检索的完整反馈。补全前不检索。

| 需要形成的业务契约 | 候选预期 |
|---|---|
| RequestGoal | `null` |
| Router / user_intent | `{"needs_clarification":true}` |
| Planner / analysis_intent | `"not_finalized"` |
| query_plan | `{"analysis_type":null,"aspects":[],"blocked":true,"sentiment":null}` |
| required_tools（计划要求） | `[]` |
| task_acceptance | `{"representation_rule":"absent_or_explicit_failure","permitted_representations":[null,{"status":"failed","complete":false}],"complete_true_forbidden":true}` |
| Agent / Worker 终态 | `{"agent":"unsupported","worker":"succeeded"}` |
| 轮后 pending_goal | `"absent"` |

执行约束：`{"business_query_executions":0,"classification_attempts":0,"must_not_publish_evidence":true,"catalog_reads_before_planning_are_not_business_queries":true}`。

- 风险：检索意向不是已绑定 SemanticQuery，不得生成猜测向量或 permitted evidence。
- 表示说明：query family 仅为未执行的 semantic_evidence 提示；最终 query_plan 仍未生成。

与当前实现的关系：待人工选定后才可形成正式预期；当前不判 PASS。

### 6）仍可能暴露的问题与人工填写

当前 Goal 默认 statistics/service/null 虽然被 Router 阻断，仍可能过早冻结未经用户确认的层次；若选候选 A/B，后续需独立核验 Goal 表示，不在本轮修复。

最终选择：**未填写**。理由：**未填写**。裁决人/时间：**未填写**。

---

## G30-T3 · 默认开关关闭时的待完成主题与短情感歧义

**分类：真正的层次歧义；原完成性预期自相矛盾。当前裁决状态：PENDING_HUMAN。**

### 1）用户问题与必要历史

1. “100013588053的差评主要集中在哪些方面？”
2. “具体体现在哪里？”
3. “那好评呢？”

P6 开关：该组 P6 三个开关均关闭，不能当作 P6 多维执行。页面初始可选 053/055；时间窗口 2023-02-01～02-28 UTC，后续范围变化以上述本轮元数据为准。

第二轮已零查询澄清：要求在客服/效果/物流等维度中选择；State 恢复的是 negative/themes 的 awaiting_aspect，completed=false。它不是完成的主题结论。

### 2）原冻结预期与理由

组在冻结前已写明业务层次歧义，第三轮 human=True、complete=False，同时保留 kind=themes 和默认 ok，造成完成性混杂。

v1 既写 themes/positive、ok/succeeded，又写 complete=false/status=not_completed，且 strict_business_completion=false；没有唯一正式业务答案。

<details>
<summary>查看 v1 的完整逐字段预期（原样）</summary>

```json
{
  "expected_analysis_intent": "aspect_drilldown",
  "expected_analysis_state": {
    "pending_goal": "present",
    "ranking_basis": "present"
  },
  "expected_final_status": {
    "agent": "ok",
    "worker": "succeeded"
  },
  "expected_pending_goal": "absent",
  "expected_query_plan": {
    "analysis_type": "aspect_drilldown",
    "aspects": [],
    "blocked": false,
    "sentiment": "positive"
  },
  "expected_request_goal": {
    "aspect": null,
    "levels": [
      "themes"
    ],
    "sentiment": "positive"
  },
  "expected_required_tools": [
    "get_aspect_drilldown"
  ],
  "expected_scope": {
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "product_ids": [
      "100013588053"
    ]
  },
  "expected_task_acceptance": {
    "complete": false,
    "status": "not_completed"
  },
  "expected_user_intent": {
    "aspect": null,
    "query_type": "aspect_drilldown",
    "sentiment": "positive"
  }
}
```

</details>

### 3）源码契约依据

- **C02**：[analysis_state.py:37](E:/AllProject/data-analysis-agent/src/data_analysis_agent/jobs/analysis_state.py:37) — 排名与待办分别严格验收、精确范围投影；当前 pending 恢复仅负向、无单维度、单商品的合法澄清任务。
- **C08**：[review_intent.py:115](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_intent.py:115) — 未明确任务不执行；RAG 省略追问先澄清；当前短情感追问可承接待完成 themes，但该代码不裁决业务语义。
- **C09**：[review_acceptance.py:41](E:/AllProject/data-analysis-agent/src/data_analysis_agent/agents/review_acceptance.py:41) — 原始 Goal 在 Planner 前独立形成；not_completed 不是任务验收状态，未完成不能 complete=true。

独立建议理由：原始句子没有明确统计或主题；默认零执行的澄清最少假设。此建议不是最终裁决。

### 4）实际输出与执行轨迹（只作证据）

<details>
<summary>查看实际九字段/范围及计数（不是标准答案）</summary>

```json
{
  "scope": {
    "model": "stage1-labels",
    "base_url": "https://labels.stage1.invalid/v1",
    "prompt_version": "stage1-labels-v1",
    "product_ids": [
      "100013588053"
    ],
    "date_from": "2023-02-01",
    "date_to": "2023-02-28",
    "as_of": null,
    "source": "reviews_csv",
    "dataset_version": "stage1-synthetic-v1",
    "baseline_from": "2023-01-01",
    "baseline_to": "2023-01-31"
  },
  "analysis_state": {
    "ranking_basis": "present",
    "pending_goal": "present"
  },
  "post_state": {
    "ranking_basis": "absent",
    "pending_goal": "absent"
  },
  "request_goal": {
    "original_question": "那好评呢？",
    "levels": [
      "themes"
    ],
    "aspect": null,
    "sentiment": "positive"
  },
  "user_intent": {
    "query_type": "aspect_drilldown",
    "product_mentions": [],
    "sentiment": "positive",
    "aspect": null,
    "sub_intent": "drill_down",
    "topic": null,
    "time_range": null,
    "period": "month",
    "needs_clarification": false,
    "clarification_question": null,
    "resolved_product_ids": [
      "100013588053"
    ],
    "effective_time_range": {
      "date_from": "2023-02-01",
      "date_to": "2023-02-28"
    }
  },
  "raw_analysis_intent": "aspect_drilldown",
  "raw_sentiment_intent": "positive",
  "query_plan": {
    "analysis_type": "aspect_drilldown",
    "requirements": [
      {
        "tool": "get_aspect_drilldown",
        "parameters": {
          "product_ids": [
            "100013588053"
          ],
          "aspect": null,
          "sentiment": "positive"
        }
      }
    ],
    "required_tools": [
      "get_aspect_drilldown"
    ]
  },
  "task_acceptance": {
    "status": "passed",
    "complete": true,
    "evidence_status": "available"
  },
  "final_status": {
    "agent": "ok",
    "worker": "succeeded"
  },
  "query_executions": 2,
  "classification_model_attempts": 2
}
```

</details>

实际流程：用户请求 → 范围/State → 独立 Goal → 普通计划/工具/验收 → ok / Worker succeeded。

普通 Registry 工具：`["get_analysis_context","get_aspect_drilldown"]`；业务查询计数 2；固定分类响应尝试 2。P6 通过内部协调器执行，不应把 Registry 空数组误认成零分类。

实际回答：

> [r1] 二级主题complete，纳入 2 条评论、6 条观点；模型候选主题未经人工确认。

### 5）人工候选方案与安全风险

**选项 A：先区分正向统计还是正向主题（建议）（建议，未批准）**

positive 和 053 已明确，但旧待办 themes 与上轮已完成统计存在两种可承接层次。先澄清，不替用户执行其中一种。

| 需要形成的业务契约 | 候选预期 |
|---|---|
| RequestGoal | `null` |
| Router / user_intent | `{"needs_clarification":true}` |
| Planner / analysis_intent | `"not_finalized"` |
| query_plan | `{"blocked":true,"analysis_type":null,"aspects":[],"sentiment":"positive"}` |
| required_tools（计划要求） | `[]` |
| task_acceptance | `{"representation_rule":"absent_or_explicit_failure","permitted_representations":[null,{"status":"failed","complete":false}],"complete_true_forbidden":true}` |
| Agent / Worker 终态 | `{"agent":"unsupported","worker":"succeeded"}` |
| 轮后 pending_goal | `"awaiting_aspect"` |

执行约束：`{"business_query_executions":0,"classification_attempts":0,"must_not_publish_evidence":true,"catalog_reads_before_planning_are_not_business_queries":true}`。

- 风险：不能直接统计来降级原 themes，也不能用主题分类把未确认层次变成事实。
- 表示说明：旧 negative/themes pending 作为未完成历史保持，不改写成 positive 已完成；当前 positive 是已知澄清条件。
- 表示说明：awaiting_aspect 为现有待办状态，仅保留原待办；不发明 awaiting_level API 枚举。
- 表示说明：待用户后续明确选项，才冻结新 Goal 并显式说明延续或替换旧目标。

与当前实现的关系：若人工选择先澄清，当前自动执行 positive/themes 的轨迹不符合；未来单独记录普通业务缺陷，本轮不修。

**选项 B：明确约定短情感追问延续待办主题层次（备选，未批准）**

将“那好评呢”定义为把待办主题目标切换到正向总体主题；不是正向排名统计。旧 negative 目标被本轮意图替换但不标为已完成。

| 需要形成的业务契约 | 候选预期 |
|---|---|
| RequestGoal | `{"aspect":null,"levels":["themes"],"sentiment":"positive"}` |
| Router / user_intent | `{"aspect":null,"query_type":"aspect_drilldown","sentiment":"positive"}` |
| Planner / analysis_intent | `"aspect_drilldown"` |
| query_plan | `{"analysis_type":"aspect_drilldown","aspects":[],"blocked":false,"sentiment":"positive"}` |
| required_tools（计划要求） | `["get_aspect_drilldown"]` |
| task_acceptance | `{"status":"passed","complete":true}` |
| Agent / Worker 终态 | `{"agent":"ok","worker":"succeeded"}` |
| 轮后 pending_goal | `"absent"` |

执行约束：`{"business_query_executions":2,"classification_attempts":2,"tool":"get_aspect_drilldown","parameters":{"product_ids":["100013588053"],"aspect":null,"sentiment":"positive"},"complete_only_if_theme_and_evidence_validation_pass":true,"negative_pending_not_marked_complete":true}`。

- 风险：用户本意若为好评数量，则分类目标及两次模型调用均错误。
- 风险：仅统计一级维度不能完成该候选语义。

与当前实现的关系：当前轨迹在此候选语义下可能满足程序验收；必须先由负责人确定承接规则，不能用输出反证该选择正确。

**选项 C：明确约定本轮替换为总体好评统计（备选，未批准）**

将该短句定义为以 positive 总体统计替换旧待办主题，不继承 themes；这必须是人工选定的业务规则，不能用于制造通过。

| 需要形成的业务契约 | 候选预期 |
|---|---|
| RequestGoal | `{"levels":["statistics"],"aspect":null,"sentiment":"positive"}` |
| Router / user_intent | `{"query_type":"aspect_summary","aspect":null,"sentiment":"positive"}` |
| Planner / analysis_intent | `"positive_aspects"` |
| query_plan | `{"blocked":false,"analysis_type":"positive_aspects","aspects":[],"sentiment":"positive"}` |
| required_tools（计划要求） | `["get_positive_aspects"]` |
| task_acceptance | `{"status":"passed","complete":true}` |
| Agent / Worker 终态 | `{"agent":"ok","worker":"succeeded"}` |
| 轮后 pending_goal | `"absent"` |

执行约束：`{"business_query_executions":2,"classification_attempts":0,"tool":"get_positive_aspects","complete_only_if_statistics_validation_pass":true}`。

- 风险：短句未写“总体统计”；若用户要延续 themes，这会降低任务目标。
- 风险：旧 negative/themes 只能记录为被替换，不能声称完成。

与当前实现的关系：当前 positive/themes 执行不符合统计语义；需要另行业务修复和回归，本轮不改代码。

### 6）仍可能暴露的问题与人工填写

若采纳 A 或 C，当前已执行两次 positive 主题模型并 complete=true 可能是错误目标完成；若采纳 B 则需完整验证主题证据。不能在人工选择前判 PASS。

最终选择：**未填写**。理由：**未填写**。裁决人/时间：**未填写**。

---


## 统一需要确认的事项

1. **澄清 Goal 的表示**：允许 themes/sentiment=null 的部分 Goal，还是在任务条件完整前暂不冻结？均不得执行工具。G10/G11/G12-T1 的 A/B 差别主要在此，不是是否允许凭空继承事实。
2. **G12-T2 条件继承**：同会话同身份且未改范围时，上一轮明确写出的 053 是用户条件，不因主题未完成而消失；旧统计/情感/排名仍不可继承为事实。建议采用 A，与你已验收的 P0-01 一致。
3. **G17～19 的状态**：按现有 P6 契约只能是 partial/complete=false/Worker partial；如要改变，属于另一个契约变更，不能只改 Oracle。
4. **G20 的阶段性指标**：计划要求三维主题不代表 stale 后允许执行。v2 需要明确“受控阻断正确性”与“已执行工具产物完整性”的适用阶段；当前 v1 工具匹配可能要求不存在的主题，正式 v2 不得静默沿用或事后改分。
5. **G24 的 Goal 及 RAG 表示**：无完整命题时不能凭“客服呢”搜索；也不能将默认 statistics 占位当作用户同意统计。
6. **G30 层次与旧待办生命周期**：必须由你选择澄清、延续正向主题，或约定替换为总体正向统计；旧负向主题被替换也不能记为已完成。

## 最终签署栏（留待本人填写）

| 案例 | 最终选项 | 业务理由 | 裁决人 | 裁决时间 |
|---|---|---|---|---|
| G10-T2 | 未填写 | 未填写 | 未填写 | 未填写 |
| G11-T2 | 未填写 | 未填写 | 未填写 | 未填写 |
| G12-T1 | 未填写 | 未填写 | 未填写 | 未填写 |
| G12-T2 | 未填写 | 未填写 | 未填写 | 未填写 |
| G17-T2 | 未填写 | 未填写 | 未填写 | 未填写 |
| G18-T2 | 未填写 | 未填写 | 未填写 | 未填写 |
| G19-T2 | 未填写 | 未填写 | 未填写 | 未填写 |
| G20-T2 | 未填写 | 未填写 | 未填写 | 未填写 |
| G24-T2 | 未填写 | 未填写 | 未填写 | 未填写 |
| G30-T3 | 未填写 | 未填写 | 未填写 | 未填写 |

全部决定完成后，仍须独立生成正式 Oracle v2、审查匹配器/阶段性指标并冻结，再执行新的独立评分。本轮没有开展这些后续步骤，也不把建议当批准。
