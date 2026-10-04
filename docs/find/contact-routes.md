# Find #5 — contact route intake

`ContactRoutes(PilotStore(path))` records a route only for a company with a
current `QUALIFIED` product-fit decision. `authorize_source(...)` records an
administrator's source-specific collection decision, processing-basis assessment
reference, source-terms review reference and UTC retention deadline. The route
must match the approved source system, reference, URL and data class. A later
revocation or expired decision blocks new collection. This is a recorded human
attestation, **not** an automatic legal assessment or proof of permission.
The live inputs and responsible owner in [readiness #13](https://github.com/ErenKoc5806/export-growth-operations-engine/issues/13)
remain outstanding; tests use invented `example.org` contacts only.

`record(...)` distinguishes `GENERIC_EMAIL`, `SWITCHBOARD`, `CONTACT_FORM`,
`NAMED_EMAIL` and `NAMED_PHONE`. Named routes require a separate
`PERSONAL_ROUTE` decision and a name. All email and phone routes require that
decision, including generic inboxes and switchboards; only contact-form links
may use `BUSINESS_ROUTE`. An email format, a public page mention
and a source decision do **not** prove reachability, a person's role, consent
to marketing or delivery. Every route is `UNVERIFIED`, with
`outreach_allowed=false`; Find #6 must verify it and Sell must make an
independent channel/send decision. `record_missing(...)` retains an explicit
unavailable result for a qualified company.

Repeat observations of the same route use one ID and keep source references,
URLs and retrieval times. A different name or role fails visibly; the
operator can call `correct_identity(...)` with a reason, then record new
evidence. The correction event keeps actor/time/reason without retaining the
old personal text. Cross-type matches are shown as `possible_duplicates`.
The `suppress(...)` method stores a digest of an email/phone/form route and
redacts primary route values and names and masks source URLs in API reads. It blocks future collection across
generic and named variants of the same address/number. For email it also
blocks plus-address variants of the same base mailbox, conservatively treating
`name+tag@domain` and `name@domain` as one suppression group. This is an
opt-out safety rule, not a claim that every mail provider delivers those
addresses to the same inbox. `suppress_domain(...)` blocks all routes for
candidate companies under the selected company domain, including forms and
phone numbers. Its operator must scope the domain to the actual objection.
Existing v12 exact-hash suppressions remain effective after migration, but
their redacted original address cannot be reconstructed to infer a base
mailbox. An operator must reconcile those older entries before any live-data
migration; a fresh live database avoids carrying unresolved synthetic records.
HTTPS form URLs normalize host case and trailing slashes; query variants are
shown as possible duplicates rather than silently merged. Plain HTTP forms
are rejected. Direct SQL edits to route identity, source and value are rejected;
person name/role correction needs an append-only correction event, and
suppression only redacts values. A retention expiry
masks personal values on reads, but provenance URLs can still exist in the
database and backups. An operator must arrange their removal under the pilot's
retention process. No automatic
purge or live outbound delivery is claimed.

The [European Commission](https://commission.europa.eu/law/law-topic/data-protection/rules-business-and-organisations/application-regulation/do-data-protection-rules-apply-data-about-company_en)
explains that a named employee's business email can be personal data. Its
[guidance on third-party contact lists](https://commission.europa.eu/law/law-topic/data-protection/information-business-and-organisations/legal-grounds-processing-data/can-data-received-third-party-be-used-marketing_en)
also calls for lawful collection and respecting objections. German
[UWG section 7](https://www.gesetze-im-internet.de/englisch_uwg/englisch_uwg.html)
sets additional rules for marketing by electronic mail. The collection
decision in Find never asserts that a proposed outreach meets those rules.
