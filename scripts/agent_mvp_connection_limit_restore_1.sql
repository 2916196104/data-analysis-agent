-- Manual administrator execution only, after the temporary MVP run has stopped.
-- First verify all sessions for rag_audit_ro and its advisory locks are released.
-- Lowering the cap does not itself terminate existing sessions.
BEGIN;
ALTER ROLE rag_audit_ro CONNECTION LIMIT 1;
COMMIT;
