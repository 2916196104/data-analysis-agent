# RAG Step 2B：Embedding 模型本地验证与语义效果评测

日期：2026-11-03（Asia/Taipei）。前置依据：[数据结构审查](rag-data-structure-audit.md)、[有效观点语料契约](rag-effective-corpus-contract.md)。实际推理始于本地时间 2026-11-03 00:21:38，使用预先保存的合成语料，未连接业务 PostgreSQL 或读取真实评论。

**结论：BAAI/bge-m3 已在本机 CPU 成功运行，实际稠密输出为 1024 维、float32、CLS 池化、L2 归一化。** 无异常向量，批量顺序与跨批大小一致性检查通过。它可作为下一阶段的模型候选；本轮未创建向量表或执行数据同步。上下文方案在本合成挑战集上改善召回，但没有修改 Step 2A 的 `quote-utf8-v1`。

## 1. 环境与依赖检查

| 检查项 | 实测结果 |
|---|---|
| 操作系统 | Windows 11，Python 报告版本 `10.0.26300` |
| 业务 Python | `.venv/Scripts/python.exe`，CPython 3.12.10，64 位 |
| CPU | Intel Core Ultra 7 155H，16 个物理核心、22 个逻辑处理器 |
| 内存 | 总计约 31.50 GiB；前置检查可用约 8.55 GiB；实际推理进程导入 Torch 后可用约 7.35 GiB |
| 显卡 | Intel Arc Graphics 核显；未发现 NVIDIA 设备或 `nvidia-smi` |
| 本轮推理设备 | CPU，4 个 Torch 计算线程、1 个 inter-op 线程；当前 CPU 构建的 `cuda.is_available()`、`xpu.is_available()` 均为 false |
| GPU 验证边界 | 没有 GPU 推理或 GPU 利用率/显存测试；这不表示 Intel 核显在其他构建和驱动下必然不能加速 |
| 项目盘空间 | 检查时 E 盘可用约 222.37 GiB；C 盘可用约 21.68 GiB，模型和新增依赖放到 E 盘 |
| 原依赖管理 | `pyproject.toml` + 业务 `.venv`；原环境没有 torch、transformers、sentence-transformers、FlagEmbedding、huggingface-hub、psutil；原 NumPy 为 2.5.4 |

本轮创建独立环境 `.cache/embedding-eval/venv`，新增 [requirements-embedding-eval.txt](../requirements-embedding-eval.txt)。没有将模型依赖加入默认业务安装，也没有升级业务虚拟环境。

| 评测依赖 | 实际安装版本 | 选择与影响 |
|---|---|---|
| PyTorch | `2.9.1+cpu` | 固定 CPU wheel，不下载 CUDA 运行库 |
| Transformers | `4.57.6` | 使用内置 XLM-RoBERTa 实现，不执行远端模型代码 |
| NumPy | `2.2.6` | 仅独立环境的数值与相似度计算；不覆盖业务 NumPy |
| psutil | `7.2.2` | 进程 CPU 时间、RSS、系统可用内存测量 |
| huggingface-hub | `0.36.2` | Transformers 解析出的依赖，用于单独下载及离线缓存定位 |
| tokenizers | `0.22.2` | 实际快速 tokenizer 后端 |

这些是本轮固定并实测兼容 Python 3.12 的组合，没有直接升级到当时索引中的最新 Torch/Transformers 大版本。CPU wheel 安装方式核对了 [PyTorch 官方历史版本说明](https://pytorch.org/get-started/previous-versions/)。完整解析结果见 [评测依赖清单](../outputs/rag-embedding/evaluation-dependencies.json)。

业务环境前后核对：54 个已安装分发包的名称/版本集合完全一致；`pyproject.toml`、Step 2A 的 `scope.py/rag_corpus.py` 及 Agent 入口源码哈希未变。见 [隔离检查](../outputs/rag-embedding/isolation-check.json)。

默认 Hugging Face 缓存位于用户目录的 `.cache/huggingface/hub`，其中没有 BGE-M3；本轮改用项目内 `.cache/embedding-eval/hf/hub`。模型缓存与虚拟环境均被现有 `.cache/` 忽略规则排除，不加入 Git。

## 2. 模型身份、下载和本地加载

| 项目 | 已确认值 |
|---|---|
| 模型 | `BAAI/bge-m3` |
| 固定修订 | `5617a9f61b028005a4858fdac845db406aefb181`，后续加载不依赖可变 `main` |
| 获取来源 | Hugging Face 官方 BAAI 模型仓库；只下载配置、tokenizer、稠密权重相关的 8 个文件 |
| 总文件大小 | 2,293,315,624 字节，约 2.14 GiB |
| 主权重 | `pytorch_model.bin`，2,271,145,830 字节 |
| 主权重 SHA-256 | `b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38` |
| 加载方式 | Transformers `AutoModel`，固定 repo/revision，`local_files_only=True`、`trust_remote_code=False`、`weights_only=True`、`token=False` |
| 推理隔离 | `HF_HUB_OFFLINE=1`，关闭遥测及隐式 token；Python 审计钩子拒绝 `socket.connect`，本次实际连接尝试为 **0** |

下载和推理是两个独立步骤：下载只请求公开模型文件，不传入任何评论；推理进程只读取缓存。没有第三方 Embedding API 调用，也未安装 Sentence Transformers、FlagEmbedding、LangGraph 或向量数据库框架。

来源：[官方模型卡](https://huggingface.co/BAAI/bge-m3)、[固定修订配置](https://huggingface.co/BAAI/bge-m3/blob/5617a9f61b028005a4858fdac845db406aefb181/config.json)、[固定修订池化配置](https://huggingface.co/BAAI/bge-m3/blob/5617a9f61b028005a4858fdac845db406aefb181/1_Pooling/config.json)。本机下载文件确认隐藏维度 1024、启用 CLS 池化而非 mean pooling；实际输出维度另经推理验证，未只凭模型卡认定。

可追溯文件清单、各文件哈希及实际缓存路径见 [下载清单](../outputs/rag-embedding/model-download.json)。

## 3. 内部编码接口与向量空间

新增 [reviews/embedding.py](../src/data_analysis_agent/reviews/embedding.py)，不被现有 Agent、标注或 SQL 路径调用。

```python
from data_analysis_agent.reviews.embedding import LocalBgeM3Embedder

encoder = LocalBgeM3Embedder(
    ".cache/embedding-eval/hf/hub", batch_size=8, device="cpu"
)
vectors = encoder.embed_texts(["客服回应很慢。", "配送及时。"])
# vectors: list[list[float]]；和输入顺序一一对应。
space = encoder.space
```

接口约束：

- 必须传字符串列表；非字符串、空白字符串、非法 UTF-8 拒绝。空列表返回空列表。
- 不 trim、不转换换行、Unicode 或大小写，不拼接提示、维度、情感、商品或用户身份。输入到 tokenizer 前保持逐字不变。
- 首版运行上限为 **512 tokens，包含特殊 token**。超过上限明确报错，`truncation=False`，没有截断 Step 2A 的原文。模型更长序列能力不等于本机已验证长文本性能。
- 按原顺序切批，返回维度统一；检查有限值、非零范数并做 L2 归一化。异常时整次调用报错，不返回部分结果，不输出原文到错误消息。
- 只支持固定身份的 BGE-M3；模型配置类型或隐藏维度不符时拒绝。请求不可用 CUDA 时明确失败，不静默换设备或模型。

tokenizer 内部预处理属于固定模型文件的一部分；上述“不改写”指适配层不修改原始字符串，不代表不同 Unicode 字节的文本必然产生不同向量。Step 2A 输入哈希仍按原始 UTF-8 字节计算。

实际向量空间：

```json
{
  "model_name": "BAAI/bge-m3",
  "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
  "dimension": 1024,
  "pooling": "cls",
  "normalization": "l2",
  "dtype": "float32",
  "distance": "cosine",
  "implementation": "transformers-cls-eager-v1",
  "input_transform": "none",
  "query_instruction": "",
  "max_input_tokens": 512,
  "space_id": "embedding-space-v1:11e6754662dbd13fc56c6e624bc9416c1c2766f962787267fd4a57b1fa8d622e"
}
```

`space_id` 对以上配置（除 ID 本身）的规范 JSON 计算完整 SHA-256，使用项目已有哈希函数。修订、维度、池化、归一化等改变会产生不同身份；批大小不改变空间身份。后续持久化和检索必须绑定这个身份，不能因为维度相同就混用其他模型。裸 `list` 向量不自带标签，调用方仍有保存/核对 `space_id` 的责任；本轮评测始终使用同一个固定实例分别建立 B、C 集合，没有混排两个输入方案。

88 条语义候选/查询向量和 768 条性能测试输出均通过形状、有限值、非零和单位范数校验。88 条向量的范数范围为 `[0.9999998808, 1.0000001192]`；不同批大小最大逐元素差为 `1.1921×10⁻⁷`，在校验容差 `1e-5` 内。归一化点积可能因 float32 舍入略高于 1，不应解释成概率。

## 4. 合成语义测试与相关性标准

固定夹具：[rag_embedding_cases.json](../tests/fixtures/rag_embedding_cases.json)。36 条候选覆盖客服、物流、使用效果、质量、包装、价格，包含正负情感、同义、否定、反义、跨维度干扰以及多来源相同短句。16 个查询中，12 个意图明确，4 个裸短句只作歧义诊断。

评分前已写入规则与 qrels，夹具哈希为 `ba99186e8e6fa84098084d4da1f1184aae6cf2dd1ce1844d191958567d8e7d2c`，编码后未修改相关性标签：

- 2：同一方面、同一问题/意图及情感方向一致，视为 Recall、Precision、MRR 的相关项。
- 1：同方面同方向，但未明确覆盖该问题，作为部分相关，仅进入分级 nDCG。
- 0：无关、跨方面、相反方向或没有标注支持的结果。

这些是**可由人逐条复核的预先规则和合成标签**，不是人工团队生产标注，也不是让模型看分数后判断正确。标签依据合成完整上下文定义，故特意包含 B 无法从短 quote 恢复信息的挑战；不能将结果当成真实评论准确率。

在全体 36 条候选上做归一化点积的精确排序，不建数据库索引。相同分数按候选 ID 排序；`1e-6` 内另标为近似同分，避免把跨批次约 `1e-7` 的差异当作语义区分。诊断容差修订后使用已保存向量重算，相关性指标未变，没有重新编码或调整标签。

### 4.1 用户指定的四类查询

以下列出 Top 3；括号为预定相关等级，候选完整原文与标签可在夹具逐项核对。

| 查询 | B：纯观点 | C：观点加必要上下文 |
|---|---|---|
| 客服一直不回复。 | s01 客服久等未回复(2)；s02 售后无人答复(2)；l03 快递迟迟未到(0) | 相同 Top 3；补回的短客服观点出现在后续名次 |
| 配送速度很快。 | l02 配送及时(2)；l01 第二天送达(2)；s03 客服回复及时(0) | l06 很快＋配送上下文(2)；l02(2)；l01(2) |
| 空调降温比较慢。 | e02 制冷弱降温不理想(2)；e01 温度降不下来(2)；e07 不是不制冷而是耗时长(2) | e06 很慢＋降温上下文(2)；e02(2)；e01(2) |
| 用了不久就出现故障。 | q02 不到一月就坏(2)；q01 两天无法开机(2)；q06 外壳毛刺(1) | q02(2)；q01(2)；q04 太差了＋两天故障上下文(2) |

例如“客服一直不回复”对 s01 的相似度约 0.9185，对错误维度 l03 仍约 0.7610；高分不证明维度正确。B、C 对四个核心问题的 Top 1 都符合规则，但其后续召回质量不同。

额外查询覆盖客服回复及时、快递迟到、降温快、睡眠噪声、长期无故障、包装破损、使用方便和售后差。否定样例包括“降温不慢，很快就凉快了”“不是不制冷，只是降温需要较长时间”“没有出现故障”。全部名次及分数保存在 [evaluation.json](../outputs/rag-embedding/evaluation.json)。

### 4.2 整体效果与干扰

下表是 12 个明确意图查询的宏平均；每个查询有 1～5 个相关项。Recall 的分母为该查询全部 grade=2 候选，**不是查询数，也不是准确率**。nDCG@5 使用 `2^grade-1` 增益。

| 指标 | B | C |
|---|---:|---:|
| Top 1 命中 | 10/12，83.33% | 12/12，100% |
| Recall@3 | 53.61% | 84.17% |
| Recall@5 | 64.72% | 94.17% |
| Precision@3 | 44.44% | 69.44% |
| MRR | 0.8750 | 1.0000 |
| nDCG@5 | 0.6671 | 0.9195 |
| Top 3 跨维度比例 | 38.89% | 19.44% |
| Top 3 相反情感比例 | 8.33% | 8.33% |

**上下文没有解决所有情感问题。** 例如 C 查询“空调很快就凉快了”仍可能把“降温很慢”的上下文排在第三；查询“包装破了”仍把“包装完好”排在第二。因此向量距离不能替代现有维度、情感及有效标注过滤。

附加诊断：若按夹具已知的目标维度/情感先过滤，B/C 的 Recall@3 分别为 87.64%/90.42%，Recall@5 为 98.33%/100%。这是**已知正确过滤条件下的实验**，不是已经实现的意图识别结果；没有改动现有 Agent。过滤后仍有同维度不同问题干扰，不能只靠标签得到语义检索结论。

## 5. 短观点与上下文方案比较

首版 B 严格取 `quote`。合成 C 在 quote 与原句不相等时使用 `quote + "\n上下文：" + context`；原句已经等于 quote 时不添加内容。没有加情感或维度标签，没有生成、改写真实原文；C 仅为评测构造，标记 `synthetic-quote-context-v1`，没有更改当前语料读取逻辑。

| 裸短查询/候选 | B 中相同原文的候选数 | 可检查的歧义 |
|---|---:|---|
| 好用 | 2 | 一条上下文正向；另一条是“根本谈不上好用”的否定，纯 quote 无法恢复否定 |
| 不错 | 4 | 分别评价使用效果、客服、质量、性价比 |
| 很慢 | 3 | 分别为客服回复、物流、空调降温 |
| 太差了 | 4 | 分别为服务、效果、质量、包装 |

B 中这些同文候选基本同分；ID 或浮点舍入决定的先后不是语义判断。C 让候选的方面与否定信息进入输入，可改善有明确意图的查询；例如“空调使用效果好，操作方便”在 B 的 Top 3 没有相关项，C 能找到 e05、e10。

但裸查询自身仍缺信息：C 对“好用”的 Top 1 是一条配送及时观点，无法据此判断用户究竟在问什么。四个裸短查询**没有分配唯一正确答案、没有进入准确率或 Recall 分母**。C 返回不再同分，也不等于已经解决用户意图。

| 输入与耗时，36 条候选 | B | C |
|---|---:|---:|
| 总字符数 / 平均字符数 | 323 / 8.97 | 621 / 17.25 |
| 总 tokenizer tokens / 最大单条 tokens | 344 / 17 | 567 / 24 |
| 本轮编码总耗时，batch=8 | 4.140 秒 | 6.401 秒 |
| 平均每候选耗时 | 115.0 ms | 177.8 ms |

本合成挑战集上 C 的 Recall@3 增加 30.56 个百分点，输入量和时间也增加；这不是普遍保证。语料刻意包含信息缺失短句，规模小、没有独立留出集或多人一致性标注，不能据此直接改生产输入。后续若升级 C，应另定上下文窗口、原文区间、否定保留规则及新的 `text_builder_version`，再验证版本/证据一致性；本轮仅提出建议。

## 6. 性能与内存

### 6.1 加载与单条编码分别计时

| 阶段 | 实测时间 |
|---|---:|
| 首次下载 8 文件 | 101.654 秒 |
| 下载加本机 SHA-256 清单校验 | 103.952 秒 |
| 新推理进程导入 Torch | 2.379 秒 |
| 首次模型加载 | 10.460 秒 |
| 首次单条编码 | 0.453 秒 |
| 预热后单条编码，5 次中位数 | 0.174 秒，范围 0.168～0.182 秒 |

模型加载不包含下载；其计时包含 Transformers 加载工作。文件刚下载，未清除操作系统页缓存，不能称为冷启动磁盘/冷启动整机基准。单条及吞吐计时包含 tokenizer、推理、归一化和 Python 列表转换。

### 6.2 不同批大小

相同 64 条合成短文本循环自候选集，不做结果缓存；每种批大小 3 次，已完成模型预热。批大小按 1、4、8、16 的顺序执行，没有随机交错；下述结论仅适用于本次负载与机器状态。

| batch_size | 64 条耗时中位数 | 吞吐，条/秒 | 该组进程 RSS 峰值 |
|---:|---:|---:|---:|
| 1 | 8.103 秒 | 7.90 | 1.704 GiB |
| 4 | 6.719 秒 | 9.53 | 1.700 GiB |
| 8 | 4.610 秒 | 13.88 | 1.708 GiB |
| 16 | 5.102 秒 | 12.54 | 1.715 GiB |

本次 batch=8 最快，继续加大没有变快。各次原始耗时、向量差异及资源采样都在 `evaluation.json`。不据此估算真实全量回填耗时，真实观点长度和并发负载尚未验证。

进程编码 CPU 利用率约为 **396%～397%（100%=一个逻辑核心）**，折算 22 个逻辑处理器的整机容量约 **18%**，符合固定 4 个计算线程的配置。不是整台电脑只有 18% 忙碌：其他应用负载未计入该进程指标。

整个推理评测的进程 RSS/Windows peak working set 峰值为 **1,841,369,088 字节，约 1.715 GiB**；采样中的系统最低可用内存约 **5.04 GiB**。RSS 每 50 ms 采样并记录 Windows 进程生命周期峰值，不等于模型文件大小、专用内存或整机内存总开销。未进行长时间压力、最长 8192-token 输入或多 Worker 并发测试。

GPU 利用率与 GPU 内存：未测，本进程只使用 CPU；不将其他应用使用核显的活动记入模型性能。

## 7. 测试代码、执行与异常

### 7.1 可复现流程

从项目根目录执行，网络仅用于独立安装和 `--prepare`，默认评测本身离线：

```powershell
.venv/Scripts/python.exe -m venv .cache/embedding-eval/venv
.cache/embedding-eval/venv/Scripts/python.exe -m pip install -r requirements-embedding-eval.txt
.cache/embedding-eval/venv/Scripts/python.exe -B -u scripts/evaluate_embeddings.py --prepare
.cache/embedding-eval/venv/Scripts/python.exe -B -u scripts/evaluate_embeddings.py
```

独立环境不安装整个业务依赖。由于项目采用 `src/` 布局，运行模型接口单测需在测试进程中加入源码路径：

```powershell
@'
import sys, unittest
sys.path.insert(0, "src")
suite = unittest.TestLoader().discover("tests", pattern="test_embeddings.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
'@ | .cache/embedding-eval/venv/Scripts/python.exe -B -

.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_rag_corpus.py -v
```

### 7.2 执行结果

| 检查 | 结果与实际验证范围 |
|---|---|
| `tests/test_embeddings.py` | 独立环境 **12/12 通过**；非法输入、批次顺序、原文保留、固定本地加载、模型身份、超长不截断、NaN/Inf/零向量/错维度、异常不回显、近似同分及指标分母 |
| `tests/test_rag_corpus.py` | 原业务环境 **21/21 通过**，Step 2A 语料契约回归 |
| 真实模型运行 | `status=completed`；36 候选 × B/C、16 查询；各批大小 64 文本 × 3 次；校验通过 |
| 真实模型非法输入 | 7 组非法输入被拒绝；空列表行为正确 |
| 网络审计 | 推理进程连接尝试 0 |
| 代码与环境 | 三个新增 Python 文件语法检查通过；业务依赖和既有关键源码未变 |

单测中的异常模型是注入的模拟编码器，只用于验证接口防护，**不作为真实模型效果证据**。真实语义与性能来自 `evaluate_embeddings.py` 的本地权重运行。见 [单测记录](../outputs/rag-embedding/embedding-unit-tests.log)、[评测 JSON](../outputs/rag-embedding/evaluation.json) 和 [合成向量矩阵](../outputs/rag-embedding/synthetic-vectors.npz)。

### 7.3 已遇到的问题和处理

- 默认受限环境的 `venv/ensurepip` 初始化失败，使用正常本机权限在同一独立目录完成；没有重建业务环境。
- Windows 缓存提示无法使用符号链接，使用普通文件缓存后下载成功；没有修改系统开发者模式。模型文件与缓存空间已记录。
- 首次在独立环境直接发现测试时因 `src/` 布局找不到项目包，按上述进程内源码路径运行后通过；未因此安装全部业务依赖。
- 模型提示两条 `pooler.dense.*` 权重未使用：本实现明确关闭 pooler，按下载的池化配置直接取 CLS；这是预期加载提示，不是随机缺失稠密编码权重。
- 本轮没有实际 OOM、推理失败或坏向量；相关失败路径仅通过异常注入测试。没有静默切换轻量模型。

## 8. 模型选择建议

建议保留 **固定修订 BAAI/bge-m3 + 1024 维 + float32 + L2 + cosine** 作为下一阶段起点。它已在这台机器上完成本地运行，现有内存和 CPU 能承载本次短文本负载，因此没有必要在本轮因猜测资源不足而更换模型。

CPU 默认可先沿用评测的 4 个计算线程、batch=8、单实例复用；这是起始配置，不是并发容量保证。实时批量处理和更长文本需要单独验收，不应每个请求重新加载模型。GPU 加速、量化、轻量候选和其他模型的质量/成本对比都未在本轮实测，不宣称 BGE-M3 已是所有候选中最优。

输入继续使用 Step 2A 的 **B / quote-utf8-v1**。C 的改善说明短观点需要保留上下文的后续价值，但当前先维持来源与版本契约；无上下文的短句不能仅凭语义距离判断方面或正负。检索必须沿用所选数据集、商品、有效标注配置及必要的维度/情感过滤。

## 9. 下一阶段 pgvector 与同步前置条件

| 项目 | 已确定 / 尚需完成 |
|---|---|
| 向量列维度依据 | 实际输出 **1024**，可据此评审固定维度列；本轮无 DDL 或迁移 |
| 空间身份 | 保存固定模型修订及完整 `embedding_space_id`；不能只保存模型简称或维度 |
| 距离/归一化 | 以 cosine 与单位范数为本轮确定参数，持续校验有限值和非零向量 |
| 文本身份 | 保留 `quote-utf8-v1`、`embedding_input_sha256`，不在编码层改写原文；C 如采用须另定版本 |
| 长度策略 | 本轮上限 512 tokens。Step 2A 允许的观点原文可能更长，需先确定超限拒绝/显式分块等业务方案，不能直接静默截断；8192-token 性能未验证 |
| 来源/有效性 | 关联 Step 2A 的数据集、商品、评论、标注、人工修订及真实观点行；成功空快照也须参与失效对账 |
| 发布和并发 | 编码后重验内容/有效集合摘要，定义原子发布、错误重试与旧向量失效协议；这些尚未实现 |
| 检索边界 | 在代码中冻结范围，按当前有效观点回连证据；Top K 和相似度不替代 SQL 统计分母或正负标签 |
| 索引/容量 | 先验证小范围精确检索与筛选；ANN 参数、长文本/并发压力、GPU 和真实负载耗时另行评估 |

新增源文件仅为模型接口、离线评测脚本、固定依赖清单、合成夹具、单测和本报告。生成证据保存在 `outputs/rag-embedding/`，模型与依赖在 `.cache/embedding-eval/`。本轮未修改数据库、有效标注选择、标注/SQL 分析、Agent 或前端，也未处理真实评论、进行全量回填或接入检索。

Step 2B 到此停止，等待下一步开发指令。
