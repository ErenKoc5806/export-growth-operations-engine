"""Disposable FICTIONAL Execute fixtures; never manufacturer acceptance evidence."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import test_live_intake as intake_tests
from pilot_engine import live_migration as migration
from pilot_engine.access import LocalAccess, Role, current_principal
from pilot_engine.live_execute import FACTS, INPUT_KINDS, LiveExecute
from pilot_engine.live_intake import LiveIntake, _stamp
from pilot_engine.live_operator import LiveOperator
from pilot_engine.operator_web import make_handler


PRICE = {"sku": "FIXTURE-ONLY", "unit_price": "10.00", "currency": "EUR", "unit": "PCS",
    "payment_terms": "Fictional advance", "incoterm": "FCA Fictional city",
    "tax_authority_ref": "TEST-TAX", "valid_until_utc": "2098-01-01T00:00:00Z"}
PO = {"po_number": "FICTIONAL-PO", "buyer": "FICTIONAL Buyer", "sku": "FIXTURE-ONLY",
    "quantity": "2", "unit": "PCS", "unit_price": "10.00", "currency": "EUR", "total": "20.00",
    "payment_terms": "Fictional advance", "incoterm": "FCA Fictional city", "delivery": "Fictional agreed date"}
FACT_VALUES = {**{key: "FICTIONAL TEST ONLY " + key for key in FACTS},
    "origin_country": "TR", "destination_country": "DE", "tax_amount": "0", "charges": "1.00",
    "discount": "0", "packing_quantity": "2", "packages": "1", "net_kg": "1.00", "gross_kg": "1.20",
    **{key: intake_tests.PROFILE[key] for key in ("permission_ref", "terms_ref", "processing_basis", "reviewer_name", "retention_until_utc")},
    "source_ref": "FICTIONAL-PACKING-SOURCE"}


class LiveExecuteTests(unittest.TestCase):
    snapshot = intake_tests.LiveIntakeTests.snapshot
    enroll = intake_tests.LiveIntakeTests.enroll
    confirm = intake_tests.LiveIntakeTests.confirm

    def setUp(self):
        intake_tests.LiveIntakeTests.setUp(self)
        self.pilot_id = self.enroll()
        self.ids = []
        for kind, values in zip(INPUT_KINDS, (intake_tests.PROFILE, PRICE, PO)):
            identity = self.intake.save(kind, {**intake_tests.PROFILE, **values, "source_ref": "FICTIONAL-" + kind},
                                        ("FICTIONAL source " + kind).encode(), intake_tests.PERMISSION)
            self.confirm(identity)
            self.ids.append(identity)
        self.execute = LiveExecute(self.intake)

    def save_order(self, **changes):
        _, digest = self.execute.selection(self.ids)
        return self.execute.save(self.ids, digest, {**FACT_VALUES, **changes},
                                 b"FICTIONAL packing and tax facts", b"FICTIONAL facts permission")

    def review_order(self, identity, decision="APPROVE", **changes):
        view = self.execute.view(identity)
        values = {"approver_name": intake_tests.AUTH["approver_name"], "reviewed": "yes", "decision": decision,
            "reason": "FICTIONAL exact complete bundle reviewed", "method": "FICTIONAL written attestation",
            "evidence_ref": "FICTIONAL-REVIEW", "confirmed_at_utc": _stamp(), **changes}
        return self.execute.review(identity, view["row"]["revision"], view["row"]["bundle_sha256"],
                                   view["sequence"], values, b"FICTIONAL full draft review"), values

    def export_order(self, identity, expires_at="2097-01-01T00:00:00Z"):
        row = self.execute.view(identity)["row"]
        return self.execute.prepare_export(identity, row["revision"], row["bundle_sha256"],
                                           self.recovery, expires_at)

    def purge(self, kind, target):
        plan = self.retention.plan(kind, target)
        operation = self.retention.begin(kind, target, plan["plan_sha256"], "FICTIONAL-POLICY", reviewed=True)
        self.retention.resume(operation)
        return plan

    def test_end_to_end_order_three_unreleased_drafts_review_metrics_export_restart_and_legacy_isolation(self):
        identity = self.save_order()
        view = self.execute.view(identity)
        self.assertEqual(view["status"], "REVIEW_REQUIRED")
        self.assertEqual(len(view["bundle"]["documents"]), 3)
        invoice = view["bundle"]["documents"]["COMMERCIAL_INVOICE_DRAFT"]
        self.assertEqual(invoice["İncelenmiş toplam EUR"], "21.00")
        self.assertEqual(invoice["Alıcı"], PO["buyer"])
        self.assertEqual(invoice["HS6"], "732690")
        for item in view["bundle"]["inputs"]:
            self.assertEqual(item["confirmation"]["approver_name"], intake_tests.AUTH["approver_name"])
            self.assertEqual(item["permission"]["permission_ref"], intake_tests.PROFILE["permission_ref"])
        with self.assertRaisesRegex(ValueError, "insan onayı"):
            self.export_order(identity)
        self.review_order(identity)
        self.assertEqual(self.execute.view(identity)["status"], "APPROVE")
        row = self.execute.view(identity)["row"]
        metrics = {"baseline_minutes": "30", "elapsed_minutes": "8.5", "corrections": "2",
                   "notes": "FICTIONAL measured review and rework", "baseline_ref": "FICTIONAL-13-BASELINE"}
        measurement = self.execute.measure(identity, 1, row["bundle_sha256"], metrics)
        self.assertEqual(self.execute.measure(identity, 1, row["bundle_sha256"], metrics), measurement)
        with self.assertRaisesRegex(ValueError, "zaten kayıtlı"):
            self.execute.measure(identity, 1, row["bundle_sha256"], {**metrics, "elapsed_minutes": "9"})
        export = self.export_order(identity)
        self.assertEqual(self.export_order(identity), export)
        artifact, copy = self.execute.download(export)
        self.assertEqual(copy["state"], "PENDING")
        self.assertIn(b"COMMERCIAL_INVOICE_DRAFT", artifact)
        self.assertIn(b"UNRELEASED", artifact)
        migration._new_file(Path(copy["path"]), artifact)
        self.assertEqual(self.execute.finish_export(export), copy["id"])
        restarted = LiveExecute(LiveIntake(self.path, retention_ledger=self.ledger))
        self.assertEqual(restarted.download(export)[0], artifact)
        self.assertEqual(self.retention.inventory()["copies"][0]["state"], "ACTIVE")
        self.assertEqual(self.snapshot(self.path), self.before)
        with self.intake.transaction() as db:
            for table in migration.EXECUTE_TABLES:
                with self.subTest(table=table), self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    db.execute("DELETE FROM " + table)

    def test_missing_facts_and_po_differences_remain_visible_and_cannot_be_approved(self):
        identity = self.save_order(buyer_address="", gross_kg="0.1", packing_quantity="3", packages="0",
                                   origin_country="CN", tax_amount="NaN")
        view = self.execute.view(identity)
        requirements = " ".join(view["bundle"]["requirements"])
        for expected in ("Alıcı yasal adresi", "Brüt", "miktar", "Paket", "TR", "vergi"):
            self.assertIn(expected, requirements)
        with self.assertRaisesRegex(ValueError, "gereksinimleri"):
            self.review_order(identity)
        self.review_order(identity, "REJECT")
        _, fingerprint = self.execute.selection(self.ids)
        self.execute.save(self.ids, fingerprint, FACT_VALUES, b"FICTIONAL packing and tax facts",
            b"FICTIONAL facts permission", expected_revision=1, expected_hash=view["row"]["bundle_sha256"])
        self.review_order(identity)
        self.assertEqual([r["revision"] for r in self.execute.view(identity)["revisions"]], [2, 1])
        self.assertEqual([r["decision"] for r in self.execute.view(identity)["history"]], ["APPROVE", "REJECT"])
        # A genuinely approved different PO price is still not authorized by the selected price record.
        po_id = self.intake.save("CUSTOMER_PO", {**intake_tests.PROFILE, **PO, "source_ref": "FICTIONAL-PO-DIFFERENT",
            "po_number": "FICTIONAL-PO-DIFFERENT", "unit_price": "11", "total": "22"}, b"FICTIONAL different PO", intake_tests.PERMISSION)
        self.confirm(po_id)
        ids = [*self.ids[:2], po_id]
        _, digest = self.execute.selection(ids)
        different = self.execute.save(ids, digest, FACT_VALUES, b"FICTIONAL facts", b"FICTIONAL permission")
        self.assertIn("unit_price", " ".join(self.execute.view(different)["bundle"]["requirements"]))
        with self.assertRaises(ValueError):
            self.review_order(different)

    def test_duplicate_po_source_and_export_retention_longer_than_inputs_are_rejected(self):
        identity = self.save_order()
        self.review_order(identity)
        with self.assertRaisesRegex(ValueError, "geçerliliğini aşamaz"):
            self.export_order(identity, "2099-01-01T00:00:00Z")
        duplicate = self.intake.save("CUSTOMER_PO", {**intake_tests.PROFILE, **PO, "source_ref": "FICTIONAL-DUPLICATE-PO"},
                                     b"FICTIONAL duplicate PO source", intake_tests.PERMISSION)
        self.confirm(duplicate)
        ids = [*self.ids[:2], duplicate]
        _, fingerprint = self.execute.selection(ids)
        with self.assertRaisesRegex(ValueError, "yerel sipariş var"):
            self.execute.save(ids, fingerprint, FACT_VALUES, b"FICTIONAL facts", b"FICTIONAL permission")
        self.assertEqual(len(self.execute.view()["orders"]), 1)

    def test_revising_po_identity_cannot_reuse_an_existing_order_or_bypass_duplicate_guard(self):
        identity = self.save_order()
        row = self.execute.view(identity)["row"]
        po = self.intake.view(self.ids[2])["row"]
        self.intake.save("CUSTOMER_PO", {**intake_tests.PROFILE, **PO, "po_number": "FICTIONAL-OTHER-PO",
            "source_ref": "FICTIONAL-PO-IDENTITY-CHANGE"}, b"FICTIONAL changed identity", intake_tests.PERMISSION,
            intake_id=self.ids[2], expected_revision=1, expected_hash=po["payload_sha256"])
        self.confirm(self.ids[2])
        _, digest = self.execute.selection(self.ids)
        with self.assertRaisesRegex(ValueError, "kimliği değişemez"):
            self.execute.save(self.ids, digest, FACT_VALUES, b"FICTIONAL facts", b"FICTIONAL permission",
                              expected_revision=1, expected_hash=row["bundle_sha256"])
        self.assertIn("BLOCKED", self.execute.view(identity)["status"])
        self.assertEqual(len(self.execute.view()["orders"]), 1)

    def test_source_revoke_reapproval_revision_and_stale_forms_invalidate_prior_drafts(self):
        identity = self.save_order()
        self.review_order(identity)
        export = self.export_order(identity)
        _, old_selection = self.execute.selection(self.ids)
        self.confirm(self.ids[0], "REVOKE")
        with self.assertRaises(ValueError):
            self.execute.download(export)
        self.confirm(self.ids[0])
        self.assertIn("BLOCKED", self.execute.view(identity)["status"])
        with self.assertRaisesRegex(ValueError, "değişti"):
            self.execute.save(self.ids, old_selection, FACT_VALUES, b"fixture", b"fixture")
        current = self.intake.view(self.ids[0])
        self.intake.save("PRODUCT_PROFILE", {**intake_tests.PROFILE, "source_ref": "FICTIONAL-PRODUCT-REV2"},
            b"FICTIONAL revised source", intake_tests.PERMISSION, intake_id=self.ids[0], expected_revision=1,
            expected_hash=current["row"]["payload_sha256"])
        with self.assertRaises(ValueError):
            self.execute.selection(self.ids)
        self.confirm(self.ids[0])
        row = self.execute.view(identity)["row"]
        _, fingerprint = self.execute.selection(self.ids)
        self.execute.save(self.ids, fingerprint, FACT_VALUES, b"FICTIONAL packing and tax facts",
            b"FICTIONAL facts permission", expected_revision=1, expected_hash=row["bundle_sha256"])
        self.review_order(identity)
        with self.assertRaisesRegex(ValueError, "Eski çıktı"):
            self.execute.download(export)

    def test_idempotent_save_review_duplicate_and_failed_upload_roll_back(self):
        identity = self.save_order()
        self.assertEqual(self.save_order(), identity)
        sequence, values = self.review_order(identity)
        row = self.execute.view(identity)["row"]
        self.assertEqual(self.execute.review(identity, 1, row["bundle_sha256"], 0, values,
                                            b"FICTIONAL full draft review"), sequence)
        self.review_order(identity, "REVOKE")
        with self.assertRaisesRegex(ValueError, "durumu değişti"):
            self.execute.review(identity, 1, row["bundle_sha256"], 0, values, b"FICTIONAL full draft review")
        with closing(migration._connect(self.path)) as db:
            before = migration._snapshot(db, include_live=True)
        _, digest = self.execute.selection(self.ids)
        with self.assertRaises(ValueError):
            self.execute.save(self.ids, digest, FACT_VALUES, b"FICTIONAL partial unique", b"",
                              expected_revision=1, expected_hash=row["bundle_sha256"])
        with closing(migration._connect(self.path)) as db:
            self.assertEqual(migration._snapshot(db, include_live=True), before)

    def test_source_confirmation_purge_retirement_and_pre_purge_restore_block_derived_outputs(self):
        identity = self.save_order()
        self.review_order(identity)
        export = self.export_order(identity)
        row = self.execute.view(identity)["row"]
        self.execute.measure(identity, 1, row["bundle_sha256"], {"baseline_minutes": "30", "elapsed_minutes": "8",
            "corrections": "1", "notes": "FICTIONAL correction", "baseline_ref": "FICTIONAL-BASELINE"})
        self.retention.backup(self.recovery / "before-purge.sqlite3", self.restore / "before-purge.sqlite3",
                              "2097-01-01T00:00:00Z")
        proof = self.intake.view(self.ids[0])["history"][0]["document_id"]
        plan = self.purge("DOCUMENT", proof)
        self.assertIn(identity, plan["affected_ids"]["live_order"])
        self.assertEqual(len(plan["affected_ids"]["export_copy_ids"]), 1)
        self.assertIn("BLOCKED", LiveExecute(LiveIntake(self.path, retention_ledger=self.ledger)).view(identity)["status"])
        with self.assertRaises(ValueError):
            self.execute.download(export)
        with self.assertRaisesRegex(ValueError, "quarantined"):
            self.retention.verify_restore(self.restore / "before-purge.sqlite3")
        # The external registry survives disposal of all live business data.
        retire = self.purge("PILOT", self.pilot_id)
        for table in migration.EXECUTE_TABLES:
            self.assertGreater(retire["affected_rows"][table], 0)
        with self.intake.transaction(write=False) as db:
            for table in migration.EXECUTE_TABLES:
                self.assertEqual(db.execute("SELECT count(*) FROM " + table).fetchone()[0], 0)
        self.assertEqual(self.snapshot(self.path), self.before)
        self.assertEqual(len(self.retention.inventory()["copies"]), 3)
        with self.assertRaises(ValueError):
            self.execute.download(export)

    def check_document_purge(self, which):
        identity = self.save_order()
        self.review_order(identity)
        export = self.export_order(identity)
        view = self.execute.view(identity)
        if which == "source":
            target = view["bundle"]["inputs"][1]["permission"]["document_id"]
        elif which == "facts_permission":
            target = view["row"]["permission_document_id"]
        elif which == "review":
            target = view["history"][0]["document_id"]
        else:
            target = view["row"]["document_id"]
        plan = self.purge("DOCUMENT", target)
        self.assertIn(identity, plan["affected_ids"]["live_order"])
        with self.assertRaises(ValueError):
            self.execute.download(export)
        self.assertIn("BLOCKED", self.execute.view(identity)["status"])

    def test_original_source_document_purge_invalidates_order(self):
        self.check_document_purge("source")

    def test_facts_document_purge_invalidates_order(self):
        self.check_document_purge("facts")

    def test_facts_permission_document_purge_invalidates_order(self):
        self.check_document_purge("facts_permission")

    def test_draft_approver_evidence_purge_invalidates_order(self):
        self.check_document_purge("review")

    def test_expired_source_facts_policy_and_withdrawal_are_rechecked_at_transitions(self):
        identity = self.save_order(retention_until_utc="2090-01-01T00:00:00Z")
        self.review_order(identity)
        export = self.export_order(identity, "2089-01-01T00:00:00Z")
        with patch("pilot_engine.live_intake._now", return_value=datetime(2091, 1, 1, tzinfo=timezone.utc)):
            with self.assertRaisesRegex(ValueError, "dolmuş"):
                self.execute.download(export)
        with patch("pilot_engine.live_retention._now", return_value=datetime(2100, 1, 1, tzinfo=timezone.utc)):
            with self.assertRaises(ValueError):
                self.execute.view(identity)
        current = self.intake.view(self.ids[2])["row"]
        self.intake.stop(self.ids[2], current["revision"], current["payload_sha256"], "FICTIONAL permission withdrawn")
        with self.assertRaisesRegex(ValueError, "geri çekilmiş"):
            self.execute.download(export)

    def test_viewer_synthetic_ids_wrong_kind_and_unreviewed_draft_cannot_cross_service(self):
        with self.assertRaises(ValueError):
            self.execute.selection(["SYN-P-001", *self.ids[1:]])
        with self.assertRaises(ValueError):
            self.execute.selection(list(reversed(self.ids)))
        with self.assertRaises(ValueError):
            self.save_order(source_ref="SYN-UNAUTHORIZED")
        viewer = LiveExecute(LiveIntake(self.path, LocalAccess({current_principal(): Role.VIEWER}), retention_ledger=self.ledger))
        _, digest = viewer.selection(self.ids)
        with self.assertRaises(PermissionError):
            viewer.save(self.ids, digest, FACT_VALUES, b"fixture", b"fixture")
        identity = self.save_order()
        with self.assertRaises(ValueError):
            self.review_order(identity, approver_name=intake_tests.AUTH["operator_name"])
        with self.assertRaises(ValueError):
            self.review_order(identity, reviewed="")
        self.assertEqual(self.snapshot(self.path), self.before)

    def test_export_pending_confirmation_wrong_bytes_disposal_expiry_and_review_revoke(self):
        identity = self.save_order()
        self.review_order(identity)
        export = self.export_order(identity)
        artifact, copy = self.execute.download(export)
        migration._new_file(Path(copy["path"]), b"wrong fixture file")
        with self.assertRaises(ValueError):
            self.execute.finish_export(export)
        Path(copy["path"]).unlink()
        migration._new_file(Path(copy["path"]), artifact)
        self.execute.finish_export(export)
        self.retention.dispose_copy(copy["id"], copy["sha256"], "FICTIONAL reviewed disposition", reviewed=True)
        with self.assertRaisesRegex(ValueError, "kopyası kullanılamaz"):
            self.execute.download(export)
        replacement = self.export_order(identity)
        self.review_order(identity, "REVOKE")
        with self.assertRaises(ValueError):
            self.execute.download(replacement)

    def test_bound_populated_v3_upgrade_registers_recovery_copies_and_preserves_existing_rows(self):
        with closing(migration._connect(self.path)) as db, db:
            for table in ("live_order_export", "live_order_measurement", "live_order_review", "live_order_input",
                          "live_order_revision", "live_order"):
                db.execute("DROP TABLE " + table)
            guard = db.execute("SELECT sql FROM sqlite_master WHERE name='live_schema_migration_no_delete'").fetchone()[0]
            db.execute("DROP TRIGGER live_schema_migration_no_delete")
            receipt = db.execute("SELECT * FROM live_schema_migration WHERE version=4").fetchone()
            db.execute("DELETE FROM live_schema_migration WHERE version=4")
            db.execute("INSERT INTO live_schema_migration VALUES (3,?,?,?,?)", receipt[1:])
            db.execute(guard)
            before = migration._snapshot(db, include_live=True)
        backup, restore = self.recovery / "upgrade-v4.sqlite3", self.restore / "upgrade-v4.sqlite3"
        with self.assertRaisesRegex(ValueError, "current retention ledger"):
            migration.migrate(self.path, backup, restore)
        self.assertFalse(backup.exists())
        result = migration.migrate(self.path, backup, restore, retention_ledger=self.ledger,
                                   copies_until="2097-01-01T00:00:00Z")
        self.assertEqual(result.live_version, 4)
        for path in (backup, restore):
            with closing(migration._connect(path)) as db:
                self.assertEqual(migration._snapshot(db, include_live=True), before)
        self.assertEqual(len(self.retention.inventory()["copies"]), 2)
        self.assertTrue(all(copy["state"] == "ACTIVE" for copy in self.retention.inventory()["copies"]))
        self.save_order()

    def test_interrupted_export_registration_reuses_pending_copy_on_retry(self):
        identity = self.save_order()
        self.review_order(identity)
        original = self.retention.ledger

        @contextmanager
        def interrupt(*, exclusive=False):
            with original(exclusive=exclusive) as ledger:
                yield ledger
                with closing(migration._connect(self.path)) as db:
                    exists = db.execute("SELECT count(*) FROM live_order_export").fetchone()[0]
                if exclusive and exists:
                    raise RuntimeError("FICTIONAL interrupted ledger finalization")

        with patch.object(self.intake.retention, "ledger", interrupt):
            with self.assertRaisesRegex(RuntimeError, "interrupted ledger"):
                self.export_order(identity)
        pending = self.retention.inventory()["copies"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["state"], "PENDING")
        self.assertIsNone(pending[0]["sha256"])
        export = self.export_order(identity)
        self.assertTrue(self.execute.download(export)[0])
        self.assertEqual(len(self.retention.inventory()["copies"]), 1)
        self.assertEqual(len(self.execute.view(identity)["exports"]), 1)

    def test_new_live_schema_ddl_failure_leaves_bound_v3_and_registered_recovery_intact(self):
        with closing(migration._connect(self.path)) as db, db:
            for table in ("live_order_export", "live_order_measurement", "live_order_review", "live_order_input",
                          "live_order_revision", "live_order"):
                db.execute("DROP TABLE " + table)
            guard = db.execute("SELECT sql FROM sqlite_master WHERE name='live_schema_migration_no_delete'").fetchone()[0]
            db.execute("DROP TRIGGER live_schema_migration_no_delete")
            receipt = db.execute("SELECT * FROM live_schema_migration WHERE version=4").fetchone()
            db.execute("DELETE FROM live_schema_migration WHERE version=4")
            db.execute("INSERT INTO live_schema_migration VALUES (3,?,?,?,?)", receipt[1:])
            db.execute(guard)
            before = migration._snapshot(db, include_live=True)
        original = migration._apply

        def fail(db, current):
            original(db, current)
            raise RuntimeError("FICTIONAL Execute DDL failure")

        backup, restore = self.recovery / "failed-v4.sqlite3", self.restore / "failed-v4.sqlite3"
        with patch.object(migration, "_apply", fail), self.assertRaisesRegex(RuntimeError, "DDL failure"):
            migration.migrate(self.path, backup, restore, retention_ledger=self.ledger,
                              copies_until="2097-01-01T00:00:00Z")
        with closing(migration._connect(self.path)) as db:
            self.assertEqual(migration._live_version(db), 3)
            self.assertEqual(migration._snapshot(db, include_live=True), before)
        for path in (backup, restore):
            with closing(migration._connect(path)) as db:
                self.assertEqual(migration._snapshot(db, include_live=True), before)
        self.assertEqual([copy["state"] for copy in self.retention.inventory()["copies"]], ["PENDING", "PENDING"])
        with self.assertRaisesRegex(ValueError, "v4"):
            LiveExecute(self.intake)

    def test_guided_http_selection_save_review_and_authenticated_attachment(self):
        app = LiveOperator(self.path, retention_ledger=self.ledger)
        with ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app)) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = HTTPConnection("127.0.0.1", server.server_port)
            try:
                connection.request("GET", "/?token=" + app.session)
                response = connection.getresponse()
                cookie = response.getheader("Set-Cookie").split(";", 1)[0]
                response.read()
                headers = {"Cookie": cookie}
                for route in ("/", "/execute", "/execute/new", "/execute/new?" + urlencode(dict(zip(INPUT_KINDS, self.ids)))):
                    connection.request("GET", route, headers=headers)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200, response.read())
                # Exercise actual multipart routes rather than calling the web adapter directly.
                def post(path, values, files=None):
                    boundary = "fictional-execute-boundary"
                    parts = []
                    for key, value in {"csrf": app.csrf, "confirm": "yes", **values}.items():
                        parts.append(("--" + boundary + '\r\nContent-Disposition: form-data; name="' + key + '"\r\n\r\n' + str(value) + "\r\n").encode())
                    for key, value in (files or {}).items():
                        parts.append(("--" + boundary + '\r\nContent-Disposition: form-data; name="' + key + '"; filename="fixture.bin"\r\n\r\n').encode() + value + b"\r\n")
                    connection.request("POST", path, b"".join(parts) + ("--" + boundary + "--\r\n").encode(),
                        headers={**headers, "Content-Type": "multipart/form-data; boundary=" + boundary})
                    response = connection.getresponse()
                    payload = response.read()
                    self.assertEqual(response.status, 303, payload)
                    return response.getheader("Location")
                _, digest = self.execute.selection(self.ids)
                location = post("/execute/save", {**FACT_VALUES, **dict(zip(INPUT_KINDS, self.ids)),
                    "inputs_hash": digest, "revision": "0", "seller_address": "<script>FICTIONAL</script>"},
                    {"facts_document": b"FICTIONAL HTTP facts", "permission_document": b"FICTIONAL HTTP permission"})
                identity = location.split("id=")[1]
                row = self.execute.view(identity)["row"]
                fixed = {"order_id": identity, "revision": 1, "digest": row["bundle_sha256"]}
                post("/execute/review", {**fixed, "sequence": 0, "approver_name": intake_tests.AUTH["approver_name"],
                    "reviewed": "yes", "decision": "APPROVE", "reason": "FICTIONAL review", "method": "FICTIONAL written",
                    "evidence_ref": "FICTIONAL-HTTP-REVIEW", "confirmed_at_utc": _stamp()},
                    {"review_document": b"FICTIONAL HTTP review proof"})
                post("/execute/export", {**fixed, "directory": str(self.recovery), "expires_at_utc": "2097-01-01T00:00:00Z"})
                export = self.execute.view(identity)["exports"][0]["id"]
                connection.request("GET", location, headers=headers)
                response = connection.getresponse()
                body = response.read().decode()
                self.assertEqual(response.status, 200)
                self.assertIn("&lt;script&gt;FICTIONAL&lt;/script&gt;", body)
                self.assertIn("OPERATÖR BEYANI", body)
                connection.request("GET", "/execute/download?id=" + export, headers=headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn(export + ".html", response.getheader("Content-Disposition"))
                self.assertIn(b"PACKING_LIST_DRAFT", response.read())
                connection.request("GET", "/execute/download?id=" + export)
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
            finally:
                connection.close()
                server.shutdown()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
