# First-pilot trust boundary — decision, 2026-10-15

## Scope and development limit

The first pilot is one manufacturer, one Windows workstation, one named operator
and one named human approver. Additional Windows hardening stops at PRs #69/#70.
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

The product owner accepts this limit for the narrow first-pilot design. The
manufacturer/IT has **not** yet accepted it; their actual acceptance belongs to
#13, and target-machine evidence belongs to #68. Do not infer either from CI.

## Recorded human review

The operator and approver must be named in the manufacturer-authorized roster.
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
  operator/approver walkthrough, exceptions and IT review. CI is supporting evidence
  only. Failures remain activation blockers; do not reopen an unlimited hardening
  workstream merely to obtain a stronger isolation claim than this pilot needs.
- #38: controlled mailbox/provider evidence and approved delivery controls before
  real email. ERP, carrier and document issuance remain separately gated.

## Revisit triggers

Introduce real service-owned data and authenticated sessions before supporting
untrusted users, remote/shared hosting, another manufacturer/tenant, or a customer
requirement for technically independent approvals. Scope that as separate work,
with its own acceptance criteria, rather than keeping #65 open indefinitely.
