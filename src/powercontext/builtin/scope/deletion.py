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

import secrets
from dataclasses import dataclass

import rfc8785
from sqlalchemy.exc import IntegrityError
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
    ScopeRangeLimits,
    ScopeResourceProgress,
    ScopeSelection,
)
from powercontext.builtin.scope.repository import ScopeRepository
from powercontext.builtin.scope.resources import (
    REQUIRED_SCOPE_RESOURCE_HANDLERS,
    ScopeResourceHandler,
    default_handlers,
    require_complete_handlers,
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
        limits: ScopeRangeLimits | None = None,
    ) -> None:
        self._database = database
        self._scopes = scopes
        self._repository = ScopeRepository() if repository is None else repository
        self._handlers = default_handlers() if handlers is None else handlers
        # Bounded resolution contract: exceeding either budget refuses the whole
        # operation instead of acting on a truncated set.
        self._limits = ScopeRangeLimits() if limits is None else limits

    async def preview(self, selection: ScopeSelection, /) -> ScopeDeletionPreview:
        """Return the exact bounded structural set and its digest. This grants no authority."""

        if selection.mode == "all":
            raise ScopeDeletionConflictError("<all>", "deletion requires an explicit Scope selection")
        async with self._database.transaction() as connection:
            return await self._preview(connection, selection)

    async def _preview(self, connection: AsyncConnection, selection: ScopeSelection) -> ScopeDeletionPreview:
        # The bounded resolver owns structural membership and bounds: it reads
        # parent edges only, refuses over budget instead of truncating, and
        # carries the per-member versions the confirmation digest is built on.
        resolved = await self._scopes.resolve_range(selection, limits=self._limits)
        members = tuple(ScopeDeletionMember.of(scope) for scope in resolved.scopes)
        root_scope_id = selection.root_scope_id or selection.scope_ids[0]
        outside = await self._outside_children(connection, members)
        return ScopeDeletionPreview(
            root_scope_id=root_scope_id,
            members=members,
            membership_digest=resolved.membership_digest,
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

        # Both checks run before any cutoff: an operation that cannot clean up every
        # resource this build owns must never be accepted and then reported complete.
        require_complete_handlers(self._handlers)
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
                # "Single-Scope delete" is a size contract, not just a
                # childlessness one: a multi-member set must never execute under
                # it, including a parent together with its own child.
                if len(current.members) != 1:
                    raise ScopeDeletionConflictError(
                        current.root_scope_id,
                        "a single-Scope delete requires exactly one confirmed Scope",
                    )
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
        if receipt.unfinished:
            return receipt
        # Finishing the recorded work is not enough: the record itself may be a
        # legacy operation created under a shorter handler set, in which case the
        # required work for this build was never recorded and can never have run.
        # That is refused before any member metadata is destroyed.
        return await self._finalize(receipt)

    async def _finalize(self, receipt: ScopeDeletionReceipt) -> ScopeDeletionReceipt:
        """Remove the members once every owned resource is gone.

        Finalization is part of the retryable operation, not a side effect of it:
        a foreign key still pointing at a member means structural state arrived
        that the confirmed set did not cover, so the operation stays incomplete
        and visible instead of raising out of ``cleanup``. Nothing outside the
        confirmed set is ever deleted to force this to succeed.
        """

        coverage = recorded_coverage(receipt)
        if coverage.missing:
            # The operation cannot be honestly completed: the recorded work does
            # not cover a required owner for every confirmed member. Keep the
            # members unavailable and the operation visible, and record the gap so
            # a manager can decide how to repair it. No receipt row is rewritten,
            # no missing task is stamped done, and the Scope metadata stays.
            async with self._database.transaction() as connection:
                await self._repository.record_finalization_failure(
                    connection, receipt.operation_id, MISSING_COVERAGE_CODE
                )
            return await self._receipt(receipt.operation_id)

        members = removal_order(receipt.members)
        try:
            async with self._database.transaction() as connection:
                # Inbound references recorded after the cutoff are removed here, so
                # a surviving Scope never keeps a dangling reference either.
                await self._repository.clear_references_to(connection, receipt_scope_ids(receipt))
                await self._repository.purge_scope_metadata(connection, members)
                await self._repository.complete_deletion(connection, receipt.operation_id)
        except IntegrityError as error:
            # Something still references a confirmed member. Record that against
            # the operation so a manager sees the blocker; the members stay
            # unavailable and the next repair or retry resumes here.
            async with self._database.transaction() as connection:
                await self._repository.record_finalization_failure(
                    connection, receipt.operation_id, type(error).__name__
                )
        return await self._receipt(receipt.operation_id)

    async def repair_coverage(self, operation_id: str, /) -> ScopeDeletionReceipt:
        """Record the required owner work a legacy operation never had, as pending.

        An operation created under an earlier, shorter handler set cannot be
        completed honestly, and refusing it forever would leave the members
        unavailable with no way forward. This adds one **pending** progress row for
        each required handler the operation never recorded for a confirmed member,
        and changes nothing else: existing rows keep their identity, state and
        attempt count, no row is stamped `done`, and the operation stays
        `cleanup_pending` until `cleanup` actually runs those owners and their
        purge succeeds on the real, initialized handler.

        The caller must already hold this build's complete handler set, so the
        added work can really be performed.
        """

        require_complete_handlers(self._handlers)
        async with self._database.transaction() as connection:
            record = await self._repository.deletion(connection, operation_id)
        if record is None:
            raise ScopeNotFoundError(operation_id)
        receipt = await self._receipt(operation_id)
        if receipt.state == "complete":
            # A legitimately completed operation is idempotent and is not reopened.
            return receipt
        coverage = recorded_coverage(receipt)
        if coverage.missing:
            async with self._scopes._write_lock, self._database.transaction() as connection:
                await self._repository.lock_hierarchy(connection)
                await self._repository.record_progress(connection, operation_id, coverage.missing)
                # The gap is now recorded as real pending work, so the blocker code
                # from a previous refusal no longer describes the operation.
                await self._repository.clear_finalization_failure(connection, operation_id)
        return await self._receipt(operation_id)

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


MISSING_COVERAGE_CODE = "MissingRequiredResourceCoverage"


@dataclass(frozen=True)
class RecordedCoverage:
    """Whether an operation's recorded progress covers every required owner.

    Coverage is read from the persisted progress rows, not from the handler set
    this build happens to have, and it is checked per confirmed member so a member
    whose own required work was never recorded is not masked by another member's
    rows.
    """

    missing: tuple[tuple[str, str], ...]

    @property
    def complete(self) -> bool:
        return not self.missing


def recorded_coverage(
    receipt: ScopeDeletionReceipt,
    /,
    *,
    required: tuple[str, ...] = REQUIRED_SCOPE_RESOURCE_HANDLERS,
) -> RecordedCoverage:
    """Report each (member, required handler) the operation never recorded."""

    recorded = {(item.scope_id): {entry.handler for entry in receipt.progress} for item in receipt.progress}
    missing: list[tuple[str, str]] = []
    for member in receipt.members:
        present = recorded.get(member.scope_id, set())
        missing.extend((member.scope_id, handler) for handler in required if handler not in present)
    return RecordedCoverage(missing=tuple(missing))


def receipt_scope_ids(receipt: ScopeDeletionReceipt, /) -> tuple[str, ...]:
    """The confirmed member identities, unchanged and in recorded order."""

    return tuple(member.scope_id for member in receipt.members)


def _children_by_parent(members: tuple[ScopeDeletionMember, ...], /) -> dict[str | None, list[str]]:
    """Group the confirmed member ids by their recorded parent edge."""

    children: dict[str | None, list[str]] = {}
    for member in members:
        children.setdefault(member.parent_scope_id, []).append(member.scope_id)
    return children


def removal_order(members: tuple[ScopeDeletionMember, ...], /) -> tuple[str, ...]:
    """Order the confirmed members so every child is removed before its parent.

    ``pc_scopes.parent_scope_id`` references ``pc_scopes`` itself, so the rows
    must go deepest-first. The confirmed members arrive in resolver order, which
    differs between an ``exact`` selection (sorted by id) and a ``subtree``
    (root-first), so the order that satisfies the constraint has to come from the
    parent edges in the set itself — never from the member order or from lexical
    id order.

    The walk is iterative and bounded by the set size. The accepted range budget
    admits a deep chain of up to ``MAX_SCOPE_RANGE_NODES`` members, and a
    recursive walk would raise ``RecursionError`` after the cutoff, wedging the
    operation with no receipt. Only members of the confirmed set are returned.

    A member whose parent lies outside the set (an ``exact`` selection naming a
    child but not its parent) is ordered too, starting from the highest such
    member. A cycle cannot be ordered; it is unreachable through the relationship
    validation that guards attach and move, so an unemitted member is instead
    appended in id order and the caller's delete fails on the real constraint.
    """

    children = _children_by_parent(members)
    confirmed = {member.scope_id for member in members}
    ordered: list[str] = []
    emitted: set[str] = set()

    def deepest_first(roots: list[str]) -> None:
        for root in roots:
            if root not in confirmed or root in emitted:
                continue
            stack: list[tuple[str, bool]] = [(root, False)]
            while stack:
                scope_id, children_done = stack.pop()
                if children_done:
                    if scope_id not in emitted:
                        emitted.add(scope_id)
                        ordered.append(scope_id)
                    continue
                if scope_id in emitted:
                    continue
                stack.append((scope_id, True))
                for child in sorted(children.get(scope_id, ()), reverse=True):
                    if child in confirmed and child not in emitted:
                        stack.append((child, False))

    deepest_first(sorted(children.get(None, ())))
    deepest_first(sorted(member.scope_id for member in members))
    return tuple(ordered)


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
    "receipt_scope_ids",
]
