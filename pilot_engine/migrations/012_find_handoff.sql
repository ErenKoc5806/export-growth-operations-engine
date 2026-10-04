BEGIN IMMEDIATE;

CREATE TABLE find_handoff (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES product(id),
    candidate_id TEXT NOT NULL REFERENCES buyer_candidate(id),
    route_id TEXT NOT NULL REFERENCES discovered_contact_route(id),
    created_at_utc TEXT NOT NULL,
    UNIQUE(product_id, candidate_id, route_id)
);
CREATE TABLE find_handoff_revision (
    handoff_id TEXT NOT NULL REFERENCES find_handoff(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    profile_revision INTEGER NOT NULL,
    profile_sha256 TEXT NOT NULL,
    fit_sequence INTEGER NOT NULL REFERENCES buyer_fit_decision(sequence),
    check_sequence INTEGER NOT NULL REFERENCES contact_route_check(sequence),
    data_origin TEXT NOT NULL CHECK(data_origin = 'SYNTHETIC'),
    observation_sha256 TEXT NOT NULL,
    candidate_evidence_sha256 TEXT NOT NULL,
    source_policy_sequence INTEGER NOT NULL REFERENCES contact_collection_policy(sequence),
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    PRIMARY KEY(handoff_id, revision)
);
CREATE TRIGGER find_handoff_revision_no_update BEFORE UPDATE ON find_handoff_revision
BEGIN SELECT RAISE(ABORT, 'find handoffs are append-only'); END;
CREATE TRIGGER find_handoff_revision_no_delete BEFORE DELETE ON find_handoff_revision
BEGIN SELECT RAISE(ABORT, 'find handoffs are append-only'); END;
CREATE TRIGGER find_handoff_no_update BEFORE UPDATE ON find_handoff
BEGIN SELECT RAISE(ABORT, 'find handoff identity is immutable'); END;
CREATE TRIGGER find_handoff_no_delete BEFORE DELETE ON find_handoff
BEGIN SELECT RAISE(ABORT, 'find handoff identity is immutable'); END;

PRAGMA user_version = 12;
COMMIT;
