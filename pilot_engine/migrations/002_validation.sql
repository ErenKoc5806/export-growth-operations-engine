BEGIN IMMEDIATE;

-- v1 allowed invalid revisions through append_quotation_revision. Refuse to
-- upgrade until those rows are reviewed; triggers protect future writes only.
CREATE TEMP TABLE _migration_validation (ok INTEGER NOT NULL CHECK(ok = 1));
INSERT INTO _migration_validation
SELECT CASE WHEN EXISTS (
    SELECT 1 FROM quotation_revision
    WHERE status NOT IN ('DRAFT', 'APPROVED')
       OR currency IS NULL OR length(currency) != 3 OR currency GLOB '*[^A-Z]*'
       OR unit IS NULL OR length(unit) = 0 OR unit GLOB '*[^A-Z0-9_]*'
       OR sku IS NULL OR trim(sku) = ''
       OR (status = 'APPROVED' AND (approved_by IS NULL OR trim(approved_by) = ''))
       OR NOT json_valid(quantity_text)
       OR CAST(quantity_text AS REAL) <= 0
       OR NOT json_valid(unit_price_text)
       OR CAST(unit_price_text AS REAL) <= 0
) THEN 0 ELSE 1 END;
DROP TABLE _migration_validation;

-- v1 ingested a full synthetic order while labeling the opportunity DISCOVERED.
UPDATE opportunity SET status = 'SYNTHETIC_DRAFT'
WHERE source_system = 'synthetic' AND status = 'DISCOVERED'
  AND EXISTS (SELECT 1 FROM sales_order s WHERE s.opportunity_id = opportunity.id);
INSERT INTO audit_event (opportunity_id, action, actor_id, target_id,
                         before_state, after_state, occurred_at_utc)
SELECT id, 'SYNTHETIC_STATUS_MIGRATED', 'migration:002', id,
       'DISCOVERED', 'SYNTHETIC_DRAFT', strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
FROM opportunity WHERE source_system = 'synthetic' AND status = 'SYNTHETIC_DRAFT'
  AND EXISTS (SELECT 1 FROM audit_event a WHERE a.opportunity_id = opportunity.id
              AND a.after_state = 'DISCOVERED');

-- Reject invalid commercial data even if a caller bypasses the Python validator.
CREATE TRIGGER quotation_validate_insert BEFORE INSERT ON quotation_revision
BEGIN
    SELECT CASE WHEN NEW.status NOT IN ('DRAFT', 'APPROVED')
        OR NEW.currency IS NULL OR length(NEW.currency) != 3
        OR NEW.currency GLOB '*[^A-Z]*'
        OR NEW.unit IS NULL OR length(NEW.unit) = 0
        OR NEW.unit GLOB '*[^A-Z0-9_]*'
        OR NEW.sku IS NULL OR trim(NEW.sku) = ''
        OR (NEW.status = 'APPROVED' AND (NEW.approved_by IS NULL OR trim(NEW.approved_by) = ''))
        OR typeof(NEW.quantity_text) != 'text'
        OR NOT json_valid(NEW.quantity_text)
        OR json_type(NEW.quantity_text) NOT IN ('integer', 'real')
        OR CAST(NEW.quantity_text AS REAL) <= 0
        OR CAST(NEW.quantity_text AS REAL) >= 1e308
        OR typeof(NEW.unit_price_text) != 'text'
        OR NOT json_valid(NEW.unit_price_text)
        OR json_type(NEW.unit_price_text) NOT IN ('integer', 'real')
        OR CAST(NEW.unit_price_text AS REAL) <= 0
        OR CAST(NEW.unit_price_text AS REAL) >= 1e308
        THEN RAISE(ABORT, 'invalid quotation revision') END;
END;

CREATE TRIGGER opportunity_validate_insert BEFORE INSERT ON opportunity
BEGIN
    SELECT CASE WHEN NEW.status NOT IN (
        'SYNTHETIC_DRAFT', 'DISCOVERED', 'CONTACT_REVIEW', 'CONTACT_READY',
        'OUTREACH_REVIEW', 'RFQ_RECEIVED', 'QUOTE_REVIEW', 'QUOTE_APPROVED',
        'PO_REVIEW', 'ORDER_DRAFT', 'DOCUMENT_REVIEW', 'CLOSED'
    ) THEN RAISE(ABORT, 'invalid opportunity status') END;
END;
CREATE TRIGGER opportunity_validate_update BEFORE UPDATE OF status ON opportunity
BEGIN
    SELECT CASE WHEN NEW.status NOT IN (
        'SYNTHETIC_DRAFT', 'DISCOVERED', 'CONTACT_REVIEW', 'CONTACT_READY',
        'OUTREACH_REVIEW', 'RFQ_RECEIVED', 'QUOTE_REVIEW', 'QUOTE_APPROVED',
        'PO_REVIEW', 'ORDER_DRAFT', 'DOCUMENT_REVIEW', 'CLOSED'
    ) THEN RAISE(ABORT, 'invalid opportunity status') END;
END;

CREATE TRIGGER po_validate_insert BEFORE INSERT ON customer_po
BEGIN
    SELECT CASE WHEN NEW.currency IS NULL OR length(NEW.currency) != 3
        OR NEW.currency GLOB '*[^A-Z]*'
        OR NEW.unit IS NULL OR length(NEW.unit) = 0
        OR NEW.unit GLOB '*[^A-Z0-9_]*'
        OR NOT json_valid(NEW.quantity_text)
        OR json_type(NEW.quantity_text) NOT IN ('integer', 'real')
        OR CAST(NEW.quantity_text AS REAL) <= 0
        OR NOT json_valid(NEW.unit_price_text)
        OR json_type(NEW.unit_price_text) NOT IN ('integer', 'real')
        OR CAST(NEW.unit_price_text AS REAL) <= 0
        OR NOT json_valid(NEW.total_text)
        OR json_type(NEW.total_text) NOT IN ('integer', 'real')
        OR CAST(NEW.total_text AS REAL) <= 0
        THEN RAISE(ABORT, 'invalid customer PO') END;
END;
CREATE TRIGGER order_validate_insert BEFORE INSERT ON sales_order
BEGIN
    SELECT CASE WHEN NEW.status != 'DRAFT_REQUIRES_REVIEW'
        OR NEW.currency IS NULL OR length(NEW.currency) != 3
        OR NEW.currency GLOB '*[^A-Z]*'
        OR NEW.unit IS NULL OR length(NEW.unit) = 0
        OR NEW.unit GLOB '*[^A-Z0-9_]*'
        OR NOT json_valid(NEW.quantity_text)
        OR json_type(NEW.quantity_text) NOT IN ('integer', 'real')
        OR CAST(NEW.quantity_text AS REAL) <= 0
        OR NOT json_valid(NEW.unit_price_text)
        OR json_type(NEW.unit_price_text) NOT IN ('integer', 'real')
        OR CAST(NEW.unit_price_text AS REAL) <= 0
        OR NOT json_valid(NEW.total_text)
        OR json_type(NEW.total_text) NOT IN ('integer', 'real')
        OR CAST(NEW.total_text AS REAL) <= 0
        THEN RAISE(ABORT, 'invalid sales order') END;
END;
CREATE TRIGGER po_no_update BEFORE UPDATE ON customer_po
BEGIN SELECT RAISE(ABORT, 'customer PO is immutable'); END;
CREATE TRIGGER order_no_update BEFORE UPDATE ON sales_order
BEGIN SELECT RAISE(ABORT, 'sales order draft is immutable'); END;

PRAGMA user_version = 2;
COMMIT;
