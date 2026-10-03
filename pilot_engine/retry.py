"""Bounded read retries and fail-closed external action recovery rules."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, TypeVar


T = TypeVar("T")


class TransientReadError(Exception):
    """An explicitly classified temporary failure of a read-only operation."""


@dataclass(frozen=True)
class ReadRetryPolicy:
    max_attempts: int = 3
    initial_delay_seconds: float = 0.5
    max_delay_seconds: float = 2.0

    def __post_init__(self) -> None:
        if (isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int)
                or not 1 <= self.max_attempts <= 5):
            raise ValueError("Read retry budget must be between 1 and 5 attempts")
        if (not 0 <= self.initial_delay_seconds <= self.max_delay_seconds <= 30
                or isinstance(self.initial_delay_seconds, bool)
                or isinstance(self.max_delay_seconds, bool)):
            raise ValueError("Read retry delays must be bounded and nonnegative")

    def run(self, read_only: Callable[[], T], *, sleep: Callable[[float], None] = time.sleep) -> T:
        """Only an explicitly read-only callable may use this retry wrapper."""
        for attempt in range(self.max_attempts):
            try:
                return read_only()
            except TransientReadError:
                if attempt + 1 == self.max_attempts:
                    raise
                sleep(min(self.max_delay_seconds, self.initial_delay_seconds * 2**attempt))
        raise AssertionError("Unreachable retry state")


class ExternalAttemptState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_FLIGHT = "IN_FLIGHT"
    UNKNOWN = "UNKNOWN"
    CONFIRMED = "CONFIRMED"
    FAILED_SAFE = "FAILED_SAFE"


class ExternalNextStep(StrEnum):
    FIRST_ATTEMPT = "FIRST_ATTEMPT"
    RECONCILE_PROVIDER = "RECONCILE_PROVIDER"
    ALREADY_CONFIRMED = "ALREADY_CONFIRMED"
    OPERATOR_REVIEW = "OPERATOR_REVIEW"


def plan_external_action(state: ExternalAttemptState, idempotency_key: str) -> ExternalNextStep:
    """Never authorize an automatic repeat of email or ERP writes."""
    if not isinstance(state, ExternalAttemptState):
        raise ValueError("Unknown external attempt state")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 128:
        raise ValueError("External action requires a stable idempotency key")
    return {
        ExternalAttemptState.NOT_STARTED: ExternalNextStep.FIRST_ATTEMPT,
        ExternalAttemptState.IN_FLIGHT: ExternalNextStep.RECONCILE_PROVIDER,
        ExternalAttemptState.UNKNOWN: ExternalNextStep.RECONCILE_PROVIDER,
        ExternalAttemptState.CONFIRMED: ExternalNextStep.ALREADY_CONFIRMED,
        ExternalAttemptState.FAILED_SAFE: ExternalNextStep.OPERATOR_REVIEW,
    }[state]
