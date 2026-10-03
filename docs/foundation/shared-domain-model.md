# FND-004 — Shared domain and data model, pilot contract v0.1.0

Scope: one Turkish industrial manufacturer, one operator, one product family, Germany, HS6 research filter `732690`, product named **bağlantı kelepçesi / connection clamp**. Technical specifications, actual SKU, intended exhaust/pipe use and tariff classification are still unverified. The fixture uses invented values and `.example` contact data.

## Canonical identity and ownership

The **main product application** owns the canonical `pilot_opportunity_id`, commercial records, decisions and documents. Trade Intelligence owns its original research/job/portfolio records; AI-Worker owns its original worker outputs. Integration adapters attach `source_system`, `source_ref`, source schema/version, retrieval timestamp and original evidence reference to each imported record. An upstream ID is never silently substituted for the main opportunity ID. Use UUID/ULID or another collision-resistant internal identifier when implementing persistence; the fixture's readable IDs are test data.

Every mutable record needs `created_at_utc`, `updated_at_utc`, `revision` and `created_by`/`updated_by` where an operator can act. Save timestamps in UTC; display in the user's locale. Money is a decimal string plus ISO 4217 currency; quantities are decimal strings plus unit. Do not use binary floats for amounts. Source documents and approvals remain linked after a revision. `schema_version` is required on exchanged payloads so an adapter can reject an unknown shape.

| Entity and owner | Required canonical fields | Relationship and rule |
| --- | --- | --- |
| Manufacturer / ProductProfile — main product | `manufacturer_id`, `product_id`, real `sku` when available, name, description, unit, `hs6`, specification reference, classification status | One manufacturer owns products. HS6 is a research filter; spec and classification are separate verified facts. |
| MarketTarget — main product | `target_id`, country `DE`, buyer/distributor profile, search terms with `confirmed_use` flag | Belongs to product. Exhaust/pipe wording remains unconfirmed until product fit is checked. |
| BuyerCompany / ContactEvidence — main product | `buyer_id`, country, role, contact route, source system/ref/URL, checked time, verification status/method | Company can have multiple contacts/evidence items; a missing route or unverified source blocks live outreach. Keep the source snapshot/revision. |
| Opportunity — main product | `pilot_opportunity_id`, product/target/buyer IDs, owner, status, revision | Stable parent for Find → Sell → Execute. Upstream IDs are separate mappings. |
| Outreach — main product | `outreach_id`, opportunity/contact IDs, immutable content hash, approval ID, attempt ID, provider result | Approval applies to exact content and recipient. Delivery unknown is a review state, never proof of delivery. |
| RFQ — main product | `rfq_id`, opportunity/buyer IDs, received timestamp, source reference, requested lines | An incoming request is evidence, not an assumed sale. |
| QuotationRevision — main product | `quotation_id`, integer `revision`, `rfq_id`, lines, price/currency, terms, approver, status | Old revisions stay readable. A PO references the accepted revision explicitly. |
| CustomerPO — main product | `po_id`, original source/document hash, `quotation_id` + revision, lines, reviewer, discrepancy status | Extracted text is a proposal until reviewed; mismatched amounts or product details block order creation. |
| SalesOrder — main product | `order_id`, `po_id`, opportunity ID, reconciled lines/totals, status, optional ERP ref | One local order per accepted PO and revision; retries use an idempotency key. ERP confirmation is separate. |
| Shipment / DocumentDraft — main product | `shipment_id`, `order_id`, destination, package/weight data; `document_id`, type, revision, source order ID, review status | Commercial invoice and packing list are reviewable drafts, not official issuance or carrier booking. |
| Approval / AuditEvent — main product | action, actor, timestamp, target ID + revision/hash, decision, before/after state, error/source ref | Append only; no approval is inferred from another module's `READY` status. |

## State and review boundaries

The base path is `DISCOVERED → CONTACT_REVIEW → CONTACT_READY → OUTREACH_REVIEW → RFQ_RECEIVED → QUOTE_REVIEW → QUOTE_APPROVED → PO_REVIEW → ORDER_DRAFT → DOCUMENT_REVIEW → CLOSED`. `pilot_engine.domain.require_transition` rejects skipped stages in this contract. These states are **not** automatic authorizations: the application must inspect contact evidence, immutable send approval, pricing approval and reviewed PO before performing actions. Any active stage can pause for review; rejection closes a case with reason. A failed or uncertain external send remains tied to its attempt ID and requires manual reconciliation before retry. No state grants automatic payment, ERP entry, carrier booking or legal invoice issuance.

## Component field mapping (reviewed at pinned commits)

| Canonical target | Existing source fields | Mapping rule / gap |
| --- | --- | --- |
| MarketTarget / Opportunity research context | Trade Intelligence `DeepDiveOpportunity.hs_code`, `product_description`, `country_code`, `country_name`, `direction`, `opportunity_score` | Preserve raw research fields and report year. Confirm Germany/DE and export direction; product description is research context, never a confirmed manufacturer SKU/spec. |
| BuyerCompany | TI `ValidatedCounterparty.name`, `country`, `role`, `company_type`, `website`, `evidence_urls`, `exact_product_evidence`, `commercial_role_evidence` | Accept only buyer role. Keep evidence refs and fit scores; verify actual clamp application. No canonical company ID is supplied, so match/review rather than auto-merge by name. |
| ContactEvidence | TI `BusinessContact.business_email`, `phone`, `contact_page_url`, `contact_confidence`; workspace `contact_id`, `source_url` or `official_source_url`, `route_status`, `verified_by_user`, `created_at_utc` | Route evidence and contact verification vary by path. An AI confidence score alone is not verified contact. Capture retrieval/check time separately if absent; never invent one. |
| Research handoff | TI `SalesOpportunityHandoff.opportunity_id`, `generated_at_utc`, `approval_status`, `commercial_validation`, `outreach_targets` | Store TI opportunity ID as `source_ref`. `APPROVED_FOR_OUTREACH` and `prepare_only` are research/preparation state, not permission to send or commit price. |
| RFQ | AI-Worker `SalesInquiry.inquiry_id`, `source_reference`, `customer`, `contact_email`, `items`, `status` | Preserve original ID/ref and map line units/quantities. Its status does not prove a buyer response in the main product; source and human review required. |
| QuotationRevision | AI-Worker `SalesQuotation.quotation_id`, `inquiry_id`, `items`, `status`, `payment_terms`, `delivery_terms` | Its `unit_price`/`total_price` are floats and a mock pricing provider can fill `100 EUR`; convert via decimal string only after real price review. No immutable revision/approval evidence in this model: create it in main product. |
| CustomerPO | AI-Worker `PurchaseOrder.po_number`, `customer`, `currency`, `items`, `total_amount` | AI extraction is a draft. `part_number`, integer `quantity`, Decimal `unit_price` help mapping, but source document hash, link to accepted quote revision, reviewer and mismatch disposition are missing. |
| ERP order | AI-Worker `ERPPurchaseOrder.external_po_number`, `customer`, `items`; `ERPCreateOrderResult.status`, `erp_order_number` | This model represents a purchase order API request; the manufacturer's **sales order** semantics and Canias mapping must be verified. Current Canias adapter is not configured; success cannot be inferred from local mapping. |

No current source model supplies all manufacturer shipment-document fields or the full approval chain. Keep unmapped fields explicitly null/pending review. Do not merge contact evidence, quotation approval or PO acceptance merely because record names resemble each other.

## Executable example and handoff to FND-005

`pilot_engine/fixtures/732690_de_synthetic.json` is a versioned example. `pilot_engine/workflow.py` checks the core references, evidence, approvals and decimal reconciliation, then returns **draft** records. It has no data store or external adapter. FND-005 decides storage, transaction/idempotency behavior, backup and retention for these entities. Live pilot suitability remains in FND-017 and the functional epics.
