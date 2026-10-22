# 本机 PostgreSQL 配置与运行记录

2026-10-22 已完成实际 SQLite 数据迁移、前后端启动及浏览器验收。当前可直接访问 [评论分析工作台](http://127.0.0.1:5173)，后端健康检查为 [API health](http://127.0.0.1:8000/api/v1/health)。

## 数据库和密码在哪里

| 配置 | 当前值 |
| --- | --- |
| PostgreSQL 服务 | 已安装的 `postgresql-x64-18`，服务端版本 18.6 |
| 地址 | `127.0.0.1:5432` |
| 数据库 | `reviews`，UTF-8 |
| 应用账号 | `review_agent`，无超级用户、创建数据库或创建角色权限 |
| 应用连接配置 | 项目根目录 `.env` 的 `DATABASE_URL` |
| 任务存储 | 与评论业务表共用 PostgreSQL 数据库 |

应用账号密码已于 2026-10-22 改为你自行填写的新密码，数据库与 `.env` 的 `DATABASE_URL` 已同步，并通过新连接及后端重启验证。URL 中的特殊字符已自动编码；DataGrip 的 Password 字段填写你设置的原始密码。临时 `POSTGRES_NEW_PASSWORD` 配置项已经移除。

安装时的 postgres 管理员密码只用于创建专用角色和数据库；临时 `POSTGRES_ADMIN_PASSWORD` 配置项已经移除，管理员账号本身的密码没有修改。保留原模型配置，前端配置中没有数据库凭据。

`.env` 被 Git 忽略。不要将其内容放入聊天、截图或提交到仓库。若以后修改应用账号密码，同步更新 `DATABASE_URL` 并重启后端；修改 `.env` 本身不会改变数据库服务器上的密码。

## 已迁移的数据与校验

| 内容 | 迁移数量 |
| --- | ---: |
| 商品 | 4 |
| 评论 | 4,941 |
| 导入历史 | 14 |
| 模型标注尝试 | 576 |
| 模型观点 | 1,103 |
| 分析快照 | 7 |
| 已完成任务历史 | 6 |

人工复核、告警和对应历史表原本为空，已创建并保留为空。原 worker 心跳不复制，由新 worker 建立。

全部复制表已核对条数与字段值 SHA-256；质量摘要、标注进度、商品与模型选项、任务详情及历史分析快照复核一致。实际数据包含两个模型版本，已统一模型列表排序，避免 SQLite 和本机 PostgreSQL 排序规则造成显示顺序不同。

原 SQLite 文件未修改，且分别通过 integrity/foreign-key 检查。备份目录为：

```text
E:\AllProject\data-analysis-agent\data\private\backups\20261022-123805-040632
```

目录中包含 `reviews.sqlite3` 和 `reviews.tasks.sqlite3`，由 SQLite backup API 创建。原库和备份保留用于回退；切换后的新任务、新分析只写入 PostgreSQL，不会同步回 SQLite。

本机验收记录（均被 Git 忽略）：

- `.cache/sqlite-backup-manifest.json`：备份与源表数量。
- `.cache/postgresql-migration.json`：迁移逐表校验和历史对比结果。
- `.cache/postgresql-live-validation.json`：实际服务与新任务验收结果。
- `output/playwright/postgresql-live-20261022.png`：实际页面截图。

## 启动和停止项目

当前 API/worker 和 Vite 前端已经在后台运行。进程信息保存在 `.cache/local-services.json`；启动日志位于 `.cache/local-api.stdout.log`、`.cache/local-api.stderr.log`、`.cache/local-web.stdout.log` 和 `.cache/local-web.stderr.log`。

如果要从 IDEA 或终端重新启动，先确认没有正在执行的任务，再停止这次启动的后台进程，避免重复 worker 或端口冲突。在项目根目录执行：

```powershell
$localServices = Get-Content -LiteralPath '.cache/local-services.json' -Raw | ConvertFrom-Json
Stop-Process -Id $localServices.api_listener_pid, $localServices.frontend_listener_pid
```

这条命令用于本次运行期间。电脑重启或服务已经退出后，不要依据旧的进程号停止其他程序。

之后可在 IDEA 选择 **71 启动HTTP API与后台任务** 或 **77 PostgreSQL启动HTTP与后台任务**，二者使用相同 PostgreSQL 配置，选择一个启动即可。**03 查看评论库摘要** 或 **78 PostgreSQL查看评论摘要** 用于只读检查。所有评论业务运行配置及本机临时评论配置均已切换到 `env:DATABASE_URL`，工作目录统一为项目根目录；不再保留指向旧 SQLite 业务库的 IDEA 配置。具体配置用途见 [IDEA 运行说明](idea-running.md)。

也可以在项目根目录用终端启动后端：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.server serve --db env:DATABASE_URL --port 8000 --import-root data/private --env-file .env --allow-origin http://127.0.0.1:5173
```

另开终端启动前端：

```powershell
Set-Location E:\AllProject\data-analysis-agent\frontend
npm run dev
```

无需重复执行 `init` 或迁移命令。当前仍保留本机访问限制和单 worker；本次没有启用公网访问或开机自动启动项目。

## 实际运行验收

- 浏览器能显示 4 款商品、4,941 条评论，读取评论原文并恢复迁移前历史分析。
- 浏览器新提交的分析任务 `2c26aa542ce147eb856de52813dc46af` 已成功完成，worker 和进度持久化正常。
- 当前分析窗口 `2013-08-08` 至 `2022-10-15`，共 4,638 条评论，其中 504 条成功标注、225 条负面评论；统计结果与原 SQLite 的同范围只读统计完全一致。
- 已保存分析快照 `3519ca7a44834ed8a5760b4bbfc3c6ad`。这次验证不调用模型，标注尝试总数仍为 576。
- 验收后 PostgreSQL 任务共 7 条成功，无排队、运行、失败或部分完成任务；新增 1 条为本次启动验证任务。
- 模型列表排序修正后，32 项标注/查询范围回归测试通过，记录在 `.cache/postgresql-profile-order-tests.log`。

完整数据库配置、通用迁移和回退规则见 [PostgreSQL 配置与迁移](postgresql.md)。
