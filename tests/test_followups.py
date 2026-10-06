import sqlite3
import unittest
from contextlib import closing

from pilot_engine.followups import Followups
from pilot_engine.store import PilotStore
import test_manual_contact


class FollowupTests(unittest.TestCase):
    def setUp(self):
        test_manual_contact.ManualContactTests.setUp(self)
        self.source = self.manual.plan(self.opportunity, self.form_handoff, **self.plan_args)
        self.manual.record(self.source, event_key="initial-action", outcome="ATTEMPTED",
                           occurred_at_utc="2026-10-05T08:10:00Z", notes="Invented submission attempt",
                           next_step="Review later", performed_by_operator=True)
        self.followups = Followups(self.store)
        self.args = dict(operation_key="followup-1", due_at_utc="2026-10-05T10:00:00Z",
                         channel="CONTACT_FORM", reason="Check whether contact led to a response")

    def test_due_completion_requires_new_operator_action(self):
        fid = self.followups.schedule(self.opportunity, self.source, **self.args)
        self.assertEqual(self.followups.schedule(self.opportunity, self.source, **self.args), fid)
        view = self.followups.read(fid)
        self.assertEqual(view["status"], "OVERDUE")
        self.assertEqual(view["attempt_count"], 1)
        self.assertFalse(view["performed_by_system"])
        self.assertEqual(len(self.followups.list_due(view["owner_id"])), 1)
        with self.assertRaisesRegex(ValueError, "New operator"):
            self.followups.conclude(fid, event_key="bad", outcome="COMPLETED",
                                    reason="Reused old attempt", linked_action_id=self.source)
        second = self.manual.plan(self.opportunity, self.form_handoff, **{
            **self.plan_args, "operation_key": "manual-action-2",
            "summary": "Operator plans a second manual inquiry"})
        self.manual.record(second, event_key="second-result", outcome="ATTEMPTED",
                           occurred_at_utc="2026-10-05T11:00:00Z", notes="Second invented attempt",
                           next_step="Stop for now", performed_by_operator=True)
        seq = self.followups.conclude(fid, event_key="complete-1", outcome="COMPLETED",
                                      reason="Operator reported new action", linked_action_id=second)
        self.assertEqual(self.followups.conclude(fid, event_key="complete-1", outcome="COMPLETED",
                                                 reason="Operator reported new action",
                                                 linked_action_id=second), seq)
        self.assertEqual(self.followups.read(fid)["status"], "COMPLETED")
        self.assertEqual(self.followups.list_due(view["owner_id"]), [])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 20)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("DELETE FROM sell_followup_event")

    def test_reply_optout_and_invalid_route_suppress_reminders(self):
        fid = self.followups.schedule(self.opportunity, self.source, **self.args)
        self.followups.stop(self.opportunity, reason="REPLY", evidence_ref="Operator inbound note example")
        self.assertEqual(self.followups.read(fid)["status"], "SUPPRESSED")
        self.assertEqual(self.followups.list_due(self.followups.read(fid)["owner_id"]), [])
        with self.assertRaisesRegex(ValueError, "stop decision"):
            self.followups.schedule(self.opportunity, self.source,
                                    **{**self.args, "operation_key": "new-followup"})
        with self.assertRaisesRegex(ValueError, "Blocked"):
            self.followups.conclude(fid, event_key="false-complete", outcome="COMPLETED",
                                    reason="No action", linked_action_id=self.source)

    def test_unknown_source_and_email_capture_do_not_claim_completion(self):
        fid = self.followups.schedule(self.opportunity, self.source,
                                      **{**self.args, "channel": "EMAIL"})
        with self.assertRaisesRegex(ValueError, "real provider"):
            self.followups.conclude(fid, event_key="email-complete", outcome="COMPLETED",
                                    reason="Mock capture isn't delivery")
        self.manual.record(self.source, event_key="uncertain-action", outcome="UNKNOWN",
                           occurred_at_utc="2026-10-05T09:00:00Z", notes="Uncertain result",
                           next_step="Review", performed_by_operator=True)
        self.assertEqual(self.followups.read(fid)["status"], "BLOCKED_UNKNOWN")
        with self.assertRaisesRegex(ValueError, "non-unknown"):
            self.followups.schedule(self.opportunity, self.source,
                                    **{**self.args, "operation_key": "another"})
        self.assertTrue(self.followups.conclude(fid, event_key="skip-1", outcome="SKIPPED",
                                          reason="Uncertain result; do not retry"))
        self.assertEqual(self.followups.read(fid)["status"], "SKIPPED")

    def test_opt_out_requires_find_suppression_first(self):
        fid = self.followups.schedule(self.opportunity, self.source, **self.args)
        with self.assertRaisesRegex(ValueError, "suppressed route"):
            self.followups.stop(self.opportunity, reason="OPT_OUT",
                                evidence_ref="Invented opt-out", route_id=self.form_id)
        self.routes.suppress("CONTACT_FORM", "https://example.org/contact/form",
                             "Invented opt-out")
        self.followups.stop(self.opportunity, reason="OPT_OUT",
                            evidence_ref="Invented opt-out", route_id=self.form_id)
        self.assertEqual(self.followups.read(fid)["status"], "SUPPRESSED")

    def test_v16_upgrade_retains_manual_action(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            for table in ("sell_quotation_decision", "sell_quotation_revision", "sell_quotation", "sell_price_authority"):
                db.execute(f"DROP TABLE {table}")
            for table in ("sell_rfq_decision", "sell_rfq_revision", "sell_rfq"):
                db.execute(f"DROP TABLE {table}")
            for table in ("sell_inbound_review", "sell_inbound_message", "sell_provider_observation"):
                db.execute(f"DROP TABLE {table}")
            for table in ("sell_followup_event", "sell_followup_stop", "sell_followup"):
                db.execute(f"DROP TABLE {table}")
            db.execute("PRAGMA user_version = 16")
        reopened = PilotStore(self.path)
        with closing(reopened._connect()) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 20)
        self.assertTrue(Followups(reopened).schedule(self.opportunity, self.source, **self.args))


if __name__ == "__main__":
    unittest.main()
