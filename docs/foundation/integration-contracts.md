# FND-014 — Integration boundaries

The main product owns canonical opportunity, approvals, quotation revisions, local sales order and document drafts. Each adapter maps external data into a versioned input and retains `source_system`, `source_ref`, source URL or document hash, and observed UTC time. Unknown schema versions or missing provenance stop at review. The [shared model](shared-domain-model.md) defines field ownership; [pilot architecture](pilot-architecture.md) records the component choices.

| Boundary | Input to main product | Output from main product | Failure owner and mode today |
| --- | --- | --- | --- |
| Trade Intelligence | Research candidate, company/contact route, source evidence and timestamp; HS6/country filter | Explicit selection/qualification request | TI owns its API and research store. Main product owns canonical mapping and review. Offline audit exists; no product adapter is connected. |
| Selected AI-Worker PO function | Extracted PO lines with source document hash and confidence/uncertainty | Reviewed mapping against SKU, quote revision, quantity and price | Main product blocks mismatches. AI-Worker imports/tests need repair before reuse; no product adapter is connected. |
| Mail provider | Provider result for immutable recipient/content hash and attempt key | Human-approved send request only after identity and exact-content check | Unknown delivery stays in review; do not auto-retry. Current Foundation uses an in-memory mock only. |
| ERP | Provider order reference/result for a reviewed PO | Idempotent local sales-order export after reconciliation | Main product keeps local order distinct from ERP acceptance. Current mock captures payload only. |
| Carrier / documents | Operator-entered shipment details and later provider reference | Reviewable draft invoice and packing list | Booking and issuance are outside the first technical slice. |

Do not pass the two component repositories' `app` packages into one Python environment. TI can run behind its own API process; selected AI-Worker functions require an isolated adapter or controlled extraction with contract tests. A response named `READY` or `APPROVED` in a component is not main-product human approval. The main product assigns its own opportunity ID and binds a local approval to an immutable target revision or content hash.

Adapter sequence: validate schema and scope → preserve source evidence → show uncertain matches to the operator → record approval and attempt key → perform at most one external write → reconcile provider result. `UNKNOWN` is not success or safe failure. Future HTTP interfaces should authenticate requests, set timeouts and rate limits, and classify read errors under [retry policy](pilot-retry.md). No API server or live connector is committed in Foundation.
