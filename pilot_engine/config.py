"""Explicit, non-secret configuration for the local pilot process."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from pilot_engine.domain import DEFAULT_PILOT_SCOPE, PilotScope


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PILOT = "pilot"


@dataclass(frozen=True)
class AppConfig:
    environment: Environment
    scope: PilotScope
    data_dir: Path | None
    secret_dir: Path | None = None

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> AppConfig:
        env = os.environ if values is None else values
        try:
            stage = Environment(env.get("EGO_ENV", "development"))
        except ValueError as exc:
            raise ValueError("EGO_ENV must be development, test, or pilot") from exc

        fields = ("EGO_HS6", "EGO_COUNTRY", "EGO_CURRENCY")
        if stage is Environment.PILOT and any(not env.get(field) for field in fields):
            raise ValueError("Pilot requires EGO_HS6, EGO_COUNTRY, and EGO_CURRENCY")
        scope = PilotScope(
            env.get("EGO_HS6", DEFAULT_PILOT_SCOPE.hs6),
            env.get("EGO_COUNTRY", DEFAULT_PILOT_SCOPE.country),
            env.get("EGO_CURRENCY", DEFAULT_PILOT_SCOPE.currency),
        )
        if not (len(scope.hs6) == 6 and scope.hs6.isascii() and scope.hs6.isdigit()):
            raise ValueError("EGO_HS6 must be six ASCII digits")
        if not (len(scope.country) == 2 and scope.country.isascii() and scope.country.isalpha()
                and scope.country.isupper()):
            raise ValueError("EGO_COUNTRY must be a two-letter uppercase country code")
        if not (len(scope.currency) == 3 and scope.currency.isascii() and scope.currency.isalpha()
                and scope.currency.isupper()):
            raise ValueError("EGO_CURRENCY must be a three-letter uppercase currency code")

        raw_dir = env.get("EGO_DATA_DIR")
        if stage is Environment.PILOT and not raw_dir:
            raise ValueError("Pilot requires EGO_DATA_DIR")
        data_dir = Path(raw_dir).expanduser() if raw_dir else None
        if data_dir is not None:
            if data_dir.is_symlink() or not data_dir.is_dir():
                raise ValueError("EGO_DATA_DIR must be an existing non-symlink directory")
            metadata = data_dir.stat()
            if (not hasattr(os, "geteuid") or metadata.st_uid != os.geteuid()
                    or metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
                raise PermissionError("EGO_DATA_DIR must be owned by this OS user and mode 0700")
            data_dir = data_dir.resolve()
        secret_dir = Path(env["EGO_SECRET_DIR"]).expanduser() if env.get("EGO_SECRET_DIR") else None
        if secret_dir is not None:
            from pilot_engine.secrets import SecretStore

            secret_dir = SecretStore(secret_dir).directory.resolve()
        return cls(stage, scope, data_dir, secret_dir)

    def open_store(self):
        """Open this environment's database only in its private data directory."""
        if self.data_dir is None:
            raise ValueError("EGO_DATA_DIR is required to open a database")
        from pilot_engine.store import PilotStore

        return PilotStore(self.data_dir / "pilot.sqlite3", scope=self.scope)

    def open_secrets(self):
        """Return the local secret source only when a connector needs one."""
        if self.secret_dir is None:
            raise ValueError("EGO_SECRET_DIR is required for connector credentials")
        from pilot_engine.secrets import SecretStore

        return SecretStore(self.secret_dir)

    def open_observer(self):
        """Write operational records beside the local pilot database."""
        if self.data_dir is None:
            raise ValueError("EGO_DATA_DIR is required for operational logs")
        from pilot_engine.observability import PilotLogger

        return PilotLogger(self.data_dir / "operations.jsonl")
