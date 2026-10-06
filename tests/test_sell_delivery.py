import sqlite3
import unittest
from contextlib import closing

from pilot_engine.sell_delivery import CaptureMailbox, SellDelivery
from pilot_engine.store import PilotStore
import test_sell_drafts


class SellDeliveryTests(unittest.TestCase):
    def setUp(self):
        test_sell_drafts.SellDraftTests.setUp(self)
        self.draft_id, _ = self.drafts.create(self.handoff_id, **self.sender)
        self.delivery = SellDelivery(self.store)
        self.mailbox = CaptureMailbox()

    def approve(self):
        return self.delivery.decide(self.draft_id, 1, "APPROVE", "Reviewed exact synthetic text",
                                    reviewed_claims=True,
                                    expected_envelope_sha256=self.delivery.preview(
                                        self.draft_id, 1)["envelope_sha256"])

    def test_exact_approval_one_capture_and_no_second_provider_call(self):
        with self.assertRaisesRegex(ValueError, "approval"):
            self.delivery.dispatch_synthetic(self.draft_id, 1, self.mailbox)
        with self.assertRaisesRegex(ValueError, "claim review"):
            self.delivery.decide(self.draft_id, 1, "APPROVE", "Missing review")
        with self.assertRaisesRegex(ValueError, "exact previewed"):
            self.delivery.decide(self.draft_id, 1, "APPROVE", "Wrong hash",
                                 reviewed_claims=True, expected_envelope_sha256="wrong")
        self.approve()
        with self.assertRaisesRegex(ValueError, "in-memory"):
            self.delivery.dispatch_synthetic(self.draft_id, 1, object())
        result = self.delivery.dispatch_synthetic(self.draft_id, 1, self.mailbox)
        self.assertEqual(result["outcome"], "CAPTURED_NOT_DELIVERED")
        self.assertFalse(result["delivered"])
        self.assertEqual(len(self.mailbox.messages), 1)
        self.assertEqual(self.delivery.dispatch_synthetic(self.draft_id, 1, self.mailbox), result)
        self.assertEqual(len(self.mailbox.messages), 1)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 18)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("UPDATE sell_send_decision SET decision = 'REVOKE'")

    def test_revocation_edit_rejection_and_stale_find_block_dispatch(self):
        self.approve()
        self.delivery.decide(self.draft_id, 1, "REVOKE", "Withdraw approval")
        with self.assertRaisesRegex(ValueError, "Effective exact-content"):
            self.delivery.dispatch_synthetic(self.draft_id, 1, self.mailbox)
        self.drafts.revise(self.draft_id, 1, **self.sender, language="en",
                           subject="Changed", body="Changed body", claim_refs=[])
        with self.assertRaisesRegex(ValueError, "Current approved"):
            self.delivery.dispatch_synthetic(self.draft_id, 1, self.mailbox)
        with self.assertRaisesRegex(ValueError, "Effective exact-content"):
            self.delivery.dispatch_synthetic(self.draft_id, 2, self.mailbox)
        self.drafts.reject(self.draft_id, 2, "Wrong wording")
        with self.assertRaisesRegex(ValueError, "Current email draft"):
            self.delivery.decide(self.draft_id, 2, "APPROVE", "Rejected draft", reviewed_claims=True)
        self.assertFalse(self.mailbox.messages)

    def test_unknown_stays_blocked_and_reconciliation_never_retries(self):
        self.approve()
        key = f"sell:{self.draft_id}:1"
        self.mailbox.unknown_keys.add(key)
        first = self.delivery.dispatch_synthetic(self.draft_id, 1, self.mailbox)
        self.assertEqual(first["outcome"], "UNKNOWN")
        self.mailbox.unknown_keys.clear()
        self.assertEqual(self.delivery.dispatch_synthetic(self.draft_id, 1, self.mailbox), first)
        self.assertFalse(self.mailbox.messages)
        with self.assertRaisesRegex(ValueError, "message ID"):
            self.delivery.reconcile(first["id"], outcome="PROVIDER_CONFIRMED",
                                    provider_ref="Synthetic provider lookup")
        final = self.delivery.reconcile(first["id"], outcome="PROVIDER_NOT_FOUND",
                                        provider_ref="Synthetic provider lookup")
        self.assertEqual(final["outcome"], "PROVIDER_NOT_FOUND")
        self.assertFalse(final["delivered"])
        self.assertEqual(self.delivery.dispatch_synthetic(self.draft_id, 1, self.mailbox), final)
        with self.assertRaisesRegex(ValueError, "Only an unknown"):
            self.delivery.reconcile(first["id"], outcome="PROVIDER_NOT_FOUND",
                                    provider_ref="Repeat")

    def test_v14_upgrade_preserves_draft_and_installs_decision_tables(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            for table in ("sell_inbound_review", "sell_inbound_message", "sell_provider_observation"):
                db.execute(f"DROP TABLE {table}")
            for table in ("sell_followup_event", "sell_followup_stop", "sell_followup"):
                db.execute(f"DROP TABLE {table}")
            for table in ("manual_contact_event", "manual_contact_action", "sell_opportunity"):
                db.execute(f"DROP TABLE {table}")
            for table in ("sell_send_result", "sell_send_attempt", "sell_send_decision"):
                db.execute(f"DROP TABLE {table}")
            db.execute("PRAGMA user_version = 14")
        reopened = PilotStore(self.path)
        self.assertEqual(SellDelivery(reopened).preview(self.draft_id, 1)["status"],
                         "CURRENT_DRAFT")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 18)


if __name__ == "__main__":
    unittest.main()
