# 具体好评与商品整体好评总结

日期：2026-11-02（Asia/Taipei）。代码、隔离端到端、真实数据库与外部模型的实际页面联合验证均已完成。用户明确授权将指定商品的物流、客服及整体好评片段发送至当前 SiliconFlow DeepSeek-V3.2，仅用于这三项真实页面验收后，才执行对应外发。隔离与真实结果分别记录。

## 已实现行为

| 问题 | 行为 |
|---|---|
| `100013588053的好评集中在什么地方？` | 保留六维度统计和既有分母，不因简单排名新增主题模型调用 |
| `100013588053物流的好评体现在哪里？` | 只取物流正向观点，语义归并同义表述，输出具体总结、数量及代表依据 |
| `客服的好评体现在哪里？` | 继承同一会话与范围内已确认商品，切换到客服正向观点 |
| `给出100013588053商品评论中具体的好评` | 对单商品的全部正向观点按六个一级维度归并、组织总结 |
| `总结这个商品的整体好评` | 清除上轮维度及细分主题，保留商品、时间和标注身份，生成整体总结 |
| `给出评论中具体的好评` | 不再把“给出评论中具体”识别为商品名；在存在唯一可信父轮时继承对象 |
| `给出100013588053的3条好评原文` | 保留原文检索，不把原文样例请求替换为主题总结 |

现有 `get_aspect_drilldown` 复用原任务 API、工具注册表、主题归属复核、校验、公开回答和历史恢复。`aspect` 允许在正向整体总结时省略；负向主题仍要求指定维度。不新增接口、数据库表或依赖，不改一级统计、有效标注及人工复核口径。

单商品的正向总结包含模型语义归并。多商品维度内容查询继续使用既有本地精确观点分组，不能称作语义主题总结；未指定维度的整体语义总结仍要求唯一商品。品牌多型号选择方式未在本阶段改动。

## 处理与校验

1. 共享语义识别区分数量、具体好评和原文，单轮与追问共同编译现有工具计划。
2. 复用 `get_evidence` 提取有效正向观点。单维度保留最近 100 条匹配评论；整体总结从各维度轮流选择评论，合计最多 100 条。
3. 正向总结按维度轮流纳入观点，所有维度共享 200 条观点、20 种表达、最多 8 个候选主题、最多 2 次主题请求和 60 秒主题阶段预算。单请求不超过配置值且最高 30 秒，仍不启用 SDK 重试。不会为六个维度分别建立完整预算。原负向主题上限仍为 80 种表达、最多 5 次请求、单次最高 20 秒。
4. 相同表述以“维度＋原文”区分，避免把不同维度的“好”等短句合并。候选依据和每次观点归属必须保持一级维度一致。
5. 模型给出主题、定义、观点归属与逐字支持片段；代码在原始观点中唯一定位起止位置，拒绝不存在或重复出现而无法唯一定位的片段，再复核 ID、来源、遗漏及重复，并重新计算去重数量和比例。模型不负责计算字符下标，候选主题仍注明未经人工确认。
6. 整体结果中每个主题的比例分母为该维度实际纳入的去重评论数。单维度分母含义保持不变；非随机受限样本不外推总体，同一评论跨主题不相加。
7. 回答先按维度总结好评要点，再展示主题定义、去重数量和原文引用。整体总结直接组装已核验主题；单维度保留现有回答生成与支持性复核。历史读取不重调模型。
8. 未归类、无有效观点、部分归属失败和全部失败沿用原发布保护；不能以一级计数补造体验总结。自动补查按真实缺失条件提示，不再把缺少维度统一说成商品超过两款。

客服沿用既有 `service` 标注维度。该维度可能含安装服务，提示词要求按实际内容命名，不能把安装师傅的评价改写成在线客服体验。

## 文件变更

| 文件 | 职责 |
|---|---|
| `agents/review_semantics.py`、`review_plan.py`、`review_intent.py` | 具体好评识别、商品误提取修复、整体总结及追问路由 |
| `agents/review_tools.py`、`reviews/analytics.py` | 扩展现有主题工具的正向整体提取，平衡各维度并保留真实总体计数 |
| `agents/review_topics.py` | 维度隔离、整体共享预算、候选依据与归属校验、按维度分母重算 |
| `agents/review_answer_facts.py`、`review_insights.py`、`review_answers.py` | 具体摘要、定义和证据组装、整体重点覆盖与历史恢复 |
| `agents/review_evidence_loop.py`、`review_prompts.py` | 精确澄清和 `review-agent-v9` 提示词身份 |
| `frontend/src/types.ts`、`features/agent/ExtendedResults.tsx`、`report-export.ts` | 整体主题类型、维度列及导出分母说明；沿用现有页面布局 |
| `tests/test_review_positive_summary.py`、`test_review_dimension_content.py`、`test_review_topics.py`、`topic_fixture.py` | 同义表达、六维来源、分母、追问、空结果、失败、紧凑传输、供应商参数隔离及兼容性测试 |
| `frontend/src/features/agent/topics.test.tsx`、`frontend/tests/pros-cons.e2e.mjs` | 主题表和真实页面的四轮总结、刷新恢复、三宽度验收 |

表中后端相对路径均位于 `src/data_analysis_agent/`。

## 验收证据

| 证据 | 结果与边界 |
|---|---|
| [全部评论回归](../output/positive-summary-backend-delivery.log) | 最终 300 项执行，297 通过、3 项缺少隔离 PostgreSQL 环境而跳过；包含供应商参数与传输边界测试 |
| [共享语义及任务提交](../output/positive-summary-task-regression.log) | 15 项通过 |
| [回答兼容性专项](../output/positive-summary-answer-repeat.log) | 44 项通过，已包含在全部评论回归内，不重复累计 |
| [前端测试](../output/positive-summary-frontend.log) | 24 文件、217 项通过 |
| [生产构建](../output/positive-summary-build.log) | TypeScript/Vite 通过；保留既有超过 500 kB 分包提示 |
| [隔离页面验收](../output/playwright/positive-summary-BccuqX/acceptance.json) | 最终精简传输协议下，实际 React、FastAPI、Worker、SQLite、Chromium；合成评论及本机受控模型；四轮具体总结全部通过，刷新零新增模型调用，业务库哈希不变 |
| [隔离1440px 摘要](../output/playwright/positive-summary-BccuqX/summary-1440.png)、[隔离1024px 摘要](../output/playwright/positive-summary-BccuqX/summary-1024.png)、[隔离768px 主题表](../output/playwright/positive-summary-BccuqX/summary-table-768.png) | 页面无横向溢出，长答案可纵向滚动，输入区域位于视口内，浏览器错误 0；此前及最终实际页面截图均已查看 |
| [当前真实页面](../output/playwright/positive-summary-current-page/acceptance.json) | 访问现有 5173/8000 服务的真实 PostgreSQL 历史任务；仅 GET，无模型调用；1440/1024/768 无横向溢出，错误 0 |
| `git diff --check` | 通过 |

## 真实页面联合验证

复用原始真实范围：`reviews_csv / reviews-csv`、商品 `100013588053`、UTC `2020-08-25—2022-08-27` 及原标注身份。使用重启后的实际 5173/8000 页面与 Worker，执行模型为 `Pro/deepseek-ai/DeepSeek-V3.2`，任务身份 `review-agent-v9`。

| 问题 | 最终任务 | 结果 | 纳入评论 / 观点 | 已归类 / 未归类观点 | 总耗时 |
|---|---|---|---|---|---|
| 物流的好评体现在哪里 | `63a91ba3d86a47ee99131cb2b6ca371e` | ok / passed | 30 / 31 | 29 / 2 | 51.736 秒 |
| 客服的好评体现在哪里 | `658c283fd77444e6aeaa9696b8bc6483` | ok / passed | 37 / 37 | 37 / 0 | 46.704 秒 |
| 商品评论中具体的好评 | `3fb9f403fc134b0584dc0ee8d78a93bd` | ok / passed | 18 / 43 | 19 / 24 | 40.106 秒 |

每项均恰好两次主题模型请求，正向和商品范围正确，所有主题批次完整通过。客服轮实际承接物流成功任务；整体初次目录响应未通过校验，保留失败任务后，沿用成功客服父任务重新提交并通过。耗时包括查询和回答，60 秒限制仅约束主题阶段，未声称所有阶段总耗时必然少于 60 秒。

真实输出已包含“发货派件快”“客服响应及时”“客服热情耐心”“安装服务态度好”等具体主题；整体结果按六个一级维度组织，包含价格、服务、使用效果等要点，不再仅展示六维数量。服务下的安装评价和在线客服体验按原文分别命名。

- [真实联合验收与三轮摘要](../output/playwright/positive-summary-live/acceptance.json)
- [持久化结果零新增模型调用复验](../output/playwright/positive-summary-live/verification.json)：三项全部重新通过 `validate_report` 和 `validate_topic_result`，复算来源、去重数量和分母。
- [真实物流回答](../output/playwright/positive-summary-live/logistics-answer.png)、[真实客服回答](../output/playwright/positive-summary-live/service-answer.png)
- [真实1440px 整体回答](../output/playwright/positive-summary-live/whole-1440.png)、[真实1024px 整体回答](../output/playwright/positive-summary-live/whole-1024.png)、[真实768px 主题表](../output/playwright/positive-summary-live/whole-table-768.png)：已查看，页面无横向溢出、输入区位于视口内。

复制回答、刷新恢复均通过；刷新后的任务模型调用数未增加，浏览器错误为 0。仅为当前商品与本次输入的联合验收，不代表全量模型准确率或性能基线。

真实结果还暴露了上游标签边界问题：既有物流标注中含安装服务表述，包装标注中含商品外观表述。代码保留既有六维标签，没有重新标注或改写数据；来源和数量校验通过并不能证明上游维度分类准确。相关内容应后续人工复核，不能把这些主题当作已经人工确认的标准分类。

隔离模型预设将“当天发出／下单即发货”归为“发货及时”、“回复及时／回应迅速”归为“客服响应及时”，用于验证数据、工具、统计及页面链路，不是实际模型理解准确率评测。

真实 PostgreSQL 只读核对沿用任务 `11dba6e36fdb4e9c82b664dfbec93494` 的日期、数据集和标注身份，商品绑定为 `100013588053`，所有抽取片段逐项验证原文跨度：

| 范围 | 总体正向匹配评论 | 总体观点 | 提取评论 | 提取观点 |
|---|---:|---:|---:|---:|
| 物流 | 228 | 234 | 100 | 103 |
| 客服 | 229 | 251 | 100 | 104 |
| 整体 | 505 | 1374 | 100 | 268 |

整体提取后的主题阶段仍会进一步执行 200 条观点、20 种表达上限；表中“提取观点”不是实际已归并数量。以上真实核查外部模型调用为 0，不包含真实主题结论。

真实模型联调暴露了完整归属响应耗时过长和模型字符下标错误的问题。归属复核现在只在传输时使用短字段，模型返回逐字支持片段，代码唯一定位跨度后恢复为原始完整结构，再执行所有原有校验。没有删除原文支持跨度或允许模型提供统计。新增测试覆盖 emoji 之后的正确位置、不存在的片段、重复位置及空片段。正向探索收敛到一批 20 种表达，在 60 秒内为发现和独立复核各保留最多 30 秒；超过范围仍是明确标注的非随机样本。

仅对 `api.siliconflow.cn` / `api.siliconflow.com` 上的 DeepSeek-V3.2 主题请求显式设置 `enable_thinking=false`，其他提供商与模型不发送该扩展字段。该开关由[供应商官方接口](https://docs.siliconflow.cn/docs/api/chat-completions-post)支持；主题发现和独立复核仍分别调用。真实 20 条表达的复核探针返回 20 条归属、398 个输出 token、0 个 reasoning token，约 19 秒，来源与跨度检查通过。

早期失败保留于 [初次归属超时](../output/playwright/positive-summary-live/first-attempt.json)、[紧凑传输重测](../output/playwright/positive-summary-live/wire-attempt.json)、[20 秒发现超时](../output/playwright/positive-summary-live/discovery-timeout-attempt.json)、[客服跨度错误](../output/playwright/positive-summary-live/service-span-attempt.json) 和 [整体目录响应未通过](../output/playwright/positive-summary-live/whole-discovery-attempt.json)，均未发布主题结论、均不计为验收通过。

## 复现

```powershell
$env:LABEL_EXECUTION_MODE='local'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_review*.py' -q
$env:PYTHONPATH='tests'
.\.venv\Scripts\python.exe -m unittest test_assistant_semantics test_task_submission -q
npm.cmd --prefix frontend test -- --maxWorkers=1
npm.cmd --prefix frontend run build
$env:E2E_POSITIVE_SUMMARY='1'
node frontend/tests/pros-cons.e2e.mjs
```

浏览器脚本启动并回收本次隔离前端、API 和模拟服务，不接管用户既有进程。受限环境需要允许本机回环连接；首轮受限环境启动等待没有计为通过，最终以上述完成的日志为准。

## 尚待验证与运行状态

- 最初真实评论外发被自动审批拒绝，未绕过；用户随后明确授权这三项查询的数据和接收方，才继续联合验收。最终三项通过，未来请求仍可能因外部服务超时或非法响应被安全拒绝，不能承诺模型服务始终成功。
- 当前 8000/5173 服务已重启加载新代码，实际提交任务使用 `review-agent-v9`。历史任务没有改写，早期联调失败任务保留；新问题使用新路由，旧任务重试继续遵守既有身份检查。
- 模型主题是候选总结，来源和数量通过代码校验不等于语义判断必然正确。未进行真实模型准确率评测、人工全面复核或压力测试。

本阶段没有扩展品牌整体分析、多商品主题比较、负向整体语义总结或新页面重构。
