# 阶段 4：自然语言回答与可信结果组装

完成日期：2026-10-30。

本阶段已把新回答接入真实聊天链路：简单统计使用确定性自然语言模板，趋势、比较及综合解释使用限时、无工具的模型请求，经过结构和命题校验后由后端注入数字。解释失败可降级，数据分析失败仍禁止发布成功答案。没有修改统计 SQL、人工复核规则、数据库 schema 或任务提交必填字段，没有进入阶段 5 的视觉重构。

本报告中的商品和趋势数字全部来自受控 SQLite 测试数据；模型调用使用 MockTransport 或本机模拟服务。未访问真实美的商品数据、真实模型服务或生产 PostgreSQL/Redis，不能把示例视为生产结论。

## 1. 修改文件

| 文件 | 修改内容 |
| --- | --- |
| `agents/review_answer_facts.py`（新增） | 从压缩上下文构建可信回答事实、真实计数/分母文本、可核验解释命题、样本限制和摘要候选。 |
| `agents/review_answers.py`（新增） | `AnswerGenerationStrategy`、`NaturalLanguageAnswerGenerator`、结构化草稿、`AnswerValidator`、`AnswerAssembler`、`PublicAnswer` 和历史回答读取。 |
| `agents/review_compression.py` | 补充稳定发现引用、有效/足量时间桶数量、最近相邻变化、首末有效桶；保留最近可比较点；显式单维度查询不因默认 Top 3 丢失目标维度。 |
| `agents/reviews.py` | 统计校验和压缩成功后调用新生成器；新增公开回答、生成诊断、解释及总模型调用计数，保留旧字段。 |
| `core/config.py`、`.env.example` | 3 个可选回答阶段设置，独立控制输入预算、超时与重试。 |
| `api/routes/public_analysis.py` | 公开读取响应追加 `public_answer`，历史转换不执行模型或新 SQL。 |
| `frontend/src/features/agent/PublicAnswerView.tsx`（新增） | 展示结论、重点、一次样本说明和必要限制；明确展示降级状态。 |
| `frontend/src/features/agent/public-answer.ts`（新增） | 将公开回答转成导出用纯文本。 |
| `frontend/src/features/agent/ChatMessage.tsx` | 优先展示公开回答；原范围/配置默认折叠；保留任务发布保护、详情和导出。 |
| `frontend/src/AgentResults.tsx` | 新回答已有结论时不重复旧摘要卡片；执行详情区分规划和解释调用。 |
| `frontend/src/features/agent/report-export.ts` | Markdown 结论使用新回答；原完整统计、配置和审计附录保留。 |
| `frontend/src/shared/api/task-api.ts` | 旧成功任务缺少新字段时读取公开接口兼容转换；失败时不回显旧技术正文。 |
| `frontend/src/types.ts`、`frontend/src/task-state.ts` | 可选公开回答/调用计数类型及回答整理进度。 |
| `tests/test_review_answers.py`（新增） | 17 项回答、数值绑定、模型异常、预算、范围/语义及历史兼容测试。 |
| `tests/test_review_agent.py`、`tests/test_review_plan.py`、`tests/test_public_analysis_api.py` | 适配真实新增解释请求和公开字段；旧同步规划夹具显式隔离回答网络。 |
| `frontend/src/features/agent/PublicAnswerView.test.tsx`（新增）、`chat.test.tsx` | 9 项新展示/API/导出检查，更新旧任务正文的兼容断言。 |
| `frontend/tests/assistant.e2e.mjs` | 原全链回归增加无工具解释协议、新回答、实际 SDK 超时降级及输入计数；修正日期操作和折叠范围快照定位。 |
| `scripts/phase4_answer_example.py`（新增） | 可重复生成受控实际回答、上下文大小和调用次数。 |
| `output/agent-refactor/phase4-example.json`（新增） | 正向、稀疏趋势、充足趋势、比较、超时降级、预算降级的完整记录。 |

后端上述相对路径均位于 `src/data_analysis_agent/`。未修改阶段 1 的工具实现、查询 SQL 和阶段 2 的工具预算/缓存；旧回答格式化器继续保留给审计与旧调用方，不再是新聊天的默认正文。

## 2. 实际回答架构

```text
原任务 / worker
  → ReviewQueryPlan → 原工具 / SQL
  → validate_report（数值、范围、情感、计划完整性）
  → 原完整 results + StructuredAnalysisResult
  → AnalysisResultCompressor → CompactAnalysisContext
  → 关闭只读事务及规划客户端
  → NaturalLanguageAnswerGenerator
      → 可信事实目录 + AnswerGenerationStrategy
      → 简单统计：确定性草稿
      → 复杂解释：预算检查 → 原 OpenAI 兼容客户端（无工具）
      → Pydantic 草稿检查 + AnswerValidator
      → 无效/超时：丢弃草稿，改用确定性草稿
  → AnswerAssembler（从可信目录重新取数和引用）
  → PublicAnswer + 内部 answer_generation
  → 原任务存储 → ChatMessage → PublicAnswerView
```

生成器不持有数据库连接或工具注册表。解释阶段关闭了此前只读事务，输入是已冻结上下文，不会为了生成文字重新分析或翻证据页。

生成失败与分析失败分开：前者保留统计并标为 fallback，可正常完成；后者仍走原 error/failed 路径，移除未确认正文，新公开回答不可用。

## 3. 模板与模型选择

| 问题 | 默认策略 |
| --- | --- |
| 好评/差评维度排名、简单比例、质量概览、星级指标、原文查看 | 模板，不增加回答模型请求。 |
| 整体/维度趋势、多商品比较、时间窗口比较、多工具综合 | LLM 解释。 |
| 明确要求原因、建议、改进、综合或详细分析 | 数据与现有查询计划支持时使用 LLM；不扩大阶段 2 的查询能力。 |
| 维度统计完全没有可分析标注/观点 | 数据不足模板，不为此增加解释调用。 |
| LLM 输入超预算、调用失败、未完整输出、结构或内容不合法 | fallback 模板；不重查 SQL。 |

普通回答优先主要发现，通常最多 5 项；明确详细分析可保留 15 项，多个明确趋势维度分别保留。超过正文范围时增加“完整分组保留在详情”的说明，不机械截断字符串。趋势正文只解释选中维度，商品比较使用各商品自己的范围与分母。

## 4. 草稿协议与可信文字边界

`AnswerDraft` 是严格 Pydantic 模型，额外字段、缺失字段和类型错误都拒绝。结构为：

```json
{
  "summary": "趋势需要结合各时段的样本量和覆盖率分别判断。",
  "items": [
    {
      "finding_ref": "finding:7c7892fabb4edb706298",
      "aspect_key": "packaging",
      "explanation": "包装可比较的标注样本不足，暂不能判断稳定趋势。"
    },
    {
      "finding_ref": "finding:bd3cc48d9a73e8680dfc",
      "aspect_key": "logistics",
      "explanation": "物流可比较的标注样本不足，暂不能判断稳定趋势。"
    }
  ],
  "caveats": []
}
```

上例使用产物 `sparse_trend` 中的实际发现引用，仅在该冻结上下文内有效，不能跨任务复制引用。

`finding_ref` 规则：统计组由来源结果、实际范围、分组和分析类型确定；维度引用由统计组引用和实际维度确定；趋势发现引用由确定性发现内容计算。证据引用对应真实来源结果和评论 ID。引用可以追溯到 `CompactAnalysisContext.statistics/key_findings/evidence`，不会展示为聊天中的 `[r1]` 等编号。

本阶段采用保守的可核验命题协议：模型可选择、组织同一真实发现提供的中文句式，以及明确标记的待核查建议；summary 从当前事实生成的候选中选择，explanation 可组合 1～3 个已核验句子。模型不能自由添加统计断言。开放中文解释无法仅靠数字/关键词扫描证明真实，因此未使用“Prompt 说不要编造就直接发布”的做法。

这限制了模型自由改写，但确保实际发布的解释在支持范围内。它属于有约束的解释组织，不宣称已经实现任意自然语言事实判断或任意原因推断。未支持的改写被拒绝后使用可靠模板，不把它当成模型正常生成。

## 5. AnswerValidator 与 AnswerAssembler

发布前检查：

1. summary 必须是当前事实支持的候选；模型不能插入数量、百分比或新结论。
2. 每个必要 finding_ref 恰好出现一次，禁止遗漏、重复、未知引用。
3. `aspect_key` 与真实发现一致；禁止制冷、静音、安装等未支持细分维度。
4. explanation 的每个句子都属于该发现支持的命题；建议不能代替统计结论。错误趋势方向、未经证实原因、任意商品/时间改写均拒绝。
5. caveats 只能引用已生成的限制；后端始终补齐必要限制，模型不能删除样本不足说明。
6. 来源结果属于真实 source_result_ids，范围引用存在且情感方向相符。商品和时间不由模型提交，模型增加 product_ids/date_from 等字段会被结构校验拒绝。

Assembler 再从同一压缩上下文重建可信目录，不采用模型提交的数字或引用对象。计数、比例、分母、峰值/变化、评论 ID、引文及证据源均由代码注入。比例保留 `positive_rate` 与 `positive_success_rate` 两个独立口径；零分母保留 null。全文结果与压缩结果保持可追溯，原统计未被修改。

`PublicAnswer` 实际字段：

| 字段 | 含义 |
| --- | --- |
| `schema_version / status` | 当前版本 1.0 与分析发布状态。 |
| `summary / highlights` | 自然语言结论和重点；每个重点内部保留发现、范围和来源引用。 |
| `metrics` | 后端绑定的数值及明确分母，按发现和范围保存。 |
| `sample_note` | 适合单一背景时只展示一次的样本说明，多范围不混加。 |
| `evidence_refs` | 已请求的真实有限证据及精确片段，不新增检索。 |
| `caveats` | 数据质量、空桶、多维去重、因果边界及正文省略说明。 |
| `generation_status` | `template`、`llm`、`fallback`、`unavailable`。 |
| `source_result_ids / scope_summary` | 真实结果和实际业务范围引用；界面不默认打印内部引用。 |
| `details_available` | 存在可核查详情，不代表获得额外访问权限。 |

## 6. 趋势解释

复用阶段 3 的方向、峰谷、最大相邻变化和首末变化，并补充有效桶数、足量桶数、最近可比较变化及其必要原始点。足量桶沿用成功标注至少 20 条、覆盖率至少 80% 的项目阈值。

稳定方向说明采用保守限制：有效桶少于 3 个、足量桶占有效桶比例不足 80%，或存在无标注桶时，不断言稳定趋势。足量且连续的已有样本可以描述上升、下降、平稳或有升有降；这仍不是统计显著性或因果检验。

峰值明确指负面占比峰值，带真实计数和成功标注分母。“最近变化”明确写实际可比较的相邻日期，不跨空桶连接，也不把很早的最后有效数据冒充当前月份。相邻变化由计数确定性计算。普通正文保留峰值和最近变化，详细请求可补最大相邻变化；首末变化等数值留在指标中。不在正文罗列全部月份，完整时序仍供原表格和图表使用。

## 7. 输入规模、超时与降级

生成输入是 CompactAnalysisContext 的白名单投影：本轮问题、业务范围、样本、选中的发现、必要指标/分母、可核验命题和限制。不发送完整 results、trace、usage、模型全身份、API 地址、标注指纹、完整时序或无关原文。

| 设置 | 默认值 | 约束 |
| --- | ---: | --- |
| `ANSWER_INPUT_MAX_BYTES` | 16000 | 1024～256000，按 system/user messages 的 canonical JSON UTF-8 字节数检查。 |
| `ANSWER_TIMEOUT_SECONDS` | 15 | 大于 0 且不超过 60，独立 SDK HTTP 超时。 |
| `ANSWER_MAX_RETRIES` | 0 | 只允许 0 或 1；仅连接/超时等传输失败受控重试。 |

预算在请求前检查，超出时不创建回答模型客户端、不截断 JSON，直接降级。SDK 自身 `max_retries=0`，应用显式限制最多两次尝试；非法草稿不重复请求。没有调大原规划总超时或重用工具规划调用预算。HTTP 超时不是新增的全任务硬截止时间；最多一次重试及原规划时间仍须分别计入任务耗时。

只有数据分析、validate_report 和上下文都有效时才允许生成。模型输出非 stop、包含工具调用、非法 JSON、缺失/额外字段、数字或命题不受支持均不能发布；丢弃该草稿，由已验证模板生成 fallback。诊断保留错误类别、字节数、尝试数、usage 和已验证草稿，不保存无效正文或异常栈到普通回答。

旧 `model_calls` 保持规划预算计数，新增 `answer_model_calls` 和 `total_model_calls` 区分解释开销；旧 usage 与解释 usage 分开记录。执行详情显示总调用及两阶段计数。

## 8. 最小前端与网络边界

新成功任务优先显示 `public_answer`，不默认显示旧 grounded_answer、重复 overview、模型配置或标注指纹。范围/配置和统计详情默认折叠，原统计、原文、执行诊断、JSON 下载和 Markdown 审计附录保留。Markdown 的结论部分使用公开回答，完整原始结果仍在附录。

旧任务没有新字段时，API 客户端追加读取 `/api/v1/public/tasks/{id}`。后端从冻结结果校验、压缩并确定性转换，无模型调用、无 SQL 重查、无历史回写。新任务在公开读取时也重新验证已保存的草稿，而非直接透传 public_answer。无法可靠转换时关闭公开视图；服务不可用时聊天给出安全历史提示，仍保留原有详情/导出入口，不直接回显旧技术正文。

任务必须 succeeded、无 task.error、Agent 为 ok/unsupported、validation 未失败，且已有 PublicAnswer 的状态可发布，才显示正文和导出。failed/partial/running/queued 即使夹带成功回答也不发布。fallback 明确显示“解释生成未完成，已根据核验统计生成可靠摘要”，不会标成 llm。

前端提交/轮询仍使用默认完整任务 API；隐藏和折叠字段只改变展示，不改变其网络可见性。阶段 3 的仅公开读取实例继续提供真正的白名单响应和路由隔离。本阶段没有新增用户角色鉴权，没有将默认完整服务变成多用户安全 API。

## 9. 实际受控回答示例

复现命令（项目根目录）：

```powershell
.\.venv\Scripts\python.exe scripts/phase4_answer_example.py
```

完整产物：[phase4-example.json](../../output/agent-refactor/phase4-example.json)。其中 positive 使用测试商品 P1/P2/P3，问题文本虽然写“美的空调”，不代表访问了真实美的商品。

### A. 好评主要方面，模板生成

> 根据当前已标注评论，好评主要集中在使用效果、物流、价格。
>
> 1. 使用效果：2 条，66.7%（2/3 条含正向观点评论）。
> 2. 物流：1 条，33.3%（1/3 条含正向观点评论）。
> 3. 价格：1 条，33.3%（1/3 条含正向观点评论）。
>
> 本次分析范围共 6 条评论，基于 3 条成功标注评论，其中 3 条包含正向观点。

同时展示样本量/覆盖率不足和同条评论可进入多个维度的限制。实际调用回答模型 0 次，没有硬编码用户参考的 308/229/228 等数字。

### B. 包装与物流趋势，受控 SQL + Mock LLM

此例另导入 150 条测试评论，6 个月每月 25 条，全部成功标注；包装每月负向计数为 5、10、20、10、5、10，物流为 15、12、10、8、6、5。统计经实际 SQL 和 validate_report 生成。

> 包装在已有标注样本中的负面占比有升有降、呈现波动。物流在已有标注样本中的负面占比呈下降变化。
>
> 1. 包装已标注样本的占比峰值在 2023-03，为 80.0%（20/25 条成功标注评论）。最近可比较的相邻时段 2023-05 至 2023-06 变化 +20.0 个百分点。
> 2. 物流已标注样本的占比峰值在 2023-01，为 60.0%（15/25 条成功标注评论）。最近可比较的相邻时段 2023-05 至 2023-06 变化 -4.0 个百分点。
>
> 本次分析范围共 150 条评论，基于 150 条成功标注评论。

另一个 3 个月稀疏例只有 3 条成功标注、其余月份空缺，摘要实际为“包装可比较的标注样本不足，暂不能判断稳定趋势。物流可比较的标注样本不足，暂不能判断稳定趋势。”不会把空月份按 0% 参与趋势计算。物流一月真实 0/3 可以显示 0.0%，与空月份的 null 分开。

### C. 解释超时降级

同一充足趋势上下文模拟 ReadTimeout，实际得到 `status=ok / generation_status=fallback / error_code=generation_timeout / attempts=1`。公开数字、范围和引用保留，未发布无效草稿，也没有重新执行工具。若在 validate_report 前失败，则无成功 PublicAnswer；两种失败不混用。

## 10. 实测输入规模与调用记录

以下均为实际 canonical JSON UTF-8 字节数，不是 Token 或生产延迟估算。`input_bytes` 包含回答 system/user messages；模板的投影虽可测量，但没有发送给模型，`sent_input_bytes=0`。

| 受控案例 | 完整压缩上下文 | 回答消息投影 | 按尝试计的消息字节 | 回答请求数 | 状态 |
| --- | ---: | ---: | ---: | ---: | --- |
| 正向维度 | 3072 | 4012 | 0 | 0 | template |
| 稀疏包装/物流趋势 | 5166 | 3645 | 3645 | 1 | llm |
| 充足包装/物流趋势 | 7506 | 4650 | 4650 | 1 | llm |
| 双商品比较 | 8552 | 4501 | 4501 | 1 | llm |
| 同一充足趋势超时 | 7506 | 4650 | 4650 | 1 | fallback |
| 同一趋势、1024 字节预算 | 7506 | 4650 | 0 | 0 | fallback |

协议本身有固定开销，不承诺每个小问题投影更小。上述字节只计算模型 messages，不包含 HTTP 字段/头部；sent_input_bytes 为消息序列化大小乘尝试次数，表示尝试提交量，不是网络流量测量，也不证明连接失败时服务端收到正文。模拟服务的 usage 不是实际供应商 Token 成本。

真实离线 SDK Agent 测试中，简单好评仍为 2 次原规划请求、0 次解释；复杂问题为 2 次规划请求加 1 次无工具解释。统计查询预算、缓存和证据页数没有因回答阶段增加。

## 11. 测试与失败记录

| 检查 | 实际结果 |
| --- | --- |
| 新回答专项 | 17 项通过：正负排名、单维度低于 Top 3、无正向/无标注、低覆盖率、多维评论、充足与稀疏双趋势、商品独立范围、超时、非法草稿/维度/数字/来源/时间/因果、预算、分析失败、历史转换、配置及重试上限。 |
| 相关阶段回归 | 开发中 117 项通过；随后新增充足趋势及重试测试，并纳入完整测试运行。 |
| 完整 Python，默认模式 | 执行 329 项，1 failure、23 errors、32 skipped。旧 SQLite 标注测试进入要求 PostgreSQL 的持久队列，错误为“持久标注队列只支持 PostgreSQL”。保留日志，未改原断言或生产队列实现。 |
| 完整 Python，本地测试模式 | 同一套 329 项执行，297 通过、32 跳过，0 failure/error。缺少隔离 PostgreSQL/真实 Redis 环境的 32 项未验证。 |
| 前端 | 22 个文件、166 项通过，其中新增 9 项公开回答/降级/发布保护/导出/历史读取检查。 |
| 生产构建 | TypeScript + Vite 通过；既有大 chunk 提示保留。 |
| 实际浏览器 | 隔离 API/Worker + 本机模拟模型 + Vite/Chrome 全链通过；84 张截图，1440/1024/768/390，页面脚本错误 0。 |
| 差异检查 | `git diff --check` 通过。 |

记录：[默认模式全量日志](../../output/agent-refactor/phase4-all-backend-tests.log)、[本地测试模式全量日志](../../output/agent-refactor/phase4-local-backend-tests.log)、[前端日志](../../output/agent-refactor/phase4-frontend-tests.log)、[构建日志](../../output/agent-refactor/phase4-build.log)。本地模式复现：

```powershell
$phase4PreviousMode = $env:LABEL_EXECUTION_MODE
try {
    $env:LABEL_EXECUTION_MODE = 'local'
    .\.venv\Scripts\python.exe -m unittest discover -s tests -q
} finally {
    $env:LABEL_EXECUTION_MODE = $phase4PreviousMode
}
```

前端目录执行 `npm test -- --maxWorkers=2`、`npm run build`；真实页面回归执行 `node tests/assistant.e2e.mjs`。以上执行使用允许本机套接字/测试子进程的环境，未延续阶段 0 的 Windows asyncio 阻塞结论。

已修复的验证问题：新增 TS 测试缺少 ChatTurn.kind、误用 Testing Library 的 exact 参数，生产构建曾失败，修正类型后通过。首次浏览器回归刷新后折叠范围的 innerText 与展开快照不同，改为严格比较完整冻结范围内容。旧日期键入偶发没有提交起始日期，改用真实日历选择并核对两个输入；跨月时补充实际月份导航。浏览器失败目录保留为 `assistant-fOsI06`、`assistant-xOm3H1`、`assistant-4G6GjA`，没有删记录或降低统计/覆盖率断言。

最终浏览器记录：[assistant-ZJF6WM/acceptance.json](../../output/playwright/assistant-ZJF6WM/acceptance.json)。84 个布局记录的页面/消息整体横向溢出数为 0，无额外对话框遮挡。已实际查看新趋势和降级回答的 1440、768、390 截图：文本正常换行，详情和导出仍可操作；390 下较长内容通过原有聊天/范围滚动区查看，没有进行整页视觉重构。截图也保留原范围选择器，不将该选择器误称为公开回答的一部分。

本机服务真实执行的包装/物流趋势解释为 `llm`，消息输入 3566 字节；下一轮将模拟服务延迟到超出 1 秒的独立 SDK 超时，实际任务仍 succeeded、回答为 fallback，前端显示降级说明。两轮均只有 3 次业务查询（overview + 两个维度趋势），没有因降级重查。整个回归的基础/规划/解释请求分别为 2/26/8 次，全部为模拟服务。原 JSON/Markdown 导出、刷新后不重调模型、证据下一页、失败/partial 隐藏和固定范围行为继续通过。

## 12. 兼容性与限制

- 旧 results、result_ids、scope、answer、answer_sections、trace、usage 和导出审计内容保留，新增字段可选；旧完整任务 API 的历史 payload 没有回写。
- 公开回答是结构化的展示边界，不是新增权限系统。当前本机前端仍可通过完整 API 取得内部字段；公开实例与原完整实例的访问限制仍须分别管理。
- 模型解释采用受控命题，任意新措辞、复杂商业原因和自由建议不在验证能力内，会降级。没有声称该校验器能够理解任意中文事实。
- 样本阈值用于保守展示，不替代统计显著性、代表性或因果分析。建议仅是待核查方向。
- 独立回答请求增加复杂任务耗时，输入预算和重试受控；未进行真实供应商质量/费用评估或生产负载压测。
- 没有新增正向趋势/正向比较或细分标签能力；仍沿用阶段 1～3 的真实能力边界。

本阶段完成数据到公开回答的最小闭环，未进行阶段 5 的完整界面改造。
