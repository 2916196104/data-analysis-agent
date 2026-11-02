-- Transactional rollback of derived data only; vector extension and source data survive.
DROP TABLE review_opinion_embeddings;
DROP TABLE rag_embedding_sync_runs;
