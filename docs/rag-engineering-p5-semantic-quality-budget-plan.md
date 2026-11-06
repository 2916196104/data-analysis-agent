# Shadow 语义质量验证与输入预算方案

2026-11-06。**建议下一轮只做 3 次固定合成输入的真实模型验证；当前仍保留 8000 输入估算限制。** 本轮已完成分析、公开官方资料查证和离线检查，没有业务数据库、Embedding 或外部 LLM 调用。没有重跑 S1/S2/S3、N1/N2/N3，未改写其保存结果。

## 1. 当前能证明什么

| 已保存实验 | 可确认的局部观察 | 不能据此声称 |
|---|---|---|
| S1/S2，提示 v1 | 10 条判断中 5 supported、4 refuted、1 not_supported；4 条 refuted 实为相反体验 | refuted 语义正确；结构/引用通过不代表语义通过 |
| N1/N2，提示 v2 | 10 条判断中 2 supported、8 not_supported；安装/客服/配送对象区分、相反体验处理有正面观察 | 提示改动已消除错误，或具备整体准确率保证；两轮问题与候选也变了 |
| S3 / N3 | S3 请求后超时；N3 发送前阻断 | 将缺输出计为 unknown、正确拒绝或语义失败 |

明确否认、纠正、同评论撤回、朋友转述、目标事实本身是假设、模糊否定范围和有效 unknown **尚无真实模型验证**。复杂 same_review/before 关系也未获真实验证。原结果的 `semantic_verified=false`、`publication_allowed=false` 和空正式许可集合继续有效。

依据：[首次实验](rag-engineering-p5-siliconflow-business-shadow-result.md)、[提示语义边界](rag-engineering-p5-shadow-semantic-boundaries.md)、[N1–N3 结果](rag-engineering-p5-n123-shadow-result.md)。这些结果里的语义阅读是助手诊断，不是独立人工金标准。

## 2. 固定案例与验收口径

固定输入为 [shadow_quality_cases_v1.json](../tests/fixtures/shadow_quality_cases_v1.json)，共 **15 条**；每条分别保存目标事实、问题、quote、完整评论、预期状态与理由。保留原有未提交草稿 C01–C12，补充 C13–C15。预期在目标模型调用前固定，由本次编码助手复核/编写，**未让待测试的 SiliconFlow 模型生成答案**；不冒称人工已裁决、独立留出集或“完全非 AI 生成”。不修改既有人工参考。

全部案例的目标都是“评论作者本人曾体验该虚构商品客服态度差”，后端沿用现有解析器生成 `service_bad`。案例均为虚构商品、评论与来源，未来模型只接收原 B1 投影，不接收预期标签、理由或本方案。

| ID | 最有价值的判别点 | 固定预期 |
|---|---|---|
| C01 | 本人直接抱怨客服，包装好评不撤回投诉 | supported |
| C02 | 客服很耐心；没有撤回此前评价 | not_supported |
| C03 | 明确说此前客服差评不属实并撤回；依据在 quote 内 | refuted |
| C04 | quote 先报告客服态度差；全文纠正“其实是配送员”，原评价作废 | refuted |
| C05 | quote 明确是朋友讲述，作者没有亲历 | not_supported |
| C06 | 明确“如果下次态度差”，尚未发生 | not_supported |
| C07 / C08 | 安装人员 / 配送员态度差，不能借给客服目标 | not_supported / not_supported |
| C09 | “不是客服态度差……”后主动中止、待确认；不补全否定范围 | unknown |
| C10 | 不耐烦的工作人员可能是客服，也可能是安装预约人员 | unknown |
| C11 | “客服没有批评我的态度”，语义明确，但不是评价客服态度 | not_supported |
| C12 | 客服差评与配送好评并存 | supported |
| C13 / C14 | 与 C12 **完全相同 quote**；全文分别明确它是朋友原话 / 尚未发生的假设 | not_supported / not_supported |
| C15 | 昨天客服差、今天换人变好，明确没有撤回昨天经历 | supported |

C02↔C03 区分相反体验与撤回；C04↔C07/C08 区分纠正原断言与单纯对象不符；C09↔C11 区分否定范围不明与清楚但不支持；C10↔C07 区分角色未知与已知角色不符；C12↔C13/C14 防止只看 quote；C15↔C03/C04 防止把后续改善当撤回。

| 状态 | 必须满足的语义标准 | 引用与验收 |
|---|---|---|
| supported | quote 直接报告指定对象、属性、说话者的事实；全文未明确否决 | `support_refs` 在 quote 内；全文不能补新支持 |
| refuted | **这条候选相关主张本身**被明确否认、撤回或纠正，关联对象/时间/说话者可确定 | quote 内否认用 `refutation_refs`；C04 全文撤回用同评论 `context_veto_refs`。必须引用真正纠正内容，不能只引用相反好评 |
| not_supported | 可明确判断没有直接支持：相反体验、其他对象/属性、转述或假设 | 不带支持/反驳引用；不能用它消灭另一买家的支持 |
| unknown | 读完整评论后，仍无法可靠确定说话者、对象、否定范围、时间或上下文关联 | 不猜测补全，不冒充明确撤回；执行失败不能转换成该标签 |

真实验证要求 **15/15 标签匹配**（3 supported、2 refuted、8 not_supported、2 unknown），并逐条检查理由与引用是否支撑该标签。C03/C04 必须识别撤回，C02/C05–C08/C11/C13/C14 不得误标 refuted，C09/C10 不得强判。不能用批次“至少一条支持”掩盖逐候选错误，也不能让模型自己的理由充当正确性证明。

结构要求：输入绑定、完整事实×候选矩阵、key、来源身份、原文 Unicode 区间全部通过；不得出现 quote 外支持、跨评论否决或角色拼接。出现任一语义错误即本固定集不通过，保留原输出及分项错误，不事后改预期。**即使全对，也只通过这 15 条开发案例，不开放正式 Agent 许可。** 本组聚焦客服负向单事实，没有覆盖正负方向全排列或复杂关系泛化。

## 3. 输入估算偏差与覆盖损失

当前 [估算实现](../src/data_analysis_agent/agents/review_shadow_siliconflow.py) 为：

```text
E = ceil(len(canonical_json(完整 request_body).encode("utf-8")) / 2) + 1024
```

`canonical_json` 已关闭 ASCII 转义并压紧分隔符，继续“去 JSON 空格”基本没有收益。body 包含系统提示、JSON 字符串形式的用户消息、strict 输出 Schema、请求控制参数，以及外层 JSON 对消息的二次转义。**HTTP 请求字节不是部署后的 prompt token**；1024 也不是有证明的最大模板开销，bytes/2 对任意文字、代码、哈希和特殊字符不构成可靠上界。

下表仅只读提取两轮 `provider-usage.json`，没有重新检索或调用：

| CASE | 本地 E | 实际输入 usage | E − usage | E / usage | 相对高估 |
|---|---:|---:|---:|---:|---:|
| S1 | 7644 | 3076 | 4568 | 2.4850 | 148.50% |
| S2 | 7739 | 3101 | 4638 | 2.4956 | 149.56% |
| N1 | 7924 | 3208 | 4716 | 2.4701 | 147.01% |
| N2 | 7914 | 3169 | 4745 | 2.4973 | 149.73% |
| S3 | 7832 | N/A，请求后超时 | N/A | N/A | N/A |
| N3 | 8225 | N/A，未发送 | N/A | N/A | N/A |

四个成功样本明显偏高，但均已通过原门槛且来自同商品、近似结构；有选择偏差，提示还有版本差异。不能除以 2.49、按样本最大误差校准，或把 N3 的真实输入推定为约 3300。

N 轮因预算损失 **1/3 问题、5/15 排名条目（33.3%）**；两轮合计因预算损失 1/6 问题、5/30 条目，另有 S3 超时损失 1/6 问题、5/30 条目。分母是固定实验的排名条目，不是不同评论数、全库覆盖率或语义准确率。N3 只比本地门槛高 **225（2.8125%）**；无法证明它实际超过 8000，也尚不能证明一定属于误阻断。

仅在内存机械恢复 N1–N3 的原 B1 输入以分解字节数，复算与原记录一致；不恢复 P0 信任、不改原文件：

| CASE | 系统提示字节 | 用户 payload 字节 | response_format 字节 | 外层包装/转义/控制字段字节 | body 总字节 |
|---|---:|---:|---:|---:|---:|
| N1 | 2814 | 6703 | 3611 | 671 | 13799 |
| N2 | 2814 | 6689 | 3611 | 665 | 13779 |
| N3 | 2814 | 7289 | 3611 | 688 | 14402 |

8000 对应本方法的 body 上限 13952 字节，N3 多了 450 字节。若机械扣掉其 688 字节外层部分，代理估值会变成 7881；**这仅展示误差来源的可能量级，不是可上线的新计数法**，仍缺实际模板/Schema 转换成本。不得据此放行或重跑 N3。

## 4. 官方计数资源与改进可信度

截至 2026-11-06，仅查询公开资料；未发送评论、调用模型/计数端点、查询账户或安装依赖。

- **服务版本**：SiliconFlow 官方公告说明 2026-10-21 将原模型 ID 升级到 1017，ID 未变。这是文档映射，不是不可变部署指纹。[官方更新公告](https://docs.siliconflow.cn/docs/release-notes/overview)
- **消息模板**：1017 官方模型卡明确没有 Jinja chat template，提供 Python `encoding.py`。它处理 BOS、角色标记、助手生成前缀；chat 模式使用关闭思考的前缀。必须先按正确模式编码消息，不能仅 tokenize 外层 HTTP JSON。[1017 模型卡](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017/raw/main/README.md)、[编码说明](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017/blob/main/encoding/README.md)
- **Schema 不能漏计**：上游 `render_message` 有 system 消息 `response_format` 的文本封装；当前 HTTP 顶层参数如何转成它，需要正确转换。官方 `deepseek-recipe` 0.2.0 补齐 json_schema 转换与渲染；不能沿用忽略该字段的旧转换结果。[编码源码](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017/blob/main/encoding/encoding.py)、[官方变更记录](https://github.com/deepseek-ai/deepseek-recipe/blob/main/CHANGELOG.md)
- **tokenizer 路线存在，但精确绑定未证实**：官方原版 Flash 有 `tokenizer.json`；本轮工具未成功取到 1017 对应词表/配置，未核验它们与原版相同。recipe 的 V4.1 随附词表标注源自 **DeepSeek-V4-Flash-Vision-Exp** 并修改了图片特殊 token，不能仅因名为 v41 就当作 1017 的部署词表。需固定精确修订与摘要，再验证普通文本/显式特殊 token 的区别；用户文本里的特殊标记不得被当作控制 token。[原版官方词表](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/tokenizer.json)、[随附词表来源](https://github.com/deepseek-ai/deepseek-recipe/blob/main/static/tokenizers/README.md)、[tokenizer 使用规则](https://github.com/deepseek-ai/deepseek-recipe/blob/main/docs/tokenizer.md)
- **SiliconFlow 预先计数**：在当前公开 API 目录、Chat/Messages 文档和官方域名定向检索中，未找到明确支持此模型及 strict Schema 的公开 `count_tokens`/`tokenize` 端点。可确认的是生成响应中的 `usage.prompt_tokens`，它发生在请求之后。不能从“OpenAI/Anthropic 兼容”推断计数端点可用，也不能断言供应商没有私有能力。[Chat API 与端点目录](https://docs.siliconflow.cn/docs/api/chat-completions-post)、[Messages API](https://api-docs.siliconflow.cn/docs/api/messages-post)

| 方案 | 可信度与适用条件 | 本轮决定 |
|---|---|---|
| 供应商部署一致的发送前计数 | 潜在最高；须有官方模型覆盖、词表/模板版本、Schema 包装和计费口径说明 | 公开能力未证实，不猜端点、不探测 |
| 精确修订 tokenizer + 官方消息/Schema 编码 | 对该上游格式可精确；对 SiliconFlow 仍需部署绑定。使用特殊 token 安全的编码路线 | 后续先作旁路比较，不改变现有放行条件 |
| 从计数输入剔除外层转义和控制参数 | 方向合理，能消除已识别的传输开销；模板与 Schema 差值仍未知 | 只作分析，不直接扣 688 或移除 1024 |
| 压缩重复系统说明，保留所有规则 | 可减少实际输入，但须新提示与语义回归；N3 本地差额相当于 450 UTF-8 字节 | 不改当前提示；后续可单独评估 |
| 缓存静态前缀 | 可能减少费用/延迟，不减少实际上下文或当前输入估算 | 不作为绕过预算手段 |

不采用截断全文、删除来源/哈希、减少 Top-5、改 Schema、摘要替代原文或按四点拟合放行。当前短合成案例已能保留全部信息并通过原门槛，不需要先改生产预算。

**可靠性所缺证据**：1017 词表及消息转换的固定版本；SiliconFlow 对这些版本、非思考前缀、strict Schema 包装和计数口径的绑定；覆盖中文、英文、代码、数字/哈希、引号/换行、emoji/特殊标记、长全文及 8000 附近输入的合成对照。后续授权的三次语义调用可附带记录现估算与 usage，额外调用数为 0；三点或零低估仍不能证明普适上界。若只取得经验校准而没有部署保证或可证明上界，继续保留当前限制，不自动降低系数或提高预算。

## 5. 下一轮真实模型验证方案（未执行）

| 固定批次 | 候选 | 原方法输入估算 | 输出参数上限 |
|---|---|---:|---:|
| Q1 | C01–C05，5 条 | 7695 | 2500 |
| Q2 | C06–C10，5 条 | 7631 | 2500 |
| Q3 | C11–C15，5 条 | 7750 | 2500 |

**最多 3 次串行请求，0 自动重试，0 补题，0 换模型；不调用业务检索。** 这三个集合是固定合成候选，不宣称经过真实向量 Top-5 检索；每批保留 5 条，业务 Top-5 策略不变。沿用 B1、B2、`p5-shadow-siliconflow-instructions-v2`、`deepseek-ai/DeepSeek-V4.1-Flash`、`enable_thinking=false`、temperature=0。每批都有独立合成 request_id，使用现有测试帮助函数准备投影，预期输出只供离线比较，不得发送给目标模型。

输入估算合计 **23076**，预算沿用每次 8000／合计 24000；输出每次 2500／合计 7500。按已核对的国内忙时价格（输入 ¥2、输出 ¥8／百万，缓存优惠不计），**条件预算 ¥0.036/次、三次 ¥0.108**；北京时间 02:00–08:00 半价时段对应 ¥0.054。它以实际输入 ≤8000、总生成 ≤2500 且无意外思考为条件，**不是账单硬上限**。API 文档的 max_tokens 不含思考，因此不能仅凭该参数保证总费用；仍检查 reasoning 与 usage，超时且缺 usage 时费用未知。[官方价格](https://www.siliconflow.cn/pricing)、[max_tokens 口径](https://docs.siliconflow.cn/docs/api/chat-completions-post)

时限沿用 read/connect/write/pool = 20/5/10/5 秒，B2 每批外层 deadline 30 秒，三批调度窗口合计最多 90 秒，另留进程回收和本地核验时间。历史四次成功 provider 阶段约 5.671–8.438 秒、B2 总计约 6.641–10.094 秒，只作排程参考，不承诺新案例时延或 p95。记录每批准备、SDK、验证及 B2 总耗时，后者包含前者，不重复相加。

Schema/引用/来源/模型身份异常、截断、意外思考、预算超限或超时即停止余下请求，不重试；未执行项单列 N/A。单纯语义分歧记为固定集失败，可完成剩余既定批次，不修改输入/预期、不追加请求。报告逐案例状态、理由/引用错误、四状态混淆矩阵与执行完成率，执行失败与语义失败分开。

所需后续授权仅为：**向指定 SiliconFlow 模型发送上述 15 条合成评论及必要 B1 来源字段、最多 3 次请求，并接受现有近似估算的费用/超时不确定性。** 不包含真实评论、业务库、索引扩展、历史题重跑、正式回答发布或额外计数探针。复用现有隔离和限制，不新增审批系统。本轮未制作会自动发请求的入口；明确授权后再接入既有 Shadow 调用路径。

## 6. 离线验证与变更边界

变更仅为本方案、原有未提交的合成夹具和 [离线测试](../tests/test_shadow_quality_plan.py)。运行源码、提示 v2、B1 Schema、B2、`reported_experience_v2`、既有人工参考、历史实验与配置均未改动。

验证结果：既有 Provider/提示专项 **55/55**、B1/B2 专项 **42/42**、本方案专项 **6/6**，共 **103 项不同测试通过**。最初 60 项运行包含修改前的 5 项方案测试；修改后方案专项复跑 6 项，计数已去重。检查了 15 条完整评论/精确引用、三批 5 候选输入预算、目标/来源/quote 越界拒绝、关系约束、不同买家不互相撤回，以及 Shadow 不发正式许可。还确认了“语义错误但引用合法”的伪支持可以通过结构审计，故未将这些测试视为 LLM 成绩。

可在仓库根目录复现最终集合（纯合成/Mock；测试屏蔽真实数据库和 HTTP）：

```powershell
$env:PYTHONPATH='src;tests'
.venv/Scripts/python.exe -B -m unittest test_shadow_quality_plan test_shadow_semantic_boundaries test_review_shadow_siliconflow test_siliconflow_schema_probe test_review_shadow_openai test_p5b1_structured_evidence_design test_review_structured_shadow -q
git diff --check
```

未做前端改动，不启动页面或构建前端；未实测官方 tokenizer、供应商计数端点或新的真实语义能力。当前已完成离线阶段并停止，等待明确的真实调用授权。
