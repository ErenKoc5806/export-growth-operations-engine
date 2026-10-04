# Solo development guide

Use Python 3.12 or newer. From the main repository, run:

```bash
python -W error::ResourceWarning -m unittest discover -s tests -v
python -m compileall -q pilot_engine tests
python tools/check_style.py
python -m pilot_engine
```

The first command checks contracts and synthetic acceptance; CI additionally scans its captured output for `ResourceWarning:` because finalizer warnings can otherwise leave a zero exit code. Compilation checks syntax; the small style check rejects tabs and trailing whitespace. Type hints are part of public pilot interfaces, but a static type checker is not yet a CI gate. Add one when adapter shapes stabilize, and keep provider-specific tests near each adapter. Never use real contacts, credentials or orders in fixtures.

Numbered SQL migrations must begin with `BEGIN IMMEDIATE;` and end with `COMMIT;`. The store strips those wrappers and executes complete statements under one writer transaction after checking `user_version`. Add each new filename to `MIGRATION_FILES` in order, back up a real pilot database before upgrading, and test both a fresh database and upgrade from the previous version.

Create a short branch per issue, include a test for behavior changes and update the relevant design note. Open a PR with the issue link, verify GitHub Actions, then merge. The main branch is the working baseline; tag a version only after its acceptance evidence is recorded. Review changes to approval, pricing, external action and migrations especially carefully. At the end of Foundation, request the agreed second-opinion Claude.ai review of the current codebase.

The two components are pinned submodules. Do not copy their generated databases or virtual environments into this product. For a deliberate update, check out a reviewed commit in the component's own repository, advance its pointer in the main repo and record the SHA, contract changes and tests in a separate PR. `git submodule update --init --recursive` restores pinned commits. Both components currently use a top-level Python package called `app`, so do not install them together in one interpreter.
