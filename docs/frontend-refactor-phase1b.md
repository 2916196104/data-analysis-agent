# 阶段 1B：前端 API 基础层验收报告

日期：2026-10-25（Asia/Taipei）  
分支：`refactor/frontened-ux`  
实施基线：`b2fc613`（阶段 1A），开始时工作区干净。

结论：阶段 1B 已完成；完整单测独立重跑、TypeScript/生产构建和原有 Playwright 流程通过，未发现请求契约或明显页面视觉回归。本阶段到此停止。

## 1. 范围与分层结果

已读取 `AGENTS.md`、[重构计划](frontend-refactor-plan.md)、[阶段 1A 报告](frontend-refactor-phase1a.md)，并对照[阶段 0 基线](frontend-refactor-baseline.md)检查请求与业务流程。

本阶段只组织 React 前端 API 代码。保留 `api.ts` 的公共导出及调用签名，现有页面、轮询 Hook、类型、布局和样式不变；不修改 Python、分析算法、业务数据库、人工复核页面或依赖。

### 分层前后对比

| 职责 | 分层前 | 分层后 |
| --- | --- | --- |
| 统一地址、JSON Fetch、超时与取消 | `api.ts` | `shared/api/http-client.ts` |
| XHR 原始文件上传、字节进度和上传错误 | `api.ts` 内联 | `http-client.ts` 的 `uploadRequest` |
| `ApiError`、错误文案、查询字符串 | `api.ts` | `http-client.ts` |
| 商品、数据集与标注版本目录 | `api.options` | `catalog-api.ts`，继续只调用 `/options` |
| 数据质量、文件上传入口、删除导入数据 | `api.ts` | `dataset-api.ts` |
| 任务列表、详情、提交、重试、删除 | `api.ts` | `task-api.ts` |
| 评论列表与原文详情 | `api.ts` | `review-api.ts` |
| 固定分析冻结快照 | `api.run` | `analysis-api.ts` |
| Agent 结果恢复 | `useTask → api.task → task.result` | 保持原流程，不增加快照或模型调用 |
| 现有调用入口和健康查询 | `api.ts` | `api.ts` 作为兼容入口，组合资源模块并保留单一健康查询 |
| 独立人工复核协议 | `frontend/workbench/workbench.js` | 原实现保留，未接入 React Client |

依赖方向为“页面/Hook → 兼容入口 → 资源 API → HTTP Client”。资源模块只描述路径、参数和返回类型；`fetch`、`XMLHttpRequest` 及基址拼接集中在 HTTP Client。沿用 `types.ts`，没有增加请求框架、状态框架、工厂或未使用的资源接口。

当前 React 商品目录由 `/options` 提供，没有为了目录划分新增 `/products` 请求。人工复核页面直接接收 `/api` 的业务 JSON，并在保存时发送 `X-CSRF-Token`；React 使用 `/api/v1` 的 `{status,result,error}` 包络。两套客户端没有共用解包逻辑，本阶段不新增无调用方的 `workbench-api.ts`，其迁移留在计划的人工复核阶段。

## 2. 修改文件清单

| 文件 | 变化 |
| --- | --- |
| `frontend/src/api.ts` | 保留 `api`、`API_BASE`、`ApiError`、`request`、`errorMessage`，组合资源 API |
| `frontend/src/shared/api/http-client.ts` | 新增统一 Fetch/XHR 传输与错误处理层 |
| `frontend/src/shared/api/catalog-api.ts` | 新增目录接口模块 |
| `frontend/src/shared/api/dataset-api.ts` | 新增质量、上传与导入数据删除模块 |
| `frontend/src/shared/api/task-api.ts` | 新增任务接口模块 |
| `frontend/src/shared/api/review-api.ts` | 新增评论与原文查询模块，注明协议边界 |
| `frontend/src/shared/api/analysis-api.ts` | 新增冻结分析快照模块 |
| `frontend/src/api.contract.test.ts` | 新增 24 项请求契约测试，拆分前先在原实现上通过 |
| `frontend/src/api.test.ts` | 扩展既有上传测试，检查方法、请求头、超时、进度上限与错误文案 |
| 本报告 | 记录范围、契约、验收和限制 |

`App.tsx`、`useTask.ts`、`DataPreview.tsx`、`local-history.ts`、`types.ts`、全部样式和现有 Playwright 脚本均未修改。没有执行 Git 暂存或提交。

## 3. 阶段 0 API 契约对照

直接比对 Git 中阶段 0 的 `136f613:frontend/src/api.ts` 与阶段 1A 的相同文件，内容一致。迁移后的基址定义、`ApiError`、Fetch `request` 和 `errorMessage` 与阶段 0 对应源码相同。

| 原有操作 | 保留的方法和路径（相对于 API_BASE） | 保留的关键契约 |
| --- | --- | --- |
| 健康与目录 | GET `/health`、`/options`、`/quality` | 返回原始 result，不转换业务字段 |
| 任务历史 | GET `/tasks?page=…&page_size=15` | 默认 page=1，页大小 15 |
| 任务详情 | GET `/tasks/{id}` | ID 使用 encodeURIComponent；支持 AbortSignal |
| 固定分析 | GET `/analysis-runs/{id}` | 编码 ID、取消信号及冻结快照返回值 |
| 评论列表 | GET `/reviews?product_key=…&profile_id=…&page=…&page_size=8` | 原编码顺序、页大小 8；仅省略 undefined；支持取消 |
| 评论详情 | GET `/reviews/{id}?profile_id=…` | 省略版本时继续保留原末尾 `?`；0 值不丢失 |
| 创建任务 | POST `/tasks` | 原 TaskInput JSON、Content-Type 和 Idempotency-Key |
| 显式重试 | POST `/tasks/{id}/retry` | 原幂等键，无请求体、不额外添加 JSON Content-Type |
| 删除任务 | DELETE `/tasks/{id}` | 原返回值和错误处理 |
| 删除导入数据 | DELETE `/tasks/{id}/imported-data` | 路径、返回的 task/deletion/reused 结构不变 |
| 上传文件 | POST `/import-files?kind=…&filename=…` | XHR 发送原 File 字节；保留文件 MIME/默认 octet-stream、10 分钟超时和字节进度 |

Fetch 仍有 30 秒超时；调用方取消通过 `AbortSignal.any` 传给 Fetch，保留原中止异常。POST 网络错误仍包含“提交结果暂未确认”的提示；HTTP 错误、非 JSON、后端 details 和通用错误文案保持原样。

XHR 仍在服务端确认成功后才 resolve；上传字节计数限制在文件大小内。HTTP 拒绝、非法 JSON、缺失 result、断网、超时及 abort 均按原方式报错。既有上传方法没有公开取消参数，本阶段保持其签名及 abort 错误处理，不新增上传取消 UI。

任务轮询仍为活跃状态 1.5 秒、网络失败 5 秒，离开任务时取消请求，终态停止轮询。固定分析读取 run；Agent 从 task.result 恢复。localStorage key 使用同一个 API_BASE，不改变旧任务与草稿恢复范围。

## 4. 测试与构建

执行目录为 `frontend/`。

| 检查 | 结果 |
| --- | --- |
| 拆分前 `npm test -- src/api.test.ts src/api.contract.test.ts` | 原实现 31/31 通过，建立行为对照 |
| 拆分后 `npm test` | 独立重跑 7 个文件、52/52 通过；首轮并行运行时有一项超时，详见下节 |
| `npm run build`（`tsc -b && vite build`） | TypeScript strict 检查与生产构建通过 |
| 原有 `npm run test:e2e` | 原有 57 项流程检查通过，32 张布局截图，浏览器运行错误 0 |
| `git diff --check` | 通过 |
| lint | 没有配置 lint script，未新增工具，未声称通过 |

构建入口约 287.21kB，UI 约 1025.92kB、charts 约 539.68kB，后两者仍触发既有 500kB 警告。CSS 产物仍为阶段 1A 的 `index-BinN7maX.css`，布局代码和样式没有改动。

## 5. 回归问题与处理过程

1. 先在原 `api.ts` 上运行请求契约测试，确认新增检查描述既有行为，再迁移实现；现有调用方通过兼容入口继续使用同一方法。
2. 首轮同时运行完整单测、构建和 E2E 时，`AgentResults.test.tsx` 的“准备入口不自动提交，确认后保留当前窗口、截止时间和失败重试”超过 5 秒限制（约 6.26 秒）。该用例及其组件未修改；未放宽超时或断言。构建与 E2E 结束后，原样独立执行 `npm test`，52 项全部通过，该用例约 1.94 秒完成；超时未在独立重跑中复现。
3. 沿用阶段 1A 已验证的沙箱外执行方式启动本机隔离服务，避免再次阻塞在已记录的 Windows 沙箱 API 启动问题。没有改 Python 或服务配置。
4. 尺寸对比发现三个导入弹窗状态的背景页面高度不同。查看 1A/1B 截图与原脚本后，确认弹窗背后的损坏 gzip 重试任务从排队进入了预期失败状态，增加错误说明和操作按钮；弹窗尺寸、内容布局、边距及横向边界一致。未修改业务流程或放宽布局断言。

## 6. 浏览器、视觉检查与证据

本轮证据目录：[frontend-XoPjZn](../output/playwright/frontend-XoPjZn/)，包括[流程验收结果](../output/playwright/frontend-XoPjZn/acceptance.json)、[布局测量](../output/playwright/frontend-XoPjZn/layout-measurements.json)和[与阶段 1A 的尺寸对比](../output/playwright/frontend-XoPjZn/layout-comparison.json)。使用已有 E2E 启动 Vite、真实 FastAPI/Worker、新建 SQLite 测试库和本机模拟模型；没有访问业务库或付费模型。结束后临时服务已退出，18765/15173 端口无监听。

原流程覆盖导入（CSV/JSONL/GZ）、上传进度、丢响应后的幂等重试、标注、固定分析、任务失败/重试/删除、JSON 导出、刷新与断网恢复，以及 Agent 工具、原文证据和刷新不重复调用模型。分页列表、历史失败与空 options 保留原有响应替身，其他核心流程走实际隔离 API。

页面检查沿用阶段 1A 的 8 种状态和 1440/1024/768/390px 四种宽度。32 个状态均通过页面横向溢出、导航文字裁切、可见弹窗横向边界及页脚对齐断言；固定分析图表和表格正常渲染。与 1A 对比的 29 个状态全部尺寸字段一致，另 3 个状态仅有上述动态背景高度差异。

已目视检查首页 1440/768px、导入弹窗 1440/1024/390px、固定结果 1024px 和 Agent 结果 768px，未发现明显视觉回归。未宣称逐像素相同；任务时间、ID 和后台状态会变化。

- [1440px 首页](../output/playwright/frontend-XoPjZn/layout-workbench-1440.png)
- [768px 首页](../output/playwright/frontend-XoPjZn/layout-workbench-768.png)
- [1024px 固定分析结果](../output/playwright/frontend-XoPjZn/layout-analysis-result-1024.png)
- [768px Agent 结果](../output/playwright/frontend-XoPjZn/layout-agent-result-768.png)

## 7. 尚未验证及遗留事项

- 真实业务数据库、PostgreSQL/Redis 标注队列、线上供应商额度和模型质量未验证；隔离模拟模型通过不代表真实模型效果。
- 独立人工复核 `/api` 服务未做浏览器 E2E；本次只读确认协议，未改变原客户端和 CSRF 流程。
- 生产 preview/部署、Firefox/Safari/Edge、后端全量回归、真实多页历史列表未验证。
- 既有 chunk 体积警告保留。窄屏宽表继续在表格内部滚动，导入高级设置仍可能需要弹窗内纵向滚动。

本阶段完成后停止，不进入 datasets/annotations、页面路由或其他后续阶段。
