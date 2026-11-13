-- Manual rollback by the same grantor after the approved column grant.
-- Baseline: id SELECT=false and no direct column ACL for rag_audit_ro.
-- If another administrator changed this ACL meanwhile, reconcile first.
BEGIN;
REVOKE SELECT (id) ON TABLE public.import_errors FROM rag_audit_ro RESTRICT;
COMMIT;
