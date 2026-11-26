-- Transactional rollback of additive ANN and active-scope indexes only.
DROP INDEX IF EXISTS review_embeddings_hnsw_cosine;
DROP INDEX IF EXISTS review_embeddings_active_scope;
