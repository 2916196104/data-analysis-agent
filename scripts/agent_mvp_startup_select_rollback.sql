-- Roll back only the newly approved direct SELECT, using the same grantor.
-- Before the change, rag_audit_ro had no direct or effective SELECT on this table.
-- Stop if another administrator changed this ACL after the saved baseline.
BEGIN;
REVOKE SELECT ON TABLE public.review_monitor_schema FROM rag_audit_ro RESTRICT;
COMMIT;
