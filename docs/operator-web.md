# Visual operator workspace (#47)

The local visual workspace starts from a **prepared synthetic Find handoff**.
The operator opens a case in a browser and follows one screen at a time through
Sell and Execute. It supplies labeled forms and buttons; the operator does not
edit JSON, copy IDs, use Python APIs or run a command for each action. An
administrator starts the local process once on a POSIX host:

```bash
export EGO_ENV=development
export EGO_DATA_DIR=/path/to/owner-only-directory
python -m pilot_engine.operator_web
```

`EGO_DATA_DIR` must already exist, be owned by the OS account and have mode
`0700`. The process prints and opens a one-time local URL. It binds only to
`127.0.0.1`, creates an unguessable session cookie, checks a per-process CSRF
token and Origin on writes, and does not log form contents. Stop it with Ctrl-C.
`EGO_ENV=pilot` is refused until #42 authorizes live-data mode. The operator
can click **Örnek vakayı başlat** to create one wholly invented product, buyer,
route and Find handoff. An existing case may also be selected from the list;
real product and buyer setup remains outside this synthetic visual walkthrough.

The guided path covers a current Find handoff, an opportunity and outreach
draft, exact envelope review, an in-memory mock capture, an operator-entered
synthetic inbound message, RFQ, price evidence, quotation, PO, local order,
manual ERP handoff, readiness, freight request and three reviewed document
drafts. The sidebar shows source references, actors and current statuses.
Each write needs a visible confirmation checkbox and still calls the existing
service, which checks permissions, exact revision, source state and suppression.
File uploads for price and PO are hashed by those services. The invented inbound
text is kept in an owner-only local evidence file so the reviewer can see what
they are classifying. No real send, ERP creation, carrier booking or issued
document is performed. `CAPTURED_NOT_DELIVERED` remains a mock result.

After a technically complete case, the screen asks for friction and improvement
notes and appends them to owner-only `operator-feedback.jsonl`. A non-developer
walkthrough and its actual feedback are still required before #47 is closed.
The current guided screen handles one synthetic email route and one package;
it stops and displays unresolved issues when evidence changes or a PO differs.
Correction/amendment flows and partial shipments remain separate work (#44).
