# Sell #15 — inbound RFQ review

`SellRFQ.save` starts from an inbound message whose latest operator review is
`RFQ_CANDIDATE`. A mere `INTEREST` message cannot become an RFQ. Each revision
stores the exact extracted customer reference, SKU/specification, quantity,
unit, destination, requested terms and optional response deadline, linked to
its source review and raw reference. Missing required fields remain visible on
a draft. Editing appends a complete new revision.

An administrator may accept only the latest, complete synthetic revision
after checking its SKU, unit and corridor against the currently approved
manufacturer profile. A correction to the inbound classification or a revoked
product makes the accepted RFQ require re-review. Revocation appends a decision
without removing history. Only `ACCEPTED_SYNTHETIC` may feed the technical
quotation step; it is never represented as an actual customer request.

Real RFQ intake requires a verifiable inbound message and pilot readiness.
The fixture demonstrates the data and review contract, not a live buyer order.
