BEGIN IMMEDIATE;

CREATE TABLE sell_draft (
    id TEXT PRIMARY KEY,
    handoff_id TEXT NOT NULL REFERENCES find_handoff(id),
    created_at_utc TEXT NOT NULL
);
CREATE TABLE sell_draft_revision (
    draft_id TEXT NOT NULL REFERENCES sell_draft(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    handoff_id TEXT NOT NULL REFERENCES find_handoff(id),
    handoff_revision INTEGER NOT NULL,
    fit_sequence INTEGER NOT NULL REFERENCES buyer_fit_decision(sequence),
    profile_sha256 TEXT NOT NULL,
    recipient_route_id TEXT NOT NULL REFERENCES discovered_contact_route(id),
    recipient_value TEXT NOT NULL,
    channel TEXT NOT NULL CHECK(channel IN ('EMAIL', 'CONTACT_FORM', 'PHONE_SCRIPT')),
    sender_name TEXT NOT NULL,
    sender_address TEXT NOT NULL,
    language TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    claim_refs_json TEXT NOT NULL CHECK(json_valid(claim_refs_json)),
    warnings_json TEXT NOT NULL CHECK(json_valid(warnings_json)),
    source TEXT NOT NULL CHECK(source IN ('TEMPLATE', 'OPERATOR_EDIT')),
    actor_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY(draft_id, revision),
    FOREIGN KEY(handoff_id, handoff_revision) REFERENCES find_handoff_revision(handoff_id, revision)
);
CREATE TABLE sell_draft_rejection (
    draft_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    reason TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    rejected_at_utc TEXT NOT NULL,
    PRIMARY KEY(draft_id, revision),
    FOREIGN KEY(draft_id, revision) REFERENCES sell_draft_revision(draft_id, revision)
);
CREATE TRIGGER sell_draft_no_update BEFORE UPDATE ON sell_draft
BEGIN SELECT RAISE(ABORT, 'sell draft identity is immutable'); END;
CREATE TRIGGER sell_draft_no_delete BEFORE DELETE ON sell_draft
BEGIN SELECT RAISE(ABORT, 'sell draft identity is immutable'); END;
CREATE TRIGGER sell_draft_revision_no_update BEFORE UPDATE ON sell_draft_revision
BEGIN SELECT RAISE(ABORT, 'sell draft revisions are append-only'); END;
CREATE TRIGGER sell_draft_revision_no_delete BEFORE DELETE ON sell_draft_revision
BEGIN SELECT RAISE(ABORT, 'sell draft revisions are append-only'); END;
CREATE TRIGGER sell_draft_rejection_no_update BEFORE UPDATE ON sell_draft_rejection
BEGIN SELECT RAISE(ABORT, 'sell draft rejections are append-only'); END;
CREATE TRIGGER sell_draft_rejection_no_delete BEFORE DELETE ON sell_draft_rejection
BEGIN SELECT RAISE(ABORT, 'sell draft rejections are append-only'); END;

PRAGMA user_version = 14;
COMMIT;
