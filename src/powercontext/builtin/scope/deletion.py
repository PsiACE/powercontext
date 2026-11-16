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

"""Explicit, confirmed Scope deletion with an immediate access cutoff.

The operation is deliberately three steps, because a deletion that acts on a set
the requester never saw is not explicit:

1. ``preview`` resolves the exact structural set and returns it with a digest.
2. ``confirm`` re-resolves that set inside the hierarchy boundary and refuses if
   it changed, then cuts access and records the operation.
3. ``cleanup`` removes the owned resources and records per-resource progress.
   A failed or interrupted cleanup stays unavailable to readers and retryable by
   a manager; it never becomes readable again.

Nothing here follows Context References or Artifact lineage. A Scope that merely
references a deleted Scope keeps existing with its outgoing reference removed; it
does not lose its own resources, and it does not gain authority.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Sequence

import rfc8785
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.scope.errors import (
    ScopeDeletionBindingRepairError,
    ScopeDeletionConflictError,
    ScopeLifecycleConflictError,
    ScopeNotFoundError,
    ScopeResourceHandlerUnsupportedError,
)
from powercontext.builtin.scope.models import (
    ScopeDeletionMember,
    ScopeDeletionPreview,
    ScopeDeletionReceipt,
    ScopeDescriptor,
    ScopeResourceProgress,
    ScopeSelection,
)
from powercontext.builtin.scope.repository import ScopeRepository
from powercontext.builtin.scope.resources import (
    ScopeResourceHandler,
    default_handlers,
    require_supported_handlers,
)

_CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"


class ScopeDeletionApplication:
    """Preview, confirm and clean up one explicit structural Scope deletion."""

    def __init__(
        self,
        database: AsyncDatabase,
        scopes,
        /,
        *,
        repository: ScopeRepository | None = None,
        handlers: tuple[ScopeResourceHandler, ...] | None = None,
    ) -> None:
        self._database = database
        self._scopes = scopes
        self._repository = ScopeRepository() if repository is None else repository
        self._handlers = default_handlers() if handlers is None else handlers

    async def preview(self, selection: ScopeSelection, /) -> ScopeDeletionPreview:
        """Return the exact structural set and its digest. This grants no authority."""

        if selection.mode == "all":
            raise ScopeDeletionConflictError("<all>", "deletion requires an explicit Scope selection")
        async with self._database.transaction() as connection:
            return await self._preview(connection, selection)

    async def _preview(self, connection: AsyncConnection, selection: ScopeSelection) -> ScopeDeletionPreview:
        scopes = await self._scopes.resolve_selection(selection)
        members = tuple(ScopeDeletionMember.of(scope) for scope in scopes)
        root_scope_id = selection.root_scope_id or selection.scope_ids[0]
        outside = await self._outside_children(connection, members)
        return ScopeDeletionPreview(
            root_scope_id=root_scope_id,
            members=members,
            membership_digest=membership_digest(members),
            outside_children=outside,
        )

    async def confirm(
        self,
        selection: ScopeSelection,
        confirmed_digest: str,
        /,
        *,
        single_scope: bool = False,
    ) -> ScopeDeletionReceipt:
        """Cut access and record the operation, refusing a changed set or unsafe prerequisites.

        ``single_scope`` asks for the single-Scope delete contract: a Scope that
        still has structural children conflicts instead of being removed with
        them, so a caller cannot widen an operation by accident.
        """

        require_supported_handlers(self._handlers)
        async with self._scopes._write_lock, self._database.transaction() as connection:
            # Structural attach/move and publication take this same boundary, so a
            # child cannot be attached or moved after the set is read and before it
            # is cut off. Any move that happened before this transaction changes
            # the digest and is refused below.
            await self._repository.lock_hierarchy(connection)
            current = await self._preview(connection, selection)
            if current.membership_digest != confirmed_digest:
                raise ScopeDeletionConflictError(
                    current.root_scope_id, "the structural set changed since it was previewed"
                )
            if single_scope:
                await self._require_childless(connection, current)
            if current.outside_children:
                raise ScopeDeletionConflictError(
                    current.root_scope_id,
                    "the confirmed set is not complete: some members still have children outside it",
                )
            await self._require_unbound(connection, current)
            operation_id = _operation_id()
            await self._cut_off(connection, current)
            await self._repository.record_deletion(
                connection,
                operation_id,
                current.root_scope_id,
                current.membership_digest,
                _encode_members(current.members),
            )
            await self._repository.record_progress(
                connection,
                operation_id,
                tuple((member.scope_id, handler.name) for member in current.members for handler in self._handlers),
            )
            # Inbound references are removed with the cutoff, not with cleanup, so
            # no consumer is left pointing at unavailable material.
            await self._repository.clear_references_to(connection, current.scope_ids)
        return await self.cleanup(operation_id)

    async def cleanup(self, operation_id: str, /) -> ScopeDeletionReceipt:
        """Run every unfinished handler; repeat calls resume and complete the operation."""

        record = await self._receipt(operation_id)
        if record.state == "complete":
            return record
        for progress in record.unfinished:
            try:
                async with self._database.transaction() as connection:
                    handler = self._handler(progress.handler)
                    await handler.purge(connection, progress.scope_id)
                    await self._repository.finish_progress(
                        connection, operation_id, progress.scope_id, progress.handler, state="done", error_code=None
                    )
            except Exception as error:
                # Keep the failure visible and retryable. The members stay in a
                # non-writable state, so an incomplete cleanup never restores reads.
                async with self._database.transaction() as connection:
                    await self._repository.finish_progress(
                        connection,
                        operation_id,
                        progress.scope_id,
                        progress.handler,
                        state="failed",
                        error_code=type(error).__name__,
                    )
        receipt = await self._receipt(operation_id)
        if not receipt.unfinished:
            async with self._database.transaction() as connection:
                await self._repository.purge_scope_metadata(connection, tuple(reversed(receipt_scope_ids(receipt))))
                await self._repository.complete_deletion(connection, operation_id)
            receipt = await self._receipt(operation_id)
        return receipt

    async def operation(self, operation_id: str, /) -> ScopeDeletionReceipt | None:
        """Read one operation and its progress for an authorized manager."""

        try:
            return await self._receipt(operation_id)
        except ScopeNotFoundError:
            return None

    async def state_of(self, scope_id: str, /) -> ScopeDescriptor:
        async with self._database.transaction() as connection:
            scope = await self._repository.get(connection, scope_id)
            if scope is None:
                raise ScopeNotFoundError(scope_id)
            return scope

    async def _cut_off(self, connection: AsyncConnection, preview: ScopeDeletionPreview) -> None:
        """Make the set unavailable before any resource is removed."""

        for member in preview.members:
            if not await self._repository.transition_lifecycle(
                connection, member.scope_id, "writable", "deleting"
            ) and not await self._repository.transition_lifecycle(connection, member.scope_id, "archived", "deleting"):
                scope = await self._repository.get(connection, member.scope_id)
                raise ScopeLifecycleConflictError(
                    member.scope_id, "deleting", "<missing>" if scope is None else scope.lifecycle_state
                )

    async def _require_childless(self, connection: AsyncConnection, preview: ScopeDeletionPreview) -> None:
        children = await self._repository.child_scope_ids(connection, preview.scope_ids)
        outside = tuple(child for child in children if child not in preview.scope_ids)
        if outside:
            raise ScopeDeletionConflictError(
                preview.root_scope_id, "a single-Scope delete requires a Scope without structural children"
            )

    async def _outside_children(
        self, connection: AsyncConnection, members: tuple[ScopeDeletionMember, ...]
    ) -> tuple[str, ...]:
        inside = {member.scope_id for member in members}
        children = await self._repository.child_scope_ids(connection, tuple(inside))
        return tuple(child for child in children if child not in inside)

    async def _require_unbound(self, connection: AsyncConnection, preview: ScopeDeletionPreview) -> None:
        """Refuse while a deployment default or persistent binding still names a deleted Scope.

        Rebinding silently would delete a Scope an operator is still pointing at,
        so the repair has to be explicit.
        """

        for member in preview.members:
            if await self._repository.default_scope_id(connection) == member.scope_id:
                raise ScopeDeletionBindingRepairError(member.scope_id, None, is_default=True)
            binding = await self._repository.binding_to(connection, member.scope_id)
            if binding is not None:
                raise ScopeDeletionBindingRepairError(member.scope_id, binding, is_default=False)

    def _handler(self, name: str) -> ScopeResourceHandler:
        for handler in self._handlers:
            if handler.name == name:
                return handler
        raise ScopeResourceHandlerUnsupportedError(name, "no registered handler owns this resource")

    async def _receipt(self, operation_id: str) -> ScopeDeletionReceipt:
        async with self._database.transaction() as connection:
            found = await self._repository.deletion(connection, operation_id)
        if found is None:
            raise ScopeNotFoundError(operation_id)
        record, progress = found
        members = _decode_members(record["confirmed_scopes"])
        return ScopeDeletionReceipt(
            operation_id=str(record["operation_id"]),
            root_scope_id=str(record["root_scope_id"]),
            membership_digest=str(record["membership_digest"]),
            state="complete" if str(record["state"]) == "complete" else "cleanup_pending",
            members=members,
            progress=tuple(
                ScopeResourceProgress(
                    scope_id=str(row["scope_id"]),
                    handler=str(row["handler"]),
                    state=row["state"],
                    attempts=int(row["attempts"]),
                    last_error_code=None if row["last_error_code"] is None else str(row["last_error_code"]),
                )
                for row in progress
            ),
        )


def membership_digest(members: Sequence[ScopeDeletionMember], /) -> str:
    """Bind a deletion to the exact structural state the requester confirmed.

    This mirrors the bounded range resolver's contract: any attachment, move or
    metadata change alters a member's parent or version and therefore the digest,
    while an unchanged set repeats it. A digest is evidence of equality, not an
    authorization for any member.
    """

    lines = sorted(f"{m.scope_id}\t{m.parent_scope_id or ''}\t{m.version}" for m in members)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def receipt_scope_ids(receipt: ScopeDeletionReceipt, /) -> tuple[str, ...]:
    """Members in confirmed order: root first, then structural descendants.

    ``pc_scopes.parent_scope_id`` references ``pc_scopes`` itself, so the Scope
    rows must be removed in the reverse of this order.
    """

    return tuple(member.scope_id for member in receipt.members)


def _encode_members(members: tuple[ScopeDeletionMember, ...]) -> bytes:
    return rfc8785.dumps([member.model_dump(mode="json") for member in members])


def _decode_members(payload: bytes | memoryview | str) -> tuple[ScopeDeletionMember, ...]:
    import json

    raw = bytes(payload).decode("utf-8") if isinstance(payload, (bytes, bytearray, memoryview)) else payload
    return tuple(ScopeDeletionMember.model_validate(item) for item in json.loads(raw))


def _operation_id() -> str:
    value = int.from_bytes(secrets.token_bytes(10), "big")
    return "del_" + "".join(_CROCKFORD[(value >> shift) & 31] for shift in range(75, -1, -5))


__all__ = [
    "ScopeDeletionApplication",
    "membership_digest",
    "receipt_scope_ids",
]
