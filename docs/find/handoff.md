# Find #7 — evidence-bound research handoff

`FindHandoff(PilotStore(path)).record(product_id, candidate_id, route_id)`
creates a stable handoff ID and an immutable revision only when the product fit
is `QUALIFIED` and the contact route has a current `VERIFIED_ROUTE` check,
source-use decision and retention deadline. Replaying identical evidence
returns the same ID/revision. New candidate evidence, product revision,
contact observations, corrections or checks invalidate the old handoff;
after review the same ID gets another revision. A suppression or expired
policy blocks it. The read contract exposes manufacturer/product, Germany
market, buyer company, route, source observation IDs, candidate evidence IDs
and exact fit/check/profile/policy references.

The current implementation accepts `SYNTHETIC` handoffs only, with a reserved
example company domain. The read status is `CURRENT_RESEARCH` or
`REVIEW_REQUIRED`, and `send_allowed=false` always. This is a provenance
record for a future Sell workflow, not an opportunity, marketing permission,
approved recipient, delivery attempt or real lead. Sell must create a
separate opportunity and check channel permission and exact message approval.
The controlled real-data gate in
[pilot readiness #13](https://github.com/ErenKoc5806/export-growth-operations-engine/issues/13)
remains open.
