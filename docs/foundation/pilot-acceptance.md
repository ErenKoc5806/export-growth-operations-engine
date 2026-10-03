# FND-011 — Pilot acceptance strategy

The first deterministic technical slice uses the user-selected HS6 research filter `732690`, Germany (`DE`) and the user-named product **bağlantı kelepçesi (connection clamp)**. The SKU, specifications, distributor, operator and amounts in the fixture are **entirely synthetic**. Its tariff classification and actual application are unverified. `exhaust clamp` and `pipe clamp` are candidate search phrases; they require product-fit checks before a buyer is accepted. The example contact is not a real buyer.

Run locally from the parent repository:

```bash
python -m pilot_engine
python -m unittest discover -s tests -v
```

The pure workflow is a contract example, not an operating export system. It consumes one linked opportunity and returns a draft sales order, commercial invoice, packing list and three trace events. It performs no network calls, sends no mail, books no transport, writes no database and creates no legal invoice. A failed validation raises `PilotValidationError` without producing those outputs. `pilot_engine.acceptance` then exercises that same synthetic record with an in-memory mailbox and ERP export capture plus SQLite persistence. The approval is explicitly marked synthetic; it is not a real authorization or proof of delivery.

| Layer | Acceptance evidence | Later integration work |
| --- | --- | --- |
| Unit / contract | HS6 and target, source-backed contact, approval and RFQ/quotation/PO link validation; amount and document reconciliation | Replace fixture with a real product profile after owner approval. |
| Component integration | Trade Intelligence response → canonical product/contact record, including source and timestamp; selected AI-Worker PO parser/equivalence tests | Implement adapters, preserve original source references and distinguish actual contacts from synthetic records. |
| End-to-end synthetic | One 732690/DE case verifies evidence and commercial links, exact mock send approval, single mock send/order on replay, unknown-delivery block, persistent order and two linked document drafts | Connect real component adapters, complete live state transitions and operator UI in functional epics; then controlled live checks. |

The `.github/workflows/pilot-tests.yml` workflow runs the current contract tests on pushes and pull requests that touch the slice. CI being configured is distinct from observing a successful GitHub Actions run. For live pilot readiness, record actual product/SKU, user/operator, source evidence, authorized pricing, test mailbox and ERP permissions in [FND-017](https://github.com/ErenKoc5806/export-growth-operations-engine/issues/1). A real delivery or official invoice requires a separate business process.
