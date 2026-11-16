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


class ScopeEvidenceHandler(ScopeResourceHandler):
    """Remove the retained evidence rows this build stores beside Artifacts.

    `pc_source_evidence_bindings` is the authoritative binding record for a
    captured Source, and the Atomic Memory evidence history holds the exact
    sources an approved Memory adopted. Both are this Scope's own content, both
    live in schema modules outside `BUILTIN_TABLES`, and both must go for a
    `complete` operation to mean what it says. Retention of a body-free operation
    receipt is separate: deletion operation and progress rows are deliberately
    kept (see ``SCOPE_DELETIONS``/``SCOPE_DELETION_PROGRESS`` and the note in
    ``persistence.scope_lifecycle``).
    """

    name = "evidence"

    @override
    async def purge(self, connection: AsyncConnection, scope_id: str, /) -> None:
        from powercontext.builtin.persistence.atomic_memory_evidence_schema import (
            ATOMIC_MEMORY_EVIDENCE_HISTORY_TABLE,
            SOURCE_EVIDENCE_BINDINGS_TABLE,
        )

        await connection.execute(
            delete(ATOMIC_MEMORY_EVIDENCE_HISTORY_TABLE).where(
                ATOMIC_MEMORY_EVIDENCE_HISTORY_TABLE.c.scope_id == scope_id
            )
        )
        await connection.execute(
            delete(SOURCE_EVIDENCE_BINDINGS_TABLE).where(SOURCE_EVIDENCE_BINDINGS_TABLE.c.scope_id == scope_id)
        )


class ScopeProjectionHandler(ScopeResourceHandler):
    """Ask each projection owner to remove this Scope's rows from its own tables.

    The Atomic Memory index and the approved-head lexical index keep projection
    tables in their own schemas, outside `BUILTIN_TABLES`. This handler delegates
    to those owners instead of deleting by column match, so each owner decides
    what it stores, and a store that cannot answer raises a normal failure that
    keeps the operation visible and retryable.
    """

    name = "projections"

    def __init__(self, atomic_memory_index, experience_index) -> None:
        self._atomic_memory_index = atomic_memory_index
        self._experience_index = experience_index

    @override
    async def purge(self, connection: AsyncConnection, scope_id: str, /) -> None:
        await self._atomic_memory_index.purge_scope(connection, scope_id)
        await self._experience_index.purge_scope(connection, scope_id)


def default_handlers(*, atomic_memory_index=None, experience_index=None) -> tuple[ScopeResourceHandler, ...]:
    """Every handler this build ships for the resources it actually owns.

    The plain relational handler always applies. The evidence handler covers the
    retained evidence modules. The projection handler is included only when the
    composition supplies the owners, so a Runtime without them never claims to
    have cleaned up tables it does not know about.
    """

    handlers: tuple[ScopeResourceHandler, ...] = (ScopeResourceHandler(), ScopeEvidenceHandler())
    if atomic_memory_index is not None and experience_index is not None:
        handlers += (ScopeProjectionHandler(atomic_memory_index, experience_index),)
    return handlers


def require_supported_handlers(handlers: tuple[ScopeResourceHandler, ...]) -> None:
    """Refuse unsupported cleanup before acceptance rather than after a partial delete."""

    from powercontext.builtin.scope.errors import ScopeResourceHandlerUnsupportedError

    for handler in handlers:
        if isinstance(handler, UnsupportedScopeResourceHandler):
            raise ScopeResourceHandlerUnsupportedError(handler.name, handler.reason)


__all__ = [
    "ScopeEvidenceHandler",
    "ScopeProjectionHandler",
    "ScopeResourceHandler",
    "UnsupportedScopeResourceHandler",
    "default_handlers",
    "require_supported_handlers",
]
