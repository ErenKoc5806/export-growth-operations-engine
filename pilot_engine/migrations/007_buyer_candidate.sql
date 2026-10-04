BEGIN IMMEDIATE;

CREATE TABLE buyer_candidate (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    country_code TEXT NOT NULL,
    website TEXT,
    domain TEXT,
    role_hypothesis TEXT NOT NULL,
    qualification_status TEXT NOT NULL DEFAULT 'UNQUALIFIED'
        CHECK(qualification_status = 'UNQUALIFIED'),
    possible_duplicate_of TEXT REFERENCES buyer_candidate(id),
    created_at_utc TEXT NOT NULL
);
CREATE UNIQUE INDEX buyer_candidate_domain ON buyer_candidate(country_code, domain)
WHERE domain IS NOT NULL;
CREATE TABLE buyer_candidate_evidence (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES buyer_candidate(id),
    source_system TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    source_url TEXT NOT NULL,
    observed_name TEXT NOT NULL,
    observed_website TEXT,
    role_hint TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    query_text TEXT NOT NULL,
    summary TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    UNIQUE(source_system, source_ref, source_url)
);
CREATE TABLE buyer_candidate_alias (
    candidate_id TEXT NOT NULL REFERENCES buyer_candidate(id),
    alias TEXT NOT NULL,
    PRIMARY KEY(candidate_id, alias)
);
CREATE TRIGGER candidate_evidence_no_update BEFORE UPDATE ON buyer_candidate_evidence
BEGIN SELECT RAISE(ABORT, 'candidate evidence is append-only'); END;
CREATE TRIGGER candidate_evidence_no_delete BEFORE DELETE ON buyer_candidate_evidence
BEGIN SELECT RAISE(ABORT, 'candidate evidence is append-only'); END;
CREATE TRIGGER candidate_no_delete BEFORE DELETE ON buyer_candidate
BEGIN SELECT RAISE(ABORT, 'candidate history cannot be deleted'); END;

PRAGMA user_version = 7;
COMMIT;
