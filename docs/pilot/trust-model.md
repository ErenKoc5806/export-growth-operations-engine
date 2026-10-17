# First-pilot trust boundary — updated decision, 2026-10-17

## Scope and development limit

The first pilot is one manufacturer, one Windows workstation and one named
operator who declares authority for the work and may also review their own drafts.
The 2026-10-17 decision supports **SINGLE_OPERATOR_SELF_REVIEW**: a separate recorded
review of exact content, visibly labeled self-review. It is not an independent
second-person approval. **DISTINCT_PERSON_ATTESTATION** remains available when the
manufacturer uses a separate named reviewer. Existing enrollments keep their
recorded model; legacy enrollments remain the distinct-person model. Additional Windows hardening stops at PRs #69/#70.
Do not build a service-owned database, new Windows account provisioning, a SQLite
VFS or authenticated cross-account transport before #66/#67. Those are deferred
until a demonstrated pilot requirement or a deployment outside this trust model.

#65 closes for the implemented baseline and this documented scope decision. Its
closure does not certify the target workstation or authorize live activation.
The critical path is **#66 → #67 → #68**, with real manufacturer inputs #13 in
parallel and #38 before any real email. Keep existing runtime safeguards; this
decision changes the plan, not permissions or activation flags.

## Accepted product-design limit

The data owner and local administrator are trusted. They can edit SQLite outside
the app and bypass its roles/audit. A localhost browser cookie is a bearer token;
the launching process SID does not authenticate the human operating that browser.
Two Windows accounts with a writable database are not isolated from one another.
We do not claim tamper-proof approvals or malicious-insider protection.

The product owner accepts this limit for the narrow first-pilot design and has
declared that the first operator is also the responsible authorized reviewer.
Record actual scope/data-use and storage-review evidence under #13; the declaration
does not measure the computer's encryption, private permissions or backup state.
Target-machine evidence belongs to #68. CI does not provide that evidence.

## Recorded human review

The operator and reviewer must be named in the manufacturer-authorized roster.
For self-review they must be the same named person, and enrollment must explicitly
acknowledge that no independent second-person control exists. For distinct-person
review their names must differ. Missing/unknown model values cannot silently enable
self-review; omitted model on an older/API enrollment means distinct-person review.
The model is immutable within the enrolled pilot, not a per-request bypass flag.
One workstation is sufficient; separate Windows accounts are not a prerequisite
for this procedural control. IT decides the permitted account arrangement.

Before an action requiring approval, show the exact current content, recipient,
commercial terms and revision/hash as applicable. The approver reviews that
version and confirms through a recorded step. Record:

- Approver name/role and the manufacturer's authority reference.
- Exact target/revision/hash, decision, reason and confirmation time.
- Confirmation method and supporting evidence reference (for example a reviewed
  written confirmation), plus the OS actor who recorded it.

Identify an operator-recorded confirmation as an attestation, not an independently
authenticated approver login. Never overwrite the OS actor with a supplied human
name. Revised content needs fresh confirmation; rejection/revocation blocks the
next action under existing approval rules. #67 implements the capture and visible
provenance; #38 still owns the actual provider/send boundary. No current synthetic
approval counts as manufacturer authorization or approval of a real recipient.

## Required before activation

- #13: verified product/SKU/classification, permitted sources/retention, named
  people and explicit manufacturer/IT acceptance of this trust boundary.
- #66: versioned live schema, consistent private pre-migration backup, checksum,
  integrity/foreign-key checks and restore rehearsal on a separate path.
- #67: authorized live intake, visible origin and recorded human confirmation;
  synthetic evidence must never authorize a live record or action.
- #68: run the actual release on the intended Windows machine/filesystem; record
  release SHA, Windows/filesystem details, data/backup permissions, restore result,
  operator/reviewer walkthrough, exceptions and workstation/storage review. CI is supporting evidence
  only. Failures remain activation blockers; do not reopen an unlimited hardening
  workstream merely to obtain a stronger isolation claim than this pilot needs.
- #38: controlled mailbox/provider evidence and approved delivery controls before
  real email. ERP, carrier and document issuance remain separately gated.

## Revisit triggers

Introduce real service-owned data and authenticated sessions before supporting
untrusted users, remote/shared hosting, another manufacturer/tenant, or a customer
requirement for technically independent approvals. Scope that as separate work,
with its own acceptance criteria, rather than keeping #65 open indefinitely.
