"""Immutable, evidence-bound Find research handoff; no external send permission."""

from __future__ import annotations

import json
from contextlib import closing
from uuid import uuid4

from pilot_engine.contact_routes import _observation_hash, _route_ready
from pilot_engine.profiles import _approval_valid
from pilot_engine.qualification import _current_status, _hash_ids
from pilot_engine.store import PilotStore, _utc_now


class FindHandoff:
    def __init__(self, store: PilotStore):
        self.store = store

    @staticmethod
    def _approved_profile(db: object, product_id: str) -> object:
        profile = db.execute("""SELECT revision, payload_sha256, payload_json
            FROM product_profile_revision WHERE product_id = ? ORDER BY revision DESC LIMIT 1""",
            (product_id,)).fetchone()
        if profile is None:
            raise ValueError("Current approved product profile is required")
        event = db.execute("""SELECT action FROM product_profile_event
            WHERE product_id = ? AND revision = ? ORDER BY sequence DESC LIMIT 1""",
            (product_id, profile["revision"])).fetchone()
        if (event is None or event["action"] != "APPROVED"
                or not _approval_valid(db, json.loads(profile["payload_json"]))):
            raise ValueError("Current approved product profile is required")
        return profile

    def record(self, product_id: str, candidate_id: str, route_id: str,
               *, data_origin: str = "SYNTHETIC") -> tuple[str, int]:
        actor = self.store._require_access("RECORD_FIND_HANDOFF", candidate_id)
        if data_origin != "SYNTHETIC":
            raise ValueError("Real Find handoff requires pilot readiness authorization")
        with self.store._transaction() as db:
            route = db.execute("SELECT * FROM discovered_contact_route WHERE id = ?",
                               (route_id,)).fetchone()
            if route is None or (route["product_id"], route["candidate_id"]) != (product_id, candidate_id):
                raise ValueError("Route does not belong to this product/company review")
            candidate = db.execute("SELECT domain FROM buyer_candidate WHERE id = ?",
                                   (candidate_id,)).fetchone()
            if candidate is None or candidate["domain"] not in ("example.org", "example.com", "example.net"):
                raise ValueError("Synthetic handoff needs a reserved example company domain")
            check, policy = _route_ready(db, route)
            fit = _current_status(db, product_id, candidate_id)
            profile = self._approved_profile(db, product_id)
            evidence_ids = [row[0] for row in db.execute("""SELECT id FROM buyer_candidate_evidence
                WHERE candidate_id = ?""", (candidate_id,))]
            snapshot = (profile["revision"], profile["payload_sha256"], fit["sequence"],
                        check["sequence"], data_origin, _observation_hash(db, route_id),
                        _hash_ids(evidence_ids), policy["sequence"])
            handoff = db.execute("""SELECT id FROM find_handoff WHERE product_id = ?
                AND candidate_id = ? AND route_id = ?""", (product_id, candidate_id, route_id)).fetchone()
            if handoff is None:
                handoff_id = f"FH-{uuid4()}"
                db.execute("""INSERT INTO find_handoff
                    (id, product_id, candidate_id, route_id, created_at_utc)
                    VALUES (?, ?, ?, ?, ?)""",
                           (handoff_id, product_id, candidate_id, route_id, _utc_now()))
            else:
                handoff_id = handoff["id"]
                latest = db.execute("""SELECT * FROM find_handoff_revision WHERE handoff_id = ?
                    ORDER BY revision DESC LIMIT 1""", (handoff_id,)).fetchone()
                if tuple(latest[key] for key in (
                        "profile_revision", "profile_sha256", "fit_sequence", "check_sequence",
                        "data_origin", "observation_sha256", "candidate_evidence_sha256",
                        "source_policy_sequence")) == snapshot:
                    return handoff_id, latest["revision"]
            revision = 1 if handoff is None else latest["revision"] + 1
            db.execute("""INSERT INTO find_handoff_revision
                (handoff_id, revision, profile_revision, profile_sha256, fit_sequence,
                 check_sequence, data_origin, observation_sha256, candidate_evidence_sha256,
                 source_policy_sequence, actor_id, recorded_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       (handoff_id, revision, *snapshot, actor, _utc_now()))
            return handoff_id, revision

    def read(self, handoff_id: str) -> dict[str, object] | None:
        self.store._require_access("READ_FIND_HANDOFF", handoff_id)
        with closing(self.store._connect()) as db:
            return self._read(db, handoff_id)

    def _read(self, db: object, handoff_id: str) -> dict[str, object] | None:
        """Internal read on an existing transaction; caller has checked access."""
        row = db.execute("""SELECT h.*, r.* FROM find_handoff h
            JOIN find_handoff_revision r ON r.handoff_id = h.id
            WHERE h.id = ? ORDER BY r.revision DESC LIMIT 1""", (handoff_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        product = db.execute("SELECT manufacturer_id FROM product WHERE id = ?",
                             (result["product_id"],)).fetchone()
        candidate = db.execute("SELECT name, country_code FROM buyer_candidate WHERE id = ?",
                               (result["candidate_id"],)).fetchone()
        result["manufacturer_id"] = product["manufacturer_id"]
        result["market_country"] = candidate["country_code"]
        result["buyer_company_name"] = candidate["name"]
        route = db.execute("SELECT * FROM discovered_contact_route WHERE id = ?",
                           (result["route_id"],)).fetchone()
        evidence_ids = [item[0] for item in db.execute("""SELECT id FROM buyer_candidate_evidence
            WHERE candidate_id = ? ORDER BY id""", (result["candidate_id"],))]
        result["candidate_evidence_ids"] = evidence_ids
        result["route_observation_ids"] = [item[0] for item in db.execute("""SELECT id
            FROM contact_route_observation WHERE route_id = ? ORDER BY rowid""",
            (result["route_id"],))]
        try:
            check, policy = _route_ready(db, route)
            fit = _current_status(db, result["product_id"], result["candidate_id"])
            profile = self._approved_profile(db, result["product_id"])
            current = (profile["revision"] == result["profile_revision"]
                       and profile["payload_sha256"] == result["profile_sha256"]
                       and fit["sequence"] == result["fit_sequence"]
                       and check["sequence"] == result["check_sequence"]
                       and _observation_hash(db, result["route_id"]) == result["observation_sha256"]
                       and _hash_ids(evidence_ids) == result["candidate_evidence_sha256"]
                       and policy["sequence"] == result["source_policy_sequence"])
        except ValueError:
            current = False
        result["status"] = "CURRENT_RESEARCH" if current else "REVIEW_REQUIRED"
        result["route_value"] = route["route_value"] if current else None
        result["send_allowed"] = False
        return result
