"""当前有效观点的内部只读语料契约，不生成向量或调用模型。"""

from dataclasses import dataclass, field
from hashlib import sha256
from itertools import chain, groupby
from typing import Literal

from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.analytics import _snapshot
from data_analysis_agent.reviews.opinions import ASPECTS, SENTIMENTS, Aspect, Sentiment
from data_analysis_agent.reviews.scope import analysis_scope, current_reviews_sql

CORPUS_CONTRACT_VERSION = "rag-effective-opinions-v1"
TEXT_BUILDER_VERSION = "quote-utf8-v1"
OpinionSource = Literal["model", "human"]


class RagCorpusIntegrityError(ReviewDataError):
    """来源或原文不一致时整次读取失败，避免返回看似完整的部分语料。"""


@dataclass(frozen=True)
class RagOpinionDocument:
    source: str
    dataset_version: str
    product_id: str
    review_id: int
    annotation_id: int
    requested_model: str
    base_url: str = field(repr=False)
    prompt_version: str
    label_profile_key: str
    opinion_source: OpinionSource
    model_opinion_id: int | None
    human_opinion_id: int | None
    human_revision_id: int | None
    aspect: Aspect
    sentiment: Sentiment
    quote: str
    quote_start: int
    quote_end: int
    review_content_sha256: str
    opinion_text_sha256: str
    embedding_input: str
    embedding_input_sha256: str
    stable_versioned_opinion_key: str
    published_at: str
    import_run_id: int
    line_number: int
    file_sha256: str
    evidence_ref: str
    text_builder_version: str = TEXT_BUILDER_VERSION


@dataclass(frozen=True)
class RagReviewState:
    source: str
    dataset_version: str
    product_id: str
    review_id: int
    review_content_sha256: str
    published_at: str
    annotation_id: int | None
    human_revision_id: int | None
    processing_status: Literal["success", "failed", "pending", "empty"]
    human_status: Literal["unreviewed", "confirmed", "corrected"]
    opinion_source: OpinionSource | None
    error_code: str | None
    opinion_count: int
    effective_snapshot_sha256: str


@dataclass(frozen=True)
class RagCorpus:
    scope: dict = field(repr=False)
    label_profile_key: str
    documents: tuple[RagOpinionDocument, ...]
    reviews: tuple[RagReviewState, ...]
    contract_version: str = CORPUS_CONTRACT_VERSION


def _document(row, identity, profile_key):
    source = row["opinion_source"]
    model_id, human_id = row["model_opinion_id"], row["human_opinion_id"]
    revision = row["human_revision_id"]
    valid_source = (
        source == "model" and model_id is not None and human_id is None and revision is None
        or source == "human" and human_id is not None and model_id is None and revision is not None
    )
    quote, start, end, text = row["quote"], row["quote_start"], row["quote_end"], row["review_text"]
    if not (valid_source and row["processing_status"] == "success" and row["annotation_id"] is not None
            and row["aspect"] in ASPECTS and row["sentiment"] in SENTIMENTS
            and isinstance(quote, str) and quote.strip()
            and type(start) is int and type(end) is int and 0 <= start < end <= len(text)
            and text[start:end] == quote):
        raise RagCorpusIntegrityError(f"评论 {row['review_id']} 的观点来源或原文区间不一致，语料未导出。")
    try:
        # 不 trim、不合并空白、不做 Unicode 规范化；哈希对应实际输入字节。
        text_hash = sha256(quote.encode("utf-8")).hexdigest()
    except UnicodeError:
        raise RagCorpusIntegrityError(f"评论 {row['review_id']} 的观点不是有效 UTF-8 文本，语料未导出。") from None
    version_key = "rag-opinion-v1:" + content_sha256([
        row["source"], row["dataset_version"], row["product_id"], row["review_id"],
        row["review_content_sha256"], profile_key, row["annotation_id"], revision,
        source, model_id, human_id, row["aspect"], row["sentiment"], start, end, text_hash,
    ])
    return RagOpinionDocument(
        source=row["source"], dataset_version=row["dataset_version"], product_id=row["product_id"],
        review_id=row["review_id"], annotation_id=row["annotation_id"], requested_model=identity[0],
        base_url=identity[1], prompt_version=identity[2], label_profile_key=profile_key,
        opinion_source=source, model_opinion_id=model_id, human_opinion_id=human_id,
        human_revision_id=revision, aspect=row["aspect"], sentiment=row["sentiment"],
        quote=quote, quote_start=start, quote_end=end, review_content_sha256=row["review_content_sha256"],
        opinion_text_sha256=text_hash, embedding_input=quote, embedding_input_sha256=text_hash,
        stable_versioned_opinion_key=version_key, published_at=row["published_at"],
        import_run_id=row["import_run_id"], line_number=row["line_number"], file_sha256=row["file_sha256"],
        evidence_ref=f"sha256:{row['file_sha256']}#line={row['line_number']}",
    )


def _review_state(row, documents, profile_key):
    source = ("human" if row["human_revision_id"] is not None
              else "model" if row["processing_status"] == "success" else None)
    state = dict(
        source=row["source"], dataset_version=row["dataset_version"], product_id=row["product_id"],
        review_id=row["review_id"], review_content_sha256=row["review_content_sha256"],
        published_at=row["published_at"], annotation_id=row["annotation_id"],
        human_revision_id=row["human_revision_id"], processing_status=row["processing_status"],
        human_status=row["human_status"], opinion_source=source, error_code=row["error_code"],
        opinion_count=len(documents),
    )
    # 空快照也有版本摘要，不能靠“本次返回的观点行”发现删除。
    snapshot_hash = content_sha256([
        CORPUS_CONTRACT_VERSION, profile_key, state,
        sorted(document.stable_versioned_opinion_key for document in documents),
    ])
    return RagReviewState(**state, effective_snapshot_sha256=snapshot_hash)


def read_effective_corpus(
    db_path, *, model: str, base_url: str, prompt_version: str, source: str,
    dataset_version: str, date_from: str, date_to: str,
    product_ids: list[str] | tuple[str, ...] | None = None, as_of: str | None = None,
) -> RagCorpus:
    """读取一个明确范围及标注配置；路径使用只读连接，共享连接必须已有事务。

    任何无效观点使整次读取失败。返回结果仅表示本次快照，不是后续写入授权或同步水位。
    """
    if any(not isinstance(value, str) or not value.strip() for value in (source, dataset_version)):
        raise ReviewDataError("语料读取必须指定非空 source 和 dataset_version。")
    identity, where, filters, scope = analysis_scope(
        model=model, base_url=base_url, prompt_version=prompt_version, source=source,
        dataset_version=dataset_version, date_from=date_from, date_to=date_to,
        product_ids=product_ids, as_of=as_of,
    )
    profile_key = content_sha256(list(identity))
    documents, reviews = [], []
    # 与现有统计共用快照管理，既不提交共享事务，也不触发初始化。
    with _snapshot(db_path) as connection:
        if scope["product_ids"]:
            products = connection.execute(
                "SELECT product_id FROM products WHERE source=? AND dataset_version=? AND product_id IN ("
                + ",".join("?" for _ in scope["product_ids"]) + ")",
                (source, dataset_version, *scope["product_ids"]),
            )
            if set(scope["product_ids"]) - {row[0] for row in products}:
                raise ReviewDataError("部分已选商品不属于当前数据集，请重新确认商品范围。")
        cte, parameters = current_reviews_sql(connection, identity, include_opinion_provenance=True)
        rows = connection.execute(cte + """SELECT c.source, c.dataset_version, c.product_id,
            c.id review_id, c.annotation_id, c.revision_id human_revision_id,
            c.processing_status, c.human_status, c.error_code,
            c.content_sha256 review_content_sha256, c.text review_text, c.published_at,
            c.import_run_id, c.line_number, i.file_sha256,
            o.opinion_source, o.model_opinion_id, o.human_opinion_id,
            o.aspect, o.sentiment, o.quote, o.quote_start, o.quote_end
            FROM current c JOIN import_runs i ON i.id=c.import_run_id
            LEFT JOIN effective_opinions o ON o.review_id=c.id WHERE """ + where + """
            ORDER BY c.id, o.quote_start, o.quote_end, o.aspect, o.sentiment,
                o.model_opinion_id, o.human_opinion_id""", (*parameters, *filters))
        for _, group in groupby(rows, key=lambda row: row["review_id"]):
            first = next(group)
            current_documents = [_document(row, identity, profile_key) for row in chain((first,), group)
                                 if row["opinion_source"] is not None]
            reviews.append(_review_state(first, current_documents, profile_key))
            documents.extend(current_documents)
    return RagCorpus(scope, profile_key, tuple(documents), tuple(reviews))
