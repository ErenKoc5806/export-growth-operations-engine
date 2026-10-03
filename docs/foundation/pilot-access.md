# FND-006 — Single-operator pilot access

## First deployment choice

The initial deployment is a **single POSIX host, one interactive operator OS account**, with no web endpoint and no shared database access. Login to that host is the authentication step. `PilotStore` derives `uid:<effective UID>` from the operating system rather than accepting an actor name from a request. The database and backup files must be regular files owned by that UID with mode `0600`; opening a more broadly accessible file or a symlink fails. Put the database in a private `0700` directory, do not commit it, and use an encrypted host/backup volume for real contacts.

`LocalAccess.single_operator()` maps the process UID to `ADMIN` for this one-person deployment. A trusted launcher may pass an explicit `LocalAccess` policy to restrict the process further. Do not accept role mappings from request payloads. Python code executing as the database owner can open SQLite directly, so this is **not** a sandbox for untrusted plugins or several users sharing a process. Multi-user deployment needs a service that owns the database, authenticates each session, and enforces roles server-side; that is a later milestone.

## Roles and actions

| Role | Summary | Contact route | Edit quote / advance state | Approve quote or PO | Backup/configuration |
| --- | --- | --- | --- | --- | --- |
| Viewer | Yes | No | No | No | No |
| Operator | Yes | Yes | Yes | Yes | No |
| Administrator | Yes | Yes | Yes | Yes | Backup and host configuration |
| Service | Yes | No | No | No | No |

The first deployment uses one administrator who also acts as operator. `SERVICE` is a placeholder for a future separate process and has no approval permission. No outreach delivery or live approval endpoint exists; an outreach permission must be checked at the eventual send boundary. A hypothetical `APPROVE_OUTREACH` row in SQLite alone is never permission to deliver. Do not provide a generic API that lets a caller specify an arbitrary actor ID.

## Enforcement and log

The public store methods check permissions before reading summaries/contacts, ingesting a synthetic case, editing quotations or backing up. `record_approval` and `transition` log the access decision and perform the authorized write in the same transaction. A denial is committed to `access_decision` and then raises `PermissionError`. The append-only access log stores UID, permission, opaque target ID, decision and UTC time; no email, contact route or document payload. `audit_event` separately records successful business decisions. A validation failure after an allowed check can roll back that attempted decision; the immutable business state is unchanged.

The actor recorded on an approval, synthetic ingestion audit event, and opportunity owner is the OS UID. The fixture's `operator_id` and `approved_by` text in a quotation or PO are historical source content; they are not proof of authentication. Quote and PO approvals use separate permissions and are bound to the target revision/PO by FND-005 checks. Production authorization must come from a trusted launcher plus OS file ownership, not an arbitrary `LocalAccess` object supplied by remote input.

## Future isolation

Before a second user or manufacturer is admitted, introduce an authenticated service/session boundary, server-side role policy, tenant ID on all canonical records and queries, per-tenant source and document access, and tests for cross-tenant denial. SQLite file permission does not isolate tenants within a shared process. At that point reassess storage and deployment topology.
