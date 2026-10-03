"""Owner-only local secret files for the single-operator POSIX pilot."""

from __future__ import annotations

import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SECRET_NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z", re.ASCII)
MAX_SECRET_BYTES = 65536


class SecretValue:
    """Keep accidental formatting and logging from exposing the value."""

    def __init__(self, value: str):
        self._value = value

    def __repr__(self) -> str:
        return "<SecretValue redacted>"

    __str__ = __repr__

    def reveal(self) -> str:
        """Call only at an authenticated connector's request boundary."""
        return self._value


class SecretStore:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory).expanduser()
        if not hasattr(os, "geteuid") or not hasattr(os, "O_NOFOLLOW"):
            raise RuntimeError("Local secret files require a POSIX host")
        if self.directory.is_symlink():
            raise PermissionError("Secret directory must not be a symlink")
        # Never put credential files under the source tree, even if Git ignores them.
        source_root = Path(__file__).resolve().parents[1]
        if self.directory.resolve().is_relative_to(source_root):
            raise PermissionError("Secret directory must be outside the source tree")
        with self._open_directory():
            pass

    @contextmanager
    def _open_directory(self) -> Iterator[int]:
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(fd)
            if (metadata.st_uid != os.geteuid()
                    or metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
                raise PermissionError("Secret directory must be owned by this OS user and mode 0700")
            yield fd
        finally:
            os.close(fd)

    def get(self, name: str) -> SecretValue:
        if not isinstance(name, str) or not SECRET_NAME.fullmatch(name):
            raise ValueError("Invalid secret name")
        with self._open_directory() as directory_fd:
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory_fd)
            except FileNotFoundError as exc:
                raise KeyError(f"Secret {name} is missing") from exc
            try:
                metadata = os.fstat(fd)
                if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                        or metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
                    raise PermissionError(f"Secret {name} must be an owner-only regular file")
                with os.fdopen(fd, "rb", closefd=False) as source:
                    content = source.read(MAX_SECRET_BYTES + 1)
                if len(content) > MAX_SECRET_BYTES:
                    raise ValueError(f"Secret {name} exceeds the size limit")
            finally:
                os.close(fd)
        try:
            value = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Secret {name} must be UTF-8") from exc
        value = value.removesuffix("\r\n").removesuffix("\n")
        if not value or "\n" in value or "\r" in value or "\x00" in value:
            raise ValueError(f"Secret {name} must contain one non-empty line")
        return SecretValue(value)
