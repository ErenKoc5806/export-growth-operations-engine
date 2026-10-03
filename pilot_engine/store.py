"""SQLite pilot store. Synthetic case ingestion is atomic and retry safe."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from pilot_engine.domain import DEFAULT_PILOT_SCOPE, OpportunityStatus, PilotScope, require_transition
from pilot_engine.workflow import PilotResult, run_case, validate_quotation_revision


MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class PilotStore:
    def __init__(self, path: str | Path, scope: PilotScope = DEFAULT_PILOT_SCOPE):
        self.path = Path(path)
        self.scope = scope
        if str(self.path) == ":memory:":
            raise ValueError("Use a file path for durable pilot storage")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise ValueError(f"Unsupported database schema version: {version}")
            for next_version in range(version + 1, 3):
                migration = MIGRATIONS / f"{next_version:03d}_{'initial' if next_version == 1 else 'validation'}.sql"
                try:
                    db.executescript(migration.read_text(encoding="utf-8"))
                except Exception:
                    db.rollback()
                    raise

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 10000")
        return db

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def save_synthetic_case(self, case: dict[str, Any]) -> PilotResult:
        """Persist a fully validated fixture as drafts; repeated identical input is a no-op.

        This test adapter does not grant approval to send or issue documents.
        Live import requires independent evidence and identity validation.
        """
        result = run_case(case, self.scope)
        digest = hashlib.sha256(_json(case).encode("utf-8")).hexdigest()
        checked_at = datetime.fromisoformat(case["buyer"]["checked_at"])
        if checked_at.tzinfo is None or checked_at.utcoffset() != timezone.utc.utcoffset(checked_at):
            raise ValueError("Contact checked_at must be an ISO 8601 UTC timestamp")
        now = _utc_now()
        oid = result.opportunity_id
        product, buyer = case["product"], case["buyer"]
        manufacturer_name = case["manufacturer_name"]
        operator_id = case["operator_id"]
        quote, po = case["quotation"], case["customer_po"]
        with self._transaction() as db:
            previous = db.execute(
                "SELECT payload_sha256 FROM case_ingest WHERE opportunity_id = ?", (oid,)
            ).fetchone()
            if previous:
                if previous["payload_sha256"] != digest:
                    raise ValueError("Opportunity already ingested with different content")
                return result

            manufacturer_id = f"M-{oid}"
            product_id = f"P-{oid}"
            target_id = f"T-{oid}"
            buyer_id = f"B-{oid}"
            contact_id = f"C-{oid}"
            db.execute("INSERT INTO manufacturer VALUES (?, ?, ?)",
                       (manufacturer_id, manufacturer_name.strip(), now))
            db.execute("INSERT INTO product VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (product_id, manufacturer_id, product["sku"], product["description"],
                        product["unit"], product["hs6"], None, "UNVERIFIED", now, now))
            db.execute("INSERT INTO market_target VALUES (?, ?, ?, ?, ?)",
                       (target_id, product_id, case["target_country"],
                        _json(product.get("search_terms", [])), now))
            db.execute("INSERT INTO buyer_company VALUES (?, ?, ?, ?, ?)",
                       (buyer_id, buyer["company"], self.scope.country, "BUYER", now))
            db.execute("INSERT INTO contact_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (contact_id, buyer_id, buyer["business_email"], None,
                        buyer["source_system"], buyer["source_ref"], buyer["source_url"],
                        buyer["checked_at"], 1, now))
            db.execute("INSERT INTO opportunity VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (oid, product_id, target_id, buyer_id, operator_id.strip(),
                        OpportunityStatus.SYNTHETIC_DRAFT.value, 1, "synthetic", oid, now, now))
            db.execute("INSERT INTO rfq VALUES (?, ?, ?, ?, ?)",
                       (case["rfq"]["id"], oid, case["rfq"]["id"], now,
                        _json({"sku": quote["sku"], "quantity": quote["quantity"],
                               "unit": quote["unit"]})))
            self._insert_quote(db, quote, now)
            db.execute("INSERT INTO customer_po VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (po["id"], oid, po["quotation_id"], po["quotation_revision"],
                        None, po["sku"], str(po["quantity"]), po["unit"], po["currency"],
                        str(po["unit_price"]), str(po["total"]), po["accepted_by"], now))
            order = result.sales_order
            db.execute("INSERT INTO sales_order VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (order["id"], oid, po["id"], f"order:{po['id']}:{quote['revision']}",
                        order["quantity"], order["unit"], order["currency"],
                        order["unit_price"], order["total"], order["status"], None, now))
            shipment = case["shipment"]
            db.execute("INSERT INTO shipment VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (f"SH-{oid}", order["id"], shipment["destination"],
                        int(shipment["boxes"]), str(shipment["net_weight_kg"]),
                        str(shipment["gross_weight_kg"]), now))
            for kind, payload in (("COMMERCIAL_INVOICE_DRAFT", result.commercial_invoice_draft),
                                  ("PACKING_LIST_DRAFT", result.packing_list_draft)):
                db.execute("INSERT INTO document_draft VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (f"{kind}-{oid}", order["id"], kind, 1,
                            _json(payload), "DRAFT_REQUIRES_REVIEW", now))
            db.execute("INSERT INTO case_ingest VALUES (?, ?, ?, ?)",
                       (oid, digest, order["id"], now))
            db.execute("INSERT INTO audit_event (opportunity_id, action, actor_id, target_id, before_state, after_state, source_ref, occurred_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (oid, "SYNTHETIC_CASE_INGESTED", operator_id.strip(), oid,
                        None, OpportunityStatus.SYNTHETIC_DRAFT.value, buyer["source_ref"], now))
        return result

    @staticmethod
    def _insert_quote(db: sqlite3.Connection, quote: dict[str, Any], now: str) -> None:
        db.execute("INSERT INTO quotation_revision VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (quote["id"], quote["revision"], quote["rfq_id"], quote["sku"],
                    str(quote["quantity"]), quote["unit"], quote["currency"],
                    str(quote["unit_price"]), _json(quote.get("terms", {})),
                    quote["status"], quote.get("approved_by"), now))

    def append_quotation_revision(self, quote: dict[str, Any]) -> None:
        """Add a later revision; old revisions and their PO references stay immutable."""
        now = _utc_now()
        with self._transaction() as db:
            prior = db.execute(
                "SELECT revision, rfq_id, sku, unit, currency FROM quotation_revision WHERE quotation_id = ? ORDER BY revision DESC LIMIT 1",
                (quote["id"],),
            ).fetchone()
            if prior is not None:
                validate_quotation_revision(
                    quote, sku=prior["sku"], unit=prior["unit"], currency=prior["currency"]
                )
                if quote["currency"] != self.scope.currency:
                    raise ValueError("Quotation currency is outside the pilot scope")
            if prior is None or quote["revision"] != prior["revision"] + 1:
                raise ValueError("Quotation revision must follow the prior revision")
            if quote["rfq_id"] != prior["rfq_id"]:
                raise ValueError("Quotation revision cannot change its RFQ")
            self._insert_quote(db, quote, now)
            oid = db.execute("SELECT opportunity_id FROM rfq WHERE id = ?", (quote["rfq_id"],)).fetchone()
            if oid is None:
                raise ValueError("RFQ does not exist")
            db.execute("INSERT INTO audit_event (opportunity_id, action, actor_id, target_id, before_state, after_state, source_ref, occurred_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (oid[0], "QUOTATION_REVISION_ADDED", quote.get("approved_by") or "system",
                        quote["id"], str(prior["revision"]), str(quote["revision"]), quote["rfq_id"], now))

    def transition(
        self, opportunity_id: str, target: OpportunityStatus, actor_id: str,
        *, approval_target_id: str | None = None, approval_revision: int | None = None,
    ) -> None:
        if not actor_id.strip():
            raise ValueError("Transition requires an actor")
        with self._transaction() as db:
            row = db.execute("SELECT status FROM opportunity WHERE id = ?", (opportunity_id,)).fetchone()
            if row is None:
                raise ValueError("Opportunity does not exist")
            current = OpportunityStatus(row["status"])
            require_transition(current, target)
            self._require_approval(
                db, opportunity_id, target, approval_target_id, approval_revision
            )
            now = _utc_now()
            db.execute("UPDATE opportunity SET status = ?, revision = revision + 1, updated_at_utc = ? WHERE id = ?",
                       (target.value, now, opportunity_id))
            db.execute("INSERT INTO audit_event (opportunity_id, action, actor_id, target_id, before_state, after_state, occurred_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (opportunity_id, "STATUS_CHANGED", actor_id, opportunity_id,
                        current.value, target.value, now))

    @staticmethod
    def _require_approval(
        db: sqlite3.Connection, oid: str, target: OpportunityStatus,
        target_id: str | None, revision: int | None,
    ) -> None:
        if target == OpportunityStatus.RFQ_RECEIVED:
            # An inbound RFQ can arrive without prior outreach, but it must exist.
            evidence = db.execute("SELECT 1 FROM rfq WHERE opportunity_id = ? LIMIT 1", (oid,)).fetchone()
            if evidence is None:
                raise ValueError("RFQ evidence is required")
            outreach = db.execute("SELECT 1 FROM outreach WHERE opportunity_id = ? LIMIT 1", (oid,)).fetchone()
            if outreach is not None:
                approved = db.execute("""SELECT 1 FROM outreach o JOIN approval a
                    ON a.opportunity_id = o.opportunity_id AND a.target_id = o.id
                    AND a.content_hash = o.content_hash
                    WHERE o.opportunity_id = ? AND a.action = 'APPROVE_OUTREACH'
                    AND a.decision = 'APPROVED' AND o.approval_id = a.id
                    LIMIT 1""", (oid,)).fetchone()
                if approved is None:
                    raise ValueError("Matching outreach content approval is required")
        elif target == OpportunityStatus.QUOTE_APPROVED:
            if not target_id or not isinstance(revision, int) or isinstance(revision, bool):
                raise ValueError("Quotation revision approval target is required")
            evidence = db.execute("""SELECT 1 FROM approval a
                JOIN quotation_revision q ON q.quotation_id = a.target_id
                                      AND q.revision = a.target_revision
                JOIN rfq r ON r.id = q.rfq_id
                WHERE r.opportunity_id = ? AND a.opportunity_id = ?
                  AND a.target_id = ? AND a.target_revision = ?
                  AND a.action = 'APPROVE_QUOTE' AND a.decision = 'APPROVED'
                  AND q.status = 'APPROVED' AND q.approved_by = a.actor_id
                  AND q.revision = (SELECT MAX(q2.revision) FROM quotation_revision q2
                                    WHERE q2.quotation_id = q.quotation_id)
                LIMIT 1""", (oid, oid, target_id, revision)).fetchone()
            if evidence is None:
                raise ValueError("Matching quotation revision approval is required")
        elif target == OpportunityStatus.ORDER_DRAFT:
            if not target_id or not isinstance(revision, int) or isinstance(revision, bool):
                raise ValueError("Customer PO approval target is required")
            evidence = db.execute("""SELECT 1 FROM approval a
                JOIN customer_po p ON p.id = a.target_id AND p.opportunity_id = a.opportunity_id
                WHERE a.opportunity_id = ? AND a.target_id = ? AND a.target_revision = ?
                  AND a.action = 'APPROVE_PO'
                  AND a.decision = 'APPROVED' AND a.actor_id = p.accepted_by
                  AND a.target_revision = p.quotation_revision
                LIMIT 1""", (oid, target_id, revision)).fetchone()
            if evidence is None:
                raise ValueError("Matching customer PO approval is required")

    def record_approval(
        self, opportunity_id: str, action: str, target_id: str,
        revision: int, actor_id: str,
    ) -> None:
        """Record explicit synthetic approval evidence; caller authentication is FND-006."""
        if action not in ("APPROVE_QUOTE", "APPROVE_PO") or not actor_id.strip():
            raise ValueError("Approval action and named actor are required")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("Approval requires a positive target revision")
        with self._transaction() as db:
            if action == "APPROVE_QUOTE":
                match = db.execute("""SELECT 1 FROM quotation_revision q JOIN rfq r ON r.id = q.rfq_id
                    WHERE r.opportunity_id = ? AND q.quotation_id = ? AND q.revision = ?
                    AND q.status = 'APPROVED' AND q.approved_by = ?""",
                    (opportunity_id, target_id, revision, actor_id)).fetchone()
            else:
                match = db.execute("""SELECT 1 FROM customer_po WHERE opportunity_id = ? AND id = ?
                    AND quotation_revision = ? AND accepted_by = ?""",
                    (opportunity_id, target_id, revision, actor_id)).fetchone()
            if match is None:
                raise ValueError("Approval target and actor do not match reviewed record")
            now = _utc_now()
            approval_id = f"{action}:{target_id}:{revision}"
            db.execute("INSERT INTO approval VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (approval_id, opportunity_id, action, target_id, revision,
                        None, actor_id.strip(), "APPROVED", now))
            db.execute("INSERT INTO audit_event (opportunity_id, action, actor_id, target_id, before_state, after_state, occurred_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (opportunity_id, action, actor_id.strip(), target_id, None, "APPROVED", now))

    def read_summary(self, opportunity_id: str) -> dict[str, Any] | None:
        """Read a small contact-free view suitable for diagnostics."""
        with closing(self._connect()) as db:
            row = db.execute("""SELECT o.id, o.status, o.revision, s.id AS order_id,
                                     s.total_text AS order_total, s.currency,
                                     (SELECT COUNT(*) FROM quotation_revision q
                                      JOIN rfq r ON r.id = q.rfq_id
                                      WHERE r.opportunity_id = o.id) AS quotation_revisions
                              FROM opportunity o LEFT JOIN sales_order s ON s.opportunity_id = o.id
                              WHERE o.id = ?""", (opportunity_id,)).fetchone()
            return dict(row) if row is not None else None

    def backup_to(self, destination: str | Path) -> None:
        """Create a consistent SQLite backup, including sensitive contact records."""
        destination = Path(destination)
        if destination.resolve() == self.path.resolve():
            raise ValueError("Backup destination must differ from the live database")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as source, closing(sqlite3.connect(destination)) as backup:
            source.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("Backup integrity check failed")
