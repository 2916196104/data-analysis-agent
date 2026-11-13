-- PENDING APPROVAL. Manual administrator execution in database reviews only.
-- PostgreSQL permits COUNT(*) with SELECT on any one column.
-- This also permits listing id values; it does NOT grant raw_line/reason access.
-- No table-wide SELECT, schema grant, write permission or grant option.
BEGIN;
GRANT SELECT (id) ON TABLE public.import_errors TO rag_audit_ro;
COMMIT;
