# Execute #19–25 — synthetic technical case

`ExecuteOrder` starts from an effectively approved Sell quotation. `save` binds a
synthetic PO source and original-document SHA-256 to a versioned, independently
entered PO snapshot. `read` lists missing or conflicting buyer, seller, product,
quantity, price, total and delivery terms. `decide(..., "APPROVE")` requires the
exact current PO hash and quote revision, then creates one immutable local order.
Replay returns the same order ID. Before PO approval, an expired, changed or
revoked quotation blocks the order. Once accepted, the order retains its exact
PO hash, quotation revision and quote-approval timestamp; later quote expiry
does not suspend fulfillment. A PO decision revocation still makes the order
review-required, and a created order cannot be silently replaced by a corrected PO.
An explicitly reviewed PO amendment can accept a buyer-name, reduced quantity,
reconciled total or delivery-place difference. The reviewer must select every
displayed difference on the exact PO payload and cite a reason. Changes to
seller, SKU, unit, price, currency or delivery rule require a new quotation;
invalid line arithmetic cannot be overridden. An accepted amendment creates
an immutable local order commercial version 2 and an append-only event bound
to the original PO byte hash, exact payload hash, quotation revision, actor and
decision. This synthetic version does not issue a revised commercial offer.
The original PO bytes are **hashed, not stored**; the source reference must be
retained by the operator. Only `SYN-` evidence is accepted in this slice.

`ExecuteOperations` records manual ERP handoff, goods-ready observations,
freight plans and synthetic carrier-confirmation evidence as append-only events.
The manual handoff embeds a reviewable, order-hash-bound export preview with
customer mapping, tax mapping, line, price and delivery fields. No ERP or
carrier API is called. Planned and actual readiness are separate;
an estimated/requested freight plan never implies a booking. A synthetic
confirmation is explicitly marked `real_carrier_booking: false`.
Freight requests require package count, weights and dimensions.
Recording a synthetic booking observation requires current `READY` evidence,
the latest requested freight plan and a reviewed packing revision bound to that
plan. Order/shipment identity, quantity, package count, net/gross weights and
package dimensions reconcile before recording. The observation pins the packing
hash/revision and freight event; a stale or unreviewed revision is rejected.
`UNKNOWN` remains an unresolved observation, and replay of either outcome
returns the same event while a second operation key cannot create another
booking observation. This never invokes a carrier.

`ExecuteDocuments` creates versioned draft commercial invoices, packing lists
and document checklists. Invoice review requires legal-party and tax-review
references plus a destination-specific requirements review. The operator records
manufacturer-reviewed HS classification and country of origin with distinct
synthetic evidence and reviewer identity. The research HS filter and seller
address never supply these facts. Packing quantities and weights must reconcile
to the order and current freight plan; a changed plan stales the old review.
The invoice copies net/gross weights only from the currently reviewed packing
revision and binds the packing hash and export-fact sequence. Changing either
makes the invoice require a new revision and review. Checklist items
retain `REQUIRED`, `NOT_REQUIRED` or `UNKNOWN` and their operator evidence.
Review means only a **reviewed synthetic draft**. No issued original is produced.
`case_summary` links the RFQ, quote, PO, order, operations and document versions,
with unresolved items, append-only operator time/correction/failure metrics and
separate false flags for real release, booking and physical shipment. Technical
acceptance requires a manual ERP handoff record, a readiness observation, a
requested freight plan and three reviewed document drafts. A `PENDING`
readiness observation can satisfy technical acceptance; it never asserts that
goods are physically ready. Metrics supplied by a synthetic operator are only
test observations, not measured productivity gains.

The first real pilot still needs manufacturer-authorized PO, pricing and tax
information, ERP permissions (or an authorized manual handoff), packing facts,
carrier evidence and document review under [pilot readiness #13](https://github.com/ErenKoc5806/export-growth-operations-engine/issues/13).
The local test actor is not that authorization. Follow-up work must measure
operator time and corrections in a real pilot, and design approved external
adapters separately; the synthetic technical case is not a live export.

## Partial shipment records

`ExecuteShipments` allocates immutable whole-unit quantities against an approved
order and the latest confirmed cumulative goods readiness. Allocation and insert
share a SQLite writer transaction, so concurrent requests cannot exceed the
order or ready quantity. A repeated operation key returns the same shipment
only for matching inputs. Later readiness observations cannot reduce confirmed
quantity below allocated units.

Each shipment has its own versioned freight plan, packing list and invoice
draft. Packing must reconcile to its shipment quantity and current freight
package count, weights and dimensions. The invoice uses that shipment's
quantity and the approved order unit price, and pins the reviewed packing
revision and manufacturer-reviewed HS/origin facts. New freight, packing or
export facts make affected document reviews stale. Synthetic booking evidence
is recorded once per shipment with packing hash and freight version; an
unknown outcome cannot be retried automatically. Later changes mark that
observation for review without deleting its audit record. These records always
report `real_shipped_quantity: 0`.

Existing order-wide freight and document records cannot be mixed with the
shipment path. The operator console exposes `shipment-create`,
`shipment-freight`, `shipment-packing`, `shipment-invoice`,
`shipment-document-review` and `shipment-booking`, with corresponding read
views. The guided visual case still follows the full-order path; shipment
forms remain a task for the visual operator workspace.
