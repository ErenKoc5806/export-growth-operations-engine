# FND-008 — Local pilot secrets

No existing synthetic workflow reads a credential or sends an external request. Future adapters can call `AppConfig.open_secrets().get("NAME")` at their request boundary. `EGO_SECRET_DIR` points to an **existing directory outside the repository**, owned by the pilot OS account and mode `0700`. Each named UTF-8 single-line credential is an owner-only regular file (`0600`). Names use uppercase letters, digits and underscores. The reader rejects symlinks, missing files, broad permissions, oversized or malformed values. It reads again for each call, so replacement takes effect on the next request. `SecretValue` masks normal string/repr formatting; adapters must call `.reveal()` only where they authenticate to the provider. This wrapper cannot prevent a caller that already has the secret from printing it.

Provision a credential in the provider with minimum necessary access and save it under a service-specific name in that private directory. Use different directories and provider credentials for development, test and pilot. Keep the directory outside the Git checkout and outside ordinary source or database backups; back up recovery material separately under the same access and encryption controls. To rotate, write a new file privately, atomically replace the old file, verify a request with the new credential, then revoke the old credential with the provider. Never put values in `.env`, fixtures, command arguments, audit logs, PRs or issue text. If a value is exposed, revoke and replace it at the provider.

On Windows, `SecretStore` uses native handle-checked reads in an existing private
directory outside the source tree. The process-token SID must own the directory
and file, with no grants beyond that account, SYSTEM and local Administrators.
Ancestors remain pinned against rename and the exact checked file handle is used
for bounded content reads. See [Windows security](../pilot/windows-security.md).
The synthetic Windows configuration still rejects EGO_SECRET_DIR; this reader is
for the future trusted service boundary and does not activate a provider.

This file source is for one trusted OS account on one host. A multi-user or hosted service requires a managed secret source and authenticated service identity. Reading a secret does not grant permission to send email, change ERP data or issue documents; each connector must enforce its action permission separately.
