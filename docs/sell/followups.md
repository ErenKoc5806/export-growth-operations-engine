# Sell #13 — follow-up reminders and stop conditions

`Followups.schedule` binds a due date, owner, channel, reason and count of
recorded manual actions to a Sell opportunity and a specific operator-reported
phone or form action. The source action must have a known attempted/connected
outcome and a current verified Find handoff. Operation keys prevent duplicate
plans after a restart. `list_due(owner_id)` is a read-only view: it reports
due or overdue work, but never performs outreach.

An unknown action result, changed or suppressed route, or recorded reply,
opt-out or closure suppresses a reminder. A reply is an operator-attested
reference until inbound response handling is implemented in #14. For an
opt-out stop, the route must first be entered into Find's suppression list.
The operator can mark a reminder skipped with a reason. Completion of a
phone/form follow-up requires a separate, recorded operator action; the
original contact cannot count twice. Results and stops are append-only.

An email reminder is a plan only. The mock `CAPTURED_NOT_DELIVERED` outcome in
#11 cannot complete one, and no email is sent by the scheduler. A real email
follow-up will require its own newly approved exact draft and provider result
after the controlled sandbox and readiness gates are available. This is a
synthetic technical slice, not live follow-up automation.
