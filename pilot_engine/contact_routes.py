"""Find contact routes with operator source decisions and no send permission."""

from __future__ import annotations

import hashlib
import re
from contextlib import closing
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pilot_engine.candidates import _domain, _text, _url
from pilot_engine.market_signals import _utc
from pilot_engine.qualification import _current_status
from pilot_engine.store import PilotStore, _utc_now


KINDS = frozenset({"GENERIC_EMAIL", "SWITCHBOARD", "CONTACT_FORM", "NAMED_EMAIL", "NAMED_PHONE"})
BASES = frozenset({"CONSENT", "LEGITIMATE_INTEREST", "OTHER_REVIEWED"})
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE = re.compile(r"^\+[1-9]\d{6,14}$")
CHECK_METHODS = frozenset({"MANUAL_PAGE", "MANUAL_CALL", "PROVIDER_FEEDBACK"})
CHECK_RESULTS = frozenset({"ROUTE_CONFIRMED", "UNCERTAIN", "INVALID", "BOUNCED"})
FRESHNESS_DAYS = 90


def _observation_hash(db: object, route_id: str) -> str:
    observed = [row[0] for row in db.execute(
        "SELECT id FROM contact_route_observation WHERE route_id = ? ORDER BY rowid", (route_id,))]
    corrections = [row[0] for row in db.execute(
        "SELECT id FROM contact_route_correction WHERE route_id = ? ORDER BY rowid", (route_id,))]
    return hashlib.sha256(repr((observed, corrections)).encode("utf-8")).hexdigest()


def _route_ready(db: object, route: object) -> tuple[object, object]:
    """Return the effective check and collection decision inside one DB transaction."""
    if route is None or route["suppressed"]:
        raise ValueError("Active contact route is required")
    if _current_status(db, route["product_id"], route["candidate_id"])["status"] != "QUALIFIED":
        raise ValueError("Current qualified company/product fit is required")
    kind = "BUSINESS_ROUTE" if route["kind"] == "CONTACT_FORM" else "PERSONAL_ROUTE"
    policy = db.execute("""SELECT * FROM contact_collection_policy
        WHERE source_system = ? AND source_ref = ? AND data_class = ?
        ORDER BY sequence DESC LIMIT 1""",
        (route["source_system"], route["source_ref"], kind)).fetchone()
    if (policy is None or policy["decision"] != "ALLOW" or policy["source_url"] != route["source_url"]
            or datetime.fromisoformat(policy["retention_until_utc"]) <= datetime.now(timezone.utc)):
        raise ValueError("Current collection decision is required")
    check = db.execute("""SELECT * FROM contact_route_check WHERE route_id = ?
        ORDER BY sequence DESC LIMIT 1""", (route["id"],)).fetchone()
    if (check is None or check["result"] != "ROUTE_CONFIRMED"
            or check["observation_sha256"] != _observation_hash(db, route["id"])
            or datetime.now(timezone.utc) - datetime.fromisoformat(check["checked_at_utc"]) >=
            timedelta(days=FRESHNESS_DAYS)):
        raise ValueError("Current verified route check is required")
    checked_policy = db.execute("""SELECT * FROM contact_collection_policy WHERE sequence = ?""",
                                (check["policy_sequence"],)).fetchone()
    if (checked_policy is None or checked_policy["decision"] != "ALLOW"
            or datetime.fromisoformat(checked_policy["retention_until_utc"]) <= datetime.now(timezone.utc)
            or not db.execute("""SELECT 1 FROM contact_collection_policy WHERE sequence = ?
                AND sequence = (SELECT MAX(sequence) FROM contact_collection_policy
                WHERE source_system = ? AND source_ref = ? AND data_class = ?)""",
                (check["policy_sequence"], checked_policy["source_system"],
                 checked_policy["source_ref"], checked_policy["data_class"])).fetchone()):
        raise ValueError("Route check has an outdated source decision")
    return check, policy


def _route_value(kind: str, value: str) -> tuple[str, str]:
    if kind not in KINDS or not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Contact kind and route value are required")
    if kind.endswith("EMAIL"):
        if not EMAIL.fullmatch(value):
            raise ValueError("Email route needs a complete address")
        normalized, channel = value.casefold(), "EMAIL"
    elif kind in ("SWITCHBOARD", "NAMED_PHONE"):
        if not PHONE.fullmatch(value):
            raise ValueError("Phone route needs E.164 format")
        normalized, channel = value, "PHONE"
    else:
        _url(value)
        normalized, channel = value.rstrip("/"), "FORM"
    key = hashlib.sha256(f"{channel}:{normalized}".encode("utf-8")).hexdigest()
    return normalized, key


def _future(value: str) -> str:
    until = _utc(value)
    if datetime.fromisoformat(until) <= datetime.now(timezone.utc):
        raise ValueError("Contact retention date must be in the future")
    return until


class ContactRoutes:
    """Operator-recorded source observations; validity belongs to Find #6."""

    def __init__(self, store: PilotStore):
        self.store = store

    def authorize_source(
        self, *, source_system: str, source_ref: str, source_url: str,
        data_class: str, decision: str, processing_basis: str,
        lawful_basis_ref: str, source_terms_ref: str, retention_until_utc: str,
    ) -> int:
        """Record an administrator's assessment, not a legal determination by code."""
        actor = self.store._require_access("APPROVE_CONTACT_SOURCE", source_ref)
        _text(source_system, "Source system")
        _text(source_ref, "Source reference")
        _url(source_url)
        _text(lawful_basis_ref, "Lawful-basis assessment reference")
        _text(source_terms_ref, "Source-terms review reference")
        if data_class not in ("BUSINESS_ROUTE", "PERSONAL_ROUTE") or decision not in ("ALLOW", "REVOKE"):
            raise ValueError("Invalid collection policy decision")
        if processing_basis not in BASES:
            raise ValueError("Processing basis needs an operator-reviewed category")
        until = _future(retention_until_utc) if decision == "ALLOW" else _utc(retention_until_utc)
        with self.store._transaction() as db:
            cursor = db.execute("""INSERT INTO contact_collection_policy
                (source_system, source_ref, source_url, data_class, decision,
                 processing_basis, lawful_basis_ref, source_terms_ref,
                 retention_until_utc, actor_id, decided_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source_system, source_ref, source_url, data_class, decision,
                 processing_basis, lawful_basis_ref, source_terms_ref,
                 until, actor, _utc_now()))
            return cursor.lastrowid

    def record(
        self, product_id: str, candidate_id: str, *, kind: str, value: str,
        source_system: str, source_ref: str, source_url: str, observed_at_utc: str,
        person_name: str | None = None, person_role: str | None = None,
    ) -> dict[str, object]:
        actor = self.store._require_access("RECORD_DISCOVERED_CONTACT", candidate_id)
        _, key = _route_value(kind, value)
        _text(source_system, "Source system")
        _text(source_ref, "Source reference")
        _url(source_url)
        observed = _utc(observed_at_utc)
        named = kind.startswith("NAMED_")
        if named:
            _text(person_name, "Person name")
            if person_role is not None:
                _text(person_role, "Person role")
        elif person_name is not None or person_role is not None:
            raise ValueError("Generic route cannot carry a person's details")
        # A generic label does not prove an address/number is nonpersonal.
        data_class = "BUSINESS_ROUTE" if kind == "CONTACT_FORM" else "PERSONAL_ROUTE"
        with self.store._transaction() as db:
            if _current_status(db, product_id, candidate_id)["status"] != "QUALIFIED":
                raise ValueError("Current qualified company/product fit is required")
            policy = db.execute("""SELECT * FROM contact_collection_policy
                WHERE source_system = ? AND source_ref = ? AND data_class = ?
                ORDER BY sequence DESC LIMIT 1""", (source_system, source_ref, data_class)).fetchone()
            if (policy is None or policy["decision"] != "ALLOW" or policy["source_url"] != source_url
                    or datetime.fromisoformat(policy["retention_until_utc"]) <= datetime.now(timezone.utc)):
                raise ValueError("A current approved collection and source-terms review is required")
            if db.execute("SELECT 1 FROM contact_suppression WHERE value_key = ?", (key,)).fetchone():
                raise ValueError("Contact route is suppressed")
            existing = db.execute("""SELECT * FROM discovered_contact_route WHERE candidate_id = ?
                AND kind = ? AND value_key = ?""", (candidate_id, kind, key)).fetchone()
            if existing is not None:
                if existing["suppressed"]:
                    raise ValueError("Contact route is suppressed")
                if existing["product_id"] != product_id:
                    raise ValueError("Existing route belongs to another product review")
                if existing["person_name"] != person_name or existing["person_role"] != person_role:
                    raise ValueError("Conflicting contact name or role needs operator review")
                route_id = existing["id"]
            else:
                route_id = f"CR-{uuid4()}"
                db.execute("""INSERT INTO discovered_contact_route
                    (id, product_id, candidate_id, kind, route_value, value_key,
                     person_name, person_role, source_system, source_ref, source_url,
                     observed_at_utc, policy_sequence, actor_id, created_at_utc)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (route_id, product_id, candidate_id, kind, value, key, person_name, person_role,
                     source_system, source_ref, source_url, observed, policy["sequence"], actor, _utc_now()))
            prior = db.execute("""SELECT observed_at_utc FROM contact_route_observation
                WHERE route_id = ? AND source_system = ? AND source_ref = ?""",
                (route_id, source_system, source_ref)).fetchone()
            if prior is not None and prior["observed_at_utc"] != observed:
                raise ValueError("Source reference changed; record a new source observation")
            if prior is None:
                db.execute("""INSERT INTO contact_route_observation VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                           (f"CO-{uuid4()}", route_id, source_system, source_ref,
                            source_url, observed, policy["sequence"], actor, _utc_now()))
        return self.read(route_id)

    def correct_identity(self, route_id: str, *, person_name: str,
                         person_role: str | None, reason: str) -> dict[str, object]:
        """Operator correction records who changed the route, without old personal text."""
        actor = self.store._require_access("CORRECT_DISCOVERED_CONTACT", route_id)
        _text(person_name, "Person name")
        if person_role is not None:
            _text(person_role, "Person role")
        _text(reason, "Correction reason")
        with self.store._transaction() as db:
            row = db.execute("SELECT * FROM discovered_contact_route WHERE id = ?", (route_id,)).fetchone()
            if row is None or row["suppressed"] or not row["kind"].startswith("NAMED_"):
                raise ValueError("Only an active named route can be corrected")
            db.execute("""UPDATE discovered_contact_route SET person_name = ?, person_role = ?
                WHERE id = ?""", (person_name, person_role, route_id))
            db.execute("""INSERT INTO contact_route_correction VALUES (?, ?, ?, ?, ?)""",
                       (f"CC-{uuid4()}", route_id, reason, actor, _utc_now()))
        return self.read(route_id)

    def record_missing(
        self, product_id: str, candidate_id: str, *, source_url: str,
        observed_at_utc: str, explanation: str,
    ) -> str:
        actor = self.store._require_access("RECORD_DISCOVERED_CONTACT", candidate_id)
        _url(source_url)
        observed = _utc(observed_at_utc)
        _text(explanation, "Missing route explanation")
        with self.store._transaction() as db:
            if _current_status(db, product_id, candidate_id)["status"] != "QUALIFIED":
                raise ValueError("Current qualified company/product fit is required")
            prior = db.execute("""SELECT id, explanation FROM contact_route_absence
                WHERE product_id = ? AND candidate_id = ? AND source_url = ? AND observed_at_utc = ?""",
                (product_id, candidate_id, source_url, observed)).fetchone()
            if prior is not None:
                if prior["explanation"] != explanation:
                    raise ValueError("Missing-route observation conflicts with its prior record")
                return prior["id"]
            record_id = f"CM-{uuid4()}"
            db.execute("""INSERT INTO contact_route_absence VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                       (record_id, product_id, candidate_id, source_url,
                        observed, explanation, actor, _utc_now()))
        return record_id

    def suppress(self, kind: str, value: str, reason: str) -> None:
        actor = self.store._require_access("SUPPRESS_CONTACT", "contact-route")
        _, key = _route_value(kind, value)
        _text(reason, "Suppression reason")
        with self.store._transaction() as db:
            db.execute("""INSERT OR IGNORE INTO contact_suppression VALUES (?, ?, ?, ?)""",
                       (key, reason, actor, _utc_now()))
            db.execute("""UPDATE discovered_contact_route SET route_value = NULL,
                person_name = NULL, person_role = NULL, source_url = NULL, suppressed = 1
                WHERE value_key = ?""", (key,))

    def check(
        self, route_id: str, *, method: str, result: str,
        source_url: str, checked_at_utc: str, explanation: str,
        domain_review_ref: str | None = None,
    ) -> int:
        actor = self.store._require_access("VERIFY_DISCOVERED_CONTACT", route_id)
        if method not in CHECK_METHODS or result not in CHECK_RESULTS:
            raise ValueError("Invalid route check method or result")
        _url(source_url)
        _text(explanation, "Check explanation")
        checked = _utc(checked_at_utc)
        if datetime.fromisoformat(checked) > datetime.now(timezone.utc):
            raise ValueError("Check time cannot be in the future")
        if domain_review_ref is not None:
            _text(domain_review_ref, "Domain review reference")
        with self.store._transaction() as db:
            route = db.execute("SELECT * FROM discovered_contact_route WHERE id = ?",
                               (route_id,)).fetchone()
            if route is None or route["suppressed"]:
                raise ValueError("Active contact route is required")
            observed = db.execute("""SELECT * FROM contact_route_observation
                WHERE route_id = ? AND source_url = ? ORDER BY rowid DESC LIMIT 1""",
                (route_id, source_url)).fetchone()
            if observed is None:
                raise ValueError("Check must cite a recorded source URL")
            if datetime.fromisoformat(checked) < datetime.fromisoformat(observed["observed_at_utc"]):
                raise ValueError("Check cannot predate its source observation")
            if result == "ROUTE_CONFIRMED":
                if method == "PROVIDER_FEEDBACK":
                    raise ValueError("Provider feedback alone does not confirm a route")
                if _current_status(db, route["product_id"], route["candidate_id"])["status"] != "QUALIFIED":
                    raise ValueError("Current buyer fit is required for confirmation")
                source_policy = db.execute("""SELECT * FROM contact_collection_policy
                    WHERE source_system = ? AND source_ref = ? AND data_class = ?
                    ORDER BY sequence DESC LIMIT 1""",
                    (observed["source_system"], observed["source_ref"],
                     "BUSINESS_ROUTE" if route["kind"] == "CONTACT_FORM" else "PERSONAL_ROUTE")).fetchone()
                if (source_policy is None or source_policy["sequence"] != observed["policy_sequence"]
                        or source_policy["decision"] != "ALLOW"
                        or datetime.fromisoformat(source_policy["retention_until_utc"]) <=
                        datetime.now(timezone.utc)):
                    raise ValueError("Current source-use decision is required for confirmation")
                company = db.execute("SELECT domain FROM buyer_candidate WHERE id = ?",
                                     (route["candidate_id"],)).fetchone()
                company_domain = company["domain"] if company else None
                domains = [_domain(source_url)]
                if route["kind"].endswith("EMAIL"):
                    domains.append(route["route_value"].rsplit("@", 1)[1].rstrip(".").lower())
                if (not company_domain or any(
                        domain != company_domain and not domain.endswith("." + company_domain)
                        for domain in domains)) and not domain_review_ref:
                    raise ValueError("Company/source domain mismatch needs an explicit review reference")
                if route["kind"].startswith("NAMED_") and not route["person_role"]:
                    raise ValueError("Named contact role uncertainty needs review")
            cursor = db.execute("""INSERT INTO contact_route_check
                (route_id, observation_sha256, policy_sequence, method, result, source_url, checked_at_utc,
                 explanation, domain_review_ref, actor_id, recorded_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (route_id, _observation_hash(db, route_id), observed["policy_sequence"], method, result, source_url,
                 checked, explanation, domain_review_ref, actor, _utc_now()))
            return cursor.lastrowid

    def read(self, route_id: str) -> dict[str, object] | None:
        self.store._require_access("READ_DISCOVERED_CONTACT", route_id)
        with closing(self.store._connect()) as db:
            row = db.execute("SELECT * FROM discovered_contact_route WHERE id = ?", (route_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result.pop("value_key")
            result["observations"] = [dict(item) for item in db.execute("""SELECT source_system,
                source_ref, source_url, observed_at_utc, policy_sequence, recorded_at_utc
                FROM contact_route_observation
                WHERE route_id = ? ORDER BY rowid""", (route_id,))]
            result["possible_duplicates"] = [item["id"] for item in db.execute("""SELECT id
                FROM discovered_contact_route WHERE candidate_id = ? AND value_key = ? AND id != ?""",
                (row["candidate_id"], row["value_key"], route_id))]
            result["corrections"] = [dict(item) for item in db.execute("""SELECT reason, actor_id,
                corrected_at_utc FROM contact_route_correction WHERE route_id = ?
                ORDER BY rowid""", (route_id,))]
            policy = db.execute("""SELECT decision, retention_until_utc, source_url
                FROM contact_collection_policy WHERE source_system = ? AND source_ref = ?
                AND data_class = ? ORDER BY sequence DESC LIMIT 1""",
                (row["source_system"], row["source_ref"],
                "BUSINESS_ROUTE" if row["kind"] == "CONTACT_FORM" else "PERSONAL_ROUTE")).fetchone()
            expired = (policy is None or datetime.fromisoformat(policy["retention_until_utc"]) <=
                       datetime.now(timezone.utc))
            source_blocked = (policy is None or policy["decision"] != "ALLOW"
                              or policy["source_url"] != row["source_url"])
            fit_stale = _current_status(db, row["product_id"], row["candidate_id"])["status"] != "QUALIFIED"
            result["status"] = ("SUPPRESSED" if row["suppressed"] else
                                "RETENTION_EXPIRED" if expired else
                                "REVIEW_REQUIRED" if source_blocked or fit_stale else "UNVERIFIED")
            latest_check = db.execute("""SELECT * FROM contact_route_check
                WHERE route_id = ? ORDER BY sequence DESC LIMIT 1""", (route_id,)).fetchone()
            result["latest_check"] = dict(latest_check) if latest_check else None
            if latest_check is not None and result["status"] == "UNVERIFIED":
                if latest_check["result"] in ("BOUNCED", "INVALID"):
                    result["status"] = latest_check["result"]
                elif (latest_check["observation_sha256"] != _observation_hash(db, route_id)
                      or datetime.now(timezone.utc) -
                      datetime.fromisoformat(latest_check["checked_at_utc"]) >=
                      timedelta(days=FRESHNESS_DAYS)
                      or not db.execute("""SELECT 1 FROM contact_collection_policy p
                          WHERE p.sequence = ? AND p.sequence = (SELECT MAX(q.sequence)
                          FROM contact_collection_policy q WHERE q.source_system = p.source_system
                          AND q.source_ref = p.source_ref AND q.data_class = p.data_class)
                          AND p.decision = 'ALLOW'""", (latest_check["policy_sequence"],)).fetchone()):
                    result["status"] = "REVIEW_REQUIRED"
                elif latest_check["result"] == "ROUTE_CONFIRMED":
                    result["status"] = "VERIFIED_ROUTE"
                    try:
                        _route_ready(db, row)
                    except ValueError:
                        result["status"] = "REVIEW_REQUIRED"
            result["source_use_status"] = ("EXPIRED" if expired else
                                           "REVOKED" if source_blocked else "REVIEWED_ALLOWED")
            if expired or row["suppressed"]:
                result["route_value"] = None
                result["person_name"] = None
                result["person_role"] = None
                result["source_url"] = None
                for observation in result["observations"]:
                    observation["source_url"] = None
                if result["latest_check"]:
                    result["latest_check"]["source_url"] = None
            result["outreach_allowed"] = False
            return result

    def list_for_candidate(self, product_id: str, candidate_id: str) -> dict[str, object]:
        self.store._require_access("READ_DISCOVERED_CONTACT", candidate_id)
        with closing(self.store._connect()) as db:
            ids = [row["id"] for row in db.execute("""SELECT id FROM discovered_contact_route
                WHERE product_id = ? AND candidate_id = ? ORDER BY created_at_utc, id""",
                (product_id, candidate_id))]
            missing = [dict(row) for row in db.execute("""SELECT id, source_url, observed_at_utc,
                explanation, actor_id, recorded_at_utc FROM contact_route_absence
                WHERE product_id = ? AND candidate_id = ? ORDER BY recorded_at_utc, id""",
                (product_id, candidate_id))]
        return {"routes": [self.read(route_id) for route_id in ids], "missing": missing}
