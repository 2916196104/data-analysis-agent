# 评论观点标注：六个维度、原文证据与增量处理

复用现有 `.env` 的模型配置，按商品质量、使用效果、价格、包装、物流、客服提取观点和情感，保存原文片段、位置、模型标识、提示词版本及处理状态。销售分析和评论导入入口继续使用。

需要在网页筛选评论、查看商品进度并人工确认或纠错时，使用 [评论标注工作台](review-workbench.md)。它单独保存人工历史，原有 CLI 的模型观点保持原始记录。

后端已支持按商品、UTC 日期和回放截止时间筛选标注及进度，并新增指定模型版本的只读 `query` 命令。使用方法和人工有效观点口径见 [按范围查询评论](review-query-scope.md)。不带筛选参数的原有命令继续可用。

**环境配置及业务库观点表初始化由你执行。** 本次没有修改 `.env`，没有在业务库建表或写入标注。`run` 会把选中评论的正文发送到 `LLM_BASE_URL` 配置的服务；不会发送评分、用户 ID、商品元数据或本地文件路径。当前检测的端点为 `https://api.siliconflow.cn/v1`，模型为 `deepseek-ai/DeepSeek-V4-Flash`。

## 业务库检查结果

2026-10-20 已只读检查 `data/private/reviews.sqlite3`：

| 检查 | 实际结果 |
| --- | ---: |
| 商品数 | 4 |
| 唯一评论数 | 4,941 |
| 有正文、可进入标注的评论 | 4,939 |
| 空正文评论 | 2 |
| 正文最大长度 | 3,812 字符 |
| 重复评论唯一键 | 0 |
| 坏记录、外键错误 | 0 |

导入历史与上一阶段验收一致：第一次新增 4 条商品元数据和 4,941 条评论，识别 39 行重复评论；第二次新增均为 0，重复元数据 4 行、重复评论 4,980 行。数据库 `quick_check=ok`，商品元数据关联完整。

完整检查记录见 [业务库检查 JSON](../outputs/review_labeling/business-db-check.json)。这里使用读取现有历史的方式核对去重，没有再次写入导入记录。

## 需要你执行的操作

以下命令在项目根目录的 PowerShell 中执行。`sqlite3` 使用标准库；当前环境已经有 OpenAI SDK 1.109.1 和 Pydantic 2.13.5，符合项目声明，不需要本次另装依赖。若在新环境安装项目，由你执行 README 中的安装命令。

### 1. 初始化观点表

```powershell
Set-Location E:\AllProject\data-analysis-agent
.\.venv\Scripts\python.exe -m data_analysis_agent.review_label_cli init --db data/private/reviews.sqlite3
```

此命令只在现有评论库中增加观点表和索引，保留商品、评论、原文、导入历史及原有数据库版本。重复执行返回 `created=false`。库不存在时不会自动创建，`run` 也不会自动初始化观点表。

### 2. 先标注一小批

复用现有 `.env`，先处理最多 20 条未处理且有正文的评论：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.review_label_cli run --db data/private/reviews.sqlite3 --limit 20
```

该命令会调用你配置的模型服务，可能产生模型调用费用；每条评论最多一次逻辑模型调用，HTTP 层重试次数沿用 `LLM_MAX_RETRIES`。不使用销售 Agent 的多轮工具调用流程。

也可以只处理一款商品：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.review_label_cli run --db data/private/reviews.sqlite3 --limit 20 --product-id B00R1TAN7I
```

`--product-id` 可重复传入，`--env-file` 可以指定另一个由你配置的文件。`--limit` 默认 20，允许 1—1000；程序不会自动连续跑完整库。

### 缩短批次等待时间

旧版标注每次只等待一条模型响应，且 CLI 直到批次结束才输出结果。现在可以保持相同模型及提示词，设置 `--workers 2` 或 `--workers 4`，让不同评论的模型请求同时进行；默认仍为 1。SQLite 写入仍由主线程逐条完成，每条成功结果立即提交，终端会实时显示已完成、成功、失败及模型调用数。一次 `run` 的 `--limit` 仍是整批最多处理的评论数，不是每个 worker 的数量。

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.review_label_cli run --db data/private/reviews.sqlite3 --source amazon_reviews_2023 --dataset-version amazon-reviews-2023 --limit 200 --workers 4
```

IDEA 可选 **18 亚马逊并发标注200条（调用模型）**，此配置不限定商品或日期。先用少量评论比较 `--workers 2` 与 `4`；服务端限流、并发配额和单条评论长度会影响实际速度。认证、限流及其他不可恢复的模型 API 错误会停批；单次超时、连接中断或服务端 5xx 会记录失败后继续，连续 3 次这类错误才停批。最多 4 条已在途的请求会保存结果后结束。失败项仍需要显式 `--retry-failed`。不要同时运行两个标注进程，避免重复发送相同评论。并发不会减少总模型调用量或直接提高标签准确率。

如果终端显示 `model_timeout`，可在 `.env` 中把 `LLM_TIMEOUT_SECONDS=30` 调到例如 `60`，并按服务状况设置 `LLM_MAX_RETRIES=1` 或 `2`（允许 0—3）。超时和重试会延长单条评论的最长等待时间。旧记录中的 `model_api_error` 未保存具体异常，不能仅凭该代码确认是超时；处理服务故障后使用 `--retry-failed` 重试。

模型标签质量应从不同商品和星级抽样，在本机[人工复核工作台](review-workbench.md)核对维度、情感及原文片段，记录漏标和错标后再修改提示词或模型。提示词规则变动会产生新的版本，原有成功记录不会自动复用；改模型也会形成独立标注身份。先用小样本比较准确率、平均耗时、失败率和 token 用量，再决定是否整库重标。

### 3. 查看进度和原文观点

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.review_label_cli status --db data/private/reviews.sqlite3
.\.venv\Scripts\python.exe -m data_analysis_agent.review_label_cli evidence --db data/private/reviews.sqlite3 --review-id 1
```

这两个命令只读访问数据库，无需 `.env` 或 API 密钥。`evidence` 返回正文、商品 ID、原始文件位置、全部标注尝试和成功观点；观点含原文片段、起止位置及对应模型版本。

`status` 初始化后显示 4,939 条可标注评论及空的 `profiles`。产生标注后，按模型、服务地址和提示词版本分别显示：

| 字段 | 含义 |
| --- | --- |
| `successful_reviews` | 与当前评论内容指纹匹配的成功评论数 |
| `failed_reviews` | 已尝试且当前还没有成功结果的评论数 |
| `unprocessed_reviews` | 当前模型/提示词版本尚未尝试的非空评论数 |
| `opinion_count` | 当前内容下成功结果的观点数量 |
| `successful_attempts`、`failed_attempts` | 全历史的成功/失败尝试数，失败重试或内容版本变更不会覆盖旧记录 |

进度默认按全库统计。`status` 可使用与 `run` 相同的 `--product-id`、`--date-from`、`--date-to`、`--as-of`、`--source` 和 `--dataset-version` 查看范围内进度；批次参数不会自动沿用到下一次 `status`，请显式传入相同范围。未指定模型身份时，没有出现的组合表示该范围内没有相关标注尝试。

### 4. 继续下一批，必要时显式重试失败项

重复执行 `run --limit 20` 会继续处理下一批，已经成功的评论不会再次发送。当前筛选范围全部处理后，`selected_reviews=0`、`model_calls=0`。

默认也跳过已有失败记录，避免反复请求同一错误。处理失败原因后，用下面命令把失败项也纳入选择：

```powershell
.\.venv\Scripts\python.exe -m data_analysis_agent.review_label_cli run --db data/private/reviews.sqlite3 --limit 20 --retry-failed
```

`--retry-failed` 同时选择未处理和已失败评论，仍跳过成功结果。零条可选记录不一定代表全部成功，应一起检查 `failed_reviews` 和 `unprocessed_reviews`。

如果希望使用 `review-label.exe` 命令，由你重新安装项目注册入口；使用上面的 `python -m` 不需要注册新命令。

## 观点与情感口径

| 维度代码 | 名称 | 范围 |
| --- | --- | --- |
| `quality` | 商品质量 | 材质、做工、损坏、耐用性、质量缺陷 |
| `effectiveness` | 使用效果 | 功能、效果、易用性、适配、使用体验 |
| `price` | 价格 | 价格高低、性价比、是否值得 |
| `packaging` | 包装 | 包装完整性、设计、容器及保护 |
| `logistics` | 物流 | 配送速度、运输、送达及丢件 |
| `service` | 客服 | 沟通、回复、退换货及售后服务 |

情感代码为 `positive`（正向）、`negative`（负向）、`neutral`（中性）、`unknown`（无法判断）。明确提及某维度但表示不清楚时使用未知；未提及的维度不输出。没有可支持的观点时可返回空数组，并作为“已成功处理、无观点”缓存。

例如模拟正文：

```text
好用，包装破了，客服回复慢。
```

对应的结构化模型输出：

```json
{
  "opinions": [
    {"aspect": "effectiveness", "sentiment": "positive", "quote": "好用"},
    {"aspect": "packaging", "sentiment": "negative", "quote": "包装破了"},
    {"aspect": "service", "sentiment": "negative", "quote": "客服回复慢"}
  ]
}
```

一条评论可以有多个维度，同一维度也可有不同情感和不同片段。程序去除完全相同的观点，但不强制把整条评论归为单一情感，也不把星级作为文本情感标签。

## 原文片段校验

Pydantic 严格校验固定 JSON 结构，拒绝额外字段、未知维度、未知情感、空白片段和非文本片段。允许完整 JSON 代码块外壳；其他解释性文字仍拒绝。JSON 重复键也会拒绝。

每个 `quote` 必须是正文中逐字一致的连续片段，保持原语言、大小写、空格、换行和标点。翻译、改写、拼接或自行补充省略号都会失败。程序计算 `quote_start` 和 `quote_end`，不会采用模型自报的位置；位置是从 0 开始的 Python Unicode 字符索引，左闭右开，满足：

```python
review_text[quote_start:quote_end] == quote
```

同一片段出现多次时，使用首次出现的位置。一条评论只要有一个观点无法通过校验，整条尝试记为失败，不保存其中的部分观点。成功结果和观点在同一事务写入，并在写入前再次核对评论正文与内容指纹。

首版单条正文上限 12,000 字符、最多 30 个观点、每个片段最多 2,000 字符。超长正文不会截断或发给模型，而是记录 `input_too_long`。当前真实业务库最长 3,812 字符，全部非空评论都在此长度范围内。模型输出达到 token 上限或没有完整结束时记为失败。

## 存储与版本

显式初始化增加三张表：

| 表 | 内容 |
| --- | --- |
| `review_labeling_schema` | 观点表 schema 版本，独立于原有导入库版本 |
| `review_annotations` | 追加保存每次尝试、状态、错误代码、模型/提示词版本、用量及耗时 |
| `review_opinions` | 关联成功标注尝试的维度、情感、原文和位置 |

增量身份由评论 ID、评论内容指纹、配置的模型 ID、服务地址、提示词版本共同决定。成功结果有数据库唯一索引，失败重试保留历史；更换模型、端点或提示词版本后，产生独立结果，旧数据不删除。

`requested_model` 保存配置的模型 ID，`model_version` 保存 API 响应中的模型标识；服务没有提供时可为空。模型别名不保证暴露实际权重版本，同一别名在服务端更新时，已有结果不会自动失效。`prompt_version` 包含提示词、JSON schema 和片段规则的 SHA-256，代码规则变化后自动形成新版本。

API 返回用量时，保存输入、输出和总 token 数；未返回的用量是 `NULL`，不当作零。`model_calls` 是逻辑调用次数，SDK 内部 HTTP 重试不单独计入。失败请求的完整服务端异常正文、无效模型输出和 API 密钥不进入标注表或命令行结果。

原始评论按导入契约保留，应用不提供编辑正文功能。后续统计应限定 `status='success'`、当前内容指纹和同一个模型/提示词版本，避免混合历史版本。`evidence` 的 `matches_current_content` 明确标识记录与当前评论是否匹配。

## 失败和恢复

| 错误代码 | 行为 |
| --- | --- |
| `invalid_output` | JSON、原文片段或结束状态不合格；保留失败记录，继续下一条 |
| `input_too_long` | 正文超限；不调用模型，保留失败记录 |
| `authentication_error` | 认证失败；保存本条失败记录，停止批次 |
| `rate_limit` | SDK 重试后仍限流；保存失败记录，停止批次 |
| `model_api_error` | 网络、超时、服务异常或请求不兼容；保存失败记录，停止批次 |

超时和 HTTP 重试沿用现有 `LLM_TIMEOUT_SECONDS`、`LLM_MAX_RETRIES`。模型需要支持聊天接口的 `response_format={"type":"json_object"}`；当前配置已用模拟样本验证。没有额外的模型格式修复调用，失败项由你选择何时重试。

每条评论独立提交，停止、中断或后续写入失败时，前面已提交的成功记录保留；下次增量处理会跳过它们。`--workers` 只并发模型请求，SQLite 保存保持单线程。不要并发运行多个标注进程：数据库能阻止重复成功结果，但进程间竞争仍可能产生重复模型调用。

| 退出码 | 含义 |
| --- | --- |
| 0 | 本批执行完成或没有可选记录；仍需查看进度确认全库覆盖率 |
| 1 | 配置、数据库、输入或文件错误；已提交的前序评论保留 |
| 2 | 命令参数错误 |
| 3 | 本批有失败记录或服务错误导致停止；可查看进度后恢复 |

## 验证与边界

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_review_labels.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

新增 17 项离线标注测试，与原有 46 项合计 63 项。覆盖六维度、多情感、Unicode 与嵌入空字符的原文位置、伪造与不连续片段拒绝、空观点缓存、新评论增量、失败显式重试、模型/端点/提示词隔离、内容版本更新、原子写入、脱敏、有限重试和 CLI。测试使用真实 OpenAI SDK 与 `httpx.MockTransport`，不会联网请求模型。

业务库检查记录位于 [business-db-check.json](../outputs/review_labeling/business-db-check.json)。线上接口联调仅使用新构造的 **SIMULATED 中英文样本**及临时库，记录位于 [simulated-live-validation.json](../outputs/review_labeling/simulated-live-validation.json)。这是接口和证据链验证，不是对真实评论分类准确率的评测。

2026-10-20 最终模拟联调中，3 条样本成功提取 7 个观点，覆盖六个维度及明确未知情感；片段与原文位置全部通过校验。相同范围再次执行时模型调用为 0，临时库已删除，业务库文件指纹未变化。此前发现“效果暂时不清楚”漏标为空数组，补充明确示例后复验通过；修复前记录保留在 [unknown 边界联调记录](../outputs/review_labeling/simulated-live-validation-unknown-before-fix.json)。

真实评论线上联调被自动审批审核拒绝，理由是尚未明确授权向具体模型端点导出评论内容。此项没有执行；改用模拟样本完成接口验证。业务库仍由你初始化观点表并决定何时执行 `run`。

以下检查和模拟联调记录描述观点标注模块交付时的状态；业务库后续真实标注进度以 `status` 查询为准。本阶段不新增趋势、告警或问答工具；这些能力可以继续基于已保存的成功标注及原文索引实现。
