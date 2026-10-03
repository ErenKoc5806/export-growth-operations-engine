# FND-005 — Pilot persistence and data lifecycle

## Decision and boundary

The main product stores its canonical commercial records in one local SQLite database. This fits the initial single-operator, single-manufacturer pilot and avoids a database service. Trade Intelligence keeps its research database; an imported record stores its `source_system` and `source_ref` here. AI-Worker output is reviewed before it becomes a canonical commercial record. The database is **not** the ERP system or a document issuance system.

`pilot_engine/migrations/001_initial.sql` defines the first schema; `002_validation.sql` adds database guards and corrects v1 synthetic status; `003_access.sql` adds append-only access decisions. `PilotStore` applies numbered forward migrations using SQLite `user_version`. Back up the previous database before future migrations; an unknown newer version is rejected. SQLite foreign keys are enabled on each connection. One writer transaction covers each synthetic case ingestion, quotation revision, approval or opportunity status change. A collision-resistant production identifier must replace the fixture's readable IDs before live use.

## Records and integrity

| Record group | Tables | Integrity rule |
| --- | --- | --- |
| Manufacturer, product, target, buyer | `manufacturer`, `product`, `market_target`, `buyer_company`, `contact_evidence` | Product classification remains `UNVERIFIED`; contact route and source provenance are separate from event logs. |
| Opportunity and decisions | `opportunity`, `approval`, `outreach`, `action_attempt`, `audit_event` | Status changes use the FND-004 transition graph. Approvals bind action, actor, target revision or content hash. Attempt idempotency keys are unique; audit events are append-only. The current synthetic adapter makes no outreach attempts. |
| Buyer request and sale | `rfq`, `quotation_revision`, `customer_po`, `sales_order` | Quotation revisions are append-only and PO references the exact accepted revision. PO is unique per local order; order idempotency keys are unique. Decimal quantities and prices are text with explicit unit and currency. |
| Fulfilment drafts | `shipment`, `document_draft` | Documents remain review drafts with revision and source order. |
| Replay protection | `case_ingest` | Exact synthetic fixture replay is a no-op. A changed fixture using the same opportunity ID fails instead of silently rewriting evidence or duplicating the order. |

The executable adapter `PilotStore.save_synthetic_case` exercises an atomic synthetic Find → Sell → Execute record set. It uses the terminal `SYNTHETIC_DRAFT` status because storing an example does not mean outreach occurred or that a live workflow advanced. `transition` checks the graph and requires approval bound to the selected quotation revision or PO before approval and order stages. An inbound RFQ must exist before entering `RFQ_RECEIVED`; where an outreach record exists, its exact content hash must have a matching approval. These checks share the status transaction. `record_approval` derives its actor from the local OS account; see [FND-006 access design](pilot-access.md) for its single-host limits. Outreach sending and other stage-specific checks remain future application work. Draft documents are neither issued nor dispatched. Live importer, real SKU/classification review, external delivery and ERP handling are separate work.

## Time, sensitive data, and retention

All new event/record times are ISO 8601 UTC with offset; imported contact check time must be an actual supplied UTC timestamp. Do not invent a retrieval date. The contact address and route are stored only in `contact_evidence`, not the general audit table or `read_summary` diagnostics. The committed fixture uses `example.com` and invented people/companies. Local owner-only file access is enforced, but encrypted backup storage, secret management and a real customer retention decision are still required before storing live contacts.

Retention needs by data class:

| Class | Pilot handling and review trigger |
| --- | --- |
| Synthetic local database and test copies | Ephemeral; remove when the development task ends. Never use customer information in fixtures. |
| Contact evidence and outreach attempts | Review when the opportunity closes or a contact becomes stale; remove unnecessary personal route data after the operator-approved retention period. Preserve a non-contact audit reference where justified. |
| RFQ, quote, PO, order, shipment and approvals | Preserve their linked history while the case is active; determine applicable contractual, accounting and jurisdiction-specific retention period with the operator before live collection or deletion. |
| Backups | Limit access like the live database; choose a rotation and deletion period before live operation. A backup can retain records removed from the active database, so apply the same retention decision to old copies. |

No automatic deletion is enabled. Real customer retention periods and legal basis are an explicit live-pilot gate; deletion across linked records, append-only audit history and backups needs a reviewed policy, not a naive cascade.

## Backup and restore

Use `PilotStore(path).backup_to(backup_path)` for a consistent SQLite snapshot; it runs `PRAGMA integrity_check` on the output. Store it outside the working tree on access-controlled, encrypted storage in live use. Take a backup before each migration and at least daily when operating the pilot. Test restoring a copy after schema changes and periodically during operation.

To restore, stop all writers, keep the original database as a rollback copy, copy the backup to a **new** path, instantiate `PilotStore(new_path)` to check the schema version, and inspect `PRAGMA integrity_check` plus representative opportunity/order counts. Switch the application path only after review. The automated test restores a backup at a new path and reads the order. SQLite database files and backups must never be committed to git.
