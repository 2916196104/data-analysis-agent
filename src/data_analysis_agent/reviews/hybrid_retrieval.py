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
from threading import Lock
from typing import Literal, Protocol
import unicodedata

from data_analysis_agent.reviews.embedding_store import OpinionMatch
from data_analysis_agent.reviews.rag_corpus import RagOpinionDocument

LEXICAL_SEMANTIC_RERANK_POLICY = "hybrid_lexical_semantic_rerank_v1"
CROSS_ENCODER_RERANK_POLICY = "local_cross_encoder_rerank_v1"

_ASCII_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
# 注意：保留否定语义字（如“不、没、无、未”），避免把“没收到货/不制冷”洗成正向词。
_QUERY_FILLER_CHARS = frozenset("的了吗呢吧啊呀哦嗯嘛个些这那有是在和与及也很非常比较请问什么哪些是否")
_BM25_STOP_UNIGRAMS = _QUERY_FILLER_CHARS | frozenset("到就都还又再已把被让给对从往向但而却或者因为所以")

_DATE_PREFIX_RE = re.compile(
    r"^\s*(?:(?:请问|在|从)\s*)?\d{4}-\d{2}-\d{2}\s*(?:至|到|~|～|—|-)\s*\d{4}-\d{2}-\d{2}\s*(?:期间|之间|内)?[，,:：\s]*"
)
_PRODUCT_PREFIX_RE = re.compile(r"^\s*(?:商品|产品|型号)\s*[A-Za-z0-9_\-]{2,40}[，,:：\s]*")
_QUESTION_OPEN_RE = re.compile(
    r"^\s*(?:请问|帮我|麻烦|请帮我)?\s*(?:(?:当前|这款|这台|该)(?:商品|产品|空调))?\s*"
    r"(?:有没有|是否有|有无|是否存在|帮我找|找一下|查找|检索|搜索)\s*(?:买家|用户|消费者)?\s*"
    r"(?:反馈说|反映说|提到说|反馈|反映|提到|说)?\s*[“「\"']?"
)
_QUESTION_CLOSE_RE = re.compile(
    r"[”」\"']?\s*(?:的)?\s*(?:类似|相关)?\s*(?:买家|用户|消费者)?\s*"
    r"(?:反馈|抱怨|评论|评价|原文|原话|证据|记录|情况|问题)\s*(?:吗|么|呢|呀|啊)?\s*[？?。！!；;\s]*$"
)
_NEGATION_PREFIX_RE = re.compile(r"(?:没有|没|并未|未|并非|不是|不算|毫不|无|不)")
_NEGATIVE_FACT_RE = re.compile(
    r"(?:不行|不好|太差|很差|差劲|拒绝|推诿|敷衍|太慢|很慢|延迟|爽约|破损|碎了|漏水|滴水|异响|太吵|太大|太响|失灵|不灵|故障|偷工减料|乱收|收费高|偏贵)"
)
_POSITIVE_FACT_RE = re.compile(
    r"(?:很好|很快|神速|次日达|当天达|静音|安静|听不到|完好|无损|实惠|划算|性价比高|性价比很高|满意|不错|扎实|耐心|态度好|强劲|暖和|凉快)"
)
_CROSS_ENCODER_LOCK = Lock()
_CROSS_ENCODER_CACHE: dict[Path, object] = {}


def strip_question_shell(question: str) -> str:
    """剥离日期/商品前缀与“有没有……的反馈？”问句外壳，保留核心事实命题与否定词。"""
    if not question:
        return ""
    text = unicodedata.normalize("NFKC", question).strip()
    text = _DATE_PREFIX_RE.sub("", text)
    text = _PRODUCT_PREFIX_RE.sub("", text)
    text = _QUESTION_OPEN_RE.sub("", text)
    text = _QUESTION_CLOSE_RE.sub("", text)
    text = text.strip("“”「」\"' ，,。！？?!；;：:")
    return text or unicodedata.normalize("NFKC", question).strip()


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
    """提取用于重排覆盖度评估的核心词元（ASCII 词元 + 去虚词后的中文 2-gram/单字，保留否定字）。"""
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


def extract_lexical_terms(
    query: str,
    *,
    extra_queries: Sequence[str] = (),
    max_terms: int = 24,
) -> tuple[str, ...]:
    """提取用于 PostgreSQL 词法预筛与多路 BM25 的去重高区分度词元（优先 2-gram 与英文/型号词元）。"""
    if max_terms <= 0:
        return ()
    ordered_multi: list[str] = []
    ordered_uni: list[str] = []
    seen: set[str] = set()

    for raw in (query, *extra_queries):
        cleaned = strip_question_shell(raw)
        if not cleaned:
            continue
        normalized = unicodedata.normalize("NFKC", cleaned).lower()
        for word in _ASCII_WORD_RE.findall(normalized):
            if word not in seen:
                seen.add(word)
                (ordered_multi if len(word) >= 2 else ordered_uni).append(word)
        for run in _CJK_RUN_RE.findall(normalized):
            if 2 <= len(run) <= 6 and run not in seen:
                seen.add(run)
                ordered_multi.append(run)
            for i in range(len(run) - 1):
                bigram = run[i : i + 2]
                if all(ch in _QUERY_FILLER_CHARS for ch in bigram):
                    continue
                if bigram not in seen:
                    seen.add(bigram)
                    ordered_multi.append(bigram)
            for ch in run:
                if ch not in _BM25_STOP_UNIGRAMS and ch not in seen:
                    seen.add(ch)
                    ordered_uni.append(ch)

    selected = ordered_multi[:max_terms]
    if len(selected) < min(4, max_terms):
        remaining = max_terms - len(selected)
        selected.extend(ordered_uni[:remaining])
    return tuple(selected)


def _has_polarity_conflict(core_query: str, quote: str) -> bool:
    """保守检测核心目标词在问句与引文之间是否出现显式肯定/否定或反义极性翻转。"""
    if not core_query or not quote:
        return False
    q_norm = unicodedata.normalize("NFKC", core_query).lower()
    quote_norm = unicodedata.normalize("NFKC", quote).lower()
    quote_has_negative_fact = bool(_NEGATIVE_FACT_RE.search(quote_norm))
    quote_has_positive_fact = bool(_POSITIVE_FACT_RE.search(quote_norm))

    # 1. 显式语义反义冲突（不依赖字面是否恰好含“不/没”，如“噪音大睡不着”vs“静音听不到噪音”、“送货慢”vs“物流神速”）
    if re.search(r"(?:噪音|噪声|太吵|吵得|声音大|嗡嗡|异响)", q_norm) and re.search(
        r"(?:静音|很安静|听不到噪音|没噪音|没有噪音|无噪音|不大|很小)", quote_norm
    ):
        return True
    if re.search(r"(?:慢|延迟|太久|拖延)", q_norm) and re.search(
        r"(?:不慢|不算慢|并不慢|没有慢|速度正常|很快|神速|次日达|当天达)", quote_norm
    ):
        return True
    if re.search(r"(?:破损|碎|压痕|湿透|浸水|漏水|滴水)", q_norm) and re.search(
        r"(?:完好|无损|不漏水|没漏水|没有漏水|没有破损|无破损)", quote_norm
    ):
        return True
    if re.search(r"(?:态度差|推诿|不处理|不给|拒绝|乱收|降价|差评|吐槽)", q_norm) and quote_has_positive_fact and not quote_has_negative_fact:
        return True

    query_negated = bool(_NEGATION_PREFIX_RE.search(q_norm))
    quote_negated = bool(_NEGATION_PREFIX_RE.search(quote_norm))

    if not query_negated and quote_negated:
        # 问句问“包装破损/噪音大/制冷慢”，引文直接说“没有破损/无噪音/不慢/不算慢”。
        stems = [
            token for token in extract_lexical_terms(q_norm, max_terms=8)
            if len(token) >= 2 and not _NEGATION_PREFIX_RE.search(token)
        ]
        for stem in stems:
            if re.search(r"(?:没有|没|并未|未|并非|不是|不算|无|不)" + re.escape(stem), quote_norm):
                return True
        return False

    if query_negated and not quote_negated:
        # 问句含否定（如“不制冷/不给保价”），若引文已含明确负面事实（如“保价被拒绝/制冷不行”），不误判冲突。
        if quote_has_negative_fact:
            return False
        positive_stems = [
            token for token in extract_lexical_terms(_NEGATION_PREFIX_RE.sub("", q_norm), max_terms=6)
            if len(token) >= 2
        ]
        if positive_stems and any(stem in quote_norm for stem in positive_stems):
            return True
    return False


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

    def search(
        self,
        query: str,
        *,
        query_variants: Sequence[str] = (),
        top_k: int = 20,
    ) -> tuple[LexicalMatch, ...]:
        if not self.documents or top_k <= 0:
            return ()
        primary_terms = tokenize_for_bm25(strip_question_shell(query))
        if not primary_terms:
            primary_terms = tokenize_for_bm25(query)
        query_weights: dict[str, float] = {
            term: float(min(freq, 3))
            for term, freq in Counter(primary_terms).items()
        }
        for variant in query_variants:
            variant_tokens = tokenize_for_bm25(strip_question_shell(variant))
            for term, freq in Counter(variant_tokens).items():
                variant_weight = 0.6 * float(min(freq, 2))
                if variant_weight > query_weights.get(term, 0.0):
                    query_weights[term] = variant_weight
        if not query_weights:
            return ()

        scored: list[tuple[RagOpinionDocument, float]] = []
        avgdl = self._avgdl or 1.0
        for doc, tf, dl in zip(self.documents, self._doc_tfs, self._doc_lens, strict=True):
            if dl == 0:
                continue
            score = 0.0
            denom_norm = self.k1 * (1.0 - self.b + self.b * (dl / avgdl))
            for term, q_weight in query_weights.items():
                freq = tf.get(term, 0)
                if freq <= 0:
                    continue
                idf = self._idf.get(term, 0.0)
                if idf <= 0.0:
                    continue
                # 2-gram 词元比单字拥有更高精确匹配权重，抑制高频单字噪声。
                gram_boost = 1.35 if len(term) >= 2 else 0.65
                score += idf * gram_boost * q_weight * (freq * (self.k1 + 1.0)) / (freq + denom_norm)
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


def fuse_vector_channels(
    channels: Sequence[Sequence[OpinionMatch]],
    *,
    k: int = 60,
    top_k: int = 20,
) -> tuple[OpinionMatch, ...]:
    """使用 RRF 融合多路同义 Query 向量召回，每条观点保留最小余弦距离。"""
    if top_k <= 0:
        return ()
    if len(channels) == 1:
        return tuple(channels[0][:top_k])
    docs: dict[str, RagOpinionDocument] = {}
    best_dist: dict[str, float] = {}
    scores: dict[str, float] = {}
    for idx, channel in enumerate(channels):
        weight = 1.0 if idx == 0 else 0.85
        seen_in_channel: set[str] = set()
        for rank, match in enumerate(channel, 1):
            key = match.document.stable_versioned_opinion_key
            if key in seen_in_channel:
                continue
            seen_in_channel.add(key)
            docs.setdefault(key, match.document)
            dist = float(match.cosine_distance)
            if key not in best_dist or dist < best_dist[key]:
                best_dist[key] = dist
            scores[key] = scores.get(key, 0.0) + weight / (k + rank)
    ordered_keys = sorted(
        docs,
        key=lambda key: (-round(scores[key], 8), best_dist[key], key),
    )[:top_k]
    return tuple(OpinionMatch(docs[key], best_dist[key]) for key in ordered_keys)


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
    """零依赖确定性融合重排器：结合向量余弦相似度、BM25 词法强度、RRF 共识、短语精确命中与极性冲突惩罚。"""

    policy = LEXICAL_SEMANTIC_RERANK_POLICY

    def score(self, question: str, items: Sequence[RerankItem]) -> dict[str, float]:
        if not items:
            return {}
        core_query = strip_question_shell(question)
        core_norm = unicodedata.normalize("NFKC", core_query).lower()
        query_terms = _content_terms(core_query)
        max_rrf = max((item.rrf_score for item in items), default=0.0) or 1.0
        scores: dict[str, float] = {}
        for item in items:
            has_dense = item.cosine_distance is not None and math.isfinite(item.cosine_distance)
            dense_sim = max(0.0, min(1.0, 1.0 - item.cosine_distance)) if has_dense else 0.0
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
                phrase_cov = min(1.0, 0.82 * overlap + 0.18 * context_overlap)
            else:
                phrase_cov = 0.0

            if has_dense:
                channel_bonus = 0.06 if item.channel == "hybrid" else 0.0
                combined = (
                    0.34 * dense_sim
                    + 0.24 * bm25_norm
                    + 0.22 * phrase_cov
                    + 0.14 * rrf_norm
                    + channel_bonus
                )
            else:
                # 纯 BM25 救回候选缺少向量距离时，按词法、短语覆盖与 RRF 重新归一，避免系统性压低。
                combined = 0.42 * bm25_norm + 0.36 * phrase_cov + 0.22 * rrf_norm

            polarity_conflict = _has_polarity_conflict(core_query, item.quote)
            quote_norm = unicodedata.normalize("NFKC", item.quote).lower()
            if not polarity_conflict and 2 <= len(core_norm) <= 16 and core_norm in quote_norm:
                combined += 0.08
            if polarity_conflict:
                combined -= 0.28

            scores[item.key] = round(max(0.0, min(1.0, combined)), 6)
        return scores


def clear_reranker_cache() -> None:
    """清理进程级本地 Cross-Encoder 模型缓存（供测试或显式释放内存使用）。"""
    with _CROSS_ENCODER_LOCK:
        _CROSS_ENCODER_CACHE.clear()


def reranker_cache_stats() -> dict[str, int]:
    """返回进程级 Cross-Encoder 重排模型缓存数量（不暴露本地缓存路径）。"""
    with _CROSS_ENCODER_LOCK:
        return {"cached_models": len(_CROSS_ENCODER_CACHE)}


class CrossEncoderReranker:
    """本地离线 Cross-Encoder（如 BGE-Reranker）适配器；按缓存目录复用进程内模型，支持测试打分注入。"""

    policy = CROSS_ENCODER_RERANK_POLICY

    def __init__(
        self,
        *,
        cache_dir: str | Path | None = None,
        score_fn: Callable[[list[tuple[str, str]]], Sequence[float]] | None = None,
        normalize_logits: bool = True,
        batch_size: int = 32,
    ):
        if cache_dir is None and score_fn is None:
            raise ValueError("CrossEncoderReranker requires cache_dir or score_fn")
        self.cache_dir = Path(cache_dir).resolve() if cache_dir is not None else None
        self._score_fn = score_fn
        self.normalize_logits = normalize_logits
        self.batch_size = max(1, int(batch_size))
        self._model = None

    def _load_local_model(self):
        if self._model is not None:
            return self._model
        if self.cache_dir is None or not self.cache_dir.is_dir():
            raise ValueError("local_reranker_cache_missing")
        with _CROSS_ENCODER_LOCK:
            cached = _CROSS_ENCODER_CACHE.get(self.cache_dir)
            if cached is not None:
                self._model = cached
                return cached
            from sentence_transformers import CrossEncoder

            model = CrossEncoder(str(self.cache_dir), local_files_only=True)
            _CROSS_ENCODER_CACHE[self.cache_dir] = model
            self._model = model
            return model

    def score(self, question: str, items: Sequence[RerankItem]) -> dict[str, float]:
        if not items:
            return {}
        pairs = [(question, item.quote) for item in items]
        if self._score_fn is not None:
            raw_scores = list(self._score_fn(pairs))
        else:
            model = self._load_local_model()
            try:
                raw_scores = [float(v) for v in model.predict(pairs, batch_size=self.batch_size)]
            except TypeError:
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
