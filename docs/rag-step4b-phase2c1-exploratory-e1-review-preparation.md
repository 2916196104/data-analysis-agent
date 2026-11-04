# RAG Step 4B-2c-1：探索性 E1 输入与人工充分性评审准备

本轮已完成 **48/48 个探索性输入和人工评审草稿**，保留 24 个原问题、222 个原排名条目、93 个候选观点、57 条评论及两个空列表。57 条评论全部仍为 `history_unproven`，没有正式历史重建通过结论。**未运行真实判断器、E1 预测或模型推理，未重新连接数据库；人工契约与充分性参考确认数均为 0。**

实验性质固定为 `exploratory_current_observation_history_unproven`。这批材料适合开始人工契约核对和充分性初评，不是独立泛化测试，也不是正式历史可信批次或语义质量验收结果。

## 1. 本轮范围及文件

已阅读 Step 4A 最终审查、Step 4B 开发计划、4B-2b 准备报告及 4B-2c-0c 执行报告，并检查 QueryContract、判断器、来源准备、人工评审协议和历史快照实现。沿用 reviews_csv / reviews-csv、商品 100013588055、UTC 2021-09-11 至 2022-08-26、BAAI/bge-m3、1024 维、quote-utf8-v1 与原 200 条索引边界。本轮没有读取或重新计算完整向量库存。

仅使用获准保存在本地的原归档、93/57 行级观察、两次采集回执、138 条完整模型观点元数据和既有审计摘要。没有读取 CSV、历史备份、新正文、人工观点/备注、向量、凭据或其他商品。没有重复 M1–M3。

新增公开文件如下，均为离线能力，不被业务路径接入：

| 文件 | 实际作用 |
|---|---|
| src/data_analysis_agent/reviews/exploratory_e1.py | 独立版本的来源投影、探索输入及未确认参考/初评视图生成 |
| src/data_analysis_agent/reviews/exploratory_e1_metrics.py | 未来对已独立审计记录的指标汇总；不运行预测、不推断证据支持 |
| scripts/templates/e1_review.html | 本地无外部资源的评审页模板；真实内容只写入私有目录 |
| tests/test_exploratory_e1.py | 完全合成的来源、身份、草稿、分母、错误处理测试 |
| tests/test_exploratory_review_page.py | 完全合成的页面脚本和导出/恢复检查 |
| 本报告 | 执行结果、人工流程、评价协议与剩余边界 |

**未修改任何既有文件**，包括 `query_contract.py`、`evidence_judge.py`、`e1_preparation.py`、`e1_review_protocol.py`、`historical_snapshot.py`、检索/排序、SQL、Agent、API 和前端。原严格 `prepare_trusted_batch()` 未改动，真实调用 0 次。

私有工作目录：`E:/AllProject/data-analysis-agent/outputs/rag-retrieval-quality/step4b-phase2c1-exploratory-v1-20261104/`。

**唯一交付入口为该目录的 `prepared-v1/review.html`**。根目录首次适配失败的材料保留供审计，不作为交付输入。目录内 README 给出人工操作步骤。

## 2. 冻结身份及跨时点来源限制

| 身份 | 值 |
|---|---|
| 原 run_id | `a2f4907ec8cacc6bae4b52f8d89a9ee557ae09f1f4e7a7f3c6f3803b34848f33` |
| manifest 规范 JSON 摘要 | `a17e77d4f7c5d83c5b1c80bad318b374432f5750a9151c826f451feb52b5c3f8` |
| scope 摘要 | `167e14701623dc709a96ce8761a84f7a89c8eaba9c34f6f71025e2dc89e7f766` |
| inventory 摘要引用 | `92e3c0ec0ff391e38110affb086def5e7b7a6ccd6412dea6f56cbe1133006ecd` |
| 原归档清单文件 SHA-256 | `f6afd216f987314de08b089ffadd8e9e6dcc867d79a11f40dc4771b775bf7b47` |

原 scope 摘要按项目既定的 `[scope, label_profile_key, embedding_space_id]` 计算，不是只对日期/商品字典计算。run_id 校验绑定原运行内容，所有列表保留原 query_id、mode、排名和候选数；原过滤条件另外保存在整理端映射，不进入盲评视图。

| 材料 | 原实际采集时间（UTC） | 已核对的文件 SHA-256 |
|---|---|---|
| 已存 93 个观点、57 条原文及版本字段 | 2026-11-04T02:15:48.466614+00:00 | `373ee19353dd3aa89c0aa55d0c7d20031af4e1e5e9a03e89eed1dc44bcf11295` |
| 已存 M1–M3 元数据 | 2026-11-04T06:46:15.772789+00:00 | `6f6806c5d458192bfc38f8101e6196e86bf94989f6e1828701b6c85bd1e080a5` |

两次原回执均记录只读、repeatable read 和 rollback；本轮只检查保存的回执，不执行事务。回执中的 transaction_snapshot 同为 `1000351:1000351:`，**相同可见事务字符串不能证明两个时点属于一个不可变快照**。本次生成时间另记 experiment.json，未冒充原采集时间。

交叉核对了 review_id、原 annotation_id、已保存正文的 SHA-256、两个时点的内容身份字段、published_at、标注 profile、状态/error_code/created_at，以及原 93 个观点的 ID、分类、区间与 quote SHA-256。旧原文重新计算其自身摘要；第二时点仅有获准元数据，没有重新读取该时点正文，不声称重新验证了当时全文。未发现这些已提供字段之间的冲突。

使用保存的 138 条模型观点元数据及已授权原文，按现有 historical_snapshot.py 重算 57 条观察性快照，均与既有观察结果及冻结值相同；45 个额外观点只参与完整集合/哈希核对，未加入候选或展示额外 quote。M3 仍为既有观察中的 0 行，不意味着历史上从未存在修订。

| 分类 | 本轮结果 |
|---|---:|
| rebuilt_match | 0 |
| rebuilt_mismatch | 0 |
| history_unproven | 57 |
| missing_source（适用的当前观察核对） | 0 |
| unverified（适用的当前观察核对） | 0 |
| 观察性摘要相同 | 57/57 |
| 具有独立历史证明 | 0/57 |

仍缺冻结时的事务/不可变历史状态、当时原 annotation 被选中的证明，以及中间成员增删或修订的历史依据。后来的元数据和哈希一致不填补这些缺口。本轮不再要求重复采集相同 M1–M3，也不把接受探索方案解释成批准历史匹配。

## 3. 独立探索输入及实际可用数量

新接口 `prepare_exploratory_input()` 使用 `e1-observed-sources-v1` 和验证政策 `local_saved_sources_cross_time_v1`。来源记录由已保存的独立 SQL 观察恢复；冻结 hit 用于身份和排序比对，不能复制 hit 充当来源。

校验包括：原 run/manifest/scope、inventory 身份引用、来源完整键集合、版本/范围/日期、原过滤条件、Top-K、候选身份、原文和 quote 摘要、字符区间、来源引用、重复来源、连续排名，以及两个采集时点与回执身份。调用方先核对原文件审计摘要，模块再校验投影；本地摘要仅用于完整性，不证明外部审批或历史可信性。

| 项目 | 实际结果 |
|---|---:|
| 唯一可用候选观点 | 93/93 |
| 可用关联原文 | 57/57 |
| 完整模型观点元数据 | 138（含候选外 45 条） |
| 探索性列表已适配 | 48/48 |
| 原排名条目已保留 | 222/222 |
| 空列表已适配 | 2/2：Q17-filtered、Q22-filtered |
| 适配失败/缺失 | 0/48 |
| 正式严格可信批次已准备 | 0/48；未尝试 |

`ExploratoryInput` 记录候选 EvidenceBatch、来源集合摘要、batch 摘要、上下文输入摘要及明确的 history_unproven/independent_seal=false；**没有 trusted_batch 字段**。没有创建正式 SourceCollection，没有外部固定摘要，也没有将自产摘要传入原严格接口自证。

已核对 242 个既有代码、报告、冻结文件和获准来源文件摘要未变化，其中包括原 12 个归档文件。原 0/1/2、备注、排名、模型空间和旧指标保持原样。本轮没有重新计算旧相关性指标。

## 4. 24 个查询契约草稿

`contracts.draft.json` 有 24/24 份可校验、可序列化的 QueryContract 草稿，版本为 rag-query-contract-v1；外层版本 e1-query-contract-draft-v1，标记 developer_assisted_query_only、pending_human_review、human_confirmed=false，绑定原问题摘要和 scope_ref。

草稿只依据原问题制定，不读旧评分或判断器输出来决定目标；保留 task_type、aspect_candidates 及依据、sentiment_intent、target_entity、subtopic、required_facts、polarity_structure、uncertainty/clarification、scope_ref。最终草稿中 23 份为 evidence，Q23 为 clarify：保留电池续航目标，要求澄清是在寻找评论反馈，还是判断设备在特定条件下的能力；不凭单条反馈直接许可产品能力结论。任务类型仍待人工确认，不将草稿当作自动解析结果。

未擅自给出硬过滤：所有 proposed_filters 的 aspect/sentiment 均为 null。Q01 保留售后对象，配送时效使用配送服务对象而非强行指向某名配送员；Q13 保留多维候选；Q21 没有强制沿用旧 effectiveness 维度。候选维度和已冻结检索过滤是两个对象，旧 U/F 条件不会随草稿改变。

Q03 的解释与实际解决、Q07 的原约定和反复后推、Q13 的夜间休息影响、Q18 的已到货和安装等待、Q21 的同评论总体满意与局部不满等条件均保留。没有把“噪声”改写成“影响睡眠”，也没有将复杂条件改写为现判断器能识别的简单句。OR/AND、先后、转折、否定范围、模糊时间及使用条件均有待核对项；规则不认识的措辞不因此删除。

`independent_target_fact_ids` 在外层 24 份中均暂为空，表示**尚未人工声明**，不是已确认“没有独立子问题”。评审页可提出独立目标 ID、接受草稿或要求修改的意见；不会自动改契约或标记已确认。复合核心条件只满足一部分，不能据此许可 limited。

原 Q01、Q09 等历史备注解释差异不自动裁决；Q13 过滤排除候选的事实不通过改分补救。原标签和备注不进入初评材料，人工充分性判断重新独立建立。

## 5. 人工材料、盲评和上下文

| prepared-v1 中的文件 | 用途 |
|---|---|
| experiment.json | 探索登记、采集版本、禁止预测/模型/数据库操作及待确认项 |
| observed-sources.json | 独立观察投影；93 条来源、57 条未证明快照和两次采集身份 |
| inputs.json | 48 份探索候选输入；原 query/mode/顺序不变 |
| contracts.draft.json | 24 份待确认契约 |
| human-reference.drafts.json | 48 份原人工协议格式的空白参考骨架 |
| operator-bindings.json | CASE 与 query/mode/原过滤/排名/输入及契约摘要对应关系，仅整理端使用 |
| reviewer-view.json、review.html | 48 项初评材料及本地页面 |
| evaluation-protocol.draft.json | 指标、分母、分组、错误与配对政策草案 |
| source-failures.json | 实际为空数组；不隐藏失败或删除候选 |

48 个骨架均包含必要事实、关系、独立目标、来源与上下文身份；reference_state=null，fact support=unknown，relation support=unknown，许可/缺失事实集合为空，确认和裁决记录为空。**这是程序建立的空白结构，不是独立人工参考已经建立**。

初评页优先展示原问题、目标事实、quote 和可展开的完整评论。CASE 顺序确定性打散，原候选排名不变；模式映射另存。初评载荷递归检查未嵌入原评分、distance、预测、reason_codes、模式名称或方案质量评价。原查询和不可更改范围仍可核对。隐藏字段不能消除评审人以往接触过标签的记忆，需本人声明接触情况。

计划为单人初评，评审人尚未填写；不声称双人盲审、独立裁决或已经确认。添加引用不会自动认定直接支持。页面导出/恢复均保持 human_confirmed=false，要求逐事实、必要关系、许可范围、缺失条件和理由由本人填写。

采用建议策略 B：`quote_full_review_veto`，context_version=1。完整评论只用于解释、限定或否决 quote，支持事实必须落在 quote 内；引用区间按 Unicode 码点而非 JavaScript UTF-16 单元处理。每个列表有 context_input_sha256。当前判断器源码的上下文行为与 B 相符，但本轮没有实际预测验证；未来如发现策略或版本不同，停止质量比较，不自动切换。

页面导出的是 `local-evidence-review-draft-v1`，其中引用为 key+区间的初评记录，**不是可直接用于评价的 confirmed HumanReference**。整理端需利用独立映射补齐完整引用身份，校验来源/事实/关系，处理契约修改及分歧，另存版本和真实人工确认记录。该正式转换/批准流程本轮未实现，不伪造确认人或确认时间。

## 6. 后续 E1 评价口径：已准备逻辑，未运行真实预测

定义 H 为来源有效且契约/人工参考均已确认的列表；V 为 H 中预测执行完成并通过输出验证的列表。应评范围始终为原 48 项，不按原相关性 answerable 的 14 题筛选充分性任务。所有指标报告分子、有效分母和未覆盖数，零分母为 N/A。

| 指标 | 分子 / 分母与规则 |
|---|---|
| 无支持场景回答许可率 | V 内人工不许可任何目标的列表中，预测许可目标的数量 / 该类列表数；sufficient、limited 都算许可 |
| 过度拒答率 | V 内人工许可至少一个目标的列表中，预测 insufficient 的数量 / 该类列表数；人工 limited 也计可答 |
| 三状态一致率 | V 中状态相同数 / V；附 reference×prediction 的 3×3 混淆矩阵及各状态分母 |
| 已许可事实实际支持率 | 经独立核对事实、具体引用及必要关系后得到支持的许可事实数 / 全部许可事实数；有未评映射时主率 N/A，另报已评支持率、审计覆盖、未评数量 |
| 引用有效率 | 来源、身份、quote 区间均有效的去重引用数 / 全部去重引用数；未知引用存在时主率 N/A，另报审计覆盖；零引用 N/A |
| 语义规则覆盖不足 | 单列 V 内未知覆盖数量、未知覆盖导致的过度拒答，以及状态虽一致但语义未知的数量；不等同于证据确实不足 |
| 缺失/错误 | 单列应评、可用记录、有效确认参考、V、缺行、缺参考、缺输出、来源失败、结构验证失败、执行失败；条件计数可能交叠，不相加冒充互斥类别 |

不能因为预测和人工都输出 insufficient 就断言规则已理解语义。对规则外措辞，应由后续输出审计保留无法识别的原因，并与人工确实缺乏支持区分。合成测试验证了“全部拒答”仍会显示可答问题的过度拒答，不能只展示安全侧收益。

`summarize_e1()` 仅汇总 `e1-preaudited-case-v1`：调用前必须独立核对真实参考、预测输入/输出身份、规则版本及具体事实来源映射。`audit_record_sha256` 是审计引用，不是人工批准证明；函数不会从原 0/1/2、事实 ID 相同或向量相似度推断支持。真实预测输出审计/引用映射执行器尚未开发，也未用空白草稿构造真实审计行。

支持事实的独立审计不能只看 ID：事实必须获人工许可，实际引用及关系也确实支持；尚无人判断的引用映射记未评。首轮建议 exact quote span 对照，其余合法区间如尚无人覆盖，单列未评，不自动视为错误或正确。

按原 manifest 的 aspect、sentiment 和待确认契约类别分组，分别报告 H、V 和各指标分母；草稿分组不当作新金标准。U/F 配对要求同 query、同契约、同观察来源版本、同上下文政策；两侧该指标均有效才报告 F-U，并给逐查询差值、配对 n 和排除原因。N/A 不填 0，不用两个独立组均值冒充配对效果。

**真实确认参考为 0/48，真实预测为 0/48，本轮不计算任何真实 E1 质量率或混淆矩阵。** 原 24 题已用于既有开发诊断，不是独立泛化验证集。后续独立验证集需要另行规划、授权和人工标注，不能扩大当前索引取样。

## 7. 实际测试、审计和早期错误

| 实际执行 | 最终结果 |
|---|---|
| 新来源/草稿/指标专项及页面检查 | **24/24 通过**，0 失败、错误或跳过 |
| 适用组合回归（含以上专项） | **201 项，199 通过、0 断言失败、2 错误、0 跳过**；退出码 1 |
| 真实已存材料适配与独立复核 | 48/48、222 条、93/57 来源通过本轮探索校验；两个空列表保留 |
| 旧文件保护 | 242 个既有文件 SHA-256 不变；12 归档文件包含在内 |
| 私有隔离与公开内容检查 | outputs 被 Git 忽略、无跟踪私有文件；新公开文件不含完整真实评论或具有辨识性的完整 quote |
| 页面验证层级 | Node 24.19.0 合成 DOM/脚本事件验证通过；未运行实际浏览器布局、截图或人工交互验收 |

专项覆盖跨时点与 history_unproven 保留、身份/范围/库存引用、重复/缺来源、区间/哈希、排名不可重排、空列表、无数据库/网络/判断器/旧严格接口调用、未确认草稿、初评字段隔离、导出恢复防篡改、Unicode 区间、分母 N/A、unknown 不伪装成支持、缺失/执行失败、limited 的双侧口径与 U/F 配对。

两项既有队列错误原样重现并保留：

- `test_review_scope.ReviewScopeTests.test_run_date_filters_incremental_skip_and_scoped_progress`
- `test_review_scope.ReviewScopeTests.test_run_retry_and_cutoff_forwarding_with_real_mocked_sdk`

原因仍是临时 SQLite 测试进入仅支持 PostgreSQL 的持久标注队列。未改原业务逻辑、未跳过或计为通过。测试驱动禁止真实网络及 psycopg 连接；模拟 SDK 的进度文字不代表调用了外部模型。24 项专项包含在 201 项中，不相加报告为独立测试数量。

本轮早期真实错误也保留说明：首次构建读取 RagOpinionDocument 不存在的 review_text 属性，未落地材料；改为使用授权保存的 SQL 原文。随后首版 scope 摘要约定不符，48 项显式失败；按项目既有 scope/profile/space 绑定修正，在独立 prepared-v1 中重新适配成功，未改原身份或来源。页面合成测试驱动最初的异步 eval 语法错误已修正；最终日志为实际重跑结果。失败材料未覆盖旧归档或被计为已准备。

实际主要命令（均在项目目录运行）：

```powershell
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/step4b-phase2c1-exploratory-v1-20261104/build_review_materials.py
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/step4b-phase2c1-exploratory-v1-20261104/run_checks.py final
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/step4b-phase2c1-exploratory-v1-20261104/finalize_materials.py
git diff --check
git status --short
git check-ignore -- outputs/rag-retrieval-quality/step4b-phase2c1-exploratory-v1-20261104
git ls-files -- outputs/rag-retrieval-quality/step4b-phase2c1-exploratory-v1-20261104
```

构建脚本独占创建 prepared-v1，已有目录不覆盖重跑；复核可以重复执行，只更新本轮新建的页面/审计材料。最终专项日志、组合回归、旧队列错误、材料审计及前后保护清单位于私有工作目录；没有执行前端构建、真实检索或真实 E1。

## 8. 新材料摘要与封存边界

以下为最终材料文件字节 SHA-256，不是外部固定或人员批准：

| prepared-v1 文件 | SHA-256 |
|---|---|
| observed-sources.json | `c6331fffd3c1ead27e763f84e5c3359ae62d67fd06215ce21acbb3df5e47c1fa` |
| inputs.json | `f79b811e79590d01c417bda2881dec601e4c8f308be135b986168e7e18e2585c` |
| contracts.draft.json | `5fefb0b07f6e03f5b6ce5cffbbb4659528769be48fef970025d6998c05429629` |
| human-reference.drafts.json | `e968e704483535b26c29e8e8085d34c6b71f314628ffa1fb7d367d668e7fd884` |
| reviewer-view.json | `2916fc7676ff315f5e3bbbd9f3df60943d237548fabd22111284949e54d11488` |
| review.html | `5b2a771526fb957bddcc311d8599247c83d4c80fc24b8b101726f286f35e210a` |

供类型化输入使用的来源规范 JSON 摘要为 `ecb19362428a75b55552d181688c6dff49a820e973b63a3274efef46276eea55`；48 输入规范 JSON 摘要为 `f14ec3941b20a08e4859b80b9f041d944f66733d397ebef5017c3a28f1e2f4a1`。它们与文件字节摘要用途不同，不混用。

本轮材料摘要清单只负责本地可复核性。历史证明、独立保管方签收、外部预期摘要及正式封存批准仍未完成，未填入外部 pin，也未宣称批准。

## 9. 人工初评与启动预测的剩余条件

**可以从本地材料开始契约核对和人工初评，无需新增数据库授权。** 当前来源适配没有缺失阻塞；仍待本人填写评审身份和历史标签接触声明，确认 B 的使用及评审操作，逐题确认原意、必要条件和独立目标。页面没有自动保存，需导出并将含私有数据的文件保存在本忽略目录，不能提交 Git 或传给外部服务。

在正式充分性参考用于比较之前，还需：

1. 同一 query 的 U/F 使用同一份人工确认契约。若修改契约，另存版本和摘要，并重审受影响事实，不能沿用过期参考。
2. 逐事实和关系建立直接支持/不足记录，限定 limited 的许可范围；参考在看到任何预测前独立完成并固定。单人流程如有分歧要记录，不能伪称已有第二人裁决。
3. 完成草稿整理、完整引用身份校验、真实确认记录及参考封存；明确源/范围/上下文错误的停止与发布规则。
4. 人工确认评价协议、可接受的无支持许可和过度拒答水平、limited 边界及独立验证集安排。**所有数值门槛仍待确认**；缺少门槛不妨碍人工初评，但不能宣布正式质量通过。

**目前不能直接启动探索性 E1 预测。** 除独立人工参考和后续明确授权外，还缺经审查的探索预测输入适配及输出审计流程。当前 candidate_batch 只是待测输入，不能原样冒充独立 trusted_batch。未来若批准使用当前观察材料，必须从另存的独立来源/回执核验预测输入，清楚保留 history_unproven；不得改原严格接口或制造外部摘要。该适配不要求反复补做相同 M1–M3，也不能宣称补齐历史证明。

冻结索引仅覆盖原 200/1091 条有效观点，短 quote、原标注边界、细分主题和规则覆盖限制继续存在。人工不足结论只适用于当前列表，不推出全业务无证据、总体比例、睡眠影响、长期耐用性或其他未直接支持的结论。

本阶段至此完成并停止。后续先由人工审阅契约和充分性材料，不自动运行预测、生成业务答案、进入 Agent 或扩大访问范围。
