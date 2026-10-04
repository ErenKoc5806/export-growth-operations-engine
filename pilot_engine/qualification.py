"""Human product-fit decisions bound to exact profile and candidate evidence."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from typing import Any

from pilot_engine.store import PilotStore, _json, _utc_now


CHECKS = ("product_spec", "buyer_role", "corridor")
RESULTS = frozenset({"CONFIRMED", "MISMATCH", "UNKNOWN"})
OUTCOMES = frozenset({"ACCEPT", "REJECT", "DEFER"})


def _hash_ids(ids: list[str]) -> str:
    return hashlib.sha256(_json(sorted(ids)).encode("utf-8")).hexdigest()


class BuyerQualification:
    """A recorded review, not automatic evidence of intent to buy."""

    def __init__(self, store: PilotStore):
        self.store = store

    def decide(
        self, product_id: str, candidate_id: str, profile_revision: int,
        *, outcome: str, checks: dict[str, str], cited_evidence_ids: list[str],
        explanation: str,
    ) -> int:
        if (outcome not in OUTCOMES or not isinstance(checks, dict)
                or set(checks) != set(CHECKS) or any(value not in RESULTS for value in checks.values())
                or not isinstance(cited_evidence_ids, list) or not cited_evidence_ids
                or not all(isinstance(item, str) for item in cited_evidence_ids)
                or not isinstance(explanation, str) or len(explanation.strip()) < 20):
            raise ValueError("Buyer fit decision requires checks, cited evidence and explanation")
        if isinstance(profile_revision, bool) or not isinstance(profile_revision, int) or profile_revision < 1:
            raise ValueError("Profile revision must be positive")
        actor = self.store._require_access("QUALIFY_CANDIDATE", candidate_id)
        with self.store._transaction() as db:
            profile = db.execute("""SELECT revision, payload_sha256 FROM product_profile_revision
                WHERE product_id = ? ORDER BY revision DESC LIMIT 1""", (product_id,)).fetchone()
            if profile is None or profile["revision"] != profile_revision:
                raise ValueError("Current manufacturer profile revision is required")
            candidate = db.execute("SELECT country_code FROM buyer_candidate WHERE id = ?",
                                   (candidate_id,)).fetchone()
            if candidate is None or candidate["country_code"] != self.store.scope.country:
                raise ValueError("Candidate is not in the selected market")
            evidence_ids = [row[0] for row in db.execute("""SELECT id FROM buyer_candidate_evidence
                WHERE candidate_id = ?""", (candidate_id,))]
            if not set(cited_evidence_ids).issubset(evidence_ids):
                raise ValueError("Cited evidence must belong to the candidate")
            if outcome == "ACCEPT":
                approval = db.execute("""SELECT action FROM product_profile_event
                    WHERE product_id = ? AND revision = ? ORDER BY sequence DESC LIMIT 1""",
                    (product_id, profile_revision)).fetchone()
                if approval is None or approval["action"] != "APPROVED":
                    raise ValueError("Manufacturer profile approval is required")
                if any(checks[key] != "CONFIRMED" for key in CHECKS):
                    raise ValueError("All product, role and corridor checks must be confirmed")
            cursor = db.execute("""INSERT INTO buyer_fit_decision
                (product_id, candidate_id, profile_revision, profile_sha256,
                 evidence_sha256, outcome, checks_json, explanation, actor_id, decided_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (product_id, candidate_id, profile_revision, profile["payload_sha256"],
                 _hash_ids(evidence_ids), outcome,
                 _json({**checks, "cited_evidence_ids": sorted(set(cited_evidence_ids))}),
                 explanation.strip(), actor, _utc_now()))
            return cursor.lastrowid

    def status(self, product_id: str, candidate_id: str) -> dict[str, Any]:
        self.store._require_access("READ_CANDIDATE", candidate_id)
        with closing(self.store._connect()) as db:
            latest = db.execute("""SELECT * FROM buyer_fit_decision
                WHERE product_id = ? AND candidate_id = ? ORDER BY sequence DESC LIMIT 1""",
                (product_id, candidate_id)).fetchone()
            if latest is None:
                return {"status": "UNQUALIFIED", "outreach_allowed": False}
            result = dict(latest)
            result["checks"] = json.loads(result.pop("checks_json"))
            current = db.execute("""SELECT revision, payload_sha256 FROM product_profile_revision
                WHERE product_id = ? ORDER BY revision DESC LIMIT 1""", (product_id,)).fetchone()
            evidence_ids = [row[0] for row in db.execute("""SELECT id FROM buyer_candidate_evidence
                WHERE candidate_id = ?""", (candidate_id,))]
            approval = db.execute("""SELECT action FROM product_profile_event
                WHERE product_id = ? AND revision = ? ORDER BY sequence DESC LIMIT 1""",
                (product_id, result["profile_revision"])).fetchone()
            stale = (current is None or current["revision"] != result["profile_revision"]
                     or current["payload_sha256"] != result["profile_sha256"]
                     or _hash_ids(evidence_ids) != result["evidence_sha256"]
                     or (result["outcome"] == "ACCEPT" and
                         (approval is None or approval["action"] != "APPROVED")))
            result["status"] = ("REVIEW_REQUIRED" if stale else
                                "QUALIFIED" if result["outcome"] == "ACCEPT" else result["outcome"])
            # Qualification alone never authorizes a contact or external message.
            result["outreach_allowed"] = False
            return result
