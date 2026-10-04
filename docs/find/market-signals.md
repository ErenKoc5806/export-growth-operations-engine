# Find #2 — Germany HS6 market signals

Germany (`DE`) is already the selected pilot market. `MarketSignals(PilotStore(path))`
records one immutable aggregate observation with source URL, exact query,
retrieval time, reporting period, units, response digest, provider quality flags
and limitations. The public preview adapter requests Germany's (`276`) annual
**imports** from world (`partnerCode=0`) for the configured HS6 filter. This is
market context for company research, not a lead or proof that a particular
manufacturer's clamp fits that tariff code.

Call `fetch_public_preview(year)` explicitly for a bounded public read. It has
15-second timeout and a 1 MB response limit, requires no API key, rejects
unexpected reporter/partner/flow/commodity/year or multiple rows, and stores
`AVAILABLE`, `MISSING` or `FAILED` with a bounded failure category. It never
generates a buyer or contact. Network failure and missing data leave direct
company research available. `record_manual(...)` preserves an operator's
source URL, observation time, year, copied aggregate value and note without
claiming a provider call. Both methods require the local `RECORD_MARKET` role;
`read(snapshot_id)` needs `READ_MARKET`. Snapshots are append-only.

## Observed public-source check — 2026-10-04

An explicit run of the adapter against the [UN Comtrade public preview](https://comtradeapi.un.org/public/v1/preview/C/A/HS?period=2024&reporterCode=276&cmdCode=732690&flowCode=M&partnerCode=0&maxRecords=500)
returned one `AVAILABLE` row for period **2024**, Germany reporter `276`, import
flow `M`, world partner `0`, HS6 `732690`. Its `primaryValue` was
`4526223107.347` **USD** and `netWgt` was `894843523.447` **kg**. The response
SHA-256 was `42ad1af1fab65d5e28c2418d73307f66ca3bb22600483348ab93927045d75016`;
retrieval was at `2026-10-04T15:21:59+00:00`. Provider flags included
`classificationCode=H6`, `isAggregate=true`, `isReported=false`,
`isNetWgtEstimated=true`, `isQtyEstimated=true`. The API supplied no commodity
description in this response. The figures may change on a later query and
should not be treated as a precise addressable market or as buyer evidence.

UN Comtrade documents its [public preview endpoint and key-free access](https://uncomtrade.org/docs/un-comtrade-api/)
and the [500-row preview limit](https://uncomtrade.org/docs/content-of-data/).
The first pilot uses this as a narrow read boundary; the existing Trade
Intelligence research component remains separate. Later Find work may map a
reviewed Trade Intelligence record into the same snapshot contract, preserving
its upstream ID and source. No real company/contact data or product-fit claim is
introduced here.
