# Sell #11 — exact approval and synthetic delivery record

`SellDelivery.preview(draft_id, revision)` exposes the exact recipient, sender,
subject, body, empty attachment digest and canonical envelope SHA-256. The
operator must inspect these fields and pass that digest to `decide(...,
"APPROVE", ..., expected_envelope_sha256=..., reviewed_claims=True)`. Only an
administrator can approve or revoke; decisions are append-only. Approval is
valid for one current email draft revision. A later edit, rejection, changed
Find evidence, source revocation, suppression or explicit send revocation
blocks dispatch. A new revision needs a new approval.

`dispatch_synthetic` accepts only reserved example recipients and an injected
`CaptureMailbox` test double. The attempt and idempotency key are committed
**before** the mock call. Replay reads that attempt; it cannot invoke the
provider again. An exception, timeout or process stop leaves the attempt
`UNKNOWN`. An administrator can record a provider lookup and its reference
through `reconcile`; even a `PROVIDER_NOT_FOUND` result never automatically
retries the same revision. A new intentional draft revision and approval are
needed for a new attempt.

`CAPTURED_NOT_DELIVERED` and a `MOCK-*` ID describe an in-memory capture only.
They are **not** evidence that a mailbox received mail. No SMTP adapter or
real send permission is enabled. The #11 sandbox acceptance still needs a
controlled mailbox, provider credentials, a provider message ID and an actual
delivery/reconciliation test. Real recipient use also depends on
[pilot readiness #13](https://github.com/ErenKoc5806/export-growth-operations-engine/issues/13).
The broad approval revocation contract is tracked in
[#16](https://github.com/ErenKoc5806/export-growth-operations-engine/issues/16).
