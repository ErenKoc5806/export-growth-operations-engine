"""All authorizations/documents here are fictional, in disposable local test DBs."""

import io
import json
import os
import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlencode
from unittest.mock import patch

import test_live_migration as migration_tests
from pilot_engine import live_migration as migration
from pilot_engine.access import LocalAccess, Role, current_principal
from pilot_engine.live_intake import LiveIntake, _stamp
from pilot_engine.live_operator import LiveOperator
from pilot_engine.operator_web import make_handler


AUTH = {"manufacturer_name": "FICTIONAL TEST ONLY Manufacturer", "authorization_ref": "TEST-AUTHORIZATION",
    "operator_name": "Fixture Operator", "approver_name": "Fixture Approver", "approver_role": "Fixture Director",
    "authorizer_name": "Fixture Director", "it_reviewer": "Fixture IT", "baseline": "Fictional 30 minutes",
    "backup_owner": "Fixture Operator", "expires_at_utc": "2099-01-01T00:00:00Z",
    "manufacturer_accepted": "yes", "it_accepted": "yes", "trust_accepted": "yes", "permission_accepted": "yes"}
PROFILE = {"sku": "FIXTURE-ONLY", "product_name": "Fictional clamp", "dimensions": "Fictional drawing",
    "material": "Fictional steel", "intended_use": "Fictional review", "hs6": "732690",
    "classification_ref": "TEST-HS-REVIEW", "source_ref": "TEST-SOURCE-1", "permission_ref": "TEST-PERMISSION",
    "terms_ref": "TEST-TERMS", "processing_basis": "Fictional permission", "reviewer_name": "Fixture Approver",
    "retention_until_utc": "2099-01-01T00:00:00Z"}
DOCUMENT = b"FICTIONAL TEST ONLY: source bytes; not manufacturer evidence"
PERMISSION = b"FICTIONAL TEST ONLY: usage permission; no real authorization"


class LiveIntakeTests(unittest.TestCase):
    snapshot = migration_tests.LiveMigrationTests.snapshot

    def setUp(self):
        migration_tests.LiveMigrationTests.setUp(self)
        migration.migrate(self.path, self.backup, self.rehearsal)
        self.intake = LiveIntake(self.path)

    def enroll(self):
        return self.intake.enroll(dict(AUTH), b"FICTIONAL manufacturer approval", b"FICTIONAL IT approval")

    def save(self, **changes):
        return self.intake.save("PRODUCT_PROFILE", {**PROFILE, **changes}, DOCUMENT, PERMISSION)

    def confirm(self, identity, decision="APPROVE", **changes):
        view = self.intake.view(identity)
        values = {"approver_name": AUTH["approver_name"], "decision": decision,
                  "reviewed": "yes", "reason": "Fictional review only", "method": "Fixture written confirmation",
                  "evidence_ref": "TEST-CONFIRMATION", "confirmed_at_utc": _stamp(), **changes}
        self.intake.confirm(identity, view["row"]["revision"], view["row"]["payload_sha256"],
                            view["sequence"], values, b"FICTIONAL TEST ONLY: exact-version confirmation")

    def test_no_intake_without_complete_explicit_authorization_and_distinct_people(self):
        with self.assertRaisesRegex(ValueError, "#13"):
            self.save()
        for changes in ({"it_accepted": ""}, {"trust_accepted": ""},
                        {"approver_name": AUTH["operator_name"]}, {"authorization_ref": "SYN-AUTH"},
                        {"expires_at_utc": "2020-01-01T00:00:00Z"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.intake.enroll({**AUTH, **changes}, b"fixture", b"fixture")
        with self.assertRaises(ValueError):
            self.intake.enroll(AUTH, b"", b"fixture")
        self.assertIsNone(self.intake.view()["pilot"])
        self.enroll()
        with self.assertRaisesRegex(ValueError, "tek üretici"):
            self.enroll()

    def test_exact_version_confirmation_revision_rejection_revocation_and_history(self):
        self.enroll()
        identity = self.save()
        self.assertEqual(self.intake.view(identity)["status"], "REVIEW_REQUIRED")
        self.confirm(identity)
        old = self.intake.view(identity)
        self.assertEqual(old["status"], "APPROVE")
        self.intake.save("PRODUCT_PROFILE", {**PROFILE, "source_ref": "TEST-SOURCE-2", "material": "Revised fictional steel"},
                         b"FICTIONAL revision 2", PERMISSION, intake_id=identity,
                         expected_revision=1, expected_hash=old["row"]["payload_sha256"])
        new = self.intake.view(identity)
        self.assertEqual(new["status"], "REVIEW_REQUIRED")
        with self.assertRaisesRegex(ValueError, "Güncel tam sürüm"):
            self.intake.confirm(identity, 1, old["row"]["payload_sha256"], old["sequence"], {}, b"fixture")
        self.confirm(identity, "REJECT")
        self.assertEqual(self.intake.view(identity)["status"], "REJECT")
        self.confirm(identity)
        self.confirm(identity, "REVOKE")
        current = LiveIntake(self.path).view(identity)
        self.assertEqual(current["status"], "REVOKE")
        self.assertEqual([item["decision"] for item in current["history"]],
                         ["REVOKE", "APPROVE", "REJECT", "APPROVE"])
        event = current["history"][0]
        self.assertNotEqual(event["recorded_by_actor"], event["approver_name"])
        self.assertEqual(event["recorded_by_actor"], self.intake.actor)
        self.assertEqual(event["identity_basis"], "OPERATOR_ATTESTATION")
        self.assertEqual(event["approver_role"], AUTH["approver_role"])

    def test_missing_permission_synthetic_references_bad_scope_and_expiry_block_writes(self):
        self.enroll()
        for changes in ({"permission_ref": ""}, {"terms_ref": "SYN-TERMS"},
                        {"source_ref": "SYN-SOURCE"}, {"reviewer_name": "Unlisted person"},
                        {"hs6": "000000"}, {"retention_until_utc": "2020-01-01T00:00:00Z"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.save(**changes)
        with self.assertRaises(ValueError):
            self.intake.save("PRODUCT_PROFILE", PROFILE, DOCUMENT, b"")
        self.assertEqual(self.intake.view()["items"], [])

    def test_denied_role_and_changed_os_actor_cannot_write_or_impersonate_approver(self):
        self.enroll()
        identity = self.save()
        viewer = LiveIntake(self.path, LocalAccess({current_principal(): Role.VIEWER}))
        self.assertTrue(viewer.view(identity))
        with self.assertRaises(PermissionError):
            viewer.save("PRODUCT_PROFILE", PROFILE, DOCUMENT, PERMISSION)
        with self.assertRaises(PermissionError):
            viewer.confirm(identity, 1, "a" * 64, 0, {}, b"fixture")
        with patch("pilot_engine.access.current_principal", return_value="different-os-account"):
            with self.assertRaises(PermissionError):
                self.intake.view(identity)
        for changes in ({"approver_name": AUTH["operator_name"]}, {"reviewed": ""},
                        {"evidence_ref": "SYN-APPROVAL"}, {"confirmed_at_utc": "2099-01-01T00:00:00Z"},
                        {"confirmed_at_utc": "2020-01-01T00:00:00Z"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.confirm(identity, **changes)

    def test_stale_revision_or_confirmation_form_cannot_overwrite_a_later_decision(self):
        self.enroll()
        identity = self.save()
        old = self.intake.view(identity)
        self.confirm(identity)
        with self.assertRaisesRegex(ValueError, "Onay durumu değişti"):
            self.intake.confirm(identity, 1, old["row"]["payload_sha256"], 0, {}, b"fixture")
        with self.assertRaisesRegex(ValueError, "Kayıt sürümü değişti"):
            self.intake.save("PRODUCT_PROFILE", {**PROFILE, "source_ref": "TEST-SOURCE-2"}, DOCUMENT,
                PERMISSION, intake_id=identity, expected_revision=9, expected_hash=old["row"]["payload_sha256"])
        with self.assertRaisesRegex(ValueError, "türü"):
            self.intake.save("BUYER_SOURCE", {**PROFILE, "source_ref": "TEST-SOURCE-2", "company": "Fixture",
                "country": "DE", "website": "https://fixture-buyer.de", "role": "Distributor", "fit_evidence": "Fixture"},
                DOCUMENT, PERMISSION, intake_id=identity, expected_revision=1, expected_hash=old["row"]["payload_sha256"])

    def test_source_and_pilot_stop_and_elapsed_retention_block_approval(self):
        self.enroll()
        retention = datetime.now(timezone.utc) + timedelta(minutes=5)
        identity = self.save(retention_until_utc=retention.isoformat())
        self.confirm(identity)
        current = self.intake.view(identity)
        expiry = retention + timedelta(minutes=1)
        with patch("pilot_engine.live_intake._now", return_value=expiry):
            self.assertTrue(self.intake.view(identity)["status"].startswith("BLOCKED"))
        self.intake.stop(identity, 1, current["row"]["payload_sha256"], "Fixture source permission withdrawn")
        self.assertTrue(self.intake.view(identity)["status"].startswith("BLOCKED"))
        with self.assertRaisesRegex(ValueError, "geri çekilmiş"):
            self.confirm(identity)
        self.intake.stop(None, 0, "", "Fixture pilot stopped")
        with self.assertRaisesRegex(ValueError, "durdurulmuş"):
            self.save(source_ref="TEST-SOURCE-2")

    def test_all_four_intake_types_validate_without_external_workflow_writes(self):
        self.enroll()
        self.save()
        price = {"sku": "FIXTURE-ONLY", "unit_price": "10.00", "currency": "EUR", "unit": "PCS",
            "payment_terms": "Fictional advance", "incoterm": "FCA Fictional city", "tax_authority_ref": "TEST-TAX",
            "valid_until_utc": "2098-01-01T00:00:00Z"}
        buyer = {"company": "Fixture buyer", "country": "DE", "website": "https://fixture-buyer.de",
            "role": "Distributor", "fit_evidence": "FICTIONAL TEST ONLY"}
        po = {"po_number": "FIXTURE-PO", "buyer": "Fixture buyer", "sku": "FIXTURE-ONLY", "quantity": "2",
            "unit": "PCS", "unit_price": "10.00", "currency": "EUR", "total": "20.00",
            "payment_terms": "Fictional advance", "incoterm": "FCA Fictional city", "delivery": "Fixture date"}
        for kind, data in (("PRICE_AUTHORITY", price), ("BUYER_SOURCE", buyer), ("CUSTOMER_PO", po)):
            identity = self.intake.save(kind, {**PROFILE, **data, "source_ref": "TEST-" + kind}, DOCUMENT, PERMISSION)
            self.confirm(identity)
            self.assertEqual(self.intake.view(identity)["status"], "APPROVE")
        for kind, data in (("BUYER_SOURCE", {**buyer, "website": "https://sales.example.org"}),
                           ("PRICE_AUTHORITY", {**price, "unit_price": "NaN"}),
                           ("PRICE_AUTHORITY", {**price, "unit_price": "1e999999"}),
                           ("CUSTOMER_PO", {**po, "total": "999"})):
            with self.assertRaises(ValueError):
                self.intake.validate(kind, data)
        self.assertEqual(self.snapshot(self.path), self.before)

    def test_evidence_immutable_and_partial_upload_failure_rolls_back_everything(self):
        self.enroll()
        with self.intake.transaction(write=False) as db:
            before = db.execute("SELECT count(*) FROM live_document").fetchone()[0]
        with self.assertRaises(ValueError):
            self.intake.save("PRODUCT_PROFILE", PROFILE, DOCUMENT, b"")
        with self.intake.transaction(write=False) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM live_document").fetchone()[0], before)
        identity = self.save()
        self.confirm(identity)
        with self.intake.transaction() as db:
            for table in migration.EVIDENCE_TABLES - {"live_intake_stop"}:
                with self.subTest(table=table), self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    db.execute("DELETE FROM " + table)

    def test_changed_confirmation_document_cannot_still_show_approved(self):
        self.enroll()
        identity = self.save()
        self.confirm(identity)
        proof = self.intake.view(identity)["history"][0]["document_id"]
        # Simulate valid-SQLite but corrupted evidence; an owner can bypass guards.
        with closing(migration._connect(self.path)) as db, db:
            db.execute("DROP TRIGGER live_document_no_update")
            db.execute("UPDATE live_document SET content=? WHERE id=?", (b"corrupted evidence", proof))
        self.assertIn("BLOCKED", self.intake.view(identity)["status"])

    def test_visual_forms_http_csrf_source_isolation_and_private_document_download(self):
        app = LiveOperator(self.path)
        with ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app)) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = HTTPConnection("127.0.0.1", server.server_port)
            try:
                connection.request("GET", "/?token=" + app.session)
                response = connection.getresponse()
                cookie = response.getheader("Set-Cookie").split(";", 1)[0]
                self.assertEqual(response.status, 303)
                response.read()
                connection.request("GET", "/", headers={"Cookie": cookie})
                response = connection.getresponse()
                body = response.read().decode()
                self.assertIn("Üretici yetkilendirme", body)
                self.assertNotIn("TEST VERİSİ", body)
                self.assertIn("CANLI KAYNAK KABULÜ", body)
                def post_form(path, values, files):
                    boundary = "fixture-boundary-773"
                    body = b""
                    for name, value in {"csrf": app.csrf, "confirm": "yes", **values}.items():
                        body += ("--" + boundary + '\r\nContent-Disposition: form-data; name="' +
                                 name + '"\r\n\r\n' + str(value) + "\r\n").encode()
                    for name, value in files.items():
                        body += ("--" + boundary + '\r\nContent-Disposition: form-data; name="' +
                                 name + '"; filename="fixture.bin"\r\nContent-Type: application/octet-stream\r\n\r\n').encode() + value + b"\r\n"
                    body += ("--" + boundary + "--\r\n").encode()
                    connection.request("POST", path, body, headers={"Cookie": cookie,
                        "Content-Type": "multipart/form-data; boundary=" + boundary})
                    response = connection.getresponse()
                    payload = response.read()
                    self.assertEqual(response.status, 303, payload)
                    return response.getheader("Location")

                post_form("/enroll", AUTH, {"manufacturer_document": b"FICTIONAL manufacturer approval",
                                           "it_document": b"FICTIONAL IT approval"})
                location = post_form("/save", {**PROFILE, "product_name": "<script>escaped fixture</script>",
                    "kind": "PRODUCT_PROFILE", "revision": "0"},
                    {"source_document": DOCUMENT, "permission_document": PERMISSION})
                identity = location.split("id=")[1]
                connection.request("GET", "/record?id=" + identity, headers={"Cookie": cookie})
                response = connection.getresponse()
                body = response.read().decode()
                self.assertIn("&lt;script&gt;", body)
                self.assertIn("OPERATÖR BEYANI", body)
                self.assertIn("İnceleme bekliyor", body)
                self.assertIn("Kaynak iznini geri çekme", body)
                view = self.intake.view(identity)
                post_form("/confirm", {"intake_id": identity, "revision": 1,
                    "digest": view["row"]["payload_sha256"], "sequence": 0,
                    "approver_name": AUTH["approver_name"], "decision": "APPROVE", "reviewed": "yes",
                    "reason": "Fictional HTTP form review", "method": "Fixture written confirmation",
                    "evidence_ref": "TEST-HTTP-CONFIRMATION", "confirmed_at_utc": _stamp()},
                    {"confirmation_document": b"FICTIONAL TEST ONLY: reviewed version proof"})
                self.assertEqual(self.intake.view(identity)["status"], "APPROVE")
                headers = {"Cookie": cookie, "Content-Type": "application/x-www-form-urlencoded"}
                for values, changed_headers, expected in (
                    ({"confirm": "yes", "csrf": "wrong"}, {}, 403),
                    ({"confirm": "yes", "csrf": app.csrf}, {"Origin": "https://attacker.invalid"}, 403),
                    ({"confirm": "yes", "csrf": app.csrf}, {}, 400)):
                    connection.request("POST", "/demo", urlencode(values), headers={**headers, **changed_headers})
                    response = connection.getresponse()
                    self.assertEqual(response.status, expected, response.read())
                    response.read()
                with self.intake.transaction(write=False) as db:
                    doc = db.execute("SELECT document_id FROM live_source_permission").fetchone()[0]
                connection.request("GET", "/document?id=" + doc, headers={"Cookie": cookie})
                response = connection.getresponse()
                self.assertEqual(response.getheader("Content-Type"), "application/octet-stream")
                self.assertIn("attachment", response.getheader("Content-Disposition"))
                self.assertEqual(response.read(), DOCUMENT)
                connection.request("GET", "/document?id=" + doc)
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
            finally:
                connection.close()
                server.shutdown()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
