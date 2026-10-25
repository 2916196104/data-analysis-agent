# 观数参考图重构与验收

本轮视觉重构已完成：58 项单测、57 项既有浏览器业务检查、9 项真实服务只读检查及生产构建通过。未修改后端或数据库；真实供应商模型与生产 Redis 链路未验证。

## 范围与基线（阶段 A）

本次按用户指定的 A→B→C→D 完成一个视觉重构交付，不推进原架构计划的目录迁移、任务路由或人工复核迁移。唯一主要视觉依据为 `reference-review-analysis.png`（来自本次附件，原仓库无此文件）。

- 技术栈：React 19、TypeScript strict、Vite 7、Ant Design 5、Ant Design Icons、ECharts 6、普通 CSS 与 CSS Tokens；不增加依赖。
- 现有 React 没有 URL 路由，通过 App 状态、localStorage 和 Drawer/Modal 恢复工作流；保留这种方式，不引入路由框架。
- 既有未提交改动：App、Composer、DataPreview、ImportProgress、dataset-api、review-api、http-client 和整个 features/datasets；在其基础上修改，不回退。
- 重构前 `npm test`：7 个文件、52 项通过。
- 重构前 `npm run build`：已有 `useImportTask.ts` 的 `useRef<AbortController>()` 与 React 19 类型不兼容；本次只补齐 undefined 类型与初值。
- 重构前 E2E 首次在受限环境中等待 API 超时；采用已有本机隔离验收，业务库与供应商模型均不作为测试夹具。

## 现有功能与重构后入口映射

下表根据实际源码确认，页面名称不作为实现证据。API 均相对于既有 `/api/v1`，独立人工复核除外。

| 已实现功能 / 源码 | 原入口 | 重构后入口 | 真实 API 与保护项 |
| --- | --- | --- | --- |
| 数据目录、健康 / App、catalog-api | 顶部工具区 | 侧栏辅助操作、数据管理 | GET options、quality、health；加载失败与刷新保留 |
| 文件导入 / ImportDialog、useImportTask | 导入数据 | 侧栏导入数据、数据管理 | POST import-files、POST tasks(kind=import)；原生文件选择、字节进度、取消、重试与幂等不变 |
| 商品、数据集选择 / Composer、DatasetSelector、ProductSelector | 分析表单 | 评论分析筛选、更多分析设置、数据管理 | options；保留多商品选择与草稿 |
| 日期与基线 / analysis-draft、Composer | 分析表单 | 筛选时间范围、更多分析设置 | UTC、基线不重叠校验、自动基线、备注与版本选择保留 |
| 固定分析 / Composer、App、useTask | 开始分析 | 评论分析标题右侧 | POST tasks(kind=analysis)、GET tasks/{id}、GET analysis-runs/{id}；任务失败、冻结结果与幂等不变 |
| 模型标注 / LabelForm、App | 准备数据 | 评论标注、侧栏准备数据、分析前置准备 | POST tasks(kind=label)；数量、并发、失败重试、批次范围、进度与继续下一批保留 |
| 评论预览 / DataPreviewDrawer、useDatasetPreview | 数据详情、预览数据 | 侧栏数据详情、数据管理、分析高级设置 | GET reviews、reviews/{id}；分页、版本、原文与下载保留 |
| AI 问答 / Composer、AgentResults | Agent 问答 | AI 助手、AI 洞察、分析高级设置 | POST tasks(kind=analysis, question)；真实队列、工具、校验、证据、补标确认和 JSON 下载保留 |
| 历史、恢复 / App、useTask、local-history | 任务记录 | 任务与报告、侧栏任务记录 | GET tasks、tasks/{id}；分页、键盘访问、刷新、断网恢复、本机备注保留 |
| 任务重试、删除 / App | 任务详情、历史 | 原任务详情、任务与报告 | POST tasks/{id}/retry、DELETE tasks/{id}；原确认与错误处理保留 |
| 删除导入数据 / App | 导入任务完成后 | 原任务完成页 | DELETE tasks/{id}/imported-data；不可恢复确认与目录刷新保留 |
| 固定分析全部结果 / ResultCards | 任务结果 | 原任务结果 | 趋势图/表、商品对比、问题、规则、原文、参数详情、JSON 导出全部保留 |
| 只读趋势 / 后端 routes/analytics.py | 已有 API，React 首页未展示 | 评论分析指标、评论趋势 | GET analytics/trend；只新增前端读取，沿用原参数与统计口径 |
| 人工复核 / frontend/workbench | 独立服务 8765 | 数据管理辅助链接 | 原独立 /api 与 CSRF；不迁移、不新增后端 |

数据概览、通知、账户设置尚无对应 React 业务实现，仅标记未开放。数据管理整合已有目录与操作；侧栏菜单触发现有页面或对话框，不伪造新 URL。

## 视觉与统计规则（阶段 B / C）

页面按原图采用外围细边框、大圆角、实测约 96px Header、230px Sidebar、标题与主按钮、横向筛选、2×2 指标、绿色趋势图、轻量 AI 洞察。桌面外框最大宽度 1024px，与原图外框宽度对齐；主区约 716px。1024/768px 时侧栏收为 200/160px，390px 时通过菜单按钮展开导航。

评论总数、已标注和负面占比来自当前筛选范围的真实 trend 响应；商品数来自 options 中当前选择范围的商品数，并通过标题提示注明口径。未选择标注版本、没有数据、加载中和请求失败时明确区分；负面占比使用服务端 `negative_rate`，不在前端重算或把空分母显示为零。原图示例数字不进入业务代码。切换范围取消旧请求；刷新目录同步刷新统计。

## 验证与交付（阶段 D）

### 新旧页面结构

| 重构前 | 重构后 |
| --- | --- |
| 全宽品牌栏、连接状态、刷新与任务记录 | 外围细框内的品牌 Header，右侧通知及用户占位 |
| 横向工作台工具区 | 六项侧栏导航，底部保留导入、准备、详情、刷新工具 |
| 大张分析范围表单 | 标题右侧主按钮，简洁横向商品/时间筛选 |
| 首页没有真实指标和趋势概览 | 2×2 真实指标、独立绿色折线图、AI 洞察入口 |
| 同卡高级设置与多个操作按钮 | 高级设置折叠保留，独立数据管理整合已有目录/上传/预览 |
| 提交后显示任务及全部结果 | 保留原任务、进度、错误、结果、证据与下载组件；只统一绿色 |

### 本次文件清单

以下为本次实际触及的文件，不将进入任务前已有的未提交改动算作本次成果。

| 文件 | 变更 |
| --- | --- |
| `docs/ui/reference-review-analysis.png` | 原附件原样归档为验收图 |
| `docs/ui/frontend-visual-refactor.md` | 盘点、映射、视觉比较与验收记录 |
| `frontend/src/app/AppLayout.tsx` | Header、六项导航、辅助工具及移动菜单 |
| `frontend/src/App.tsx` | 绑定现有导航操作、数据管理、AI 弹窗及统计刷新；任务业务保留 |
| `frontend/src/Composer.tsx` | 重排展示与主按钮；原日期/基线/版本/备注/Agent 提交保留 |
| `frontend/src/features/analysis/ReviewDashboard.tsx` | 已有统计 API 的指标、趋势、空/加载/失败/重试展示 |
| `frontend/src/features/analysis/review-analysis.css` | 评论分析页样式 |
| `frontend/src/features/analysis/ReviewDashboard.test.tsx` | 未统计、空分母、零占比、取消竞态、重试、刷新检查 |
| `frontend/src/features/datasets/components/DataManagement.tsx` | 已有数据目录、上传/预览入口与独立人工复核链接 |
| `frontend/src/features/datasets/hooks/useImportTask.ts` | 仅补齐已有 AbortController ref 的类型初值，修复基线编译错误 |
| `frontend/src/shared/api/analysis-api.ts` | 读取原有 GET analytics/trend，使用重复商品参数和原取消协议 |
| `frontend/src/api.contract.test.ts` | 新增趋势查询参数与取消信号契约检查 |
| `frontend/src/shared/styles/layout.css` | 参考图页面壳与断点，替换旧布局样式 |
| `frontend/src/shared/styles/tokens.css` | #4E7468、字体、浅灰边框与控件高度 |
| `frontend/src/styles.css` | 删除被替代的首页样式并统一绿色 |
| `frontend/src/main.tsx` | 引入分析页面样式 |
| `frontend/src/ResultCards.tsx` | 只调整原趋势图主色，不改数据、系列或结果布局 |
| `frontend/.env.example` | 说明可选人工复核服务地址，不含凭据 |
| `frontend/tests/capture-layout.mjs` | 新布局的边界、侧栏、对齐、弹窗、图表及表格断言 |
| `frontend/tests/workbench.e2e.mjs` | 更新导航入口、过时的导入设置选择器与显式 CSV 版本填写；保留业务断言 |
| `frontend/tests/reference-ui.e2e.mjs` | 真实 PostgreSQL 的只读日期筛选、统计核对、入口与四视口截图 |

`DataPreview.tsx`、`ImportProgress.tsx`、`dataset-api.ts`、`review-api.ts`、`http-client.ts` 和其他 datasets 文件的既有改动保留，未覆盖。没有修改 Python、数据库、依赖版本或任务协议；未暂存或提交 Git。

### 真实服务验证

使用既有 Vite `npm run dev`（5173）和原后端 `serve --no-worker`（8000）。只读验收连接现有 PostgreSQL，不启动任务执行器，不提交标注、导入、删除或供应商模型请求。

- 目录真实返回 1706 款商品；本轮截图选中 `reviews-csv` 的 24 款商品。
- 用页面原有日期控件选择 2021-01-01 至 2022-08-31，接口真实返回评论 **9567**、成功标注 **4174**、负面占比 **62.9%**；页面指标与接口逐项相等。
- 日期筛选后真实趋势图正常渲染，刷新数据会重新读取统计。
- 数据管理、商品目录、评论原文预览、标注表单、AI 表单、历史任务与移动导航均通过。标注/AI 在真实库仅打开表单，没有提交模型请求。
- 默认日期继续沿用原来的全库 quality 最新月份；当前 CSV 数据主要在 2022 年，因此最初默认时间窗显示真实空状态。未改原日期推导语义，也未用全日期计数填充窗口统计。
- 只读验收结果：[acceptance.json](../../output/playwright/reference-ui/acceptance.json)，浏览器运行错误为 0。

### 截图与视觉比较

| 屏幕宽度 | 最终真实数据页面 | 响应式处理 |
| --- | --- | --- |
| 1440px | [截图](../../output/playwright/reference-ui/review-analysis-1440.png) | 1024px 外框居中，230px 侧栏，716px 主区 |
| 1024px | [截图](../../output/playwright/reference-ui/review-analysis-1024.png) | 200px 侧栏，保留横向筛选 |
| 768px | [截图](../../output/playwright/reference-ui/review-analysis-768.png) | 160px 侧栏，筛选分行，指标保持两列 |
| 390px | [截图](../../output/playwright/reference-ui/review-analysis-390.png) | 菜单展开导航，筛选分行，宽表仅内部滚动 |

另有[与原图接近宽度的 1116px 截图](../../output/playwright/reference-ui/review-analysis-1116.png)、[空时间窗](../../output/playwright/reference-ui/review-empty-window.png)、[真实原文预览](../../output/playwright/reference-ui/real-review-preview.png)、[390px 数据管理](../../output/playwright/reference-ui/data-management-390.png)、[390px 导入提交区域](../../output/playwright/reference-ui/import-dialog-390-actions.png)。截图保留在项目既有的 `output/playwright/` 输出目录。

| 对比项 | 参考图人工测量（约） | 最终桌面页面测量 |
| --- | --- | --- |
| 外框宽度 | 1024px | 1024px |
| Header 高度 | 96px | 96px |
| Sidebar 宽度 | 230px | 230px |
| 主区宽度 | 719px | 716px |
| 筛选卡片 y / 高度 | 205 / 74px | 205 / 74px |
| 指标区域 y / 总高度 | 295 / 166px | 295 / 166px |
| 趋势卡片 y / 高度 | 477 / 258px | 477 / 258px |
| 品牌色 | 近似 #4E7468 | #4E7468 |

已目视检查桌面、768px 和 390px 图像，四视口自动检查均无页面横向溢出。图表、卡片与筛选对齐，弹窗和抽屉边界由原 E2E 的四视口检查覆盖。

仍然存在的视觉差异：

1. 图表曲线、数值和日期标签来自真实数据，与原图示意走势不同；保留日期标签及悬停数据以便解读。
2. 使用现有 Ant Design 图标，品牌、用户、AI 图标形状与参考图略有区别。
3. 原功能需要的高级设置、侧栏辅助操作、目录页脚及执行器状态继续可见，完整页面比原图更高。执行器离线用轻量文本保留真实提示。390px 导入弹窗高级内容使用内部纵向滚动；已检查可滚动到提交按钮并完成缺文件校验。
4. 1024px 外框在更宽桌面居中；原附件左右留白不完全对称。字体因系统渲染存在细微差别，未宣称逐像素一致。

### 仅 UI 展示

- 数据概览：禁用并标注“未开放”。
- 通知、用户中心：图标保留，名称与悬停提示明确“未开放”，无网络调用。

评论分析、数据管理、评论标注、AI 助手及任务与报告均连接已有业务；没有用占位界面替代已实现功能。

### 测试结果和边界

| 检查 | 最终结果 |
| --- | --- |
| `npm test` | 8 个文件，58/58 通过（原 52 项保留，新增 6 项统计保护检查） |
| `npm run build` | TypeScript 与 Vite 生产构建通过 |
| `npm run test:e2e` | 原有 57 项业务检查全部通过，32 张四视口状态截图，运行错误 0 |
| `node tests/reference-ui.e2e.mjs` | 9 项真实服务只读检查通过，5 张评论分析截图及额外空状态/原文/移动数据管理/导入截图 |
| `git diff --check` | 通过 |
| lint | 项目未配置独立 lint 命令，未新增工具 |

最终完整回归证据：[frontend-tiChmf/acceptance.json](../../output/playwright/frontend-tiChmf/acceptance.json)，[布局测量](../../output/playwright/frontend-tiChmf/layout-measurements.json)。涵盖原有 CSV/JSONL/GZ、真实上传进度、丢响应幂等重试、数据库行和标注、任务失败/重试/删除、标注并发/继续批次、固定分析/证据/JSON、历史分页/刷新/断网恢复、Agent 工具/原文/导出/刷新不重复调用模型。分页列表、历史故障和空目录沿用原脚本的受控响应替身，其他核心操作走真实隔离 API。

重构前浏览器基线：[首页 1440px](../../output/playwright/frontend-qhKSIb/layout-workbench-1440.png)、[首页 390px](../../output/playwright/frontend-qhKSIb/layout-workbench-390.png)。原脚本在已有改动后的“导入设置”旧选择器处失败；本次按实际高级设置入口修正。CSV 验收显式填写数据集版本，继续断言真实导入来源与标注范围，不再依赖原脚本中过时的默认名称。

原有 E2E 沿用其真实 FastAPI/Worker/SQLite 和本机模型测试夹具；本次没有增加模拟业务后端。夹具数据、模型不代表真实供应商效果。隔离服务运行结束已退出；保留实际开发服务器与仅 API 预览供查看。

构建保留既有大 chunk 警告：UI 约 1058kB、charts 约 540kB（未压缩）；未做无关拆包或架构调整。

未验证：进行中的真实上传取消、真实供应商模型质量及额度、真实库写入/删除、生产 Redis 标注链路、独立人工复核服务内的编辑流程、生产部署、多浏览器及后端全量回归。当前实际预览后端为 `--no-worker`，页面如实提示执行器离线，提交的任务需原执行器运行后才会执行。

本次 A–D 视觉重构阶段完成，到此停止，不推进原架构计划的其他阶段。
