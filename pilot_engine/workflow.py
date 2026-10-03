"""A deterministic local acceptance slice; no external action is performed."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from pilot_engine.domain import SCHEMA_VERSION


class PilotValidationError(ValueError):
    """The case needs operator review before progressing."""


@dataclass(frozen=True)
class PilotResult:
    opportunity_id: str
    sales_order: dict[str, Any]
    commercial_invoice_draft: dict[str, Any]
    packing_list_draft: dict[str, Any]
    audit_events: tuple[dict[str, str], ...]


def _required(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PilotValidationError(f"{key} is required")
    return value.strip()


def _positive_number(value: Any, key: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise PilotValidationError(f"{key} must be a positive decimal")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise PilotValidationError(f"{key} must be a positive decimal") from exc
    if not number.is_finite() or number <= 0:
        raise PilotValidationError(f"{key} must be a positive decimal")
    return number


def _section(case: dict[str, Any], key: str) -> dict[str, Any]:
    section = case.get(key)
    if not isinstance(section, dict):
        raise PilotValidationError(f"{key} must be an object")
    return section


def run_case(case: dict[str, Any]) -> PilotResult:
    """Validate one linked case and generate reviewed-only order/document drafts.

    The caller owns persistence, authorizations, delivery, and ERP integration.
    On validation failure this pure function produces no order or documents.
    """
    if not isinstance(case, dict) or case.get("synthetic") is not True:
        raise PilotValidationError("Only explicitly synthetic cases are accepted")
    if case.get("schema_version") != SCHEMA_VERSION:
        raise PilotValidationError("Unsupported pilot schema version")

    opportunity_id = _required(case, "opportunity_id")
    product = _section(case, "product")
    buyer = _section(case, "buyer")
    rfq = _section(case, "rfq")
    quote = _section(case, "quotation")
    po = _section(case, "customer_po")
    shipment = _section(case, "shipment")

    if product.get("hs6") != "732690" or case.get("target_country") != "DE":
        raise PilotValidationError("Pilot filter must be 732690 → DE")
    sku = _required(product, "sku")
    description = _required(product, "description")
    unit = _required(product, "unit")
    company = _required(buyer, "company")
    contact = _required(buyer, "business_email")
    source = _required(buyer, "source_url")
    _required(buyer, "checked_at")
    _required(buyer, "source_ref")
    if buyer.get("source_system") != "synthetic":
        raise PilotValidationError("Synthetic contact must declare its source system")
    if not source.startswith("https://") or buyer.get("verified") is not True:
        raise PilotValidationError("Buyer contact needs verified source evidence")
    if "@" not in contact:
        raise PilotValidationError("Buyer email is invalid")

    rfq_id = _required(rfq, "id")
    quote_id = _required(quote, "id")
    po_id = _required(po, "id")
    if rfq.get("opportunity_id") != opportunity_id:
        raise PilotValidationError("RFQ opportunity does not match")
    if quote.get("rfq_id") != rfq_id or po.get("quotation_id") != quote_id:
        raise PilotValidationError("RFQ, quotation and PO references do not match")
    revision = quote.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise PilotValidationError("Quotation revision must be a positive integer")
    if po.get("quotation_revision") != revision:
        raise PilotValidationError("Customer PO references a different quotation revision")
    if quote.get("status") != "APPROVED":
        raise PilotValidationError("Quotation requires a named human approver")
    _required(quote, "approved_by")
    _required(po, "accepted_by")

    quote_quantity = _positive_number(quote.get("quantity"), "quotation quantity")
    po_quantity = _positive_number(po.get("quantity"), "PO quantity")
    unit_price = _positive_number(quote.get("unit_price"), "unit price")
    po_price = _positive_number(po.get("unit_price"), "PO unit price")
    if unit == "PCS" and po_quantity != po_quantity.to_integral_value():
        raise PilotValidationError("PCS quantity must be a whole number")
    if unit_price != unit_price.quantize(Decimal("0.01")):
        raise PilotValidationError("Unit price must have at most two decimal places")
    if (quote.get("sku"), quote.get("unit"), quote.get("currency")) != (
        sku, unit, "EUR"
    ):
        raise PilotValidationError("Quotation product, unit or currency does not match")
    if (po.get("sku"), po.get("unit"), po.get("currency")) != (
        sku, unit, "EUR"
    ) or (po_quantity, po_price) != (quote_quantity, unit_price):
        raise PilotValidationError("Customer PO differs from approved quotation")
    total = (po_quantity * po_price).quantize(Decimal("0.01"))
    try:
        po_total = Decimal(str(po.get("total", "NaN")))
    except InvalidOperation as exc:
        raise PilotValidationError("Customer PO total is invalid") from exc
    if not po_total.is_finite() or po_total != total:
        raise PilotValidationError("Customer PO total does not reconcile")

    boxes = _positive_number(shipment.get("boxes"), "boxes")
    net_weight = _positive_number(shipment.get("net_weight_kg"), "net weight")
    gross_weight = _positive_number(shipment.get("gross_weight_kg"), "gross weight")
    if boxes != boxes.to_integral_value() or gross_weight < net_weight:
        raise PilotValidationError("Shipment boxes or weights are inconsistent")
    destination = _required(shipment, "destination")

    order_id = f"SO-{opportunity_id}"
    order = {
        "id": order_id, "opportunity_id": opportunity_id,
        "customer_po_id": po_id, "buyer": company, "sku": sku,
        "quotation_id": quote_id, "quotation_revision": revision,
        "quantity": str(po_quantity), "unit": unit,
        "currency": "EUR", "unit_price": str(unit_price),
        "total": str(total), "status": "DRAFT_REQUIRES_REVIEW",
    }
    invoice = {
        "type": "COMMERCIAL_INVOICE_DRAFT", "order_id": order_id,
        "buyer": company, "description": description, "quantity": str(po_quantity),
        "unit": unit, "currency": "EUR", "total": str(total),
        "destination": destination, "status": "DRAFT_REQUIRES_REVIEW",
    }
    packing = {
        "type": "PACKING_LIST_DRAFT", "order_id": order_id,
        "sku": sku, "quantity": str(po_quantity), "unit": unit,
        "boxes": int(boxes), "net_weight_kg": str(net_weight),
        "gross_weight_kg": str(gross_weight), "destination": destination,
        "status": "DRAFT_REQUIRES_REVIEW",
    }
    events = (
        {"stage": "FIND", "reference": _required(buyer, "source_ref"), "decision": "CONTACT_VERIFIED"},
        {"stage": "SELL", "reference": quote_id, "decision": "QUOTE_APPROVED"},
        {"stage": "EXECUTE", "reference": po_id, "decision": "PO_RECONCILED"},
    )
    return PilotResult(opportunity_id, order, invoice, packing, events)
