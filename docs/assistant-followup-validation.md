# 评论分析多轮追问验收

日期：2026-10-30。范围：本次追问、歧义澄清及查询快照阶段。

## 文件变更

- `frontend/src/features/agent/followup.ts`：从成功任务的实际工具参数解析维度、周期和原文分页追问；范围变化和多个目标先澄清，历史服务端专用字段不重新提交。
- `frontend/src/features/agent/useAgentChat.ts`：关联父轮、冻结查询、确认后提交、失败幂等重试，读取失败不猜测上文。
- `frontend/src/features/agent/chat-state.ts`：保存并恢复查询及澄清快照，兼容旧分析记录。
- `frontend/src/features/agent/ChatMessage.tsx`、`AiAssistantPage.tsx`、`assistant.css`：追问入口、澄清选项、快照查看与 JSON 下载、窄屏按钮换行。
- `frontend/src/features/agent/followup.test.tsx`：9 项解析、分页、范围、历史参数、快照恢复、重复提交和失败路径测试。
- `frontend/tests/assistant.e2e.mjs`：复用现有真实 API、独立 SQLite、Vite 和 Chromium，增加追问及澄清验收；模型使用本机脚本化响应。
- `docs/review-agent.md`：功能用法及持久化边界。

本次未修改 Python API、数据库结构或统计口径。工作区同期出现的后端、评测脚本及测试改动来自其他工作，未回退或纳入本次交付。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| `npm test -- --maxWorkers=2 --reporter=dot` | 21 个文件、150 项通过；之后补充的历史字段用例由下列最终专项覆盖 |
| `npm test -- src/features/agent/followup.test.tsx --maxWorkers=1` | 最终追问专项 9/9 通过，含同轮多页继续查询回归 |
| `npm run build` | TypeScript 与 Vite 通过；保留既有大于 500 kB 分包警告 |
| `node frontend/tests/assistant.e2e.mjs` | 完整助手验收通过；浏览器错误 0；64 张截图 |
| 1440 / 1024 / 768 / 390 像素 | 页面无横向溢出，输入区在视口内；澄清按钮可滚动到可视区域且中心未被遮挡；已人工查看快照与澄清截图 |
| `git diff --check -- frontend docs/review-agent.md` | 通过；仅 Git 换行提示 |

最终浏览器证据：`output/playwright/assistant-e8FQsK/acceptance.json`，以及同目录 `followup-snapshot-*.png`、`followup-clarification-*.png`。

关键链路：按日包装趋势 → 只看物流 → 换成按月 → 继续查原文；包装原文第一页 → 下一页且评论 ID 不重复；切换数据集后先澄清 → 刷新保持待确认 → 点击当前范围后执行新任务。刷新不调用模型，旧轮快照不被新范围改写。

首次全量检查的括号错误已修复；并发超时通过降低测试并发复核。首次分页验收使用了没有足够原文的范围，产品正确显示末页提示；脚本已显式选择有多页证据的商品、日期和标注版本后通过。

## 保留边界

- 此轮验证使用隔离样本和本机模拟模型，没有验证真实模型理解准确率、生产数据库性能或完整工作台其他业务 E2E。
- 规则覆盖明确的维度、周期和原文追问；未知指代要求补充完整问题。基础问答仍为单轮。
- 澄清记录及父轮关联保存在当前浏览器，不提供跨设备会话同步；执行结果保存在现有服务端任务。
