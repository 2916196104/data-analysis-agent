# 阶段 1A：AppLayout 与布局样式验收

日期：2026-10-25（Asia/Taipei）  
分支：`refactor/frontened-ux`

## 1. 阶段范围与结果

从阶段 0 基线继续实施阶段 1A，将页面壳从业务编排中抽出，并集中基础样式和布局 Token。保留 `/` 入口、绿色视觉、现有工作台操作、任务历史 Drawer、导入/标注 Modal 和任务恢复方式。

AppLayout 只接收展示状态与回调，不请求接口、不读取后端字段、不解释任务结果。请求、任务提交、错误提示和弹窗状态仍由 App 管理。未迁移 API、路由、任务中心或人工复核页面；未增加依赖、修改 Python、业务数据库或 `.env`。

## 2. 文件变更

| 文件 | 变更 |
| --- | --- |
| `frontend/src/app/AppLayout.tsx` | 抽取品牌、状态栏、范围摘要、工作台操作、main 和 footer |
| `frontend/src/App.tsx` | 接入 AppLayout，保留业务状态、请求和全部操作回调 |
| `frontend/src/shared/styles/tokens.css` | 集中品牌、背景、文字、字体、圆角、间距、控件高度和内容宽度 |
| `frontend/src/shared/styles/globals.css` | 提取全局基础样式、键盘焦点和减少动画设置 |
| `frontend/src/shared/styles/layout.css` | 提取布局及响应式规则，修复 768px 下页脚与内容相差 8px 的边距 |
| `frontend/src/main.tsx` | 引入共享样式；Ant Design 从 CSS Token 读取主题值 |
| `frontend/src/styles.css` | 保留业务组件样式，移除已迁移的重复布局声明 |
| `frontend/tests/workbench.e2e.mjs` | 导入进度断言前等待 Modal 关闭；测试进程显式使用 local 标注；加入布局采集 |
| `frontend/tests/capture-layout.mjs` | 在真实隔离流程中采集四种宽度，断言对齐、边距、页面/弹窗溢出、导航文字和图表/表格渲染 |
| `docs/frontend-refactor-plan.md` | 明确阶段 1A/1B 边界，API 分层保留在阶段 1B |
| 本文档 | 记录验收结果和限制 |

原有暂存的 `AGENTS.md`、基线报告和计划保留；本轮只修改计划的工作区版本，没有暂存、提交、回退或清理既有改动。

## 3. 验证结果

执行目录：`frontend/`。

| 检查 | 结果 |
| --- | --- |
| `npm test` | 6 个文件、28 项测试通过 |
| `npm run build` | TypeScript 和 Vite 构建通过；页脚修复后已重跑 |
| `npm run test:e2e` | 页脚修复后完整复验通过：原有 57 项流程检查、32 张布局截图，浏览器运行错误 0 |
| `git diff --check` | 通过 |
| lint | 项目未配置 lint script，未声称通过 |

沙箱内首轮 E2E 在 API ready 阶段超时（证据：`output/playwright/frontend-iBMh2u/`）。在获准的沙箱外执行环境中，同一命令正常启动 API，首次完成原有 57 项流程检查和 32 张布局截图（`frontend-mX5DYa/`）；目视发现 768px 页脚边距不一致后修复，再完整复验。

隔离环境由既有 E2E 启动 Vite、真实 FastAPI、TaskWorker 和本机模拟模型；数据来自项目明确标记为模拟的 fixture，存入每次新建的 SQLite 测试库。只有测试进程指定 `LABEL_EXECUTION_MODE=local`，业务配置不变。任务历史分页、历史错误和空 options 继续使用原脚本的响应替身/故障注入，其余核心流程通过实际隔离 API；不能将分页 UI 验收解释为真实多页后端验收。

## 4. 页面与截图

最终复验目录：[frontend-Afx2BU](../output/playwright/frontend-Afx2BU/)。[流程验收结果](../output/playwright/frontend-Afx2BU/acceptance.json) 和 [布局测量记录](../output/playwright/frontend-Afx2BU/layout-measurements.json) 可追溯本轮断言。

四种视口为 1440×900、1024×768、768×1024、390×844。采集 8 种状态：分析工作台、数据预览、固定分析结果、导入弹窗、标注弹窗、任务记录、Agent 提问和 Agent 结果。

文件命名为 `layout-状态-宽度.png`，尺寸与渲染数据保存在 `layout-measurements.json`。所有截图由实际 Vite 页面生成，没有替换生产接口或向业务页面加入模拟数据。

32 个状态均通过页面横向溢出、顶部/主内容/页脚对齐、导航文字裁切和可见弹窗横向越界检查；固定分析图表及表格均正常渲染。已确认页脚与主内容的左边界在四个视口下分别为 156、36、28、16px。

代表性截图已目视检查：工作台 1440/768/390px、固定结果 1024/390px、导入 390px、标注 1024px、预览 1440px、任务历史 768px、Agent 提问 1024px、Agent 结果 768px。宽表在容器内横向滚动，不等同于页面溢出。全页截图中遮罩只覆盖当前 viewport，这是固定遮罩的截图表现。

- [1440px 工作台](../output/playwright/frontend-Afx2BU/layout-workbench-1440.png)
- [768px 页脚对齐](../output/playwright/frontend-Afx2BU/layout-workbench-768.png)
- [390px 固定分析结果](../output/playwright/frontend-Afx2BU/layout-analysis-result-390.png)
- [768px Agent 结果](../output/playwright/frontend-Afx2BU/layout-agent-result-768.png)

## 5. 遗留问题与未验证项

- UI 和 charts chunk 仍超过 500kB（约 1026kB / 540kB），本阶段未做拆包。
- 导入高级设置展开后，部分视口需要在弹窗内纵向滚动才能看到提交按钮，与基线一致；现有导入流程可完成。
- 未验证真实业务库、PostgreSQL/Redis 端到端链路、付费模型质量、独立人工复核页面、生产部署和其他浏览器；未运行后端全量回归。
- App 中业务编排仍较多，后续按既定 feature 阶段逐步迁移，不在布局阶段一次重写。

本次到阶段 1A 为止。阶段 1B 的 API 分层尚未实施。
