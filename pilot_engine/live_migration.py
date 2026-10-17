"""Explicit live-extension maintenance, with a verified whole-database restore first.

Run with the operator app stopped. A SQLite reserved writer lock prevents new
writes during backup/rehearsal/migration. No live intake or side effect is enabled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import time
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from pilot_engine.access import current_principal
from pilot_engine.store import _migration_statements


SCHEMA = Path(__file__).parent / "live_migrations" / "001_namespace.sql"
BASE_TABLES = frozenset({"live_schema_migration", "live_pilot", "live_source",
                         "live_intake", "live_intake_revision", "live_confirmation"})
EVIDENCE_TABLES = frozenset({"live_document", "live_authorization", "live_source_permission",
                            "live_confirmation_evidence", "live_intake_stop"})
RETENTION_TABLES = frozenset({"live_retention_binding", "live_document_purge"})
EXECUTE_TABLES = frozenset({"live_order", "live_order_revision", "live_order_input",
                            "live_order_review", "live_order_measurement", "live_order_export"})
LIVE_TABLES = BASE_TABLES | EVIDENCE_TABLES | RETENTION_TABLES | EXECUTE_TABLES
LIVE_VERSION = 4


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _connect(path: Path, *, readonly=False) -> sqlite3.Connection:
    mode = "ro" if readonly else "rw"
    db = sqlite3.connect(path.as_uri() + "?mode=" + mode, uri=True, timeout=1)
    db.execute("PRAGMA foreign_keys = ON")
    return db


def _check(db: sqlite3.Connection) -> None:
    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise sqlite3.DatabaseError("Database integrity check failed")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise sqlite3.IntegrityError("Database foreign key check failed")


def _snapshot(db: sqlite3.Connection, *, include_live=False, tables=None,
              migration_version=None) -> dict:
    """Hash rows/schema without exposing payloads; optionally include live history."""
    objects = db.execute("SELECT type, name, tbl_name, sql FROM sqlite_master "
                         "ORDER BY type, name").fetchall()
    objects = [row for row in objects if
               (include_live or not row[2].startswith("live_")) and
               (tables is None or row[2] in tables)]
    digest = hashlib.sha256(json.dumps(objects, ensure_ascii=True).encode())
    counts = {}
    for kind, name, _, _ in objects:
        if kind != "table":
            continue
        rows = db.execute("SELECT * FROM " + _quote(name)).fetchall()
        # Live AUTOINCREMENT must not change the legacy sqlite_sequence digest.
        if name == "sqlite_sequence":
            rows = [row for row in rows if (include_live or not row[0].startswith("live_"))
                    and (tables is None or row[0] in tables)]
        if name == "live_schema_migration" and migration_version is not None:
            rows = [row for row in rows if row[0] <= migration_version]
        encoded = sorted(json.dumps(row, default=lambda value: {"bytes_hex": value.hex()},
                                    ensure_ascii=True) for row in rows)
        counts[name] = len(rows)
        digest.update(json.dumps([name, encoded], ensure_ascii=True).encode())
    return {"fingerprint": digest.hexdigest(), "table_counts": counts,
            "legacy_version": db.execute("PRAGMA user_version").fetchone()[0],
            "application_id": db.execute("PRAGMA application_id").fetchone()[0]}


def _private(path: Path, *, directory=False) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Maintenance paths must be absolute without parent traversal")
    if os.name == "nt":
        from pilot_engine.windows_security import current_process_sid, inspect_resource

        sid = current_process_sid()
        inspect_resource(path, owner_sid=sid, allowed_sids=frozenset({sid}), directory=directory)
        return
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise PermissionError("Maintenance paths must not traverse symlinks")
    metadata = path.stat()
    correct_type = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    if (not correct_type or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077
            or (not directory and metadata.st_nlink != 1)):
        raise PermissionError("Maintenance resources must be private and owned by this OS account")


def _new_file(path: Path, content: bytes = b"") -> None:
    _private(path.parent, directory=True)
    if os.name == "nt":
        from pilot_engine.windows_files import WindowsPrivateFiles

        WindowsPrivateFiles(path.parent).write_new(path.name, content)
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    _private(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy_database(source: Path, destination: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(destination) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            raise FileExistsError("Recovery destination has an existing SQLite sidecar")
    _new_file(destination)
    started = time.monotonic()

    def progress(status, remaining, total):
        if time.monotonic() - started > 30:
            raise TimeoutError("Database backup exceeded the maintenance limit")

    with closing(_connect(source, readonly=True)) as src, closing(_connect(destination)) as dst:
        src.backup(dst, pages=128, progress=progress, sleep=0.01)
        _check(dst)
    _private(destination)
    with destination.open("r+b") as stream:
        os.fsync(stream.fileno())


def _live_version(db: sqlite3.Connection) -> int:
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    live_tables = {name for name in tables if name.startswith("live_")}
    if not live_tables:
        return 0
    if "live_schema_migration" not in live_tables:
        raise ValueError("Incomplete or unknown live namespace")
    version = db.execute("SELECT max(version) FROM live_schema_migration").fetchone()[0]
    if version not in (1, 2, 3, 4):
        raise ValueError("Unsupported live schema version")
    version_tables = (BASE_TABLES if version == 1 else BASE_TABLES | EVIDENCE_TABLES
                      if version == 2 else BASE_TABLES | EVIDENCE_TABLES | RETENTION_TABLES
                      if version == 3 else LIVE_TABLES)
    if live_tables != version_tables:
        raise ValueError("Incomplete or unknown live namespace")
    expected = {name + suffix for name in version_tables for suffix in ("_no_update", "_no_delete")}
    triggers = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    if not expected <= triggers:
        raise ValueError("Live append-only guards are missing")
    return version


def _apply(db: sqlite3.Connection, current: int = 0) -> None:
    scripts = ([SCHEMA] if current == 0 else [])
    if current < 2:
        scripts.append(SCHEMA.with_name("002_intake_evidence.sql"))
    if current < 3:
        scripts.append(SCHEMA.with_name("003_retention.sql"))
    scripts.append(SCHEMA.with_name("004_execute.sql"))
    for script in scripts:
        for statement in _migration_statements(script.read_text(encoding="utf-8")):
            db.execute(statement)
    new_tables = (BASE_TABLES if current == 0 else frozenset())
    if current < 2:
        new_tables |= EVIDENCE_TABLES
    if current < 3:
        new_tables |= RETENTION_TABLES
    new_tables |= EXECUTE_TABLES
    for table in sorted(new_tables):
        for action in ("UPDATE", "DELETE"):
            db.execute(f"CREATE TRIGGER {_quote(table + '_no_' + action.lower())} "
                       f"BEFORE {action} ON {_quote(table)} BEGIN "
                       "SELECT RAISE(ABORT, 'live records are append-only'); END")


@dataclass(frozen=True)
class MigrationResult:
    status: str
    legacy_version: int
    live_version: int
    backup_sha256: str | None = None
    legacy_fingerprint: str | None = None


def _migrate(database: str | Path, backup: str | Path, rehearsal: str | Path, guard=None) -> MigrationResult:
    """Create new recovery files, rehearse restore, then atomically add live tables.

    Existing recovery artifacts are never overwritten. After failure, retain them
    for inspection and retry with new paths. No restore ever overwrites the source.
    """
    database, backup, rehearsal = (Path(value).absolute() for value in (database, backup, rehearsal))
    if len({database, backup, rehearsal}) != 3 or any(
        path.is_relative_to(database.parent) for path in (backup, rehearsal)
    ):
        raise ValueError("Recovery paths must be distinct and outside the data directory")
    _private(database.parent, directory=True)
    _private(database)
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(database) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            _private(sidecar)
    with closing(_connect(database)) as db:
        if db.execute("PRAGMA journal_mode").fetchone()[0].lower() in ("off", "memory"):
            raise ValueError("A durable SQLite journal mode is required")
        db.execute("BEGIN IMMEDIATE")
        try:
            _check(db)
            if guard:
                guard(db)
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version != 29:
                raise ValueError("Live extension requires the reviewed legacy schema v29")
            current = _live_version(db)
            if current == LIVE_VERSION:
                row = db.execute("SELECT backup_sha256, legacy_fingerprint "
                                 "FROM live_schema_migration WHERE version=?", (current,)).fetchone()
                db.rollback()
                return MigrationResult("ALREADY_MIGRATED", version, current, *row)
            if not {"manufacturer", "product", "audit_event", "approval_event"} <= {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }:
                raise ValueError("Not the expected legacy application database")
            before = _snapshot(db)
            full_before = _snapshot(db, include_live=True)
            old_tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            _copy_database(database, backup)
            with closing(_connect(backup, readonly=True)) as copy:
                if _snapshot(copy, include_live=True) != full_before:
                    raise ValueError("Backup does not reproduce the legacy database")
            checksum = _sha256(backup)
            _copy_database(backup, rehearsal)
            with closing(_connect(rehearsal, readonly=True)) as restored:
                if _snapshot(restored, include_live=True) != full_before:
                    raise ValueError("Restore rehearsal does not reproduce the legacy database")
            if _sha256(backup) != checksum:
                raise ValueError("Backup changed during restore rehearsal")
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            receipt = {"format_version": 2, "backup_name": backup.name,
                       "backup_sha256": checksum, "restore_name": rehearsal.name,
                       "recorded_at_utc": now, "legacy_fingerprint": before["fingerprint"],
                       "from_live_version": current, "to_live_version": LIVE_VERSION, **full_before}
            manifest = backup.with_name(backup.name + ".manifest.json")
            _new_file(manifest, json.dumps(receipt, sort_keys=True).encode("utf-8"))
            if current:
                _apply(db, current)
            else:
                _apply(db)
            principal = current_principal()
            actor = "uid:" + str(principal) if isinstance(principal, int) else principal
            db.execute("INSERT INTO live_schema_migration "
                       "(version, backup_sha256, legacy_fingerprint, actor_id, applied_at_utc) "
                       "VALUES (?, ?, ?, ?, ?)", (LIVE_VERSION, checksum, before["fingerprint"], actor, now))
            _check(db)
            if _snapshot(db) != before:
                raise ValueError("Live extension changed legacy rows or schema")
            if _snapshot(db, include_live=True, tables=old_tables,
                         migration_version=current) != full_before:
                raise ValueError("Live extension changed existing records or schema")
            _live_version(db)
            if guard:
                guard(db)
            db.commit()
            return MigrationResult("MIGRATED", version, LIVE_VERSION, checksum, before["fingerprint"])
        except BaseException:
            db.rollback()
            raise


def migrate(database, backup, rehearsal, *, retention_ledger=None, copies_until=None):
    """Bound upgrades retain their current control lease and register new copies first."""
    database = Path(database).absolute()
    _private(database.parent, directory=True)
    _private(database)
    with closing(_connect(database, readonly=True)) as db:
        version = _live_version(db)
        bound = version >= 3 and db.execute("SELECT 1 FROM live_retention_binding").fetchone()
    if not bound or version == LIVE_VERSION:
        return _migrate(database, backup, rehearsal)
    if retention_ledger is None or copies_until is None:
        raise ValueError("Bound upgrade requires the current retention ledger and copies-until")
    from pilot_engine.live_retention import Retention

    retention = Retention(database, retention_ledger)
    with retention.ledger() as ledger, closing(_connect(database, readonly=True)) as db:
        retention.guard(db, ledger)
    ids = [retention.register_copy(backup, copies_until, pending=True),
           retention.register_copy(rehearsal, copies_until, kind="REHEARSAL", pending=True)]
    with retention.ledger() as ledger:
        result = _migrate(database, backup, rehearsal, lambda db: retention.guard(db, ledger))
    with retention.ledger(exclusive=True) as ledger:
        retention._ready(ledger)
        for identity, path in zip(ids, (backup, rehearsal)):
            ledger.execute("UPDATE recovery_copy SET sha256=?,state='ACTIVE' WHERE id=?",
                           (_sha256(Path(path).absolute()), identity))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline live-schema migration with restore rehearsal")
    parser.add_argument("--database", required=True)
    parser.add_argument("--backup", required=True)
    parser.add_argument("--rehearsal", required=True)
    parser.add_argument("--retention-ledger")
    parser.add_argument("--copies-until")
    args = parser.parse_args()
    print(json.dumps(asdict(migrate(args.database, args.backup, args.rehearsal,
                                   retention_ledger=args.retention_ledger, copies_until=args.copies_until)), sort_keys=True))


if __name__ == "__main__":
    main()
