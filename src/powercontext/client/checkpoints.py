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

"""Prevent automatic replay of an unacknowledged checkpoint."""

from __future__ import annotations

import asyncio


class Checkpoints:
    """A new confirmed Source position permits a new checkpoint attempt."""

    def __init__(self) -> None:
        self._unknown: dict[str, int] = {}

    def allows(self, scope_id: str, position: int) -> bool:
        return position > self._unknown.get(scope_id, -1)

    def failed(self, scope_id: str, position: int, error: BaseException) -> None:
        if isinstance(error, TimeoutError | asyncio.CancelledError) or getattr(error, "outcome", None) == "unknown":
            self._unknown[scope_id] = max(position, self._unknown.get(scope_id, -1))
