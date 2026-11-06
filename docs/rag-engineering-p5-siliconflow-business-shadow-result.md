# 首次 S1/S2/S3 SiliconFlow 真实 Shadow 结果

2026-11-06。**固定流程执行一次，发起 3 次模型请求：2 次成功，1 次 SDK 超时；没有重试或补跑。** S1 多识别出一条 v2 未许可的直接反馈；S2 没有观察到相对 v2 的改善；S3 无模型输出，客服/否定/转述风险未完成验证。已停止，未启用正式 LLM 许可。

逐候选原文、完整上下文、原始 v2/LLM 判断、来源 key/区间和阅读分析保存在 Git 忽略目录：

- [私有逐条阅读明细](E:/AllProject/data-analysis-agent/outputs/rag-semantic-retrieval/first-siliconflow-shadow-v1/reading-analysis.md)
- [机器可读逐条明细](E:/AllProject/data-analysis-agent/outputs/rag-semantic-retrieval/first-siliconflow-shadow-v1/reading-analysis.json)
- [离线一致性检查](E:/AllProject/data-analysis-agent/outputs/rag-semantic-retrieval/first-siliconflow-shadow-v1/offline-consistency-check.json)
- [原始运行汇总](E:/AllProject/data-analysis-agent/outputs/rag-semantic-retrieval/first-siliconflow-shadow-v1/execution-summary.json)

以下语义结论来自本次助手对已保存文本的逐条阅读，**不是新增人工金标准、用户确认或双人裁决**；没有修改原始运行文件中的 `semantic_review=pending`，Shadow 的 `semantic_verified=false` 始终保留。

## 执行及来源范围

先运行既有离线 preflight，状态为 `ready_for_explicit_fixed_business_run`、`missing=[]`，两项限定授权生效，再运行用户指定的一次命令。

```powershell
.venv/Scripts/python.exe -B scripts/run_rag_shadow_first.py preflight --provider siliconflow --shadow-env .env.shadow --business-env .env --authorize-fixed-business-shadow --accept-estimated-token-risk
.venv/Scripts/python.exe -B scripts/run_rag_shadow_first.py run --provider siliconflow --shadow-env .env.shadow --business-env .env --authorize-fixed-business-shadow --accept-estimated-token-risk --output-dir outputs/rag-semantic-retrieval/first-siliconflow-shadow-v1
```

- 范围保持 `reviews_csv / reviews-csv`、商品 `100013588055`、UTC `2021-09-11` 至 `2022-08-26`，本地 BGE-M3/1024 维、既有 pgvector、`quote-utf8-v1`。没有写库、扩索引、重新标注、向量同步或其他商品查询。
- 三题均完成 P0 检索和来源验证，各返回 **5 条**，共 **15 个排名条目、11 个不同观点及 11 条关联评论**。这不是全库召回率或索引覆盖率统计。
- 三份来源回执均为当前 `repeatable read / read_only=true` 观察，`current_effective_readonly_v1`、`quote_full_review_veto / 1`；保留既有标注 profile 和 EmbeddingSpace。没有升级为历史可信快照。
- S1/S2 未附加 aspect 或 sentiment 过滤；S3 沿用既有适配器的 `aspect=service`，均 Top-5、原排名、原问题不变。S3 的 service 类别不能直接证明具体客服对象或态度。
- 三次请求均固定 `deepseek-ai/DeepSeek-V4.1-Flash`、strict B1 Schema、`enable_thinking=false`、输出参数 2500、SDK 20 秒、外层 30 秒、`max_retries=0`。S1/S2 返回模型与请求一致；S3 未收到模型身份回执，不能按已验证记账。

## 每题结果、token、耗时和费用

| 问题 | v2 原判断 | Shadow 结果 | 本地输入估算 | 实际输入/输出/思考 token | Factory/SDK s | B2 总 s | 忙时 usage 费用估算 |
|---|---|---|---:|---|---:|---:|---:|
| S1：有没有制冷不足的反馈？ | sufficient，许可 2 条 | 成功，3 supported、2 refuted | 7644 | 3076 / 1136 / 0 | 8.438 | 10.094 | ¥0.015240 |
| S2：有没有制冷效果好的反馈？ | sufficient，许可 2 条 | 成功，2 supported、2 refuted、1 not_supported | 7739 | 3101 / 1026 / 0 | 7.844 | 8.938 | ¥0.014410 |
| S3：是否有客服态度差的反馈？ | insufficient，许可 0 条 | **model_failed，APITimeoutError** | 7832 | **N/A / N/A / N/A** | 20.343 | 21.391 | **N/A** |

Factory/SDK 阶段包含本地准备、HTTP 和解析，不是服务端纯推理时间；B2 总耗时已包含它，不能重复相加。Embedding/检索/来源补齐的独立分段计时未由本入口持久化，本轮无法可靠分离报告。

S1/S2 均 HTTP **200**、`finish_reason=stop`，无 reasoning_content 字段、reasoning_tokens=0、缓存输入=0；总 token 分别为 **4212、4127**，均在目标内。本地估算不是准确计数，不能根据本次差值拟合通用换算率。

费用按本轮固定的[SiliconFlow 国内忙时价格](https://www.siliconflow.cn/pricing)输入 ¥2、输出 ¥8／百万 token 计算，发送/接收时间均处于北京时间约 18:02：

- S1：`(3076×2 + 1136×8)/1e6 = ¥0.015240`。
- S2：`(3101×2 + 1026×8)/1e6 = ¥0.014410`。
- **已知两题合计 ¥0.029650；三题实际总费用未知。** S3 已进入 SDK 请求，约 20 秒后报 APITimeoutError，没有 HTTP 状态、token、结束原因或费用回执；远端可能仍执行/计费，不能填 0，也不能保证最终扣费不超过条件预算。未额外读取账户账单。

请求数为 **3 次尝试、2 次确认成功、1 次超时**；不是 3 次成功或 2 次请求。脚本正常退出且汇总 `failure=null` 只表示未抛出外层异常，不能覆盖 S3 的逐题失败状态。S3 worker 退出码 70，B2 保留 `adapter_error`，provider sidecar 给出 APITimeoutError；三题进程及管道线程均已回收。

## 逐题语义对比

### S1：有局部覆盖收益，查询级原本已可回答

前两个候选是直接的负面制冷自述，v2 与 LLM 均支持。第三个候选明确报告安装后短期内没有制冷，quote 本身已支持所问的负面反馈，完整评论没有撤回；LLM 支持而 v2 为 unknown，属于本次观察到的**规则表达覆盖不足**。

该新增证据只支持“存在这样的买家反馈”，不能证明客观故障原因、全商品质量或长期耐用性。第二条的全文还有安装问题，不能据此推断安装导致故障或当作性能实测结论。

另外两个候选属于正面体验，LLM 没有将其许可为负面事实，方向正确；但其 `refuted` 标签有下文所述的语义问题。**本题 v2 原来已 sufficient，因此收益是多识别一条证据，不能说把整个问题从拒答变为回答。**

### S2：双方支持相同两条，没有证明减少过度拒答

两个直接正面制冷候选均被 v2 和 LLM 接受。完整评论中的外观、能耗、安装、智能控制等其他主题没有撤回 quote，模型也未从这些上下文借入新支持事实；但 v2 本次同样正常许可，**没有出现预定的“无关上下文导致 v2 拒绝、LLM 纠正”现象**。

两条负面制冷候选未获正向许可；另一个候选的 quote 属于冬季制热，LLM 返回 not_supported，未将制热偷换为制冷，处理合理。不能据此宣称已普遍改善过度拒答。

### S3：检索有相关证据，但模型比较因超时缺失

v2 对五条候选全部 unknown，查询为 insufficient。逐条阅读可见：

- 两条 quote 明确指向客服态度差，是直接相关候选。其一全文同时肯定安装/物流人员，属于不同服务对象，不能据此否决客服抱怨；这里仍看到 v2 的表达/对象覆盖不足。
- 两条 quote 只是泛化售后差评，不能直接当作客服态度差；全文的其他内容不能从 quote 外补入支持事实。
- 一条 quote 同时涉及客服推脱和售后态度，角色是否等同仍有不确定性，不能自动把售后映射为客服。

**LLM 没有输出，以上只是文本阅读观察，不是 LLM 成绩。** 不能把超时记为“正确拒答”或“无消费者反馈”。这些候选也没有明确的转述、假设或对客服负面断言的否定/撤回；预定风险没有得到有效验证，不换题补足。

## 发现的问题与未覆盖项

1. **反驳标签过强：4 条。** S1 两条正面体验、S2 两条负面体验被标为 refuted。现有提示/协议将 refuted 定义为候选断言被明确否定或撤回；这些文本只是与所问方向相反，通常更适合 not_supported。真实引用、正确区间并不能解决此状态语义问题。没有改写原模型结果；本次聚合也未用另一评论的相反体验取消已经成立的存在性支持。
2. **未发现无依据的正向支持，但范围有限。** 已完成的 10 条判断中有 5 条 supported，逐条阅读其 quote/上下文均能支持对应单事实；这是 5 条局部观察，不是整体准确率。没有将 S3 的 5 条缺输出算成错误率为零。
3. **超时是实际执行问题。** S3 的已知错误为 APITimeoutError，无法仅凭它确定是网络、排队还是服务端推理延迟；不修改时限或补跑去消除失败记录。
4. **没有上下文否决/复杂关系实测。** 已完成输出没有 context_veto_refs，也没有 unknown；三份契约原本均无必要关系，返回关系集合为空。不能声称已验证否定/转述/假设、before 或同评论复合关系。

## 结构、来源、隔离与保护检查

- 三题传输产物、范围、quote/原文哈希及 v2 重放均通过离线检查；原完整来源验证由本次 P0 只读事务完成。离线仅恢复原 B1 封套做输出一致性校验，没有创建新的 P0 信任标志、trusted_batch 或历史来源证明。
- S1/S2 的原封套摘要与运行 sidecar/输出绑定一致；**10/10** 逐事实判断、**9/9** 引用的 key、source identity、原文切片和 quote 边界通过检查。其中 5 个支持引用、4 个反驳引用；引用合法不消除上述反驳标签问题。
- S3 的输出 Schema、引用、实际 usage/模型身份为 **N/A**，不能记作第三份通过。三题估算均未超过 8000；已返回两题实际输入/输出也未超目标，没有观察到来源、区间、模型漂移、截断或思考异常。
- `publication_allowed=false`、`semantic_verified=false`、正式许可集合为空。原 v2 判断单独保存，未修改正式 Agent 的 assessment、引用、回答或历史。
- 本轮没有修改运行代码：124 项已有源码、入口、Schema 和配置摘要全部一致，保留原有用户改动；`.env`、`.env.shadow` 未变。旧单次探针 11 项产物摘要也全部一致，未再次运行它。
- 本轮实际执行既有 preflight、一次真实三题流程、保存产物的 v2/结构/引用重放与阅读分析、保护摘要检查及 `git diff --check`。未重跑此前 600 项合成回归，也未进行前端或正式 Agent 使用验收。

## 是否扩大验证

**有继续做有限独立语义验证的价值，但不宜立即扩大批量或启用正式 LLM 许可。** 依据只有 S1 新增一条正确直接支持；S2 未显示相对收益，S3 超时且关键风险未覆盖，同时发现四条反驳标签边界问题。

下一次最有价值的工作是明确 refuted/not_supported 的语义边界，验证客服与其他服务角色、否定/转述/假设等独立反例，并改善超时/usage 未知的可观测性。本轮不改规则、不扩大样本、不添加审批阶段。原三个已见问题不能当作独立泛化集。

**固定流程已完成并停止；没有第二次流程、没有自动重试、没有正式 LLM 许可。**
