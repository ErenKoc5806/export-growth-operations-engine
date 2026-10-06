# Sell #12 — operator-recorded phone and form actions

`ManualContact.open_opportunity(handoff_id)` creates a stable local Sell
opportunity for a current, synthetic Find handoff. `plan` records an intended
phone call or contact-form visit with the current verified route, destination,
planned time, action summary and an explicit operator claim review. It checks
the exact destination and source-use policy through the Find handoff. A reused
operation key returns the same plan only if all inputs match. Planning does
not call, open a form or submit anything.

After an operator acts outside the application, `record` accepts an explicit
operator attestation, actual UTC time, outcome, notes and next step. `ATTEMPTED`
means an action was tried, `CONNECTED` means the operator reports a phone
conversation, and `UNKNOWN` means the result is unclear. A form confirmation
page alone cannot be recorded as a connected conversation or buyer reply.
Events are append-only and an event key prevents duplicate reports. There is
no automatic retry, transcript, inferred buyer response or RFQ.

The action remains visible for audit if its route later becomes stale or
suppressed; the read view hides that destination and flags re-review. New
plans require a current handoff. Only synthetic example contacts can enter
this path. Live manual contact still depends on
[pilot readiness #13](https://github.com/ErenKoc5806/export-growth-operations-engine/issues/13).
