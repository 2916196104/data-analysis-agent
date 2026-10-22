# All_Beauty 真实数据、选品与导入验收

2026-10-20 已获取官方 All_Beauty 评论和商品元数据，选定 4 款美发造型工具，在临时库完成两次导入、去重和全量原文定位验证。**业务数据库由你初始化和操作，本次没有写入业务库。** 本次只读检查发现 `data/private/reviews.sqlite3` 已是初始化完成的空评论库，可以直接使用。

## 已获取的数据

来源为 [Amazon Reviews 2023 官方项目](https://amazon-reviews-2023.github.io/main.html) 的 All_Beauty 下载链接。数据用于历史回放，官方时间范围截至 2023 年 9 月，不是当前商品舆情。

| 文件 | 本地路径 | 文件字节数 | 解压后物理行数 |
| --- | --- | ---: | ---: |
| 评论 | `data/private/amazon_reviews_2023/raw/All_Beauty.jsonl.gz` | 94,441,517 | 701,528 |
| 商品元数据 | `data/private/amazon_reviews_2023/raw/meta_All_Beauty.jsonl.gz` | 39,871,253 | 112,590 |

官方文件地址：[评论 gzip](https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/review_categories/All_Beauty.jsonl.gz)、[元数据 gzip](https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/meta_categories/meta_All_Beauty.jsonl.gz)。本地已完整下载，无需再次下载或解压。

两个文件均核对了 HTTP 分段范围、总字节数和 gzip 完整性，并全量通过现有字段校验，没有格式或字段拒绝记录。评论中实际出现 112,565 个 `parent_asin`；元数据文件的行数与评论中的商品数采用不同口径。

本地计算的 SHA-256（用于复核本次输入，不是官方公布的校验值）：

```text
All_Beauty.jsonl.gz
ee00e66835567c3f12fde6a482f8d7055c22cac2d0924f677263affdd8a0e349

meta_All_Beauty.jsonl.gz
51f8255c2794afd60e274c10d3e2d09dc1f671eba4ba35f74f748d8631216d05
```

下载地址、实际文件路径、字节数、下载时间和指纹保存在 [下载记录](../outputs/all_beauty_validation/downloads.json)。原始文件未改写，保持官方文件中的顺序和行号。

## 选定的 4 款商品

以 `parent_asin` 为商品标识，同一父商品下的颜色、尺寸等变体合并。先扫描全量评论，取得至少 300 条非空正文且有元数据的 126 个候选商品，再按美发造型工具范围、评论规模、品牌和元数据完整性人工筛选。最终每款去重后仍超过 1,000 条评论，且均有一条合法元数据。

| 商品 ID | 商品简称 | 源评论行数 | 去重后评论数 | 去重后 1—2 星评论数 |
| --- | --- | ---: | ---: | ---: |
| `B00R1TAN7I` | GranNaturals 猪鬃梳 | 1,372 | 1,362 | 239 |
| `B019GBG0IE` | The Curly Co. 可折叠吹风扩散罩 | 1,328 | 1,319 | 392 |
| `B01M1OFZOG` | Bed Head Curve Check 卷发棒 | 1,243 | 1,237 | 257 |
| `B01N4885PD` | Helen of Troy 1511 电热卷发梳 | 1,037 | 1,023 | 342 |
| 合计 | 4 款 | 4,980 | 4,941 | 1,230 |

这组数据用于功能验证，不代表整个类目的问题分布。不同工具的使用效果需要按各自用途解释；1—2 星是原始评分口径，尚未做文本情感或问题维度标注。

完整商品名称、品牌、来源行号、月份分布、输入指纹及验收结果索引保存在 [选品清单](../data/private/amazon_reviews_2023/selection.json)。[全量扫描结果](../outputs/all_beauty_validation/profile.json) 保存了候选商品及质量计数。这些本地数据和运行产物均由 Git 忽略。

## 需要你执行的步骤

所有命令在项目根目录的 PowerShell 执行，沿用现有 `.venv`。本次不需要安装新依赖、配置模型 API 或启动数据库服务。

### 1. 检查现有业务数据库

```powershell
Set-Location E:\AllProject\data-analysis-agent
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.reviews summary --db data/private/reviews.sqlite3
```

本次检查的该库返回 `review_count=0`、`product_count=0`、`import_count=0`。若仍是此状态，可以跳过第 2 步。

### 2. 仅在需要新建数据库时，由你初始化

如果改用其他路径，下面及后续命令的 `--db` 都换成你选定的路径。父目录先创建，随后显式初始化：

```powershell
New-Item -ItemType Directory -Force data/private | Out-Null
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.reviews init --db data/private/reviews.sqlite3
```

已初始化的评论库会返回 `created=false`，不会清空数据。程序拒绝覆盖其他类型的数据库。

### 3. 导入选中的 4 款商品

这条命令扫描完整原始文件，只保存指定商品；原文位置直接指向官方文件。可以整段复制到 PowerShell：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.reviews import `
  --db data/private/reviews.sqlite3 `
  --reviews data/private/amazon_reviews_2023/raw/All_Beauty.jsonl.gz `
  --meta data/private/amazon_reviews_2023/raw/meta_All_Beauty.jsonl.gz `
  --product-id B00R1TAN7I `
  --product-id B019GBG0IE `
  --product-id B01M1OFZOG `
  --product-id B01N4885PD
```

对空库首次执行的预期：

| 输入 | 新增 `inserted_records` | 重复 `duplicate_records` | 坏记录 `invalid_records` |
| --- | ---: | ---: | ---: |
| 元数据 | 4 | 0 | 0 |
| 评论 | 4,941 | 39 | 0 |

最终 `status=ok`、退出码 0；`quality.review_count=4941`、`product_count=4`。有 2 条空正文保留在库中，后续文本分析可用 `has_text=1` 筛选其余 4,939 条。缺评分、缺用户 ID、缺商品元数据的评论均为 0。

### 4. 再执行一次同样的导入，检查去重

重复执行第 3 步。元数据和评论的新增数都应为 0，重复数分别为 4 和 4,980。这里的 4,980 是本次输入行数，包含原文件自身的 39 行重复；库中唯一评论数继续为 4,941。导入历史数会增加。

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.reviews summary --db data/private/reviews.sqlite3
```

### 5. 查询原文位置

在空库按上面的命令导入后，下面例子对应 GranNaturals 的一条评论，原始评论文件第 44,623 行：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.cli.reviews evidence --db data/private/reviews.sqlite3 --review-id 347
```

核对返回的 `product_id=B00R1TAN7I`、`line_number=44623`、`file_sha256` 和 `raw_json`。`evidence_ref` 应为：

```text
sha256:ee00e66835567c3f12fde6a482f8d7055c22cac2d0924f677263affdd8a0e349#line=44623
```

评论 ID 由数据库分配；如果原库已有其他评论，ID 可能不同，应以该次导入的数据库结果为准。gzip 的行号指解压后的物理行，SHA-256 指下载的压缩文件。

## 本次已经完成的验证

验证仅使用 `TemporaryDirectory` 下的临时数据库：初始化临时库、通过真实 CLI 导入两次、核查外键、逐条读取官方源文件比对，再删除临时库。脚本没有业务数据库参数，业务库未被写入。

| 检查 | 实际结果 |
| --- | --- |
| 首次导入 | 4 条元数据、4,941 条唯一评论 |
| 原文件中的重复评论 | 39 行被识别并去重 |
| 再次导入 | 两种输入的新增数均为 0 |
| 评论原文逐条对照 | 4,941 条全部与官方文件对应行一致 |
| 元数据原文逐条对照 | 4 条全部一致 |
| 每款商品代表评论的证据接口 | 商品 ID、正文、文件指纹、行号、证据引用均核对通过 |
| 外键错误、字段拒绝、缺失元数据关联 | 均为 0 |
| 输入文件变化 | 无 |
| 临时数据库 | 已删除 |

完整结果保存在 [真实数据验收 JSON](../outputs/all_beauty_validation/verification.json)，包含两次导入摘要、商品统计及每款代表评论的来源证据。

需要独立复验时可以运行以下命令；它只创建并清理临时测试库，不初始化业务数据库：

```powershell
.\.venv\Scripts\python.exe scripts/validate_review_import.py `
  --reviews data/private/amazon_reviews_2023/raw/All_Beauty.jsonl.gz `
  --meta data/private/amazon_reviews_2023/raw/meta_All_Beauty.jsonl.gz `
  --product-id B00R1TAN7I `
  --product-id B019GBG0IE `
  --product-id B01M1OFZOG `
  --product-id B01N4885PD `
  --report outputs/all_beauty_validation/verification.json
```

## 后续历史回放建议

去重后这组商品在 2023 年合计只有 75 条评论，直接使用最近 7—14 天作为告警窗口通常样本不足。先采用月度历史回放，再依据样本量校准窗口和告警阈值。

按“四款商品各自的三个月评论量尽量均衡”选择出的共同回放窗口为 **2016-01-01（含）至 2016-04-01（不含）**，共 304 条唯一评论：四款分别有 164、47、51、42 条。该窗口用于后续开发测试，选择时使用了全历史的月份统计；未来评测应另设保留时段，逐个回放时点只向分析工具提供当时可见数据。此次没有随机抽掉评论，也没有人为制造异常。

当前尚未做观点标注、主题趋势或告警评测；窗口建议不表示已经发现业务风险。完整导入的数据契约、字段含义和错误处理见 [评论导入说明](review-import.md)。
