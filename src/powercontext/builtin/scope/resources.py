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

"""Cleanup of the owned resources a Scope deletion must remove.

This is a boundary, not a workflow system. A handler answers one question for one
Scope: remove the rows that Scope owns, idempotently. The deleting Scope service
owns the operation record, the confirmed set, the access cutoff and the
per-resource progress; a handler owns only the removal itself.

Handlers never follow Context References or Artifact lineage into another Scope.
A resource that this Scope merely references, or an Artifact in another Scope
that cites a Source here, belongs to that other Scope and is not removed.
"""

from __future__ import annotations

from sqlalchemy import Table, delete
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import sort_tables
from typing_extensions import override

from powercontext.builtin.persistence.tables import (
    ARTIFACT_PUBLICATIONS_TABLE,
    BUILTIN_TABLES,
    SCOPE_BINDINGS_TABLE,
    SCOPE_CONTEXT_REFERENCES_TABLE,
    SCOPE_CREATION_REQUESTS_TABLE,
    SCOPE_EXTERNAL_REFERENCES_TABLE,
    SCOPE_SETTINGS_TABLE,
)

# Scope bookkeeping is this service's own record, not a Scope's owned content. It
# is removed by the deletion service in its own order and never by a handler.
_BOOKKEEPING = frozenset({
    "pc_scopes",
    "pc_scope_context_references",
    "pc_scope_external_references",
    "pc_scope_creation_requests",
    "pc_scope_settings",
    "pc_scope_bindings",
    "pc_scope_deletions",
    "pc_scope_deletion_progress",
})

# Every table a Scope owns is one that carries `scope_id`, so the set is derived
# from the schema instead of being restated here and drifting from it. Removal
# order is the reverse of foreign-key dependency order, so no child is left
# pointing at a removed parent within this Scope.
_OWNED_SCOPE_TABLES: tuple[Table, ...] = tuple(
    reversed(
        sort_tables([table for table in BUILTIN_TABLES if "scope_id" in table.c and table.name not in _BOOKKEEPING])
    )
)


class ScopeResourceHandler:
    """Remove everything one Scope owns. Callers persist progress around this call."""

    name = "owned-resources"

    async def purge(self, connection: AsyncConnection, scope_id: str, /) -> None:
        """Delete this Scope's owned rows; repeating the call is a no-op.

        Only rows keyed to ``scope_id`` are removed. Another Scope's rows are
        untouched even when they cite, reference or publish this Scope's
        material, which is what keeps deletion structural rather than transitive.
        """

        # This Scope's own record of what it published goes first: its target-side
        # foreign key points at an Artifact this Scope is about to remove. The
        # source Artifact may live in any Scope and stays with its owner.
        await connection.execute(
            delete(ARTIFACT_PUBLICATIONS_TABLE).where(ARTIFACT_PUBLICATIONS_TABLE.c.target_scope_id == scope_id)
        )
        for table in _OWNED_SCOPE_TABLES:
            await connection.execute(delete(table).where(table.c.scope_id == scope_id))
        # This Scope's outgoing references and its own binding/default records.
        await connection.execute(
            delete(SCOPE_CONTEXT_REFERENCES_TABLE).where(SCOPE_CONTEXT_REFERENCES_TABLE.c.scope_id == scope_id)
        )
        await connection.execute(
            delete(SCOPE_EXTERNAL_REFERENCES_TABLE).where(SCOPE_EXTERNAL_REFERENCES_TABLE.c.scope_id == scope_id)
        )
        await connection.execute(
            delete(SCOPE_CREATION_REQUESTS_TABLE).where(SCOPE_CREATION_REQUESTS_TABLE.c.scope_id == scope_id)
        )
        await connection.execute(delete(SCOPE_SETTINGS_TABLE).where(SCOPE_SETTINGS_TABLE.c.scope_id == scope_id))
        await connection.execute(delete(SCOPE_BINDINGS_TABLE).where(SCOPE_BINDINGS_TABLE.c.scope_id == scope_id))


class UnsupportedScopeResourceHandler(ScopeResourceHandler):
    """Refuse a cleanup mode instead of performing a partial removal and claiming success."""

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason

    @override
    async def purge(self, connection: AsyncConnection, scope_id: str, /) -> None:
        raise NotImplementedError(self.reason)


def default_handlers() -> tuple[ScopeResourceHandler, ...]:
    """The only handler this build ships: the owned relational resources."""

    return (ScopeResourceHandler(),)


def require_supported_handlers(handlers: tuple[ScopeResourceHandler, ...]) -> None:
    """Refuse unsupported cleanup before acceptance rather than after a partial delete."""

    from powercontext.builtin.scope.errors import ScopeResourceHandlerUnsupportedError

    for handler in handlers:
        if isinstance(handler, UnsupportedScopeResourceHandler):
            raise ScopeResourceHandlerUnsupportedError(handler.name, handler.reason)


__all__ = [
    "ScopeResourceHandler",
    "UnsupportedScopeResourceHandler",
    "default_handlers",
    "require_supported_handlers",
]
