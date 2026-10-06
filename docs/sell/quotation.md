# Sell #16 — versioned synthetic quotation

`Quotations.register_price_authority` binds a product, SKU, currency, exact
unit price and expiry to the SHA-256 of an invented test price document. The
original document must be retained separately. Hash registration proves which
bytes the operator reviewed; it does not authenticate a manufacturer. Real
pricing authority remains an open pilot input.

A quotation revision starts from an accepted synthetic RFQ. It copies the
reviewed specification, quantity/unit and current approved manufacturer
Incoterm, place, payment and lead-time fields. Price comes from the matching
registered authority; the total is computed from quantity × unit price with
currency precision checked. Every edit appends a complete revision. Approval
requires the latest exact payload digest and an authorized local actor;
revocation appends a new decision. An expired authority, changed RFQ or
revoked product blocks approval and makes an earlier quotation require review.

`APPROVED_SYNTHETIC` means a local technical approval only. `send_allowed` and
`customer_accepted` are always false. The software does not send a price,
claim customer acceptance, or open an order. Real sent/rejected/accepted
states require provider evidence and actual customer confirmation or a PO in
the Execute flow. This issue's live acceptance remains open.
