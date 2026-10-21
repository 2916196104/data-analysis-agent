# 保存分析、按月回放与处理规则告警

监控入口复用现有四个业务工具，保存一次分析的完整输入和结果，按商品、问题维度生成有原文证据的告警。继续使用 SQLite，无新增依赖，不调用模型。

**首次使用须显式初始化监控表。** 数据库应已导入评论并初始化观点表；人工复核表可选。`init` 增加独立监控表，不修改评论、模型观点、人工复核或原有 schema 版本；重复初始化安全。业务库初始化仍由你执行。

分析窗口和基线窗口都需要足够的成功标注。缺少样本或覆盖率不足会保存 `insufficient_data`（无法判断），不生成风险告警；监控命令不会补做标注。

## 初始化并选择模型配置

在项目根目录 PowerShell 执行。下例沿用已经存在的模型配置；有多个配置时，先查看列表并选择你要评估的一组。

```powershell
$monitorPython = (Resolve-Path '.\.venv\Scripts\python.exe').Path
$monitorDb = 'data/private/reviews.sqlite3'
& $monitorPython -m data_analysis_agent.review_monitor_cli init --db $monitorDb

$progress = & $monitorPython -m data_analysis_agent.review_label_cli status --db $monitorDb | ConvertFrom-Json
$progress.profiles | Select-Object requested_model, base_url, prompt_version
$profile = @($progress.profiles)[0]
if ($null -eq $profile) { throw '请先完成一批评论标注，并确认模型配置。' }
$monitorArgs = @(
    '--db', $monitorDb,
    '--model', $profile.requested_model,
    '--base-url', $profile.base_url,
    '--prompt-version', $profile.prompt_version,
    '--source', 'amazon_reviews_2023',
    '--dataset-version', 'amazon-reviews-2023',
    '--rule-name', 'beauty-monthly-v1'
)
```

`--product-id` 可重复，省略则包含指定来源和版本的全部商品。即使未限定来源或版本，告警仍按 `source＋dataset_version＋product_id` 单独计算，不混合不同数据集版本。安装项目后也可以使用 `review-monitor` 快捷命令；`python -m` 可直接运行。

## 按月回放并查看分析记录

```powershell
$replay = & $monitorPython -m data_analysis_agent.review_monitor_cli replay @monitorArgs --month-from 2016-01 --month-to 2016-03 --baseline-months 1 | ConvertFrom-Json
$replay.result.runs | Select-Object month, run_id, reused, new_alerts, merged_alerts

& $monitorPython -m data_analysis_agent.review_monitor_cli runs --db $monitorDb --page 1 --page-size 20
$runId = $replay.result.runs[0].run_id
& $monitorPython -m data_analysis_agent.review_monitor_cli run-detail --db $monitorDb --run-id $runId
```

每个当前窗口是一个完整 UTC 月，截止时间为该月最后一毫秒。例如 2016 年 2 月窗口为 2 月 1—29 日，默认基线为 1 月 1—31 日。`--baseline-months 3` 表示此前连续三个完整月；每个月的基线都会向前滚动。回放范围最多 1200 个月，基线长度为 1—120 个月。

每个月单独提交。某个月失败时返回 `status: partial`、`failed_month`、错误原因及已完成月份的 `run_id`，退出码为 1。修复问题后重新执行原命令：参数和范围内输入相同的月份返回 `reused: true`，不重复创建运行记录、命中记录或告警。

回放按时间升序执行。补跑早于已有告警的窗口时，不会把它合并到未来告警，也不会重排已经保存的历史归属。比较不同回放实验或不同窗口策略时使用不同的 `--rule-name`，分别观察结果。

## 分析一个自定义窗口

```powershell
& $monitorPython -m data_analysis_agent.review_monitor_cli run @monitorArgs --date-from 2016-03-01 --date-to 2016-03-31 --baseline-from 2016-02-01 --baseline-to 2016-02-29 --as-of 2016-03-31
```

起止日期包含当天，基线必须完全早于当前窗口。`--as-of` 默认当前窗口结束日，也支持带时区的 ISO 时间，所有工具、输入快照和证据使用同一截止时间。任意窗口允许长度不同，告警比较的是负面评论占比，数量差仅作为辅助信息，不自动换算日均数量。

回放中的截止时间限制**评论发布时间**。模型和人工观点采用执行分析时已有的有效结果，并在本次运行中冻结；它不是对“过去某时刻已完成哪些人工复核”的还原。随后补标、人工纠正或正文变化会产生新的运行记录，旧记录保持原样。

## 调整告警规则

```powershell
& $monitorPython -m data_analysis_agent.review_monitor_cli replay @monitorArgs --month-from 2016-01 --month-to 2016-03 --baseline-months 1 --min-samples 20 --min-coverage 0.8 --min-negative-count 3 --min-increase-pp 10 --cooldown-days 30
```

| 参数 | 默认值 | 判断方式 |
| --- | --- | --- |
| `--rule-name` | `negative-rate-growth` | 区分不同监控任务；同名且参数相同的任务共享冷却期 |
| `--min-samples` | 20 | 当前及基线分别至少有这么多条成功标注，1—1000000 |
| `--min-coverage` | 0.8 | 两个窗口的成功标注数 ÷ 有正文评论数均达到阈值，0—1 |
| `--min-negative-count` | 3 | 当前窗口该维度的去重负面评论数达到阈值，1—1000000 |
| `--min-increase-pp` | 10 | 当前负面占比减基线负面占比，至少增加这么多**百分点**，大于 0 且不超过 100 |
| `--cooldown-days` | 30 | 从一次告警首次命中时间起算的固定冷却期，0—3650 天 |

所有触发条件同时满足才产生告警。最低样本量以成功标注计；未标注、失败和空正文不被当作正面。占比的分母为该窗口成功标注的评论数，同一评论的同维度多个片段只计一次。规则判断使用原始计数，避免展示时四舍五入造成阈值误判。

每个商品、每个维度的评估有三种状态：

- `insufficient_data`：当前或基线样本量、覆盖率未达标。
- `normal`：数据足够，但未同时达到负面数量和占比增幅阈值。
- `triggered`：全部条件成立，生成或合并告警。

`checks` 保存每个条件是否满足，`reasons` 列出未通过条件。`summary.triggered/normal/insufficient_data` 是商品与维度组合的数量，不是评论数。当前和基线都没有可见数据的商品不生成维度评估，可通过比较结果中的 `no_reviews_product_ids` 识别；没有告警不等于数据证明商品正常。

冷却期使用分析截止时间，而非命令实际执行时间。同商品、来源、数据集版本、问题维度、模型配置、规则名称及全部阈值构成同一告警序列。在首次命中后的冷却期内，后续命中追加到同一个告警；不延长冷却期。恰好达到冷却期边界时可产生新的待核查告警。即使冷却期为 0，同一截止时刻的命中仍合并。

阈值或模型配置改变会形成独立序列。人工确认或忽略不删除历史，冷却期内追加命中保留人工状态；冷却期后再次触发会新建 `pending` 告警。后续统计下降不会自动关闭旧告警。

## 查询、确认与忽略告警

```powershell
$alerts = & $monitorPython -m data_analysis_agent.review_monitor_cli alerts --db $monitorDb --status pending --page-size 20 | ConvertFrom-Json
$alerts.result.items | Select-Object id, product_id, aspect, first_seen, last_seen, status, occurrence_count

if ($alerts.result.items.Count -gt 0) {
    $alertId = $alerts.result.items[0].id
    & $monitorPython -m data_analysis_agent.review_monitor_cli alert --db $monitorDb --alert-id $alertId
}
```

列表支持 `--status pending/confirmed/ignored`、`--product-id`、`--aspect`、`--source`、`--dataset-version` 和 `--run-id`。不指定状态时查询全部状态；页大小为 1—100，超出末页返回空列表。

查看详情后，根据核查结论执行下面一种操作；以下命令沿用上面选出的 `$alertId`：

```powershell
# 确认存在值得跟进的问题
& $monitorPython -m data_analysis_agent.review_monitor_cli confirm --db $monitorDb --alert-id $alertId --actor '本地复核员' --note '已核查原文，跟进包装问题'

# 或：记录忽略理由
& $monitorPython -m data_analysis_agent.review_monitor_cli ignore --db $monitorDb --alert-id $alertId --actor '本地复核员' --note '经复核暂不跟进'
```

两种操作都要求处理人，备注最多 2000 字符；确认与忽略之间可以纠正选择，每次实际状态变化均记录前后状态、处理人、备注和实际 UTC 时间。重复设置当前状态返回 `changed: false`，不重复写入处理历史；该操作不会用新备注覆盖旧备注。

`alert` 详情包括每次命中的 `run_id`、窗口、阈值、统计、原文和处理历史。每次命中附带最多 20 条负面评论样本，同时返回匹配评论总量、观点总量和分页信息。对应运行的 `inputs` 留存当前与基线全部入选评论及有效观点，完整依据不会因为样本分页而丢失。

## 保存了哪些内容

| 表 | 内容 |
| --- | --- |
| `review_analysis_runs` | 不可变运行 ID、参数与输入哈希、实际创建时间、回放截止时间、完整输入和结果 JSON |
| `review_alerts` | 商品和问题维度、首次及最近命中时间、当前处理状态 |
| `review_alert_occurrences` | 某次运行与告警的关联、是否新建告警、当时的统计条件和原文证据 |
| `review_alert_actions` | 人工确认/忽略的状态变化历史 |
| `review_monitor_schema` | 监控表版本，当前为 1 |

输入快照保存评论 ID、正文与指纹、处理状态、模型记录和版本、人工记录 ID、有效观点及 Unicode 位置、来源文件路径/哈希/行号。不会读取模型密钥，也不复制用户 ID 或原始 JSON。快照包含评论正文，导出时使用已有的本地 `outputs/` 目录。

同一次分析中的四个工具共享一个事务，运行记录与告警一起提交；任何步骤失败全部回滚，不留下半条成功记录。失败详情由命令返回，当前没有后台任务队列或失败任务表。相同的规范化参数、规则和范围内输入生成相同的 `run_key`；范围外新数据不会使缓存失效。

命令返回结构为 `{"status":"ok","result":...}`。`run`/`replay` 的 `new_alerts` 和 `merged_alerts` 表示本次调用新增或追加的数量；复用运行时两者为 0，原来的命中关联仍可通过 `alert_occurrences` 查到。`runs` 和所有查询命令使用只读连接。

## 从后端调用与运行测试

| Python 函数 | 用途 |
| --- | --- |
| `review_monitor.run_analysis` | 指定当前窗口和基线，保存分析并检测告警 |
| `review_monitor.replay_monthly` | 指定起止月份和基线月数，逐月调用上述函数 |
| `review_monitor_store.init_monitoring` | 显式创建监控表 |
| `review_monitor_store.list_analysis_runs/get_analysis_run` | 查询运行列表或完整快照 |
| `review_monitor_store.list_alerts/get_alert` | 查询告警列表或完整命中、处理历史 |
| `review_monitor_store.set_alert_status` | 确认或忽略告警，保存处理历史 |

参数使用 CLI 对应的下划线名称；商品列表用 `product_ids`，规则用 `rules` 字典。`run_analysis` 默认 `as_of=date_to`；`replay_monthly` 自行生成每个月末的截止时间，模型、来源和版本参数在所有月份保持一致。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_review_monitor.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试使用临时模拟库，覆盖触发与不触发、两侧覆盖率、精确阈值、观点去重、人工空快照、版本隔离、UTC 截止、跨年闰月、并发去重、冷却边界、样本分页、事务回滚、回放重试、只读查询及全部命令行入口。

2026-10-21 交付验证：新增 19 项监控测试，全项目 119 项测试通过。业务库临时副本的 2016 年 1—3 月回放分别读取 98、98、108 条评论；因区间内尚未标注，每月 4 款商品的 24 个维度评估均为 `insufficient_data`，未生成告警。重复回放复用全部 3 次运行，原业务库 SHA-256 未改变。

成功退出 0；业务参数、数据库、文件错误退出 1 并返回 JSON；回放部分失败也退出 1，但保留已完成月份。命令格式错误或缺少必填参数由 argparse 退出 2。当前仍是本地同步批处理：分析期间占用 SQLite 写事务，适合首版小规模数据；大范围回放会逐月保存输入快照并增加数据库体积。
