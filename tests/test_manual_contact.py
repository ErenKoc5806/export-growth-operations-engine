import sqlite3
import unittest
from contextlib import closing

from pilot_engine.find_handoff import FindHandoff
from pilot_engine.manual_contact import ManualContact
from pilot_engine.store import PilotStore
import test_sell_drafts


class ManualContactTests(unittest.TestCase):
    def setUp(self):
        test_sell_drafts.SellDraftTests.setUp(self)
        self.manual = ManualContact(self.store)
        self.routes.authorize_source(
            source_system="EXAMPLE_SITE", source_ref="FORM-1",
            source_url="https://example.org/contact", data_class="BUSINESS_ROUTE",
            decision="ALLOW", processing_basis="OTHER_REVIEWED",
            lawful_basis_ref="SYNTHETIC-BASIS", source_terms_ref="SYNTHETIC-TERMS",
            retention_until_utc="2030-01-01T00:00:00Z")
        form = self.routes.record(
            self.product_id, self.candidate_id, kind="CONTACT_FORM",
            value="https://example.org/contact/form", source_system="EXAMPLE_SITE",
            source_ref="FORM-1", source_url="https://example.org/contact",
            observed_at_utc="2026-10-04T00:00:00Z")
        self.form_id = form["id"]
        self.routes.check(self.form_id, method="MANUAL_PAGE", result="ROUTE_CONFIRMED",
                          source_url="https://example.org/contact",
                          checked_at_utc="2026-10-04T00:00:00Z", explanation="Invented form route")
        self.form_handoff, _ = FindHandoff(self.store).record(
            self.product_id, self.candidate_id, self.form_id)
        self.opportunity = self.manual.open_opportunity(self.form_handoff)
        self.plan_args = dict(operation_key="synthetic-form-1",
                              expected_destination="https://example.org/contact/form",
                              summary="Operator plans an invented catalog inquiry",
                              planned_at_utc="2026-10-05T08:00:00Z", reviewed_claims=True)

    def test_planned_attempted_unknown_and_idempotent_events(self):
        action = self.manual.plan(self.opportunity, self.form_handoff, **self.plan_args)
        self.assertEqual(self.manual.plan(self.opportunity, self.form_handoff, **self.plan_args), action)
        planned = self.manual.read(action)
        self.assertEqual(planned["status"], "PLANNED")
        self.assertFalse(planned["performed_by_system"])
        with self.assertRaisesRegex(ValueError, "reused"):
            self.manual.plan(self.opportunity, self.form_handoff,
                             **{**self.plan_args, "summary": "Different action"})
        event = dict(event_key="form-outcome-1", outcome="ATTEMPTED",
                     occurred_at_utc="2026-10-05T08:10:00Z",
                     notes="Operator reports pressing submit; receipt not verified",
                     next_step="Check manually for a response", performed_by_operator=True)
        first = self.manual.record(action, **event)
        self.assertEqual(self.manual.record(action, **event), first)
        self.assertEqual(self.manual.read(action)["status"], "ATTEMPTED")
        with self.assertRaisesRegex(ValueError, "not a connected"):
            self.manual.record(action, event_key="form-false-connected", outcome="CONNECTED",
                               occurred_at_utc="2026-10-05T08:20:00Z",
                               notes="Confirmation page only", next_step="Wait",
                               performed_by_operator=True)
        self.manual.record(action, event_key="form-outcome-2", outcome="UNKNOWN",
                           occurred_at_utc="2026-10-05T08:20:00Z",
                           notes="No receipt or buyer response known",
                           next_step="Check the form manually", performed_by_operator=True)
        record = self.manual.read(action)
        self.assertEqual(len(record["events"]), 2)
        self.assertEqual(record["status"], "UNKNOWN")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 25)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("UPDATE manual_contact_event SET outcome = 'UNKNOWN'")

    def test_phone_connection_is_an_operator_report_not_a_transcript(self):
        self.routes.authorize_source(
            source_system="EXAMPLE_SITE", source_ref="PHONE-1",
            source_url="https://example.org/contact", data_class="PERSONAL_ROUTE",
            decision="ALLOW", processing_basis="OTHER_REVIEWED",
            lawful_basis_ref="SYNTHETIC-BASIS", source_terms_ref="SYNTHETIC-TERMS",
            retention_until_utc="2030-01-01T00:00:00Z")
        phone = self.routes.record(
            self.product_id, self.candidate_id, kind="SWITCHBOARD", value="+491234567890",
            source_system="EXAMPLE_SITE", source_ref="PHONE-1",
            source_url="https://example.org/contact", observed_at_utc="2026-10-04T00:00:00Z")
        self.routes.check(phone["id"], method="MANUAL_PAGE", result="ROUTE_CONFIRMED",
                          source_url="https://example.org/contact",
                          checked_at_utc="2026-10-04T00:00:00Z", explanation="Invented switchboard")
        handoff, _ = FindHandoff(self.store).record(self.product_id, self.candidate_id, phone["id"])
        action = self.manual.plan(
            self.opportunity, handoff, operation_key="synthetic-phone-1",
            expected_destination="+491234567890", summary="Operator plans an invented call",
            planned_at_utc="2026-10-05T08:00:00Z", reviewed_claims=True)
        self.manual.record(action, event_key="phone-result-1", outcome="CONNECTED",
                           occurred_at_utc="2026-10-05T08:10:00Z", notes="Operator reports a conversation",
                           next_step="Record a reply separately", performed_by_operator=True)
        self.assertEqual(self.manual.read(action)["status"], "CONNECTED")
        with self.assertRaisesRegex(ValueError, "final"):
            self.manual.record(action, event_key="phone-result-2", outcome="ATTEMPTED",
                               occurred_at_utc="2026-10-05T08:20:00Z", notes="Again",
                               next_step="None", performed_by_operator=True)

    def test_review_gate_no_system_action_and_suppression(self):
        with self.assertRaisesRegex(ValueError, "review"):
            self.manual.plan(self.opportunity, self.form_handoff,
                             **{**self.plan_args, "reviewed_claims": False})
        with self.assertRaisesRegex(ValueError, "Destination changed"):
            self.manual.plan(self.opportunity, self.form_handoff,
                             **{**self.plan_args, "expected_destination": "https://example.org/evil"})
        with self.assertRaisesRegex(ValueError, "phone or form"):
            self.manual.plan(self.opportunity, self.handoff_id,
                             **{**self.plan_args, "expected_destination": "sales@example.org"})
        action = self.manual.plan(self.opportunity, self.form_handoff, **self.plan_args)
        with self.assertRaisesRegex(ValueError, "attest"):
            self.manual.record(action, event_key="fake", outcome="CONNECTED",
                               occurred_at_utc="2026-10-05T09:00:00Z", notes="",
                               next_step="", performed_by_operator=False)
        self.routes.suppress("CONTACT_FORM", "https://example.org/contact/form", "Synthetic opt-out")
        self.assertIsNone(self.manual.read(action)["destination_value"])
        self.assertEqual(self.manual.read(action)["route_status"], "REVIEW_REQUIRED")
        with self.assertRaisesRegex(ValueError, "Current Find handoff"):
            self.manual.plan(self.opportunity, self.form_handoff,
                             **{**self.plan_args, "operation_key": "new"})
        # A later operator report can still describe what already happened.
        self.manual.record(action, event_key="late-report", outcome="UNKNOWN",
                           occurred_at_utc="2026-10-05T09:00:00Z", notes="No reply observed",
                           next_step="Do not contact again", performed_by_operator=True)

    def test_v15_upgrade_preserves_find_and_installs_manual_tables(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            for table in ("sell_quotation_decision", "sell_quotation_revision", "sell_quotation", "sell_price_authority"):
                db.execute(f"DROP TABLE {table}")
            for table in ("sell_rfq_decision", "sell_rfq_revision", "sell_rfq"):
                db.execute(f"DROP TABLE {table}")
            for table in ("sell_inbound_review", "sell_inbound_message", "sell_provider_observation"):
                db.execute(f"DROP TABLE {table}")
            for table in ("sell_followup_event", "sell_followup_stop", "sell_followup"):
                db.execute(f"DROP TABLE {table}")
            for table in ("manual_contact_event", "manual_contact_action", "sell_opportunity"):
                db.execute(f"DROP TABLE {table}")
            db.execute("PRAGMA user_version = 15")
        reopened = PilotStore(self.path)
        self.assertIsNotNone(FindHandoff(reopened).read(self.form_handoff))
        self.assertTrue(ManualContact(reopened).open_opportunity(self.form_handoff).startswith("SO-"))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 25)


if __name__ == "__main__":
    unittest.main()
