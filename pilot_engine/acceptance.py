"""Deterministic mock boundaries for the Foundation end-to-end acceptance run."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from pilot_engine.retry import ExternalAttemptState, ExternalNextStep, plan_external_action
from pilot_engine.store import PilotStore
from pilot_engine.workflow import PilotResult, run_case


@dataclass(frozen=True)
class SyntheticSendApproval:
    recipient: str
    content_sha256: str
    reviewed_by: str


class MockMail:
    """A controlled in-memory mailbox; never contacts a provider."""

    def __init__(self) -> None:
        self.messages: dict[str, tuple[str, str]] = {}
        self.uncertain_keys: set[str] = set()

    def send(self, key: str, recipient: str, content: str,
             approval: SyntheticSendApproval) -> None:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if (not approval.reviewed_by.strip() or approval.recipient != recipient
                or approval.content_sha256 != digest):
            raise ValueError("Exact synthetic recipient/content approval required")
        state = (ExternalAttemptState.UNKNOWN if key in self.uncertain_keys else
                 ExternalAttemptState.CONFIRMED if key in self.messages else
                 ExternalAttemptState.NOT_STARTED)
        decision = plan_external_action(state, key)
        if decision is ExternalNextStep.RECONCILE_PROVIDER:
            raise RuntimeError("Unknown delivery requires reconciliation")
        if decision is ExternalNextStep.FIRST_ATTEMPT:
            self.messages[key] = (recipient, digest)
        elif self.messages[key] != (recipient, digest):
            raise ValueError("Idempotency key cannot be reused with different content")


class MockERP:
    """Records a reviewed export payload, not an ERP order."""

    def __init__(self) -> None:
        self.exports: dict[str, dict[str, Any]] = {}

    def prepare(self, order: dict[str, Any]) -> None:
        key = order["id"]
        if key in self.exports and self.exports[key] != order:
            raise ValueError("Order export changed under the same ID")
        self.exports[key] = dict(order)


def run_synthetic_acceptance(case: dict[str, Any], store: PilotStore,
                             mail: MockMail, erp: MockERP,
                             approval: SyntheticSendApproval, content: str) -> PilotResult:
    """Exercise fake Find → Sell → Execute boundaries with one linked case."""
    result = run_case(case, store.scope)
    recipient = case["buyer"]["business_email"]
    mail.send(f"outreach:{result.opportunity_id}", recipient, content, approval)
    persisted = store.save_synthetic_case(case)
    erp.prepare(persisted.sales_order)
    return persisted
