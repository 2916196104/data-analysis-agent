# P7-C-S2-F01 验收闸门候选

此目录只包含验收运行器及其离线联测，不属于生产 RAG 实现。`p7c_real_guard.py` 来自原 S2 证据，只有 `check_scope` 和固定标注身份常量发生逻辑变化；`s1_guard.py`、`s2_guard.py` 与原 S2 版本相同。历史运行目录没有被替换。

原生 API 请求不含 `timezone`。新检查调用生产 `review_scope` 派生 UTC，并继续严格比较商品、来源、数据集、日期、基线、as_of 和三个标注身份字段。可选 timezone 元数据若已存在也必须为 UTC。

离线复现（PowerShell，从仓库根目录运行，先选择一个全新的结果目录）：

```powershell
$env:F01_OUTPUT_DIR='C:\Users\a7989\Documents\Codex\2026-11-13\mei\work\f01-reproduce'
New-Item -ItemType Directory -Path $env:F01_OUTPUT_DIR -ErrorAction Stop
$env:F01_CAPTURE_POST_PATH=Join-Path $env:F01_OUTPUT_DIR 'react-serialized-post.json'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:NODE_OPTIONS='--import='+([System.Uri](Join-Path (Get-Location) 'scripts\p7c_s2_guard\no_network.mjs')).AbsoluteUri
Push-Location frontend
node node_modules/vitest/vitest.mjs run src/features/agent/p7c-s2-guard-contract.test.tsx --maxWorkers=2
if($LASTEXITCODE -ne 0){throw 'React contract test failed'}
Pop-Location
.venv\Scripts\python.exe scripts/p7c_s2_guard/check_contract.py
if($LASTEXITCODE -ne 0){throw 'Native contract test failed'}
```

前端联测使用原生 `useAgentChat → api.submit → request → JSON.stringify`，仅在 fetch 端捕获 JSON，不使用浏览器。Python 再将捕获体交给真实 ASGI 路由、`TaskInput`、`prepare_parameters` 和候选闸门。唯一被创建的任务记录位于临时合成 SQLite，保持 queued，不启动 Worker。其余编码、RPC 和 SQL 边界测试使用合成适配器，不连接真实数据库或模型。

原生 API 的只读商品目录检查先于 enqueue。本联测的 `business_db=None` 不执行该回读，因此拒绝无副作用的证明覆盖任务创建、Worker、模型、Top-5、付费和写入，不代表所有只读元数据连接都在闸门之后。

下一次真实 S2 必须另获授权，创建新的目录、任务库、claim 和业务额度，再将这三个 guard 文件的已审查版本复制到新运行目录。不能复用历史 claim，也不能只在导入旧 guard 后追加本目录到路径。执行前应核对实际导入的模块路径及 SHA-256，重做真实启动前的只读权限/网络/额度检查。本目录没有服务启动命令，不自动发起真实验收。
