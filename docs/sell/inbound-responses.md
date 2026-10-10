# Sell #14 — provider signals and reviewed inbound evidence

`InboundResponses` accepts labeled **synthetic** provider observations and
inbound references. Provider `ACCEPTED`, `BOUNCED` and `UNKNOWN` describe a
separate source observation, not a buyer response. A captured mock message ID
can correlate an invented inbound message to exactly one Sell opportunity;
missing or ambiguous correlation remains `REVIEW_REQUIRED`. The original
source reference and received time are immutable, and repeated source IDs
cannot create duplicate records.

An operator reviews an inbound message as `INTEREST`, `REJECTION`,
`RFQ_CANDIDATE`, `OTHER` or `OPT_OUT` with a cited raw reference and explanation. A
correction appends a new review. `RFQ_CANDIDATE` does not create an RFQ; #15
must capture requested product, quantity and terms from inbound evidence.
An operator-reviewed response suppresses pending follow-up reminders. No
observed response is displayed as `NO_RESPONSE_OBSERVED`, never a rejection.

An `OPT_OUT` review resolves the exact route from a unique captured send, then
atomically stores hashed Find suppression rules and redacts matching active
routes, including mailbox aliases. The inbound read displays `SUPPRESSED`.
When the route cannot be uniquely correlated, the review displays
`PENDING_SUPPRESSION`; all routes for the reviewed opportunity's company and
product are blocked from new handoff, drafts, manual outreach and follow-ups.
An operator can inspect the evidence and use `opt-out-resolve` with the matching
route ID and explanation. Suppression remains durable after a later review;
repeated events do not undo it. No raw route value is written to the opt-out
event or access log.

All current evidence and provider IDs are test data. The in-memory mailbox
cannot demonstrate delivery, and the operator's classification is not proof
that a real buyer replied. Live provider and inbound account access remain
gated by the controlled sandbox, pilot readiness and source retention rules.
