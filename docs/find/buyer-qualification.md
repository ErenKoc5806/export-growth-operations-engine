# Find #4 — review company/product fit

`BuyerQualification(PilotStore(path)).decide(...)` records an append-only human
decision for one product/profile revision and one candidate. The reviewer cites
candidate evidence IDs, explains the comparison and marks **product
specification**, **commercial role** and **Germany corridor** as `CONFIRMED`,
`MISMATCH` or `UNKNOWN`. `ACCEPT` requires all three confirmed and the current
manufacturer profile approved. `REJECT` and `DEFER` retain negative or
uncertain findings. A keyword match or Trade Intelligence confidence score
never creates an accepted decision automatically.

`status(product_id, candidate_id)` derives the latest effective decision by
monotonic row sequence. It returns `QUALIFIED` only while the cited company
evidence set and manufacturer profile revision/hash are current and profile
approval remains effective. New evidence, product changes or revocation make
an old acceptance `REVIEW_REQUIRED`. The decisions themselves cannot be
updated or deleted. Every result has `outreach_allowed=false` because contact
route verification and exact send approval are separate Sell gates. A future
contact-enrichment adapter must require `QUALIFIED` before advancing.

The example tests use invented product and company data to exercise the
mechanics. No real manufacturer SKU, drawing or technical-use confirmation is
available for a live buyer decision. The [Berner candidate](company-candidates.md)
therefore remains **unqualified**. Actual fit and classification review must
be supplied by an authorized manufacturer operator under [pilot readiness #13](https://github.com/ErenKoc5806/export-growth-operations-engine/issues/13).
