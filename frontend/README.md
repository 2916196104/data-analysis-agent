# 观数 · 评论分析工作台

后端现支持 SQLite / PostgreSQL，前端接口和配置保持一致。PostgreSQL 配置及旧数据迁移见 [数据库接入说明](../docs/postgresql.md)；后端启动示例中的 `--db data/private/reviews.sqlite3` 可改为 `--db env:DATABASE_URL`。数据库凭据只配置在后端。

独立的 React + TypeScript + Vite + Ant Design + ECharts 前端，接入现有 `/api/v1` 评论服务，界面为简体中文。无需修改 Python 业务代码。

目前 HTTP 服务支持结构化评论统计。问题输入是**本机任务备注**，不会交给模型理解。数据导入使用**服务端文件相对路径**；下载是浏览器对后端真实 JSON 的导出。没有伪造上传、聊天、进度或服务器文件下载接口。

## 启动

需要 Node.js 22.12+，以及已安装项目依赖、已显式初始化的后端数据库。后端安装与初始化见 [HTTP API 说明](../docs/review-http-api.md)。不要用验收脚本初始化业务库。

从项目根目录启动后端（现有业务库已经初始化时）：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.server serve --db data/private/reviews.sqlite3 --port 8000 --import-root data/private --env-file .env --allow-origin http://127.0.0.1:5173
```

另开终端启动前端：

```powershell
cd frontend
npm ci
Copy-Item .env.example .env.local
npm run dev
```

打开 `http://127.0.0.1:5173`。已有 `.env.local` 时直接编辑，勿覆盖自己的配置。前端不需要 Python，也可以将 `frontend/` 作为独立项目安装和构建。

## 环境配置与构建

| 配置 | 作用 |
| --- | --- |
| `VITE_API_BASE_URL` | API 基址，含 `/api/v1`。示例 `http://127.0.0.1:8000/api/v1`；未配置时为同源 `/api/v1`，需由部署环境反向代理 |
| 后端 `--allow-origin` | 必须与浏览器实际前端 Origin 完全一致；`localhost` 和 `127.0.0.1` 不同，可重复传参 |

`VITE_*` 会进入浏览器代码，只放公开 API 地址。模型密钥继续留在后端 `.env`；前端仅使用 `/options` 返回的模型身份选择既有标注版本。

```powershell
cd frontend
npm run build
npm run preview
```

构建产物在 `frontend/dist/`，可独立托管。预览端口是 4173，后端需额外设置 `--allow-origin http://127.0.0.1:4173`。Vite 环境变量在构建时注入，改地址后需重新构建。后端仍限制本机 Host，因此此版本面向本机工作台，公网部署还需要后端提供相应访问能力。

## 完成一次分析

导入后可直接在页面完成模型标注：

1. 导入成功后点击“标注已导入的数据”，或随时通过左栏“模型标注”打开表单。
2. 选择数据集和商品（留空表示该数据集全部商品）。每批标注数量可设为 1–1000，默认 100；并发数可设为 1–4，默认 2。数量是本次任务所有商品合计的上限，不会自动循环。
3. 按需勾选“同时重试此前标注失败的评论”，点击“提交标注任务”。使用后端 `.env` 配置的模型，不受分析日期窗口限制；范围可能包含以前导入的未标注评论，同一版本下的成功记录会跳过。
4. 页面自动显示已处理/实际选中总量、成功、失败、模型调用数。排队与等待首条响应时会提示状态；刷新页面可继续查看。完成后可“继续标注下一批”，也可“前往分析”，后者沿用所选商品及本次标注版本。

模型密钥仍由后端管理；配置缺失、请求失败或执行器离线会显示提示。分析与标注是各自独立的任务。

1. 选择数据集来源/版本、商品和已有标注版本。空商品选择表示该数据集全部商品；不同来源和版本不会混在一起。日期默认取评论库最新月份与上一个月，可自行调整。
2. 在“这次想了解什么”填写便于识别的备注。执行内容固定为评论趋势、商品对比、六维度负面问题及基线规则分析。
3. 点击“开始分析”。任务以幂等键提交，轮询后端状态；未知总量只显示阶段，执行器离线会明确提示。
4. 阅读实际统计结论、趋势图、表格和冻结原文证据。数据不足、未标注和空分母分别显示，不会把空分母当成 0%。
5. 点击“下载 JSON”导出完整分析快照和本机备注；右侧详情也可单独导出。

“从服务端目录导入”支持后端 import-root 下的 Amazon 格式评论或元数据 JSONL / JSONL.gz 路径。后端限定评论时间戳为 1996—2023 年、单位为毫秒。提交后显示真实导入任务状态，完成时刷新数据集。没有浏览器字节上传接口，因此不展示拖拽上传区或上传百分比。

“本次最多新增评论数”可填写正整数（例如 1000），留空不限。重复记录不占额度；达到数量后停止解析评论，后续记录不在本次检查范围内，商品元数据仍按原规则完整处理。再次提交会跳过已有评论继续新增，已有全部评论时不会重复增加数据。仅导入元数据时请留空此项；后端文件指纹校验仍会读取完整文件。

左栏展示后端持久化任务历史，可分页恢复任务和结果；刷新页面会恢复上次选择。问题备注按 API 地址和任务 ID 保存在浏览器 localStorage，不同步到其他设备。清理浏览器存储会丢失备注，但后端任务与分析快照仍在。

## 接口与前端功能

| 接口（均位于 `/api/v1`） | 前端用途 |
| --- | --- |
| `GET /health` | 服务及 worker 心跳状态 |
| `GET /options`、`GET /quality` | 数据集/商品/模型版本选择、评论范围与数量；来源和版本组成前端数据集分组 |
| `GET /reviews`、`GET /reviews/{id}` | 当前数据预览、原文与字段详情；历史证据使用已保存的 run.inputs |
| `POST /tasks` | 提交 `analysis`、`import` 或 `label` 及其契约参数，带 `Idempotency-Key` |
| `GET /tasks`、`GET /tasks/{id}` | 历史分页、真实状态/阶段、结果引用和错误 |
| `POST /tasks/{id}/retry` | 仅 failed/partial 可重试，产生关联的新任务 |
| `GET /analysis-runs/{id}` | 冻结范围、统计、趋势、对比、问题、证据，所有结果卡片的唯一数据来源 |

`analysis.parameters` 提交 `model/base_url/prompt_version/source/dataset_version/product_ids/date_from/date_to/baseline_from/baseline_to`。基线结束必须早于当前窗口开始。规则使用后端默认值；分析可能保存规则告警，并不调用模型。`import.parameters` 为 `reviews_path/meta_path/dataset_version/product_ids/max_reviews`，至少一个文件路径；`max_reviews` 可选，要求评论路径且为正整数。

`label.parameters` 提交 `source/dataset_version/product_ids/limit/workers/retry_failed`。模型身份由后端冻结，前端不提交密钥或模型配置。

成功包络为 `{status:"ok", result: ...}`。错误为 `{status:"error", error:{code,message,details?}}`，界面显示业务及字段错误；统一 API 层处理 JSON、HTTP、超时和网络异常。请求超时 30 秒。任务每 1.5 秒串行轮询，网络失败后每 5 秒重连，保持最后已知状态；终态停止轮询。切换任务或退出页面会中止请求及计时器。

当前无用户账号或 Token；沿用后端本机 Host 和 Origin 校验，不新建浏览器认证方案。

## 验证

```powershell
cd frontend
npm test
npm run build
npm run test:e2e
```

`npm test` 验证真实进度计算、状态迁移、迟到响应隔离、网络恢复、轮询停止和统一接口错误/幂等处理。

`npm run test:e2e` 使用已安装 Chrome（可通过 `E2E_BROWSER_CHANNEL=msedge` 切换 Edge）。脚本使用项目 `.venv` 和本项目 Python 包，自动创建全新模拟数据目录，启动真实 API、worker、Vite 和无头浏览器。无需模型密钥，也不请求外部模型。它验证选择商品、原文预览、提交备注和分析、实际队列与结果、JSON 下载内容一致性、刷新恢复、断网重连、损坏 gzip 导致的真实任务失败、显式重试、正常文件导入和窄屏侧栏。

默认验收端口为 18765/15173，已被占用时直接失败，不结束其他服务。可用 `E2E_API_PORT`、`E2E_WEB_PORT`、`E2E_PYTHON` 覆盖。运行结束关闭自己启动的服务。截图、日志、下载文件、临时数据库和 `acceptance.json` 保留在被 Git 忽略的 `output/playwright/frontend-*/` 下。

标注验收使用本机模拟模型接口，经过真实 SDK、任务执行器和数据库保存流程。脚本直接用 SQL 核对新增评论、成功标注、观点原文及重复导入后的记录不变，证据保存在 `database-evidence.json`。默认使用新建的隔离 SQLite 库；也可将 `E2E_DATABASE_URL` 指向预先创建的空 PostgreSQL 测试库（库名必须以 `agent_test_` 开头，禁止指向业务库），由调用方管理测试库清理。

### 2026-10-22 模型标注入口验收

- 构建和 12 项前端单元测试通过；后端导入及标注共 42 项回归测试通过。
- SQLite、独立 PostgreSQL 18 测试实例各通过 23 项浏览器检查，浏览器运行错误为 0。
- 输入 3 条测试评论、设置导入上限 2：数据库评论总数由 96 增至 98，新增 2 个唯一评论 ID，正文与输入一致。
- 页面设置每批 2 条、并发 2：实际观察到 2 个同时执行的模型请求，数据库保存 2 条成功标注及 2 条观点。
- 再次标注已完成评论，模型调用未增加；重复导入相同 2 条评论，新增 0、重复 2，既有评论及标注保持不变。
- 刷新恢复进度、继续下一批、从标注转到分析时保留商品及标注版本均通过。使用隔离数据与本机模拟模型，未调用真实模型服务。

### 2026-10-21 本机验收

- `npm run build` 通过，TypeScript 编译通过。Ant Design / ECharts 依赖包仍有超过 500 kB 的构建体积提示，图表按需加载。
- `npm test`：3 个测试文件、12 项测试全部通过。
- `npm run test:e2e`：13 项浏览器流程检查通过，浏览器运行错误为 0。
- 实际选取模拟商品 DEMO-A：当前窗口 24 条评论、12 条负面评论、占比 50%，包装维度较基线上升 41.666667 个百分点，触发 1 项规则。下载的 JSON 与后端快照逐项一致。
- 损坏 gzip 经真实 worker 执行后进入 failed，显示 `file_error`；显式重试生成新任务并保留父任务 ID。有效文件导入使隔离库评论数从 96 增至 97。
- 桌面 1440 px 与窄屏 390 px 均已截图检查；窄屏侧栏可收起，页面无横向溢出。

本机证据目录：`output/playwright/frontend-p54xQm/`，含 `acceptance.json`、桌面/结果/失败/窄屏截图及下载文件。该目录被 Git 忽略，重新执行验收会创建自己的目录。本次使用真实 API 与 worker、隔离的人工模拟数据及预设标注；没有调用线上模型或改动业务数据库，也没有重跑整套历史 Python 测试。

## 后端边界

- 无会话 CRUD 或自然语言 HTTP 问答；左栏是任务历史，问题是本机备注。
- 无浏览器文件上传；仅支持已有的服务端路径导入。
- 无取消任务；没有取消按钮。失败/部分完成才显示显式重试。
- 无生成文件清单或下载接口；JSON 为浏览器导出，图表由后端统计数据渲染。
- CSV/Excel 销售分析仅有 CLI；当前工作台仅接评论 HTTP API。
- 前端分析需要已有标注版本；无版本时提示使用现有后端标注入口。本次不增加标注配置、人工复核或告警管理页面。

主要文件：`src/api.ts` 统一请求；`src/useTask.ts` 与 `src/task-state.ts` 管理轮询；`src/Composer.tsx` 管理分析输入；`src/ResultCards.tsx` 展示快照；`src/DataPreview.tsx` 展示当前原文；`src/App.tsx` 组合工作台。
