# 配置 PostgreSQL 并迁移现有评论数据

评论导入、模型标注、人工复核、统计、监控、HTTP API 和后台任务均支持 PostgreSQL。SQLite 入口继续可用；销售 CSV / Excel 分析入口保持原样。

本机已于 2026-10-22 完成实际数据迁移并启动，当前连接位置、备份、验收结果和启动/停止命令见 [本机运行记录](local-postgresql-running.md)。下面步骤供新环境使用，不要对已迁移的目标重复执行初始化或迁移。

**先停止所有连接旧库的 API、worker、工作台和 CLI，再备份和迁移。已有数据迁移与新库初始化是两个独立流程：迁移前不要运行目标的 `init`。本文不自动改动你的业务库或 `.env`。**

## 1. 准备专用数据库

本机已安装 PostgreSQL 18，以下示例使用其工具路径。先确认 PostgreSQL 服务已启动；服务器地址、端口、管理员账号按实际安装修改。也可使用远程 PostgreSQL，客户端 Python 驱动不要求本机安装 PostgreSQL 服务端。

在项目根目录安装依赖：

```powershell
Set-Location E:\AllProject\data-analysis-agent
.\.venv\Scripts\python.exe -m pip install -e .
```

连接管理员数据库，输入安装时设置的管理员密码：

```powershell
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -h 127.0.0.1 -p 5432 -U postgres -d postgres
```

在 psql 内执行下面的命令。`\password` 会交互设置密码，不把密码写进 SQL 文件；数据库或角色已存在时不要重复创建，应选择新的专用数据库名。

```sql
CREATE ROLE review_agent LOGIN;
\password review_agent
CREATE DATABASE reviews OWNER review_agent TEMPLATE template0 ENCODING 'UTF8';
\q
```

应用账号需要连接专用数据库、在 `public` schema 建表及读写项目表的权限；使用该库的 owner 即可，无需给应用账号超级用户权限。当前支持单个 UTF-8 数据库的 `public` schema，不支持自定义 schema 路由。

## 2. 配置后端连接地址

在项目根目录已有的 `.env` 末尾增加下面一行，保留原来的模型配置。将 `URL_ENCODED_PASSWORD` 替换为刚设置的密码；密码内的 `@`、`/`、`#`、`%`、`?` 等字符必须先进行 URL 百分号编码。

```dotenv
DATABASE_URL=postgresql://review_agent:URL_ENCODED_PASSWORD@127.0.0.1:5432/reviews
```

远程服务器可按服务要求加 `?sslmode=verify-full&sslrootcert=证书路径`，证书路径等 URL 参数同样须正确编码。已有模型配置仍按原方式使用。

所有评论 CLI 的 `--db` 都支持：

| 参数 | 含义 |
| --- | --- |
| `--db data/private/reviews.sqlite3` | 原来的 SQLite 文件 |
| `--db env:DATABASE_URL` | 从项目根目录 `.env` 或进程环境变量读取 PostgreSQL URL；环境变量优先 |
| `--db postgresql://...` | 直接传连接地址；推荐使用上一行，避免密码出现在命令历史和进程参数中 |

`DATABASE_URL` 不会自动切换旧运行配置；需要显式使用 `--db env:DATABASE_URL`。从 IDEA 运行时将工作目录设置为项目根目录，修改对应运行配置的参数。URL 在启动时解析，修改凭据后需重启服务。

PostgreSQL 默认将任务表放在同一数据库，**无需额外配置 `--task-db`**。也可显式用 `--task-db env:TASK_DATABASE_URL` 指定另一个已初始化的 PostgreSQL 库，或传 SQLite 文件。任务库绑定的业务库标识不包含密码，轮换账号密码不需要迁移任务；连接地址的主机名和端口应保持一致。

## 3. 迁移现有 SQLite 数据

### 停止写入并备份

先等待任务结束，再停止所有业务进程。在原数据库上查看剩余任务：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.server tasks --db data/private/reviews.sqlite3 --status queued
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.server tasks --db data/private/reviews.sqlite3 --status running
```

如果原任务库未初始化，可跳过任务查询及任务历史迁移。若需迁移历史，两种状态都必须为空。异常退出留下的 `running` 任务，可以先用旧 SQLite 启动方式恢复 worker，让它按现有规则记录中断，再检查队列并停止；不要同时启动两套业务服务。

使用 SQLite backup API 生成独立备份，兼容源库存在 WAL 的情况。以下脚本只读取源库；备份存入新的时间戳目录，目录已存在则直接失败。

```powershell
@'
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3

root = Path('data/private').resolve()
backup = root / 'backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
backup.mkdir(parents=True)
for name in ('reviews.sqlite3', 'reviews.tasks.sqlite3'):
    source = root / name
    if not source.is_file():
        if name == 'reviews.sqlite3':
            raise FileNotFoundError(source)
        continue
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(backup / name)) as dst:
            src.backup(dst)
print(backup)
'@ | .\.venv\Scripts\python.exe -
```

### 执行一次迁移

确认第 1 步创建的 PostgreSQL 目标尚无项目表。迁移命令会自动建全套业务表和任务表；源库尚未启用的标注、人工复核或监控模块会在目标中创建为空表。

迁移业务数据及已结束的任务历史：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.migrate --source data/private/reviews.sqlite3 --source-tasks data/private/reviews.tasks.sqlite3 --target env:DATABASE_URL
```

只迁移业务数据、使用新的空任务历史：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.migrate --source data/private/reviews.sqlite3 --target env:DATABASE_URL
```

迁移行为：

- 原 SQLite 文件以只读方式打开，不删除、升级或替换源数据。
- 保留商品、评论、导入错误原始字节、模型尝试、观点、人工复核历史、分析快照、告警及处理记录；保留原 ID、时间字符串和 JSON 字符串。
- 可选迁移任务及父子任务关系；worker 心跳不复制，目标启动时重新建立。任务历史迁移与业务迁移在同一 PostgreSQL 数据库事务中提交。
- 每批最多复制 500 行，逐表对主键顺序下的字段值计算 SHA-256，并核对条数。输出 `verified_tables` 列出条数和摘要；核验后修复自增序列，后续写入继续分配新 ID。
- 源库外键、版本或字段不匹配，存在未知业务表，目标已有项目表，或者复制/校验出错时，停止并回滚整个目标事务。没有清空、合并、覆盖或跳过错误记录的选项。
- PostgreSQL `TEXT` 不允许 `U+0000` 空字符；遇到这样的源记录会明确拒绝并回滚，请先核查业务数据，工具不会静默删字。PostgreSQL 正常导入也受该限制。
- 迁移不调用模型，也不执行任何历史任务。原文件路径作为证据保留；若原 JSONL 文件已经搬家，历史原文仍可从库内读取，但旧导入任务参数不会自动改路径。

成功返回 `status=ok` 和退出码 0。失败退出码 1。失败后排查原因，再对没有项目表的目标重试；成功后重复迁移会被拒绝。不要删除旧库，至少保留到核对完成。

### 核对迁移结果

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.reviews summary --db env:DATABASE_URL
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.labeling status --db env:DATABASE_URL
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.server tasks --db env:DATABASE_URL
```

将质量摘要和标注统计与同样命令使用 `--db data/private/reviews.sqlite3` 的输出比较；任务历史迁移后也应能按原任务 ID 查询。`verified_tables` 已逐字段校验所有复制数据，质量摘要用于人工复查。

## 4. 启动后端和前端

迁移成功后直接启动，不需要再次初始化：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.server serve --db env:DATABASE_URL --port 8000 --import-root data/private --env-file .env --allow-origin http://127.0.0.1:5173
```

另开终端，前端继续使用现有 API 配置：

```powershell
Set-Location E:\AllProject\data-analysis-agent\frontend
npm run dev
```

浏览器打开 `http://127.0.0.1:5173`。后端健康检查地址为 `http://127.0.0.1:8000/api/v1/health`。前端的 `VITE_API_BASE_URL` 仍是 `http://127.0.0.1:8000/api/v1`，不要把数据库 URL 或密码放进 `VITE_*`。

独立 worker 模式分别运行：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.server worker --db env:DATABASE_URL --import-root data/private --env-file .env
```

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.server serve --db env:DATABASE_URL --no-worker --port 8000 --allow-origin http://127.0.0.1:5173
```

仍然是每个业务库一个串行 worker。PostgreSQL 使用会话 advisory lock 防止不同进程或主机重复启动 worker；需要直连或会话池连接，不能通过事务池模式复用该连接。导入/复核等原有读后写事务使用事务锁串行化，保留现有冲突与幂等规则。读取事务使用可重复读快照。数据库改造不会自动启用多 worker 或解除 HTTP 本机 Host 限制。

## 没有旧数据时初始化新库

仅在不需要迁移旧数据时运行：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.server init --db env:DATABASE_URL
```

该命令只建项目表，不负责创建 PostgreSQL 服务器、角色或数据库。然后按第 4 步启动，并用原来的 JSONL 导入入口导入真实数据。

## 回退与故障处理

迁移失败时源文件保留，可继续用原 SQLite 启动命令。切换成功后如需回退，先停止 PostgreSQL 后端和 worker，再用原库启动：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.server serve --db data/private/reviews.sqlite3 --port 8000 --allow-origin http://127.0.0.1:5173
```

切换到 PostgreSQL 后产生的新数据不会自动同步回旧 SQLite。不要同时对两套库开展业务写入；需要保留切换后的新增内容时，应先规划单独的数据回迁。

| 错误 | 处理 |
| --- | --- |
| PostgreSQL 连接失败 | 检查服务、主机、端口、密码编码、TLS 和访问规则；错误响应不回显凭据 |
| PostgreSQL 表未初始化 | 有旧数据先迁移；无旧数据执行 `review_api_cli init` |
| 目标已有项目表 | 使用新的未初始化专用数据库，禁止覆盖已有业务表 |
| 源任务库仍有 queued/running | 先完成任务并停止服务，或省略 `--source-tasks` |
| worker 已存在 | 保留一个 worker；额外 API 使用 `--no-worker` |
| 任务库绑定不匹配 | 检查 `--task-db` 和业务地址是否对应；主机别名应统一 |

## 验证改动

SQLite 回归测试不需要 PostgreSQL：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

PostgreSQL 集成测试需要一个专用测试管理员连接，其账号必须能创建和删除数据库。设置 `TEST_POSTGRES_ADMIN_URL` 后运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_postgres.py -v
```

测试只创建和清理自己随机命名的 `agent_test_*` 数据库，不初始化传入 URL 指向的数据库。未设置变量时自动跳过集成项。覆盖迁移前后统计/证据一致、原 ID 和后续自增、人工复核与告警历史、错误回滚、并发冲突、任务历史和真实 worker、HTTP 与 CLI。

2026-10-22 本机验收：在 PostgreSQL 18 隔离测试实例、Psycopg 3.3.6 上运行完整 Python 测试，138 项全部通过；其中新增配置与 PostgreSQL 专项测试共 7 项。`pip check` 通过。测试只使用模拟数据，没有迁移现有业务库；记录保存在本机 `.cache/all-database-tests.log`，该目录不纳入 Git。临时 PostgreSQL 实例验收后停止。

实现依据：[Psycopg 事务管理](https://www.psycopg.org/psycopg3/docs/basic/transactions.html)。当前仍沿用 TEXT 中的原始 JSON，以保留既有存储契约和迁移内容；没有引入向量检索或改用 JSONB。
