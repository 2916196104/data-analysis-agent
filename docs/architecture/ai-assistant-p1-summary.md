# 观数 AI 助手 P0–P1 架构演进与运维评估手册

本文档总结观数商品评论数据分析 AI 助手在 **P0（稳定性修复）** 与 **P1-1 ~ P1-4（传输协议、混合语义检索、多轮上下文与智能路由、可观测性与评估基准）** 完成后的核心能力边界、观测接口与回归命令。

---

## 一、阶段能力总览

| 阶段 | 核心交付 | 关键模块 |
| :--- | :--- | :--- |
| **P0 稳定性** | Embedding 多批增量同步、pgvector HNSW 迭代扫描、常驻编码子进程退避与冷却恢复、任务取消接口、Worker 指数退避重试、流式 `include_usage` 及 400 自动降级 | `embedding_store.py`、`embedding_process.py`、`worker.py`、`llm_gateway.py` |
| **P1-1 传输与协议** | 非阻塞异步 SSE 事件中心（`async_wait_task_update`）、PostgreSQL `LISTEN/NOTIFY` 跨进程低延迟桥接、`Last-Event-ID` 断点续传、细粒度分析阶段上报、结构化拒答与澄清契约（`PublicAnswer`） | `jobs/events.py`、`api/routes/tasks.py`、`agents/review_answers.py`、`frontend/src/useTask.ts` |
| **P1-2 混合语义检索** | 统一 Multi-Query + BM25 + RRF + Rerank 管线、PostgreSQL 参数化词法 SQL 下推、否定词保留与极性冲突惩罚、进程缓存 `CrossEncoderReranker`、扩展领域规则、前端引用来源徽标 | `reviews/hybrid_retrieval.py`、`reviews/semantic_retrieval.py`、`AnswerEvidence.tsx` |
| **P1-3 上下文与路由** | 零 DDL 服务端会话聚合与跨端恢复（`/api/v1/conversations`）、轻量 `include_audit=False` 上下文初筛与按需水合、8 轮显式上限、结构化 6 维澄清按钮、零模型快捷追问、非侵入式模式切换建议 | `jobs/store.py`、`jobs/analysis_context.py`、`agents/review_intent.py`、`mode-suggestion.ts`、`useAgentChat.ts` |
| **P1-4 可观测与基准** | 基于 `ContextVar` 的线程隔离单任务 LLM Token 归因（`result["llm_telemetry"]`）、`GET /api/v1/observability/summary`、`X-Request-ID` 链路追踪、前端折叠调试区补齐、离线自动化评估基准与真机多视口 E2E 闭环 | `core/llm_gateway.py`、`api/app.py`、`AgentResults.tsx`、`tests/eval_ai_assistant_benchmark.py` |

---

## 二、可观测与诊断契约

### 1. 运行态观测端点
- `GET /api/v1/health`：返回业务库连通性、任务队列状态、Worker 心跳及 `pg_listener_active`。
- `GET /api/v1/observability/summary`（**在 `public_only=True` 模式下严格隔离返回 404**，不输出密钥、提示词、连接串或评论原文）：
  - `llm_gateway`：总调用、错误数、连接池命中/未命中、累计 Token 与按角色调用分布；
  - `events`：活跃异步 SSE 订阅数、追踪版本号数量、PG `LISTEN` 存活与重连次数；
  - `embedding_process`：常驻编码子进程存活/失败/重启计数；
  - `reranker_cache`：进程内 Cross-Encoder 缓存数量；
  - `tasks`：按状态/类型分布、近期终态失败率（剔除主动 `task_cancelled`）、分析任务执行耗时与排队等待耗时（`avg` / `p50` / `p95`）。
- 所有 HTTP 响应均附带 `X-Request-ID` 响应头，服务端异常日志同步关联该 ID。

### 2. 单任务归因与流式遥测
- 每次 `run_review_agent()` 自动收集线程隔离的 `result["llm_telemetry"]`（含 `calls`、`errors`、`prompt_tokens`、`completion_tokens`、`total_tokens`、`duration_ms`、`by_role`），并将 Token 汇总同步至 `result["usage"]`（零模型直达路径保持 `usage=None`）。
- `/api/v1/chat/stream` 的 `start` 事件透出 `history_truncated` / `included_history_turns`，`done` 事件透出 `duration_ms` 及可选 `usage`。

---

## 三、离线评估基准与验证命令

### 1. 一键运行 P1 离线质量基准（零外部付费）
```powershell
$env:PYTHONPATH='tests'
.\.venv\Scripts\python.exe tests\eval_ai_assistant_benchmark.py --output .cache\p1-eval\benchmark-report.json
```
门禁覆盖：
- **混合检索质量**：对比纯向量基线与 P1-2 混合管线的 `Recall@5`、`MRR@5`、`nDCG@5` 与极性错召回率（`Polarity@1 == 0`）；
- **多轮路由与安全**：路由准确率、结构化澄清覆盖率、越界/跨会话零泄露率（均要求 `1.0`）；
- **传输与载荷效率**：20 并发异步 SSE 唤醒 P95 延迟、`include_audit=False` 载荷压缩比（≥ 30%）及跨会话轻筛零水合。

### 2. 后端与前端回归测试
```powershell
# 后端核心回归（P1-1 ~ P1-4 + 意图/追问/上下文/聊天）
$env:PYTHONPATH='tests'; $env:LABEL_EXECUTION_MODE='local'
.\.venv\Scripts\python.exe -m unittest test_p1_1_transport_and_protocol test_p1_2_hybrid_retrieval_upgrade test_p1_3_context_and_routing test_p1_4_observability_and_eval test_review_intent test_review_followup_p7b test_review_context_performance test_trusted_analysis_state test_chat_api -v

# 前端 Vitest 与生产构建
npm.cmd --prefix frontend test -- --maxWorkers=1 src/api.contract.test.ts src/features/agent/PublicAnswerView.test.tsx src/features/agent/sessions.test.tsx src/features/agent/assistant-ui.test.tsx src/features/agent/followup.test.tsx src/features/agent/chat.test.tsx
npm.cmd --prefix frontend run build
```

---

## 四、历史评测冻结资产说明

仓库中的 `tests/test_agent_stage1_v2_fixes.py` 与 `tests/test_agent_stage1_remaining_p1.py` 属于早期 Stage-1 审计快照：前者校验 `docs/evaluation/agent-stage1-v2-fix/final/source-revision.v5.json` 历史源码 SHA-256 冻结哈希，后者依赖早期非 `reviews_csv` 的 `oracle.v1.json` 语料（已被现行 `review_mvp_scope.py` 守卫拦截）。二者作为历史审计归档保留原样，新阶段质量回归统一以本手册所述单测与 `tests/eval_ai_assistant_benchmark.py` 为准。
