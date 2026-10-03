# FND-010 — Failure and retry policy

| Failure or action | Decision |
| --- | --- |
| Invalid product, contact evidence, amount, authorization or state | Stop and ask for operator correction. Do not retry the same input. |
| Temporary failure during a **read-only** provider request | Use `ReadRetryPolicy` with at most three attempts by default, capped exponential delays and an explicitly classified `TransientReadError`. Preserve the final failure for review. |
| New approved external write with a stable idempotency key | Record the attempt before calling the provider. A first attempt is allowed only at the future adapter's approval boundary. |
| Timeout, interrupted process or uncertain result after email/ERP write | Mark outcome unknown; look up provider status by attempt/key or reconcile manually. Do not auto-send or auto-create again. |
| Confirmed provider result | Reuse the confirmation; no second action. |
| Provider definitively confirms no action | An operator may review a fresh attempt and its unchanged approval/content. Do not automatically repeat. |

`pilot_engine.retry` implements only bounded read retries and a pure external-attempt decision table. It deliberately contains **no external transport**. The SQLite `action_attempt.idempotency_key` is unique, but provider adapters have not yet been wired to it. The adapter must persist an attempt and immutable target/content, use the same key at the provider where supported, then store the provider reference and result. A process crash between dispatch and response is `UNKNOWN` until reconciled. A stable local order ID and PO uniqueness protect local order creation; they do not prove whether an ERP call succeeded.

During a failure, use the correlation ID in the operational log to inspect `audit_event`, `approval` and `action_attempt`. Compare the exact recipient/content hash or PO revision and the provider's result. If provider lookup is impossible, leave the item for manual review. Record the operator's decision before a fresh attempt. Do not infer delivery from a successful local log line. Connector-specific classification, timeouts, rate limits and end-to-end fault tests belong to the Find/Sell/Execute integration issues.
