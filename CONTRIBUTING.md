# Solo development guide

Use Python 3.12 or newer. From the main repository, run:

```bash
python -W error::ResourceWarning -m unittest discover -s tests -v
python -m compileall -q pilot_engine tests
python tools/check_style.py
python -m pilot_engine
```

The first command checks contracts and synthetic acceptance; compilation checks syntax; the small style check rejects tabs and trailing whitespace. Type hints are part of public pilot interfaces, but a static type checker is not yet a CI gate. Add one when adapter shapes stabilize, and keep provider-specific tests near each adapter. Never use real contacts, credentials or orders in fixtures.

Create a short branch per issue, include a test for behavior changes and update the relevant design note. Open a PR with the issue link, verify GitHub Actions, then merge. The main branch is the working baseline; tag a version only after its acceptance evidence is recorded. Review changes to approval, pricing, external action and migrations especially carefully. At the end of Foundation, request the agreed second-opinion Claude.ai review of the current codebase.

The two components are pinned submodules. Do not copy their generated databases or virtual environments into this product. For a deliberate update, check out a reviewed commit in the component's own repository, advance its pointer in the main repo and record the SHA, contract changes and tests in a separate PR. `git submodule update --init --recursive` restores pinned commits. Both components currently use a top-level Python package called `app`, so do not install them together in one interpreter.
