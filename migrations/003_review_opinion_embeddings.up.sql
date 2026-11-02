-- Explicit migration: pgvector must already be installed. No source tables are changed.
CREATE TABLE rag_embedding_sync_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    product_id TEXT NOT NULL,
    label_profile_key TEXT NOT NULL,
    embedding_space_id TEXT NOT NULL,
    scope_json JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running','succeeded','partial','retry_required','failed')),
    metrics JSONB NOT NULL DEFAULT '{}',
    error_code TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    finished_at TIMESTAMPTZ
);

CREATE TABLE review_opinion_embeddings (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    product_id TEXT NOT NULL,
    review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    annotation_id INTEGER NOT NULL REFERENCES review_annotations(id) ON DELETE CASCADE,
    label_profile_key TEXT NOT NULL,
    opinion_source TEXT NOT NULL CHECK (opinion_source IN ('model','human')),
    model_opinion_id INTEGER REFERENCES review_opinions(id) ON DELETE CASCADE,
    human_opinion_id INTEGER REFERENCES review_human_opinions(id) ON DELETE CASCADE,
    human_revision_id INTEGER REFERENCES review_human_reviews(id) ON DELETE CASCADE,
    stable_versioned_opinion_key TEXT NOT NULL,
    aspect TEXT NOT NULL CHECK (aspect IN ('service','logistics','effectiveness','quality','price','packaging')),
    sentiment TEXT NOT NULL CHECK (sentiment IN ('positive','negative','neutral','unknown')),
    quote_start INTEGER NOT NULL CHECK (quote_start >= 0),
    quote_end INTEGER NOT NULL CHECK (quote_end > quote_start),
    review_content_sha256 TEXT NOT NULL,
    embedding_input_sha256 TEXT NOT NULL,
    effective_snapshot_sha256 TEXT NOT NULL,
    published_at TEXT NOT NULL,
    text_builder_version TEXT NOT NULL CHECK (text_builder_version = 'quote-utf8-v1'),
    embedding_space_id TEXT NOT NULL CHECK (embedding_space_id =
        'embedding-space-v1:11e6754662dbd13fc56c6e624bc9416c1c2766f962787267fd4a57b1fa8d622e'),
    embedding vector(1024) NOT NULL CHECK (abs(vector_norm(embedding) - 1) < 0.0001),
    sync_run_id BIGINT REFERENCES rag_embedding_sync_runs(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    synced_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    invalidated_at TIMESTAMPTZ,
    FOREIGN KEY (source,dataset_version,product_id) REFERENCES products(source,dataset_version,product_id) ON DELETE CASCADE,
    CHECK ((opinion_source='model' AND model_opinion_id IS NOT NULL AND human_opinion_id IS NULL AND human_revision_id IS NULL)
        OR (opinion_source='human' AND model_opinion_id IS NULL AND human_opinion_id IS NOT NULL AND human_revision_id IS NOT NULL)),
    UNIQUE (stable_versioned_opinion_key,embedding_space_id,text_builder_version)
);
CREATE INDEX review_embeddings_scope ON review_opinion_embeddings
    (source,dataset_version,product_id,label_profile_key,embedding_space_id,published_at);
CREATE INDEX review_embeddings_review ON review_opinion_embeddings(review_id);
CREATE INDEX review_embeddings_annotation ON review_opinion_embeddings(annotation_id);
CREATE INDEX review_embeddings_model_opinion ON review_opinion_embeddings(model_opinion_id) WHERE model_opinion_id IS NOT NULL;
CREATE INDEX review_embeddings_human_opinion ON review_opinion_embeddings(human_opinion_id) WHERE human_opinion_id IS NOT NULL;
CREATE INDEX review_embeddings_revision ON review_opinion_embeddings(human_revision_id) WHERE human_revision_id IS NOT NULL;
CREATE INDEX review_embeddings_run ON review_opinion_embeddings(sync_run_id);
