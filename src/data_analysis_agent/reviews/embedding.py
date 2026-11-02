"""固定修订 BGE-M3 的本地稠密编码；不下载模型、不访问业务数据。"""

from dataclasses import asdict, dataclass
from pathlib import Path

from data_analysis_agent.core.serialization import content_sha256

MODEL_NAME = "BAAI/bge-m3"
MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
MODEL_FILES = (
    "config.json", "pytorch_model.bin", "tokenizer.json", "tokenizer_config.json",
    "special_tokens_map.json", "sentencepiece.bpe.model", "1_Pooling/config.json", "modules.json",
)


class EmbeddingError(RuntimeError):
    """本地模型加载、推理或输出校验失败。"""


@dataclass(frozen=True)
class EmbeddingSpace:
    model_name: str
    model_revision: str
    dimension: int
    pooling: str = "cls"
    normalization: str = "l2"
    dtype: str = "float32"
    distance: str = "cosine"
    implementation: str = "transformers-cls-eager-v1"
    input_transform: str = "none"
    query_instruction: str = ""
    max_input_tokens: int = 512

    @property
    def space_id(self) -> str:
        return "embedding-space-v1:" + content_sha256(asdict(self))


class LocalBgeM3Embedder:
    def __init__(self, cache_dir: str | Path, *, batch_size: int = 8,
                 max_input_tokens: int = 512, device: str = "cpu"):
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size 必须是正整数。")
        if type(max_input_tokens) is not int or not 2 <= max_input_tokens <= 8192:
            raise ValueError("max_input_tokens 必须为 2 至 8192 的整数。")
        if device not in ("cpu", "cuda"):
            raise ValueError("device 仅支持 cpu 或 cuda，不能自动切换向量配置。")
        try:
            import torch
            from transformers import AutoConfig, AutoModel, AutoTokenizer
        except (ImportError, OSError):
            raise EmbeddingError("请在独立评测环境安装 requirements-embedding-eval.txt。") from None
        if device == "cuda" and not torch.cuda.is_available():
            raise EmbeddingError("未发现可用 CUDA 设备，不自动回退 CPU。")
        self.batch_size, self.device, self._torch = batch_size, device, torch
        local = dict(revision=MODEL_REVISION, cache_dir=str(cache_dir), local_files_only=True,
                     trust_remote_code=False, token=False)
        try:
            config = AutoConfig.from_pretrained(MODEL_NAME, **local)
            if config.model_type != "xlm-roberta" or config.hidden_size != 1024:
                raise EmbeddingError("模型配置与固定 BGE-M3 向量空间不一致。")
            self.space = EmbeddingSpace(MODEL_NAME, MODEL_REVISION, config.hidden_size,
                                        max_input_tokens=max_input_tokens)
            self._tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, use_fast=True, **local)
            # 稠密向量取 CLS，不经过检查点中的 pooler，因此显式关闭该层。
            self._model = AutoModel.from_pretrained(
                MODEL_NAME, config=config, use_safetensors=False, weights_only=True,
                attn_implementation="eager", add_pooling_layer=False, **local,
            ).to(device=device, dtype=torch.float32).eval()
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"固定修订本地模型加载失败（{type(exc).__name__}）；请先单独准备模型缓存。") from None

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """按原顺序编码；空列表返回空列表，空白/非字符串/超限文本拒绝。"""
        if not isinstance(texts, list) or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("texts 必须是非空白字符串列表。")
        try:
            for text in texts:
                text.encode("utf-8")
        except UnicodeError:
            raise ValueError("输入必须能严格编码为 UTF-8。") from None
        torch, result = self._torch, []
        try:
            with torch.inference_mode():
                for start in range(0, len(texts), self.batch_size):
                    batch = texts[start:start + self.batch_size]
                    tokens = self._tokenizer(batch, padding=True, truncation=False, return_tensors="pt")
                    if tokens["input_ids"].shape[1] > self.space.max_input_tokens:
                        raise ValueError("输入超出 max_input_tokens，拒绝隐式截断。")
                    tokens = {key: value.to(self.device) for key, value in tokens.items()}
                    vectors = self._model(**tokens).last_hidden_state[:, 0].float()
                    if vectors.shape != (len(batch), self.space.dimension) or not torch.isfinite(vectors).all():
                        raise EmbeddingError("模型输出形状错误或包含 NaN/Inf。")
                    norms = torch.linalg.vector_norm(vectors, dim=1, keepdim=True)
                    if not torch.isfinite(norms).all() or (norms <= 1e-12).any():
                        raise EmbeddingError("模型输出包含零向量或异常范数。")
                    vectors = vectors / norms
                    if not torch.isfinite(vectors).all():
                        raise EmbeddingError("归一化后的向量无效。")
                    result.extend(vectors.cpu().tolist())
        except (ValueError, EmbeddingError):
            raise
        except Exception as exc:
            raise EmbeddingError(f"本地向量推理失败（{type(exc).__name__}），未返回部分结果。") from None
        return result
