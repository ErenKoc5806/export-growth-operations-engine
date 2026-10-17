"""Offline, trusted-owner retention maintenance; never a forensic-erasure claim.

Keep the private control ledger outside database/backup directories. Never restore
it from an older database backup. Pending operations block the reviewed intake app.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pilot_engine.access import LocalAccess
from pilot_engine.live_migration import (_check, _connect, _copy_database, _live_version,
                                         _new_file, _private, _quote, _sha256, _snapshot)


TOMBSTONE = b"PURGED"
RETIRED_TABLES = ("live_order_export", "live_order_measurement", "live_order_review",
                  "live_order_input", "live_order_revision", "live_order", "live_intake_stop", "live_confirmation_evidence", "live_confirmation",
                  "live_intake_revision", "live_intake", "live_source_permission", "live_source",
                  "live_authorization", "live_pilot", "live_document")
LEDGER_SCHEMA = """
CREATE TABLE control (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    ledger_id TEXT NOT NULL, database_id TEXT NOT NULL);
CREATE TABLE policy (sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    backup_owner TEXT NOT NULL, metadata_until TEXT NOT NULL, policy_hash TEXT NOT NULL,
    storage_review_hash TEXT NOT NULL, actor_id TEXT NOT NULL, recorded_at TEXT NOT NULL);
CREATE TABLE operation (id TEXT PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL,
    plan_json TEXT NOT NULL, policy_hash TEXT NOT NULL, actor_id TEXT NOT NULL,
    started_at TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('PENDING','COMPLETE')));
CREATE TABLE blocked_document (sha256 TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES operation(id));
CREATE TABLE retired_pilot (id TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES operation(id));
CREATE TABLE recovery_copy (id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE,
    sha256 TEXT, owner TEXT NOT NULL, expires_at TEXT NOT NULL, kind TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('PENDING','ACTIVE','DISPOSING','DISPOSED')),
    disposition_hash TEXT);
"""


def _now():
    return datetime.now(timezone.utc)


def _stamp():
    return _now().isoformat(timespec="microseconds")


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _reference(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise ValueError("Reviewed policy/storage/disposition reference is required")
    return _digest(value.strip())


def _future(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed <= _now():
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, AttributeError) as exc:
        raise ValueError("A future retention expiry with timezone is required") from exc


def private_database(path):
    path = Path(path).absolute()
    _private(path.parent, directory=True)
    if not path.exists() and not path.is_symlink():
        raise FileNotFoundError("Private database/control file is missing")
    _private(path)
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            _private(sidecar)
    return path


class Retention:
    def __init__(self, database, ledger, access=None):
        self.database = Path(database).absolute()
        self.path = Path(ledger).absolute()
        if (self.path.is_relative_to(self.database.parent)
                or self.database.is_relative_to(self.path.parent)):
            raise ValueError("Retention ledger must be in a separate private directory")
        self.access = access if access is not None else LocalAccess.single_operator()

    def admin(self):
        actor, allowed = self.access.decision("PURGE_LIVE_EVIDENCE")
        if not allowed:
            raise PermissionError("Retention maintenance requires the authorized administrator")
        return actor

    @contextmanager
    def ledger(self, *, exclusive=False):
        private_database(self.path)
        with closing(_connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            if db.execute("PRAGMA journal_mode").fetchone()[0].lower() != "delete":
                raise ValueError("Retention control requires DELETE journal mode for reader/writer exclusion")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN EXCLUSIVE" if exclusive else "BEGIN")
            try:
                if db.execute("SELECT count(*) FROM control").fetchone()[0] != 1:
                    raise ValueError("Invalid retention control ledger")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def review(self, owner, metadata_until, policy_ref, storage_ref, *, reviewed=False):
        actor = self.admin()
        if not reviewed or not isinstance(owner, str) or not owner.strip() or len(owner) > 200:
            raise ValueError("Named backup owner and reviewed retention/hold/storage policy are required")
        values = (owner.strip(), _future(metadata_until), _reference(policy_ref),
                  _reference(storage_ref), actor, _stamp())
        with self.ledger(exclusive=True) as ledger:
            ledger.execute("INSERT INTO policy VALUES (NULL,?,?,?,?,?,?)", values)

    def initialize(self, owner, metadata_until, policy_ref, storage_ref, *, reviewed=False,
                   existing_copies=(), copies_until=None):
        self.admin()
        # Validate declarations before creating any control file.
        if not reviewed or not owner or not owner.strip():
            raise ValueError("Explicit reviewed retention/hold/storage declarations are required")
        _future(metadata_until)
        _reference(policy_ref)
        _reference(storage_ref)
        private_database(self.database)
        with closing(_connect(self.database)) as db:
            if _live_version(db) not in (3, 4):
                raise ValueError("Install live schema v3 before binding retention control")
            binding = db.execute("SELECT * FROM live_retention_binding").fetchone()
            if binding:
                raise ValueError("Database is already bound; use its existing current ledger")
            if db.execute("SELECT 1 FROM live_document").fetchone() and not existing_copies:
                raise ValueError("Populated upgrade requires registration of reviewed existing recovery copies")
        if existing_copies:
            _future(copies_until)
        _new_file(self.path)
        with closing(_connect(self.path)) as ledger:
            ledger.executescript(LEDGER_SCHEMA)
            ledger.execute("INSERT INTO control VALUES (1,?,?)", (uuid4().hex, uuid4().hex))
            ledger.commit()
        self.review(owner, metadata_until, policy_ref, storage_ref, reviewed=True)
        for path in existing_copies:
            self.register_copy(path, copies_until)
        self.bind()

    def bind(self):
        """Retry interrupted initial binding, or reconcile an unbound old copy.

        Existing control IDs may never be replaced. Denied restored evidence is
        checked before binding; this is not permission to restore lost live history.
        """
        self.admin()
        private_database(self.database)
        with self.ledger(exclusive=True) as ledger, closing(_connect(self.database)) as db:
            self._ready(ledger)
            if _live_version(db) not in (3, 4):
                raise ValueError("Install live schema v3")
            self._denied(db, ledger)
            if (db.execute("SELECT 1 FROM live_document").fetchone() and
                    not ledger.execute("SELECT 1 FROM recovery_copy WHERE state='ACTIVE'").fetchone()):
                raise ValueError("Register reviewed existing recovery copies before binding populated data")
            control = ledger.execute("SELECT * FROM control").fetchone()
            db.execute("BEGIN IMMEDIATE")
            try:
                prior = db.execute("SELECT database_id, ledger_id FROM live_retention_binding").fetchone()
                expected = (control["database_id"], control["ledger_id"])
                if prior and tuple(prior) != expected:
                    raise ValueError("Wrong database/control ledger binding")
                if not prior:
                    db.execute("INSERT INTO live_retention_binding VALUES (1,?,?)", expected)
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _ready(self, ledger):
        if ledger.execute("SELECT 1 FROM operation WHERE state='PENDING'").fetchone():
            raise ValueError("Retention maintenance pending; stop intake and resume the recorded operation")
        policy = ledger.execute("SELECT * FROM policy ORDER BY sequence DESC LIMIT 1").fetchone()
        if not policy:
            raise ValueError("Reviewed retention/storage policy missing")
        _future(policy["metadata_until"])
        return policy

    def _binding(self, db, ledger):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_retention_binding'").fetchone():
            raise ValueError("Install live schema v3; older applications/copies cannot bypass retention")
        control = ledger.execute("SELECT database_id, ledger_id FROM control").fetchone()
        binding = db.execute("SELECT database_id, ledger_id FROM live_retention_binding").fetchone()
        if not binding or tuple(binding) != tuple(control):
            raise ValueError("Current external retention ledger binding is required")

    def _denied(self, db, ledger):
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "live_pilot" in tables:
            retired = {row[0] for row in ledger.execute("SELECT id FROM retired_pilot")}
            if any(row[0] in retired for row in db.execute("SELECT id FROM live_pilot")):
                raise ValueError("Restored retired pilot is quarantined; do not activate this copy")
        if "live_document" not in tables:
            return
        blocked = {row[0] for row in ledger.execute("SELECT sha256 FROM blocked_document")}
        for row in db.execute("SELECT id, sha256 FROM live_document"):
            if row[1] not in blocked:
                continue
            receipt = (db.execute("SELECT 1 FROM live_document_purge WHERE document_id=? AND sha256=?",
                                  (row[0], row[1])).fetchone() if "live_document_purge" in tables else None)
            content = db.execute("SELECT content FROM live_document WHERE id=?", (row[0],)).fetchone()[0]
            if not receipt or content != TOMBSTONE:
                raise ValueError("Restored/reuploaded purged document is quarantined")

    def guard(self, db, ledger):
        self._ready(ledger)
        self._binding(db, ledger)
        self._denied(db, ledger)

    def verify_restore(self, restored):
        restored = private_database(restored)
        with self.ledger() as ledger, closing(_connect(restored, readonly=True)) as db:
            self._ready(ledger)
            _check(db)
            self._denied(db, ledger)
            if _live_version(db) in (3, 4):
                self._binding(db, ledger)
            return {"status": "NO_RETIRED_CONTENT_FOUND", "sha256": _sha256(restored),
                    "activation_authorized": False}

    def _plan(self, db, kind, target):
        if kind == "DOCUMENT":
            row = db.execute("SELECT id, sha256, length(content) FROM live_document WHERE id=?", (target,)).fetchone()
            if not row:
                raise ValueError("Document not found")
            documents = [[row[0], row[1], row[2]]]
        elif kind == "PILOT":
            pilots = [row[0] for row in db.execute("SELECT id FROM live_pilot")]
            if pilots != [target]:
                raise ValueError("Retirement supports the sole named pilot only")
            documents = [list(row) for row in db.execute("SELECT id, sha256, length(content) FROM live_document ORDER BY id")]
        else:
            raise ValueError("Choose DOCUMENT purge or PILOT retirement")
        refs, affected_ids = {}, {}
        existing_tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in RETIRED_TABLES:
            if kind == "PILOT" and table in existing_tables:
                refs[table] = db.execute("SELECT count(*) FROM " + _quote(table)).fetchone()[0]
        if kind == "DOCUMENT":
            for table, columns in (("live_authorization", ("manufacturer_document_id", "it_document_id")),
                                   ("live_source_permission", ("document_id", "permission_document_id")),
                                   ("live_confirmation_evidence", ("document_id",))):
                clause = " OR ".join(_quote(column) + "=?" for column in columns)
                refs[table] = db.execute("SELECT count(*) FROM " + table + " WHERE " + clause,
                                         (target,) * len(columns)).fetchone()[0]
                identity = {"live_authorization": "pilot_id", "live_source_permission": "source_id",
                            "live_confirmation_evidence": "confirmation_sequence"}[table]
                affected_ids[table] = [row[0] for row in db.execute(
                    "SELECT " + identity + " FROM " + table + " WHERE " + clause + " ORDER BY " + identity,
                    (target,) * len(columns))]
        if "live_order" in existing_tables:
            if kind == "PILOT":
                orders = [row[0] for row in db.execute("SELECT id FROM live_order ORDER BY id")]
            else:
                orders = [row[0] for row in db.execute("""
                    SELECT DISTINCT o.id FROM live_order o
                    JOIN live_order_revision r ON r.order_id=o.id
                    WHERE r.document_id=? OR r.permission_document_id=?
                    OR EXISTS (SELECT 1 FROM live_order_review v WHERE v.order_id=o.id AND v.document_id=?)
                    OR EXISTS (SELECT 1 FROM live_order_input i
                        JOIN live_source_permission p ON p.source_id=i.source_id
                        JOIN live_confirmation_evidence e ON e.confirmation_sequence=i.confirmation_sequence
                        WHERE i.order_id=o.id AND (p.document_id=? OR p.permission_document_id=? OR e.document_id=?))
                    OR EXISTS (SELECT 1 FROM live_authorization a WHERE a.pilot_id=o.pilot_id
                        AND (a.manufacturer_document_id=? OR a.it_document_id=?)) ORDER BY o.id
                    """, (target,) * 8)]
            affected_ids["live_order"] = orders
            refs["live_order"] = len(orders)
            affected_ids["export_copy_ids"] = [row[0] for order in orders for row in db.execute(
                "SELECT copy_id FROM live_order_export WHERE order_id=? ORDER BY copy_id", (order,))]
        # A full logical fingerprint invalidates a plan if any record/decision changes.
        return {"kind": kind, "target": target, "documents": documents,
                "affected_rows": refs, "affected_ids": affected_ids,
                "snapshot": _snapshot(db, include_live=True)["fingerprint"],
                "retains_live_text": kind == "DOCUMENT"}

    def plan(self, kind, target):
        self.admin()
        private_database(self.database)
        with self.ledger() as ledger, closing(_connect(self.database)) as db:
            db.execute("BEGIN")
            self.guard(db, ledger)
            plan = self._plan(db, kind, target)
            return {**plan, "plan_sha256": _digest(_json(plan))}

    def begin(self, kind, target, plan_hash, policy_ref, *, reviewed=False):
        actor = self.admin()
        if not reviewed:
            raise ValueError("Explicit scope, remaining metadata and retention/hold review is required")
        policy_hash = _reference(policy_ref)
        private_database(self.database)
        with self.ledger(exclusive=True) as ledger, closing(_connect(self.database)) as db:
            self.guard(db, ledger)
            policy = self._ready(ledger)
            if policy_hash != policy["policy_hash"]:
                raise ValueError("Use the current reviewed policy reference")
            db.execute("BEGIN IMMEDIATE")
            plan = self._plan(db, kind, target)
            if _digest(_json(plan)) != plan_hash:
                raise ValueError("Purge plan is stale; inspect a new plan")
            operation = uuid4().hex
            ledger.execute("INSERT INTO operation VALUES (?,?,?,?,?,?,?,?)",
                           (operation, kind, target, _json(plan), policy_hash, actor, _stamp(), "PENDING"))
            for _, digest, _ in plan["documents"]:
                ledger.execute("INSERT OR IGNORE INTO blocked_document VALUES (?,?)", (digest, operation))
            if kind == "PILOT":
                ledger.execute("INSERT INTO retired_pilot VALUES (?,?)", (target, operation))
            # Commit intent in the independent ledger BEFORE modifying main DB.
            return operation

    def _mutate(self, db, operation):
        plan = json.loads(operation["plan_json"])
        guards = {row[0]: row[1] for row in db.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND name GLOB 'live_*'")}
        before = _snapshot(db)
        for document, digest, _ in plan["documents"]:
            row = db.execute("SELECT sha256,content FROM live_document WHERE id=?", (document,)).fetchone()
            if row is None and operation["kind"] == "PILOT":
                continue  # Retirement committed before a compaction interruption.
            receipt = db.execute("SELECT sha256,operation_id FROM live_document_purge WHERE document_id=?",
                                 (document,)).fetchone()
            if receipt:
                if receipt[0] != digest or row is None or row[1] != TOMBSTONE:
                    raise ValueError("Purge receipt/content mismatch")
                continue
            if row is None or row[0] != digest or hashlib.sha256(row[1]).hexdigest() != digest:
                raise ValueError("Purge target no longer matches the reviewed document")
            db.execute("INSERT INTO live_document_purge VALUES (?,?,?,?,?,?)",
                       (document, digest, operation["id"], operation["policy_hash"],
                        operation["actor_id"], _stamp()))
        if operation["kind"] == "DOCUMENT":
            db.execute("DROP TRIGGER live_document_no_update")
            db.execute("UPDATE live_document SET content=? WHERE id=?", (TOMBSTONE, operation["target"]))
            db.execute(guards["live_document_no_update"])
        else:
            for table in RETIRED_TABLES:
                if table + "_no_delete" not in guards:
                    continue
                trigger = table + "_no_delete"
                db.execute("DROP TRIGGER " + _quote(trigger))
                db.execute("DELETE FROM " + _quote(table))
                db.execute(guards[trigger])
        after_guards = {row[0]: row[1] for row in db.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND name GLOB 'live_*'")}
        if guards != after_guards or before != _snapshot(db):
            raise ValueError("Maintenance changed guards or legacy data")

    def _compact(self):
        with closing(_connect(self.database)) as db:
            if db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] != 0:
                raise sqlite3.OperationalError("Stop every database user before compaction")
            if db.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
                raise sqlite3.OperationalError("Cannot switch to durable offline journal")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("VACUUM")
            _check(db)
            if _live_version(db) not in (3, 4):
                raise ValueError("Retention schema/guards missing after compaction")
        with self.database.open("r+b") as stream:
            os.fsync(stream.fileno())
        for suffix in ("-wal", "-shm", "-journal"):
            if Path(str(self.database) + suffix).exists():
                raise ValueError("SQLite sidecar remains; keep maintenance pending")

    def resume(self, operation_id):
        self.admin()
        private_database(self.database)
        with self.ledger() as ledger:
            operation = ledger.execute("SELECT * FROM operation WHERE id=?", (operation_id,)).fetchone()
            if not operation:
                raise ValueError("Unknown maintenance operation")
            operation = dict(operation)
        if operation["state"] == "COMPLETE":
            self.verify_restore(self.database)
            return {"operation_id": operation_id, "status": "COMPLETE"}
        # Intake cannot acquire a usable lease while this durable intent is pending.
        with closing(_connect(self.database)) as db:
            with self.ledger() as ledger:
                self._binding(db, ledger)
            if _live_version(db) not in (3, 4):
                raise ValueError("Wrong live schema for maintenance")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            try:
                self._mutate(db, operation)
                _check(db)
                db.commit()
            except BaseException:
                db.rollback()
                raise
        self._compact()
        with self.ledger(exclusive=True) as ledger, closing(_connect(self.database)) as db:
            self._binding(db, ledger)
            self._denied(db, ledger)
            ledger.execute("UPDATE operation SET state='COMPLETE' WHERE id=?", (operation_id,))
        return {"operation_id": operation_id, "status": "COMPLETE", "physical_erasure_claimed": False}

    def inventory(self):
        self.admin()
        private_database(self.database)
        with self.ledger() as ledger, closing(_connect(self.database, readonly=True)) as db:
            documents = [{"id": row[0], "sha256": row[1], "byte_length": row[2], "purged": bool(row[3])}
                         for row in db.execute("SELECT d.id,d.sha256,length(d.content),p.document_id "
                             "FROM live_document d LEFT JOIN live_document_purge p ON p.document_id=d.id ORDER BY d.id")]
            return {"operations": [dict(row) for row in ledger.execute(
                        "SELECT id,kind,target,state,started_at FROM operation ORDER BY started_at")],
                    "copies": [{**dict(row), "expired": datetime.fromisoformat(row["expires_at"]) <= _now(),
                                "present": Path(row["path"]).exists() or Path(row["path"]).is_symlink(),
                                "reappeared_after_disposal": row["state"] == "DISPOSED" and
                                (Path(row["path"]).exists() or Path(row["path"]).is_symlink())}
                               for row in ledger.execute("SELECT * FROM recovery_copy ORDER BY id")],
                    "policy": dict(ledger.execute("SELECT * FROM policy ORDER BY sequence DESC LIMIT 1").fetchone()),
                    "documents": documents, "pilots": [row[0] for row in db.execute("SELECT id FROM live_pilot")],
                    "live_text_columns": {table: [row[1] for row in db.execute("PRAGMA table_info(" + _quote(table) + ")")
                                                  if row[2] == "TEXT"] for table in RETIRED_TABLES}}

    def register_copy(self, path, expires_at, *, kind="BACKUP", pending=False):
        self.admin()
        path = Path(path).absolute()
        expiry = _future(expires_at)
        if kind not in ("BACKUP", "REHEARSAL", "EXPORT"):
            raise ValueError("Unknown recovery/export copy kind")
        if path.is_relative_to(self.database.parent) or path.is_relative_to(self.path.parent):
            raise ValueError("Copies must be outside data/control directories")
        _private(path.parent, directory=True)
        if pending:
            if path.exists() or path.is_symlink():
                raise FileExistsError("Use a new copy path")
            checksum = None
        else:
            private_database(path)
            checksum = _sha256(path)
        identity = uuid4().hex
        with self.ledger(exclusive=True) as ledger:
            policy = self._ready(ledger)
            if datetime.fromisoformat(expiry) > datetime.fromisoformat(policy["metadata_until"]):
                raise ValueError("Copy expiry cannot outlive the reviewed control-metadata retention")
            ledger.execute("INSERT INTO recovery_copy VALUES (?,?,?,?,?,?,?,NULL)",
                           (identity, str(path), checksum, policy["backup_owner"], expiry, kind,
                            "PENDING" if pending else "ACTIVE"))
        return identity

    def backup(self, backup, rehearsal, expires_at):
        self.admin()
        backup, rehearsal = Path(backup).absolute(), Path(rehearsal).absolute()
        if backup == rehearsal:
            raise ValueError("Separate backup/rehearsal paths required")
        ids = [self.register_copy(backup, expires_at, pending=True),
               self.register_copy(rehearsal, expires_at, kind="REHEARSAL", pending=True)]
        private_database(self.database)
        with self.ledger() as ledger, closing(_connect(self.database)) as db:
            db.execute("BEGIN IMMEDIATE")
            self.guard(db, ledger)
            before = _snapshot(db, include_live=True)
            _copy_database(self.database, backup)
            _copy_database(backup, rehearsal)
            for path in (backup, rehearsal):
                with closing(_connect(path, readonly=True)) as restored:
                    _check(restored)
                    if _snapshot(restored, include_live=True) != before:
                        raise ValueError("Recovery copy does not match the full database")
        with self.ledger(exclusive=True) as ledger:
            for identity, path in zip(ids, (backup, rehearsal)):
                ledger.execute("UPDATE recovery_copy SET sha256=?,state='ACTIVE' WHERE id=?",
                               (_sha256(path), identity))
        return {"backup_id": ids[0], "rehearsal_id": ids[1], "verified": True}

    def dispose_copy(self, copy_id, expected_sha256, disposition_ref, *, reviewed=False):
        self.admin()
        if not reviewed:
            raise ValueError("Reviewed expiry/hold/disposition decision required")
        reference = _reference(disposition_ref)
        with self.ledger(exclusive=True) as ledger:
            row = ledger.execute("SELECT * FROM recovery_copy WHERE id=?", (copy_id,)).fetchone()
            if not row:
                raise ValueError("Unknown registered copy")
            path = Path(row["path"])
            if row["state"] == "DISPOSED":
                if path.exists() or path.is_symlink():
                    raise ValueError("Disposed copy path reappeared; quarantine and review it")
                return {"status": "DISPOSED"}
            if row["sha256"] and row["sha256"] != expected_sha256:
                raise ValueError("Copy hash changed; do not delete it")
            if path.is_relative_to(self.database.parent) or path.is_relative_to(self.path.parent):
                raise ValueError("Never dispose data/control files")
            if path.exists() or path.is_symlink():
                private_database(path)
                if _sha256(path) != expected_sha256 or (row["sha256"] and row["sha256"] != expected_sha256):
                    raise ValueError("Copy hash changed; do not delete it")
                if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
                    raise ValueError("Stop copy users and review SQLite sidecars first")
            elif row["state"] != "DISPOSING":
                raise FileNotFoundError("Copy missing outside the recorded disposal operation")
            ledger.execute("UPDATE recovery_copy SET state='DISPOSING',disposition_hash=?,"
                           "sha256=coalesce(sha256,?) WHERE id=?", (reference, expected_sha256, copy_id))
        if path.exists():
            path.unlink()
        with self.ledger(exclusive=True) as ledger:
            ledger.execute("UPDATE recovery_copy SET state='DISPOSED' WHERE id=?", (copy_id,))
        return {"status": "DISPOSED", "physical_erasure_claimed": False}


def main():
    parser = argparse.ArgumentParser(description="Offline reviewed live-evidence retention maintenance")
    parser.add_argument("--database", required=True)
    parser.add_argument("--ledger", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "review"):
        part = sub.add_parser(command)
        part.add_argument("--owner", required=True)
        part.add_argument("--metadata-until", required=True)
        part.add_argument("--policy-ref", required=True)
        part.add_argument("--storage-ref", required=True)
        part.add_argument("--reviewed", action="store_true", required=True)
        if command == "init":
            part.add_argument("--existing-copy", action="append", default=[])
            part.add_argument("--copies-until")
    sub.add_parser("bind")
    sub.add_parser("inventory")
    part = sub.add_parser("plan")
    part.add_argument("--kind", choices=("DOCUMENT", "PILOT"), required=True)
    part.add_argument("--target", required=True)
    part = sub.add_parser("begin")
    part.add_argument("--kind", choices=("DOCUMENT", "PILOT"), required=True)
    part.add_argument("--target", required=True)
    part.add_argument("--plan-sha256", required=True)
    part.add_argument("--policy-ref", required=True)
    part.add_argument("--reviewed", action="store_true", required=True)
    part = sub.add_parser("resume")
    part.add_argument("--operation", required=True)
    part = sub.add_parser("verify-restore")
    part.add_argument("--copy", required=True)
    part = sub.add_parser("register-copy")
    part.add_argument("--copy", required=True)
    part.add_argument("--expires-at", required=True)
    part.add_argument("--kind", choices=("BACKUP", "REHEARSAL", "EXPORT"), default="BACKUP")
    part = sub.add_parser("backup")
    part.add_argument("--backup", required=True)
    part.add_argument("--rehearsal", required=True)
    part.add_argument("--expires-at", required=True)
    part = sub.add_parser("dispose-copy")
    part.add_argument("--copy-id", required=True)
    part.add_argument("--sha256", required=True)
    part.add_argument("--disposition-ref", required=True)
    part.add_argument("--reviewed", action="store_true", required=True)
    args = parser.parse_args()
    retention = Retention(args.database, args.ledger)
    command = args.command
    if command in ("init", "review"):
        method = retention.initialize if command == "init" else retention.review
        extra = {"existing_copies": args.existing_copy, "copies_until": args.copies_until} if command == "init" else {}
        method(args.owner, args.metadata_until, args.policy_ref, args.storage_ref, reviewed=args.reviewed, **extra)
        result = {"status": "RECORDED_OPERATOR_ATTESTATION"}
    elif command == "bind":
        retention.bind()
        result = {"status": "BOUND"}
    elif command == "inventory":
        result = retention.inventory()
    elif command == "plan":
        result = retention.plan(args.kind, args.target)
    elif command == "begin":
        result = {"operation_id": retention.begin(args.kind, args.target, args.plan_sha256,
                  args.policy_ref, reviewed=args.reviewed), "status": "PENDING"}
    elif command == "resume":
        result = retention.resume(args.operation)
    elif command == "verify-restore":
        result = retention.verify_restore(args.copy)
    elif command == "backup":
        result = retention.backup(args.backup, args.rehearsal, args.expires_at)
    elif command == "register-copy":
        result = {"copy_id": retention.register_copy(args.copy, args.expires_at, kind=args.kind)}
    else:
        result = retention.dispose_copy(args.copy_id, args.sha256, args.disposition_ref, reviewed=args.reviewed)
    print(_json(result))


if __name__ == "__main__":
    main()
