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
