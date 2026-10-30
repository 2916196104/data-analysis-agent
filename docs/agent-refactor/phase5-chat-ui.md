# 阶段 5：商品数据分析 Agent 前端 AI 助手交互与布局优化

完成日期：2026-10-30（Asia/Taipei）

本阶段只调整 React 展示层、交互层和前端验收脚本。阶段 1～4 的统计 SQL、工具编排、PublicAnswer 生成、任务终态保护和公开接口均未重构；数据库内容和任务提交协议没有变化。

## 修改文件

- `frontend/src/features/agent/AiAssistantPage.tsx`：聊天区域滚动跟随策略和“回到最新消息”入口。
- `frontend/src/features/agent/ChatMessage.tsx`：公开回答优先、精简查询范围、开发字段折叠、证据下一页和任务状态保护。
- `frontend/src/features/agent/PublicAnswerView.tsx`：结论、重点、样本限制和复制回答操作。
- `frontend/src/features/agent/AssistantScopeBar.tsx`：默认显示数据集与商品摘要，完整窗口和标注字段可展开。
- `frontend/src/AgentResults.tsx`：统计/证据区与开发审计区分层，证据分页继续走现有追问协议。
- `frontend/src/features/agent/assistant.css`：回答卡片、范围摘要、审计折叠、移动端和滚动入口样式。
- `frontend/src/task-state.ts`：`answer_generating` 显示为“正在整理分析回答”。
- `frontend/src/features/agent/PublicAnswerView.test.tsx`：复制、范围折叠和证据分页测试。
- `frontend/tests/assistant.e2e.mjs`：增加复制和阶段 5 三类截图记录，保留原有真实链路验收步骤。
- `frontend/tests/phase5-ui.e2e.mjs`：受控接口响应下的 Vite/Chromium 页面验收脚本。

阶段 0～4 已存在的后端和前端改动仍保留在工作区，本报告只描述阶段 5 新增的前端行为。

## 页面布局和滚动

AI 助手继续使用 `AppLayout` 的主内容区，桌面端左侧导航保持固定，聊天内容区和输入区在主内容区内部布局。聊天消息、范围栏和输入框使用相同的最大阅读宽度，窄屏下沿用现有导航折叠和多行输入布局。

聊天列表现在记录用户是否停留在底部：

- 用户在底部时，新任务状态或新回答会跟随到最新消息。
- 用户阅读历史消息时，任务更新不会强制拉到底部。
- 有未读的新消息时显示“回到最新消息”，点击后才滚动到末尾。

没有引入弹窗或右侧抽屉，统计详情、证据和审计信息仍在当前聊天消息中展开。

## 回答与统计详情分层

成功任务的正文使用后端 `public_answer`：

1. 结论摘要；
2. 来自 PublicAnswer 的重点统计；
3. 样本量或分母说明；
4. 可信限制说明；
5. 复制回答操作；
6. “查看统计、证据与执行记录”入口。

复制内容通过 `publicAnswerText()` 组装，只包含公开回答正文，不读取 `trace`、完整 JSON、模型名、API 地址或内部结果编号。旧任务没有 PublicAnswer 时继续使用安全兼容读取；如果公开回答状态不是 `ok/unsupported`，聊天区不发布回答和详情。

统计详情默认折叠，展开后继续复用 `AgentResults` 和 `ExtendedResults` 的真实结构化数据。原文证据只展示后端返回的当前页；存在下一页时显示“继续查看下一页”，点击后调用 `chat.send(turn.parameters, '继续查原文')`，因此仍由后端原有分页协议冻结范围和页码。

开发审计信息移动到独立的“开发调试与结果校验”折叠区，其中包括校验清单、工具 trace、模型调用数、JSON 下载、Markdown 导出和完整范围脚注。普通回答和统计表不会默认显示这些字段。

## 查询范围展示规则

分析消息默认显示：

`当前数据集：<dataset_version> · 商品：<商品名称或真实商品 ID>`

范围栏也采用同样的摘要。商品名称来自现有目录映射，映射不存在时使用真实商品 ID；“全部商品”保留实际商品数。当前窗口、基线窗口、标注版本和服务地址放在“查看完整查询范围”及更深的“开发调试字段”中。

分析消息继续使用提交时复制的 `turn.parameters`，因此全局数据集切换、商品选择或日期编辑不会改写历史消息的范围。范围编辑仍由 `AssistantScopeBar` 的应用动作一次性提交，未应用的草稿不会进入任务。

## 状态和操作

- `queued/running`：只显示任务阶段，例如“正在统计评论”或“正在整理分析回答”，没有假的百分比。
- `succeeded`：只有任务成功、结果校验未失败且 PublicAnswer 状态可发布时才显示正文。
- `partial/failed`：只显示任务错误、部分完成说明和既有重试入口，不显示统计结论、证据或导出按钮。
- `generation_status=fallback`：继续展示可信模板回答，并保留轻量说明“解释生成未完成，已根据核验统计生成可靠摘要”。
- 复制回答、导出报告、重试任务、展开统计详情和证据下一页只在相应数据或协议可用时渲染。

输入区保留自然语言、多行自适应、Enter 发送、Shift+Enter 换行、发送中禁用和错误反馈。没有增加重复快捷键说明或新的工具选择面板。

## 验证结果

### 前端单测

在 `frontend` 目录执行：

```text
npm test -- --reporter=dot
```

结果：22 个测试文件、169 个测试通过。测试输出仍有既有 jsdom 提示 `Window's getComputedStyle() method: with pseudo-elements`，不影响断言。

新增覆盖：

- 公开回答复制只复制自然语言正文；
- 查询范围摘要默认折叠完整字段；
- 证据详情提供下一页入口；
- 既有历史、失败、部分完成和 PublicAnswer 发布保护测试保持通过。

### 生产构建

执行 `npm run build` 通过，TypeScript 检查和 Vite 构建均完成。Vite 继续提示已有的大于 500 kB chunk 警告，本阶段没有增加依赖或改变分包策略。

### 受控 Chromium 页面验收

执行 `node frontend/tests/phase5-ui.e2e.mjs` 通过。脚本使用真实构建产物、真实 React/Vite 页面和 Chromium，接口响应由脚本提供的受控测试数据拦截，不访问生产数据库或真实模型。

覆盖内容：

- PublicAnswer 正常展示和复制；
- 好评统计正文不出现内部结果编号、模型身份或 API 地址；
- 包装与物流趋势回答；
- LLM 解释降级回答；
- 统计详情折叠；
- 评论证据下一页入口；
- 用户阅读历史消息时保留滚动位置并提供回到最新消息；
- 1440、1024、768、390 像素下页面和聊天区域无横向溢出。

三张重点截图：

- [好评维度回答（1440）](../../output/playwright/phase5-ui/positive-answer-1440.png)
- [包装与物流趋势回答（1440）](../../output/playwright/phase5-ui/trend-answer-1440.png)
- [解释超时降级回答（390）](../../output/playwright/phase5-ui/fallback-answer-390.png)

同一目录还生成了三类回答在 1024、768、390（或 1440）宽度下的完整截图，共 12 张；验收过程输出保存在 `output/phase5-ui-e2e.log`。

### 真实助手链路阻塞

阶段 4 的 `frontend/tests/assistant.e2e.mjs` 已尝试重新执行。隔离 fixture 已成功生成，但当前受限环境中，测试脚本启动的 API/Vite 子进程端口无法被 Chromium 进程连接，脚本停在健康检查阶段，没有生成可宣称通过的真实链路 `acceptance.json`。因此本阶段没有把该脚本标记为通过；需要在允许本地子进程端口互通的环境中重跑。

## 兼容性和已知问题

- 后端 API、任务协议、`result_ids`、`results`、`scope`、PublicAnswer 和导出内容保持兼容。
- 历史任务没有 PublicAnswer 时仍调用现有安全公开读取流程，不重复提交任务或重新查询统计。
- 原始评论、证据引用和完整 JSON 仍可从详情/导出获取，只是从普通正文移到审计层。
- 页面仍沿用现有表格展示趋势，没有根据自然语言临时生成图表；趋势中的空月份、低样本和不同分母继续由后端结构化结果负责。
- 真实生产数据、真实模型延迟和真实浏览器到后端的跨进程连通性未在本次受限环境中验证。

阶段 5 已完成后停止，未进入阶段 6。
