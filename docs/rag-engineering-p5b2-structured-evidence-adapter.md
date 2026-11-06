# P5-B2：独立结构化证据判断适配器

2026-11-06。已实现可运行、默认关闭的独立组件：活动 RuntimeQueryContract → P0 已校验 SemanticResult → 白名单输入 → 可注入判断器子进程 → JSON/来源/事实关系检查 → ShadowOutcome。**没有安装 Agent 调用钩子，没有外部模型、真实数据库或 Embedding 调用；Shadow 始终不能提供正式回答许可。**

## 文件与接入位置

| 文件 | 实际改动 |
|---|---|
| 新增 `agents/review_structured_evidence.py` | 将 B1 输入/输出模型与确定性检查落实为独立模块；沿用原版本及三份 Schema |
| 新增 `agents/review_shadow_adapter.py` | 默认关闭设置、可信工厂注入、请求 deadline、取消、进程/管道回收、安全诊断与独立结果 |
| 新增 `agents/review_shadow_worker.py` | 一次性判断器进程；只接收白名单 payload 和单独的工厂配置；抑制 SDK 标准输出/错误，返回有界输出 |
| `agents/review_task_contract.py` | 已识别事实的 description 从原问题已解析的完整子句生成，不再只写事实 ID；增加旧描述的精确兼容比较 |
| `agents/review_compression.py` | 历史读取兼容旧 v1 的 ID 式描述，仍拒绝任意目标、范围或其他字段变化，不改保存记录 |
| `reviews/semantic_retrieval.py` | 在原来源校验完成后绑定内存结果摘要；供独立消费者复用，不再次读取有效语料 |
| 新增 `tests/test_review_structured_shadow.py` | 22 项专项，含实际 Windows 子进程超时、取消、退出与资源回收 |
| 新增 `tests/fixtures/shadow_mock_judge.py`、`shadow_pipe_flood.py` | 可注入合成判断器、挂起/异常/超大输出等进程夹具 |
| 本报告 | 执行方式、证据及后续真实 Shadow 所需边界 |

上述 agents/reviews 路径均位于 `src/data_analysis_agent/`。未新增依赖、API、环境变量、数据库表、审批或审计系统。

## 实际调用流程

1. 调用方持有原契约和独立固定的 `trusted_contract_sha256`，不能从模型返回的目标反推预期值。
2. `prepare_input()` 检查范围、原问题、任务类型及事实/关系；未解析、unsupported、SQL/原因任务或 ID/unknown/待确认式描述返回 not_eligible。未覆盖问题不由判断器自行改写。
3. 检查 SemanticResult 确由 P0 本次来源核验产生且未变更，再生成 B1 的 Candidate 输入。保留原顺序、key、review/annotation、有效快照、字符区间与完整上下文。
4. `model_payload()` 机械投影，移除标注 endpoint，不携带人工参考、v2 assessment、相关性分数或 cosine distance。完整评论只作解释/否决，超预算拒绝，不截断后放行。
5. 显式启用时，启动一次性子进程，调用可信工厂返回对象的 `judge(payload_json) -> str`；可选 `close()` 在正常退出时调用。配置与模型输入分开，凭据不放入 argv、模型 payload 或日志。
6. 子进程返回后，按 B1 协议严格解析并检查事实×候选矩阵、引用、见证及 all_of/any_of。产生独立 ShadowRun，不改 Agent 任务、回答、引用或正式许可集合。

默认调用会立即跳过，甚至不验证输入、不创建进程：

```python
from data_analysis_agent.agents.review_shadow_adapter import StructuredEvidenceShadow

run = StructuredEvidenceShadow().run(
    None, None, trusted_contract_sha256='', request_id='disabled-example')
assert run.outcome.execution_state == 'disabled'
assert run.outcome.publication_allowed is False
```

可执行的合成示例（仓库根目录，需现有 tests 路径）：

```powershell
$env:PYTHONPATH='src;tests'
@'
from test_review_structured_shadow import verified
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.agents.review_shadow_adapter import (
    StructuredEvidenceShadow, ShadowSettings, JudgeFactory)

contract, result, _ = verified()  # FakeEncoder/FakeRepository，经真实 P0 校验
adapter = StructuredEvidenceShadow(
    settings=ShadowSettings(enabled=True),
    judge_factory=JudgeFactory(entrypoint='fixtures.shadow_mock_judge:MockJudge',
                              config_json='{"mode":"support"}'))
run = adapter.run(contract, result,
    trusted_contract_sha256=content_sha256(contract.model_dump(mode='json')),
    request_id='SYNTHETIC-DEMO')
assert run.outcome.execution_state == 'validated_shadow'
assert not run.outcome.publication_allowed
print(run.debug_record())
'@ | .venv/Scripts/python.exe -B -
```

工厂是后端受信任的可导入代码配置，不接受用户问题或模型输出指定模块。此处 Mock 只返回预置的结构判断，不证明自然语言蕴含。

## Schema、来源与历史兼容

原三份 `docs/schemas/p5b1-{backend-input,model-input,model-output}.schema.json` 未改；正式模块生成的 Schema 与它们逐项相同，没有新协议。B1 测试原型保留为历史设计回归，正式运行不导入 tests。

P0 原本已独立检查完整有效观点集合、来源版本、原文及快照。本轮只在返回时附加一个不序列化的内存完整性绑定，消费者核对完整结果（包括内部标注身份）。适配器没有调用 repository 或 encoder；测试确认准备输入前后调用事件相同。只携带 `source_validated=true` 的伪造对象、修改后的副本或从旧 JSON 反构造的对象不能通过。

这个绑定不是外部可信摘要或历史封存，也不改变 `current_effective_readonly_v1` 来源政策。旧历史材料不能借此升级为可信历史快照；跨进程/持久化后若没有原 P0 内存结果，本接口会拒绝，不能填自行计算的标志绕过。

描述修复只使用原 `target()` 已成功解析的单句或同评论子句，例如 `买家自述该商品的体验：制冷不足`；事实 ID、方向、关系及 v2 规则不变。无法可靠解析的自由问题仍不可进入判断器。旧 v1 历史只允许“相同事实 ID 曾作为其 description”的已知格式兼容，其他字段仍精确比较；既不覆写旧契约，也不使旧 ID 描述直接具备 Shadow 准入资格。

## Deadline、取消与调试边界

- 默认 `enabled=false`、deadline 10 秒、输入最多 128000 UTF-8 字节、输出最多 65536 字节；配置可收紧输入上限，输出最高 262144 字节。沿用 B1 最多 5 候选、8 事实、单条完整上下文最多 40000 字符。
- deadline 从 run 开始计时；准备后、子进程等待和解析/校验后都检查，过期输出不接受。无自动重试，每轮最多一次子进程尝试。
- 调用方可传 `threading.Event` 主动取消。挂起/到期/取消后 kill、wait，并 join 读写线程、关闭管道；清理有独立有界等待，包含在 total_ms 中。
- 输出长度在子进程返回时及父进程管道读取时分别限制；非法 JSON、UTF-8、未知 ID、缺项、越界引用等失败关闭。即使先收到完整输出，只要进程仍未正常退出，也不能报成功。
- 正常结束调用判断器 close；强制终止不能保证执行 Python finally，但操作系统会释放该进程的句柄/连接。终止失败明确返回 cleanup_failed/reaped=false，不能伪称已回收。
- 子进程是**生命周期隔离，不是 AppContainer 或文件/网络权限沙箱**。工厂不得启动脱离管理的辅助进程/服务；远端已接收请求的计算和收费未必能随本地取消撤销。

`debug_record()` 只含状态/稳定错误码、事实 ID 汇总、输入输出摘要/字节数、PID/退出码、耗时及回收结果；不含原文、prompt、判断理由、配置或异常堆栈。已校验的逐项输出仅在显式调用方的 `run.judgments` 内存中保留，repr 隐藏，不自动写入任务、公开 API 或前端。

ShadowOutcome 强制保持 `semantic_verified=false`、`publication_allowed=false`、`permitted_target_fact_ids=[]`。形式条件满足只叫 `structural_goal_satisfied`，不叫语义质量通过。same_review/before 继续验证同评论见证，before 还要求 quote 内时间关系证据；其真正语义仍需模型验证，不能用文字位置替代。

## 实际验证结果

专项 **22/22**、适用后端回归 **551/551** 通过（包含原 529 与新增 22）；原 RAG/运行时 85 项是子集，不重复累加。两项既有 SQLite 夹具调用 PostgreSQL 标注队列的错误单独重现并保留，未计为通过。

| 验证 | 结果与限制 |
|---|---|
| 正常 Mock 输出 | 结构完整、事实/关系可追溯，正常 close；仍不许可回答 |
| 单/多事实、all_of/any_of | 未支持原子事实与真正缺失必要条件分别计算；不自行增加 limited |
| same_review/before | 同评论结构成立；跨评论拼接、无时间证据、未知关系不放行 |
| 否定、纠正、转述、冲突 | 复用 B1 已见合成场景及局部否决检查；合法引用不被误称为语义证明 |
| 来源与引用 | 跨商品/日期/标注身份、文件/哈希/quote 变更、Unicode 区间错误、目标摘要变化均拒绝 |
| 未知描述/不支持任务 | 不启动进程，不允许证据模型补目标 |
| 无限等待 | 实际进程到期后终止；核对 Popen PID 等于判断器自己写出的 PID，确认不是仅杀启动器 |
| 主动取消 | 等判断器真实启动后发取消信号；进程退出、读写线程释放 |
| 崩溃/缺输出/超大输出 | 显式失败；分别验证子进程输出限额及父进程管道限额 |
| 输出后线程挂起 | 仍按 deadline 终止，不能把已有输出当成功 |
| 并发 | 两请求使用独立 PID、请求 ID 和输入摘要，无串线 |
| 关闭及历史兼容 | 不调用准备/启动入口；SQL、RAG 和历史回答不变；旧 ID 描述可读取，任意目标篡改仍被拒绝 |

最终全量回归中的真实本地 Mock 进程样例（单次观察，不是性能基准）：

| 场景 | PID | 退出码 | 总耗时约 s | 回收/线程释放 |
|---|---:|---:|---:|---|
| 正常结构返回 | 28348 | 0 | 0.063 | 均通过 |
| 无限等待到期 | 872 | 1 | 1.500 | 均通过，执行过强制终止 |
| 主动取消 | 34936 | 1 | 0.094 | 均通过，执行过强制终止 |
| 输出后残留非守护线程 | 12452 | 1 | 1.515 | 均通过，执行过强制终止 |
| 返回超大字符串 | 9700 | 72 | 0.063 | 均通过，子进程拒绝输出 |
| 管道持续超限输出 | 9788 | 1 | 0.047 | 均通过，父进程终止 |

首轮实际测试发现 Windows venv `python.exe` 是启动器：Popen PID 31540，而判断器 PID 7620；首轮测试因此失败，没有记作取消成功。现通过 `GetModuleFileNameW` 获取当前实际 Python 映像，复用同一依赖路径；修正后 PID 一致断言与回收测试通过。没有改系统 ACL、账户或原 Embedding 进程实现。

完整回归首次在沙箱内的 FastAPI TestClient 初始化阻塞；单独 faulthandler 栈定位到 Windows asyncio 的内部 socketpair/accept。保留并中止阻塞运行，允许本机内部 socketpair 后，同一 API 用例及全量回归通过。测试入口仍阻断真实 PostgreSQL 和外部 HTTP，不访问业务数据库或模型。没有通过修改生产 API 绕过该环境问题。

复现命令：

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_review_structured_shadow.py -v
.venv/Scripts/python.exe -B outputs/rag-semantic-retrieval/p5b2-20261106/run_checks.py
git diff --check
```

实际日志、原始失败、线程栈及 `os-process-receipts.json` 保存在 Git 忽略的 `outputs/rag-semantic-retrieval/p5b2-20261106/`。127 项既有源码/阶段报告/Schema/原型前后核对，仅上述三处允许的源码发生变化，其余 124 项不变，三份 Schema 完全相同。未触及 R0、reported_experience_v2、B2 参考、人工裁决或冻结资料；本轮没有重新读取/逐项哈希私有冻结归档。

前端、Agent Worker 调度、正式检索工具、许可策略及回答生成器未修改。没有新的 Shadow 配置被自动读入 `.env`，也没有调用钩子；正式链路不会依赖 Shadow 成败。本轮未重跑真实浏览器、业务数据库或模型验收。

原 15 个 P5 探针作为已见回归保留；B1 的 12 个新表达已参与开发测试，现也归为已见集合，不能再称为未见泛化测试。

## 首次真实模型 Shadow 还需要什么

组件已具备工程基础，但**尚无具体厂商的真实判断器工厂，也未授权真实调用**。下一次可直接准备一次受限 Shadow 联调，不需要新增审批平台或历史认证阶段：

1. 明确模型/修订、endpoint、仅本地保存的凭据及结构化输出支持。工厂实现 judge/close、请求 token 上限、HTTP 响应读取上限、SDK deadline 和 `max_retries=0`；模型无工具权限，不接收人工参考。不能静默复用主 Agent 的默认模型或密钥。
2. 建议首轮最多 **3 个已明确目标的查询，各 1 次主调用，失败不重试**；例如先覆盖简单自述、上下文限定和转述反例。样例选择与数量是建议，不是本轮执行授权，也不是正式 E1/B3-B。
3. 需单独批准真实 P0 检索/本地 Embedding 的读取范围，以及发送给选定模型的 Top-K quote 与对应完整评论。可沿此前单商品/数据集/日期范围；P0 来源校验可能读取范围内完整有效语料，不能只把授权写成 Top-5。实际行数需运行前确认，不能假定 200 条向量覆盖全量。
4. 建议首轮每次最多输入 8000、输出 1500 token，超限拒绝，不截断否决上下文。当前组件的字节上限不是 token 上限，需要具体工厂按所选模型落实；deadline 也需按该次配置明确。
5. 尚未选模型，无法给出实际货币成本或模型延迟。若输入/输出单价为每百万 token 的 Pin/Pout，三次建议预算的调用成本上界为 `3 × (8000×Pin + 1500×Pout) / 1000000`，不含可能的供应商固定费用；需在模型确定后核对价格。超时请求仍可能收费，不能承诺取消即不计费。

这次实现没有扩大语义许可。自由改写、否定纠正和时间关系的语义正确性仍未经过真实模型验证，复杂目标也没有被自动解锁。本阶段完成后停止，保持 Shadow 关闭，不进入正式 LLM 许可。
