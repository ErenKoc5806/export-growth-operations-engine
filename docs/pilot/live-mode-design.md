# Controlled live-mode design (#42)

Status: design only. The application still refuses live data and real external actions.

## Pilot workstation decision

Target a Windows workstation with a local visual operator app. The operator already
completed the synthetic walkthrough in PowerShell on Windows, and a WSL setup is not
an acceptable operator journey. Do not remove `AppConfig`'s Windows pilot refusal
until a Windows security adapter is implemented and exercised on the actual pilot
machine. The Unix owner/mode check and `getpass.getuser()` fallback are not adequate
live Windows authentication. Use the process token's account SID for the actor and
validate the owner and DACL of the database directory, database, backups, uploads
and secrets. The local web session must be tied to that trusted actor; a shared
Windows account cannot enforce separate operator and approver roles. Confirm the
manufacturer's workstation/IT policy before choosing account provisioning.

The app remains loopback-only during the first controlled pilot. Remote access,
shared accounts and browser access from another computer require a separate
authenticated transport design. No installer or real-data path exists yet.

## Synthetic-only inventory

| Boundary | Current restriction | Required live path |
| --- | --- | --- |
| Schema | Migrations 012, 018, 020 and 021 constrain handoff, inbound, price and PO origins to `SYNTHETIC`. Related tables and payloads carry synthetic status. | New versioned tables or carefully rebuilt CHECK tables with explicit `LIVE` provenance and immutable source references. Never relabel a synthetic row. |
| Find | `find_handoff.py` requires synthetic origin and `example.*` candidate domain; `manual_contact.py` and `sell_drafts.py` require synthetic handoff. | Authorized manufacturer profile, lawful contact source/terms, retention and reviewed buyer/route evidence before handoff. |
| Sell | `inbound.py` requires `SYN-` references; `rfq.py`, `quotations.py` and `sell_delivery.py` require synthetic states, price origin and exact `example.*` recipients. | Separate live intake and provider adapter. #38 owns exact-content approval, named independent approver, provider idempotency, bounce/suppression and uncertain outcomes. |
| Execute | `execute_order.py`, `execute_operations.py`, `execute_documents.py` and `shipments.py` require `SYN-` evidence and synthetic states. | Authorized real PO, price/tax authority and reviewed source documents. ERP, carrier and legal invoice actions remain separate explicit gates. |
| UI/config | `operator_web.py` is synthetic-only; `config.py` refuses Windows pilot and checks Unix file ownership elsewhere. | Windows SID/ACL enforcement, separate named roles and pilot-specific screens. No live operation from the synthetic demo route. |

The inventory should be re-run against all `SYNTHETIC`, `SYN-` and `example.*`
checks before each live adapter is built; no global replacement or environment
flag may bypass them.

## Migration and activation sequence

1. Obtain #13's verified product, classification, pricing authority, people,
   contact-source permission, retention/backup owner and baseline. Record the
   approvals in an access-controlled pilot record, separate from example fixtures.
2. Define a new live namespace/schema with explicit origin on every source and
   decision, unique IDs, foreign-key boundaries and append-only audit. For a
   CHECK-constrained SQLite table, use a staged table rebuild with explicit
   columns and indexes/triggers. Rehearse the exact migration on a copy containing
   legacy synthetic data and verify counts, foreign keys and immutable history.
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

- Windows token-SID actor, ACL checks and role separation; tests on the target
  Windows filesystem and backup/restore path.
- Versioned live schema migration and its rehearsal/rollback tests.
- Dedicated real source intake and controlled mail/provider boundary (#38).
- UI for live provenance and decisions; no synthetic demo defaults or example
  capture labeled as delivery.
- Manufacturer authorization, source terms, approvers and measurement baseline
  from #13. The current code does not satisfy these gates.

Implementation references: [Microsoft file access rights](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights),
[Windows security identifiers](https://learn.microsoft.com/en-us/windows/win32/secauthz/security-identifiers),
and [SQLite ALTER TABLE guidance](https://www.sqlite.org/lang_altertable.html).
