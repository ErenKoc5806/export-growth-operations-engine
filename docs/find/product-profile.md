# Find #8 — manufacturer product profile

`ProductProfiles(PilotStore(path))` records the manufacturer and product under
stable IDs. `save_draft(payload, product_id=..., expected_revision=...)` creates
an immutable full snapshot and returns the product ID and new revision. A new
profile requires only the manufacturer and product names; all other fields may
remain pending. A stale `expected_revision` is rejected. The SQLite v5 migration
preserves existing synthetic cases and introduces append-only profile revisions
and decisions. The API uses the local OS account/role boundary; it does not
authenticate a manufacturer's representative or obtain permission on its own.

The payload includes manufacturer identity/country, SKU, name, unit, drawing
reference or dimensions, material, intended use, technical limits, claims,
search terms, target buyer role/country, six-digit research code, classification
review note and source reference. Claims and search terms are objects with
`text`, `confirmed_use` and `evidence_ref`. A search term such as `exhaust clamp`
may be kept as an **unconfirmed candidate**, but `read()` exposes only confirmed
terms in `approved_search_terms` after approval. Unconfirmed claims cannot be
approved. Approval additionally requires the specification, a source marked
`MANUFACTURER`, `REVIEWED` classification review status and the chosen pilot
country/code. The code remains a research filter, not a certified tariff ruling.

`decide(product_id, revision, "APPROVED" | "REVOKED", reason)` appends a decision
for the current revision; the latest decision by row sequence applies.
`require_current_approval(product_id, revision)` is the gate for future Find
adapters. A new draft or revocation makes the core product classification status
`REVIEW_REQUIRED`; it cannot reuse an earlier approval. Existing opportunities
are flagged in `profile_revalidation`, and the state transition API blocks
progression until an approved current revision is reviewed with
`acknowledge_revalidation(opportunity_id, revision, reason)`. New opportunities
on a profiled product start flagged too. This explicit review does not itself
prove buyer fit or authorize outreach: the later Find/Sell gates must check
their own evidence and approval.

No actual manufacturer data, drawings or credentials are committed here. The
values and manufacturer authority required for a live profile remain in
[pilot readiness #13](https://github.com/ErenKoc5806/export-growth-operations-engine/issues/13).
The test profile is entirely invented; its local approval exercises mechanics
only. Existing synthetic acceptance records intentionally do not gain a real
product profile through migration.
