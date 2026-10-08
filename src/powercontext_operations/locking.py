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

"""Serialize native registration changes across Runtime and maintenance entry points."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import cast

from powercontext_operations.model import ServiceError


@contextmanager
def service_lock(path: Path, *, timeout: float = 5.0) -> Generator[None, None, None]:
    if os.name == "nt":
        with _windows_service_lock(path, timeout=timeout):
            yield
        return

    import fcntl

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ServiceError(  # noqa: TRY003
                        "another PowerContext service operation is still running"
                    ) from None
                time.sleep(0.05)
        yield
    finally:
        with suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def _windows_service_lock(path: Path, *, timeout: float) -> Generator[None, None, None]:
    import msvcrt

    # These Windows-only members are missing from the stdlib type stubs.
    msvcrt_members = vars(msvcrt)
    locking = cast(Callable[[int, int, int], None], msvcrt_members["locking"])
    lock_nonblocking = cast(int, msvcrt_members["LK_NBLCK"])
    lock_unlock = cast(int, msvcrt_members["LK_UNLCK"])
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        deadline = time.monotonic() + timeout
        while True:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                locking(descriptor, lock_nonblocking, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ServiceError(  # noqa: TRY003
                        "another PowerContext service operation is still running"
                    ) from None
                time.sleep(0.05)
        yield
    finally:
        with suppress(OSError):
            os.lseek(descriptor, 0, os.SEEK_SET)
            locking(descriptor, lock_unlock, 1)
        os.close(descriptor)
