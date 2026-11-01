# AI 评论分析助手多会话 MVP 验收记录

日期：2026-11-01。范围：本地多会话管理。本阶段不增加搜索、分享、重命名、删除或跨设备同步。

## 实现方案与最终行为

- 在既有 `review-agent-chat:${API_BASE}` 存储键上升级为 `version: 2`，保存 `sessions` 和 `activeSessionId`。会话使用 UUID，包含标题、创建/更新时间、既有消息联合类型和轻量分析上下文。
- 分析轮继续复用 `ChatTurn`，保存任务 ID、幂等键、提交范围、查询及澄清快照。大型分析结果仍通过既有任务 API 读取；基础问答的文本及状态随会话保存在本机。
- 新建保留当前全局数据集、商品、日期和标注版本，清空聊天视图及输入草稿。当前为空时不重复创建；存在空会话时复用它。
- 历史入口位于页面标题右侧，使用 Ant Design 覆盖式抽屉，按更新时间倒序排列并标明当前会话。主导航、消息卡片、统计详情、输入组件和聊天最大宽度不重构。
- 每个会话独立持有提交锁、历史读取锁、加载状态和临时错误。异步回调绑定发起时的会话 ID，后台完成不会写入另一个会话。非活动会话的任务由后端继续执行，重新打开时读取最新状态，不重新提交。
- 旧单会话先解析为一个历史会话，首次写入新版存储前将完整原文保存至带 UUID 的备份键。备份失败时不覆盖旧记录；损坏记录提供警告和原文导出。读取失败且无法获得原文时停止写入，保留内存使用能力。
- 刷新时恢复活动会话；基础问答没有后端任务 ID，因此刷新中的 pending 回答变为“未确认，请重新发送”，不会自动调用模型。
- 后端 `load_analysis_context` 要求当前任务有非空会话 ID，并逐级校验父任务会话一致、范围兼容及结果验证。无会话 ID 的旧任务可以查看，不能成为新会话的上下文入口。
- 当前范围与历史范围不一致时，不继承旧分析作为省略式追问依据；完整的新问题可以独立提交。历史澄清选项在确认前再次核对当前全局范围，避免点击旧选项提交旧商品。
- 从任务 URL 打开时恢复到已有归属会话；之后手动切换不会被旧任务 URL 拉回。

## 文件清单

| 文件 | 改动 |
| --- | --- |
| `frontend/src/features/agent/chat-state.ts` | 会话集合模型、旧记录解析和兼容分析视图 |
| `frontend/src/features/agent/useChatSessions.ts` | 新增会话存储、迁移备份、损坏恢复和按会话管理请求状态 |
| `frontend/src/features/agent/useAgentChat.ts` | 会话归属写入、历史任务恢复、上下文及范围隔离 |
| `frontend/src/features/agent/AiAssistantPage.tsx` | 标题按钮、历史抽屉和存储恢复提示 |
| `frontend/src/features/agent/assistant.css` | 抽屉列表和按钮布局 |
| `frontend/src/App.tsx` | 切换时清理输入、保护延迟发送回调、传入当前范围 |
| `src/data_analysis_agent/jobs/analysis_context.py` | 后端严格校验上下文的会话归属 |
| `frontend/src/features/agent/sessions.test.tsx` | 新增多会话专项测试 |
| `frontend/src/features/agent/chat.test.tsx` | 更新基础问答持久化和新版存储断言 |
| `frontend/src/features/agent/followup.test.tsx` | 父任务夹具包含会话 ID，更新范围变化契约 |
| `frontend/src/features/agent/assistant-ui.test.tsx` | 更新普通问答保存断言 |
| `tests/test_review_intent.py` | 缺失、空白及跨会话父任务拒绝继承测试 |
| `frontend/tests/sessions.e2e.mjs` | 真实本地 API、Worker、Vite、Chromium 会话验收与截图 |

没有新增数据库表，没有改变分析工具权限、统计口径、评论标注或 Agent 分析实现。

## 验收证据

| 场景 | 验证方式与结果 |
| --- | --- |
| A 新建空会话、保留旧记录 | 单元测试及浏览器通过；重复点击不增加空会话 |
| B 两个会话分别提问 | 单元测试及真实任务 API/Worker 浏览器链路通过 |
| C 历史切换 | 单元测试及抽屉点击通过，消息和任务引用未混入 |
| D 刷新恢复 | 浏览器恢复最后活动会话，提交数不增加 |
| E A 执行时切换 B | 本机模型响应门控使 A 保持执行，切换 B 后放行；A 只在自身历史展示。单元测试还覆盖延迟提交、普通问答和错误回调 |
| F 新会话“那物流呢” | 浏览器先澄清，不提交父任务或读取另一会话上下文 |
| G 保留当前商品 | 单元测试使用“美的-ID”；浏览器使用明确标注的 DEMO-A，验证新会话提交相同 ID |
| H 商品/会话交叉切换 | 浏览器从 DEMO-A 改为 DEMO-B，历史任务仍为 DEMO-A，省略追问要求澄清，新完整问题使用 DEMO-B |
| I 单会话迁移 | 浏览器和单元测试验证已完成任务 ID/幂等键保留、原文备份、零重提交；损坏及配额错误由单元测试验证 |
| J 原分析能力回归 | 评论系列后端测试覆盖意图、统计、主题、证据及范围；单独提交测试覆盖任务幂等 |

后端命令与结果：

```powershell
$env:LABEL_EXECUTION_MODE='local'
.venv/Scripts/python.exe -u -m unittest discover -s tests -p 'test_review*.py' -q
# 284 项：281 通过，3 跳过（需要隔离 PostgreSQL）
.venv/Scripts/python.exe -m unittest discover -s tests -p test_task_submission.py -q
# 4 项通过
```

后端日志：`output/session-backend-local-tests.log`。最初未指定 local 模式时，SQLite 标注夹具受到默认 PostgreSQL 队列设置阻断；该轮失败未算通过。最终使用本地模式完成全部适用回归。

前端最终运行：`npm.cmd test -- --maxWorkers=2 --reporter=dot`，24 个文件、216 项全部通过。结果见 `output/session-frontend-tests.log`。`npm.cmd run build` 完成 TypeScript 检查和生产打包，保留既有大于 500 kB chunk 警告。

浏览器命令：`node frontend/tests/sessions.e2e.mjs`。最终结果 **passed**，浏览器脚本错误为 0。使用真实 FastAPI、Worker、Vite 和 Chromium，但数据库为隔离 SQLite、模型为本机模拟服务；没有以此声称真实模型准确率或生产 PostgreSQL 验收。

最终证据目录：`output/playwright/sessions-Ga5ruS/`，`acceptance.json` 保存提交参数、检查结果和尺寸测量。共 12 张截图：分析历史表格、历史抽屉、范围隔离各 4 个宽度（1440、1024、768、390px）。页面及消息区无横向溢出，输入区在视口内，历史标题无溢出，抽屉覆盖且可关闭。当前助手统计详情使用表格，此验收不把表格误称为图表。

## 剩余限制

- 只保证当前浏览器、当前站点和 API 地址下的本地恢复；不提供跨设备同步，也未实现多个标签页同时写入的合并。
- 后端任务仍可能被独立删除或不可达，此时沿用既有任务错误和重连提示。
- localStorage 容量耗尽时提示保存失败，不删除历史腾出空间。浏览器清理站点数据会移除本地会话。
- 没有会话归属的旧任务只能查看，继续提问需要完整问题；基础问答仍沿用原单轮 API，不增加模型历史记忆。
- 本次未调用生产模型，3 项 PostgreSQL 专项测试因没有隔离配置跳过。
- 当前仓库主题变量实际为蓝色（`shared/styles/tokens.css`），新增控件沿用原主题；本阶段没有修改全局品牌颜色或主导航。

本阶段完成后停止，不自动扩展其他会话功能。
