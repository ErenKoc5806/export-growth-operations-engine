# Controlled live-mode design (#42)

Status: design only. The application still refuses live data and real external actions.

Native SID identity, local process/session binding, ACL preflight and private-file
I/O are implemented in the [Windows groundwork](windows-security.md). Further
hardening/service isolation is deferred under the [first-pilot trust model](trust-model.md).
#65 closes for that baseline; #13/#68 still require manufacturer/IT acceptance and
actual target-machine evidence. [#66 maintenance tooling](live-schema.md) adds an
isolated live schema and verified restore. [#67 source intake](authorized-intake.md)
now has a separate gated workspace; real-sample acceptance still needs #13.

## Pilot workstation decision

Target a Windows workstation with a local visual operator app. The operator already
completed the synthetic walkthrough in PowerShell on Windows, and a WSL setup is not
an acceptable operator journey. Keep `AppConfig`'s Windows pilot refusal until
#66/#67 implementation and #68's target-machine rehearsal satisfy the activation
procedure. Use the process SID as the recording OS actor, ordinary private-file
controls and one named human approver's recorded confirmation. This does not
authenticate the browser user or isolate the database owner. Manufacturer/IT must
accept that limit in #13; separate OS accounts and a service are not first-pilot
prerequisites. Do not infer approval from an actor label.

The app remains loopback-only during the first controlled pilot. Remote access,
untrusted users or another manufacturer require a separate authenticated service
design. The local account arrangement is subject to manufacturer/IT approval.
No installer or real-data path exists yet.

## Synthetic-only inventory

| Boundary | Current restriction | Required live path |
| --- | --- | --- |
| Schema | Migrations 012, 018, 020 and 021 constrain handoff, inbound, price and PO origins to `SYNTHETIC`. Related tables and payloads carry synthetic status. | New versioned tables or carefully rebuilt CHECK tables with explicit `LIVE` provenance and immutable source references. Never relabel a synthetic row. |
| Find | `find_handoff.py` requires synthetic origin and `example.*` candidate domain; `manual_contact.py` and `sell_drafts.py` require synthetic handoff. | Authorized manufacturer profile, lawful contact source/terms, retention and reviewed buyer/route evidence before handoff. |
| Sell | `inbound.py` requires `SYN-` references; `rfq.py`, `quotations.py` and `sell_delivery.py` require synthetic states, price origin and exact `example.*` recipients. | Separate live intake and provider adapter. #38 owns exact-content approval, named independent approver, provider idempotency, bounce/suppression and uncertain outcomes. |
| Execute | `execute_order.py`, `execute_operations.py`, `execute_documents.py` and `shipments.py` require `SYN-` evidence and synthetic states. | Authorized real PO, price/tax authority and reviewed source documents. ERP, carrier and legal invoice actions remain separate explicit gates. |
| UI/config | `operator_web.py` is synthetic-only; `config.py` refuses Windows pilot and checks Unix file ownership elsewhere. | Private local storage, named operator/approver with recorded human confirmation, and live provenance screens under the accepted trust model. No live operation from the synthetic demo route. |

The inventory should be re-run against all `SYNTHETIC`, `SYN-` and `example.*`
checks before each live adapter is built; no global replacement or environment
flag may bypass them.

## Migration and activation sequence

1. Obtain #13's verified product, classification, pricing authority, people,
   contact-source permission, retention/backup owner and baseline. Record the
   approvals in an access-controlled pilot record, separate from example fixtures.
2. Install the additive [live extension v2](live-schema.md) with explicit origin,
   unique IDs, foreign-key boundaries and append-only records. Keep legacy
   `user_version=29` and its synthetic CHECK constraints intact. No legacy table
   rebuild is needed. Rehearse on a complete legacy database copy and verify
   counts, foreign keys and immutable history.
3. Stop writers. Take a consistent SQLite backup to a separate, access-controlled
   path **before** the migration; verify its integrity and record a checksum.
   A failed backup or permission check blocks activation. Apply the migration in
   one transaction, then run `integrity_check`, `foreign_key_check` and source
   counts before allowing a live record.
4. Validate an authorized, non-contact sample in the isolated live namespace.
   Negative checks must prove that synthetic IDs/evidence cannot authorize live
   decisions, missing roles and source permissions block writes, and the mock
   mailbox/ERP/carrier cannot become a real side effect by configuration alone.
5. Activate each live source and side effect separately after #38's provider
   sandbox evidence and the named manufacturer approvers sign the procedure.
   Record operator, time, scope, version and backup reference. A second person
   reviews the activation result.

Before activation, rollback is restoration of the tested backup while writers
are stopped. After any live event or provider attempt, never silently restore an
older database: reconcile external outcomes, preserve the audit history and
perform a reviewed forward correction. The pilot must have a rehearsed restore
procedure on a separate path before any live data is entered.

## Still required to implement

- Actual target-Windows/filesystem/backup trial and manufacturer/IT review (#68/#13).
- Target-workstation execution of the implemented migration/restore procedure (#68).
- Dedicated real source intake and controlled mail/provider boundary (#38).
- Real manufacturer acceptance of the implemented local source/provenance and
  recorded human confirmation workspace (#67/#13); this does not enable live
  Find/Sell/Execute adapters or provider actions.
- Manufacturer authorization, source terms, approvers and measurement baseline
  from #13. The current code does not satisfy these gates.

Implementation references: [Microsoft file access rights](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights),
[Windows security identifiers](https://learn.microsoft.com/en-us/windows/win32/secauthz/security-identifiers),
and [SQLite ALTER TABLE guidance](https://www.sqlite.org/lang_altertable.html).
