"""Small, payload-free operational event stream for the local pilot."""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4


class Event(StrEnum):
    SYNTHETIC_RUN = "SYNTHETIC_RUN"


class Outcome(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    IO_ERROR = "IO_ERROR"


def new_correlation_id() -> str:
    return str(uuid4())


class PilotLogger:
    """Append restricted JSONL events; business audit stays in SQLite."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._check_parent()

    def _check_parent(self) -> None:
        parent = self.path.parent
        if parent.is_symlink() or not parent.is_dir():
            raise PermissionError("Log directory must be an existing non-symlink directory")
        metadata = parent.stat()
        if (not hasattr(os, "geteuid") or metadata.st_uid != os.geteuid()
                or metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
            raise PermissionError("Log directory must be owned by this OS user and mode 0700")

    def emit(
        self, event: Event, outcome: Outcome, correlation_id: str,
        *, error_code: ErrorCode | None = None, duration_ms: int | None = None,
    ) -> None:
        if not isinstance(event, Event) or not isinstance(outcome, Outcome):
            raise ValueError("Unknown operational event or outcome")
        try:
            if str(UUID(correlation_id)) != correlation_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Correlation ID must be a canonical UUID") from exc
        if ((outcome is Outcome.FAILED) != (error_code is not None)
                or (error_code is not None and not isinstance(error_code, ErrorCode))):
            raise ValueError("Failure requires one bounded error category")
        if duration_ms is not None and (isinstance(duration_ms, bool)
                                        or not isinstance(duration_ms, int) or duration_ms < 0):
            raise ValueError("Duration must be a nonnegative integer")

        record = {"occurred_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "event": event.value, "outcome": outcome.value,
                  "correlation_id": correlation_id, "error_code": error_code.value if error_code else None,
                  "duration_ms": duration_ms}
        content = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
        self._check_parent()
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                     0o600)
        try:
            metadata = os.fstat(fd)
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                    or metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
                raise PermissionError("Log file must be an owner-only regular file")
            if os.write(fd, content) != len(content):
                raise OSError("Incomplete operational log write")
        finally:
            os.close(fd)
