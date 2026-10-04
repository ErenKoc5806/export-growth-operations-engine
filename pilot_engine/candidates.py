"""Source-backed company candidates; qualification and contacts belong to later Find work."""

from __future__ import annotations

from contextlib import closing
from urllib.parse import urlsplit
from uuid import uuid4

from pilot_engine.market_signals import _utc
from pilot_engine.store import PilotStore, _utc_now


ROLES = frozenset({"DISTRIBUTOR", "MANUFACTURER", "RESELLER", "END_USER", "UNKNOWN"})


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field} is required")
    return value


def _url(value: object) -> str:
    value = _text(value, "Evidence URL")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.port not in (None, 443)):
        raise ValueError("Candidate evidence requires a public HTTPS URL")
    return value


def _domain(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host.rstrip(".").removeprefix("www.").lower()


def _related_domain(first: str | None, second: str | None) -> bool:
    return bool(first and second and (first.endswith("." + second) or second.endswith("." + first)))


class CandidateDiscovery:
    """Import reviewed research output or operator-discovered official pages."""

    def __init__(self, store: PilotStore):
        self.store = store

    def record(
        self, *, source_system: str, source_ref: str, query: str,
        observed_at_utc: str, name: str, country_code: str, role_hypothesis: str,
        website: str | None, evidence_urls: list[str], summary: str,
    ) -> dict[str, object]:
        source_system = _text(source_system, "Source system")
        source_ref = _text(source_ref, "Source reference")
        query = _text(query, "Search query")
        name = _text(name, "Company name")
        summary = _text(summary, "Evidence summary")
        observed = _utc(observed_at_utc)
        if country_code != self.store.scope.country or role_hypothesis not in ROLES:
            raise ValueError("Company country or role is outside the pilot candidate scope")
        if not isinstance(evidence_urls, list) or not evidence_urls:
            raise ValueError("Candidate requires source evidence")
        urls = list(dict.fromkeys(_url(url) for url in evidence_urls))
        website = _url(website) if website else None
        domain = _domain(website) if website else None
        actor = self.store._require_access("RECORD_CANDIDATE", source_ref)
        now = _utc_now()
        with self.store._transaction() as db:
            # A repeated exact source is a no-op only if its content is unchanged.
            prior = db.execute("""SELECT c.id, e.observed_name, e.observed_website,
                e.role_hint, e.query_text, e.summary, e.observed_at_utc
                FROM buyer_candidate_evidence e
                JOIN buyer_candidate c ON c.id = e.candidate_id
                WHERE e.source_system = ? AND e.source_ref = ? LIMIT 1""",
                (source_system, source_ref)).fetchone()
            if prior is not None:
                saved_urls = [row[0] for row in db.execute("""SELECT source_url FROM buyer_candidate_evidence
                    WHERE source_system = ? AND source_ref = ? ORDER BY source_url""",
                    (source_system, source_ref))]
                if (prior["observed_name"] != name or prior["observed_website"] != website
                        or prior["role_hint"] != role_hypothesis
                        or prior["query_text"] != query or prior["summary"] != summary
                        or prior["observed_at_utc"] != observed or saved_urls != sorted(urls)):
                    raise ValueError("Source reference already recorded with different evidence")
                candidate_id = prior["id"]
            else:
                existing = (db.execute("""SELECT id, name FROM buyer_candidate
                    WHERE country_code = ? AND domain = ?""", (country_code, domain)).fetchone()
                    if domain else None)
                if existing is not None:
                    candidate_id = existing["id"]
                    if existing["name"] != name:
                        db.execute("INSERT OR IGNORE INTO buyer_candidate_alias VALUES (?, ?)",
                                   (candidate_id, name))
                else:
                    candidate_id = f"BC-{uuid4()}"
                    possible = next((item for item in db.execute("""SELECT id, name, domain
                        FROM buyer_candidate WHERE country_code = ? ORDER BY created_at_utc, id""",
                        (country_code,)) if item["name"].casefold() == name.casefold()
                        or _related_domain(item["domain"], domain)), None)
                    db.execute("""INSERT INTO buyer_candidate
                        (id, name, country_code, website, domain, role_hypothesis,
                         possible_duplicate_of, created_at_utc)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (candidate_id, name, country_code, website, domain, role_hypothesis,
                         possible["id"] if possible else None, now))
                for url in urls:
                    db.execute("""INSERT INTO buyer_candidate_evidence
                        (id, candidate_id, source_system, source_ref, source_url,
                         observed_name, observed_website, role_hint,
                         observed_at_utc, query_text, summary, actor_id, created_at_utc)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (f"CE-{uuid4()}", candidate_id, source_system, source_ref,
                         url, name, website, role_hypothesis, observed, query, summary, actor, now))
        return self.read(candidate_id)

    def import_ti_candidate(
        self, candidate: dict[str, object], *, source_ref: str,
        query: str, observed_at_utc: str,
    ) -> dict[str, object]:
        """Map Trade Intelligence CompanyCandidate, without accepting its fit as approval."""
        if not isinstance(candidate, dict) or candidate.get("role") != "buyer":
            raise ValueError("Only Trade Intelligence buyer candidates are accepted")
        country = candidate.get("country")
        if country not in ("DE", "Germany", "Deutschland"):
            raise ValueError("Trade Intelligence candidate must be in Germany")
        return self.record(
            source_system="TRADE_INTELLIGENCE", source_ref=source_ref,
            query=query, observed_at_utc=observed_at_utc,
            name=candidate.get("name"), country_code="DE",
            role_hypothesis="UNKNOWN", website=candidate.get("website"),
            evidence_urls=candidate.get("evidence_urls"),
            summary=candidate.get("relevance_summary"),
        )

    def read(self, candidate_id: str) -> dict[str, object] | None:
        self.store._require_access("READ_CANDIDATE", candidate_id)
        with closing(self.store._connect()) as db:
            row = db.execute("SELECT * FROM buyer_candidate WHERE id = ?", (candidate_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["aliases"] = [entry[0] for entry in db.execute(
                "SELECT alias FROM buyer_candidate_alias WHERE candidate_id = ? ORDER BY alias",
                (candidate_id,))]
            result["possible_duplicates"] = [entry["id"] for entry in db.execute("""SELECT
                id, name, domain FROM buyer_candidate WHERE country_code = ? AND id != ?""",
                (row["country_code"], candidate_id)) if (entry["name"].casefold() == row["name"].casefold()
                or _related_domain(entry["domain"], row["domain"]))]
            result["evidence"] = [dict(entry) for entry in db.execute("""SELECT id, source_system,
                source_ref, source_url, observed_name, observed_website, role_hint,
                observed_at_utc, query_text, summary
                FROM buyer_candidate_evidence WHERE candidate_id = ? ORDER BY created_at_utc, id""",
                (candidate_id,))]
            result["product_fit_verified"] = False
            result["contact_verified"] = False
            result["outreach_allowed"] = False
            return result
