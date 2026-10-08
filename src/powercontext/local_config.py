# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""Revision-checked publication of one local configuration resource."""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from hashlib import sha256
from pathlib import Path
from typing import cast


class ConfigurationConflictError(ValueError):
    """A caller's snapshot no longer identifies the current file."""


def read_configuration(path: Path) -> tuple[bytes | None, str]:
    """Read exact bytes and an opaque revision; absent files have a distinct revision."""
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return None, "missing"
    return content, "sha256:" + sha256(content).hexdigest()


@contextmanager
def configuration_lock(path: Path, *, timeout: float = 5.0) -> Generator[None, None, None]:
    """Serialize cooperating writers using an OS lock released on process termination."""
    lock = path.with_name("." + path.name + ".lock")
    lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    if os.name == "nt":
        import msvcrt

        members = vars(msvcrt)
        locking = cast(Callable[[int, int, int], None], members["locking"])
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")

        def acquire() -> None:
            os.lseek(descriptor, 0, os.SEEK_SET)
            locking(descriptor, cast(int, members["LK_NBLCK"]), 1)

        def release() -> None:
            os.lseek(descriptor, 0, os.SEEK_SET)
            locking(descriptor, cast(int, members["LK_UNLCK"]), 1)

    else:
        import fcntl

        def acquire() -> None:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def release() -> None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)

    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ConfigurationConflictError("Another configuration writer is still running") from None  # noqa: TRY003
                time.sleep(0.05)
        yield
    finally:
        with suppress(OSError):
            release()
        os.close(descriptor)


def publish_configuration(path: Path, content: bytes, *, expected_revision: str) -> str:
    """Replace one private file after checking its revision; caller holds its lock."""
    if read_configuration(path)[1] != expected_revision:
        raise ConfigurationConflictError("Configuration changed; inspect it again before applying")  # noqa: TRY003
    descriptor, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return "sha256:" + sha256(content).hexdigest()
