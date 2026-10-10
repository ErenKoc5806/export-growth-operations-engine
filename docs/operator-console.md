# Pilot operator console (#41)

This command-line interface operates the existing **synthetic local** services.
It is available only with `EGO_ENV=development` or `test`; `pilot` is refused
until the reviewed live-data mode in #42 exists. The console has no network
send, ERP creation, carrier booking, document release, or real customer offer.
The current POSIX account is the authenticated actor; the services still check
its permission for every read and action. Set up an owner-only `EGO_DATA_DIR`
as described in [pilot configuration](foundation/pilot-configuration.md).

```bash
export EGO_ENV=development
export EGO_DATA_DIR=/path/to/owner-only-directory
python -m pilot_engine.operator_cli actions
python -m pilot_engine.operator_cli queue handoff
python -m pilot_engine.operator_cli show handoff FH-... 
python -m pilot_engine.operator_cli form draft-create > draft-create.json
```

Edit `draft-create.json`, replacing placeholders with the reviewed handoff ID,
sender and language. Then run:

```bash
python -m pilot_engine.operator_cli act draft-create draft-create.json
python -m pilot_engine.operator_cli show draft SD-...
python -m pilot_engine.operator_cli send-preview SD-... 1
```

For any `act`, the console displays the source record and exact input, then
requires `CONFIRM <action>` typed at the prompt. It does not accept a silent
`--yes`. For send approval it also displays the exact envelope. Copy its
`envelope_sha256` into the `send-approve` form, set `reviewed_claims` only after
human review, and keep the revision number. Edited text is **not** checked for
arbitrary factual claims; the known-phrase warning and checkbox are no
automatic claim validation. `send-capture` only records an in-memory mock
capture, labeled `CAPTURED_NOT_DELIVERED`.

Use `queue` and `show` to discover IDs and inspect statuses, source references,
actor, origin, missing fields, differences and unresolved case issues. Work in
this order: `handoff-record` if there is already an approved synthetic Find
profile, fit decision and route; `opportunity-open`, `draft-create`,
`send-approve`, `send-capture`, `inbound-record`, `inbound-review`, `rfq-save`,
`rfq-decide`, `price-register`, `quote-save`, `quote-decide`, `po-save`,
`po-decide`, `erp-handoff`, `readiness-record`, `freight-plan`, the three
document draft actions, and `document-review`. A phone/form route can use
`manual-plan` and `manual-record` instead of a mock email capture. The
`price-register` form takes `content_file`, and `po-save` takes `original_file`;
the services hash those local synthetic bytes. Do not put credentials or real
customer documents in form files. `show case EO-...` displays the final
technical status and every unresolved item; it does not claim a real shipment.

Record operator observations after the walkthrough with a JSON file containing
`order_id`, `friction` and `suggestion`, then run `feedback input.json`. The
console asks for confirmation and appends the actor and time to the owner-only
`operator-feedback.jsonl` in `EGO_DATA_DIR`. `metric-record` separately records
synthetic operator minutes, corrections and failures in the case summary. Both
are test observations, not a measured baseline or productivity claim.

The end-to-end CLI test starts with a prepared synthetic Find handoff and uses
the console for every Sell and Execute action through three reviewed document
drafts. Product profile, candidate qualification and route evidence remain
the existing Find services; they are not yet editable through this console.
