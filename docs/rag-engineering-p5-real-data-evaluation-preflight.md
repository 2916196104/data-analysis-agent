# P5 Text Evidence 真实业务评估样本与契约预检

2026-11-07。本轮完成离线暂选：**24 个目标契约×候选单元、20 条不同评论、20 个观点键、27 个事实×候选待审行**。这不是 24 个合格可执行单元：8 个目标的当前 Runtime 均不等价，24 个单元全部处于 `pending_contract_equivalence`。**当前可执行请求为 0，新四状态人工确认数为 0。**

没有连接 SiliconFlow、PostgreSQL 或 Embedding，没有执行历史 D/E/T 实验、重新检索或发布人工参考。`publication_allowed=false`、`semantic_verified=false`、`permitted_target_fact_ids=[]` 保持不变。

## 1. 材料及筛选结果

依据[评估计划](rag-engineering-p5-real-data-evaluation-plan.md)、[Text Evidence 结果](rag-engineering-p5-text-evidence-result.md)、原 48 组检索快照、E1-A 已发布参考及其原会话进行核对。已发布参考通过原 `ReferenceRelease` 解析，原 `validate_session` 仍返回可冻结状态；本轮没有新增人工事件。原参考确认的是旧事实/列表判断，不是新的逐候选四状态金标准。

| 层次 | 评论数 | 观点键 | 原排名记录 / 去重目标×候选 |
|---|---:|---:|---|
| 原 24 题×U/F 共 48 组 | 57 | 93 | 222 条排名 |
| 排除已知 S/N 开发暴露后 | 37 | 51 | 118 条排名；85 个 query×key 对 |
| 8 个暂选目标内可选 | — | — | 30 个 query×key 对 |
| 本轮暂选 | 20 | 20 | 40 条原排名记录；24 个目标×候选 |

具体排除与去重：

- S1–S3、N1–N3 共涉及 22 条不同评论；超时和未发送的开发输入也排除。其中与历史池重合 **20 条评论、42 个观点键**，按整条评论排除。同正文换 ID 也检查暴露，不靠换 key 回流。
- 85 个未暴露 query×key 对中，**55 个不属本轮 8 个目标**，另外 **6 个因目标配额和评论多样性未选入**。51 个观点键中 20 个入选、31 个本轮不选；这些排除不宣称来源错误或语义不支持。
- 24 单元仅涉及 20 条评论；**4 条评论各用于两个目标**，按评论聚类，不当作新增独立评论。40 条 U/F 原排名记录合并为 24 单元；保留每个 mode/rank，不改变历史 Top-K。
- 全池按原始全文相似度 `SequenceMatcher(autojunk=False) ≥ 0.80` 标记近重复，同时与已知开发正文比较；所选 20 条未出现该阈值标记。此启发式不证明没有转述、近义复制或其他开发暴露；当前声明仅为未命中已知 S/N 评论及相同正文，不能宣称全面独立或预训练未见。

本轮是历史 Top-K 的定向语义评审子集，不是新的 Top-K，不用于重算一套“删掉困难/暴露候选后”的检索成绩。相关性 2 分未参与生成四状态答案。

## 2. 暂选清单统计

真实 ID、key、原文、来源身份和人工私有理由仅保存在忽略目录。下列 R 编号是本轮评审索引，**不是新的检索 rank**。

| 原目标 | 评审索引 | 单元 / 事实行 | 原排名记录 | 目标分层 |
|---|---|---:|---:|---|
| Q02 | R001 | 1 / 1 | 2 | 客服对象、服务态度与泛称服务的区分 |
| Q04 | R002–R004 | 3 / 3 | 5 | 购买后问题、持续无人跟进 |
| Q05 | R005–R008 | 4 / 4 | 8 | 下单至收到货的速度；混合安装体验 |
| Q06 | R009–R010 | 2 / 2 | 3 | 未收到货与已到货未安装的区分 |
| Q09 | R011–R014 | 4 / 4 | 5 | 早期故障、泛质量评价及配件对象 |
| Q11 | R015–R017 | 3 / 3 | 4 | 炎热条件下室内快速变凉；制热干扰 |
| Q12 | R018–R021 | 4 / 4 | 7 | 长时间运行仍不凉与缺少时长/相反体验 |
| Q18 | R022–R024 | 3 / 6 | 6 | 到货与安装等待的同评论、先后关系 |
| **合计** | **R001–R024** | **24 / 27** | **40** | **8 个目标、20 条评论** |

每单元记录原 query/fact/version/revision、review/annotation/key、quote 绝对范围、全文 UTF-8 摘要、历史来源字段、全部 U/F 位置、开发暴露、近重复、业务分层、原参考指针、准入阻断。同评论观点不作为独立评论；全文摘要只是内容指纹。

## 3. 八个人工支持种子的处理

13 条 U/F 原支持引用按 `query/fact/key` 去重为 8 个种子：

| 原目标 / 事实 | 本轮处理 |
|---|---|
| Q04 / f0 | 入选；保留原人工 direct 发现和支持片段 |
| Q05 / f0 | 入选；保留原人工 direct 发现和支持片段 |
| Q06 / f0 | 入选；保留原人工 direct 发现和支持片段 |
| Q12 / f0 | 入选；保留原人工 direct 发现和支持片段 |
| Q18 / f0、f1 | 两个事实种子共同进入一个候选单元，不能计作两条评论 |
| Q15 / f1 | 保留旁表，不纳入本轮八目标；价格不是本轮优先分层 |
| Q20 / f1 | 保留旁表，不新增目标；同评论/key 已用于 Q12，但目标条件不同，不能转移人工确认 |

共 **6 个入选事实种子、5 个候选单元**，另 2 个种子本轮排除。`previously_confirmed` 仅描述原确认事实及引用；**所有种子的 `new_four_state_confirmed=false`**。旧 direct、partial、not_supported、列表 sufficient/insufficient 均未转换为新四状态。

## 4. 原契约到实验草稿的逐项等价核对

8 个原目标均为已确认的 `rag-query-contract-v1`、revision 1。私有 `contract-mappings.json` 保存原问题、确认事件及理由、原完整契约和逐字段映射；只读调用现有 `build_query_plan` 比较，未修改解析器。

草稿逐字复制 `task_type/target_entity/subtopic/sentiment_intent/required_facts/polarity_structure/proposed_filters/uncertainty/clarification/scope_ref`，并保留 `independent_target_fact_ids`。因此可以核对“草稿没有删改旧语义”，但**该草稿不是 RuntimeQueryContract，也不是可信绑定**。旧 `scope_ref` 与当前 Runtime scope 不是同一种身份；保存的旧 Runtime scope 仅用于重解析比较，不代表本次来源已核验。

| 目标 | 必须保留的事实、方向和条件 | 当前 Runtime 实际结果 | 结论 |
|---|---|---|---|
| Q02 | f0；客服对象及态度/说话行为；negative；all_of | unsupported / statistics；0 事实 | 对象和目标事实未被 Runtime 表达，pending |
| Q04 | f0；购买后、出现问题、持续无人跟进；negative；all_of | unsupported / statistics；0 事实 | 不能替换成一般服务差或安装慢，pending |
| Q05 | f0；从下单到收到货的快速经历；positive；all_of | unsupported / statistics；0 事实 | 不能以安装速度或仅收货日期替代，pending |
| Q06 | f0；迟迟未收货或明显配送延误；negative；all_of | fact_search / retrieval_only；0 事实 | 检索路由存在但没有可判定目标事实，pending |
| Q09 | f0；使用不久即损坏/故障；negative；all_of | unsupported / statistics；0 事实 | 不能删早期条件或任意扩大设备/配件对象，pending |
| Q11 | f0；炎热环境、开机、室内很快凉下；positive；all_of | unsupported / statistics；0 事实 | 保留原非数值条件及 uncertainty，不改成一般制冷快，pending |
| Q12 | f0；运行较久后仍不凉；negative；all_of | unsupported / statistics；0 事实 | 不删除时长，也不另定数值阈值，pending |
| Q18 | all_of(f0 已到货，f1 到货后长期无人安装)；f0 neutral / f1 negative；same_review、before | opinion_summary / supported；0 事实 | supported 字面值不代表事实准入；两个关系均未表达，pending |

八目标均没有 any_of、没有独立许可子事实；Q18 不拆分。其他目标不新增必要关系；原极性结构、澄清和条件全部保留。没有把统计、unsupported、retrieval_only 或 opinion_summary 强制改成 evidence/fact_search，也没有新建实验子目标。

当前 Runtime 也没有原 `polarity_structure`、`clarification`、`scope_ref`、独立许可清单的同名字段。草稿保留它们供核对，不能通过丢弃这些字段完成格式转换；未来须明确哪些是必要语义约束、如何在现有实验边界保留，不能仅以字段复制作为 Runtime 等价证明。

**当前 Runtime 与原确认目标不等价；未来实验 Runtime 的等价与绑定仍待解决。** 原人工理由已复制，包含对 Q18 分类解释/关系及其他条件的原确认依据；不能仅凭遗留 uncertainty 文本要求用户重审全部旧目标，也不能直接删掉该字段以通过准入。后续只核对新旧表示差异和绑定方式，禁止默默换题。

所有 `experimental_runtime_contract`、`trusted_runtime_sha256`、`new_confirmation_record` 均为 null。没有创建可交给模型执行的契约。

## 5. 四状态旁表与当前门槛

私有旁表准备了 **27 行门控草稿**。契约等价尚未满足，故当前 `review_enabled=false`，**实际进入新四状态评审的行数为 0**；不是把 27 个待审行宣称为已准入。目标与来源通过前，数量可以减少。

各行有原事实/版本、原支持引用或列表参考指针、quote/全文位置、既有判断的可复用范围、待核对条件、歧义和漂移状态。字段状态严格分开：

- `previously_confirmed`：仅原确认目标、原 direct 事实及其引用；不赋予新四状态含义。
- `proposed`：Codex 整理的核对重点，不包含新标签建议或新引用答案。
- `pending_human_review`：全部 27 个新四状态行；`expected_status=null`、`expected_reference_type=null`、`necessary_evidence_texts=[]`、`human_confirmed=false`。
- `excluded`：本轮未选目标/观点及已知暴露材料，不进入本次主评估。

需要用户未来独立判断 **27 个事实×候选**，并为 Q18 的 **3 个候选**分别核对 same_review 和 before（共 6 个关系检查）。可以在同一评论阅读时完成，但不能以一个事实成立代替整个 all_of。当前不要求立即填写这些受契约门槛阻断的答案。

| 单元的首要筛选状态 | 数量 |
|---|---:|
| suitable_for_next_review | 0 |
| pending_contract_equivalence | 24 |
| pending_human_review | 0 |
| pending_source_validation | 0 |
| excluded（所选单元内） | 0 |

以上按首个阻断计数；24 单元同时都缺新人工金标准和本次 P0 来源核验，后两行的 0 **不表示已完成这些门槛**。51 键池内另有 31 键本轮 excluded；历史暴露排除另列，分母不能混加。

引用验收继续使用现有 Text Evidence 和 B1/B2：精确原文、唯一匹配、同 key/身份、Unicode code point 绝对区间；support/refutation 只在 quote，context_veto 只在同评论全文；全文不能补支持。即便位置可靠、B1/B2 合法，仍须由独立人工核对语义充分性。重复、无匹配、漂移和来源不一致失败关闭；结构失败的诊断不进入正式准确率。

未来最多可按 **8 个同契约、每组 1–4 个候选**安排；`review-groups.json` 仅列人工阅读索引，`request_body=null`、`executable=false`，不是模型批次。没有生成 prompt、模型输入或可自动发送的清单。

## 6. 覆盖与缺口

| 类型 | 本轮材料情况及限制 |
|---|---|
| 制冷正面/不足 | 保留制冷、制热干扰和缺时长候选。Q11 所需炎热条件仍不能由一般制冷快自动满足；正面目标不等于已有正面金标准 |
| 安装/客服/配送区分 | 有泛称服务、安装等待、未收货等候选；对象未知与明确不同对象必须由用户裁决 |
| 快速配送、延迟、售后跟进、质量 | 已选相关旧支持种子及无直接目标条件的干扰候选；早期故障与遥控器等配件边界待核对 |
| 同评论混合体验、否定 | 有配送/安装、效果/售后等混合上下文和否定表达；不把无关正面体验当撤回 |
| 转述、假设、未知对象 | 有需核对对象及电话转述的材料；没有冻结的类标签；纯假设场景未形成合格覆盖 |
| 明确撤回/纠正 | **未确认覆盖**。不把客服纠正商品承诺、相反体验或一般否定改标为作者撤回，不编造真实 refuted |
| quote 外全文否决 | 没有已确认真实 context_veto 金标准；本轮不能证明该业务能力 |
| 重复/无法唯一定位 | 所选 20 条完整 quote 在全文均只出现一次；51 键池中的全文重复 quote 未进入本轮八目标清单。实际短引用仍可能重复，需按允许范围检查；不能声称真实业务歧义拒绝已被该子集验证 |

当前四状态各类分布均未冻结，尤其不能预报 refuted/unknown 的样本数或召回。缺类时相关指标为 N/A；不追加合成数据填充真实覆盖，不追加模型调用追求通过率。

## 7. 最小数据库只读来源验证设计（未实现、未执行）

针对当前暂选 **20 个明确 review_id**，精确清单只在私有 `source-validation-request.json`。若人工/契约核对后减少，只能收缩授权集合；新增、替换或跨范围读取须重新列明。需要这 20 条评论各自的**完整当前有效观点集合**，不只读所选的 20 个 key，否则无法发现撤销、替换、空快照或集合漂移。当前有效观点总行数未知，不能用历史所选 key 数假装完整。

| 表 / 结构 | 最小读取字段和目的 |
|---|---|
| reviews | id、source/dataset_version/product_id、title/text/has_text、content_sha256、published_at、import_run_id、line_number；比对完整正文、内容身份与范围 |
| review_annotations | 同正文、指定 model/base_url/prompt_version 的当前标注 id/status/model_version/error_code/content_sha256；保持原“优先成功再按 id”选择语义 |
| review_opinions | 当前有效模型观点的 id、annotation_id、aspect/sentiment、quote/quote_start/quote_end；不漏完整集合 |
| review_human_reviews / review_human_opinions（若存在） | 最新 revision/action 与完整人工观点，包括有效空快照；不回退到被人工替换的模型集合 |
| import_runs | 对应导入 id、file_sha256；联合 line_number 验证原来源定位 |
| 必要范围元数据 | 限同 source/dataset/product/date/profile 的绑定；如需验证 schema 版本，仅查询对应版本元数据，不读其他商品评论 |

授权范围沿用已有范围和 annotation profile，精确值保存在私有请求草稿，不在 Git 新增来源细节。历史快照缺少可用的 import_run_id、当前处理/人工状态、完整有效观点集合和 P0 内存证明。历史 `evidence_valid`、自算全文摘要或旧发布确认均不能补齐它们。

现有接口的边界：

1. `semantic_retrieval.load_review_contexts` 已支持按 ID 读取正文/导入字段，但不包含完整当前观点、处理和修订状态，**单独调用不足以签发 P0**。
2. `current_reviews_sql` 能保持模型/人工覆盖语义；`read_effective_corpus`、`_PostgresSnapshot.corpus` 当前会读取整个选定商品日期/profile 的有效语料。不能用其现有检索入口完成限 ID 核验，也不能用假编码器绕过真实检索来签发真实来源。
3. 后续最小适配应把参数化 ID 集合约束下推到 reviews、annotation attempts/current、有效观点关联阶段，在同一只读 repeatable-read 快照内读取。仅在最终 SELECT 外套 ID 条件不足以保证内部不扫描范围语料。不得引入向量读取、生成、同步或数据库写入。
4. 将真实读取构成的 `ReviewSource/RagReviewState` 与所有有效观点交给现有 `_document`、`_review_state`、`_verified_sources` 等拥有者边界核对：范围、profile、来源行 ID、quote 切片、全文、导入身份、完整集合和 key。与历史参考正文/quote/标注/修订不同则阻断并报告，不静默重基准。
5. **现有 P0 只在真实来源检查及一致快照通过后由拥有者签发内存绑定。** 后续需要单独设计、测试拥有者内部的限 ID 校验/签发路径；本轮没有实现该接口，没有构造 `_source_validation` 或 `_SOURCE_VALIDATOR`，没有生成可信 result。自行计算 SHA 只用于比较，不能成为信任发行者。

旧 Top-K/rank 始终只是历史来源元数据。限 ID 重验不产生新的检索命中、距离或排名；若未来还要证明当前检索质量，须另列范围，本轮不授权。

## 8. 私有交付与离线检查

新目录：`outputs/rag-semantic-retrieval/real-evaluation-review-preflight-v1/`，已由 `git check-ignore` 确认忽略，沿用现有私有目录创建与 ACL 保护；未修改历史目录或其权限。

| 私有文件 | 用途 |
|---|---|
| review.md | 24 单元阅读索引、完整 quote 和未截断全文、核对重点 |
| candidate-pool.json / selection-log.json | 51 键池、暴露排除、全文摘要/历史身份、85 对选入与排除理由 |
| selected-units.json | 24 单元完整来源、历史位置、分层和阻断 |
| contract-mappings.json | 八目标原确认、逐字段草稿及未改变的 Runtime 重解析结果 |
| review-sidecar.json / seed-reuse.json | 27 行空金标准草稿、八支持种子的原参考与可复用边界 |
| source-validation-request.json | 精确 20 个 ID、范围和完整有效观点要求；design_only/not_authorized |
| review-groups.json / summary.json / input-digests.json | 不可执行的人工分组、安全统计、12 个已读历史输入摘要 |

Git 中仅新增本报告、小型离线筛选脚本及合成专项测试；没有真实评论、ID/key 清单、私有来源字段、个人信息或私有评审内容。

验证结果：

- 专项合成测试 **15/15 通过**：整评论/同正文暴露排除、U/F 合并、排名不改写、来源切片不一致拒绝、Unicode 范围、配额不足不凑数、同评论聚类优先、旧引用去重及不升级新金标准、完整条件复制和 Runtime 门控。
- 实际离线准备在禁止 socket/HTTP、psycopg、OpenAI client、BGE 编码器及真实检索入口的拦截下完成；没有任何外部调用。新目录创建前检查未占用，既有五份主输入的固定字节摘要匹配。
- 实际材料只读复核：24/24 原 quote 与完整正文切片一致；27/27 新答案及引用类型为空、确认 false、评审门关闭；8 组均 ≤5 且无请求正文；全部 P5 source identity、可信 Runtime 绑定为空，P0 未准入。
- 已读的 **12 个历史输入**在生成后字节摘要仍一致；原会话/参考未重新发布。开始时记录的 **396 个既有 src/scripts/tests/docs/experiments 文件**在结束时全部字节摘要不变，正式 Agent、reported_experience_v2、B1/B2 和旧 Text Evidence 实现均未修改。新文件语法、空白检查及 `git diff --check` 通过。

复核测试命令：

```powershell
.venv/Scripts/python.exe -B -m unittest tests.test_p5_real_data_review_preparation -v
```

离线准备命令已完成，不需要重跑：

```powershell
.venv/Scripts/python.exe -B scripts/prepare_p5_real_data_review.py prepare
```

入口固定读取本地档案，不接受数据库/模型参数；目标目录已存在时拒绝运行。它不是可自动发送真实评论的任务入口。

## 9. 下一步最少操作与分别授权

1. **先核对八个契约映射的差异与隔离绑定方案**，复用原确认事实，不重复审核全部 48 组。当前路由没有等价目标；不能仅勾选“同意”就解除程序门槛，后续须有符合原约束的实验 Runtime 表示及真实可信绑定。不能等价保留的目标减少或排除，不能换成更容易的问题。
2. 只有等价目标进入人工四状态评审。用户在私有旁表独立填写对应事实行的状态、引用类型和充分原文，核对 Q18 必要关系、对象/时长及已知开发暴露边界；当前上限 27 行，**零行已确认**。Codex/待测 LLM 不替用户裁决，也不修订答案。
3. **数据库读取另行授权**：仅私有清单中最终保留的至多 20 个 review_id、同范围/profile、完整当前有效观点和上述来源字段；只读同快照，不读向量、不调用 BGE、不扫描全商品、不写库。先审查最小限 ID 适配设计及离线测试，再考虑实际读取；本轮没有此执行入口。
4. **模型发送与费用另行授权**：来源、Runtime 和独立人工标准全部冻结后，才可能按原 Text Evidence 契约组织至多 8 次、每次最多 5 候选的独立请求；当前为 0，旧合成实验授权不延伸到真实评论。明确批准真实评论/完整上下文及必要来源字段发送 SiliconFlow、零重试、留存/隐私和超时/usage 风险后才能运行；预期答案不能发送。

沿用前计划保存费率的条件上限：估算输入 ≤8000、输出参数 2500，每请求 ¥0.036，8 请求 ¥0.288；**未形成冻结输入，尚无本轮实际 token 估算或账单上限**。实际条数、费用与调用授权须在所有门槛通过后另行明确，不为覆盖不足或失败增加调用。

本阶段停在离线材料与契约阻断清单。没有自动进入数据库验证、真实模型评估或正式集成。
