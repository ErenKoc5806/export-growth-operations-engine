# Export Growth & Operations Engine for Industrial Manufacturers

The product umbrella for an industrial manufacturer's international growth and export operations. The intended workflow is **Find → Sell → Execute**: identify buyers and their source-backed contact details, manage approved sales contact, then create a reviewed local order and shipment-document drafts.

## Current status

Foundation contains a **synthetic** 732690 → Germany connection-clamp acceptance case, local SQLite store and mock mail/ERP boundaries. It is not a live export operation. No real buyer discovery, customer outreach, ERP order or legal invoice is enabled. [Decisions and limitations](docs/foundation/decisions.md) and [acceptance criteria](https://github.com/ErenKoc5806/export-growth-operations-engine/issues/1) describe the boundary.

## Existing components

| Path | Repository | Current role |
| --- | --- | --- |
| `components/ai-worker` | [AI-Worker](https://github.com/ErenKoc5806/AI-Worker) | Candidate PO and operations functions; not integrated |
| `components/trade-intelligence` | [trade_intelligence](https://github.com/ErenKoc5806/trade_intelligence) | Research/contact candidate; not integrated |

These are independent Git submodules pinned to specific commits. Access to both is needed for a full clone:

```bash
git clone --recurse-submodules https://github.com/ErenKoc5806/export-growth-operations-engine.git
```

For an existing clone, run `git submodule update --init --recursive`. The main product's synthetic tests do not require the private submodules.

## Reproduce Foundation locally

Use Python 3.12 or newer. No third-party Python package is required for the main product's current tests.

```bash
python -W error::ResourceWarning -m unittest discover -s tests -v
python -m compileall -q pilot_engine tests
python tools/check_style.py
python -m pilot_engine
```

The last command prints only synthetic draft outputs. To test SQLite persistence, create an owner-only private directory and set `EGO_DATA_DIR` as described in [configuration](docs/foundation/pilot-configuration.md). The synthetic CLI refuses `EGO_ENV=pilot`. No credentials are needed for these tests; [secret setup](docs/foundation/pilot-secrets.md) applies to future adapters. Do not put real contacts in fixtures.

## Planning

[GitHub Project](https://github.com/users/ErenKoc5806/projects/1) tracks Foundation, Find, Sell, Execute, orchestration, production readiness, pilot and commercial milestones. Target: controlled pilot in February–April 2027; first customer and company setup by July 2027. [Contributing guide](CONTRIBUTING.md) covers the solo branch, PR and submodule workflow. [Find product profile](docs/find/product-profile.md) documents the first versioned manufacturer input and its approval boundary.
