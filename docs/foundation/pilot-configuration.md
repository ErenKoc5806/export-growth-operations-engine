# FND-007 — Environment and configuration

`AppConfig.from_env` is the single entry point for process settings. It accepts `EGO_ENV=development|test|pilot`, `EGO_HS6`, `EGO_COUNTRY`, `EGO_CURRENCY`, and `EGO_DATA_DIR`. Development and test default to the synthetic 732690/DE/EUR scope. Pilot requires all three scope fields and an existing private data directory; invalid values fail before opening the database. `AppConfig.open_store()` passes the scope to `PilotStore`. The CLI (`python -m pilot_engine`) runs the synthetic example in development/test only and refuses pilot mode.

For a local test, copy the **names and non-secret values** in `.env.example` into shell exports. No `.env` loader is installed. A pilot operator creates a separate directory owned by the OS account with mode `0700`, sets `EGO_DATA_DIR` to its absolute path, and runs only from that account. The SQLite database is created at `pilot.sqlite3` with mode `0600`. Use distinct directories for test and pilot; do not reuse or copy a test database into a real pilot. The one-person POSIX authentication boundary is described in [pilot-access.md](pilot-access.md).

No Comtrade, mail, ERP, or AI credential is consumed by this application yet. `EGO_SECRET_DIR` is an optional local source for future adapters; see [pilot-secrets.md](pilot-secrets.md) for provisioning and rotation. Give each environment separate credentials and scope. `.env` variants, database files and backups are ignored by Git, but ignored files still need access-controlled storage and backups.

This configuration does not authorize live outreach, ERP changes or issued documents. Those actions require their own reviewed workflow and permission check at the external action boundary.
