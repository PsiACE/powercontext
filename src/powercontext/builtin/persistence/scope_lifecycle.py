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

"""Scope lifecycle state read at the write boundary it actually guards.

Lifecycle state is authoritative in ``pc_scopes`` next to the Scope identity it
describes. Every writer that stores ordinary Scope content reads that row inside
its own transaction and locks it, so a state change and an in-flight write cannot
interleave: whichever transaction takes the row first commits first. A queued
task that started before the cutoff therefore fails at its final write instead of
publishing afterwards.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.tables import SCOPES_TABLE
from powercontext.builtin.scope.errors import ScopeNotWritableError

ScopeLifecycleState = Literal["writable", "archived", "deleting", "deleted"]

LIFECYCLE_STATES: tuple[ScopeLifecycleState, ...] = ("writable", "archived", "deleting", "deleted")
WRITABLE: ScopeLifecycleState = "writable"


def reject_unsupported_state(state: ScopeLifecycleState) -> None:
    """Refuse a state this build cannot enforce instead of widening it."""

    if state not in LIFECYCLE_STATES:
        raise ScopeNotWritableError("<unknown>", str(state))


async def lock_lifecycle_state(
    connection: AsyncConnection,
    scope_id: str,
    /,
) -> ScopeLifecycleState | None:
    """Read the owning Scope's state under a row lock; ``None`` if it is unregistered.

    The lock is what makes the gate a cutoff rather than a hint. SQLite reserves
    its writer boundary per transaction, so the plain read is already ordered.
    """

    statement = select(SCOPES_TABLE.c.lifecycle_state).where(SCOPES_TABLE.c.scope_id == scope_id)
    if connection.dialect.name != "sqlite":
        statement = statement.with_for_update()
    try:
        state = await connection.scalar(statement)
    except OperationalError as error:
        # A composition that installs only the shared content tables has no Scope
        # storage, so it has no lifecycle authority to consult. The serving
        # runtime always composes Scope storage (``BUILTIN_TABLES`` includes
        # ``SCOPE_TABLES``), and an incomplete serving schema fails long before a
        # write. Never treat any other database failure as "no state".
        if not _scope_storage_missing(error):
            raise
        return None
    if state is None:
        return None
    return state


def _scope_storage_missing(error: OperationalError) -> bool:
    message = str(getattr(error, "orig", error)).lower()
    return SCOPES_TABLE.name in message and ("no such table" in message or "doesn't exist" in message)


async def require_writable(connection: AsyncConnection, scope_id: str, /) -> None:
    """Refuse ordinary content writes in a Scope that is no longer writable."""

    if getattr(connection, "is_frozen_maintenance", False):
        # A migration domain or historical fixture records rows into a schema
        # frozen at an earlier revision, which may predate lifecycle state
        # entirely. Nothing is being served from that shape, and no Scope there
        # can be archived, so there is no cutoff to enforce.
        return
    state = await lock_lifecycle_state(connection, scope_id)
    if state is not None and state != WRITABLE:
        raise ScopeNotWritableError(scope_id, state)
