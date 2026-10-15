# Windows security groundwork (#65)

Scope decision, 2026-10-15: stop additional Windows hardening at PRs #69/#70.
#65 closes for this baseline plus the [documented first-pilot limit](trust-model.md).
Service isolation and a custom SQLite I/O boundary are deferred. Target-machine
evidence and manufacturer/IT acceptance remain open in #68/#13; CI is not proof
that the intended workstation is ready.

This implements native identity, security preflight and **handle-checked private
file I/O**, not a live pilot launcher. Windows pilot mode, real records and
external sends remain gated. Do not use the synthetic
Windows storage exception for real data.

## Identity and sessions

`current_principal()` now uses the process token's TokenUser SID on native Windows.
There is no username/environment fallback. Token inspection failures deny access;
threads with an impersonation token are rejected rather than being attributed to
the process owner. Native handles and Windows-allocated SID strings are released.

The local visual session records the launching actor and checks that actor and
READ_SUMMARY permission again when redeeming the URL or using its cookie. This
does **not** identify the Windows account operating a browser: the local HTTP
session remains a bearer token. A copied token used by another browser on the
same machine cannot be distinguished this way. Consequently this is not a live
multi-account approval endpoint.

## Explicit roles

`WindowsAccess` is a separate opt-in policy supplied only by a trusted launcher.
It copies its SID mapping immutably and requires distinct named operator and
approver accounts. Operators cannot approve; approvers can read and approve but
cannot edit or dispatch. Unknown SIDs and permissions are denied. There is no
automatic Windows pilot administrator. The existing synthetic single-operator
administrator is unchanged except for its SID-derived audit identity.

Account SID syntax is not proof of a human identity (domain groups can also have
account SIDs). Manufacturer/IT must verify each configured SID and named role.
Do not accept mappings from form fields, request JSON or untrusted configuration.

## Existing-resource preflight

`inspect_resource` inspects an existing local absolute path. It rejects network
and device paths, alternate streams, parent traversal, reparse points along the
path, wrong owners/types, additional file hard links, null/empty DACLs and
unsupported ACE forms. A handle pins the target while GetSecurityInfo reads its
owner and DACL. Only ordinary allow/deny ACEs are supported; allow ACEs must name
explicit resource trustees or SYSTEM/local Administrators. Even inherit-only
grants to other trustees fail. This deliberately rejects some safe but more
complex Windows ACLs rather than approximating effective access.

Inspect the data, backup, upload and secret directories individually, and every
database, WAL/SHM sidecar, backup, restored file, upload and secret file separately.
Each resource may use a narrower trustee set; an approver need not read secrets.
The probe never changes an ACL, reads file contents or creates a credential.

**A successful probe is only a snapshot.** Ancestor permissions are not an
effective-access proof, and closing the inspected handle permits subsequent
replacement/ACL changes. The standalone probe is not a live I/O gate. A data owner
or host administrator can bypass application roles by editing SQLite. Real
separate-account isolation would require a service that owns the data; that is
deferred, not claimed by this pilot. #66 owns practical private backup/restore
controls, and #67 records human review under the accepted trust model.

## Runtime private files and secrets

`WindowsPrivateFiles` adds bounded reads and create-only artifact writes in an
existing IT-provisioned private directory. Each operation derives the owner SID
from the process token again, opens and holds every directory component without
delete sharing, rejects reparse points, and validates the private root's owner
and DACL. The leaf is opened without data sharing; its owner/DACL, disk type and
hard-link count are checked on that handle. ReadFile/WriteFile use the same handle
while the directory chain remains pinned. No checked path or cached preflight
result is reopened for content I/O. Device names, alternate streams and traversal
are rejected before opening the leaf.

New artifacts use CREATE_NEW with an explicit owner and protected DACL from the
first instant of creation; inherited broad permissions and elevated-account
default ownership are not used. Existing names are never truncated or replaced.
Writes are flushed; only a successful return can be treated as persisted evidence.
A failed write may leave an incomplete private file requiring reconciliation;
this is not an atomic publication or application-level idempotency protocol.

`SecretStore` uses this reader natively on Windows with the same name, size,
single-line UTF-8 validation and SecretValue redaction as POSIX. Secrets must
remain outside the source tree. Every get reopens and rechecks permissions;
owner-controlled replacement between requests rotates the value. No credential
is provisioned and no provider is called. The synthetic Windows AppConfig path
still rejects EGO_SECRET_DIR; future service code can use SecretStore directly.

This protects these content operations against concurrent path replacement and
data writes under Windows sharing semantics. The owner, SYSTEM and local
administrators remain trusted: they can change ACLs, bypass the application or
read process memory. This is not a SQLite VFS. Consistent backups remain in #66;
service-owned storage and stronger isolation are outside the first-pilot scope.

## Validation and remaining acceptance

The Windows CI job runs `test_windows_security.py` on the actual Windows APIs:
TokenUser versus forged environment labels; private inherited file ACLs; SQLite
backup/restore with an integrity check; invented upload/secret resources; broad
file/directory grants; hard links and directory junctions. Portable tests cover
unknown/unsupported ACLs and role separation; HTTP tests cover actor changes and
failed identity resolution. CI uses invented data and no real credentials.

`test_windows_files.py` exercises actual handle-checked reads/writes and the
Windows SecretStore: replacement/write attempts between ACL check and read,
directory rename while pinned, no-overwrite creation with explicit ownership,
permission rechecks, rotation, malformed/oversized secrets and handle cleanup.

#65 closes for the baseline and documented limit, without certifying a real
Windows installation. #68 must record the intended workstation/filesystem trial
and IT review; #13 must record named people and manufacturer authorization.
#66 owns versioned live schema and pre-migration restore rehearsal; the CI fixture
backup is not that rehearsal. #67/#68 and #38 still gate real use.

References: [TokenUser](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-gettokeninformation),
[handle security descriptors](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-getsecurityinfo),
[file access](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights).
