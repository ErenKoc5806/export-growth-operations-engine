"""SQLite pilot store. Synthetic case ingestion is atomic and retry safe."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from pilot_engine.domain import OpportunityStatus, require_transition
from pilot_engine.workflow import PilotResult, run_case


MIGRATION = Path(__file__).resolve().parent / "migrations" / "001_initial.sql"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class PilotStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(self.path) == ":memory:":
            raise ValueError("Use a file path for durable pilot storage")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                try:
                    db.executescript(MIGRATION.read_text(encoding="utf-8"))
                except Exception:
                    db.rollback()
                    raise
            elif version != 1:
                raise ValueError(f"Unsupported database schema version: {version}")

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
        result = run_case(case)
        digest = hashlib.sha256(_json(case).encode("utf-8")).hexdigest()
        checked_at = datetime.fromisoformat(case["buyer"]["checked_at"])
        if checked_at.tzinfo is None or checked_at.utcoffset() != timezone.utc.utcoffset(checked_at):
            raise ValueError("Contact checked_at must be an ISO 8601 UTC timestamp")
        now = _utc_now()
        oid = result.opportunity_id
        product, buyer = case["product"], case["buyer"]
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
                       (manufacturer_id, "Synthetic manufacturer", now))
            db.execute("INSERT INTO product VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (product_id, manufacturer_id, product["sku"], product["description"],
                        product["unit"], product["hs6"], None, "UNVERIFIED", now, now))
            db.execute("INSERT INTO market_target VALUES (?, ?, ?, ?, ?)",
                       (target_id, product_id, case["target_country"],
                        _json(product.get("search_terms", [])), now))
            db.execute("INSERT INTO buyer_company VALUES (?, ?, ?, ?, ?)",
                       (buyer_id, buyer["company"], "DE", "BUYER", now))
            db.execute("INSERT INTO contact_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (contact_id, buyer_id, buyer["business_email"], None,
                        buyer["source_system"], buyer["source_ref"], buyer["source_url"],
                        buyer["checked_at"], 1, now))
            db.execute("INSERT INTO opportunity VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (oid, product_id, target_id, buyer_id, "Synthetic Operator",
                        OpportunityStatus.DISCOVERED.value, 1, "synthetic", oid, now, now))
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
                       (oid, "SYNTHETIC_CASE_INGESTED", "Synthetic Operator", oid,
                        None, OpportunityStatus.DISCOVERED.value, buyer["source_ref"], now))
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
                "SELECT revision, rfq_id FROM quotation_revision WHERE quotation_id = ? ORDER BY revision DESC LIMIT 1",
                (quote["id"],),
            ).fetchone()
            if prior is None or quote["revision"] != prior["revision"] + 1:
                raise ValueError("Quotation revision must follow the prior revision")
            if quote["rfq_id"] != prior["rfq_id"]:
                raise ValueError("Quotation revision cannot change its RFQ")
            if quote["status"] == "APPROVED" and not quote.get("approved_by"):
                raise ValueError("Approved quotation revision requires a named approver")
            self._insert_quote(db, quote, now)
            oid = db.execute("SELECT opportunity_id FROM rfq WHERE id = ?", (quote["rfq_id"],)).fetchone()
            if oid is None:
                raise ValueError("RFQ does not exist")
            db.execute("INSERT INTO audit_event (opportunity_id, action, actor_id, target_id, before_state, after_state, source_ref, occurred_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (oid[0], "QUOTATION_REVISION_ADDED", quote.get("approved_by") or "system",
                        quote["id"], str(prior["revision"]), str(quote["revision"]), quote["rfq_id"], now))

    def transition(self, opportunity_id: str, target: OpportunityStatus, actor_id: str) -> None:
        if not actor_id.strip():
            raise ValueError("Transition requires an actor")
        with self._transaction() as db:
            row = db.execute("SELECT status FROM opportunity WHERE id = ?", (opportunity_id,)).fetchone()
            if row is None:
                raise ValueError("Opportunity does not exist")
            current = OpportunityStatus(row["status"])
            require_transition(current, target)
            now = _utc_now()
            db.execute("UPDATE opportunity SET status = ?, revision = revision + 1, updated_at_utc = ? WHERE id = ?",
                       (target.value, now, opportunity_id))
            db.execute("INSERT INTO audit_event (opportunity_id, action, actor_id, target_id, before_state, after_state, occurred_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (opportunity_id, "STATUS_CHANGED", actor_id, opportunity_id,
                        current.value, target.value, now))

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
