import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError, URLError

from pilot_engine.access import LocalAccess, Role
from pilot_engine.market_signals import MarketSignals
from pilot_engine.store import PilotStore
from pilot_engine.retry import ReadRetryPolicy


def response(data):
    return io.BytesIO(json.dumps(data).encode("utf-8"))


class MarketSignalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "market.sqlite3"
        self.signals = MarketSignals(PilotStore(self.path))

    def test_public_preview_records_reproducible_aggregate_without_buyer_claim(self):
        seen = []

        def opener(request, timeout):
            seen.append((request.full_url, timeout))
            return response({"data": [{"cmdCode": "732690", "reporterCode": 276,
                                       "partnerCode": 0, "flowCode": "M", "period": 2024,
                                       "primaryValue": 1250000, "netWgt": 3456,
                                       "cmdDesc": "Other articles of iron or steel",
                                       "isNetWgtEstimated": True, "isReported": False,
                                       "isAggregate": True, "classificationCode": "H6"}]})

        result = self.signals.fetch_public_preview(2024, opener=opener)
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual((result["trade_value_usd_text"], result["unit"]), ("1250000", "USD"))
        self.assertEqual(result["net_weight_kg_text"], "3456")
        self.assertEqual(result["query"]["reporterCode"], "276")
        self.assertEqual(result["query"]["partnerCode"], "0")
        self.assertEqual(result["query"]["flowCode"], "M")
        self.assertIn("cmdCode=732690", seen[0][0])
        self.assertEqual(seen[0][1], 15)
        self.assertFalse(result["buyer_evidence"])
        self.assertTrue(result["direct_company_research_allowed"])
        self.assertTrue(result["raw_sha256"])
        self.assertTrue(result["quality"]["isNetWgtEstimated"])
        self.assertFalse(result["quality"]["isReported"])
        self.assertIn("not identify a buyer", " ".join(result["limitations"]))
        self.assertEqual(self.signals.read(result["id"])["status"], "AVAILABLE")
        with closing(sqlite3.connect(self.path)) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("DELETE FROM market_signal_snapshot")

    def test_missing_failure_and_ambiguous_are_visible(self):
        missing = self.signals.fetch_public_preview(2024, opener=lambda *_args, **_kw: response({"data": []}))
        self.assertEqual((missing["status"], missing["trade_value_usd_text"]), ("MISSING", None))

        def failed(*_args, **_kwargs):
            raise URLError("network unavailable")

        failed_result = self.signals.fetch_public_preview(2024, opener=failed, sleep=lambda _: None)
        self.assertEqual((failed_result["status"], failed_result["failure_category"]),
                         ("FAILED", "TRANSIENT_READ"))
        self.assertNotIn("network unavailable", str(failed_result))
        row = {"cmdCode": "732690", "reporterCode": 276, "partnerCode": 0,
               "flowCode": "M", "period": 2024, "primaryValue": 99,
               "customsCode": "C00", "motCode": 0, "partner2Code": 0,
               "isAggregate": True}
        ambiguous = self.signals.fetch_public_preview(
            2024, opener=lambda *_args, **_kw: response({"data": [row, row]}))
        self.assertEqual((ambiguous["status"], ambiguous["failure_category"]),
                         ("FAILED", "AMBIGUOUS_ROWS"))
        wrong = self.signals.fetch_public_preview(
            2024, opener=lambda *_args, **_kw: response({"data": [
                {"cmdCode": "732690", "reporterCode": 792, "partnerCode": 0,
                 "flowCode": "X", "period": 2024, "primaryValue": 99}]}))
        self.assertEqual(wrong["failure_category"], "INVALID_RESPONSE")

        detail = {**row, "customsCode": "C01", "isAggregate": False, "primaryValue": 10}
        split = self.signals.fetch_public_preview(
            2024, opener=lambda *_args, **_kw: response({"data": [row, detail]}))
        self.assertEqual(split["status"], "AVAILABLE")
        self.assertEqual(split["trade_value_usd_text"], "99")
        self.assertEqual(split["quality"]["responseRows"], 2)

    def test_retry_only_temporary_public_reads(self):
        attempts, delays = [], []
        row = {"cmdCode": "732690", "reporterCode": 276, "partnerCode": 0,
               "flowCode": "M", "period": 2024, "primaryValue": 42}

        def flaky(*_args, **_kwargs):
            attempts.append(1)
            if len(attempts) < 3:
                raise URLError("temporary")
            return response({"data": [row]})

        result = self.signals.fetch_public_preview(
            2024, opener=flaky, retry_policy=ReadRetryPolicy(max_attempts=3),
            sleep=delays.append)
        self.assertEqual((result["status"], len(attempts), delays),
                         ("AVAILABLE", 3, [0.5, 1.0]))
        rejected = self.signals.fetch_public_preview(
            2024, opener=lambda *_args, **_kw: (_ for _ in ()).throw(
                HTTPError("https://example.org", 403, "forbidden", None, None)),
            sleep=delays.append)
        self.assertEqual(rejected["failure_category"], "HTTP_ERROR")

    def test_manual_evidence_and_scope_validation(self):
        snapshot = self.signals.record_manual(
            year=2023, source_url="https://example.org/research-record",
            observed_at_utc="2026-10-04T12:00:00+00:00", trade_value_usd="1500.25",
            description="Broad HS6 aggregate", source_note="Operator copied published total")
        self.assertEqual(snapshot["source_system"], "MANUAL")
        self.assertEqual(snapshot["trade_value_usd_text"], "1500.25")
        with self.assertRaises(ValueError):
            self.signals.record_manual(
                year=2023, source_url="http://example.org", observed_at_utc="2026-10-04",
                trade_value_usd="-1", description="X", source_note="X")
        with self.assertRaisesRegex(ValueError, "UTC"):
            self.signals.record_manual(
                year=2023, source_url="https://example.org", observed_at_utc="2026-10-04",
                trade_value_usd="1", description="X", source_note="X")
        with self.assertRaisesRegex(ValueError, "reporting year"):
            self.signals.fetch_public_preview(True)

    def test_access_and_v5_upgrade(self):
        viewer = MarketSignals(PilotStore(self.path, access=LocalAccess({os.geteuid(): Role.VIEWER})))
        with self.assertRaises(PermissionError):
            viewer.record_manual(
                year=2024, source_url="https://example.org", observed_at_utc="2026-10-04T00:00:00Z",
                trade_value_usd=None, description="No data", source_note="Source checked")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DROP TABLE contact_suppression_rule")
            db.execute("DROP TABLE find_handoff_revision")
            db.execute("DROP TABLE find_handoff")
            db.execute("DROP TABLE contact_route_check")
            for table in ("contact_route_observation", "contact_route_correction", "discovered_contact_route",
                          "contact_route_absence", "contact_suppression", "contact_collection_policy"):
                db.execute(f"DROP TABLE {table}")
            db.execute("DROP TRIGGER profile_source_no_update")
            db.execute("DROP TRIGGER profile_source_no_delete")
            db.execute("DROP TABLE profile_source_document")
            db.execute("DROP TRIGGER buyer_fit_no_update")
            db.execute("DROP TRIGGER buyer_fit_no_delete")
            db.execute("DROP TABLE buyer_fit_decision")
            db.execute("DROP TRIGGER candidate_evidence_no_update")
            db.execute("DROP TRIGGER candidate_evidence_no_delete")
            db.execute("DROP TRIGGER candidate_no_delete")
            db.execute("DROP TABLE buyer_candidate_alias")
            db.execute("DROP TABLE buyer_candidate_evidence")
            db.execute("DROP TABLE buyer_candidate")
            db.execute("DROP TRIGGER market_signal_no_update")
            db.execute("DROP TRIGGER market_signal_no_delete")
            db.execute("DROP TABLE market_signal_snapshot")
            db.execute("PRAGMA user_version = 5")
        PilotStore(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 13)


if __name__ == "__main__":
    unittest.main()
