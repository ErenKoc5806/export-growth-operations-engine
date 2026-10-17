# Local live Execute drafts (#75)

Implemented software boundary: approved product, price authority and manufacturer
PO → local live order → reviewed commercial-invoice, packing-list and shipment /
document-checklist **DRAFTS**. They remain **UNRELEASED**, including after approval.
There is no legal invoice numbering/issuance, ERP write, carrier booking, email,
provider call or automated outreach. Sell remains manual; Find is source research.

**Actual pilot acceptance is outstanding.** Fictional tests and CI do not supply
#13 manufacturer consent, qualified tax/classification/privacy decisions, the responsible storage reviewer's
actual disk/backup encryption and retention review (#74), or #68's real Windows
workstation rehearsal. Do not enter actual documents before those gates are met.
The [trusted-owner model](trust-model.md) continues: one manufacturer, one local
OS operator. The primary solo pilot uses **SINGLE_OPERATOR_SELF_REVIEW**: the
operator and reviewer are the same authorized person, with an explicit separate
review step. Enrollment, review screens and downloaded drafts disclose **SELF
REVIEW — no independent second-person approval**. The distinct-person attestation
model remains available and applies to legacy enrollments. Neither model is an
authenticated approver login. Storage-review roles may be held by the responsible
authorized operator; actual computer/backup checks must still be evidenced.

## Prepare and use

1. Close all database users. Install live extension **v4** through the
   [verified backup/restore procedure](live-schema.md). For an already-bound v3
   database, pass the **existing current** ledger and approved copy expiry:

   ```powershell
   python -m pilot_engine.live_migration --database C:\Pilot\data\pilot.sqlite3 --backup C:\Pilot\backup\before-v4.sqlite3 --rehearsal C:\Pilot\rehearsal\before-v4.sqlite3 --retention-ledger C:\Pilot\control\retention.sqlite3 --copies-until '<approved-ISO-expiry>'
   ```

   The two new copies are registered PENDING before creation and become ACTIVE
   only after verified migration. Failure retains registered partial artifacts;
   review/dispose them and retry with new paths. Never recreate a missing ledger.
   Fresh/unbound installations follow the existing retention initialization steps;
   populated upgrades also inventory all prior copies.
2. Open the existing loopback workspace:

   ```powershell
   python -m pilot_engine.live_operator --database C:\Pilot\data\pilot.sqlite3 --retention-ledger C:\Pilot\control\retention.sqlite3
   ```

3. Complete the [authorized intake](authorized-intake.md), including the registered
   reviewer's exact-version evidence for product/classification, price/tax
   authority and customer PO. Select **Canlı Execute** and choose those three
   records by their visible labels. Internal IDs are generated, not entered.
4. Record the manufacturer-provided party, destination, origin, tax, packing and
   document-review facts with their source and permission files, terms, processing
   basis, reviewer and retention end. There are **no invented defaults** for tax,
   fees, discount, weight, origin, addresses or document requirements. Enter an
   explicitly reviewed zero / not-applicable explanation where appropriate.
   The initial scope is HS6 732690, TR origin, DE destination and EUR.
5. Save the local draft. Missing/invalid facts appear in an explicit review list.
   SKU, unit, currency, price, payment terms and Incoterm/place must agree between
   PO and authority. Differences block approval: revise the appropriate source,
   obtain its exact-version approval, then regenerate the local order. This
   release does not implement discretionary price overrides or a PO amendment
   workflow. The quantity × quoted price / price quantity PO total and reviewed tax / charges / discount
   produce the displayed draft total using decimal arithmetic.
6. The named reviewer (the same operator in solo mode) reviews the **entire exact bundle/hash**, including the PO
   differences and checklist. Record decision, reason, method, reference, time and
   confirmation file. The recording OS actor and claimed human are kept separate.
   Approval still leaves every document a DRAFT. Corrections append a new version
   and require a new review. Old versions/decisions remain available for history.
7. Record observed total operator minutes (including review/corrections), the
   correction count and notes, and numeric manual minutes with #13 baseline
   evidence. The app shows the difference; it is not an automatically measured
   productivity claim. One immutable measurement is allowed per review; a
   corrected measurement needs a new review and preserves the previous one.
8. For a download, pre-provision a private export directory using the same OS
   ownership/ACL procedure as the other private directories. It must be outside
   both data and control directories. As the authorized copy administrator,
   record that directory and approved expiry in the form. The ledger supplies the
   named copy owner; expiry cannot exceed input/authorization or control-policy
   retention. A generated `.html` destination is registered **PENDING before any
   bytes leave the app**. Use the browser's **Save As** to that exact shown path.
   The download contains all three printable DRAFT documents, full source /
   permission / input-confirmation provenance and the bundle-review attestation.
9. Click **Kaydettiğim özel dosyayı doğrula**. Only the matching private file/hash
   becomes ACTIVE. The app cannot control a browser's save location: until this
   check succeeds the copy remains PENDING and must be reconciled in the
   retention inventory. Repeating a download must replace only that exact
   registered destination with the same bytes; additional browser, printed,
   emailed or manually copied artifacts require their own copy/retention review.
   No automatic revocation or deletion of an already downloaded file is claimed.

## Invalidation, recovery and erasure

Each save, review approval/retry, measurement, export preparation, download and
export confirmation checks the current external ledger and input eligibility.
A revised, reapproved, rejected, revoked, expired, withdrawn or purged source /
confirmation invalidates the dependent bundle. A new review also invalidates a
previous export's review binding. History remains visible as blocked history;
stale artifacts cannot be downloaded again. Existing external copies require
reviewed disposition through [retention maintenance](evidence-retention.md).

Extension v4 adds `live_order`, immutable `live_order_revision`, normalized
`live_order_input` bindings, `live_order_review`, `live_order_measurement` and
`live_order_export`. All have append-only guards and no legacy foreign keys.
The stable PO intake owns one order; duplicate buyer/PO numbers through another
intake are refused; an existing order cannot be reassigned to a different buyer /
PO identity through a source revision. Retry of an identical current submission returns its existing
record; stale forms cannot overwrite a newer version or decision.

Document-purge plans list affected orders and registered export-copy IDs. Purging
a document blocks dependent outputs but **retains source and derived text**.
Whole-pilot retirement deletes all six new operational tables as well as the
intake/document tables. External copy/control metadata remains under its reviewed
policy. A pre-purge/pre-retirement restore containing denied material is
quarantined against the current independent ledger, including after restart.
Application checks cannot protect against the trusted owner editing both ledgers.

## Verification and real acceptance

`tests/test_live_execute.py` uses explicitly fictional disposable data. It covers
the three-document chain, numeric totals, missing facts and price differences,
exact-version reviews, stale and duplicate submissions, rollback, logical-PO
duplicates, role/synthetic isolation, expiry/withdrawal, source/facts/permission /
review-proof purge, full retirement, restore quarantine, registered downloads,
wrong-file confirmation, disposal, restart, authenticated HTTP forms and bound v3
upgrade with verified recovery copies. CI runs it on Linux and native Windows.

For #68, repeat the chain with **one authorized real manufacturer PO** on the
actual Windows workstation with IT present, record release and input/output
hashes privately, have the registered reviewer confirm under the recorded model, and capture manual vs observed
minutes plus correction/rework counts. Include source invalidation, export-copy
reconciliation/disposition and a separate-path restore check. Keep #75 open until
that evidence exists. No actual manufacturer evidence belongs in GitHub fixtures.

## Price quoted per 100 / 1000 units

The local service compares prices using both quoted value and its price quantity.
Equivalent per-piece/per-hundred/per-thousand authorities match exactly. Draft
invoices retain the original quoted value and explicitly show a non-unit price
quantity and calculation. They do not mislabel a per-100 quote as a piece price.
Legacy sources without `price_unit` retain basis 1 and unchanged artifact bytes;
there is no schema migration or rewrite of previous approvals. Conditional payment
discounts remain reviewed facts and are not inferred from the PO's terms.
