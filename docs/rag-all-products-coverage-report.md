# 全商品库 RAG 覆盖盘点

## 2026-11-12 本机真实盘点结果

本机一次只读运行 **COMPLETED**：商品目录 1,706 个 `source + dataset_version + product_id` 组合，全部扫描，未扫描 0。该数字是组合数，不擅自认定为跨来源去重后的商品数。结果绑定本次确认的标注 profile 和 Embedding 空间，不能推广到其他 profile。

| 项目 | 本次结果 |
|---|---:|
| 有评论的组合 | 1,706 |
| 当前 profile 有成功标注 / 有有效观点 | 17 / 17 |
| 有历史向量 / 至少一个认证可用向量 | 2 / 2 |
| 有有效观点但没有向量 | 15 |
| 当前 profile 没有有效观点 | 1,689 |
| 来源异常待核验 UNKNOWN | 0 |
| 当前有效观点 E / 认证可用观点 U | 14,845 / 400 |
| 汇总观点覆盖率 U/E | 约 2.69% |

1,689 项“没有有效观点”只针对**本次 profile**，不代表没有任何历史标注，也不是 0% 覆盖率；其覆盖率为 N/A。15 个无向量商品的详情见本次 CSV。已有可用向量的只有下列两个组合：

| 来源 / 数据集 | 商品 | E | U | 观点覆盖 | 有可用观点的评论 / 有观点的评论 |
|---|---|---:|---:|---:|---:|
| reviews_csv / reviews-csv | 100013588053 | 3,591 | 200 | 5.57% | 114 / 1,493 |
| reviews_csv / reviews-csv | 100013588055 | 1,091 | 200 | 18.33% | 105 / 436 |

对于已认证的 400 条当前向量，记录显示均匹配当前成员、来源字段与快照；本次未记录需进一步核验的来源异常。pgvector 0.8.7、`vector(1024)` 可用，记录中的向量表索引均为 valid/ready；本次现象主要是**当前 profile 下大量商品无有效观点，以及已有有效观点的商品向量覆盖低**，不能仅凭索引元数据断言检索性能或将缺口全部归因于同步失败。

覆盖分布还显示：053 的价格正向观点有 85 条，但可用向量为 0；055 的若干维度/情感组合也未覆盖。完整 aspect、sentiment 和月份分布在本次 JSON 和 Markdown 中，不能把这些样本缺口表述为全量评论事实。

程序给出的前五个**待业务确认的候选**为 `100019386660`、`100010350689`（reviews_csv）以及 `B019GBG0IE`、`B00R1TAN7I`、`B01M1OFZOG`（amazon_reviews_2023）；它们当前 profile 下均有有效观点但没有向量。候选由缺口与规模形成，业务需求、费用与同步风险仍未知；如果近期需求集中在 053/055，应同时考虑优先补齐这两个已有商品。任何补齐均须另行评估和授权，本次没有同步。

[本次 CSV](evaluation/rag-all-products-coverage-20261112T071552646424Z.csv)｜[本次脱敏 JSON](evaluation/rag-all-products-coverage-20261112T071552646424Z.json)｜[本次完整 Markdown](evaluation/rag-all-products-coverage-20261112T071552646424Z.md)。本轮独立对照 JSON 与 CSV 的 1,706 行的来源、数据集、商品、E、U 和状态：0 处不一致；aspect/sentiment 与月份分组的 E、U 合计均与商品总数一致，重复组合 0，U>E 为 0。JSON SHA-256：`d8bf4967c69091a2a736acff12276109d5d1ebdbdb4137970ea5902150c80433`；CSV SHA-256：`d3e2ee28e8797549d16608f15625212c95221ffd4dc99b541a6f75392bc1c556`。这些是文件一致性核验，未重连数据库复核。

执行记录：1 次连接，ROLLBACK 与连接关闭均成功；数据库写入、Embedding、LLM 和 Agent 调用均为 0。本次盘点不等于真实检索质量或多商品 Agent 通过，也不解除 P6-E1B 的 BLOCKED。

---

## 以下为执行前的准备记录

## 2026-11-12 本机执行前参数复核

当前本地配置中的标注模型身份与 `reviews.prompts.PROMPT_VERSION` 计算所得 profile 为 `1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304`，与商品 055 已记录的 profile 一致；本次没有连接数据库。它证明本地配置匹配，不能单独证明数据库中所有商品都使用该 profile；脚本会在只读快照内验证 profile 的唯一身份并按商品如实统计。

参数为 `--execute-authorized --profile-key <上述摘要>`；入口固定本机 `reviews` 的 `rag_audit_ro`、单连接和只读快照，其他约束及超时见下文。静态检查 PASS，专项 **7/7 PASS**；尚无全库真实执行结果。Codex 当前受限上下文先前不能可靠连接本机数据库，本次不在该上下文重复尝试；应在此前已验证连接的本机 PowerShell 中执行一次并保留输出。不会自动重新运行、改数据库、调用 Embedding/LLM 或运行 Agent。

状态：**准备完成，真实盘点 NOT EXECUTED**。本轮只检查源码和合成数据，未连接 PostgreSQL、运行 Agent、调用 Embedding 或 LLM，也未同步向量。商品总数、各商品覆盖率和优先名单尚不能回答；不得把历史商品 055 的单商品记录当作全库结果。

## 当前能够确定的范围

此前 055 固定范围的真实记录为 1,091 条有效观点、200 条认证可用向量，覆盖率约 18.33%，是**历史单商品观察**，不是本次全库盘点。实际商品目录、不同来源及数据集的范围、当前生产标注 profile、其他商品覆盖和检索可用性，本轮均为 **UNKNOWN**。P6-E1B 独立 PostgreSQL 验收继续 BLOCKED。

盘点入口：[audit_all_products_rag_coverage.py](../scripts/audit_all_products_rag_coverage.py)。默认只执行本地静态契约检查，真实读取仅由另行批准的 `--execute-authorized` 启动。离线结果为 `OFFLINE_CHECK_PASS`，源码中的 18 个来源字段与生产检索实现一致；[专项测试](../tests/test_all_products_rag_coverage.py) **7/7 PASS**。不涉及真实数据库和真实评论。

本轮[CSV 文件](evaluation/rag-all-products-coverage.csv)只有字段头，不含任何商品记录；[脱敏执行状态](evaluation/rag-all-products-coverage-not-executed.json)明确 `NOT_EXECUTED`。两者不是空数据库或全库零商品的证据。

## 已准备的真实盘点方式

未来一次运行固定使用本机 `reviews` 中的 `rag_audit_ro`；角色及数据库身份、只读事务、UTC、角色写权限、RLS、表版本、pgvector 和可见索引需逐项通过。采用单连接和 `REPEATABLE READ READ ONLY`，单语句 15 秒、锁 3 秒、数据库事务 280 秒、程序启动新商品的截止时间约 270 秒；不自动重试。结束时 rollback 并关闭；不能证明资源清理则 BLOCKED。超时是有限保护，无法保证整个 Python 进程有硬截止；发生未响应应保留原始证据，由负责人处理，不自动重连。

程序从数据库目录发现 `source + dataset_version + product_id`，先取商品真实 UTC 日期边界，不复制 055 旧日期。复用 `review_scope()`、`PostgresSemanticRepository.snapshot()`、`read_effective_corpus()`、`_eligible()` 和 `_verified_sources()`；模型、人工修订及人工删空均由生产有效语料规则处理。每商品比较观点键、18 项持久化来源、快照、profile、space、text builder、失效状态及向量有效性；分别统计有效观点、去重评论以及 aspect/sentiment 和月份覆盖。按商品依次处理，每个商品最多 10,000 个有效观点和 20,000 条历史向量；超限、权限不足、数据不一致或事务错误立即停止，不自动忽略未盘点商品。

当前实现对观点不再属于当前集合且标注或人工修订不同的向量，记录 `annotation_mismatch_unverified` / `revision_mismatch_unverified`，**不会只凭 ID 不同就宣称已证实旧标注或旧修订**；若需精确区分历史来源，必须再核对历史行。来源字段损坏、无法解释的非成员或这类未核验历史关系将商品保留为 UNKNOWN。即使有可用向量，UNKNOWN 商品也不能被直接称为稳定可检索。E=0 的观点覆盖率为 N/A；观点与评论分母不混合。

## 一次性本机执行命令

**本轮不执行。** 需要负责人先确认目标实例、`rag_audit_ro`、允许的全库读取范围及标注 profile，再独立明确授权一次真实盘点。使用本机已有的受控 libpq 凭据，不在终端、报告或聊天输入密码。以下命令只在取得授权后由本人运行；profile 为已确认的 64 位 SHA-256，不是商品 ID，也不会自动把 055 的 profile 当作整个数据库的当前配置。

```powershell
Set-Location -LiteralPath 'E:\AllProject\data-analysis-agent'
$CoverageProfile = Read-Host '输入负责人确认的标注 profile SHA-256'
& '.\.venv\Scripts\python.exe' -B '.\scripts\audit_all_products_rag_coverage.py' --execute-authorized --profile-key $CoverageProfile
```

脚本在 `docs/evaluation/` 创建带运行时间的独立脱敏 JSON、CSV 和 Markdown，不覆盖本文件、空模板或旧记录。完成后以该次实际输出为准；BLOCKED/部分扫描不能算全库结果。公开文件不含评论正文、原始向量、密码或标注 endpoint。建议先排查 UNKNOWN 与来源异常，再综合无可用向量、有效观点规模、覆盖率和经负责人确认的业务需求、成本、风险决定 3～5 个商品；业务需求未知时不自动生成最终排序或同步。

## 尚未回答的问题

当前数据库商品数、已有认证可用向量的商品、无法检索的商品、覆盖最低商品和应优先补齐的 3～5 款商品：**均未测得**。缺索引、来源失效或数据本身缺失哪个是主要问题也未知；有向量不等于高质量回答。真实执行后的可复核结论才可回答这些问题；本轮到此停止。
