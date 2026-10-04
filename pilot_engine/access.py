"""Single-host pilot policy; the OS account is the authentication boundary."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    VIEWER = "VIEWER"
    OPERATOR = "OPERATOR"
    ADMIN = "ADMIN"
    SERVICE = "SERVICE"


PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.VIEWER: frozenset({"READ_SUMMARY"}),
    Role.OPERATOR: frozenset({"READ_SUMMARY", "READ_CONTACT", "SAVE_CASE",
                              "EDIT_QUOTE", "APPROVE_QUOTE", "APPROVE_PO", "TRANSITION",
                              "READ_PROFILE", "EDIT_PROFILE", "APPROVE_PROFILE",
                              "READ_MARKET", "RECORD_MARKET", "READ_CANDIDATE",
                              "RECORD_CANDIDATE", "QUALIFY_CANDIDATE",
                              "READ_DISCOVERED_CONTACT", "RECORD_DISCOVERED_CONTACT",
                              "SUPPRESS_CONTACT", "CORRECT_DISCOVERED_CONTACT"}),
    Role.ADMIN: frozenset({"READ_SUMMARY", "READ_CONTACT", "SAVE_CASE",
                           "EDIT_QUOTE", "APPROVE_QUOTE", "APPROVE_PO", "TRANSITION", "BACKUP",
                           "READ_PROFILE", "EDIT_PROFILE", "APPROVE_PROFILE",
                           "READ_MARKET", "RECORD_MARKET", "READ_CANDIDATE",
                           "RECORD_CANDIDATE", "QUALIFY_CANDIDATE",
                           "READ_DISCOVERED_CONTACT", "RECORD_DISCOVERED_CONTACT",
                           "SUPPRESS_CONTACT", "CORRECT_DISCOVERED_CONTACT",
                           "APPROVE_CONTACT_SOURCE"}),
    Role.SERVICE: frozenset({"READ_SUMMARY"}),
}


def current_uid() -> int:
    """Read the effective Unix UID; fail closed on unsupported platforms."""
    if not hasattr(os, "geteuid"):
        raise RuntimeError("Local pilot authentication requires a POSIX host")
    return os.geteuid()


@dataclass(frozen=True)
class LocalAccess:
    roles_by_uid: dict[int, Role]

    @classmethod
    def single_operator(cls) -> LocalAccess:
        """Development default: the current OS account is the sole administrator."""
        return cls({current_uid(): Role.ADMIN})

    def decision(self, permission: str) -> tuple[str, bool]:
        uid = current_uid()
        role = self.roles_by_uid.get(uid)
        return f"uid:{uid}", role is not None and permission in PERMISSIONS[role]
