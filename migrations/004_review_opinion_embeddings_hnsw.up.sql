-- Explicit additive migration: pgvector HNSW cosine index and active-scope B-tree index.
-- Requires 003_review_opinion_embeddings.up.sql. Does not modify source tables or constraints.
CREATE INDEX IF NOT EXISTS review_embeddings_hnsw_cosine
    ON review_opinion_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE invalidated_at IS NULL;

CREATE INDEX IF NOT EXISTS review_embeddings_active_scope
    ON review_opinion_embeddings (
        source,
        dataset_version,
        product_id,
        label_profile_key,
        embedding_space_id,
        text_builder_version,
        published_at
    )
    WHERE invalidated_at IS NULL;
