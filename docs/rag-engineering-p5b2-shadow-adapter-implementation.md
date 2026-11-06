# P5-B2：默认关闭 Shadow 适配器实现（待本机回归）

2026-11-06。基于 P5-B1 原型与三份 Schema，在独立分支建立了生产侧 Shadow-only 结构校验与有界子进程运行器；**未挂入正式回答链路，未调用数据库、Embedding 或外部 LLM，未运行本机完整回归，不可宣称通过 P5-B2 验收**。

## 变更

1. `src/data_analysis_agent/agents/p5b_shadow_contract.py`：从 P5-B1 测试原型独立移植输入绑定、来源核验、精确 Unicode 引用、事实/关系矩阵与结构审计。额外拒绝 ID 式事实描述。ShadowOutcome 恒定 `semantic_verified=false` / `publication_allowed=false` / 空许可集合。原 B1 测试原型、Schema、v2 许可逻辑不改。
2. `src/data_analysis_agent/agents/review_task_contract.py`：为现有 `reported_experience_v2` 已知事实 ID 提供人类可读的保守描述，在可信目标产生边界构建；不增加新事实或推理边，不开放任意自由改写。
3. `src/data_analysis_agent/agents/p5b_shadow_adapter.py`：`ShadowSettings(enabled=False)` 为默认，提供显式命令的独立子进程结构化判断接口。对输入和输出 UTF-8 字节、事实和候选数量、粗略 token 预算、运行截止时间有限制；stdin/out 分线程，超时/输出溢出终止 Worker，Unix 使用进程组终止，Windows 尝试 taskkill /T /F。
4. `tests/test_p5b2_shadow_adapter.py`：新增默认关闭、原目标描述、可信来源、实际子进程超时、stdin 阻塞、输出溢出、错误回退、Shadow 不发许可等隔离测试。

## 安全和兼容性边界

- 不启用真实模型；`worker_argv` 必须由调用方明确提供可信程序，不读取评论中的指令，不执行模型生成的命令；此运行器不是不可信任意代码的安全沙箱。
- 现有主链路、历史读取、v2 assessment、R0 / 人工裁决、SQL、向量及冻结材料均不作 Shadow 覆盖。
- `prepare_input` 只应接受 P0 已在原只读事务核验的可信来源记录；不得通过模型返回对象构造可信来源。
- 新适配器不能根据自由改写自动补目标；若原 RuntimeQueryContract 仍 unsupported/retrieval_only，必须在调用前停止。
- 结构通过不等于语义正确；复杂否定、时间先后和假设仍需真实人工金标准对照。
- 当前模块尚未安装运行时 hook、未引入私有 Shadow 原始输出持久化、尚未固定真实模型 tokenizer 与预算、未验证外部服务的取消语义。若后续允许真实 LLM，仍须评估调用费用、取消是否传播到远端以及私有数据防护。

## 待本机验收

请在用户真实 Windows 项目副本中、隔离数据库和外部网络后运行：

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_p5b2_shadow_adapter.py -v
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_p5b1_structured_evidence_design.py -v
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_review_task_contract.py -v
git diff --check
```

再按原 P5-B1 报告的回归脚本运行全部适用测试，核对旧结果字节级不变（新契约描述字段除外）、两项既有队列错误独立记录。不执行真实 PostgreSQL、BGE-M3 或外部 LLM；不得填写未经运行的通过数量。

## 后续准入

完成本地回归与 source-diff review 后，再设计显式启用的独立 Shadow 诊断 hook 和私有留痕；运行时检查确保不阻塞正式回答，原始输出不会进入前端/历史许可。之后才可讨论授权的真实模型 Shadow 和独立未见语义质量评测。**在完成上述步骤前，P5-B2 视为部分实现，尚未验收。**
