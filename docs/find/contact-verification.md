# Find #6 — route verification and freshness

`ContactRoutes.check(...)` adds an immutable, sequenced check for a recorded
route. It records a method (`MANUAL_PAGE`, `MANUAL_CALL` or `PROVIDER_FEEDBACK`),
result, exact observed source URL, check time, explanation, actor and an
optional domain-review reference. `ROUTE_CONFIRMED` requires a currently
qualified company/product pair, a current source-use decision, a recorded
observation, an operator check, a plausible match to the candidate's domain
or an explicit domain exception, and a known role for a named person.
Provider feedback alone cannot confirm a route. A format check does not count.

`read()` reports `VERIFIED_ROUTE` only while the cited observation/correction
set and collection policy are current and the check is under 90 days old.
New source observations or corrections require another check. `BOUNCED`,
`INVALID`, suppression, expiry, source-policy revocation and stale buyer fit
block a current verified status. The full check log remains append-only, with
row sequence deciding the latest result. The route can also remain
`UNVERIFIED` or `REVIEW_REQUIRED`. No result asserts delivery or permits
outreach; `outreach_allowed` remains false. Sell must separately review
marketing channel permission, recipient, content and the send attempt.

The tests use invented `example.org` addresses and operator observations.
No live person was contacted and no mailbox or phone provider was exercised.
The real source-use and personal-data decisions in
[pilot readiness #13](https://github.com/ErenKoc5806/export-growth-operations-engine/issues/13)
still gate any live verification.
