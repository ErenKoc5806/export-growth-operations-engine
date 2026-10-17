# Authorized local source intake (#67)

Implementation supports a separate local intake workspace. Manufacturer/IT
acceptance and real inputs in #13 have **not** been supplied. Tests use fictional
documents in disposable databases, not manufacturer consent. Keep #67's real
sample acceptance open until #13 is evidenced. #68 still owns target Windows
rehearsal and full workflow activation.

## Start and record authorization

1. Stop other database users and run the [verified maintenance procedure](live-schema.md)
   with new backup/rehearsal paths to install extension **v4**. This upgrades
   either legacy v29 without a live extension or released v1/v2. Backup/restore
   comparisons include existing live history and schema; post-DDL checks preserve
   all existing rows/schema objects. Legacy `user_version=29` stays intact.
2. Complete the [retention/control setup](evidence-retention.md), with reviewed
   policy/hold/storage evidence and a private external current ledger. A populated
   upgrade must also register existing recovery copies. Then run
   `python -m pilot_engine.live_operator --database <private-database-path> --retention-ledger <private-current-ledger-path>`.
   A separate workspace opens on `127.0.0.1` with a new per-process bearer session.
   The launching OS account must pass the existing private storage checks.
   Full `AppConfig` Windows pilot activation remains disabled. This intake app
   has no mail/provider/ERP/carrier adapter or synthetic demo route.
3. The first screen requires manufacturer, authority reference, authorizer,
   named operator, separate named approver/role, IT reviewer, baseline,
   backup/retention owner and expiry. Upload manufacturer/data-use approval and
   IT acceptance documents. Explicitly attest people/scope authorization, data
   handling, account/backup arrangements and acceptance of the trusted-owner
   limit. Missing proofs, unchecked acceptances, synthetic references, identical
   operator/approver or expired authority block enrollment. One manufacturer is
   supported; its authorization is bound to the recording OS actor.
4. Inspect the saved authorization/evidence before intake. This records supplied
   evidence; it does not verify a signature or independently authenticate a human.
   Do not enter invented approvals into a pilot database. Closing a GitHub issue
   or setting an environment flag cannot supply manufacturer authorization.

## Operator workflow

| Source | Required content |
| --- | --- |
| Product/classification | SKU, name, drawing/dimensions, material, use, reviewed HS6 and review reference |
| Price/tax authority | SKU, unit, positive unit price, EUR, payment terms, Incoterm/place, tax/price authority reference, validity |
| Buyer source | Company, DE, actual source website, role and fit evidence; no contact or outreach action |
| Customer PO | PO reference, buyer, SKU, quantity/unit/price/total, EUR, payment terms, Incoterm/place and delivery |

Scope is HS6 732690 / Germany / EUR. A recorded HS review is not automated tariff
classification. A PO intake is not quote matching, an order or a tax calculation.
Approval here never authorizes a recipient, message, email, ERP write, carrier
booking or document release. The separate [local live Execute drafts](live-execute.md) now consume approved
product, price and PO records through their own guarded service. External
Find/Sell/provider adapters are not
implemented by this source capture.

Every source requires a unique source-version reference, permission/terms
references, processing basis, the registered approver's source-review name,
future retention end, original source file and usage-permission/terms evidence.
Files are limited to 5 MB each and stored as immutable BLOBs in the private DB,
with SHA-256, OS actor and time. Source/revision/evidence creation is atomic.
Browser-supplied filenames are never used as storage paths. The named human must
assess authenticity and source terms; bytes alone cannot prove consent.
Synthetic IDs/references and example buyer domains cannot authorize live rows.

The record screen shows origin, full content and terms, source permission,
retention, evidence downloads/checksums, revision/payload hash, OS actor and
unresolved review state. **New version** preserves old content/decisions and
requires a fresh source-version reference, evidence and human confirmation.
Internal IDs are generated/selected by links; no JSON editing or internal ID
entry is required.

The named approver reviews the full record and source evidence. The operator
records approve/reject/revoke, name, reason, method, confirmation time and uploaded
evidence/reference. Role/authority come from the roster; the OS actor comes from
the process. The UI labels this **operator attestation**, not an authenticated
approver login. UI time entry uses Istanbul time; storage/audit uses UTC.

Revision/hash and latest decision sequence are checked in one writer transaction.
A stale page cannot approve revised content or overwrite a later revocation.
A new revision starts with review required. Expired authority, retention or price
validity, withdrawn source permission, pilot stop or rejection/revocation prevents
the current record from showing approved. Source/pilot stop controls retain
history. Pilot stop is terminal in this version; renewal requires a separately
reviewed procedure.

## Storage, access and evidence

V2 added append-only `live_document`, `live_authorization`,
`live_source_permission`, `live_confirmation_evidence` and `live_intake_stop`.
V3 adds control binding and purge receipts. Normal writes remain append-only;
supported offline administrator maintenance narrowly removes reviewed live
content and restores all guards in one transaction. Legacy synthetic tables
remain unchanged. Read-only viewers inspect available evidence;
operators/admins record intake and confirmations, bound to the enrollment OS
actor. HTTP uses existing session/CSRF/Host/origin checks, no-store/CSP headers
and redacted logging. Downloads are authenticated binary attachments.

The [trusted-owner limit](trust-model.md) applies: an owner/admin can edit SQLite,
and a bearer cookie does not authenticate the human at the browser. No new
service/account isolation is included. Expiry blocks use but does not itself erase
evidence/backups. The named owner must use the [supported retention procedure](evidence-retention.md),
including document-only versus complete-pilot scope and separate copy disposition.
Old copies are checked against current external deny history before use.
Encryption, forensic erasure and automatic deletion are not promised. V1 records
without authorization do not become implicitly authorized during upgrade.

Automated tests cover missing authorization, all four inputs, stale forms,
revisions, rejection/revocation, permissions, scope/origin/expiry, changed OS actor,
viewer denial, atomic writes and immutable history. Actual HTTP multipart forms
exercise enrollment, intake, confirmation, CSRF/origin denial, synthetic-route
isolation, escaped text and evidence download. Native Windows runs the same
tests. Real consent and target-workstation acceptance remain unevidenced.
