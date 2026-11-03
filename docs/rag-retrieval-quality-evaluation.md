# RAG Step 4A：真实业务语义检索质量评估

日期：2026-11-03（Asia/Taipei）。真实检索产物保存时间：2026-11-03T01:54:46+08:00。

**自动化检索验收完成，真实语义质量等待人工评审，暂不能宣称质量验收通过。**

已完成固定 200 条索引的只读清点、24 条冻结问题的 48 组检索、222 个返回条目的证据复核及本地人工评审工具。人工标签仍全部空白，未替用户打分，Precision、Hit Rate、MRR、分组质量和无证据误召回指标均为 N/A。当前不建议进入 Agent 工具集成。

## 1. 范围与实际交付

读取了项目 AGENTS.md、Step 1～3 报告及语料、编码器、编码进程、存储、同步/验收脚本和相关测试。开始时 Git 工作区干净。只新增以下 6 个文件，既有业务文件没有改动：

| 文件 | 职责 |
|---|---|
| [retrieval_evaluation.py](../src/data_analysis_agent/reviews/retrieval_evaluation.py) | 冻结契约、范围拒绝、标签校验、纯离线人工质量指标 |
| [evaluate_retrieval_quality.py](../scripts/evaluate_retrieval_quality.py) | inventory / run / score，复用现有检索，输出本地私有评审产物 |
| [rag_retrieval_review.html](../scripts/rag_retrieval_review.html) | 无外部依赖的本地人工评分模板；可保存和恢复标签 |
| [rag-retrieval-v1.json](../evaluation/rag-retrieval-v1.json) | 24 条冻结问题、能力目标、过滤条件、逐题标准、固定范围和规则 |
| [test_retrieval_evaluation.py](../tests/test_retrieval_evaluation.py) | 25 项合成数据自动化测试 |
| 本文件 | 本阶段审查、真实证据、复现、人工待办及阶段结论 |

业务范围严格为 `reviews_csv / reviews-csv / 100013588055`，UTC `2021-09-11` 至 `2022-08-26`，含整天；模型身份为 `Pro/deepseek-ai/DeepSeek-V3.2`，提示版本 `review-opinions-v1-e61de599683cba6703c2f61e85125d0be4627c2763440486aa717e4bc884036e`。profile 固定为 `1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304`。base_url 从现有 LLM_BASE_URL 配置读取，经现有 model_identity 规范化和完整 profile 哈希核验，再由当前有效语料实际关联所选标注；没有猜地址或在报告中输出地址。

业务连接强制 `REPEATABLE READ READ ONLY`。没有业务 DDL、DML、同步、回填、模型标注、外部 LLM 或 Embedding API 请求，没有读取其他商品/数据集原文。为复核完整集合，现有语料接口仍在同一获批范围内读取 1,091 条当前有效观点；只检索已有 200 条向量，不给其他 891 条编码或输出它们的原文。

真实原文只存在本机被 Git 忽略的 `outputs/rag-retrieval-quality/baseline-v1/review.json` 和 `review.html`。发布产物使用字段白名单，命令行只输出数量和状态；标签备注不进入汇总指标。模型子进程环境和网络禁用策略沿用原实现。

## 2. 现有检索架构审查

调用链为：冻结问题 → `LocalEmbeddingProcess.encode()` → 已缓存固定 BGE-M3 → `search_effective_opinions()` → `read_effective_corpus()` → `current_reviews_sql()` → PostgreSQL 精确余弦排序 → 观点来源与元数据复核。

| 检查项 | 当前实现与结论 |
|---|---|
| 查询编码 | 与观点相同的 EmbeddingSpace；BAAI/bge-m3 固定修订 5617a9f61b028005a4858fdac845db406aefb181，1024 / float32 / CLS / L2 / cosine / 512 tokens；不加查询指令 |
| 文本契约 | 继续使用 quote-utf8-v1，未拼接维度或上下文，未修改 builder |
| 排序 | PostgreSQL `<=>` cosine distance 升序，距离相同按派生行 id 升序，LIMIT 5；没有第二套检索 SQL、ANN 或重排序 |
| 范围 | 复用 review_scope 的 source、dataset_version、product_ids、UTC 起止边界，并限定 profile、空间、builder |
| 维度和情感 | 参数化精确标签过滤，枚举为原六维度及四种情感；mixed 只表示评测意图，绝不作为数据库标签 |
| 来源及完整集合 | 来源键与每条评论有效集合摘要双重 JOIN；再逐项复核 review/annotation/模型或人工观点 ID、内容与输入哈希、字符区间、日期等元数据 |
| 陈旧证据 | invalidated_at 之外，还要求当前来源键和集合摘要匹配；人工确认、修订、删空或评论内容身份变化会使旧向量不可检索，不能等同步后才判断 |
| 原文 | 语料接口校验 Python 字符区间；本评测对所有返回条目再次读取获批商品原文，逐项检查切片和 UTF-8 SHA-256 |
| 事务与重复性 | 编码前关闭数据库连接；全部正式检索与复跑共享只读重复读快照；编码前、编码后及检索后对源状态/有效键/缓存元数据和向量摘要核对 |

检索保证候选的当前有效性，不保证语义相关性。当前没有拒答距离阈值；非空候选集通常会返回 Top-K，不能把“返回结果”解释为“证据足够”。过滤只保证标签相符，不能证明标签正确或观点回答了细分问题。默认读全范围契约及重复来源重验也意味着该耗时不能直接外推生产吞吐。

## 3. 真实 200 条索引分布

| aspect | positive | negative | neutral | unknown | 合计 |
|---|---:|---:|---:|---:|---:|
| quality | 2 | 16 | 0 | 0 | 18 |
| effectiveness | 25 | 30 | 0 | 1 | 56 |
| price | 4 | 5 | 0 | 0 | 9 |
| packaging | 0 | 1 | 0 | 0 | 1 |
| logistics | 9 | 20 | 0 | 0 | 29 |
| service | 8 | 79 | 0 | 0 | 87 |
| 合计 | 48 | 151 | 0 | 1 | 200 |

200 条全部当前有效、来源均为 model，显式失效及旧键/摘要失效计数均为 0；覆盖 105 个完整评论组，维度全部 1024，向量范数范围 `[0.9999998920, 1.0000001014]`。同范围有效观点 1,091 条，未索引 891 条，受限观点覆盖率约 18.33%，不是全商品评论覆盖率。

客服负向、物流和使用效果可作初步评测；正向质量只有 2 条、包装负向 1 条、包装正向/全部中性 0 条，无法形成稳定质量结论。索引是 Step 3 按评论完整分组截取的非随机样本，不能代表真实商品总体或所有用户提问分布。

## 4. 正式评分前冻结的问题与规则

查询集版本 `rag-retrieval-quality-v1`；人工标签版本 `human-relevance-v1`。

manifest SHA-256：`a17e77d4f7c5d83c5b1c80bad318b374432f5750a9151c826f451feb52b5c3f8`。

授权源/索引快照 SHA-256：`92e3c0ec0ff391e38110affb086def5e7b7a6ccd6412dea6f56cbe1133006ecd`。

首次正式 run_id：`a2f4907ec8cacc6bae4b52f8d89a9ee557ae09f1f4e7a7f3c6f3803b34848f33`。

问题在查看检索结果前基于分布冻结；Q17、Q22 是标签零覆盖诊断，Q23、Q24 是具体主题覆盖探针，均不预设存在真实证据。查询的相关性标准见清单逐题 criterion；本次未根据结果修改问题或挑掉不利排名。

| ID | 自然问题 | 考察能力 | 评测意图 | filtered 条件 |
|---|---|---|---|---|
| Q01 | 问了售后好几次都等不到回音，有这类反馈吗？ | 同义改写与回复时效 | `service/negative` | `aspect=service, sentiment=negative` |
| Q02 | 有人说客服说话不耐烦、服务态度差吗？ | 客服态度与回复速度区分 | `service/negative` | `aspect=service, sentiment=negative` |
| Q03 | 客服会耐心解释并帮忙解决问题吗？ | 正向服务 | `service/positive` | `aspect=service, sentiment=positive` |
| Q04 | 买完之后出了状况，一直没人跟进处理吗？ | 售后间接描述 | `service/negative` | `aspect=service, sentiment=negative` |
| Q05 | 下单后很快就收到货的评价有哪些？ | 正向配送同义 | `logistics/positive` | `aspect=logistics, sentiment=positive` |
| Q06 | 买家有没有抱怨迟迟收不到货？ | 配送延误 | `logistics/negative` | `aspect=logistics, sentiment=negative` |
| Q07 | 约好的送货时间总往后推，有人遇到吗？ | 承诺时点与间接表达 | `logistics/negative` | `aspect=logistics, sentiment=negative` |
| Q08 | 收到的机器有没有做工粗糙、零件不牢的问题？ | 质量与外观包装区分 | `quality/negative` | `aspect=quality, sentiment=negative` |
| Q09 | 用了不久就坏了或者发生故障的反馈有哪些？ | 耐用性与故障 | `quality/negative` | `aspect=quality, sentiment=negative` |
| Q10 | 有没有夸机器用料扎实、做工可靠的？ | 稀少正向质量 | `quality/positive` | `aspect=quality, sentiment=positive` |
| Q11 | 炎热时开机，屋里很快就凉下来了吗？ | 使用效果口语表达 | `effectiveness/positive` | `aspect=effectiveness, sentiment=positive` |
| Q12 | 开了很久还是不凉快，有这样的评价吗？ | 效果不足与否定 | `effectiveness/negative` | `aspect=effectiveness, sentiment=negative` |
| Q13 | 晚上休息会不会被机器的声音吵到？ | 噪声的间接表达 | `effectiveness/negative` | `aspect=effectiveness, sentiment=negative` |
| Q14 | 哪些反馈觉得花这笔钱挺值的？ | 价格口语表达 | `price/positive` | `aspect=price, sentiment=positive` |
| Q15 | 有人觉得售价偏高，或者刚买就降价了吗？ | 价格负向与售后混淆 | `price/negative` | `aspect=price, sentiment=negative` |
| Q16 | 到手时纸箱或外包装有没有破损？ | 极少包装样本 | `packaging/negative` | `aspect=packaging, sentiment=negative` |
| Q17 | 有没有专门夸包装完整、保护到位的评价？ | 标签层面零覆盖 | `packaging/positive` | `aspect=packaging, sentiment=positive` |
| Q18 | 货到了却迟迟没人来安装，这是哪类反馈？ | 安装与物流跨维度混淆 | `service/negative` | `aspect=service, sentiment=negative` |
| Q19 | 客服并没有让人久等，回答还挺及时，有这种说法吗？ | 否定负向措辞 | `service/positive` | `aspect=service, sentiment=positive` |
| Q20 | 不是制冷快，而是根本降不下温，有没有类似抱怨？ | 否定句极性 | `effectiveness/negative` | `aspect=effectiveness, sentiment=negative` |
| Q21 | 总体不错但有些地方又让人不满意，能找到这样的反馈吗？ | 混合感情与短quote上下文 | `effectiveness/mixed` | `aspect=effectiveness` |
| Q22 | 只陈述使用情况、不带明显褒贬的评价有哪些？ | 中性标签零样本 | `effectiveness/neutral` | `aspect=effectiveness, sentiment=neutral` |
| Q23 | 这款设备的电池续航能撑一天吗？ | 无已知证据/超出产品主题 | `effectiveness/unknown` | `aspect=effectiveness` |
| Q24 | 有没有提到机身生锈或金属腐蚀的情况？ | 具体主题覆盖探针 | `quality/negative` | `aspect=quality, sentiment=negative` |

每个问题都跑 unfiltered 和 filtered。后者是诊断对照，使用冻结的手工条件，并非实现了自然语言意图识别或自动过滤策略。Q13 等疑问句本身可存在极性解释空间，本版按冻结 criterion 的目标极性评分；若人工认为问法有歧义，应在备注中记录，后续另建版本，不能事后改 v1 提分。

## 5. 查询级真实检索结果

48 组查询共返回 222 个排名条目：无过滤 120、有过滤 102；去重为 93 条观点、57 条评论。每组按返回 Top-5 前缀检查 Top-1/3/5，所有 222 条的原文切片、输入哈希和来源一致；48 组同快照复跑的完整结果均完全一致。每条记录保留实际距离、维度、情感、评论/标注 ID、来源键和证据引用。

本机 CPU 一次加载模型约 4,419.08 ms，24 问批量编码约 2,022.97 ms，模型进程峰值 RSS 约 1.70 GiB，网络连接尝试 0。首次检索耗时中位数 113.575 ms、最大 141.8 ms，包含逐次语料复核；这不是压力或生产延迟验收。

下表单元格为“返回数 / Top-1 review_id / 实际 aspect / 实际 sentiment / distance”。**这些是检索事实，相关性均未人工评分。** 完整 Top-5 明细位于本机 summary.json；原文只在本地评审文件中。

| ID | 无过滤 | 有过滤 | 相关性 |
|---|---|---|---|
| Q01 | 5 / 9948 / service / negative / 0.198403 | 5 / 9948 / service / negative / 0.198403 | 待人工 |
| Q02 | 5 / 10001 / service / negative / 0.251027 | 5 / 10001 / service / negative / 0.251027 | 待人工 |
| Q03 | 5 / 9912 / service / negative / 0.272953 | 5 / 9910 / service / positive / 0.327640 | 待人工 |
| Q04 | 5 / 9975 / service / negative / 0.242018 | 5 / 9975 / service / negative / 0.242018 | 待人工 |
| Q05 | 5 / 9949 / logistics / positive / 0.176854 | 5 / 9949 / logistics / positive / 0.176854 | 待人工 |
| Q06 | 5 / 9940 / logistics / negative / 0.169018 | 5 / 9940 / logistics / negative / 0.169018 | 待人工 |
| Q07 | 5 / 9958 / logistics / negative / 0.175138 | 5 / 9958 / logistics / negative / 0.175138 | 待人工 |
| Q08 | 5 / 10001 / quality / negative / 0.261981 | 5 / 10001 / quality / negative / 0.261981 | 待人工 |
| Q09 | 5 / 9921 / quality / negative / 0.194175 | 5 / 9921 / quality / negative / 0.194175 | 待人工 |
| Q10 | 5 / 9910 / quality / positive / 0.321418 | 2 / 9910 / quality / positive / 0.321418 | 待人工 |
| Q11 | 5 / 9978 / effectiveness / negative / 0.299889 | 5 / 9910 / effectiveness / positive / 0.321325 | 待人工 |
| Q12 | 5 / 9960 / effectiveness / negative / 0.211143 | 5 / 9960 / effectiveness / negative / 0.211143 | 待人工 |
| Q13 | 5 / 9908 / effectiveness / positive / 0.237729 | 5 / 9941 / effectiveness / negative / 0.298277 | 待人工 |
| Q14 | 5 / 9901 / price / positive / 0.252035 | 4 / 9901 / price / positive / 0.252035 | 待人工 |
| Q15 | 5 / 9926 / price / negative / 0.249067 | 5 / 9926 / price / negative / 0.249067 | 待人工 |
| Q16 | 5 / 9946 / logistics / positive / 0.336135 | 1 / 9927 / packaging / negative / 0.403106 | 待人工 |
| Q17 | 5 / 9910 / quality / positive / 0.329853 | 0 / N/A | 待人工 |
| Q18 | 5 / 9963 / service / negative / 0.096752 | 5 / 9963 / service / negative / 0.096752 | 待人工 |
| Q19 | 5 / 9949 / logistics / positive / 0.211885 | 5 / 9953 / service / positive / 0.322369 | 待人工 |
| Q20 | 5 / 9934 / effectiveness / negative / 0.165662 | 5 / 9934 / effectiveness / negative / 0.165662 | 待人工 |
| Q21 | 5 / 9921 / quality / negative / 0.311064 | 5 / 9941 / effectiveness / negative / 0.319509 | 待人工 |
| Q22 | 5 / 9986 / service / negative / 0.466334 | 0 / N/A | 待人工 |
| Q23 | 5 / 9908 / effectiveness / positive / 0.346263 | 5 / 9908 / effectiveness / positive / 0.346263 | 待人工 |
| Q24 | 5 / 10002 / quality / negative / 0.344309 | 5 / 10002 / quality / negative / 0.344309 | 待人工 |

编码前后及检索后的授权源/索引摘要完全一致。此结论仅覆盖本次获批范围及读取窗口，不宣称校验了其他商品或七张源表的全库内容。

## 6. 人工评审机制与执行状态

打开 [本地评审页面](../outputs/rag-retrieval-quality/baseline-v1/review.html)。页面包含 24 问、222 个检索排名、实际标签、距离、观点、必要原文及字符区间；可填写 2/1/0 和备注、保存/恢复 JSON，没有后端服务或外部请求。

1. 每题选择：有可判断的相关证据 / 无已知相关证据 / 样本不足 / 未评审。若只见 Top-5，不能把“未看到相关证据”当成完整 200 条没有相关证据；不确定就保留未评审或样本不足。
2. 对两种检索的每个返回条目评分：2 为直接回答、主题与事实及意图极性一致；1 为主题相近但信息不足；0 为不相关、主题/极性冲突或证据不支持。判断标签有误时保留原数据并备注 B 类问题。
3. 填写评审人；只有确实由本人完成的评分才勾选人工确认，点击保存标签。可分批保存和恢复，空白标签不会补零。
4. 将下载的 labels.human.json 放入本次 baseline-v1 本地目录，执行下面的 score 命令。

当前 labels.template.json 的 222 个 grade 均为 null、24 题均为 unreviewed、human_confirmed=false；不存在已获用户确认的相关性标签。浏览器自动操作只对合成副本打过测试分，文件位于 output/playwright 的 synthetic 目录，没有改动真实模板或产生真实人工标签。

## 7. 指标口径与当前值

| 指标 | 分子与分母 / 适用条件 | 当前 |
|---|---|---|
| Precision@1/3/5 | 人工 2 分条数 / k；仅人工确认且问题可回答，前 min(k,返回数) 条全部评分；不足 k 的缺位计 0 | N/A，适用查询分母 0 |
| 部分相关@1/3/5 | 人工 1 分条数 / k，单独展示，绝不算作 2 分相关 | N/A |
| Hit Rate@5 | Top-5 有至少一条 2 分的查询数 / 完整评分且可回答的查询数 | N/A |
| MRR@5 | 首个 2 分排名倒数的均值；可回答且已完整评分但未命中取 0 | N/A |
| 按 aspect / sentiment | 按冻结查询意图分组、分别保留两个 mode 的适用查询分母；每条命中仍保留实际标签和人工 grade | N/A |
| 有无过滤差异 | 只比较同一问题、两种 mode 对该指标都可用的配对差值，保留配对分母 | N/A |
| 无已知相关证据的返回情况 | 单独统计人工确认 no_known_relevant 的候选返回率及 0/1 分数量；不是模型错误概率 | N/A |
| Recall@K | 本工具只收集 Top-5 判断，未收集完整 200 候选 qrels，始终明确不适用 | N/A |

空结果只有在人工确认问题可回答时才产生 0 分质量指标；未评审、无已知相关证据、样本不足不能混算为 0。未勾选人工确认，即使填了分数也不发布质量指标。标签必须精确匹配 query hash、run_id、scope hash 和 label version；跨范围、跨运行、旧标签、重复/缺失条目、非法类型或超界分数均拒绝。

## 8. 候选失败案例及原因区分

以下仅是有证据的检索现象和待评审归因，**没有自动生成相关性分数或断言它们已被用户判错**。

| 类别 | 证据 / 当前能证明的事实 | 可能原因与可验证方向 |
|---|---|---|
| A 语义匹配 | Q03 正向服务查询的无过滤 Top-1 为 review 9912/service/negative（0.272953）；Q11 正向降温 Top-1 为 9978/effectiveness/negative（0.299889）；Q19 Top-1 为物流正向 | 主题相似可能压过极性/否定，也可能是问法歧义或标签不准；先按原文和冻结标准人工评分，再比较过滤前后。不能仅凭标签冲突归罪模型 |
| B 语料/标签 | 200 条全部来自既有模型标注，没有真实人工修订；标签和当前证据版本一致只证明存储一致 | 安装与物流、质量与包装边界及短 quote 上下文需人工核查；Q18 为安装/物流区分，Q21 展开原文核对混合情感。本轮不改标签或 builder |
| C 索引覆盖 | 891 条有效观点尚未索引；Q10 filtered 仅 2 条、Q14 4 条、Q16 1 条、Q17/Q22 为 0 | 能证明受限候选覆盖不足，不能证明这些具体问题的相关观点必在 891 条里。扩大索引和更有代表性的抽样须另行批准，当前没有回填 |
| D 过滤/排序/验证 | 48 组复跑一致，222 条证据有效，带过滤结果标签均满足条件；Q17 包装正向和 Q22 中性严格过滤为空 | 当前未发现范围、排序或陈旧证据泄漏。Q21 限定 effectiveness 等手工条件可能过窄；标签质量不佳也会过滤掉语义证据，需人工对照，不能用 filtered 结果替代自动路由验收 |
| E 评审不足 | 222 个人工分数全为空；Q23 电池续航、Q24 生锈等只是覆盖探针 | 尚不能确定语义误召回数量、是否有高位不相关、是否否定理解失败，也不能计算真实 P/MRR。先完成用户评审，再按 A～D 分类，必要时保留 E |

Q16 的无过滤 Top-1 实际为物流正向（review 9946，0.336135），有过滤才返回唯一包装负向候选（review 9927，0.403106）。低距离并不保证主题与极性符合问题，这正是需要人工相关性判断的原因。Q13 无过滤首位为使用效果正向也只能作为极性/问法核查线索。

## 9. 自动化验证与实际结果

| 检查 | 实际结果 |
|---|---|
| 新评测专项最终版本 | **25/25 通过**，无跳过；手算指标、空/缺标签、非法标签、Top-K、冻结版本/范围、索引变化停止、编码器空间、陈旧证据拒绝、异常不泄露、输出目录和 HTML 转义 |
| 既有评论 + 语料 + 编码契约 + 当时新增评测 22 项的组合回归 | **355 项：345 通过、10 跳过、0 失败/错误**；最终另补的 3 项已包含在上述 25/25 中，不把两组重复测试相加成独立用例数 |
| 原向量存储 + 原 PostgreSQL 集成 | **28 项：27 通过、1 失败、0 错误/跳过**；其中原存储专项 20/20 通过 |
| 独立模型环境原编码器专项 | **12/12 通过、无跳过**；补验了组合回归中因业务环境没有模型依赖而跳过的 7 项 |
| 本地真实 pgvector 检索 | 24 问 / 48 组 / 222 命中全部通过来源和区间复核；48 组重复一致，模型连接尝试 0 |
| 评审页面 | Chrome + 项目已有 Playwright，1440/1024/768 三种宽度无横向滚动；真实清单 24 问/222 行/246 个选择控件；合成标签保存和恢复通过，页面错误 0、HTTP 请求 0 |
| 其他检查 | 3 个新增 Python 文件语法解析、CLI help、git diff --check 通过；没有业务前端改动，未运行业务前端测试/构建或业务页面验收 |

组合回归跳过的另外 3 项为 test_review_agent_postgres.py 的扩展统计一致性测试，当时未配置该进程的隔离管理地址；本轮独立 28 项 PG 集成不包含这 3 项，不能称其已补跑。模型环境 12 项中有模拟编码器防护测试，不能将 12/12 本身冒充真实模型语义质量；真实推理证据来自本轮 24 问的模型进程和 PG 查询。

唯一失败为既有 `test_postgres.PostgresTests.test_task_history_and_live_queue_worker`，普通 analysis Worker 调用链出现 TypeError，任务最终 failed。对照源码，`jobs/worker.py` 普通 analysis 分支将包含 conversation_id 的参数传给不接受该参数的 `reviews/monitoring.py::run_analysis()`，与 Step 3 记录一致。本轮未修改 Worker 或 run_analysis，不宣称全套回归全绿。

最初受限环境不能连接业务库，组合回归在异步套接字初始化停住，已中止该次运行并用正常本机权限重跑。隔离实例原 55439 被非 PostgreSQL 代理连接占用；备用启动一度被自动审批拦截。只读核对无 postmaster.pid、pg_ctl 确认停止且占用者为 verge-mihomo 后，批准在 127.0.0.1:15439 启动相同测试目录，合成测试完成后已停止。没有更改业务端口或服务。

## 10. 复现及人工待办

以下从仓库根目录执行。业务数据库与 base_url 通过原有配置读取，命令不传或打印连接串/密钥。

```powershell
# 只读清点。会更新本地 inventory.json，不更新业务库。
.venv/Scripts/python.exe -B scripts/evaluate_retrieval_quality.py inventory

# 使用新的本地目录重跑冻结问题；源/索引快照变化会拒绝，不自动改 manifest。
.venv/Scripts/python.exe -B scripts/evaluate_retrieval_quality.py --output outputs/rag-retrieval-quality/reproduction-v1 run

# 仅在用户保存并确认真实人工标签后执行。
.venv/Scripts/python.exe -B scripts/evaluate_retrieval_quality.py score --labels outputs/rag-retrieval-quality/baseline-v1/labels.human.json

# 新专项。
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_retrieval_evaluation.py -v
```

run 拒绝覆盖已有 review.json。重复实验的 run_id 可能因耗时变化不同；复现排名应比较同一 manifest/snapshot 下各 query/mode 的 key/rank/distance 和 query_vectors_sha256，不要求耗时或整个运行哈希一致。score 纯离线，不再连接数据库；其结论只属于冻结的检索快照，不保证标签完成时证据仍是最新版本。

组合回归按以下实际范围构造，LABEL_EXECUTION_MODE 只影响当前测试进程：

```python
import os, unittest
os.environ['LABEL_EXECUTION_MODE'] = 'local'
loader = unittest.TestLoader()
suite = loader.discover('tests', pattern='test_review*.py')
for pattern in ('test_rag_corpus.py', 'test_embeddings.py', 'test_retrieval_evaluation.py'):
    suite.addTests(loader.discover('tests', pattern=pattern))
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
```

独立模型环境执行 test_embeddings.py 时，在 Python 入口先 sys.path.insert(0, 'src')，设置 HF_HUB_OFFLINE=1、TRANSFORMERS_OFFLINE=1，再以同样 unittest discover 方式运行。PG 集成使用现有已初始化隔离实例和显式 TEST_PGVECTOR_ADMIN_URL / TEST_POSTGRES_ADMIN_URL，只运行 test_embedding_store.py 与 test_postgres.py；这些测试会在隔离实例创建/清理唯一命名的合成数据库，绝不能将 TEST 配置指向业务实例。原实例操作与安全核验参见 [Step 3 报告](rag-pgvector-storage-acceptance.md)。

本轮命令和产物还包括本地 `node outputs/rag-retrieval-quality/verify-review.mjs`，以及用 labels.template.json 实际运行 score，得到 awaiting_human_review、全部质量指标 N/A。浏览器脚本、截图、合成标签及运行日志均在忽略目录，不作为业务前端变更提交。

## 11. 证据 → 发现 → 下一步

| 证据 | 本机来源 / 复现 | SHA-256 |
|---|---|---|
| E01 冻结设计 | evaluation/rag-retrieval-v1.json；run 的 --manifest 默认输入 | `bb526d31bfe9a91576a4e7c070628c8863d853a1dbbd07bfd204f53ea7c977e8` |
| E02 真实检索与来源 | outputs/rag-retrieval-quality/baseline-v1/summary.json；上文 run 命令 | `713ef7aea6b1c17294898a7b2a4604a8dae591707c946020c91c08aa45d9cac6` |
| E03 新专项 | outputs/rag-retrieval-quality/evaluation-tests.log；上文专项命令 | `e47e6d2c5cda749ee5f99e8bf3f2084c44b68ca60f940a0f1c0181f05c637410` |
| E04 原 PG 回归及失败 | outputs/rag-retrieval-quality/postgres-tests.log；上述隔离 PG 测试范围 | `38f4ad162fe96e2477114c6c607c156be76a601f590374bd6020966c36e34919` |
| E05 页面交互 | output/playwright/rag-retrieval-quality/verification.json；本地 verify-review.mjs | `5b748e2c094fc5eb7e1317467dbde0d405381db15bc4b9fd78822f59c853a5bc` |

F01（已验证，E01/E02）：来源和运行链路在授权 200 条范围通过 → 保留当前空间和检索实现，进入人工评审路径。

F02（已验证覆盖事实，语义归因待判断，E02）：正向/包装/中性样本不足，且索引仅覆盖 18.33% 的有效观点 → 先报告受限基线；是否扩大/重抽样单独申请，不在本轮自动处理。

F03（尚未人工验证，E01/E02）：标签冲突和跨维度候选提示语义风险 → 用户评分，按 A～E 分类，再提出可验证改进实验；不直接改模型、builder 或标注。

F04（既有失败复现，E04）：普通 Worker 参数不兼容 → 单独阶段修复，不与本阶段质量验收混同。

## 12. 与 SQL 的边界及下一阶段条件

现有 SQL 继续负责全范围评论数、标注覆盖、观点数量、分母、比例、趋势及确定性统计。这里的 Top-K 是受限索引的候选证据；既不是全商品统计，也不证明某个问题在完整业务语料中的频率或不存在。没有接入 Supervisor、Insight Agent、API、队列或前端，没有实现完整 RAG 问答。

建议顺序：先由用户完成/确认人工标签并计算基线；再审阅高位极性错误、跨维度和标签边界；随后决定是否另行批准更具代表性的索引覆盖实验及拒答/过滤策略。需要增加上下文、新 builder、重建向量或更换模型的建议，都必须另立实验，不能在本阶段执行。

候选验收门槛供人工确认：范围/来源/旧证据拒绝必须通过；目标问题要有明确的人工评分及适用分母；无证据场景必须单独复核；业务方在看到基线后确认可接受的相关性与误召回门槛；既有 Worker 失败单独处理。当前不设缺乏依据的“生产合格”百分比。

**当前结论：具备开展受限真实语义人工评审的条件，尚不具备宣称真实语义质量通过或进入 Agent 工具集成的依据。本阶段停止于评测交付和人工待审。**
