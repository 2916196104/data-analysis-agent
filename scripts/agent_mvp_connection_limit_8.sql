-- PENDING SEPARATE APPROVAL. Manual administrator execution only.
-- Changes only this role's connection cap in the local PostgreSQL cluster.
-- Does not grant any schema, table, data-write or delegation privileges.
BEGIN;
ALTER ROLE rag_audit_ro CONNECTION LIMIT 8;
COMMIT;
