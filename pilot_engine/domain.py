"""Shared pilot identifiers and allowed lifecycle transitions.

This module defines a contract only. It does not persist state or authorize
external actions; those responsibilities belong to the application layer.
"""

from dataclasses import dataclass
from enum import StrEnum


SCHEMA_VERSION = "0.1.0"


@dataclass(frozen=True)
class PilotScope:
    hs6: str
    country: str
    currency: str


DEFAULT_PILOT_SCOPE = PilotScope("732690", "DE", "EUR")


class OpportunityStatus(StrEnum):
    SYNTHETIC_DRAFT = "SYNTHETIC_DRAFT"
    DISCOVERED = "DISCOVERED"
    CONTACT_REVIEW = "CONTACT_REVIEW"
    CONTACT_READY = "CONTACT_READY"
    OUTREACH_REVIEW = "OUTREACH_REVIEW"
    RFQ_RECEIVED = "RFQ_RECEIVED"
    QUOTE_REVIEW = "QUOTE_REVIEW"
    QUOTE_APPROVED = "QUOTE_APPROVED"
    PO_REVIEW = "PO_REVIEW"
    ORDER_DRAFT = "ORDER_DRAFT"
    DOCUMENT_REVIEW = "DOCUMENT_REVIEW"
    CLOSED = "CLOSED"


ALLOWED_TRANSITIONS: dict[OpportunityStatus, frozenset[OpportunityStatus]] = {
    OpportunityStatus.SYNTHETIC_DRAFT: frozenset(),
    OpportunityStatus.DISCOVERED: frozenset({OpportunityStatus.CONTACT_REVIEW, OpportunityStatus.CLOSED}),
    OpportunityStatus.CONTACT_REVIEW: frozenset({OpportunityStatus.CONTACT_READY, OpportunityStatus.CLOSED}),
    OpportunityStatus.CONTACT_READY: frozenset({OpportunityStatus.OUTREACH_REVIEW, OpportunityStatus.CLOSED}),
    OpportunityStatus.OUTREACH_REVIEW: frozenset({OpportunityStatus.RFQ_RECEIVED, OpportunityStatus.CLOSED}),
    OpportunityStatus.RFQ_RECEIVED: frozenset({OpportunityStatus.QUOTE_REVIEW, OpportunityStatus.CLOSED}),
    OpportunityStatus.QUOTE_REVIEW: frozenset({OpportunityStatus.QUOTE_APPROVED, OpportunityStatus.CLOSED}),
    OpportunityStatus.QUOTE_APPROVED: frozenset({OpportunityStatus.PO_REVIEW, OpportunityStatus.CLOSED}),
    OpportunityStatus.PO_REVIEW: frozenset({OpportunityStatus.ORDER_DRAFT, OpportunityStatus.CLOSED}),
    OpportunityStatus.ORDER_DRAFT: frozenset({OpportunityStatus.DOCUMENT_REVIEW, OpportunityStatus.CLOSED}),
    OpportunityStatus.DOCUMENT_REVIEW: frozenset({OpportunityStatus.CLOSED}),
    OpportunityStatus.CLOSED: frozenset(),
}


def require_transition(current: OpportunityStatus, target: OpportunityStatus) -> None:
    """Reject skips; the application must separately check approval evidence."""
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"Illegal opportunity transition: {current} → {target}")
