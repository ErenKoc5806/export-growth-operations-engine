BEGIN IMMEDIATE;

CREATE TABLE manufacturer (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at_utc TEXT NOT NULL
);
CREATE TABLE product (
    id TEXT PRIMARY KEY, manufacturer_id TEXT NOT NULL REFERENCES manufacturer(id),
    sku TEXT, name TEXT NOT NULL, unit TEXT NOT NULL, hs6 TEXT NOT NULL,
    specification_ref TEXT, classification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
    created_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL,
    UNIQUE(manufacturer_id, sku)
);
CREATE TABLE market_target (
    id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES product(id),
    country_code TEXT NOT NULL, search_terms_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);
CREATE TABLE buyer_company (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, country_code TEXT NOT NULL,
    role TEXT NOT NULL, created_at_utc TEXT NOT NULL
);
-- Contact routes live here, never in audit_event or generic application logs.
CREATE TABLE contact_evidence (
    id TEXT PRIMARY KEY, buyer_id TEXT NOT NULL REFERENCES buyer_company(id),
    business_email TEXT, contact_route TEXT, source_system TEXT NOT NULL,
    source_ref TEXT NOT NULL, source_url TEXT NOT NULL, checked_at_utc TEXT NOT NULL,
    verified INTEGER NOT NULL CHECK(verified IN (0, 1)),
    created_at_utc TEXT NOT NULL
);
CREATE TABLE opportunity (
    id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES product(id),
    target_id TEXT NOT NULL REFERENCES market_target(id),
    buyer_id TEXT NOT NULL REFERENCES buyer_company(id),
    owner_id TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
    source_system TEXT, source_ref TEXT, created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
CREATE TABLE approval (
    id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL REFERENCES opportunity(id),
    action TEXT NOT NULL, target_id TEXT NOT NULL, target_revision INTEGER,
    content_hash TEXT, actor_id TEXT NOT NULL, decision TEXT NOT NULL,
    decided_at_utc TEXT NOT NULL
);
CREATE TABLE outreach (
    id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL REFERENCES opportunity(id),
    contact_evidence_id TEXT NOT NULL REFERENCES contact_evidence(id),
    content_hash TEXT NOT NULL, approval_id TEXT REFERENCES approval(id),
    status TEXT NOT NULL, created_at_utc TEXT NOT NULL
);
CREATE TABLE action_attempt (
    id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL REFERENCES opportunity(id),
    action TEXT NOT NULL, target_id TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
    provider_status TEXT NOT NULL, provider_ref TEXT, created_at_utc TEXT NOT NULL
);
CREATE TABLE rfq (
    id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL REFERENCES opportunity(id),
    source_ref TEXT NOT NULL, received_at_utc TEXT NOT NULL,
    requested_lines_json TEXT NOT NULL
);
CREATE TABLE quotation_revision (
    quotation_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
    rfq_id TEXT NOT NULL REFERENCES rfq(id), sku TEXT NOT NULL,
    quantity_text TEXT NOT NULL, unit TEXT NOT NULL, currency TEXT NOT NULL,
    unit_price_text TEXT NOT NULL, terms_json TEXT NOT NULL,
    status TEXT NOT NULL, approved_by TEXT, created_at_utc TEXT NOT NULL,
    PRIMARY KEY(quotation_id, revision)
);
CREATE TABLE customer_po (
    id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL REFERENCES opportunity(id),
    quotation_id TEXT NOT NULL, quotation_revision INTEGER NOT NULL,
    source_document_hash TEXT, sku TEXT NOT NULL, quantity_text TEXT NOT NULL,
    unit TEXT NOT NULL, currency TEXT NOT NULL, unit_price_text TEXT NOT NULL,
    total_text TEXT NOT NULL, accepted_by TEXT NOT NULL,
    reviewed_at_utc TEXT NOT NULL,
    FOREIGN KEY(quotation_id, quotation_revision)
        REFERENCES quotation_revision(quotation_id, revision)
);
CREATE TABLE sales_order (
    id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL REFERENCES opportunity(id),
    po_id TEXT NOT NULL UNIQUE REFERENCES customer_po(id),
    idempotency_key TEXT NOT NULL UNIQUE, quantity_text TEXT NOT NULL,
    unit TEXT NOT NULL, currency TEXT NOT NULL, unit_price_text TEXT NOT NULL,
    total_text TEXT NOT NULL, status TEXT NOT NULL, erp_ref TEXT,
    created_at_utc TEXT NOT NULL
);
CREATE TABLE shipment (
    id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES sales_order(id),
    destination TEXT NOT NULL, boxes INTEGER NOT NULL,
    net_weight_kg_text TEXT NOT NULL, gross_weight_kg_text TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);
CREATE TABLE document_draft (
    id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES sales_order(id),
    type TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
    payload_json TEXT NOT NULL, status TEXT NOT NULL, created_at_utc TEXT NOT NULL,
    UNIQUE(order_id, type, revision)
);
CREATE TABLE case_ingest (
    opportunity_id TEXT PRIMARY KEY REFERENCES opportunity(id),
    payload_sha256 TEXT NOT NULL, order_id TEXT NOT NULL REFERENCES sales_order(id),
    created_at_utc TEXT NOT NULL
);
CREATE TABLE audit_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id TEXT NOT NULL REFERENCES opportunity(id),
    action TEXT NOT NULL, actor_id TEXT NOT NULL, target_id TEXT NOT NULL,
    before_state TEXT, after_state TEXT, source_ref TEXT,
    occurred_at_utc TEXT NOT NULL
);
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_event
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_event
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE TRIGGER quotation_no_update BEFORE UPDATE ON quotation_revision
BEGIN SELECT RAISE(ABORT, 'quotation revisions are immutable'); END;
CREATE TRIGGER quotation_no_delete BEFORE DELETE ON quotation_revision
BEGIN SELECT RAISE(ABORT, 'quotation revisions are immutable'); END;

PRAGMA user_version = 1;
COMMIT;
