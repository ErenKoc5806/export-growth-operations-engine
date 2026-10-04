BEGIN IMMEDIATE;

-- Operator attestations are recorded, not inferred from a public page.
CREATE TABLE contact_collection_policy (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    source_system TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    source_url TEXT NOT NULL,
    data_class TEXT NOT NULL CHECK(data_class IN ('BUSINESS_ROUTE', 'PERSONAL_ROUTE')),
    decision TEXT NOT NULL CHECK(decision IN ('ALLOW', 'REVOKE')),
    processing_basis TEXT NOT NULL CHECK(processing_basis IN
        ('CONSENT', 'LEGITIMATE_INTEREST', 'OTHER_REVIEWED')),
    lawful_basis_ref TEXT NOT NULL,
    source_terms_ref TEXT NOT NULL,
    retention_until_utc TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    decided_at_utc TEXT NOT NULL
);
CREATE INDEX contact_policy_latest ON contact_collection_policy
    (source_system, source_ref, data_class, sequence DESC);
CREATE TABLE discovered_contact_route (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES product(id),
    candidate_id TEXT NOT NULL REFERENCES buyer_candidate(id),
    kind TEXT NOT NULL CHECK(kind IN ('GENERIC_EMAIL', 'SWITCHBOARD', 'CONTACT_FORM',
                                     'NAMED_EMAIL', 'NAMED_PHONE')),
    route_value TEXT,
    value_key TEXT NOT NULL,
    person_name TEXT,
    person_role TEXT,
    source_system TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    source_url TEXT,
    observed_at_utc TEXT NOT NULL,
    policy_sequence INTEGER NOT NULL REFERENCES contact_collection_policy(sequence),
    actor_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    suppressed INTEGER NOT NULL DEFAULT 0 CHECK(suppressed IN (0, 1)),
    UNIQUE(candidate_id, kind, value_key)
);
CREATE TABLE contact_route_observation (
    id TEXT PRIMARY KEY,
    route_id TEXT NOT NULL REFERENCES discovered_contact_route(id),
    source_system TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    source_url TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    policy_sequence INTEGER NOT NULL REFERENCES contact_collection_policy(sequence),
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(route_id, source_system, source_ref)
);
CREATE TABLE contact_route_correction (
    id TEXT PRIMARY KEY,
    route_id TEXT NOT NULL REFERENCES discovered_contact_route(id),
    reason TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    corrected_at_utc TEXT NOT NULL
);
CREATE TABLE contact_route_absence (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES product(id),
    candidate_id TEXT NOT NULL REFERENCES buyer_candidate(id),
    source_url TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    explanation TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(product_id, candidate_id, source_url, observed_at_utc)
);
-- Suppression stores a digest, never the address/number. The route table can
-- redact personal fields on request; historical backups need separate purging.
CREATE TABLE contact_suppression (
    value_key TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);
CREATE TRIGGER contact_policy_no_update BEFORE UPDATE ON contact_collection_policy
BEGIN SELECT RAISE(ABORT, 'contact policy is append-only'); END;
CREATE TRIGGER contact_policy_no_delete BEFORE DELETE ON contact_collection_policy
BEGIN SELECT RAISE(ABORT, 'contact policy is append-only'); END;
CREATE TRIGGER contact_observation_no_update BEFORE UPDATE ON contact_route_observation
BEGIN SELECT RAISE(ABORT, 'contact observations are append-only'); END;
CREATE TRIGGER contact_observation_no_delete BEFORE DELETE ON contact_route_observation
BEGIN SELECT RAISE(ABORT, 'contact observations are append-only'); END;
CREATE TRIGGER contact_correction_no_update BEFORE UPDATE ON contact_route_correction
BEGIN SELECT RAISE(ABORT, 'contact corrections are append-only'); END;
CREATE TRIGGER contact_correction_no_delete BEFORE DELETE ON contact_route_correction
BEGIN SELECT RAISE(ABORT, 'contact corrections are append-only'); END;
CREATE TRIGGER contact_suppression_no_update BEFORE UPDATE ON contact_suppression
BEGIN SELECT RAISE(ABORT, 'contact suppression is append-only'); END;
CREATE TRIGGER contact_suppression_no_delete BEFORE DELETE ON contact_suppression
BEGIN SELECT RAISE(ABORT, 'contact suppression is append-only'); END;

PRAGMA user_version = 10;
COMMIT;
