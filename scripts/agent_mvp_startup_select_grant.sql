-- PENDING APPROVAL. Run only after separate ACL approval, in database reviews,
-- by the table owner or an authorized administrator. Agent login stays rag_audit_ro.
BEGIN;
GRANT SELECT ON TABLE public.review_monitor_schema TO rag_audit_ro;
COMMIT;
