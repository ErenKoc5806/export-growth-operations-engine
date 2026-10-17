# Live schema extension and recovery rehearsal (#66)

Implemented maintenance tooling; **full live operation remains disabled**.
The separate [authorized intake workspace](authorized-intake.md) requires #13
evidence, extension v4 and an external current [retention ledger](evidence-retention.md).
Manufacturer acceptance and actual workstation
activation (#68) remain outstanding. Use the [trusted-owner pilot model](trust-model.md).

## Two schema versions, one database

The existing 29 migrations and `PRAGMA user_version = 29` remain unchanged.
`live_migrations/001_namespace.sql` is an independently versioned, additive
extension. Its first release was v1; `002_intake_evidence.sql` adds v2 evidence,
authorization, source permission and stop records. `003_retention.sql` adds
control binding and minimal purge receipts. `004_execute.sql` adds local live
orders, input bindings, reviewed draft revisions, measurements and export bindings
(see [live Execute](live-execute.md)). Its latest receipt is
`live_schema_migration.version = 4`. The normal
`PilotStore` startup does not install it. Keeping the old CHECK constraints avoids
rebuilding synthetic tables or allowing existing synthetic services to consume
live rows. Never relabel or copy a synthetic record into the live namespace.

| Table | Purpose |
| --- | --- |
| `live_schema_migration` | Extension version, pre-migration backup checksum, legacy fingerprint, OS actor and time |
| `live_pilot` | Authorized manufacturer, authorization reference and named operator/approver |
| `live_source` | Source reference, kind, byte length and SHA-256, explicitly `LIVE` |
| `live_intake` | Stable `LIVE-` identity and intake kind |
| `live_intake_revision` | Immutable revision payload/hash linked to a source in the same pilot |
| `live_confirmation` | Append-only decisions bound to an exact revision/hash; named human and recording OS actor stored separately |

Foreign keys never point from these tables into legacy tables. Live IDs use
`LIVE-`; synthetic source references are rejected. UPDATE and DELETE guards cover
every live table. Confirmation identity is explicitly `OPERATOR_ATTESTATION`,
not proof of an authenticated approver. #67 must validate source permissions,
payloads, source/intake kind agreement, distinct named people and confirmation
evidence before exposing writes. This schema alone authorizes no live workflow,
email, ERP or carrier action. The original Find/Sell/Execute restrictions remain
listed in [the boundary inventory](live-mode-design.md).

## Offline procedure

1. Stop the operator app and other database users. Keep the database, recovery
   directory and separate rehearsal directory on storage private to the current
   OS account. Provision Windows directories using the existing SID/ACL procedure
   in [windows-security.md](windows-security.md); on POSIX use owner-only
   directories/files. This tool does not provision accounts or weaken ACLs.
2. Use the reviewed release on a **copy of the complete legacy application
   database** first. It must already be v29; unknown/older versions fail closed.
   Choose new backup and rehearsal filenames outside the source data directory.
   Example PowerShell invocation, after replacing the paths with approved private
   directories:

   ```powershell
   python -m pilot_engine.live_migration --database C:\Pilot\data\pilot.sqlite3 --backup C:\Pilot\backup\before-live.sqlite3 --rehearsal C:\Pilot\rehearsal\restored.sqlite3
   ```

   When upgrading an already-bound v3 database, also pass `--retention-ledger`
   with the existing current control path and `--copies-until` with an approved
   expiry. The tool registers the new backup/rehearsal before creating either,
   holds the current control lease, and checks its binding/deny policy through
   migration. Failed copies stay PENDING for reviewed reconciliation. A missing
   ledger is a stop condition; never initialize a replacement to bypass history.

3. The tool holds `BEGIN IMMEDIATE` through the complete operation. Another
   connection uses SQLite's backup API, including committed WAL data; copying
   just the main SQLite file is not a valid backup. Both source and backup pass
   `integrity_check` and `foreign_key_check`. The backup must reproduce every
   legacy table's rows, schema objects, counts, application ID and version.
4. Before any live DDL, the tool restores that backup into a separate new SQLite
   file and verifies the same complete legacy fingerprint/counts and integrity.
   Existing live history is included when upgrading v1/v2/v3 to v4. It verifies the
   backup SHA-256 again, then writes a private
   `<backup>.manifest.json` receipt. The receipt contains counts/hashes, not row
   payloads. Keep it with the backup under the approved retention policy. SQLite
   files and the JSON receipt are not encrypted by this tool.
5. The extension and its receipt are applied in one transaction. Integrity,
   foreign keys, version, append-only guards and unchanged legacy and existing
   live records/schema are checked before commit. Output reports `MIGRATED`, the
   two schema versions and backup/fingerprint hashes. A completed retry reports
   `ALREADY_MIGRATED` without creating or replacing recovery artifacts.
6. Record release SHA, paths, output hashes, named operator, date and the result
   of opening the **restored copy** in the synthetic app. Keep the source app
   stopped until the maintenance result has been reviewed. #68 must repeat this
   procedure on the actual Windows workstation/filesystem, with manufacturer IT
   review; a GitHub Windows runner is not that evidence.

Backup, restore, permission or migration failures abort the source transaction.
Existing recovery files/SQLite sidecars are never overwritten; partial files are
retained for inspection. After resolving a failure, retry with new filenames.
A backup exceeding 30 seconds fails rather than keeping the writer lock forever.
Disk errors and an interrupted process require checking artifacts before retry.
The tool must run under the accepted trusted-owner model: a data owner/admin can
still bypass these controls or edit SQLite directly.

## Recovery limits

The tool never restores over the source. Before any live activity, a reviewed
recovery can stop all users, preserve the failed database and all sidecars, verify
the backup checksum against the receipt, and promote the separately verified
restored copy under the approved private permissions. Do not mix old WAL/SHM files
with the replacement. Open and validate that copy before resuming. It contains
the recorded pre-migration state; rerun maintenance with new artifacts if needed.
After retirement/purge, check any restored copy against the **current external**
retention ledger before promotion. A copy containing retired content is rejected.
Subsequent live backups use the registered-copy workflow in
[evidence-retention.md](evidence-retention.md), with explicit owner/expiry/disposition.

After any live event or external attempt, preserve history and reconcile external
outcomes before a reviewed forward correction. Never silently restore an older
database and lose live confirmations or resend an uncertain delivery.

## Automated evidence

`tests/test_live_migration.py` seeds the full v29 application schema with synthetic
Find/profile history, quotation/PO/order data, audit and approval events. It checks
whole-database backup/restore equality, restart, repeated migration, foreign keys,
origin/ID/hash guards, append-only behavior, competing writers, injected backup,
restore and DDL failures, retained artifacts and retry. POSIX additionally checks
committed WAL data. The same suite runs on native Windows with private SID-owned
directories (the POSIX WAL case is skipped there). No real manufacturer data or
target-workstation acceptance is claimed.
