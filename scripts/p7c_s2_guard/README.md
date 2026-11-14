# P7-C-S2-R1A-F02 验收运行器候选

此目录仅包含验收运行器，不属于生产 RAG 实现。F02 修改了工具观测、持久额度、实际Popen前闸门、固定SQL许可集、任务/检索范围绑定、网络钩子及候选导入入口；新增有界浏览器观察器。F01、S2、R1-A原件均保留在历史输出目录，不以当前候选替换。

原生 API 请求不含 `timezone`。新检查调用生产 `review_scope` 派生 UTC，并继续严格比较商品、来源、数据集、日期、基线、as_of 和三个标注身份字段。可选 timezone 元数据若已存在也必须为 UTC。

当前真实入口保持 **BLOCKED**：Python网络审计不能证明原生DLL/子进程树的操作系统网络隔离。`f02_entry.py` 在安装真实服务前拒绝此状态，没有强制通过开关。浏览器Mock取消/清理也不等于实际Chrome/进程树清理验收。不得直接用候选执行R1-A/R1-B。

离线复现（PowerShell，从仓库根目录运行，使用全新结果目录）：

```powershell
$env:F01_OUTPUT_DIR=Join-Path (Get-Location) ('outputs\mvp-real-acceptance\f02-offline-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $env:F01_OUTPUT_DIR -ErrorAction Stop | Out-Null
$env:F01_CAPTURE_POST_PATH=Join-Path $env:F01_OUTPUT_DIR 'react-serialized-post.json'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:NODE_OPTIONS='--import='+([System.Uri](Join-Path (Get-Location) 'scripts\p7c_s2_guard\no_network.mjs')).AbsoluteUri
Push-Location frontend
node node_modules/vitest/vitest.mjs run src/features/agent/p7c-s2-guard-contract.test.tsx --maxWorkers=2
if($LASTEXITCODE -ne 0){throw 'React contract test failed'}
Pop-Location
Remove-Item Env:NODE_OPTIONS
$env:F02_OUTPUT_DIR=Join-Path $env:F01_OUTPUT_DIR 'python'
$env:F02_CAPTURE_POST_PATH=$env:F01_CAPTURE_POST_PATH
.venv\Scripts\python.exe -B scripts/p7c_s2_guard/test_f02.py
if($LASTEXITCODE -ne 0){throw 'F02 offline guard test failed'}
$env:F02_BROWSER_OUT=Join-Path $env:F01_OUTPUT_DIR 'browser'
New-Item -ItemType Directory -Path (Join-Path $env:F02_BROWSER_OUT 'browser') -ErrorAction Stop | Out-Null
# 这是只读保存的修复前反例，不把它作为本次复验PASS。
Copy-Item -LiteralPath 'outputs\mvp-real-acceptance\p7c-s2-r1a-f02-20261113T142834Z\browser\historical-browser-baseline.json' -Destination (Join-Path $env:F02_BROWSER_OUT 'browser\historical-browser-baseline.json') -ErrorAction Stop
node --test scripts/p7c_s2_guard/browser_observer.test.mjs
```

前端联测使用原生 `useAgentChat → api.submit → request → JSON.stringify`，仅在fetch端捕获JSON，不使用Chrome。Python走原生ASGI路由、TaskInput、prepare_parameters和当前闸门，队列记录仅在临时合成SQLite内保持queued；原生Worker方法、模型Popen/RPC和SQL末端使用合成适配器。辅助Python进程仅验证SQLite跨进程竞争、崩溃与重启，不加载BGE。

浏览器测试需先有同一离线输出树下的 `browser/historical-browser-baseline.json`，其生成方法及本轮独立复现证据见F02报告；缺少时该基线测试会失败，不会使用旧PASS充数。浏览器候选仅导出观察器，不自动打开浏览器。

原生 API 的只读商品目录检查先于 enqueue。本联测的 `business_db=None` 不执行该回读，因此拒绝无副作用的证明覆盖任务创建、Worker、模型、Top-5、付费和写入，不代表所有只读元数据连接都在闸门之后。

`check_contract.py`保留为F01测试源，原37项中的内存计数重置/任意SYNTHETIC任务库等假设与F02收紧的契约不兼容；不要再按旧README直接运行它安装当前候选。当前F01 UTC/原生请求回归由test_f02.py及独立复核执行，不声称旧37项原样通过。

未来必须重新通过R1-A并另获R1-B授权。完整候选包括本目录的Python模块、SQL模板、子进程入口和浏览器观察器，不能只复制三个旧guard。采用新解释器、拒绝sys.modules旧缓存、核对完整模块路径/SHA/共享base及wrapper捕获对象。不得复用历史目录、授权、claim或任务库，也不能以本轮离线结果宣称真实业务通过。
