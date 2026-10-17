# Reviewed evidence retention and purge (#74)

Supported offline maintenance is available. **Actual personal-data intake remains
blocked on #13/#68:** manufacturer/IT encryption/access review and a qualified
retention/privacy/hold decision have not been supplied. `--reviewed` records an
administrator's attestation; it cannot verify those external facts or determine
legal retention rules. No disk encryption is enabled or certified by this tool.

## Scope and remaining data

| Operation | Removes from the maintained main DB | Retains |
| --- | --- | --- |
| DOCUMENT | Selected BLOB content, including all shared uses of that document | Inert `PURGED` bytes; original hash, ID, actor/time; all source/payload/approval text and minimal purge receipts |
| PILOT | All operational live document, authorization, source, intake, confirmation, stop, order, draft/input, review, measurement and export-binding rows for the sole pilot | Legacy synthetic rows/schema; live schema versions, control binding and minimal purge receipts; external control/copy metadata |
| Registered copy disposal | The selected registered file through normal unlink | Disposition receipt, path/hash/owner/expiry in the external control ledger; other copies remain untouched |

**A document purge is not erasure of names in the surrounding records.** The plan
reports retained text and every shared authorization/source/confirmation use.
Use reviewed PILOT retirement when the required scope includes those live fields.
There is no selective personal-field redaction in this release. Retained IDs,
hashes, OS actors, backup-owner names and paths can themselves be identifying;
the qualified policy review must cover their necessity, access and retention.
No anonymity claim is made. Legacy tables are retained and must remain synthetic.
[Live Execute v4](live-execute.md) extends retirement to all six order/draft tables.
Document plans include affected orders and export-copy IDs. Derived draft text
remains after DOCUMENT purge; PILOT retirement removes it. Registered external
exports remain subject to separate reviewed disposition.

SQLite `secure_delete`, WAL checkpoint/truncation, rollback-journal handling and
VACUUM are used to remove selected bytes from the current logical DB and compact
it. This is **not forensic erasure** of SSD blocks, OS/pagefile/crash snapshots,
cloud sync, unmanaged backups, clipboard/browser/download copies or an already
downloaded document. IT must address the actual encrypted media, key custody and
all copies. A tombstone by itself is not deletion of original bytes.

## One-time setup and upgrade

1. Stop the operator app and all database users. Upgrade through
   `python -m pilot_engine.live_migration` with new private backup/rehearsal paths
   to live extension **v4**. V1/v2/v3 upgrade preserves all existing rows and schema.
   For an already-bound v3 upgrade, add the existing `--retention-ledger` and
   approved `--copies-until`; new migration copies are registered before creation.
   Do not initialize another control ledger after upgrading a bound database.
2. Provision a private control directory outside the database directory and
   outside recovery/export directories, under the same accepted OS account.
   Maintain a current external retention ledger there. Ordinary DB backups must
   never include/replace that ledger as part of a database rollback.
3. Obtain a named backup owner, approved expiry/hold/deletion rules, a permitted
   minimal-metadata retention end, and IT evidence for workstation/backup
   encryption, access and recovery-key custody. Use reference identifiers without
   names/free-text personal data; the ledger stores reference hashes and actor/time.
4. Initialize and bind the ledger. Example PowerShell (replace all placeholders
   with the actual approved private paths, people, references and timezone dates):

   ```powershell
   $retentionArgs = @('-m', 'pilot_engine.live_retention', '--database', 'C:\Pilot\data\pilot.sqlite3', '--ledger', 'C:\Pilot\control\retention.sqlite3')
   python @retentionArgs init --owner '<approved-owner>' --metadata-until '<approved-ISO-expiry>' --policy-ref '<reviewed-retention-and-hold-ref>' --storage-ref '<IT-encryption-access-key-review-ref>' --reviewed
   ```

   A populated v2 upgrade also requires `--existing-copy <path>` for each reviewed
   pre-upgrade backup/rehearsal/export copy and `--copies-until <approved-ISO-expiry>`.
   Copies cannot outlive the reviewed retention of their control metadata.
   Inventory every existing copy, not only the two most recent files. Initialization
   refuses an already-bound DB; never create a fresh ledger to bypass lost history.
   If setup was interrupted, use `review`, `register-copy`, then `bind` with the
   existing ledger after resolving the recorded failure; do not erase/reinitialize it.
5. Start intake with both paths:

   ```powershell
   python -m pilot_engine.live_operator --database C:\Pilot\data\pilot.sqlite3 --retention-ledger C:\Pilot\control\retention.sqlite3
   ```

The app now requires this exact binding and a current policy. Missing/wrong
ledger, expired policy, pending maintenance or replayed retired evidence blocks
use. Use `review` with the same review arguments to record a newly approved policy.
Expiry never silently deletes the control history. Resolving its eventual
retention/deletion, recovery and availability is a manufacturer/qualified-review
decision; do not delete the deny history while copies could still be restored.

## Plan, apply and resume

1. Close the app. As the authorized administrator, run `python @retentionArgs inventory`.
   It reports document IDs/hashes/lengths, pilot IDs, live text column names,
   pending operations, copy paths/owners/expiry/state and the current policy. It
   does not print source bytes or payload/approval text. Output is private: owner
   names and paths may still be personal information; do not paste it into GitHub.
2. Run `python @retentionArgs plan --kind DOCUMENT --target <document-id>` or
   `--kind PILOT --target <sole-pilot-id>`. Review target hashes, all affected/shared
   IDs and row counts, retained metadata/text, copies and applicable holds. A plan
   fingerprint covers the full DB; any intervening data/decision change invalidates it.
3. Record the reviewed decision using
   `python @retentionArgs begin --kind DOCUMENT --target <document-id> --plan-sha256 <reviewed-plan-hash> --policy-ref <current-reviewed-policy-ref> --reviewed`.
   Substitute PILOT for retirement. The independent ledger durably records the
   intent and denied document hashes/pilot ID **before** changing main data.
4. Run `python @retentionArgs resume --operation <returned-operation-id>`.
   This checks the target, performs the narrowly scoped maintenance mutation in
   one DB writer transaction, restores/compares every original guard, checks
   foreign keys/integrity and unchanged legacy data, then compacts/checkpoints the
   DB. Users never edit triggers or SQL manually. Normal append-only behavior
   remains unchanged outside this supported administrator maintenance transaction.
5. Review COMPLETE plus the current inventory and the UI's blocked/removed
   evidence state before resuming work. Repeating a completed operation verifies
   that retired content has not been restored. Interrupted mutation rolls back;
   interrupted post-commit compaction leaves the durable operation PENDING and
   intake blocked. Resolve the failure and rerun `resume` with the same ID.

The control ledger deliberately uses durable DELETE journal mode: its exclusive
intent transaction drains existing intake leases, then pending state blocks new
ones. Intake holds a control read lease for its whole DB transaction and checks
denied evidence before commit. Do not change the control ledger to WAL mode.
This is local trusted-owner coordination, not a service or hostile-owner boundary.

## Copies and restored data

- Use `python @retentionArgs backup --backup <new-private-path> --rehearsal <separate-new-private-path> --expires-at <approved-ISO-expiry>` for subsequent live backups.
  Both files are registered with owner/expiry before creation; full SQLite backup,
  restore and complete legacy/live fingerprint/integrity checks precede ACTIVE
  state and checksums. A failure retains PENDING files/records for inspection.
- Use `register-copy --copy <existing-private-path> --expires-at <approved-ISO-expiry> --kind BACKUP` (or REHEARSAL/EXPORT) for existing copies/exports. The ledger cannot discover unmanaged OS/cloud backups or downloads; IT's copy inventory is still required.
- Run `verify-restore --copy <quarantined-private-copy>` against the **current**
  external ledger before any promotion. Old documents/pilots erased since the
  backup are rejected; the app independently checks this on every transaction.
  The check does not authorize activation, restore missing later decisions or
  overwrite the source. Select a current reconciled copy; do not promote a rejected
  one, run an older app against it or reset the ledger to make it pass.
- Use `dispose-copy --copy-id <registered-id> --sha256 <reviewed-current-file-hash> --disposition-ref <approved-disposition-ref> --reviewed` for reviewed expiry/hold/recovery disposition. This never automatically destroys the last recovery copy; the reviewer must establish the replacement/recovery decision first. Changed hashes or live SQLite sidecars block deletion. DISPOSING intent allows safe retry if interrupted after unlink. Normal file unlink is not forensic erasure.
- Expiry is visible in inventory; automatic deletion is not performed. Purging the
  main DB does not delete its backups. Until dispositions are completed, encrypted
  pre-purge copies still contain the original data and require restricted access.

Protect and maintain the external ledger under IT's separate recovery procedure.
If it is missing/damaged, keep intake stopped until the **current** history is
recovered and reviewed. The app cannot detect that a trusted owner replaced both
the DB and ledger with mutually consistent old versions; it cannot revoke bytes
from an unmanaged copy. These limits must remain explicit in #13/#68 acceptance.

## Evidence

Tests use fictional disposable data. They exercise shared-document purge,
complete pilot retirement, retained-text disclosure, revoked/blocked downloads,
re-upload and old-copy quarantine, v2 upgrade, wrong/stale plans, admin denial,
active-transaction exclusion, guard/legacy preservation, rollback and interrupted
compaction/disposal retry, copy metadata and policy expiry, CLI commands, and
POSIX main-WAL handling. Native Windows CI runs the same normal-mode scenarios;
actual machine encryption, privacy review and acceptance are not claimed.
