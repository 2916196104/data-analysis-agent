"""面向商品评论有效观点语料的 BM25 词法检索、RRF 混合融合与可插拔 Reranker。

纯确定性本地计算：不访问网络、不下载模型、不绕过 P0 只读事务快照与来源完整性校验。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Literal, Protocol
import unicodedata

from data_analysis_agent.reviews.embedding_store import OpinionMatch
from data_analysis_agent.reviews.rag_corpus import RagOpinionDocument

LEXICAL_SEMANTIC_RERANK_POLICY = "hybrid_lexical_semantic_rerank_v1"
CROSS_ENCODER_RERANK_POLICY = "local_cross_encoder_rerank_v1"

_ASCII_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_QUERY_FILLER_CHARS = frozenset("的了吗呢吧啊呀哦嗯嘛个些这那有没是在和与及也很非常比较请问什么哪些是否")
_BM25_STOP_UNIGRAMS = _QUERY_FILLER_CHARS | frozenset("到就都还又再已把被让给对从往向但而却或者因为所以")


def tokenize_for_bm25(text: str) -> list[str]:
    """中英混合分词：保留英文/数字词元、连续中文 2-gram 及去停用字后的实义 1-gram。"""
    if not text:
        return []
    normalized = unicodedata.normalize("NFKC", text).lower()
    tokens: list[str] = []
    tokens.extend(_ASCII_WORD_RE.findall(normalized))
    for run in _CJK_RUN_RE.findall(normalized):
        if len(run) == 1:
            if run not in _BM25_STOP_UNIGRAMS:
                tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
            tokens.extend(ch for ch in run if ch not in _BM25_STOP_UNIGRAMS)
    return tokens


def _content_terms(text: str) -> set[str]:
    """提取用于重排覆盖度评估的核心词元（ASCII 词元 + 去虚词后的中文 2-gram/单字）。"""
    if not text:
        return set()
    normalized = unicodedata.normalize("NFKC", text).lower()
    terms: set[str] = set(_ASCII_WORD_RE.findall(normalized))
    for run in _CJK_RUN_RE.findall(normalized):
        filtered = "".join(ch for ch in run if ch not in _QUERY_FILLER_CHARS)
        target = filtered or run
        if len(target) == 1:
            terms.add(target)
        else:
            for i in range(len(target) - 1):
                terms.add(target[i : i + 2])
    return terms


@dataclass(frozen=True)
class LexicalMatch:
    document: RagOpinionDocument
    bm25_score: float
    rank: int


@dataclass(frozen=True)
class HybridCandidate:
    document: RagOpinionDocument
    cosine_distance: float | None
    channel: Literal["hybrid", "vector", "bm25"]
    vector_rank: int | None
    bm25_rank: int | None
    bm25_score: float | None
    rrf_score: float


@dataclass(frozen=True)
class RerankItem:
    key: str
    quote: str
    review_text: str
    cosine_distance: float | None
    bm25_score: float | None
    rrf_score: float
    channel: str


class CandidateReranker(Protocol):
    policy: str

    def score(self, question: str, items: Sequence[RerankItem]) -> dict[str, float]: ...


class BM25Index:
    """基于 Lucene 非负 IDF 变体的轻量 Okapi BM25 内存索引（作用于当前事务内的有效观点集）。"""

    def __init__(self, documents: Sequence[RagOpinionDocument], *, k1: float = 1.5, b: float = 0.75):
        self.documents = tuple(documents)
        self.k1 = float(k1)
        self.b = float(b)
        self._doc_tfs: list[Counter[str]] = []
        self._doc_lens: list[int] = []
        df: Counter[str] = Counter()
        for doc in self.documents:
            tokens = tokenize_for_bm25(doc.quote)
            tf = Counter(tokens)
            self._doc_tfs.append(tf)
            self._doc_lens.append(len(tokens))
            for term in tf:
                df[term] += 1
        n = len(self.documents)
        self._avgdl = (sum(self._doc_lens) / n) if n else 0.0
        # 使用非负 Lucene IDF：在小样本单商品语料上仍保持严格正向区分度。
        self._idf = {
            term: math.log(1.0 + (n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def search(self, query: str, *, top_k: int = 20) -> tuple[LexicalMatch, ...]:
        if not self.documents or top_k <= 0:
            return ()
        query_terms = tokenize_for_bm25(query)
        if not query_terms:
            return ()
        query_tf = Counter(query_terms)
        scored: list[tuple[RagOpinionDocument, float]] = []
        avgdl = self._avgdl or 1.0
        for doc, tf, dl in zip(self.documents, self._doc_tfs, self._doc_lens, strict=True):
            if dl == 0:
                continue
            score = 0.0
            denom_norm = self.k1 * (1.0 - self.b + self.b * (dl / avgdl))
            for term, q_weight in query_tf.items():
                freq = tf.get(term, 0)
                if freq <= 0:
                    continue
                idf = self._idf.get(term, 0.0)
                if idf <= 0.0:
                    continue
                # 2-gram 词元比单字拥有更高精确匹配权重，抑制高频单字噪声。
                gram_boost = 1.35 if len(term) >= 2 else 0.65
                score += idf * gram_boost * min(q_weight, 3) * (freq * (self.k1 + 1.0)) / (freq + denom_norm)
            if score > 0.0:
                scored.append((doc, round(score, 6)))
        # 确定性排序：score 降序 -> published_at 降序 -> stable_versioned_opinion_key 升序。
        scored.sort(key=lambda item: item[0].stable_versioned_opinion_key)
        scored.sort(key=lambda item: item[0].published_at, reverse=True)
        scored.sort(key=lambda item: item[1], reverse=True)
        return tuple(
            LexicalMatch(document=doc, bm25_score=score, rank=rank)
            for rank, (doc, score) in enumerate(scored[:top_k], 1)
        )


def reciprocal_rank_fusion(
    vector_matches: Sequence[OpinionMatch],
    lexical_matches: Sequence[LexicalMatch],
    *,
    k: int = 60,
    vector_weight: float = 1.0,
    lexical_weight: float = 1.0,
    top_k: int = 20,
) -> tuple[HybridCandidate, ...]:
    """Reciprocal Rank Fusion (RRF)：融合稠密向量与 BM25 词法两路召回并保留完整来源排名。"""
    if top_k <= 0:
        return ()
    by_key_doc: dict[str, RagOpinionDocument] = {}
    vec_rank: dict[str, int] = {}
    vec_dist: dict[str, float] = {}
    lex_rank: dict[str, int] = {}
    lex_score: dict[str, float] = {}

    for rank, match in enumerate(vector_matches, 1):
        key = match.document.stable_versioned_opinion_key
        if key not in vec_rank:
            by_key_doc[key] = match.document
            vec_rank[key] = rank
            vec_dist[key] = float(match.cosine_distance)

    for match in lexical_matches:
        key = match.document.stable_versioned_opinion_key
        if key not in lex_rank:
            by_key_doc.setdefault(key, match.document)
            lex_rank[key] = match.rank
            lex_score[key] = float(match.bm25_score)

    fused: list[HybridCandidate] = []
    for key, doc in by_key_doc.items():
        v_rank = vec_rank.get(key)
        b_rank = lex_rank.get(key)
        score = 0.0
        if v_rank is not None:
            score += vector_weight / (k + v_rank)
        if b_rank is not None:
            score += lexical_weight / (k + b_rank)
        channel: Literal["hybrid", "vector", "bm25"] = (
            "hybrid" if (v_rank is not None and b_rank is not None)
            else "vector" if v_rank is not None
            else "bm25"
        )
        fused.append(
            HybridCandidate(
                document=doc,
                cosine_distance=vec_dist.get(key),
                channel=channel,
                vector_rank=v_rank,
                bm25_rank=b_rank,
                bm25_score=lex_score.get(key),
                rrf_score=round(score, 8),
            )
        )

    fused.sort(
        key=lambda row: (
            -row.rrf_score,
            row.cosine_distance if row.cosine_distance is not None else math.inf,
            -(row.bm25_score if row.bm25_score is not None else -1.0),
            row.document.stable_versioned_opinion_key,
        )
    )
    return tuple(fused[:top_k])


class LexicalSemanticReranker:
    """零依赖确定性融合重排器：结合向量余弦相似度、BM25 词法强度、RRF 共识与短语覆盖率。"""

    policy = LEXICAL_SEMANTIC_RERANK_POLICY

    def score(self, question: str, items: Sequence[RerankItem]) -> dict[str, float]:
        if not items:
            return {}
        query_terms = _content_terms(question)
        max_rrf = max((item.rrf_score for item in items), default=0.0) or 1.0
        scores: dict[str, float] = {}
        for item in items:
            dense_sim = (
                max(0.0, min(1.0, 1.0 - item.cosine_distance))
                if item.cosine_distance is not None and math.isfinite(item.cosine_distance)
                else 0.0
            )
            bm25_norm = (
                item.bm25_score / (item.bm25_score + 2.0)
                if item.bm25_score is not None and item.bm25_score > 0
                else 0.0
            )
            rrf_norm = max(0.0, min(1.0, item.rrf_score / max_rrf))
            if query_terms:
                quote_terms = _content_terms(item.quote)
                overlap = len(query_terms & quote_terms) / len(query_terms)
                # 整条评论中的额外语义命中只提供轻微加权，不替代原 quote 匹配。
                review_terms = _content_terms(item.review_text)
                context_overlap = len(query_terms & review_terms) / len(query_terms)
                phrase_cov = min(1.0, 0.8 * overlap + 0.2 * context_overlap)
            else:
                phrase_cov = 0.0
            channel_bonus = 0.05 if item.channel == "hybrid" else 0.0
            combined = 0.35 * dense_sim + 0.25 * bm25_norm + 0.20 * phrase_cov + 0.15 * rrf_norm + channel_bonus
            scores[item.key] = round(max(0.0, min(1.0, combined)), 6)
        return scores


class CrossEncoderReranker:
    """本地离线 Cross-Encoder（如 BGE-Reranker）适配器；也支持测试/外部注入打分函数。"""

    policy = CROSS_ENCODER_RERANK_POLICY

    def __init__(
        self,
        *,
        cache_dir: str | Path | None = None,
        score_fn: Callable[[list[tuple[str, str]]], Sequence[float]] | None = None,
        normalize_logits: bool = True,
    ):
        if cache_dir is None and score_fn is None:
            raise ValueError("CrossEncoderReranker requires cache_dir or score_fn")
        self.cache_dir = Path(cache_dir).resolve() if cache_dir is not None else None
        self._score_fn = score_fn
        self.normalize_logits = normalize_logits
        self._model = None

    def _load_local_model(self):
        if self._model is not None:
            return self._model
        if self.cache_dir is None or not self.cache_dir.is_dir():
            raise ValueError("local_reranker_cache_missing")
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(str(self.cache_dir), local_files_only=True)
        return self._model

    def score(self, question: str, items: Sequence[RerankItem]) -> dict[str, float]:
        if not items:
            return {}
        pairs = [(question, item.quote) for item in items]
        if self._score_fn is not None:
            raw_scores = list(self._score_fn(pairs))
        else:
            model = self._load_local_model()
            raw_scores = [float(v) for v in model.predict(pairs)]
        if len(raw_scores) != len(items):
            raise ValueError("invalid_reranker_scores")
        result: dict[str, float] = {}
        for item, raw in zip(items, raw_scores, strict=True):
            if type(raw) not in (int, float) or not math.isfinite(raw):
                raise ValueError("invalid_reranker_scores")
            value = float(raw)
            if self.normalize_logits:
                clamped = max(-40.0, min(40.0, value))
                value = 1.0 / (1.0 + math.exp(-clamped))
            result[item.key] = round(value, 6)
        return result
