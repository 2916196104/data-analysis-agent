-- Run as rag_audit_ro AFTER the separately approved administrator grant.
-- Read-only verification; no payload values are read.
BEGIN READ ONLY;
SET LOCAL statement_timeout = '15s';
SELECT current_database() AS database_name, current_user AS role_name,
       current_setting('transaction_read_only') AS read_only;

-- Expected: only id=true; all other import_errors columns=false.
SELECT a.attname AS column_name,
       has_column_privilege(current_user, a.attrelid, a.attnum, 'SELECT') AS can_select
FROM pg_catalog.pg_attribute a
WHERE a.attrelid = 'public.import_errors'::regclass
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum;

-- Expected: both false. Full-table SELECT is NOT needed for COUNT(*).
SELECT has_table_privilege(current_user, 'public.import_errors', 'SELECT') AS full_table_select,
       has_table_privilege(current_user, 'public.import_errors', 'INSERT,UPDATE,DELETE,TRUNCATE') AS can_write;

-- The exact operation required by the unchanged production /quality endpoint.
SELECT COUNT(*) AS rejected_record_count FROM public.import_errors;
ROLLBACK;
