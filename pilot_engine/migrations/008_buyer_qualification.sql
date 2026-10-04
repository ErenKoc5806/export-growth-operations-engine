BEGIN IMMEDIATE;

CREATE TABLE buyer_fit_decision (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL REFERENCES product(id),
    candidate_id TEXT NOT NULL REFERENCES buyer_candidate(id),
    profile_revision INTEGER NOT NULL,
    profile_sha256 TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('ACCEPT', 'REJECT', 'DEFER')),
    checks_json TEXT NOT NULL CHECK(json_valid(checks_json)),
    explanation TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    decided_at_utc TEXT NOT NULL,
    FOREIGN KEY(product_id, profile_revision)
        REFERENCES product_profile_revision(product_id, revision)
);
CREATE INDEX buyer_fit_latest ON buyer_fit_decision(product_id, candidate_id, sequence DESC);
CREATE TRIGGER buyer_fit_no_update BEFORE UPDATE ON buyer_fit_decision
BEGIN SELECT RAISE(ABORT, 'buyer fit decisions are append-only'); END;
CREATE TRIGGER buyer_fit_no_delete BEFORE DELETE ON buyer_fit_decision
BEGIN SELECT RAISE(ABORT, 'buyer fit decisions are append-only'); END;

PRAGMA user_version = 8;
COMMIT;
