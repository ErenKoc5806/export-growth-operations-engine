# FND-016 — Foundation decisions

1. **Product boundary:** one main application owns Find → Sell → Execute state and human approvals. Trade Intelligence provides research through a separate boundary; AI-Worker is selectively reused after import/test repair. See [pilot architecture](pilot-architecture.md) and [component audit](component-audit.md).
2. **Storage:** local SQLite with forward migrations and append-only quotation/audit records fits one operator and one manufacturer. TI retains its research store. See [persistence](pilot-persistence.md).
3. **Identity and deployment:** one POSIX account owns private local files; no web login or multi-user claim. See [access](pilot-access.md) and [security](security-baseline.md).
4. **External actions:** exact human approval, stable attempt keys and reconciliation before an uncertain repeat. Mock mail/ERP are test boundaries, not integrations. See [integration contracts](integration-contracts.md), [retry](pilot-retry.md) and [acceptance](pilot-acceptance.md).
5. **Scope:** 732690 is a research filter, Germany the first target and connection clamp the named product. The true SKU/application/classification and commercial owner remain open in [FND-017](https://github.com/ErenKoc5806/export-growth-operations-engine/issues/1).

Alternatives deferred: one combined Python package (component `app` collision), full service orchestration, multi-tenant database, automatic ERP/transport execution and real customer outreach before approval. Revisit these decisions with evidence from the controlled pilot rather than treating this Foundation as production readiness.
