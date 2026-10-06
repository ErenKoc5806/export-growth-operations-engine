# Sell #10 — reviewable outreach drafts

`SellDrafts(PilotStore(path)).create(handoff_id, sender_name=..., sender_address=...,
language="en", claim_refs=[])` creates a local text draft from a **current
synthetic** Find handoff. The operator supplies the sender identity and selects
claim evidence references from the approved product profile. The template uses
the approved product name and SKU, and it does not assert an exhaust or pipe
application. English, German and Turkish templates are available. This is a
text aid, not a translation or claim validation service.

`read(draft_id)` shows the exact recipient route, channel, language, sender,
subject and body, the fit decision and cited evidence IDs, permitted claims,
warnings and immutable revision. `revise` replaces the entire text and sender
snapshot in a new revision, using an expected revision to reject conflicting
edits. A different current handoff for the same product and company can change
the recipient. `reject` records an append-only rejection; the operator can
create a replacement revision. Earlier revisions remain visible. A suppressed,
expired or otherwise stale Find handoff changes the current draft to
`REVIEW_REQUIRED` and hides its recipient value.

Selected claim references must belong to confirmed profile claims. An edit is
flagged for human claim review, and known unconfirmed application phrases are
flagged when they appear. Free text cannot be proven safe by keyword matching:
the operator must check every factual claim, identity and recipient against
the displayed evidence before #11 can introduce approval. Every draft has
`send_allowed=false` and `approval_valid=false`; no mail, form or phone action
occurs here. A later approval must bind to the exact draft revision and
recipient snapshot, and must recheck the Find evidence and pilot readiness.

Only invented example domains are usable through the Find handoff. Real
manufacturer authorization, contact source terms and retention ownership
remain in [pilot readiness #13](https://github.com/ErenKoc5806/export-growth-operations-engine/issues/13).
