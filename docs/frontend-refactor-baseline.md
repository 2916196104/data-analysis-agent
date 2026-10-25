# 观数前端重构阶段 0 基线报告

日期：2026-10-25（Asia/Taipei）。本阶段完成环境、测试和现有界面基线采集，没有进入阶段 1A。

## 1. 结论与进入下一阶段的前提

| 项目 | 结论 |
| --- | --- |
| 开发规范 | 已读取根目录 `AGENTS.md` 和 `docs/frontend-refactor-plan.md` |
| 计划阶段划分 | **尚未拆分 1A/1B**；原计划第 10 节仍是“阶段 1：抽取 AppLayout 和 API 基础层” |
| 单元测试 | 已验证：6 个文件、28 项测试通过 |
| TypeScript 与生产构建 | 已验证：通过，存在大 chunk 警告 |
| lint | 未配置：`package.json` 没有 lint script，本阶段没有新增或临时下载 lint 工具 |
| Playwright 浏览器启动 | 已验证：Chrome 156.0.8078.47 可以启动和退出 |
| 原始 E2E 命令 | **验证失败**：两次在导入弹窗关闭期间重复定位“数据处理阶段”，未达到全量通过 |
| 补充诊断验收 | 已验证：临时副本等待弹窗关闭，进程使用 local 标注模式，57 项流程检查通过，浏览器运行错误 0 |
| 视口基线 | 已采集 1440、1024、768、390 px，8 个状态共 32 张截图；页面级横向溢出 0 |
| 实际业务库和线上模型 | 未使用；全部写操作只发生在新建的隔离模拟 SQLite 数据库 |

进入阶段 1A 前，应先确认计划拆分：1A 仅处理 AppLayout/导航/布局样式；1B 再处理 API 分层。本报告提出边界，不修改现有计划，不自动实现任一阶段。原始 E2E 的定位问题和标注模式隔离也需要明确后续处理范围。

## 2. Git 工作区与文件保护

本次开始时和结束验证时，分支为 `refactor/frontened-ux`，HEAD 为 `136f6130fd1a06df27fe8c7283eabe3f656bcb85`。

开始时的已有改动：

```text
AM AGENTS.md
?? docs/frontend-refactor-plan.md
```

`AM` 表示 `AGENTS.md` 已暂存为新增文件，暂存后又有工作区修改；不能将其当成未跟踪文件或本次新改动。本阶段保留其暂存区和工作区内容，也保留未跟踪的计划文件。

本阶段新增：

- 本报告 `docs/frontend-refactor-baseline.md`。
- `output/playwright/` 中的隔离数据库、日志、下载、截图和临时诊断工具；该目录已在项目 `.gitignore` 中。

未修改 React 组件、Python、既有测试脚本、npm scripts、依赖清单、lockfile、`.env` 或重构计划。`npm run build` 按已有命令生成 `frontend/dist` 和 TypeScript 缓存，均属于已忽略的构建产物。没有执行 Git 暂存、提交、清理或回退。

## 3. 已有测试配置与运行环境

配置来源：[package.json](../frontend/package.json)、[vite.config.ts](../frontend/vite.config.ts)、[tsconfig.json](../frontend/tsconfig.json) 和 [workbench.e2e.mjs](../frontend/tests/workbench.e2e.mjs)。

| script | 实际配置 | 本阶段执行 |
| --- | --- | --- |
| `dev` | `vite --host 127.0.0.1` | E2E 通过既有 Vite 入口启动隔离端口 |
| `build` | `tsc -b && vite build` | 已执行并通过 |
| `preview` | `vite preview --host 127.0.0.1` | 未执行，浏览器基线使用开发服务器 |
| `test` | `vitest run` | 已执行并通过 |
| `test:watch` | `vitest` | 未执行，基线使用一次性测试 |
| `test:e2e` | `node tests/workbench.e2e.mjs` | 已执行，失败原因见第 5 节 |
| `lint` | 无 | 不适用，未声称通过 |

Vitest 配置位于 Vite 配置内：`jsdom` 环境、`clearMocks: true`、`restoreMocks: true`；测试文件与源码并置。没有独立 `playwright.config.*`，E2E 是 Node 脚本，直接使用 `playwright.chromium` 和断言；默认调用已安装 Chrome，可用 `E2E_BROWSER_CHANNEL` 选择浏览器。

本机实际安装版本（区别于 package.json 的版本范围）：Node 24.19.0、npm/npx 11.17.0、React/ReactDOM 19.3.1、Ant Design 5.29.3、ECharts 6.1.0、dayjs 1.11.23、TypeScript 5.9.3、Vite 7.3.7、Vitest 3.2.7、Playwright 1.64.1、Testing Library React 16.3.3、jsdom 27.4.0。本阶段没有安装或升级依赖。

## 4. 单元测试与构建结果

执行目录：`frontend/`。

```powershell
npm test
npm run build
```

| 测试文件 | 数量 | 已验证内容 |
| --- | --- | --- |
| `task-state.test.ts` | 5 | 进度百分比、任务状态、显式重试、网络错误、迟到响应隔离 |
| `api.test.ts` | 7 | 参数和幂等键、错误包络、查询编码、删除、上传字节与失败处理 |
| `analysis-draft.test.ts` | 6 | 默认月份与基线、人工日期、版本校验、旧草稿和问题恢复 |
| `useTask.test.tsx` | 4 | 轮询生命周期、重连、取消、Agent 持久结果恢复 |
| `ImportProgress.test.tsx` | 3 | 上传确认、读取与提交阶段、失败前计数 |
| `AgentResults.test.tsx` | 3 | 空分母/真实零、商品筛选、标注补齐只打开表单并保留范围 |

合计 28/28 通过。TypeScript `strict` 编译和 Vite 构建通过。构建产物主要体积（未压缩）：

| 产物 | 大小 | gzip |
| --- | --- | --- |
| UI chunk | 1,025.92 kB | 323.29 kB |
| charts chunk | 539.68 kB | 184.03 kB |
| 应用入口 chunk | 286.48 kB | 91.98 kB |

Vite 报告部分 chunk 超过 500 kB；这是已存在的构建警告，本阶段未进行拆包优化。

## 5. 浏览器与 E2E：原始失败和补充验证

### 5.1 执行记录

| 运行 | 结果 | 证据目录 |
| --- | --- | --- |
| 沙箱内原始 `npm run test:e2e` | 失败，等待 API ready 超时 | `output/playwright/frontend-kRjBxS/` |
| 沙箱外原始命令第 1 次 | 失败，原脚本第 314 行严格定位重复元素 | `output/playwright/frontend-0GVDaZ/` |
| 沙箱外原始命令第 2 次 | 同一位置再次失败 | `output/playwright/frontend-Nuw4R4/` |
| 增加弹窗同步与截图的诊断副本，未覆盖标注模式 | 失败，label 任务返回 `持久标注队列只支持 PostgreSQL。` | `output/playwright/frontend-PziL2l/` |
| 同一诊断副本，进程指定 `LABEL_EXECUTION_MODE=local` | 通过，57 项流程检查、浏览器运行错误 0 | `output/playwright/frontend-I095XA/` |

API 超时排查取得 Python 调用栈：阻塞在 `socket._fallback_socketpair → asyncio.windows_events → uvicorn.run`，尚未监听 API。沙箱外相同入口正常启动，说明该次超时与执行环境相关，不能归为页面回归。

原脚本第 314 行使用全页面 `getByLabel('数据处理阶段', { exact: true })`。提交完成后的页面已有任务进度区，但 Ant Design 导入 Modal 仍处于关闭生命周期，DOM 中同时存在两段相同标签，Playwright 因严格模式拒绝读取。两次原样执行都复现。未改动原脚本，也未把它登记为通过。

另一项配置问题：`reviews.labeling.run_labeling` 会读取其 `env_file`（默认根目录 `.env`）决定 `LABEL_EXECUTION_MODE`。现有 E2E 默认使用 SQLite，没有显式覆盖标注执行模式；当前 Redis 模式不兼容该 SQLite 夹具。仅在最后一次诊断进程设置 `LABEL_EXECUTION_MODE=local`，没有改配置文件、队列代码或业务库。

### 5.2 补充诊断与隔离范围

临时工具保存在忽略目录中：

- `output/playwright/phase0-20261025-prepare.cjs`：读取原 E2E，生成诊断副本。
- `output/playwright/phase0-20261025-diagnostic.mjs`：原有流程加截图；在原进度断言前增加“等待导入对话框隐藏”。

原断言没有删除或放宽。副本只调整自身的模块路径、增加必要的关闭同步和截图/DOM 尺寸记录。复现补充验收可在项目根目录、能正常运行 Windows asyncio 的终端执行：

```powershell
node output/playwright/phase0-20261025-prepare.cjs
$env:LABEL_EXECUTION_MODE = 'local'
node output/playwright/phase0-20261025-diagnostic.mjs
```

以上命令中的环境变量只用于验收进程；不要把本地业务 `.env` 改成 local 模式。端口 18765/15173 被占用时脚本失败，不结束其他服务。

所有数据写入新建的 `output/playwright/frontend-*/reviews.sqlite3` 和任务库。使用项目已有 `scripts/frontend_fixture.py` 创建明确标记为模拟的 96 条评论，真实 FastAPI、TaskWorker、HelloAgents/OpenAI SDK 调用本机模拟模型。未调用付费供应商或访问真实业务数据库。

现有 E2E 中任务历史分页、历史接口故障和空 options 场景使用脚本原有的响应替身/故障注入；分页验证不能等同于真实多页后端列表验证。其余核心导入、标注、固定分析、Agent 和结果恢复走真实隔离 API/Worker。最终任务记录截图在分页替身撤销之后采集，显示隔离库的真实任务。

完成后脚本关闭浏览器和自己启动的服务，实测 18765/15173 两端口均已关闭；本阶段保留隔离数据与证据用于追溯。

## 6. 四视口截图与布局观察

最终补充验收的统一证据目录：[frontend-I095XA](../output/playwright/frontend-I095XA/)。

| 状态 | 文件前缀 | 1440 × 900 | 1024 × 768 | 768 × 1024 | 390 × 844 |
| --- | --- | --- | --- | --- | --- |
| 分析工作台 | `baseline-workbench-` | 已采集 | 已采集 | 已采集 | 已采集 |
| 当前数据预览 | `baseline-data-preview-` | 已采集 | 已采集 | 已采集 | 已采集 |
| 固定分析结果 | `baseline-analysis-result-` | 已采集 | 已采集 | 已采集 | 已采集 |
| 导入对话框 | `baseline-import-dialog-` | 已采集 | 已采集 | 已采集 | 已采集 |
| 标注对话框 | `baseline-annotation-dialog-` | 已采集 | 已采集 | 已采集 | 已采集 |
| 任务记录抽屉 | `baseline-task-history-` | 已采集 | 已采集 | 已采集 | 已采集 |
| Agent 提问 | `baseline-agent-question-` | 已采集 | 已采集 | 已采集 | 已采集 |
| Agent 结果 | `baseline-agent-result-` | 已采集 | 已采集 | 已采集 | 已采集 |

文件名为“前缀 + 宽度 + `.png`”，例如 [1440 首页](../output/playwright/frontend-I095XA/baseline-workbench-1440.png)、[390 首页](../output/playwright/frontend-I095XA/baseline-workbench-390.png)、[390 Agent 结果](../output/playwright/frontend-I095XA/baseline-agent-result-390.png)。同名 `.txt` 保存截图时的界面文本；[baseline-measurements.json](../output/playwright/frontend-I095XA/baseline-measurements.json) 保存视口、主内容、Header、工具区、对话框边界和图表/表格数量。

已完成 DOM 测量和代表性截图人工目视检查，观察如下：

- 当前是顶部品牌/状态栏和横向操作区，没有 Sidebar；路径保持 `/`，任务切换由 localStorage 状态决定。
- 1440 px 主内容、Header 和工具区外框均为 1200 px 宽，左边距 120 px；首页内部分析表单最大宽度为 920 px。后续布局改造须考虑外框与表单两层宽度。
- 32 个采集状态均未出现页面级横向溢出，所有可见对话框左右边界均在视口内。
- 1024/768 px 仍保留横向工具栏和双列表单；390 px 工具区换行、表单单列、指标两列，开始分析按钮位于首屏下方。
- 固定结果中 ECharts canvas 正常渲染；商品对比、问题、证据表格和 Agent 表格存在。390 px 的宽表采用表格内部横向滚动，不代表所有列能同时看见。
- 数据预览和任务记录抽屉在 390 px 为视口宽度；任务长标题换行。标注长数据集名在选择框内省略，未突破边界。
- 导入高级设置展开后，1440 × 900、1024 × 768 和 390 × 844 的表单主体需要内部纵向滚动才能完整看到提交按钮。这是当前布局现象；自动化可滚动并完成导入，本阶段未修改。
- 对话框和 Drawer 的截图使用 fullPage：固定遮罩只覆盖当前 viewport，截图下方可能看到未遮罩的页面剩余部分，不能据此认定运行中的遮罩失效。

## 7. 功能回归范围与证据

下表的浏览器“已验证”指最后一次**补充诊断验收**，不代表原始 E2E 命令通过。可核对 [acceptance.json](../output/playwright/frontend-I095XA/acceptance.json) 中 57 项清单。

| 功能 | 已验证范围 | 证据/边界 |
| --- | --- | --- |
| 顶部操作与状态 | 导入入口唯一、准备数据、数据详情、新建分析、刷新、任务记录键盘打开/Esc 关闭 | 当前为组件状态切换；没有新路由 |
| 选择与草稿 | 数据集/商品选择、日期回车不误提交、高级设置折叠、商品/日期/备注刷新恢复 | `analysis-draft` 单测和浏览器流程 |
| 数据预览 | 真实隔离评论正文和商品选择，抽屉展示与关闭 | `baseline-data-preview-*` |
| 数据导入 | 缺文件提示、中文/空格路径、原生选择器、JSONL/GZ 元数据、CSV 文本 ID 和来源、上传中间字节、服务端处理阶段 | `upload-evidence.json`；上传文件与源字节一致 |
| 导入幂等与数量 | 丢失提交响应后复用上传副本和同一任务；上限 2 条评论；重复导入新增 0、重复 2 | `upload-evidence.json`、`database-evidence.json` |
| 大批导入 | 实际导入 30,000 条隔离评论，读取字节/行数、提交后计数、处理中刷新和隔离数据删除 | `processing-evidence.json`；不是生产性能基准 |
| 评论标注 | 数量整数校验、商品范围、批量 2/并发 2、失败重试参数、实时进度、2 条成功记录与观点、刷新恢复、继续下一批跳过已成功记录 | `database-evidence.json`：模型调用 2、观测最大并发 2 |
| 标注到分析 | 商品、标注模型版本和日期继承，一键分析使用已有标注，不增加模型调用 | 固定分析任务参数与调用计数断言 |
| 固定分析 | 真正创建/执行任务，读取 run 快照、结论、趋势、商品对比、负面问题、证据及图表 | DEMO-A 当前 24 条、负面 12 条、50%、1 项规则触发 |
| 导出与恢复 | 固定分析 JSON 的 run_id/results/本机备注一致；刷新恢复和断网重连 | `analysis-*.json` 与真实快照逐项比较 |
| 失败与重试 | 损坏 gzip 的真实任务失败、显式重试生成父子任务、查看原任务 | `03-failure.png`、任务日志 |
| 删除 | 删除本次隔离导入的评论和关联标注；删除任务记录返回 404；业务数量恢复 | 只删除本次随机隔离数据，未操作真实库 |
| 任务历史 | 空列表、键盘入口、分页 UI、真实任务结果恢复、错误重试、390 px 抽屉/长标题 | 分页列表响应使用原脚本替身；真实多页后端未独立验证 |
| Agent 问答 | 当前已选数据集、两商品、日期及标注版本绑定；真实任务队列、SDK 和统计/证据工具；原文详情、文件哈希引用、JSON 导出、刷新不再调用模型 | Agent 模型响应由本机模拟接口提供；2 次模型响应，趋势汇总 48 条评论 |
| 指标状态和补标 | 待标注/失败/空正文/无评论/真实 0% 区分，补标入口不自动提交并保留窗口/截止时间 | 28 项单测的一部分；不是对真实模型质量的评估 |

主要界面定位契约需要在阶段 1A 继续保留：`导入数据文件`、`准备数据`、`数据详情`、`新建分析`、`任务记录`、`刷新数据`、`开始分析`、`提交导入任务`、`提交标注任务`、`评论分析问题`、`提交问题`、`下载 JSON`、`下载 Agent JSON` 及 `.history-item` 等现有选择器。若阶段 1A 调整导航，应明确迁移这些交互定位，不顺带改变任务协议。

## 8. 未验证项目与遗留问题

**未验证：** PostgreSQL/Redis 端到端标注链、真实业务库、真实供应商额度和模型质量、人工复核独立页面、Firefox/Safari/Edge、生产 preview 部署、全部后端回归、真实多页任务列表、所有表格滚动/排序/筛选和日期弹层的每一种窄屏组合。不能用本次 SQLite/Chrome 验收替代这些结论。

**验证失败或需处理：**

1. 计划未拆分阶段 1A/1B：需先确认范围；本阶段没有修改计划。
2. 原始 E2E 重复定位：需要在单独获准的测试维护范围内处理 Modal 生命周期同步，不能声称原命令全绿。
3. SQLite 验收受项目标注模式影响：需为测试命令提供显式隔离配置，避免读取 Redis 模式。本阶段仅覆盖诊断进程环境。
4. 沙箱内 Python asyncio socket pair 启动受阻：本次在沙箱外取得实际页面证据。
5. 构建 UI/charts chunk 超过阈值：保留警告，未开展拆包。
6. 导入高级设置滚动与移动端主按钮位置：已记录当前视觉基线；是否优化须归入后续明确阶段。

## 9. 证据到后续行动

| 证据 | 发现 | 后续路径 |
| --- | --- | --- |
| 原计划第 10 节 | AppLayout 与 API 同在阶段 1 | 确认 1A/1B 文件边界后再实施 |
| 原脚本第 314 行、两次 failure-state | Modal 关闭期间同名标签并存 | 独立测试同步修复，不改进度业务语义 |
| 隔离 label 任务 business_error | Redis 模式与 SQLite 夹具冲突 | 显式测试模式隔离，不修改业务 `.env` |
| 32 张截图和尺寸 JSON | 当前布局与窄屏行为已留档 | 作为阶段 1A 对齐、溢出和 overlay 回归对照 |
| 57 项补充检查及数据库/下载证据 | 核心操作链在隔离模式可完成 | 后续每阶段继续保护相同 API、任务、指标和证据契约 |

本阶段到此停止。下一步等待确认阶段 1A 的边界与执行授权。
