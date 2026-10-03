BEGIN IMMEDIATE;
CREATE TRIGGER approval_no_update BEFORE UPDATE ON approval
BEGIN SELECT RAISE(ABORT, 'approvals are append-only'); END;
CREATE TRIGGER approval_no_delete BEFORE DELETE ON approval
BEGIN SELECT RAISE(ABORT, 'approvals are append-only'); END;
PRAGMA user_version = 4;
COMMIT;
