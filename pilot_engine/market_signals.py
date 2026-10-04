"""Source-backed aggregate trade observations for the selected Find corridor."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from pilot_engine.store import PilotStore, _json, _utc_now


PREVIEW_ENDPOINT = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
GERMANY_REPORTER = "276"
LIMITATIONS = (
    "UN Comtrade public preview is limited to 500 rows and may be revised or unavailable.",
    "HS6 is a broad research filter, not confirmed SKU classification or application fit.",
    "Aggregate import value does not identify a buyer, contact or addressable demand.",
    "Reporter import statistics may differ from exporter-reported flows and product years.",
)
MAX_RESPONSE_BYTES = 1_000_000


def _amount(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("Invalid trade amount")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid trade amount") from exc
    if not number.is_finite() or number < 0:
        raise ValueError("Invalid trade amount")
    return format(number, "f")


def _utc(value: str) -> str:
    try:
        timestamp = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Observation time must be ISO 8601 UTC") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset().total_seconds() != 0:
        raise ValueError("Observation time must be ISO 8601 UTC")
    return timestamp.astimezone(timezone.utc).isoformat(timespec="seconds")


class MarketSignals:
    """Persist evidence without treating a statistic as a company lead."""

    def __init__(self, store: PilotStore):
        self.store = store

    def _query(self, year: int) -> dict[str, str]:
        if isinstance(year, bool) or not isinstance(year, int) or not 1990 <= year <= 2100:
            raise ValueError("Year must be a reporting year")
        if self.store.scope.country != "DE":
            raise ValueError("Public preview adapter currently supports Germany only")
        return {"period": str(year), "reporterCode": GERMANY_REPORTER,
                "cmdCode": self.store.scope.hs6, "flowCode": "M", "partnerCode": "0",
                "maxRecords": "500"}

    def fetch_public_preview(
        self, year: int, *, opener: Callable[..., Any] = urlopen,
    ) -> dict[str, Any]:
        """Explicit, bounded read of the public API; network is never used on import."""
        query = self._query(year)
        url = f"{PREVIEW_ENDPOINT}?{urlencode(query)}"
        request = Request(url, headers={"User-Agent": "export-growth-operations-engine/0.1"})
        observed = _utc_now()
        raw = None
        status, category, value, weight, description = "FAILED", "TRANSPORT", None, None, None
        quality: dict[str, Any] = {}
        try:
            with closing(opener(request, timeout=15)) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("Oversized response")
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise ValueError("Malformed response")
            if payload.get("error"):
                raise ValueError("Provider error")
            rows = payload["data"]
            if not rows:
                status, category = "MISSING", None
            elif len(rows) != 1:
                status, category = "FAILED", "AMBIGUOUS_ROWS"
            else:
                row = rows[0]
                if (not isinstance(row, dict) or str(row.get("cmdCode")) != query["cmdCode"]
                        or str(row.get("reporterCode")) != query["reporterCode"]
                        or str(row.get("partnerCode")) != "0"
                        or str(row.get("flowCode")) != "M"
                        or str(row.get("period")) != query["period"]):
                    raise ValueError("Mismatched response scope")
                value = _amount(row.get("primaryValue"))
                weight = (_amount(row["netWgt"]) if row.get("netWgt") is not None else None)
                description = str(row.get("cmdDesc") or "")[:500]
                quality = {key: row.get(key) for key in (
                    "classificationCode", "isOriginalClassification", "isAggregate",
                    "isReported", "isNetWgtEstimated", "isQtyEstimated",
                )}
                status, category = "AVAILABLE", None
        except (URLError, TimeoutError, OSError):
            pass
        except (ValueError, TypeError, json.JSONDecodeError):
            category = "INVALID_RESPONSE"
        return self._save(
            source_system="UN_COMTRADE_PREVIEW", source_url=url, query=query,
            observed_at_utc=observed, year=year, status=status, trade_value_usd=value,
            net_weight_kg=weight, description=description,
            raw_sha256=hashlib.sha256(raw).hexdigest() if raw is not None else None,
            failure_category=category, quality=quality,
        )

    def record_manual(
        self, *, year: int, source_url: str, observed_at_utc: str,
        trade_value_usd: str | None, description: str, source_note: str,
    ) -> dict[str, Any]:
        """Record an operator-supplied aggregate value with its source, not a buyer."""
        query = self._query(year)
        if (not isinstance(source_url, str) or not source_url.startswith("https://")
                or not isinstance(source_note, str) or not source_note.strip()
                or not isinstance(description, str) or not description.strip()):
            raise ValueError("Manual source URL, description and note are required")
        observed = _utc(observed_at_utc)
        status = "MISSING" if trade_value_usd is None else "AVAILABLE"
        return self._save(
            source_system="MANUAL", source_url=source_url,
            query={**query, "operator_note": source_note.strip()},
            observed_at_utc=observed, year=year, status=status,
            trade_value_usd=_amount(trade_value_usd) if trade_value_usd is not None else None,
            net_weight_kg=None, description=description.strip(), raw_sha256=None,
            failure_category=None, quality={},
        )

    def _save(
        self, *, source_system: str, source_url: str, query: dict[str, str],
        observed_at_utc: str, year: int, status: str, trade_value_usd: str | None,
        net_weight_kg: str | None, description: str | None,
        raw_sha256: str | None, failure_category: str | None,
        quality: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot_id = f"MS-{uuid4()}"
        actor = self.store._require_access("RECORD_MARKET", snapshot_id)
        with self.store._transaction() as db:
            db.execute("""INSERT INTO market_signal_snapshot
                (id, source_system, source_url, query_json, observed_at_utc,
                 recorded_at_utc, actor_id, hs6, country_code, period, direction, status,
                 trade_value_usd_text, net_weight_kg_text, description, raw_sha256,
                 quality_json, limitation_json, failure_category)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (snapshot_id, source_system, source_url, _json(query), observed_at_utc,
                 _utc_now(), actor, self.store.scope.hs6, self.store.scope.country,
                 year, "IMPORT", status, trade_value_usd, net_weight_kg,
                 description, raw_sha256, _json(quality), _json(LIMITATIONS), failure_category))
        return self.read(snapshot_id)

    def read(self, snapshot_id: str) -> dict[str, Any] | None:
        self.store._require_access("READ_MARKET", snapshot_id)
        with closing(self.store._connect()) as db:
            row = db.execute("SELECT * FROM market_signal_snapshot WHERE id = ?",
                             (snapshot_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["query"] = json.loads(result.pop("query_json"))
            result["limitations"] = json.loads(result.pop("limitation_json"))
            result["quality"] = json.loads(result.pop("quality_json"))
            result["unit"] = "USD"
            result["net_weight_unit"] = "kg"
            result["buyer_evidence"] = False
            result["direct_company_research_allowed"] = True
            return result
