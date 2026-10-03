# FND-015 — Single-host security baseline

The initial trust boundary is one POSIX OS account and a private local data directory. The database owner can bypass application role checks by opening SQLite directly; untrusted plugins and several users in one process are outside this design. A hosted or multi-tenant product needs authenticated service sessions and tenant-scoped queries before admission.

| Risk | Foundation control | Before live pilot |
| --- | --- | --- |
| Unauthorized contact/decision access | OS UID-derived actor, role checks, append-only access decisions, owner-only SQLite/backup files | Verify operator account, private directory, encrypted host/backup storage and restore. |
| Credential exposure | Secret files outside checkout, owner-only permissions, no symlinks, redacted formatting | Provision minimum-scope provider keys, separate by environment and test rotation/revocation. |
| Wrong buyer or product | Source URL/ref/time in contact evidence, explicit pilot scope, quotation/PO matching | Confirm real SKU, material, use, classification and contact relevance with manufacturer. |
| Unapproved or duplicated external action | Revision-bound quote/PO approval, immutable audit, idempotency keys, unknown outcome reconciliation policy | Connect approval to actual authenticated UI and enforce it again at the provider boundary; verify sandbox mail and ERP behavior. |
| Sensitive data in diagnostics | Fixed operational event fields with no payload and separate contact table | Set log/backup retention, inspect provider diagnostics and avoid raw support exports. |

The synthetic acceptance tests cover forged actor denial, contact read permissions, unsafe file modes, secret symlinks, invalid commercial values, duplicate local order and mock delivery uncertainty. CI runs these on each PR. Direct SQLite writes by the owner and real provider behavior are not covered by those tests. Before any real contact collection or outreach, review the lawful basis, notice/retention and source terms with the business owner and appropriate advisers; record the decision and deletion/backups process. No automated outreach or official document issuance is enabled here.
