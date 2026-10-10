import sqlite3
import unittest
from contextlib import closing

from pilot_engine.inbound import InboundResponses
from pilot_engine.sell_delivery import CaptureMailbox, SellDelivery
from pilot_engine.store import PilotStore
import test_followups


class InboundResponseTests(unittest.TestCase):
    def setUp(self):
        test_followups.FollowupTests.setUp(self)
        self.inbound = InboundResponses(self.store)

    def capture(self):
        draft_id, _ = self.drafts.create(self.handoff_id, **self.sender)
        delivery = SellDelivery(self.store)
        digest = delivery.preview(draft_id, 1)["envelope_sha256"]
        delivery.decide(draft_id, 1, "APPROVE", "Synthetic exact review",
                        expected_envelope_sha256=digest, reviewed_claims=True)
        attempt = delivery.dispatch_synthetic(draft_id, 1, CaptureMailbox())
        return attempt

    def test_provider_capture_reply_correlation_and_correction(self):
        attempt = self.capture()
        self.assertEqual(self.inbound.opportunity_summary(self.opportunity)["response_status"],
                         "NO_RESPONSE_OBSERVED")
        self.assertFalse(self.inbound.opportunity_summary(
            self.opportunity)["no_response_is_rejection"])
        provider = self.inbound.record_provider(
            attempt["id"], source_ref="MOCK-OBS-1",
            provider_message_id=attempt["provider_message_id"], outcome="ACCEPTED",
            observed_at_utc="2026-10-06T00:00:00Z")
        self.assertEqual(self.inbound.record_provider(
            attempt["id"], source_ref="MOCK-OBS-1",
            provider_message_id=attempt["provider_message_id"], outcome="ACCEPTED",
            observed_at_utc="2026-10-06T00:00:00Z"), provider)
        inbound_id = self.inbound.record_inbound(
            source_ref="SYN-MSG-1", raw_ref="SYN-MAILBOX-1",
            received_at_utc="2026-10-06T01:00:00Z", channel="EMAIL",
            provider_message_id=attempt["provider_message_id"], conversation_id="SYN-THREAD-1")
        self.assertEqual(self.inbound.read(inbound_id)["match_status"], "MATCHED")
        self.assertEqual(self.inbound.read(inbound_id)["opportunity_id"], self.opportunity)
        self.assertEqual(self.inbound.record_inbound(
            source_ref="SYN-MSG-1", raw_ref="SYN-MAILBOX-1",
            received_at_utc="2026-10-06T01:00:00Z", channel="EMAIL",
            provider_message_id=attempt["provider_message_id"],
            conversation_id="SYN-THREAD-1"), inbound_id)
        followup = self.followups.schedule(self.opportunity, self.source, **self.args)
        self.inbound.review(inbound_id, self.opportunity, classification="INTEREST",
                            evidence_ref="SYN-MAILBOX-1", explanation="Synthetic buyer interest")
        self.assertEqual(self.followups.read(followup)["status"], "SUPPRESSED")
        self.assertEqual(self.followups.read(followup)["stop_reason"], "REVIEWED_REPLY")
        self.inbound.review(inbound_id, self.opportunity, classification="RFQ_CANDIDATE",
                            evidence_ref="SYN-MAILBOX-1", explanation="Operator corrected classification")
        view = self.inbound.read(inbound_id)
        self.assertEqual(len(view["reviews"]), 2)
        self.assertEqual(view["classification"], "RFQ_CANDIDATE")
        self.assertFalse(view["rfq_created"])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 24)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("UPDATE sell_inbound_review SET classification = 'REJECTION'")

    def test_ambiguous_or_unmatched_waits_for_operator_review(self):
        inbound_id = self.inbound.record_inbound(
            source_ref="SYN-MSG-UNMATCHED", raw_ref="SYN-RAW-2",
            received_at_utc="2026-10-06T02:00:00Z", channel="CONTACT_FORM",
            conversation_id="SYN-UNMATCHED")
        self.assertEqual(self.inbound.read(inbound_id)["match_status"], "REVIEW_REQUIRED")
        self.assertEqual(self.inbound.read(inbound_id)["classification"], "UNREVIEWED")
        self.assertEqual(self.followups.schedule(self.opportunity, self.source, **self.args)[:3], "FU-")
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.inbound.record_inbound(source_ref="SYN-MSG-UNMATCHED", raw_ref="SYN-DIFFERENT",
                                        received_at_utc="2026-10-06T02:00:00Z", channel="CONTACT_FORM")
        self.inbound.review(inbound_id, self.opportunity, classification="OTHER",
                            evidence_ref="SYN-RAW-2", explanation="Operator matched invented source")
        self.assertEqual(self.inbound.opportunity_summary(self.opportunity)["response_status"],
                         "REVIEWED_RESPONSE")
        with self.assertRaisesRegex(ValueError, "synthetic"):
            self.inbound.record_inbound(source_ref="real", raw_ref="real-mailbox",
                                        received_at_utc="2026-10-06T02:00:00Z", channel="EMAIL")

    def test_v17_upgrade_keeps_followup(self):
        fid = self.followups.schedule(self.opportunity, self.source, **self.args)
        with closing(sqlite3.connect(self.path)) as db, db:
            for table in ("sell_quotation_decision", "sell_quotation_revision", "sell_quotation", "sell_price_authority"):
                db.execute(f"DROP TABLE {table}")
            for table in ("sell_rfq_decision", "sell_rfq_revision", "sell_rfq"):
                db.execute(f"DROP TABLE {table}")
            for table in ("sell_inbound_review", "sell_inbound_message", "sell_provider_observation"):
                db.execute(f"DROP TABLE {table}")
            db.execute("PRAGMA user_version = 17")
        reopened = PilotStore(self.path)
        self.assertEqual(self.followups.read(fid)["status"], "OVERDUE")
        self.assertEqual(InboundResponses(reopened).opportunity_summary(
            self.opportunity)["response_status"], "NO_RESPONSE_OBSERVED")


if __name__ == "__main__":
    unittest.main()
