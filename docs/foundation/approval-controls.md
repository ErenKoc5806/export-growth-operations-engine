# FND-018 — Human decisions and evidence

| Gate | Operator reviews | Required record before action | Current implementation |
| --- | --- | --- | --- |
| Buyer qualification | Company/product fit, business route, source URL/ref, check time and verification status | Contact evidence plus a product-relevance decision and source reference | Contact provenance is stored; product-relevance decision and TI adapter are pending Find. Synthetic fixture is not a real buyer. |
| Outreach | Exact recipient and immutable content hash | Authenticated actor, approval, attempt key, provider outcome | Exact mock approval and duplicate/unknown behavior are tested; live send is absent. |
| Price/quotation | SKU, quantity, unit, currency, price, terms and revision | Actor and approval bound to latest quote revision | Local approval/transition check exists; manufacturer pricing authority must be confirmed. |
| Customer PO / local order | PO document hash, quotation revision and all commercial fields | Actor, decision, idempotent local order key | Local PO approval/validation exists; real PO import is absent. |
| Shipment document release | Draft values against order/packing evidence | Reviewer, revision, decision and final issue reference | Drafts only; no release, legal invoice or carrier booking is enabled. |

The first deployment has one authenticated POSIX operator, and the local access policy maps that account to administrative capability. The operating company still decides who can approve price, customer contact, order entry and document release before real use. Never infer approval from an AI suggestion, a component's `READY` field or a free-text `approved_by` value. `approval`, `audit_event` and `access_decision` persist decisions and actor/time; `action_attempt` provides the unique outbound key when future adapters use it. Operational JSONL is diagnostic, not a substitute for those records.

Contact route data remains in `contact_evidence`, separate from generic logs. Before real collection/outreach, record product relevance evidence, lawful basis and notice/retention decisions with the business owner; define deletion across SQLite and backups. Rejected or uncertain data stays in human review. An unknown provider result must be reconciled before retry. Mock outputs and real external outcomes must display distinct status and provider references. The functional Find/Sell/Execute issues must implement the remaining gates at their actual external boundaries.
