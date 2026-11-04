# Step 4B-2c-1b：人工评审一致性审计与正式参考准备

本轮已完成原始导出备份、48 CASE 一致性审计、24 查询的共享契约建议和 diff、人工复核清单及待确认参考材料。**48/48 通过草稿结构校验，但目前不能冻结正式人工参考：7 个 CASE 存在状态/许可/缺失字段逻辑冲突，另有关系、理由和契约意见未解决。正式转换条件满足数为 0/48，实际正式转换、确认和真实 E1 预测均为 0。**

本报告审计现有人工记录，不重新给 222 条候选打相关性分，也不替代人工裁决争议语义。候选来源/区间合法、字段格式合法和人工语义判断正确是不同结论。

## 1. 输入、备份与保护

已完整阅读上一阶段探索准备报告和 e1_review_protocol.py、exploratory_e1.py、exploratory_e1_metrics.py，逐项读取当前 prepared-v1 的 operator-bindings.json、contracts.draft.json、human-reference.drafts.json、输入和来源材料，并与原冻结 run 对照。

| 项目 | 实际身份 |
|---|---|
| 原导出 | outputs/rag-retrieval-quality/baseline-v1/evidence-review-draft (3).json |
| 文件 SHA-256 | `d5e97d4c4726f689c4a474c1853b6fabd8f6864d6eff03b763a990ce819dc009` |
| 评审格式版本 | local-evidence-review-draft-v1 |
| 原导出时间（UTC） | 2026-11-04T11:53:39.702Z |
| source_input_sha256 | `f14ec3941b20a08e4859b80b9f041d944f66733d397ebef5017c3a28f1e2f4a1` |
| 原 run_id | `a2f4907ec8cacc6bae4b52f8d89a9ee557ae09f1f4e7a7f3c6f3803b34848f33` |
| 原 manifest 摘要 | `a17e77d4f7c5d83c5b1c80bad318b374432f5750a9151c826f451feb52b5c3f8` |
| 实验性质 | exploratory_current_observation_history_unproven |

私有目录为 `E:/AllProject/data-analysis-agent/outputs/rag-retrieval-quality/step4b-phase2c1b-human-review-audit-20261104/`。原始文件逐字节复制到 backup/evidence-review-draft.original.json，备份摘要与上表相同；backup-receipt.json 记录实际备份时间、原导出时间、评审版本及全部 CASE→query/mode 绑定。两种时间没有混用。

原导出、旧阶段材料和 Step 4A 均未覆盖。已固定并复核 247 个既有文件，包括 12 个冻结归档文件、业务代码、既有报告、原评审页和本轮使用的私有输入。私有材料受 Git 忽略、未跟踪；本轮公开新增仅为本报告。没有读取新数据库记录、向量、CSV 或额外历史备份。

## 2. 48 份初评的完整性

| 校验 | 结果与分母 |
|---|---|
| CASE 完整且唯一 | 48/48 |
| 对应原 query-mode | 48/48，24 个查询各 U/F 一份 |
| 原候选、顺序及过滤条件 | 222/222 排名条目与原冻结 run 一致 |
| 唯一候选观点/关联评论 | 93 / 57 |
| 人工事实引用 | 49/49 可追溯至当前列表，区间均位于相应 quote 内 |
| 草稿结构校验 | 48/48，通过现有协议的未确认草稿路径 |
| 当前源身份/区间错误 | 0；不代表已核实每条引用的语义蕴含 |
| 历史状态 | 57/57 继续 history_unproven |

222 是全部检索排名条目数，49 是人工逐事实引用次数，两者分母不同。保留两个空候选列表，未静默剔除。源码校验只核对确定性来源、记录身份和人工已填条件，不运行判断器推断语义。

来源记录来自此前独立采集的本地观察。已比对观点键、来源版本、正文、字符区间、quote UTF-8 摘要、来源引用及原候选身份，绑定原 scope、inventory 摘要和两次真实采集回执。业务 review_content_sha256 覆盖规范化复合字段，不能当作正文 UTF-8 摘要；本轮不补读已脱敏字段重算该业务摘要，仅核对已有版本身份，并单独记录原文 UTF-8 摘要。此边界详见 source-validation.json。

## 3. 原始状态及契约决定分布

| 原填写状态 | 数量 / 分母 |
|---|---:|
| sufficient | 18/48 |
| limited | 0/48 |
| insufficient | 30/48 |
| 未选择 | 0/48 |

| 契约初评意见 | 数量 / 分母 |
|---|---:|
| accept_draft | 32/48 |
| needs_revision | 7/48 |
| pending | 9/48 |

另有 4/70 个事实 support=unknown、4/20 个必要关系 support=unknown。单人评审代号已填，reviewer_count=1；既往相关性评分接触声明仍为 null。页面原本就导出 human_confirmed=false，该字段本身不是漏填，不要求人工直接修改 JSON 为 true。accept_draft 也不等于正式批准。

以上是原始填写分布，不是本轮认可其充分性结论后的金标准分布，不能用 18/48 表示回答准确率。

## 4. 逐 CASE 审计口径和严重程度

P0 用于来源或身份违规，本次未发现。P1 表示冻结阻塞，包括字段逻辑、未完成条件、共享契约或配对一致性问题；P2 表示理由、记录可追溯性需要澄清。所有问题均提出建议并等待确认，没有自动改写原判断。

| 统计口径 | 实际数量 |
|---|---:|
| 状态/许可/缺失字段逻辑冲突 | 7 个 CASE |
| 完全相同输入的 U/F 判断冲突 | 2 组查询：Q05、Q07 |
| 至少一个 CASE 级发现 | 24/48 |
| 按最高严重度汇总 | P1 22、P2 2、未发现 CASE 级问题 24 |

各类别有重叠，不能相加。未发现问题的 24 份仍未确认，也未被自动评定为语义正确。24 份待确认契约和全局评审声明另行检查，不包含在“无 CASE 级问题”结论内。

### 4.1 七个字段逻辑冲突

| CASE / 查询 | 原判断及冲突 | 待确认修订建议 |
|---|---|---|
| CASE-004 / Q20 | sufficient；f0 未直接支持，必要关系未完成或未支持 | 优先确认 Q20 共享契约，然后重审，不在旧契约下直接放行 |
| CASE-009 / Q20 | sufficient；f0 partial、必要关系 unknown，许可集合为空 | 同上；状态、许可和缺失集合要与确认后的契约一起复核 |
| CASE-022 / Q14 | sufficient 且许可 f0，但 f0=unknown，理由却称直接支持 | 本人确认逐事实 support 或修改理由、许可和状态，程序不自动改 direct |
| CASE-031 / Q09 | insufficient、f0 partial，却许可 f0 且缺失为空 | 若保留现有逐事实与状态，建议清空许可并补列 f0 缺失；不自动升级 partial |
| CASE-035 / Q22 | insufficient、f0 not_supported，却许可 f0 | 不能保留无效许可；若保留原状态和事实判断，建议许可为空、缺失 f0 |
| CASE-036 / Q22 | 空候选、f0 unknown、insufficient，却许可 f0 | 当前列表没有支持引用，不能许可 f0；unknown 的处理须人工确认，不推断全库不存在 |
| CASE-046 / Q05 | insufficient，但 f0 direct、许可 f0，备注写 sufficient | 要求本人复核是否应为 sufficient；不代替修改 |

CASE-046 已有人工引用的直接快速到货陈述。其与 CASE-029 的问题、契约、候选、顺序及上下文完全一致，两者状态不同必须复核。另有从安装完成日期推定收货上界的材料，其推断是否算直接支持仍待确认；无需依靠该推断才能提出 CASE-046 状态冲突。

条件式修改路径和建议值保存在 case-field-proposals.json。所有建议 applied=false，分别标记是否需要新的语义判断和本人确认。自动检测出的集合期望值不能代替确认后的人工答案。

### 4.2 Q07 跨模式关系

CASE-023（U）和 CASE-034（F）候选完全相同，f0/f1 都标 direct、f2 都标 partial，最终都选 insufficient；冲突发生在 relation_1：U 为 supported、F 为 not_supported。

原契约中 relation_0 是 same_review(f0,f1,f2)，relation_1 是 before(f0,f1)。f2 的反复延期不足不自动否定 f0/f1 的先后关系，也不能把“同一条评论”物理身份与“全部必要事实联合支持”混为一谈。

CASE-023 的 relation_1 理由复制了“未提供定义、无法判断”的说明，与其 supported 字段及 review_notes 的 before 说明不一致；CASE-034 的 relation_1 字段为 not_supported，理由却明确写其 before 得到支持。两项原字段、理由、同一来源键/区间及条件式建议已进入私有审计材料；没有自动把任一字段改为 supported。原引用区分一次后推与随后提前的理由保留，不将其自动升级为反复后推。

### 4.3 CASE-012 理由与 any_of

CASE-012 对 f0 标 not_supported、对 f1 标 direct，但两项 rationale 完全相同，都在说明为何不能证明商品售价偏高，没有解释 f1 的购后降价支持。review_notes 与 f1 来源引用另有与降价有关的说明，应由本人统一到正确事实的理由。

该查询原本就是 any_of。若 f1 直接支持经确认，f0 未支持不构成强制改 insufficient 或把 missing 设为 f0 的理由；当前 missing=[] 与已完成 any_of 的协议口径一致。本轮没有因此重新裁定其状态，也不将评论中对降价的反馈当作已独立核实的历史价格数据。

### 4.4 其他复核事项

CASE-005、022、036 的事实仍有 unknown；CASE-004、005、009 的必要关系仍有 unknown。空候选的“当前证据未支持”与“全量不存在”必须区分；若人工保留 unknown，则记录仍不能通过正式参考的完整性门槛。

CASE-030 的 contract_notes 混入整页旧表单，其中包括“待人工评审”等过期内容。已保留原文，建议明确真实契约意见，不把粘贴页面推断为正式确认记录。

完整的 48 份原判断、问题、严重程度、修改字段、原始字段依据、引用键/区间与是否需重新人工判断，见 case-findings.json、case-review-tasks.md。未发现问题的 CASE 也有独立条目。

## 5. 24 份共享查询契约：提案与 diff

每个 query_id 只生成一个主提案，U/F 共同引用其 proposal_sha256。24 份均为 pending_user_confirmation、human_confirmed=false、confirmation_record=null。保留原问题及摘要、scope_ref、原 U/F 过滤和原排名，建议过滤仍为空。

| 查询 | 重点建议；均待确认 |
|---|---|
| Q03 | 保留“并”、all_of 和 same_review；解决两模式独立目标建议不一致 |
| Q05 | 明确直接到货时效与安装日期推断的边界，先复核 CASE-046 |
| Q07 | 保留三个事实和反复后推要求，明确两个关系的不同作用，不设置新数值门槛 |
| Q08 | 保留已有 any_of，提出共享独立许可 ID；不按备注中的假设改为 all_of |
| Q09 | 澄清是否包括随附配件、到货即坏；不擅自扩大设备范围 |
| Q10 | 保留 all_of 的完整要求，提出可独立许可的用料/做工目标，不自动改为 any_of |
| Q13 | 为“会不会”提出正反替代目标，保留夜间及休息条件；不由声音大小推断睡眠 |
| Q15 | 保留 any_of，提出共享独立许可 ID，区分售价与安装附加费用 |
| Q17 | 当前已含 same_review；确认是否必须同时称赞完整和保护，保留复合主方案 |
| Q18 | 明确安装服务对象及“哪类反馈”的分类目标是否需要另行表示，保留到货/等待/关系 |
| Q20 | 优先提出仅保留 f1 降温失败核心，移除作为必要证据要求的速度事实及其关系；原句纠正和否定作用范围仍明确记录 |
| Q21 | 保留同评论总体满意与局部不满及 mixed，不按规则连接词简化原意 |
| Q22 | 明确整条评论单位及 B 策略下全局中性属性的支持边界 |
| Q24 | 保留 any_of，提出两模式共享的生锈/腐蚀独立许可 ID |

上述 14 个提案有载荷变化，包括独立许可声明变化；其余 Q01、Q02、Q04、Q06、Q11、Q12、Q14、Q16、Q19、Q23 保留原载荷，仍列出边界及确认要求。needs_revision 原意见涉及 Q08/Q09/Q10/Q13/Q20，pending 涉及 Q03/Q05/Q07/Q15/Q16/Q17/Q19/Q21。提案不因状态为 accept_draft 就自动批准。

Q13 的双向目标使用 unknown 情感意图、any_of 和分别许可的目标；“替代方向的证据”不能冒充要求同评论正负共现的 mixed，也不生成 sentiment SQL 条件。Q23 保留 clarify，不推断产品一定有或没有电池，不把电池续航改成能耗表现。

Q03 的建议还暴露现有协议边界：只加入独立目标 ID，不能绕过尚未支持的 same_review 等必要关系，原校验会阻断这些目标。不能声称仅填两个 ID 就实现“保留完整联合要求，同时允许独立回答”的新语义。本轮列出选择和限制，没有修改协议或为迎合判断器删除关系。

14 个提案对应 **28 个非激活待复核草稿**：新草稿的 reference_state=null、所有事实/关系 unknown、引用与许可清空。它们只在采纳新契约后用于重审，不继承旧事实结论；原48份评审映射则完整保留。事实 ID 相同也不意味着在语义或关系变化后仍可沿用结论。

## 6. 待确认参考与正式转换条件

本轮仅生成 e1-reference-preparation-v1 的 references.pending.json，包含原48份人工选择、完整来源引用、原理由/备注、单人限制、来源/上下文/契约摘要和待确认字段。它不是已确认的正式 e1-human-reference-v1 导出。

在内存中复用原协议进行未确认草稿结构校验，48/48 通过；以 for_evaluation=True 校验则48份均按 reference_not_confirmed 拒绝，未通过临时设置 true 绕过该保护。已满足全部正式转换条件的记录为 **0/48**，实际正式确认数 **0/48**。

待本人确认后，后续转换才可填写正式 human_confirmed、review_status、reviewer_decisions 及独立的 confirmation_record 回执。现有 HumanReference 模型没有 confirmation_record 字段，需以侧面确认回执绑定最终参考摘要，不能擅自扩展或塞入未知字段。必须记录真实确认身份、确认时点、所批准的原导出/契约/来源和上下文版本；当前没有伪造这些值。

评审仅为单人初评，不补写第二人或裁决人，不宣称双人盲审。既往相关性评分接触声明仍待本人选择“未接触/曾接触/不确定”。初评参考独立于未来判断器输出；原0/1/2只作为历史材料保留，未转换为充分性答案。

## 7. 私有交付与建议审核顺序

| 私有文件 | 内容 |
|---|---|
| backup/、backup-receipt.json | 原始字节备份、版本/时间/48项绑定与摘要 |
| audit.json | 实际计数、审计范围和未确认状态 |
| case-findings.json、case-review-tasks.md | 全48份问题、严重度、原判断、建议与引用依据 |
| case-field-proposals.json | 重点CASE的条件式字段修订建议，全部未应用 |
| contracts.proposed.json | 24份共享、待批准契约及前后字段 |
| contract-diffs/Q01.diff 至 Q24.diff | 逐查询差异；无载荷变化者明确注明 |
| contract-approval-checklist.md | 原问题、对象/细分主题、事实、关系、独立许可和待确认问题 |
| references.pending.json | 原48份评审的待确认映射，未改人工选择 |
| references.proposed-blank.json | 采纳14份变更提案后需重新人工评审的28份空白材料 |
| paired-audit.json | Q05/Q07 相同输入判断不一致依据 |
| source-validation.json、independent-verification.json | 来源、版本、原候选与过滤、引用和新版本无结论继承检查 |
| 测试日志、protected.before/after、file-hashes.json | 实际执行证据与完整性记录；不是外部封存批准 |

建议先审批 Q20 的含义，再处理 Q03/Q09/Q10/Q13/Q17 等目标边界；随后复核重点CASE和 Q07/CASE-012，最后统一理由、引用、许可/缺失及单人声明。契约变更须同时审查 U/F，不能只调整更容易通过的一边。

## 8. 实际校验与测试

| 本轮实际执行 | 结果 |
|---|---|
| 新审计逻辑的完全合成断言 | 9/9，通过；覆盖 all_of、any_of、limited 独立许可及关系阻断、未知、无效许可、缺失集合 |
| 现有 E1准备/协议、探索模块、QueryContract 定点回归 | 81/81，通过，无失败、错误或跳过 |
| 既有两个队列测试单独重现 | 0通过、2错误，测试组退出码1；不计为通过 |
| 实际材料独立复核 | 原48份选择保留、49处引用展开、222条原候选/过滤、24共享提案、28份无结论继承空白记录均通过适用检查 |
| 文件、隐私与Git隔离检查 | 247个既有文件摘要保持不变；私有材料忽略且未跟踪；公开报告不含真实评论或完整逐条标签数据 |

既有错误仍为临时 SQLite 路径进入仅支持 PostgreSQL 的持久标注队列：

- test_review_scope.ReviewScopeTests.test_run_date_filters_incremental_skip_and_scoped_progress
- test_review_scope.ReviewScopeTests.test_run_retry_and_cutoff_forwarding_with_real_mocked_sdk

未改原业务逻辑、跳过或计为通过。测试驱动禁止真实网络与 psycopg 连接。新审计脚本也阻断真实判断器和严格批次准备调用。81项通过与上述2项错误分开记录，不声称所有回归全绿。

本轮审计脚本首跑曾错误地将业务内容版本摘要按正文摘要比较，退出1；核对原存储/采集代码后，确认业务摘要还覆盖本轮未读取的规范化字段，改为身份对照并单独固定正文摘要。未补读脱敏字段、未修复或改写任何来源；修正后审计退出0。该实际错误和最终边界保存在 execution-notes.json。

实际主要命令如下，审计输出均独占创建，不覆盖历史材料：

```powershell
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/step4b-phase2c1b-human-review-audit-20261104/audit_review.py
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/step4b-phase2c1b-human-review-audit-20261104/verify_delivery.py
.venv/Scripts/python.exe -B outputs/rag-retrieval-quality/step4b-phase2c1b-human-review-audit-20261104/run_checks.py
git diff --check
git status --short
git check-ignore -- outputs/rag-retrieval-quality/step4b-phase2c1b-human-review-audit-20261104
```

没有修改公开业务代码、Agent、API、前端、检索过滤或排序；没有运行真实E1、外部LLM、向量同步或数据库读取。没有新增真实金标准答案，也未把结构测试当语义质量验收。

## 9. 冻结和进入探索性 E1 的结论

**当前不能冻结独立人工参考，也不能启动真实探索性 E1。** 本轮授权范围内的审计、diff、条件式修订建议、待确认材料和报告已完成，到此停止。

下一步需要本人确认24份共享契约的最终含义及独立许可，完成相应CASE复核、统一 Q05/Q07 配对判断、修正 CASE-012 等理由、补齐评分接触声明，并明确批准最终结果。完成后才能正式转换并固定人工参考版本；不能只把确认字段批量设为 true。

后续预测仍需独立审查探索性来源到预测输入的适配、输出和引用审计、上下文一致性及停止规则，并另获执行授权。质量数值门槛继续待确认；冻结24题属于已见过的诊断集，不作为独立泛化验证。索引边界及跨采集时点限制保留，不继续重复 M1–M3。

57条来源继续 history_unproven；将来冻结的是明确带此限制的探索性人工参考，不能借此宣称历史快照已证明，也不通过更改严格 trusted_batch 接口或制造外部摘要来放行。
