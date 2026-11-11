# Agent 第一阶段：人工裁决与 Oracle v2 准备

日期：2026-11-11。项目：`E:\AllProject\data-analysis-agent`。

**裁决材料已准备；正式 Oracle v2 发布 BLOCKED，等待本人逐项决定。** 本轮不修改 Agent、不修复 G25-T1、不执行模型/数据库、不运行评测或重新计分。历史基线仍是 **51 PASS、1 FAIL、10 PENDING_HUMAN**，不是本轮新通过率。

交付：

- [10 轮人工裁决工作表](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-human-review.md)：原始问题/历史、原预期、源码契约、实际轨迹、候选语义与风险，全部最终选择留空。
- [Oracle v2 未批准草案](E:/AllProject/data-analysis-agent/tests/fixtures/agent_stage1/oracle.v2.pending.json)：10 轮 expected 字段全部标 pending，51 个既有 PASS 与 G25-T1 的独立预期不动，原始 30 组输入和历史元数据保留。
- [既有输出的独立观察文件](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-human-review-observations.json)：与候选标准分离，不作为标准答案。

## 1. 问题分类与建议

| 案例 | 分类 | 建议（不是批准） |
|---|---|---|
| G10-T2 | 标注身份变化后的范围/事实失效，条件不足；旧 REVIEW 理由有误 | A：不恢复旧身份事实或 SKU，保留有限主题意图并澄清商品/情感；B：完整 Goal 暂不冻结 |
| G11-T2 | 新会话不能拿旧会话事实或条件，当前问题省略 | A：只依据本会话页面条件澄清；B：任务 Goal 暂不冻结 |
| G12-T1 | 本轮 SKU 明确但情感/方向未定 | A：保留 053，主题 Goal 未完整；B：先完整澄清再冻结 Goal |
| G12-T2 | v1 把“上一轮没完成事实”混同于“显式 SKU 条件无效” | A：承接合法用户 053 条件，仍澄清，不继承排名或 negative |
| G17-T2 | 原预期状态定义错误，使用效果失败 | A：完整三维目标，partial、complete=false，只发布客服/物流 |
| G18-T2 | 原预期状态定义错误，客服失败 | A：partial、complete=false，只发布使用效果/物流 |
| G19-T2 | 原预期状态定义错误，物流失败 | A：partial、complete=false，只发布客服/使用效果 |
| G20-T2 | 原验收标签错误及执行阶段指标不清 | A：保留主题目标，stale_ranking 阻断，unsupported/Worker succeeded，任务 complete=false |
| G24-T2 | RAG 后“客服呢”含义不唯一 | A：先问业务类型和客服命题；B：保留检索意向，只请补全客服命题；两者均零查询 |
| G30-T3 | 待办主题和已有统计之间的短情感层次歧义 | A：先澄清（建议）；B：约定延续正向主题；C：约定替换总体好评统计 |

建议 G17～19 及 G20 的状态按现有契约处理：这是由既有业务定义推出，不是照抄当前输出。真正存在多个可执行业务解释的是 G30；G24 的具体检索命题和是否继续检索也不唯一。G10/G11/G12-T1 因条件不全，无论选择哪种 Goal 表示都必须先澄清，不能把它们变成自动分析选项。

## 2. 原预期和旧说明需要区分

直接核对 v1 后确认：G10、G11、G12-T1/T2 的 expected_request_goal.sentiment 原本均为 null；G10/G11 的 expected_scope 也是页面两商品，不是旧说明中的单商品。旧 REVIEW 里“预期 negative”有事实性表述错误。本轮只在新材料中注明，不修改旧说明、旧排除或旧分数。

明确的 v1 状态问题：

- G17/G18/G19 同时要求 Agent partial/Worker partial，却将 task_acceptance.status 写成 failed；与 P6 已冻结映射不一致。
- G20 的 not_completed 并非既有 task_acceptance.status；准备阶段 stale 不等于服务崩溃，不应套执行后全局 failed→error 的映射。
- G30 既指定 positive/themes、ok，又指定 complete=false/not_completed，且原本声明待人工裁决；没有自洽的唯一正式预期。
- 多个澄清轮同样写了 not_completed；需要确认允许省略验收或显式 complete=false 的表示，不新增外层 API 枚举。

原始输入、selection_changes、conversation_override、故障注入、P6 开关、组内完整历史都在 pending 草案保留；待裁决轮还嵌入完整 v1_original_turn，原冻结预期可逐字段追溯。

## 3. 仍可能暴露 Agent 缺陷的地方

1. G10/G11/G12 原始结果在澄清时仍留下候选 `analysis_intent=aspect_drilldown`、`sentiment_intent=negative`，而独立 Goal 情感未定。执行被阻断是安全事实，但这些候选值能否作为最终 API 语义需要决定；不能借 v1 的 not_finalized 投影把原始字段隐去。
2. G24 当前 RequestGoal 默认 statistics/service/null，却未确认用户是否要统计。Router 已正确零查询澄清，但如果人工选择 Goal 不冻结，则当前 Goal 表示可能需要另列缺陷。不能因“最终没查工具”就认定所有字段正确。
3. G30 当前执行 positive 总体主题，两次固定模型响应，complete=true。若选 A 或 C，就是潜在错误目标执行/完成；若选 B 才可能是对应语义下的正确执行，还需证据验收。候选主题不等于人工主题质量批准。
4. G20 仍保留历史排名来源作诊断，但已判其统计过期；下次请求必须重新验证，不能只因来源摘要有效就继续分析。当前源码有重新复核，本轮未验证真实数据库行为。
5. G12-T2 单商品条件承接与此前已验收 P0-01 相符；不应为了匹配旧两商品预期而回退该安全修复。

这些都只是裁决准备中的差异/风险，没有在本轮自动修复或改判。

## 4. v2 草案隔离与正式发布条件

草案 `scoring_enabled=false`、`publication_status=pending_human_decision`，顶层批准人/批准时间为 null。10 轮的所有 expected 字段及严格完成性标记为 pending，最终选项、理由、裁决人、时间均 null。候选方案旁的 recommendation_option_id 仅为建议，不是 selected_option_id。

其余 **52 轮逐对象保持 v1 原样**，包括原 51 PASS 和 G25-T1 FAIL，没有修改任何独立预期来配合当前结果。原 v1 的评分规则、分子、分母、10 项排除及已有记录均不改变。pending 格式不是现有正式评分器支持的 Oracle 格式，禁止输入评分器。

正式 v2 尚需：

1. 你对 10 项逐一给出业务选择及理由，不以“当前 Agent 输出如此”为唯一依据。
2. 明确澄清 Goal/验收表示、G30 待办替换或继续生命周期、G20 阻断阶段如何考核工具。
3. 由独立步骤把选定候选转成可执行、无 pending 的正式预期，核验 52 轮未变及全部输入/历史保持，再冻结新的清单。
4. 如果需要 v2 专用的匹配器或阶段性指标，另行审查、测试和版本化；不修改旧 v1 计分记录，不预先计算或承诺“62/62”。
5. 形成正式 Oracle v2 后才允许另一次独立评测，发现 Agent 差异应保留 FAIL，不反向改 Oracle。

本轮没有创建 `oracle.v2.json`，没有运行当前 Agent 或旧评分器，也没有给出任何新 v2 分数。

## 5. 检查和停止结论

本轮只做材料完整性检查，**18/18 项通过**，这不是 Agent 业务测试通过或人工语义批准。检查包含：30 组/62 轮输入及历史不变；52 个非待裁决 turn 与 v1 逐对象相同；10 个原 turn 完整嵌入；全部 expected 字段及严格完成性待定；审批字段未填；无正式 v2；观察文件/源码摘要绑定；partial/stale 约束；旧材料保护及链接有效。

| 本轮检查 | 结果 |
|---|---|
| 原冻结文件、旧报告/评分/运行证据及源码等 | 303 个既有文件摘要相同 |
| 原 v3 / v4 | 保留原文件、原摘要，未重写 |
| 既有 51 PASS、G25-T1 FAIL | 52 个 turn 的原始预期及其他字段完整保留；未改判 |
| 10 个待裁决轮 | 全部 pending，0 项最终选择、批准人或批准时间 |
| 人工候选 | 16 个未批准建议/备选，推荐项不等于已选项 |
| 本轮 Agent / 模型 / 数据库执行 | 均为 0 |
| 本轮业务评测/重新计分 | 0 次，未运行旧或新评分器 |
| 正式 oracle.v2.json | 未创建 |

检查记录：[human-review-validation.json](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-human-review-validation.json)。只读校验入口为 `.\.venv\Scripts\python.exe .cache/agent-stage1-human-review/validate.py`，仅使用标准库读取文件，不导入 Agent 或评分函数。交付摘要清单见 [human-review-manifest.json](E:/AllProject/data-analysis-agent/docs/evaluation/agent-stage1-human-review-manifest.json)。

原始证据 → 发现 → 后续处理：v1 输入/生成器与源码状态映射 → 状态错误、范围不完整或语义歧义 → 本人裁决后另行冻结正式预期，绝不从当前模型回答回填。后续模型/数据库验证仍独立于人工语义标准。

**准备完整性 READY；正式 v2 发布 BLOCKED（本人未裁决）；P6-E1B 仍 BLOCKED。** 这不代表上述任何候选获批，也不解除 G25-T1。工作在裁决材料交付后停止，等待你的独立决定，不自动开启修复或评测阶段。
