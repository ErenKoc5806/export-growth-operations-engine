"""Handle-checked Windows private files for bounded secrets and staged evidence.

Not a SQLite VFS, database-backup implementation, or live-mode activation gate.
The owner, SYSTEM and local administrators remain trusted.
"""

from __future__ import annotations

import ctypes
import re
from contextlib import ExitStack, contextmanager
from ctypes import wintypes as w
from pathlib import Path

from pilot_engine.windows_security import _WinAPI, _account_sid, check_descriptor


FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z", re.ASCII)
DEVICE_NAME = re.compile(r"(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)", re.I)


def _file_name(name: str) -> str:
    if (not isinstance(name, str) or not FILE_NAME.fullmatch(name)
            or name.endswith(".") or DEVICE_NAME.match(name)):
        raise ValueError("A plain non-device private file name is required")
    return name


def _directory_path(path: str | Path) -> Path:
    path = Path(path).expanduser()
    if (not path.is_absolute() or str(path).startswith("\\\\") or ".." in path.parts
            or any(":" in part or part.endswith((".", " ")) for part in path.parts[1:])):
        raise ValueError("An absolute local directory without streams or aliases is required")
    return path


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("length", w.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", w.BOOL)]


class _FileAPI(_WinAPI):
    def __init__(self) -> None:
        super().__init__()
        ptr = ctypes.c_void_p
        self._bind(self.kernel, "GetFileType", [w.HANDLE], w.DWORD)
        self._bind(self.kernel, "ReadFile", [w.HANDLE, ptr, w.DWORD, ptr, ptr], w.BOOL)
        self._bind(self.kernel, "WriteFile", [w.HANDLE, ptr, w.DWORD, ptr, ptr], w.BOOL)
        self._bind(self.kernel, "FlushFileBuffers", [w.HANDLE], w.BOOL)
        self._bind(self.advapi, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
                   [w.LPCWSTR, w.DWORD, ptr, ptr], w.BOOL)

    @contextmanager
    def open(self, path: Path, *, directory: bool = False, attributes=None, create=False):
        # Include FILE_LIST_DIRECTORY, not metadata access alone: Windows sharing
        # restrictions do not pin a directory opened only for attributes/control.
        access = 0x00020081 if directory else 0x80020000
        if create:
            access |= 0x40000000  # GENERIC_WRITE; never truncate an existing file
        # Directory ancestors allow other reads/writes but never deletion/rename.
        # The leaf disallows all other data readers/writers/deletion while in use.
        handle = self.kernel.CreateFileW(str(path), access, 3 if directory else 0,
                                         attributes, 1 if create else 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            error = ctypes.get_last_error()
            if error in (2, 3):
                raise FileNotFoundError("Private resource is missing")
            if error in (80, 183):
                raise FileExistsError("Private resource already exists")
            raise PermissionError("Cannot lock private Windows resource", error)
        try:
            if self.kernel.GetFileType(handle) != 1:  # FILE_TYPE_DISK
                raise PermissionError("A disk file/directory is required")
            info = self.handle_info(handle)
            if bool(info.attributes & 0x10) != directory:
                raise PermissionError("Unexpected private resource type")
            yield handle
        finally:
            self.kernel.CloseHandle(handle)

    @contextmanager
    def creation_attributes(self, owner_sid: str):
        descriptor = ctypes.c_void_p()
        # Explicit owner avoids elevated Windows' default Administrators owner.
        sddl = f"O:{owner_sid}D:P(A;;FA;;;{owner_sid})(A;;FA;;;SY)(A;;FA;;;BA)"
        self.require(self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None))
        try:
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            yield ctypes.byref(attributes)
        finally:
            self.kernel.LocalFree(descriptor)


class WindowsPrivateFiles:
    """Existing private directory; each operation pins ancestors and the exact leaf.

    Directories must be provisioned by IT. No permissions are repaired here.
    The object caches neither a security decision nor a secret value.
    """

    def __init__(self, directory: str | Path):
        self.directory = _directory_path(directory)
        # Validate at construction AND on every operation. Resolve identity from
        # the token each time; no policy or owner SID is accepted from a request.
        with self._pinned_directory():
            pass

    @contextmanager
    def _pinned_directory(self):
        api = _FileAPI()
        sid = _account_sid(api.process_sid())
        with ExitStack() as stack:
            for path in (*reversed(self.directory.parents), self.directory):
                handle = stack.enter_context(api.open(path, directory=True))
            self._check(api, handle, sid, directory=True)
            yield api, sid

    @staticmethod
    def _check(api, handle, sid, *, directory=False):
        check_descriptor(api.handle_security(handle), owner_sid=sid,
                         allowed_sids=frozenset({sid}), directory=directory)

    def read_bytes(self, name: str, *, max_bytes: int) -> bytes:
        name = _file_name(name)
        if type(max_bytes) is not int or not 0 < max_bytes <= 16 * 1024 * 1024:
            raise ValueError("A bounded file size from 1 byte to 16 MiB is required")
        with self._pinned_directory() as (api, sid):
            with api.open(self.directory / name) as handle:
                self._check(api, handle, sid)
                result = bytearray()
                while len(result) <= max_bytes:
                    size = min(65536, max_bytes + 1 - len(result))
                    buffer, count = ctypes.create_string_buffer(size), w.DWORD()
                    api.require(api.kernel.ReadFile(handle, buffer, size, ctypes.byref(count), None))
                    if not count.value:
                        return bytes(result)
                    result.extend(buffer.raw[:count.value])
                raise ValueError("Private file exceeds the size limit")

    def write_new(self, name: str, content: bytes) -> None:
        """Write a new immutable-name artifact; never replace an existing file.

        Failure can leave an incomplete private file; callers must treat only a
        successful return as persisted evidence and reconcile failures separately.
        """
        name = _file_name(name)
        if not isinstance(content, bytes) or len(content) > 16 * 1024 * 1024:
            raise ValueError("Private file content must be bytes, at most 16 MiB")
        with self._pinned_directory() as (api, sid):
            with api.creation_attributes(sid) as attributes:
                with api.open(self.directory / name, attributes=attributes, create=True) as handle:
                    # Check before writing any content, using the very same handle.
                    self._check(api, handle, sid)
                    offset = 0
                    while offset < len(content):
                        block = content[offset:offset + 65536]
                        buffer, count = ctypes.create_string_buffer(block), w.DWORD()
                        api.require(api.kernel.WriteFile(handle, buffer, len(block),
                                                         ctypes.byref(count), None))
                        if not count.value:
                            raise OSError("Private Windows write made no progress")
                        offset += count.value
                    api.require(api.kernel.FlushFileBuffers(handle))
