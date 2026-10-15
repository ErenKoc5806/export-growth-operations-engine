"""Windows security preflight. This module does not activate live data or I/O.

Only standard allow/deny ACEs are understood. Unsupported descriptors fail
closed rather than attempting to approximate Windows effective-access rules.
"""

from __future__ import annotations

import ctypes
import os
import re
from ctypes import wintypes as w
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from pilot_engine.access import PERMISSIONS, Role


SID_PATTERN = re.compile(r"S-1-(?:\d+-)*\d+\Z", re.ASCII)
HOST_TRUSTEES = frozenset({"S-1-5-18", "S-1-5-32-544"})  # SYSTEM, local admins
BROAD_TRUSTEES = frozenset({"S-1-1-0", "S-1-5-11", "S-1-5-32-545"})


def _account_sid(sid: str) -> str:
    # Account SIDs only: no Everyone/Users/Administrators or other group aliases.
    # The trusted launcher must additionally confirm these identify named users.
    if (not isinstance(sid, str) or not SID_PATTERN.fullmatch(sid)
            or not (sid.startswith("S-1-5-21-") or sid.startswith("S-1-12-1-"))):
        raise ValueError("A named Windows account SID is required")
    return sid


class WindowsRole(StrEnum):
    OPERATOR = "OPERATOR"
    APPROVER = "APPROVER"
    VIEWER = "VIEWER"


READ_PERMISSIONS = frozenset(p for p in PERMISSIONS[Role.ADMIN] if p.startswith("READ_"))
APPROVAL_PERMISSIONS = frozenset(p for p in PERMISSIONS[Role.ADMIN] if p.startswith("APPROVE_"))
WINDOWS_PERMISSIONS = {
    WindowsRole.OPERATOR: PERMISSIONS[Role.OPERATOR] - APPROVAL_PERMISSIONS,
    WindowsRole.APPROVER: READ_PERMISSIONS | APPROVAL_PERMISSIONS,
    WindowsRole.VIEWER: PERMISSIONS[Role.VIEWER],
}


@dataclass(frozen=True)
class WindowsAccess:
    """Explicit trusted-launcher policy, never a request-supplied role mapping.

    This separates service permissions, not users who can edit the same SQLite
    file directly. A separately authenticated service is still needed for live
    cross-account approval; this policy alone does not provide that boundary.
    """

    roles_by_sid: Mapping[str, WindowsRole]

    def __post_init__(self) -> None:
        roles = {_account_sid(sid): WindowsRole(role) for sid, role in self.roles_by_sid.items()}
        if not all(role in roles.values() for role in (WindowsRole.OPERATOR, WindowsRole.APPROVER)):
            raise ValueError("Separate named operator and approver accounts are required")
        object.__setattr__(self, "roles_by_sid", MappingProxyType(roles))

    def decision(self, permission: str) -> tuple[str, bool]:
        sid = current_process_sid()
        role = self.roles_by_sid.get(sid)
        return "windows-sid:" + sid, role is not None and permission in WINDOWS_PERMISSIONS[role]


@dataclass(frozen=True)
class Ace:
    kind: int
    flags: int
    mask: int
    sid: str


@dataclass(frozen=True)
class FileSecurity:
    owner_sid: str
    aces: tuple[Ace, ...] | None
    is_directory: bool
    links: int


def check_descriptor(security: FileSecurity, *, owner_sid: str,
                     allowed_sids: frozenset[str], directory: bool) -> None:
    """Conservative ACL policy for an existing resource, including inherit-only ACEs."""
    _account_sid(owner_sid)
    if (owner_sid not in allowed_sids or allowed_sids & BROAD_TRUSTEES
            or any(not SID_PATTERN.fullmatch(sid) for sid in allowed_sids)):
        raise ValueError("Explicit resource trustees including the owner are required")
    if security.owner_sid != owner_sid or security.is_directory != directory:
        raise PermissionError("Resource owner or type does not match the pilot policy")
    if not directory and security.links != 1:
        raise PermissionError("Pilot files must not have additional hard links")
    if not security.aces:
        raise PermissionError("A non-empty DACL is required")
    owner_grant = False
    for ace in security.aces:
        if ace.kind not in (0, 1):
            raise PermissionError("Unsupported ACE type in pilot resource")
        if ace.kind == 0 and ace.mask:
            if ace.sid not in allowed_sids | HOST_TRUSTEES:
                raise PermissionError("Resource grants access outside the trusted SID set")
            if ace.sid == owner_sid and not ace.flags & 0x08:  # INHERIT_ONLY_ACE
                owner_grant = True
    if not owner_grant:
        raise PermissionError("Resource owner needs an explicit applicable grant")


class _AclHeader(ctypes.Structure):
    _fields_ = [("revision", w.BYTE), ("reserved", w.BYTE), ("size", w.WORD),
                ("count", w.WORD), ("reserved2", w.WORD)]


class _AceHeader(ctypes.Structure):
    _fields_ = [("kind", w.BYTE), ("flags", w.BYTE), ("size", w.WORD)]


class _FileInfo(ctypes.Structure):
    _fields_ = [("attributes", w.DWORD), ("created", w.FILETIME),
                ("accessed", w.FILETIME), ("written", w.FILETIME),
                ("volume", w.DWORD), ("size_high", w.DWORD), ("size_low", w.DWORD),
                ("links", w.DWORD), ("index_high", w.DWORD), ("index_low", w.DWORD)]


class _WinAPI:
    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows security inspection requires a native Windows host")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        ptr = ctypes.c_void_p
        self._bind(self.kernel, "GetCurrentProcess", [], w.HANDLE)
        self._bind(self.kernel, "GetCurrentThread", [], w.HANDLE)
        self._bind(self.kernel, "CloseHandle", [w.HANDLE], w.BOOL)
        self._bind(self.kernel, "LocalFree", [ptr], ptr)
        self._bind(self.kernel, "GetFileAttributesW", [w.LPCWSTR], w.DWORD)
        self._bind(self.kernel, "CreateFileW", [w.LPCWSTR, w.DWORD, w.DWORD, ptr,
                                              w.DWORD, w.DWORD, w.HANDLE], w.HANDLE)
        self._bind(self.kernel, "GetFileInformationByHandle", [w.HANDLE, ptr], w.BOOL)
        self._bind(self.advapi, "OpenProcessToken", [w.HANDLE, w.DWORD, ptr], w.BOOL)
        self._bind(self.advapi, "OpenThreadToken", [w.HANDLE, w.DWORD, w.BOOL, ptr], w.BOOL)
        self._bind(self.advapi, "GetTokenInformation", [w.HANDLE, w.DWORD, ptr,
                                                      w.DWORD, ptr], w.BOOL)
        self._bind(self.advapi, "IsValidSid", [ptr], w.BOOL)
        self._bind(self.advapi, "ConvertSidToStringSidW", [ptr, ptr], w.BOOL)
        self._bind(self.advapi, "IsValidAcl", [ptr], w.BOOL)
        self._bind(self.advapi, "GetAce", [ptr, w.DWORD, ptr], w.BOOL)
        self._bind(self.advapi, "GetSecurityInfo", [w.HANDLE, w.DWORD, w.DWORD,
                                                  ptr, ptr, ptr, ptr, ptr], w.DWORD)

    @staticmethod
    def _bind(library, name, args, result) -> None:
        function = getattr(library, name)
        function.argtypes, function.restype = args, result

    @staticmethod
    def require(ok) -> None:
        if not ok:
            raise PermissionError("Windows security inspection failed", ctypes.get_last_error())

    def sid_text(self, sid) -> str:
        self.require(self.advapi.IsValidSid(sid))
        output = w.LPWSTR()
        self.require(self.advapi.ConvertSidToStringSidW(sid, ctypes.byref(output)))
        try:
            return output.value
        finally:
            self.kernel.LocalFree(ctypes.cast(output, ctypes.c_void_p))

    def process_sid(self) -> str:
        token = w.HANDLE()
        # Never silently authorize a thread impersonating a different account.
        if self.advapi.OpenThreadToken(self.kernel.GetCurrentThread(), 0x0008, True,
                                      ctypes.byref(token)):
            self.kernel.CloseHandle(token)
            raise PermissionError("Impersonating threads are not supported")
        if ctypes.get_last_error() != 1008:  # ERROR_NO_TOKEN
            self.require(False)
        self.require(self.advapi.OpenProcessToken(self.kernel.GetCurrentProcess(), 0x0008,
                                                 ctypes.byref(token)))
        try:
            size = w.DWORD()
            ok = self.advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            if ok or ctypes.get_last_error() != 122 or not size.value:
                raise PermissionError("Cannot size Windows TokenUser")
            buffer = ctypes.create_string_buffer(size.value)
            self.require(self.advapi.GetTokenInformation(token, 1, buffer, size,
                                                        ctypes.byref(size)))
            # TOKEN_USER starts with SID_AND_ATTRIBUTES, whose first field is PSID.
            return self.sid_text(ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0])
        finally:
            self.kernel.CloseHandle(token)

    def file_security(self, path: Path) -> FileSecurity:
        # OPEN_REPARSE_POINT + BACKUP_SEMANTICS open the object itself, including
        # directories. This is a snapshot, not an authorization for later I/O.
        handle = self.kernel.CreateFileW(str(path), 0x00020080, 7, None, 3,
                                         0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            self.require(False)
        try:
            return self.handle_security(handle)
        finally:
            self.kernel.CloseHandle(handle)

    def handle_security(self, handle) -> FileSecurity:
        """Inspect the exact caller-held file/directory handle, without reopening."""
        descriptor, owner, dacl = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        try:
            info = self.handle_info(handle)
            result = self.advapi.GetSecurityInfo(handle, 1, 5, ctypes.byref(owner), None,
                                                ctypes.byref(dacl), None,
                                                ctypes.byref(descriptor))
            if result:
                raise PermissionError("Cannot read Windows security descriptor", result)
            aces = None
            if dacl.value:
                self.require(self.advapi.IsValidAcl(dacl))
                header = ctypes.cast(dacl, ctypes.POINTER(_AclHeader)).contents
                aces = []
                for index in range(header.count):
                    pointer = ctypes.c_void_p()
                    self.require(self.advapi.GetAce(dacl, index, ctypes.byref(pointer)))
                    ace = ctypes.cast(pointer, ctypes.POINTER(_AceHeader)).contents
                    if ace.kind not in (0, 1) or ace.size < 16:
                        raise PermissionError("Unsupported ACE layout in pilot resource")
                    mask = ctypes.cast(pointer.value + 4, ctypes.POINTER(w.DWORD))[0]
                    aces.append(Ace(ace.kind, ace.flags, mask, self.sid_text(pointer.value + 8)))
                aces = tuple(aces)
            return FileSecurity(self.sid_text(owner), aces, bool(info.attributes & 0x10), info.links)
        finally:
            if descriptor.value:
                self.kernel.LocalFree(descriptor)

    def handle_info(self, handle) -> _FileInfo:
        info = _FileInfo()
        self.require(self.kernel.GetFileInformationByHandle(handle, ctypes.byref(info)))
        if info.attributes & 0x400:
            raise PermissionError("Reparse points are not permitted in pilot paths")
        return info


def current_process_sid() -> str:
    """Native process-token identity; no username/environment fallback."""
    return _WinAPI().process_sid()


def inspect_resource(path: str | Path, *, owner_sid: str,
                     allowed_sids: frozenset[str], directory: bool = False) -> FileSecurity:
    """Read-only preflight for existing DB/backup/upload/secret resources.

    Call separately for each directory and every file (including SQLite sidecars).
    This snapshot must not be used as a race-free runtime file-opening primitive.
    """
    api = _WinAPI()
    path = Path(path)
    if (not path.is_absolute() or str(path).startswith("\\\\")
            or ".." in path.parts or any(":" in part for part in path.parts[1:])):
        raise ValueError("An absolute local Windows path without streams is required")
    for component in (*reversed(path.parents), path):
        attributes = api.kernel.GetFileAttributesW(str(component))
        if attributes == 0xFFFFFFFF:
            api.require(False)
        if attributes & 0x400:
            raise PermissionError("Pilot paths must not traverse reparse points")
    security = api.file_security(path)
    check_descriptor(security, owner_sid=owner_sid, allowed_sids=allowed_sids, directory=directory)
    return security
