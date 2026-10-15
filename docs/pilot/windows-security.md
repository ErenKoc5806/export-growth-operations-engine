# Windows security groundwork (#65)

This implements native identity and **read-only security preflight**, not a live
pilot launcher. Windows pilot mode, real records, credentials, external sends and
the existing POSIX database/secret checks remain gated. Do not use the synthetic
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
replacement/ACL changes. The probe is not wired into live I/O. Runtime work must
keep resource creation/opening and permissions safe against replacement and
recheck the actual handles used for I/O. A data owner or host administrator can
still bypass application roles by editing SQLite. Real separate-account approval
requires an authenticated service that owns the data; handing both accounts a
writable database is not isolation.

## Validation and remaining acceptance

The Windows CI job runs `test_windows_security.py` on the actual Windows APIs:
TokenUser versus forged environment labels; private inherited file ACLs; SQLite
backup/restore with an integrity check; invented upload/secret resources; broad
file/directory grants; hard links and directory junctions. Portable tests cover
unknown/unsupported ACLs and role separation; HTTP tests cover actor changes and
failed identity resolution. CI uses invented data and no real credentials.

Before closing #65, record a trial on the intended Windows workstation and its
filesystem/IT policy, an independently verified operator/approver roster (#13),
and the service/session and race-safe resource I/O implementation. #66 owns the
versioned live schema and pre-migration restore rehearsal; this fixture backup is
not that rehearsal. #67/#68 and #38 still gate real data and external actions.

References: [TokenUser](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-gettokeninformation),
[handle security descriptors](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-getsecurityinfo),
[file access](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights).
