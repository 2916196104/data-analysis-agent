# 观数前端重构计划（阶段 0 基线版）

状态：阶段 0 已完成。阶段 0 只建立运行基线、冻结入口与契约、修订本计划；没有修改 React、Python、数据库、环境配置或依赖。

## 1. 范围和保护边界

本计划覆盖 frontend/ React 工作台及其与 Python 静态工作台、/api/v1 的边界。

必须保持：

- Python API 路径、HTTP 方法、请求体、响应包络、错误结构和 Idempotency-Key 语义。
- 导入、评论标注、固定分析、Agent 查询、任务轮询、重试、删除和 JSON 导出。
- /analysis/new、/assistant、/data-management、/annotation、/tasks 及兼容路径。
- Header、固定侧栏、右侧独立滚动、移动端导航和现有视觉样式。
- frontend/workbench 原生人工复核工作台及其独立 /api、CSRF、Host 和 Origin 约束。
- 现有兼容入口：src/api.ts、src/types.ts、根目录 re-export 文件和动态 import。

本阶段禁止：删除文件、移动源码、修改业务代码、修改 Python、修改数据库、更新依赖、降低测试断言、提交或推送 Git。

## 2. 阶段 0 运行基线

### 2.1 工作区和工具链

基线日期：2026-10-28（Asia/Taipei）。

| 项目 | 结果 |
|---|---|
| 分支 | refactor/frontened-ux |
| HEAD | cf184af 柱状图的修改2 |
| Git 工作区 | 干净；没有已有修改需要迁移或覆盖 |
| Node/npm | Node 24.19.0，npm 11.17.0 |
| 项目环境 | frontend/node_modules 和根目录 .venv 均存在 |
| 依赖树 | npm ls --depth=0 无 missing、extraneous 或 invalid 报告 |
| lint | 未配置；package.json 没有 lint script |
| CI | 未发现 .github、.gitlab 或其他前端 CI 配置 |

### 2.2 现有脚本

来自 frontend/package.json：

| 命令 | 作用 |
|---|---|
| npm run dev | Vite 开发服务器，127.0.0.1 |
| npm run build | tsc -b && vite build |
| npm run preview | Vite 预览服务器 |
| npm test | Vitest 一次性单元测试 |
| npm run test:watch | Vitest watch 模式 |
| npm run test:e2e | node tests/workbench.e2e.mjs，启动隔离 API、Worker、Vite、模拟模型和 Playwright |

reference-ui.e2e.mjs 是独立的只读浏览器脚本，不在 package script 中；capture-layout.mjs 由 workbench.e2e.mjs 导入。

### 2.3 单元测试

执行命令：

~~~powershell
cd E:\AllProject\data-analysis-agent\frontend
npm test
~~~

结果：

- 12 个测试文件通过。
- 88 项测试通过。
- 总耗时约 28.85 秒。
- 覆盖 API 契约、草稿日期、任务状态、轮询、路由、趋势图、Agent、导入进度和任务记录页面。

### 2.4 生产构建

执行命令：

~~~powershell
cd E:\AllProject\data-analysis-agent\frontend
npm run build
~~~

结果：TypeScript strict 检查和 Vite 构建通过。当前未压缩产物：

| 文件 | 大小 |
|---|---:|
| index-CEJpJAnO.js | 314.94 kB |
| charts-C-wSvojg.js | 575.93 kB |
| ui-BE-NglN5.js | 1,057.85 kB |

构建仍有既有的“部分 chunk 大于 500 kB”警告。本阶段不做拆包优化，也不把警告误判为构建失败。

### 2.5 E2E

第一次在普通执行环境运行 npm run test:e2e：

- 失败于 API ready 超时。
- fixture.log 和 vite.log 有启动结果，api.log 为空，API 没有监听。
- 证据目录：output/playwright/frontend-56tfaH/。

第二次在允许本机端口和 Windows asyncio 的执行环境运行同一个命令：

- 隔离 fixture、FastAPI、Worker、Vite 和 Chrome 均启动。
- 实际完成数据导入、失败导入、任务重试、成功导入、评论标注和四视口布局截图。
- browser-errors.json 为空，浏览器运行错误为 0。
- 最终在既有脚本的 prepared analysis ready 等待条件处超时，位置为 frontend/tests/workbench.e2e.mjs:438。
- 证据目录：output/playwright/frontend-VgzEIO/。

该失败记录为当前基线的“部分通过”，不能宣称全量 E2E 通过，也不能归因于本阶段重构。后续必须单独分析该等待条件，再决定是测试环境、历史断言还是页面状态问题。

## 3. 当前真实目录结构

~~~text
frontend/
├─ src/
│  ├─ main.tsx
│  ├─ App.tsx
│  ├─ api.ts
│  ├─ types.ts
│  ├─ useTask.ts
│  ├─ task-state.ts
│  ├─ local-history.ts
│  ├─ analysis-draft.ts
│  ├─ Composer.tsx
│  ├─ LabelForm.tsx
│  ├─ ResultCards.tsx
│  ├─ AgentResults.tsx
│  ├─ Chart.tsx
│  ├─ DataPreview.tsx              # 兼容 re-export，待确认是否仍为公共入口
│  ├─ ImportProgress.tsx            # 兼容 re-export，测试仍直接引用
│  ├─ app/
│  │  ├─ AppLayout.tsx
│  │  └─ router.ts
│  ├─ features/
│  │  ├─ agent/
│  │  ├─ analysis/
│  │  ├─ annotations/
│  │  ├─ datasets/
│  │  └─ tasks/
│  ├─ shared/api/
│  └─ shared/styles/
├─ tests/
│  ├─ workbench.e2e.mjs
│  ├─ reference-ui.e2e.mjs
│  └─ capture-layout.mjs
└─ workbench/
   ├─ index.html
   ├─ workbench.js
   └─ workbench.css
~~~

当前源码规模为 59 个 TypeScript/TSX 文件和 8 个 CSS 文件。最大编排文件是 src/App.tsx，类型中心是 src/types.ts。

## 4. 页面、路由和业务入口

| URL | 页面 | 入口和状态 |
|---|---|---|
| /analysis/new | 评论分析 | Composer、商品/日期/版本/备注、ReviewDashboard |
| /analysis/new?mode=agent | Agent 兼容入口 | 解析后进入 /assistant |
| /assistant | AI 评论分析助手 | AiAssistantPage、useAgentChat、消息历史和输入区 |
| /assistant/tasks/:taskId | Agent 历史结果 | 从既有 analysis task 读取 task.result.agent |
| /data-management | 数据管理 | 数据集目录、上传、评论原文预览 |
| /annotation | 评论标注 | AnnotationPage、LabelForm、批量/并发/失败重试 |
| /annotation/prepare | 标注兼容入口 | 解析为 /annotation |
| /annotation/tasks/:taskId | 标注任务 | 标注进度、结果、继续分析/下一批 |
| /tasks | 任务与报告 | 分页、刷新、删除、状态和结果入口 |
| /tasks/:taskId | 通用任务兼容入口 | 读取任务类型后重定向到分析、标注或 Agent 模块 |
| / | 旧入口 | 仅在本机 localStorage 有选中任务时恢复旧任务 |

### 4.1 /api/v1 契约

统一客户端位于 src/shared/api/http-client.ts，src/api.ts 组合资源 API 并作为兼容门面。成功响应为：

~~~json
{"status":"ok","result":{}}
~~~

错误响应为：

~~~json
{"status":"error","error":{"code":"...","message":"...","details":[]}}
~~~

React 当前调用：

| 方法 | 路径 | 调用方 |
|---|---|---|
| GET | /health | App 健康状态和执行器状态 |
| GET | /options | 数据集、商品、标注版本 |
| GET | /quality | 日期和评论数量 |
| GET | /reviews | 数据预览，参数为 product_key/profile_id/page/page_size |
| GET | /reviews/{id} | 评论原文和字段详情 |
| GET | /analytics/trend | 当前分析范围，重复发送 product_ids，附 period=month |
| GET | /analysis-runs/{id} | 固定分析冻结快照 |
| POST | /import-files?kind=...&filename=... | 原始字节上传和进度 |
| POST | /tasks | 导入、标注、分析、Agent 任务；带 Idempotency-Key |
| GET | /tasks?page=...&page_size=15 | 任务历史分页 |
| GET | /tasks/{id} | 任务状态、进度、错误和结果 |
| POST | /tasks/{id}/retry | failed/partial 任务重试；带 Idempotency-Key |
| DELETE | /tasks/{id}/imported-data | 删除导入数据并刷新目录 |
| DELETE | /tasks/{id} | 删除任务记录，不删除业务数据 |

后端还提供 /products、/labeling/*、/analytics/top-issues、/analytics/compare-products、/analytics/evidence、/analysis-runs 和 /alerts。这些接口被 Python 测试和文档覆盖，不能因为当前 React 没有调用就删除。

### 4.2 任务流程

- 导入：XHR 上传 → 服务确认 → import task → 轮询 → 导入结果。
- 标注：label task，参数为 source/dataset_version/product_ids/limit/workers/retry_failed，可带当前分析日期范围。
- 固定分析：analysis task，参数包含模型身份、数据范围、当前窗口、基线窗口和可选备注。
- Agent：仍使用 analysis task，但带 question；结果保存在 task.result.agent，不请求 analysis-runs。
- 轮询：活跃任务约 1.5 秒一次；网络失败约 5 秒重连；切换任务会取消旧请求。
- 重试：只允许 failed 或 partial，后端创建带 parent_task_id 的新任务。
- 删除：运行中任务不可删除；删除导入数据后刷新 options、quality、历史和当前任务。

### 4.3 replay 缺口

后端支持 replay task，前端 TaskKind 也声明了 replay，但当前页面逻辑只对 import、label 和普通 analysis 做明确分支：

- taskStepLabel 会把未识别类型显示成分析结果。
- taskRoute 会把非 label 任务归到分析模块。
- 任务列表的 replay 展示和详情没有独立页面。
- TaskInput 没有 replay 提交类型。

这不是删除候选。后续必须选择“增加只读 replay 页面”或“明确显示为未接入的历史任务”，避免误导用户。

## 5. 独立 workbench 入口

frontend/workbench/ 不是 React 旧副本，而是独立人工复核产品：

- src/data_analysis_agent/api/workbench.py 读取源码目录或安装后的 share/data-analysis-agent/workbench。
- pyproject.toml 的 setuptools.data-files 打包三个静态文件。
- 页面使用 /api/options、/api/reviews 和 /api/reviews/{id}/human-reviews。
- 保存使用 X-CSRF-Token，服务限制本机 Host/Origin。

必须保留该目录、Python 入口、打包配置和独立协议。阶段 6 以后如要迁移，也必须先实现兼容 API 并保留原入口作为回滚路径。

## 6. 候选删除和冗余证据

| 候选 | 证据 | 当前结论 | 风险 |
|---|---|---|---|
| src/styles.css 中 .form-grid、.span-two | 全仓没有 JSX、测试、动态字符串或配置引用 | 低风险删除候选 | 低 |
| src/DataPreview.tsx | 只有一行 re-export；当前源码无调用方，README/旧计划仍提及 | 待确认的兼容入口 | 中 |
| src/ImportProgress.tsx | ImportProgress.test.tsx 直接引用，且是旧路径 re-export | 必须保留到兼容迁移完成 | 高 |
| features/datasets/index.ts 的 useDatasetPreview re-export | 内部调用方直接导入 hook 文件，未发现 barrel 调用方 | 待确认的公共 re-export | 低到中 |
| frontend/dist/、tsconfig.tsbuildinfo、.npm-cache/ | 构建或本地工具生成，已被忽略 | 可本地清理，不属于源码删除 | 低 |
| output/playwright/、outputs/ | 含历史验收证据和文档引用 | 保留，不能批量清理 | 中 |

动态 import 必须保护：Chart.tsx 由 ResultCards.tsx 和 ReviewDashboard.tsx 懒加载。api.ts、types.ts、useTask.ts 和 frontend/workbench/* 也不能按“当前静态调用较少”删除。

## 7. 候选合并和拆分

| 范围 | 当前问题 | 建议 | 预期收益 | 风险 |
|---|---|---|---|---|
| App.tsx | 同时管理页面、API、任务、存储、删除和导航 | 拆 page container 与 workspace hooks | 降低最大耦合点 | 高 |
| types.ts | 混合所有领域类型 | 按任务、数据集、分析、Agent、API 拆分；旧文件先 re-export | 缩小类型变更影响 | 高 |
| api.ts 与 shared/api/* | facade 和直接资源 API 并存 | 统一 feature 依赖资源 API，保留 facade 过渡 | API 入口单一 | 中 |
| analysis-draft.ts 与 dataset model | 数据集 feature 反向依赖分析模块 | 抽出 datasetKey/datasets | 切断跨模块依赖 | 低到中 |
| 全局 CSS | 分析 CSS 被数据管理、任务和标注使用 | 抽 shared PageHeader/Status/JSON 样式，再拆 feature CSS | 删除和移动更安全 | 中 |
| 选择器 | LabelForm 和 DatasetPreview 重复拼接 Select options | 复用 DatasetSelector/ProductSelector 和 option builder | 减少 UI 重复 | 中 |
| ResultCards 与 AgentResults | 共享指标、表格、证据视觉 | 只抽展示原语，不合并整页组件 | 保留不同数据语义 | 中高 |
| local-history | 同时持久化草稿和下载 JSON | 拆 storage adapter 与 JSON export helper | 责任更清晰 | 低 |
| Agent 历史消息 | 每个历史消息可能独立调用 useTask | 后续引入任务结果缓存 | 降低多轮历史读取开销 | 中 |

不合并 DataManagement 与 AnnotationPage，也不合并 React workbench 与原生人工复核页面。

## 8. 推荐目录结构

~~~text
frontend/src/
├─ app/
│  ├─ App.tsx
│  ├─ AppLayout.tsx
│  ├─ router.ts
│  └─ providers.tsx
├─ pages/
│  ├─ AnalysisPage/
│  ├─ DataManagementPage/
│  ├─ AnnotationPage/
│  ├─ TaskReportsPage/
│  ├─ TaskDetailPage/
│  └─ AiAssistantPage/
├─ features/
│  ├─ datasets/{components,hooks,model,api.ts,index.ts}
│  ├─ annotations/{components,hooks,model,api.ts,index.ts}
│  ├─ analysis/{components,hooks,model,api.ts,index.ts}
│  ├─ tasks/{components,hooks,model,api.ts,index.ts}
│  └─ agent/{components,hooks,model,api.ts,index.ts}
├─ shared/
│  ├─ api/{http-client.ts,index.ts}
│  ├─ hooks/
│  ├─ ui/{PageHeader,StatusBadge,JsonExportButton,Chart}
│  ├─ types/
│  ├─ formatters/
│  └─ styles/{tokens,globals,layout,components}
├─ legacy/compatibility-exports/
└─ main.tsx
~~~

迁移顺序必须先添加适配器和 re-export，再迁移内部调用；不要一次性做大规模重命名。

## 9. 分阶段重构顺序

阶段按风险从低到高排列，每阶段独立提交、独立测试、可单独回滚。

### 阶段 0：基线和契约冻结（已完成）

范围：本文件、运行记录、路由/API/资源清单。

验证：npm test 通过；npm run build 通过；E2E 记录为部分通过并保留证据目录。

回滚：恢复本文件即可，不涉及业务代码。

### 阶段 1：确认低风险冗余（下一阶段）

精确文件范围：

- frontend/src/styles.css：删除已确认无引用的 .form-grid、.span-two。
- frontend/src/features/datasets/index.ts：确认并处理未使用的 useDatasetPreview barrel re-export。
- frontend/src/app/router.test.tsx：补 replay 路由行为测试，不修改生产路由。
- frontend/README.md：修正仍指向旧 DataPreview.tsx 的文件说明。

验证：

- 全仓 rg 检查 CSS、re-export 和动态 import 引用。
- npm test。
- npm run build。
- 不运行后端写操作，不改变 API。

回滚：只回退上述单个文件变更。

### 阶段 2：拆 App 的只读编排层

建议文件范围：

- frontend/src/App.tsx。
- 新建 frontend/src/app/useWorkspaceCatalog.ts。
- 新建 frontend/src/app/useTaskHistory.ts。
- 新建 frontend/src/app/useTaskMutations.ts。
- 新建 frontend/src/pages/*。

不改变 API、路由、页面结构和 CSS。先移动状态读取和 mutation，再移动 JSX。

验证：单测、构建、任务/导入/标注/分析/Agent E2E、四视口截图。

回滚：保留旧 App.tsx，按页面容器切换恢复。

### 阶段 3：统一 feature API 和领域类型

建议文件范围：

- frontend/src/api.ts。
- frontend/src/shared/api/*。
- frontend/src/types.ts。
- 新建 features/*/model/types.ts。
- frontend/src/analysis-draft.ts。
- frontend/src/features/datasets/model/dataset-options.ts。

先保持旧 re-export，再迁移调用方。API 请求路径、参数和响应包络必须逐项对比 api.contract.test.ts。

验证：API 契约测试、TypeScript build、真实隔离导入/标注/分析/Agent 流程。

回滚：保留 api.ts 和旧 types.ts 作为适配层。

### 阶段 4：样式和共享 UI 收口

建议文件范围：

- frontend/src/styles.css。
- frontend/src/features/analysis/review-analysis.css。
- frontend/src/features/agent/assistant.css。
- frontend/src/features/tasks/task-reports.css。
- 新建 frontend/src/shared/styles/components.css。
- 新建 frontend/src/shared/ui/*。

只迁移重复样式和页面头，不改变视觉值和布局断点。

验证：1440、1024、768、390 截图，对比横向溢出、文本裁切、独立滚动和导航高亮。

回滚：按 CSS 文件回退，不回退业务组件。

### 阶段 5：任务类型和 Agent 读取优化

范围：replay 明确展示策略、Agent 历史任务缓存、任务类型判别联合。

验证：任务历史、直接 URL、浏览器前进后退、Agent 刷新恢复、失败重连、重试和导出。

回滚：保留旧任务详情和逐任务读取路径。

### 阶段 6：兼容入口收口

只有确认外部调用方、文档、测试和动态引用都迁移完成后，才评估删除：

- frontend/src/DataPreview.tsx。
- frontend/src/ImportProgress.tsx。
- 未使用的 barrel re-export。

验证：全仓搜索、npm test、npm run build、E2E、独立 workbench 打包和启动检查。

回滚：保留原文件恢复导入路径。

## 10. 阶段 0 后的第一批低风险修改清单

本清单是下一阶段建议，不代表本阶段已经执行：

| 文件 | 修改 | 收益 | 风险 | 验证 |
|---|---|---|---|---|
| frontend/src/styles.css | 删除 .form-grid、.span-two | 减少无效 CSS | 低 | rg + 单测 + build |
| frontend/src/features/datasets/index.ts | 清理未使用的 hook re-export | 减少无意义公共入口 | 低到中 | 全仓 import 搜索 + build |
| frontend/src/app/router.test.tsx | 增加 replay 缺口保护测试 | 冻结当前行为边界 | 低 | Vitest |
| frontend/README.md | 更新旧文件和入口说明 | 避免后续误删兼容入口 | 低 | 文档链接检查 |

本阶段完成后停止，等待确认再进入阶段 1。

