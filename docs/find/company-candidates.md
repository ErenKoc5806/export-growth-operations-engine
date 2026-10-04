# Find #3 — candidate company intake

`CandidateDiscovery(PilotStore(path))` stores potential German buyer companies
with a stable ID and append-only source evidence. `record(...)` accepts an
operator's official-page observation; `import_ti_candidate(...)` maps a Trade
Intelligence `CompanyCandidate` buyer result without inheriting its confidence
or qualification gate. Both need a source reference, query, observation time,
company name, country, role hypothesis, site/domain when known, evidence URLs
and summary. Only HTTPS evidence is accepted. Rerunning the same source is
idempotent if unchanged and fails visibly if the same source reference changes.
The same country/domain shares a company identity; name variants are aliases.
Without a site, an exact name match is flagged as a possible duplicate for
human review rather than silently merged.

Every record remains `UNQUALIFIED` with `product_fit_verified=false`,
`contact_verified=false` and `outreach_allowed=false`. An official category
page supports a **candidate hypothesis**, not proof that the company buys the
manufacturer's SKU. Product match and buyer role are decided in #4; contact
research and legal/privacy decisions follow in #5–6 and [pilot readiness](https://github.com/ErenKoc5806/export-growth-operations-engine/issues/13).
Aggregate HS6 statistics from #2 cannot be submitted as company evidence.
The main product does not run Trade Intelligence's separate `app` package in
its process, and this feature does not scrape contacts or send messages.

## Read-only source check — 2026-10-04

The [Berner Deutschland official product category](https://shop.berner.eu/de-de/dc/43590861-schellen/)
lists hose and pipe clamp products. A manual `record` call produced one German
candidate under `shop.berner.eu` with source ref `BERNER-DE-2026-10-04` and a
`DISTRIBUTOR` hypothesis. The record explicitly remains **unqualified**:
neither the intended connection-clamp SKU nor Berner's procurement interest is
known. It contains no personal contact route. The observed company page can
change; an operator should refresh the evidence before qualification.

The example database used for this check was temporary. A real pilot data
directory and source-use/retention decision are still required before keeping
production research records or doing outreach.
