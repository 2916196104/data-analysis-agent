# 品牌与商品优缺点分析修复

日期：2026-10-31。范围：评论分析中的正负评价概览与品牌/商品范围解析。

## 修复结果

“美的空调与小米空调的优缺点都有哪些？”已在当前 `reviews-csv` 真实数据上只读执行成功。查询覆盖数据集完整时间范围 **2020-08-25—2022-08-27**，按现有 DeepSeek V3.2 标注版本读取，不执行新增标注。页面原本只选 `100013588053`，本轮根据明确的两个品牌扩展到美的 7 款、小米 7 款商品。

执行耗时 **5.429 秒**，实际业务查询函数调用 **5 次**（范围读取 + 两组各正负方向查询），规划模型、回答模型调用均为 **0**；统计与引用校验通过。这是一次本机实测，不是延迟保证。真实结果保存于 [result.json](../output/playwright/pros-cons-live/result.json)，位于 Git 已忽略的本机验收目录，未写业务库或正式任务队列。

| 商品组 | 评论数 | 成功标注数 | 覆盖率 | 本次出现较多的正向表述 | 本次出现较多的负向表述 |
| --- | ---: | ---: | ---: | --- | --- |
| 美的空调，7 款 | 3236 | 2184 | 67.5% | 服务好；物流很快 | 制冷效果不好；噪音大 |
| 小米空调，7 款 | 741 | 707 | 95.4% | 送货速度快；制冷效果不错 | 不制冷；风力是坏的，那个档的风力都是一样的 |

这些是按已有观点原文精确归组的结果，同义表述未合并，不能当作完整主题排名或品牌整体质量结论。覆盖率和样本结构不同，不能据此直接判定哪个品牌更好。

## 代码中确认的问题及处理

- “优缺点”没有被识别为两个明确方向；现在保留双向意图并拆成必需查询。
- 原有商品匹配只支持 ID/完整名称；现在可解析明确的品牌与品类，使用当前数据集商品目录中的品牌字段分组，不任意选取一个型号。具体系列有多个候选时仍要求补充 ID。
- 已确定的优缺点查询仍依赖规划模型选择工具；现在通过原注册表直接执行固定计划，保留只读事务、范围限制、调用预算及完整发布校验。
- 正向观点文本结果曾尝试读取不存在的正向汇总字段；适配时不再为文本归组计算不适用的汇总指标。
- 缺少商品的具体原因在公开回答中被通用提示替换；现在保留可行动的说明，历史公开读取也兼容异常数据类型。
- 回答按商品组区分正负方向，保留样本量、覆盖率和原文引用。正文每方向最多展示两种表述；占比分母是该组包含对应情感观点的评论数。
- 范围栏展示服务端实际范围。查询快照下载同时保留原始页面选择 `selection` 和实际查询范围 `resolved_scope`，刷新不重新执行查询。

原始失败任务的执行 trace 未作为本次证据读取，因此不能断言历史 `timeout` 具体发生在哪一轮调用。本次验证证明修复后的该类问题无需规划/回答模型即可完成。

## 文件变更

- `src/data_analysis_agent/agents/review_prompts.py`、`review_semantics.py`、`review_plan.py`：双向意图、商品/品牌解析及固定分组计划。
- `src/data_analysis_agent/agents/reviews.py`：直接调度、预算预检、具体不支持原因；执行版本更新为 `review-agent-v7`。
- `review_results.py`、`review_compression.py`、`review_answer_facts.py`、`review_answers.py`：正向文本结果适配、分组快照、回答与安全历史读取。
- `frontend/src/features/agent/ChatMessage.tsx`、`chat.test.tsx`：实际范围显示和快照追溯。
- `tests/test_assistant_semantics.py`：增加品牌多型号、原问题、缺少方向、未知/模糊商品、预算、错误数据及历史读取回归。
- `frontend/tests/pros-cons.e2e.mjs`：隔离四型号数据、真实 API/worker/Vite/Chromium 的完整验收。
- `docs/review-agent.md`：更新用法、范围规则和边界。

API 路由、提交请求必填字段、数据库结构及原有统计口径未修改。

## 验证

- 后端：运行 208 项，205 项通过，3 项外部 PostgreSQL 测试跳过；见 [后端日志](../output/pros-cons-brands-backend-final.log)。使用 `LABEL_EXECUTION_MODE=local` 与临时数据库、模拟模型。
- 前端：22 个文件、197 项通过；见 [前端日志](../output/pros-cons-brands-frontend-final.log)。
- TypeScript/Vite 构建通过，保留原有大于 500 kB 的分包提示；见 [构建日志](../output/pros-cons-build-delivery.log)。
- 浏览器：1440 / 1024 / 768 / 390 像素无横向溢出，输入区在视口内，文本正常换行；已查看桌面及窄屏截图。提交、复制、公开历史读取、查询快照下载、刷新恢复和未知商品提示通过，浏览器错误 0，模型请求 0，隔离业务库哈希不变；见 [验收日志](../output/pros-cons-brands-browser-final.log)。
- `git diff --check` 通过。沙箱内 Python 的本机 socketpair 会卡住，需本机回环网络的后端/浏览器验收已在获准的沙箱外环境执行。
- 初次扩大回归受本地 `.env` 的持久标注队列配置影响；显式指定 local 模式后最终回归通过，未更改用户配置文件。

复现核心回归：

```powershell
$env:LABEL_EXECUTION_MODE = 'local'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_assistant_semantics.py -v
npm --prefix frontend test -- --maxWorkers=2
npm --prefix frontend run build
node frontend/tests/pros-cons.e2e.mjs
```

## 生效与边界

更新代码后需重启当前后端和任务 worker，再提交新问题；本次没有停止用户原有进程，也没有改写旧失败任务。真实库验证使用数据集全时间范围，不代表复现了原历史任务未提供的日期窗口。

此轮支持最多两个品牌/商品组的正负观点概览。双向趋势、多个细分主题、指定双向原文条数/分页会明确说明需要分别查询；没有有效标注或没有匹配观点时只说明数据不足。没有新增任意 SQL、自动标注、因果判断或自由主题聚类能力。业务查询本身失败或结果校验不通过时，仍禁止发布成功回答。
