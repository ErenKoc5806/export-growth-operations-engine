BEGIN IMMEDIATE;

CREATE TABLE contact_suppression_rule (
    scope TEXT NOT NULL CHECK(scope IN ('MAILBOX_BASE', 'COMPANY_DOMAIN')),
    value_key TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    PRIMARY KEY(scope, value_key)
);
CREATE TRIGGER contact_suppression_rule_no_update BEFORE UPDATE ON contact_suppression_rule
BEGIN SELECT RAISE(ABORT, 'contact suppression rules are append-only'); END;
CREATE TRIGGER contact_suppression_rule_no_delete BEFORE DELETE ON contact_suppression_rule
BEGIN SELECT RAISE(ABORT, 'contact suppression rules are append-only'); END;

ALTER TABLE discovered_contact_route ADD COLUMN last_correction_id TEXT
    REFERENCES contact_route_correction(id);
CREATE TRIGGER contact_route_guard_update BEFORE UPDATE ON discovered_contact_route
WHEN NOT (
    NEW.id IS OLD.id AND NEW.product_id IS OLD.product_id
    AND NEW.candidate_id IS OLD.candidate_id AND NEW.kind IS OLD.kind
    AND NEW.value_key IS OLD.value_key AND NEW.source_system IS OLD.source_system
    AND NEW.source_ref IS OLD.source_ref AND NEW.observed_at_utc IS OLD.observed_at_utc
    AND NEW.policy_sequence IS OLD.policy_sequence AND NEW.actor_id IS OLD.actor_id
    AND NEW.created_at_utc IS OLD.created_at_utc
    AND (
        (OLD.suppressed = 0 AND NEW.suppressed = 1 AND NEW.route_value IS NULL
         AND NEW.person_name IS NULL AND NEW.person_role IS NULL AND NEW.source_url IS NULL
         AND NEW.last_correction_id IS OLD.last_correction_id)
        OR
        (OLD.suppressed = 0 AND NEW.suppressed = 0
         AND NEW.route_value IS OLD.route_value AND NEW.source_url IS OLD.source_url
         AND NEW.last_correction_id IS NOT OLD.last_correction_id
         AND EXISTS (SELECT 1 FROM contact_route_correction c
                     WHERE c.id = NEW.last_correction_id AND c.route_id = OLD.id))
    )
)
BEGIN SELECT RAISE(ABORT, 'contact route mutation needs correction or redaction'); END;

PRAGMA user_version = 13;
COMMIT;
