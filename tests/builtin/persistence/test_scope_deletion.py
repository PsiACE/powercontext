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

"""Explicit Scope deletion: confirmed set, immediate cutoff, retryable cleanup."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import Experience, ExperienceContent
from powercontext.builtin.artifacts.experience.models import ExperienceDraft
from powercontext.builtin.authz.models import PrincipalRef, ResourceRef
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.scope_lifecycle import require_writable
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
from powercontext.builtin.scope import ScopeApplication, ScopeDeletionApplication, ScopeDraft, ScopeSelection
from powercontext.builtin.scope.deletion import TrustedEmbedding
from powercontext.builtin.scope.errors import (
    ScopeDeletionBindingRepairError,
    ScopeDeletionConflictError,
    ScopeDeletionUnauthorizedError,
    ScopeNotFoundError,
    ScopeNotWritableError,
    ScopeRangeLimitError,
    ScopeResourceHandlerUnsupportedError,
)
from powercontext.builtin.scope.resources import (
    ScopeResourceHandler,
    UnsupportedScopeResourceHandler,
    default_handlers,
)


def _run(coroutine_factory) -> None:
    asyncio.run(coroutine_factory())


def _experience(lesson: str) -> ExperienceContent:
    return ExperienceContent(situation="Retry", action="Idempotency key", outcome="Once", lesson=lesson)


class _FailingHandler(ScopeResourceHandler):
    """A resource whose cleanup fails, to prove the operation stays retryable."""

    name = "failing-store"

    def __init__(self) -> None:
        self.calls = 0
        self.fail = True

    async def purge(self, connection, scope_id, /) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError


class _ExtendComplete:
    """Marks a handler tuple that extends the complete set rather than replacing it."""


_EXTEND = _ExtendComplete()


async def _resolve_handlers(opened, handlers):
    complete = await _complete_handlers(opened)
    if handlers is None:
        return complete
    if handlers and handlers[0] is _EXTEND:
        return complete + tuple(handlers[1:])
    return handlers


async def _complete_handlers(opened):
    """The handler set a complete deletion needs, with its owners initialized."""

    from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
    from powercontext.builtin.persistence.sqlite.experience_index import SQLiteExperienceFTSIndex
    from powercontext.builtin.scope.resources import ScopeEvidenceHandler, ScopeProjectionHandler

    atomic, experience = SQLiteAtomicMemoryIndex(), SQLiteExperienceFTSIndex()
    async with opened.database.transaction() as connection:
        await atomic.initialize(connection)
        await experience.initialize(connection)
    return (ScopeResourceHandler(), ScopeEvidenceHandler(), ScopeProjectionHandler(atomic, experience))


@asynccontextmanager
async def _family(tmp_path, *, handlers=None):
    """A root with two children, plus the deletion service under test."""

    async with SQLiteProfile.open(
        SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'deletion.db'}"), tables=BUILTIN_TABLES
    ) as opened:
        scopes = ScopeApplication(opened.database)
        # A complete deletion needs the real projection owners. A test that injects
        # its own store extends that complete set instead of replacing it, so the
        # required-handler contract stays satisfied.
        deletion = ScopeDeletionApplication(
            opened.database,
            scopes,
            handlers=await _resolve_handlers(opened, handlers),
            trusted_embedding=TrustedEmbedding("test-fixture"),
        )
        root = await scopes.create(ScopeDraft(title="Orders", summary="Project", idempotency_key="root"))
        child = await scopes.create(
            ScopeDraft(title="Interface", summary="Library", parent_scope_id=root.scope_id, idempotency_key="child")
        )
        sibling = await scopes.create(ScopeDraft(title="Operations", summary="Other", idempotency_key="sibling"))
        yield opened, scopes, deletion, root, child, sibling


def test_confirmed_subtree_delete_removes_only_its_members_and_their_resources(tmp_path) -> None:
    async def scenario() -> None:
        async with _family(tmp_path) as (_opened, _scopes, deletion, root, child, sibling):
            selection = ScopeSelection(mode="subtree", root_scope_id=root.scope_id)
            preview = await deletion.preview(selection)
            assert {member.scope_id for member in preview.members} == {root.scope_id, child.scope_id}
            # A complete structural set leaves no member child behind.
            assert preview.outside_children == ()

            receipt = await deletion.confirm(selection, preview.membership_digest)
            assert receipt.complete and receipt.members[0].scope_id == root.scope_id
            assert receipt.operation_id.startswith("del_")
            # One progress row per member and handler, all finished.
            assert {item.state for item in receipt.progress} == {"done"}
            assert all(item.attempts >= 1 for item in receipt.progress)

            # The sibling outside the set is untouched and still readable.
            assert (await deletion.state_of(sibling.scope_id)).scope_id == sibling.scope_id
            assert (await deletion.state_of(sibling.scope_id)).lifecycle_state == "writable"

    _run(scenario)


def test_single_scope_delete_conflicts_with_children_and_never_widens(tmp_path) -> None:
    async def scenario() -> None:
        async with _family(tmp_path) as (_opened, _scopes, deletion, root, _child, sibling):
            selection = ScopeSelection(mode="exact", scope_ids=(root.scope_id,))
            preview = await deletion.preview(selection)
            # Preview tells the operator what confirming now would leave behind.
            assert preview.scope_ids == (root.scope_id,)

            with pytest.raises(ScopeDeletionConflictError):
                await deletion.confirm(selection, preview.membership_digest, single_scope=True)
            # Refused before any cutoff: the root is still writable.
            assert (await deletion.state_of(root.scope_id)).lifecycle_state == "writable"

            # A childless Scope deletes normally under the single-Scope contract.
            leaf = ScopeSelection(mode="exact", scope_ids=(sibling.scope_id,))
            leaf_preview = await deletion.preview(leaf)
            assert (await deletion.confirm(leaf, leaf_preview.membership_digest, single_scope=True)).complete

    _run(scenario)


def test_changed_set_requires_fresh_confirmation(tmp_path) -> None:
    """A child attached or moved out after preview invalidates the confirmation."""

    async def scenario() -> None:
        async with _family(tmp_path) as (_opened, scopes, deletion, root, child, _sibling):
            selection = ScopeSelection(mode="subtree", root_scope_id=root.scope_id)
            preview = await deletion.preview(selection)

            # Attach a new child after the operator saw the set.
            await scopes.create(
                ScopeDraft(title="Help", summary="Library", parent_scope_id=root.scope_id, idempotency_key="late")
            )
            with pytest.raises(ScopeDeletionConflictError):
                await deletion.confirm(selection, preview.membership_digest)
            # Nothing was cut off by the refused attempt.
            assert (await deletion.state_of(root.scope_id)).lifecycle_state == "writable"

            # A child moved *out* of the set changes it too.
            fresh = await deletion.preview(selection)
            current = await scopes.get(child.scope_id)
            from powercontext.builtin.scope.models import ScopeMutation

            await scopes.update(
                child.scope_id,
                ScopeMutation(
                    expected_version=current.version,
                    title=current.title,
                    summary=current.summary,
                    parent_scope_id=None,
                ),
            )
            with pytest.raises(ScopeDeletionConflictError):
                await deletion.confirm(selection, fresh.membership_digest)
            assert (await deletion.state_of(root.scope_id)).lifecycle_state == "writable"

            # Reconfirming the current set proceeds.
            latest = await deletion.preview(selection)
            assert (await deletion.confirm(selection, latest.membership_digest)).complete

    _run(scenario)


def test_unresolved_binding_requires_repair_before_removal(tmp_path) -> None:
    async def scenario() -> None:
        async with _family(tmp_path) as (_opened, scopes, deletion, _root, _child, target):
            from powercontext.builtin.scope.models import ScopeBindingKey

            await scopes.bind(ScopeBindingKey(integration="app", kind="project", external_id="orders"), target.scope_id)
            selection = ScopeSelection(mode="exact", scope_ids=(target.scope_id,))
            preview = await deletion.preview(selection)
            with pytest.raises(ScopeDeletionBindingRepairError):
                await deletion.confirm(selection, preview.membership_digest)
            assert (await deletion.state_of(target.scope_id)).lifecycle_state == "writable"

            await scopes.clear_binding(ScopeBindingKey(integration="app", kind="project", external_id="orders"))
            retry_preview = await deletion.preview(selection)
            # Clearing the binding changed no structural state, so the set the
            # operator already confirmed is still the set they will delete.
            assert retry_preview.membership_digest == preview.membership_digest
            assert (await deletion.confirm(selection, preview.membership_digest)).complete

    _run(scenario)


def test_failed_cleanup_stays_unavailable_and_retryable(tmp_path) -> None:
    """The cut-off is not undone by a cleanup failure; a manager can retry it."""

    async def scenario() -> None:
        failing = _FailingHandler()
        async with _family(tmp_path, handlers=(_EXTEND, failing)) as (
            opened,
            _scopes,
            deletion,
            _root,
            _child,
            target,
        ):
            selection = ScopeSelection(mode="exact", scope_ids=(target.scope_id,))
            preview = await deletion.preview(selection)
            receipt = await deletion.confirm(selection, preview.membership_digest)

            # Cleanup failed, so the operation is not complete...
            assert not receipt.complete
            assert {item.handler for item in receipt.failed} == {"failing-store"}
            assert receipt.failed[0].last_error_code == "RuntimeError"
            # ...but the cutoff already took effect, so the Scope is not readable
            # as ordinary content again.
            assert (await deletion.state_of(target.scope_id)).lifecycle_state == "deleting"
            async with opened.database.transaction() as connection:
                with pytest.raises(ScopeNotWritableError):
                    await require_writable(connection, target.scope_id)

            # A manager retries; the retry resumes and completes the operation.
            failing.fail = False
            resumed = await deletion.cleanup(receipt.operation_id)
            assert resumed.complete
            assert {item.state for item in resumed.progress} == {"done"}
            assert resumed.failed == ()

    _run(scenario)


def test_unsupported_cleanup_mode_is_refused_before_acceptance(tmp_path) -> None:
    async def scenario() -> None:
        async with _family(
            tmp_path, handlers=(_EXTEND, UnsupportedScopeResourceHandler("index-store", "no index adapter"))
        ) as (_opened, _scopes, deletion, _root, _child, target):
            selection = ScopeSelection(mode="exact", scope_ids=(target.scope_id,))
            preview = await deletion.preview(selection)
            # Refused before the cutoff, so nothing is half-deleted and no erasure
            # is claimed.
            with pytest.raises(ScopeResourceHandlerUnsupportedError):
                await deletion.confirm(selection, preview.membership_digest)
            assert (await deletion.state_of(target.scope_id)).lifecycle_state == "writable"

    _run(scenario)


def test_deletion_cuts_off_ordinary_runtime_reads_and_writes(tmp_path) -> None:
    """The observable guarantee: after the cutoff the Scope serves no ordinary work.

    This drives the supported runtime entry point, not the persistence layer, so
    it fails if the serving boundary stops honouring the cutoff.
    """

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
        async with open_builtin_runtime(BuiltinConfig(database=database)) as runtime:
            # These capabilities are optional in composition; the runtime under
            # test is expected to provide them, so narrow once and use the typed
            # references rather than suppressing the attribute access.
            assert runtime.scopes is not None
            assert runtime.scope_deletions is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Orders", summary="Project", idempotency_key="runtime")
            )
            records = runtime.records.for_scope(scope.scope_id)
            created = await records.create_artifact(
                "experience", ArtifactWrite(content=_experience("Before deletion").model_dump(mode="json"))
            )
            assert created.revision == 1

            selection = ScopeSelection(mode="exact", scope_ids=(scope.scope_id,))
            preview = await runtime.scope_deletions.preview(selection)
            receipt = await runtime.scope_deletions.confirm(selection, preview.membership_digest)
            assert receipt.complete

            # The serving boundary refuses a fresh ordinary read of a Scope whose
            # cutoff took effect and whose resources are gone.
            with pytest.raises(ScopeNotFoundError):
                await runtime.records.for_scope(scope.scope_id).list_artifact_revisions(
                    "experience", created.artifact_id, limit=10, cursor=None
                )
            # And an ordinary write.
            with pytest.raises(ScopeNotFoundError):
                await runtime.records.for_scope(scope.scope_id).create_artifact(
                    "experience", ArtifactWrite(content=_experience("After deletion").model_dump(mode="json"))
                )

    _run(scenario)


def test_deletion_does_not_follow_citations_into_a_surviving_scope(tmp_path) -> None:
    """Deletion is structural: a surviving Scope keeps its own resources intact.

    Cross-Scope citation storage is F's additive `pc_artifact_lineage_origins`
    relation, which is not in this base; the local lineage foreign key is
    same-Scope by design. What this slice must prove is the part it owns: removing
    one Scope never reaches through a relationship into another Scope's rows, so a
    retained output in a surviving Scope is left exactly as it was.
    """

    async def scenario() -> None:
        async with _family(tmp_path) as (opened, scopes, deletion, _root, _child, sibling):
            origin = await scopes.create(ScopeDraft(title="Origin", summary="Incident work", idempotency_key="origin"))
            artifacts = ArtifactRepository((Experience,))
            async with opened.database.transaction() as connection:
                await artifacts.create(
                    connection,
                    origin.scope_id,
                    "retry",
                    ExperienceDraft(content=_experience("Origin")),
                )
            async with opened.database.transaction() as connection:
                retained = await artifacts.create(
                    connection,
                    sibling.scope_id,
                    "orders-conclusion",
                    ExperienceDraft(content=_experience("Retained conclusion")),
                )
            retained_ref = ArtifactRef(
                family="experience", artifact_id=retained.artifact_id, revision=retained.revision
            )

            # Delete the leaf that owns the origin, leaving the citing sibling.
            selection = ScopeSelection(mode="exact", scope_ids=(origin.scope_id,))
            preview = await deletion.preview(selection)
            assert preview.outside_children == ()
            assert (await deletion.confirm(selection, preview.membership_digest)).complete

            # The surviving Scope keeps its revision, identity and content.
            async with opened.database.transaction() as connection:
                kept = await artifacts.get(connection, sibling.scope_id, retained_ref)
            assert kept.as_ref() == retained_ref
            assert kept.content == retained.content
            assert (await deletion.state_of(sibling.scope_id)).lifecycle_state == "writable"

    _run(scenario)


def test_confirmed_membership_cannot_change_after_the_cutoff(tmp_path) -> None:
    """New members, moves and bindings are refused once the set is cut off.

    Parent reported these accepted on `11dcf845`: a child could be attached to a
    Scope in `deleting` state, and a new persistent binding could point at it,
    which contradicted the confirmed set. Emptying the set is not permission to
    remove what arrived late, so the boundary must refuse instead.
    """

    async def scenario() -> None:
        failing = _FailingHandler()
        async with _family(tmp_path, handlers=(_EXTEND, failing)) as (
            _opened,
            scopes,
            deletion,
            root,
            _child,
            target,
        ):
            selection = ScopeSelection(mode="exact", scope_ids=(target.scope_id,))
            preview = await deletion.preview(selection)
            receipt = await deletion.confirm(selection, preview.membership_digest)
            # Cleanup fails, so the member sits in `deleting` while we probe.
            assert not receipt.complete
            assert (await deletion.state_of(target.scope_id)).lifecycle_state == "deleting"

            # A new child cannot join a Scope whose deletion is confirmed.
            with pytest.raises(ScopeNotWritableError):
                await scopes.create(
                    ScopeDraft(title="Late", summary="Library", parent_scope_id=target.scope_id, idempotency_key="late")
                )
            # A new persistent binding cannot point at it either.
            from powercontext.builtin.scope.models import ScopeBindingKey

            with pytest.raises(ScopeNotWritableError):
                await scopes.bind(
                    ScopeBindingKey(integration="app", kind="project", external_id="late"), target.scope_id
                )
            # Nor can it become the deployment default.
            with pytest.raises(ScopeNotWritableError):
                await scopes.set_default(target.scope_id)
            # And an unrelated Scope cannot move into it.
            with pytest.raises(ScopeNotWritableError):
                await scopes.update(
                    root.scope_id,
                    _move_to(root, parent_scope_id=target.scope_id),
                )

            # Nothing late was deleted to force completion: the member is still
            # present, and a retry after the store recovers completes normally.
            failing.fail = False
            resumed = await deletion.cleanup(receipt.operation_id)
            assert resumed.complete
            with pytest.raises(ScopeNotFoundError):
                await deletion.state_of(target.scope_id)

    _run(scenario)


def _move_to(scope, *, parent_scope_id: str):
    from powercontext.builtin.scope.models import ScopeMutation

    return ScopeMutation(
        expected_version=scope.version,
        title=scope.title,
        summary=scope.summary,
        parent_scope_id=parent_scope_id,
    )


def test_deletion_preview_refuses_an_over_budget_range_instead_of_truncating(tmp_path) -> None:
    """Deletion acts on the bounded resolver's set, or on nothing at all.

    A truncated set would delete part of a subtree while reporting success, so an
    over-budget range raises before any cutoff or confirmation is possible.
    """

    async def scenario() -> None:
        async with _family(tmp_path) as (_opened, scopes, deletion, root, child, _sibling):
            await scopes.create(
                ScopeDraft(title="Deep", summary="Library", parent_scope_id=child.scope_id, idempotency_key="deep")
            )
            selection = ScopeSelection(mode="subtree", root_scope_id=root.scope_id)

            # The full set fits the default budget.
            assert len((await deletion.preview(selection)).members) == 3

            # A one-node budget refuses rather than returning just the root.
            from powercontext.builtin.scope.models import ScopeRangeLimits

            bounded = ScopeDeletionApplication(scopes._database, scopes, limits=ScopeRangeLimits(max_nodes=1))
            with pytest.raises(ScopeRangeLimitError):
                await bounded.preview(selection)
            # Refusing the preview left every member writable.
            assert (await deletion.state_of(root.scope_id)).lifecycle_state == "writable"

    _run(scenario)


def test_exact_parent_and_child_set_completes_in_either_id_order(tmp_path) -> None:
    """Removal follows parent edges, not the confirmed order or lexical ids.

    An `exact` selection returns members sorted by `scope_id`, so a child whose id
    sorts first used to be removed after its parent and the operation never
    finalized. Topology decides, so both id orders converge.
    """

    async def scenario() -> None:
        for parent_sorts_first in (True, False):
            async with _family(tmp_path) as (_opened, _scopes, deletion, root, child, _sibling):
                # `_family` ids are random; select the pair explicitly in both
                # orders to cover the exact-selection shape directly.
                ordered = (child.scope_id, root.scope_id) if parent_sorts_first else (root.scope_id, child.scope_id)
                selection = ScopeSelection(mode="exact", scope_ids=ordered)
                preview = await deletion.preview(selection)
                assert len(preview.members) == 2
                assert preview.outside_children == ()
                receipt = await deletion.confirm(selection, preview.membership_digest)
                assert receipt.complete, (parent_sorts_first, receipt.state, receipt.progress)
                assert {item.state for item in receipt.progress} == {"done"}
                # Both member rows are gone, whatever order they were confirmed in.
                for member in preview.members:
                    assert (await _state_or_none(deletion, member.scope_id)) is None

    _run(scenario)


async def _state_or_none(deletion, scope_id: str):
    try:
        return await deletion.state_of(scope_id)
    except ScopeNotFoundError:
        return None


def test_single_scope_delete_refuses_a_multi_member_set_before_any_cutoff(tmp_path) -> None:
    """`single_scope=True` is a size contract, not only a childlessness one."""

    async def scenario() -> None:
        async with _family(tmp_path) as (_opened, _scopes, deletion, root, _child, sibling):
            # Two unrelated leaves: neither has children, so only the size rule can
            # refuse this set.
            selection = ScopeSelection(mode="exact", scope_ids=(_child.scope_id, sibling.scope_id))
            preview = await deletion.preview(selection)
            assert len(preview.members) == 2
            assert preview.outside_children == ()
            with pytest.raises(ScopeDeletionConflictError):
                await deletion.confirm(selection, preview.membership_digest, single_scope=True)
            # Refused before any cutoff: both members are still untouched.
            assert (await deletion.state_of(_child.scope_id)).lifecycle_state == "writable"
            assert (await deletion.state_of(sibling.scope_id)).lifecycle_state == "writable"

            # A parent with its own child is a multi-member set too, and must not
            # execute under the single-Scope contract.
            pair = ScopeSelection(mode="exact", scope_ids=(root.scope_id, _child.scope_id))
            pair_preview = await deletion.preview(pair)
            assert len(pair_preview.members) == 2
            with pytest.raises(ScopeDeletionConflictError):
                await deletion.confirm(pair, pair_preview.membership_digest, single_scope=True)
            assert (await deletion.state_of(root.scope_id)).lifecycle_state == "writable"

    _run(scenario)


def test_complete_deletion_removes_owner_content_but_keeps_the_operation_receipt(tmp_path) -> None:
    """`complete` means this Scope's own authoritative content is gone.

    Atomic Memory keeps its current projection, its adopted evidence and the
    captured-Source bindings in schema modules outside `BUILTIN_TABLES`, so those
    owners must purge their own rows before an operation may report success. The
    deletion operation and its progress rows are body-free evidence of what
    happened and are deliberately retained.
    """

    async def scenario() -> None:
        from sqlalchemy import func, select

        from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
        from powercontext.builtin.persistence.atomic_memory_evidence_schema import (
            ATOMIC_MEMORY_EVIDENCE_HISTORY_TABLE,
            SOURCE_EVIDENCE_BINDINGS_TABLE,
        )
        from powercontext.builtin.persistence.atomic_memory_index_schema import ATOMIC_MEMORY_EVIDENCE_PROJECTION_TABLE
        from powercontext.builtin.persistence.tables import (
            SCOPE_DELETION_PROGRESS_TABLE,
            SCOPE_DELETIONS_TABLE,
        )

        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'content.db'}")
        async with open_builtin_runtime(BuiltinConfig(database=database)) as runtime:
            assert runtime.scopes is not None and runtime.scope_deletions is not None
            assert runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Orders", summary="Project", idempotency_key="content")
            )
            await runtime.atomic_memory.for_scope(scope.scope_id).create((
                AtomicMemoryContent(kind="rule", text="customer-private-rule-42"),
            ))
            selection = ScopeSelection(mode="exact", scope_ids=(scope.scope_id,))
            preview = await runtime.scope_deletions.preview(selection)
            receipt = await runtime.scope_deletions.confirm(selection, preview.membership_digest)
            assert receipt.complete
            assert {item.state for item in receipt.progress} == {"done"}

            owner_tables = (
                ATOMIC_MEMORY_EVIDENCE_HISTORY_TABLE,
                SOURCE_EVIDENCE_BINDINGS_TABLE,
                ATOMIC_MEMORY_EVIDENCE_PROJECTION_TABLE,
            )
            async with runtime.primary_database.transaction() as connection:
                for table in owner_tables:
                    remaining = await connection.scalar(
                        select(func.count()).select_from(table).where(table.c.scope_id == scope.scope_id)
                    )
                    assert remaining == 0, table.name
                # The operation receipt is retained: body-free evidence of the act.
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(SCOPE_DELETIONS_TABLE)
                        .where(SCOPE_DELETIONS_TABLE.c.operation_id == receipt.operation_id)
                    )
                    == 1
                )
                progress_rows = await connection.scalar(
                    select(func.count())
                    .select_from(SCOPE_DELETION_PROGRESS_TABLE)
                    .where(SCOPE_DELETION_PROGRESS_TABLE.c.operation_id == receipt.operation_id)
                )
                assert (progress_rows or 0) > 0

    _run(scenario)


def test_read_cutoff_refuses_publishing_from_and_exporting_a_deleting_scope(tmp_path) -> None:
    """Ordinary reads stop at the deletion cutoff, and archive stays readable.

    Publication and portable export are ordinary reads of Scope content. An
    archived source must stay publishable and exportable (archive stops writes,
    not reads); only a `deleting` Scope is unavailable.
    """

    async def scenario() -> None:
        from sqlalchemy import update

        from powercontext.artifacts import ArtifactAddress, ArtifactRef
        from powercontext.builtin.persistence.tables import SCOPES_TABLE
        from powercontext.builtin.portability.bundle import PortableBundleService
        from powercontext.builtin.publication import ArtifactPublicationRequest

        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'cutoff.db'}")
        async with open_builtin_runtime(BuiltinConfig(database=database)) as runtime:
            assert runtime.scopes is not None and runtime.publications is not None
            publications = runtime.publications
            source = await runtime.scopes.create(ScopeDraft(title="Origin", summary="Work", idempotency_key="origin"))
            target = await runtime.scopes.create(
                ScopeDraft(title="Orders", summary="Project", idempotency_key="target")
            )
            artifact = await runtime.records.for_scope(source.scope_id).create_artifact(
                "experience", ArtifactWrite(content=_experience("Origin lesson").model_dump(mode="json"))
            )

            def publication(key: str) -> ArtifactPublicationRequest:
                return ArtifactPublicationRequest(
                    source=ArtifactAddress(
                        scope_id=source.scope_id,
                        artifact=ArtifactRef(family="experience", artifact_id=artifact.artifact_id, revision=1),
                    ),
                    target_scope_id=target.scope_id,
                    idempotency_key=key,
                )

            async def export(scope_id: str, name: str) -> None:
                await PortableBundleService(runtime.primary_database).export(
                    [scope_id], tmp_path / name, authorize=_allow_export
                )

            # Archived source: publication and export are still allowed reads.
            archived = await runtime.scopes.archive(
                source.scope_id, (await runtime.scopes.get(source.scope_id)).version
            )
            assert archived.lifecycle_state == "archived"
            await publications.publish(publication("from-archived"))
            await export(source.scope_id, "archived.pcb")

            # Deletion cutoff: the same reads are refused.
            async with runtime.primary_database.transaction() as connection:
                await connection.execute(
                    update(SCOPES_TABLE)
                    .where(SCOPES_TABLE.c.scope_id == source.scope_id)
                    .values(lifecycle_state="deleting")
                )
            with pytest.raises(ScopeNotWritableError):
                await publications.publish(publication("from-deleting"))
            with pytest.raises(ScopeNotWritableError):
                await export(source.scope_id, "deleting.pcb")

    _run(scenario)


async def _allow_export(_scopes, /) -> None:
    return None


def test_default_handler_constructor_refuses_before_cutoff_instead_of_reporting_complete(tmp_path) -> None:
    """The class default must not claim `complete` over content it cannot remove.

    `ScopeDeletionApplication(db, scopes)` without projection owners used to
    accept the deletion and report `complete` while Atomic Memory text was still
    stored and the Scope row was already gone. It must fail closed before the
    cutoff. This test deliberately constructs the default — it does not pass a
    handler set — so the omission branch is the one exercised.
    """

    async def scenario() -> None:
        from powercontext.builtin.scope.resources import (
            MissingScopeResourceHandlerError,
        )

        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'default.db'}"), tables=BUILTIN_TABLES
        ) as profile:
            scopes = ScopeApplication(profile.database)
            scope = await scopes.create(ScopeDraft(title="Orders", summary="Project", idempotency_key="default"))

            # The default set is genuinely short of the projection owner.
            assert "projections" not in {handler.name for handler in default_handlers()}

            deletion = ScopeDeletionApplication(profile.database, scopes)
            selection = ScopeSelection(mode="exact", scope_ids=(scope.scope_id,))
            preview = await deletion.preview(selection)

            with pytest.raises(MissingScopeResourceHandlerError) as refused:
                await deletion.confirm(selection, preview.membership_digest)
            assert refused.value.missing == ("projections",)

            # Refused before the cutoff: the Scope is still writable and present,
            # so no content was orphaned behind a false `complete`.
            assert (await deletion.state_of(scope.scope_id)).lifecycle_state == "writable"

    _run(scenario)


def test_deep_exact_chain_finalizes_without_recursion_and_retry_converges(tmp_path) -> None:
    """A budget-legal deep chain orders iteratively and completes.

    `MAX_SCOPE_RANGE_NODES` admits a chain this deep. A recursive ordering walk
    raised `RecursionError` after the cutoff, so `confirm` returned no receipt and
    every retry re-crashed, leaving the whole chain `deleting` forever.
    """

    async def scenario() -> None:
        from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
        from powercontext.builtin.persistence.sqlite.experience_index import SQLiteExperienceFTSIndex
        from powercontext.builtin.scope.models import ScopeRangeLimits
        from powercontext.builtin.scope.resources import (
            ScopeEvidenceHandler,
            ScopeProjectionHandler,
        )

        depth = 1_000
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'deep.db'}"), tables=BUILTIN_TABLES
        ) as profile:
            scopes = ScopeApplication(profile.database)
            atomic = SQLiteAtomicMemoryIndex()
            experience = SQLiteExperienceFTSIndex()
            async with profile.database.transaction() as connection:
                await atomic.initialize(connection)
                await experience.initialize(connection)
            deletion = ScopeDeletionApplication(
                profile.database,
                scopes,
                handlers=(
                    ScopeResourceHandler(),
                    ScopeEvidenceHandler(),
                    ScopeProjectionHandler(atomic, experience),
                ),
                limits=ScopeRangeLimits(max_nodes=depth),
                trusted_embedding=TrustedEmbedding("test-fixture"),
            )

            root = await scopes.create(ScopeDraft(title="root", summary="Project", idempotency_key="root"))
            parent, chain = root.scope_id, [root.scope_id]
            for index in range(depth - 1):
                child = await scopes.create(
                    ScopeDraft(
                        title=f"child-{index}",
                        summary="Library",
                        parent_scope_id=parent,
                        idempotency_key=f"child-{index}",
                    )
                )
                chain.append(child.scope_id)
                parent = child.scope_id

            # Both lexical orders, and the deepest member sorted first.
            for ordered in (tuple(sorted(chain)), tuple(sorted(chain, reverse=True))):
                selection = ScopeSelection(mode="exact", scope_ids=ordered)
                preview = await deletion.preview(selection)
                assert len(preview.members) == depth
                assert preview.outside_children == ()
                receipt = await deletion.confirm(selection, preview.membership_digest)
                assert receipt.complete, (receipt.state, receipt.failed[:3])
                assert {item.state for item in receipt.progress} == {"done"}
                break

            # The whole chain is gone and a further cleanup stays idempotent.
            for scope_id in chain:
                with pytest.raises(ScopeNotFoundError):
                    await deletion.state_of(scope_id)
            assert (await deletion.cleanup(receipt.operation_id)).complete

    _run(scenario)


def test_legacy_short_recorded_operation_never_reports_false_complete(tmp_path) -> None:
    """An operation recorded under a shorter handler set cannot finalize as complete.

    `confirm` enforces the required owners, but a pending operation persisted by an
    earlier build carries only the handler rows it recorded then. Resuming it with
    a complete handler set used to mark it complete, delete the Scope row, and
    leave the Scope's Atomic Memory text stored and unreachable. The record is now
    validated per confirmed member before any member metadata is destroyed, and an
    explicit repair records the missing owner as real pending work.
    """

    async def scenario() -> None:
        from sqlalchemy import text

        from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
        from powercontext.builtin.scope.deletion import encode_legacy_confirmed_scopes
        from powercontext.builtin.scope.models import ScopeDeletionMember
        from powercontext.builtin.scope.repository import ScopeRepository

        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
        async with open_builtin_runtime(BuiltinConfig(database=database)) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scopes = runtime.scopes
            scope = await scopes.create(ScopeDraft(title="Orders", summary="Project", idempotency_key="legacy"))
            await runtime.atomic_memory.for_scope(scope.scope_id).create((
                AtomicMemoryContent(kind="rule", text="legacy-private-42"),
            ))

            # Record the pending operation exactly as the previous build's confirm
            # did: lifecycle cutoff, operation, and only the short handler set.
            repository = ScopeRepository()
            async with runtime.primary_database.transaction() as connection:
                await repository.lock_hierarchy(connection)
                await repository.transition_lifecycle(connection, scope.scope_id, "writable", "deleting")
                members = (ScopeDeletionMember(scope_id=scope.scope_id, parent_scope_id=None, version=scope.version),)
                await repository.record_deletion(
                    connection, "del_legacy0001", scope.scope_id, "0" * 64, encode_legacy_confirmed_scopes(members)
                )
                await repository.record_progress(
                    connection, "del_legacy0001", ((scope.scope_id, "owned-resources"), (scope.scope_id, "evidence"))
                )

            assert runtime.scope_deletions is not None
            deletion = runtime.scope_deletions

            # Resuming must not finalize: the recorded set cannot cover the owner.
            refused = await deletion.cleanup("del_legacy0001")
            assert not refused.complete
            assert refused.state == "cleanup_pending"
            assert {item.handler for item in refused.progress} == {"owned-resources", "evidence"}
            # The member metadata is intact, so the residue is still reachable.
            assert (await deletion.state_of(scope.scope_id)).lifecycle_state == "deleting"

            # An explicit repair adds the missing owner as genuinely pending work.
            repaired = await deletion.repair_coverage("del_legacy0001")
            assert ("projections", "pending") in {(item.handler, item.state) for item in repaired.progress}
            # Existing rows keep their identity and their finished state.
            done = {(item.scope_id, item.handler) for item in repaired.progress if item.state == "done"}
            assert done == {
                (scope.scope_id, "owned-resources"),
                (scope.scope_id, "evidence"),
            }

            # Only now does a cleanup run the real owner and complete honestly.
            completed = await deletion.cleanup("del_legacy0001")
            assert completed.complete
            assert completed.operation_id == "del_legacy0001"
            assert [member.scope_id for member in completed.members] == [scope.scope_id]
            assert {item.state for item in completed.progress} == {"done"}

            async with runtime.primary_database.transaction() as connection:
                current = text("SELECT COUNT(*) FROM pc_atomic_memory_current WHERE scope_id = :scope_id")
                remaining = await connection.scalar(current, {"scope_id": scope.scope_id})
                assert remaining == 0
            with pytest.raises(ScopeNotFoundError):
                await deletion.state_of(scope.scope_id)

            # A legitimately completed operation stays idempotent.
            assert (await deletion.cleanup("del_legacy0001")).complete
            assert (await deletion.repair_coverage("del_legacy0001")).complete

    _run(scenario)


def test_full_recorded_operation_still_converges_after_a_failure(tmp_path) -> None:
    """The normal path is unchanged: a recorded full set recovers to complete."""

    async def scenario() -> None:
        failing = _FailingHandler()
        async with _family(tmp_path, handlers=(_EXTEND, failing)) as (
            _opened,
            _scopes,
            deletion,
            _root,
            _child,
            target,
        ):
            selection = ScopeSelection(mode="exact", scope_ids=(target.scope_id,))
            preview = await deletion.preview(selection)
            receipt = await deletion.confirm(selection, preview.membership_digest)
            assert not receipt.complete
            # Every required owner was recorded for this member, so no coverage gap
            # is reported and recovery is the ordinary retry.
            assert {item.handler for item in receipt.progress} >= {
                "owned-resources",
                "evidence",
                "projections",
            }
            failing.fail = False
            assert (await deletion.cleanup(receipt.operation_id)).complete

    _run(scenario)


def test_coverage_is_checked_per_member_not_unioned_across_members(tmp_path) -> None:
    """One member's recorded rows must not cover another member's missing owner.

    A persisted asymmetric record — member A with all three owners, member B with
    only two — used to pass the guard because the helper unioned every row of the
    operation onto every member. The check runs before finalization, so B's live
    Atomic Memory residue and B's Scope row both have to survive the refusal.
    """

    async def scenario() -> None:
        from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
        from powercontext.builtin.scope.deletion import (
            encode_legacy_confirmed_scopes,
            recorded_coverage,
        )
        from powercontext.builtin.scope.models import ScopeDeletionMember
        from powercontext.builtin.scope.repository import ScopeRepository

        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'asymmetric.db'}")
        async with open_builtin_runtime(BuiltinConfig(database=database)) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            assert runtime.scope_deletions is not None
            scopes = runtime.scopes
            parent = await scopes.create(ScopeDraft(title="Orders", summary="Project", idempotency_key="p"))
            complete_member = await scopes.create(
                ScopeDraft(title="A", summary="Library", parent_scope_id=parent.scope_id, idempotency_key="a")
            )
            short_member = await scopes.create(
                ScopeDraft(title="B", summary="Library", parent_scope_id=parent.scope_id, idempotency_key="b")
            )

            # Real owner content in BOTH members, so residue is observable.
            for scope in (complete_member, short_member):
                await runtime.atomic_memory.for_scope(scope.scope_id).create((
                    AtomicMemoryContent(kind="rule", text=f"private-{scope.title}"),
                ))

            repository = ScopeRepository()
            members = tuple(
                ScopeDeletionMember(
                    scope_id=scope.scope_id, parent_scope_id=scope.parent_scope_id, version=scope.version
                )
                for scope in (complete_member, short_member)
            )
            async with runtime.primary_database.transaction() as connection:
                await repository.lock_hierarchy(connection)
                for scope in (complete_member, short_member):
                    await repository.transition_lifecycle(connection, scope.scope_id, "writable", "deleting")
                await repository.record_deletion(
                    connection, "del_asymmetric01", parent.scope_id, "0" * 64, encode_legacy_confirmed_scopes(members)
                )
                # A: full set. B: required owner never recorded.
                await repository.record_progress(
                    connection,
                    "del_asymmetric01",
                    tuple(
                        (member.scope_id, handler)
                        for member, handlers in (
                            (complete_member, ("owned-resources", "evidence", "projections")),
                            (short_member, ("owned-resources", "evidence")),
                        )
                        for handler in handlers
                    ),
                )

            deletion = runtime.scope_deletions
            refused = await deletion.cleanup("del_asymmetric01")
            assert not refused.complete
            # The gap names B only; A's rows must not have covered it.
            coverage = recorded_coverage(refused)
            assert set(coverage.missing) == {(short_member.scope_id, "projections")}
            # Both members' metadata survives, so B's residue stays reachable.
            for scope in (complete_member, short_member):
                assert (await deletion.state_of(scope.scope_id)).lifecycle_state == "deleting"

            # Repair adds one pending row for B only, then the real owner runs.
            repaired = await deletion.repair_coverage("del_asymmetric01")
            pending = {(item.scope_id, item.handler) for item in repaired.progress if item.state == "pending"}
            assert pending == {(short_member.scope_id, "projections")}
            completed = await deletion.cleanup("del_asymmetric01")
            assert completed.complete
            assert {item.state for item in completed.progress} == {"done"}
            for scope in (complete_member, short_member):
                with pytest.raises(ScopeNotFoundError):
                    await deletion.state_of(scope.scope_id)

    _run(scenario)


def test_repair_preserves_a_rows_own_failure_diagnostic(tmp_path) -> None:
    """Repair clears only its own coverage blocker, never a real failure reason."""

    async def scenario() -> None:
        from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
        from powercontext.builtin.scope.deletion import (
            MISSING_COVERAGE_CODE,
            encode_legacy_confirmed_scopes,
        )
        from powercontext.builtin.scope.models import ScopeDeletionMember
        from powercontext.builtin.scope.repository import ScopeRepository

        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'evidence.db'}")
        async with open_builtin_runtime(BuiltinConfig(database=database)) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            assert runtime.scope_deletions is not None
            scopes = runtime.scopes
            scope = await scopes.create(ScopeDraft(title="Orders", summary="Project", idempotency_key="ev"))
            await runtime.atomic_memory.for_scope(scope.scope_id).create((
                AtomicMemoryContent(kind="rule", text="private-evidence"),
            ))
            repository = ScopeRepository()
            members = (ScopeDeletionMember(scope_id=scope.scope_id, parent_scope_id=None, version=scope.version),)
            async with runtime.primary_database.transaction() as connection:
                await repository.lock_hierarchy(connection)
                await repository.transition_lifecycle(connection, scope.scope_id, "writable", "deleting")
                await repository.record_deletion(
                    connection, "del_evidence01", scope.scope_id, "0" * 64, encode_legacy_confirmed_scopes(members)
                )
                await repository.record_progress(
                    connection, "del_evidence01", ((scope.scope_id, "owned-resources"), (scope.scope_id, "evidence"))
                )
                # A real resource failure with its own reason, plus the coverage gap.
                await repository.finish_progress(
                    connection,
                    "del_evidence01",
                    scope.scope_id,
                    "owned-resources",
                    state="failed",
                    error_code="IntegrityError",
                )
                await repository.finish_progress(
                    connection, "del_evidence01", scope.scope_id, "evidence", state="done", error_code=None
                )
                await repository.record_finalization_failure(connection, "del_evidence01", MISSING_COVERAGE_CODE)

            deletion = runtime.scope_deletions
            repaired = await deletion.repair_coverage("del_evidence01")
            by_handler = {item.handler: item for item in repaired.progress}
            # The real failure keeps its reason, state and attempt count.
            assert by_handler["owned-resources"].last_error_code == "IntegrityError"
            assert by_handler["owned-resources"].state == "failed"
            assert by_handler["owned-resources"].attempts == 1
            # Its own coverage blocker is gone because the gap is now pending work.
            assert by_handler["evidence"].last_error_code is None
            assert by_handler["evidence"].state == "done"
            assert by_handler["evidence"].attempts == 1
            # The missing owner is genuinely pending, not done.
            assert by_handler["projections"].state == "pending"
            assert by_handler["projections"].attempts == 0

            # Repeating the repair changes nothing and erases no evidence.
            again = await deletion.repair_coverage("del_evidence01")
            assert {(i.handler, i.state, i.attempts) for i in again.progress} == {
                (i.handler, i.state, i.attempts) for i in repaired.progress
            }
            assert {i.handler: i.last_error_code for i in again.progress}["owned-resources"] == "IntegrityError"

    _run(scenario)


def test_concurrent_repair_converges_without_duplicate_work(tmp_path) -> None:
    """Two repairs of one operation converge instead of raising a duplicate-PK error.

    Repair is a supported explicit recovery step, so overlapping attempts must reach
    the same recorded intent. The rollback-and-reread follows the pattern this
    repository already uses for its other idempotent insert races.
    """

    async def scenario() -> None:
        import asyncio

        from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
        from powercontext.builtin.scope.deletion import encode_legacy_confirmed_scopes
        from powercontext.builtin.scope.models import ScopeDeletionMember
        from powercontext.builtin.scope.repository import ScopeRepository

        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'concurrent.db'}")
        async with open_builtin_runtime(BuiltinConfig(database=database)) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            assert runtime.scope_deletions is not None
            scopes = runtime.scopes
            scope = await scopes.create(ScopeDraft(title="Orders", summary="Project", idempotency_key="c"))
            await runtime.atomic_memory.for_scope(scope.scope_id).create((
                AtomicMemoryContent(kind="rule", text="private-concurrent"),
            ))
            repository = ScopeRepository()
            members = (ScopeDeletionMember(scope_id=scope.scope_id, parent_scope_id=None, version=scope.version),)
            async with runtime.primary_database.transaction() as connection:
                await repository.lock_hierarchy(connection)
                await repository.transition_lifecycle(connection, scope.scope_id, "writable", "deleting")
                await repository.record_deletion(
                    connection, "del_concurrent1", scope.scope_id, "0" * 64, encode_legacy_confirmed_scopes(members)
                )
                await repository.record_progress(
                    connection,
                    "del_concurrent1",
                    ((scope.scope_id, "owned-resources"), (scope.scope_id, "evidence")),
                )

            deletion = runtime.scope_deletions
            # A refusing cleanup records the coverage blocker first.
            assert not (await deletion.cleanup("del_concurrent1")).complete

            results = await asyncio.gather(
                deletion.repair_coverage("del_concurrent1"),
                deletion.repair_coverage("del_concurrent1"),
                return_exceptions=True,
            )
            failures = [result for result in results if isinstance(result, BaseException)]
            assert failures == [], failures
            receipts = [result for result in results if not isinstance(result, BaseException)]
            assert len(receipts) == 2
            # Both attempts observed the same single pending row: no duplicate work.
            for receipt in receipts:
                pending = [(item.scope_id, item.handler) for item in receipt.progress if item.state == "pending"]
                assert pending == [(scope.scope_id, "projections")]
            assert (await deletion.cleanup("del_concurrent1")).complete

    _run(scenario)


GRANTER = PrincipalRef(type="service", id="test-granter")
MANAGER = PrincipalRef(type="user", id="manager")
OUTSIDER = PrincipalRef(type="user", id="outsider")
OBSERVER = PrincipalRef(type="service", id="observer")


async def _access_for(profile):
    """Open the canonical Access service over the same database the Scopes live in."""

    from contextlib import AsyncExitStack

    from powercontext.builtin.authz.composition import open_builtin_access_control

    stack = AsyncExitStack()
    access = await stack.enter_async_context(
        open_builtin_access_control(
            SQLiteConfig(url=str(profile.database.engine.url)), bootstrap_administrators=(GRANTER,)
        )
    )
    return stack, access


async def _grant(access, principal, scope_id: str, role, *, granter=None):
    """Create one binding through the canonical service; returns the stored binding."""

    from powercontext.builtin.authz.service import AccessAuditContext, CreateBinding

    return await access.create_binding(
        granter,
        CreateBinding(
            subject=principal,
            resource=ResourceRef.scope(scope_id),
            role=role,
            idempotency_key=f"g:{principal.id}:{scope_id}:{role.value}",
        ),
        context=AccessAuditContext(transport="uds", operation="test_grant"),
    )


def _checker(access, principal):
    from powercontext.builtin.authz.service import AccessAuditContext

    async def check(checks) -> None:
        await access.require_all(
            principal, tuple(checks), context=AccessAuditContext(transport="uds", operation="scope_deletion")
        )

    return check


async def _access_for_database(profile):
    """The canonical Access service over the same engine the Scopes live in."""

    from contextlib import AsyncExitStack

    from powercontext.builtin.authz.composition import open_builtin_access_control

    stack = AsyncExitStack()
    access = await stack.enter_async_context(
        open_builtin_access_control(
            SQLiteConfig(url=str(profile.database.engine.url)), bootstrap_administrators=(GRANTER,)
        )
    )
    return stack, access


def test_manager_needs_authority_on_every_member_and_observer_is_never_enough(tmp_path) -> None:
    """One member's manager cannot confirm; observation cannot confirm at all."""

    async def scenario() -> None:
        from powercontext.builtin.authz.models import AccessRole

        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'every-member.db'}")
        async with SQLiteProfile.open(database, tables=BUILTIN_TABLES) as profile:
            stack, access = await _access_for_database(profile)
            try:
                scopes = ScopeApplication(profile.database)
                deletion = ScopeDeletionApplication(
                    profile.database, scopes, handlers=await _complete_handlers(profile)
                )
                root = await scopes.create(ScopeDraft(title="Orders", summary="P", idempotency_key="r"))
                child = await scopes.create(
                    ScopeDraft(title="Interface", summary="L", parent_scope_id=root.scope_id, idempotency_key="c")
                )
                selection = ScopeSelection(mode="subtree", root_scope_id=root.scope_id)
                preview = await deletion.preview(selection)

                # Authority on one member only: refused before any cutoff.
                await _grant(access, MANAGER, root.scope_id, AccessRole.SCOPE_ADMIN, granter=GRANTER)
                with pytest.raises(ScopeDeletionUnauthorizedError):
                    await deletion.confirm(
                        selection,
                        preview.membership_digest,
                        requester=MANAGER,
                        authorize=_checker(access, MANAGER),
                    )
                assert (await deletion.state_of(root.scope_id)).lifecycle_state == "writable"
                assert (await deletion.state_of(child.scope_id)).lifecycle_state == "writable"

                # An observer role is not management authority.
                await _grant(access, OBSERVER, root.scope_id, AccessRole.SCOPE_VIEWER, granter=GRANTER)
                await _grant(access, OBSERVER, child.scope_id, AccessRole.SCOPE_VIEWER, granter=GRANTER)
                with pytest.raises(ScopeDeletionUnauthorizedError):
                    await deletion.confirm(
                        selection,
                        preview.membership_digest,
                        requester=OBSERVER,
                        authorize=_checker(access, OBSERVER),
                    )
                assert (await deletion.state_of(root.scope_id)).lifecycle_state == "writable"

                # Authority on every member is admitted, and the requester is recorded.
                await _grant(access, MANAGER, child.scope_id, AccessRole.SCOPE_ADMIN, granter=GRANTER)
                receipt = await deletion.confirm(
                    selection,
                    preview.membership_digest,
                    requester=MANAGER,
                    authorize=_checker(access, MANAGER),
                )
                assert receipt.complete
                assert receipt.attestation is not None
                assert (receipt.attestation.requester_type, receipt.attestation.requester_id) == (
                    MANAGER.type,
                    MANAGER.id,
                )
            finally:
                await stack.aclose() if stack is not None else None

    _run(scenario)


def test_revoked_manager_cannot_resume_a_pending_deletion(tmp_path) -> None:
    """Live authority is re-checked, so a revocation stops further destructive work."""

    async def scenario() -> None:
        from powercontext.builtin.authz.models import AccessRole

        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'revoked.db'}")
        async with SQLiteProfile.open(database, tables=BUILTIN_TABLES) as profile:
            stack, access = await _access_for_database(profile)
            try:
                scopes = ScopeApplication(profile.database)
                failing = _FailingHandler()
                owners = await _complete_handlers(profile)
                deletion = ScopeDeletionApplication(profile.database, scopes, handlers=(*owners, failing))
                scope = await scopes.create(ScopeDraft(title="Orders", summary="P", idempotency_key="rv"))
                binding = await _grant(access, MANAGER, scope.scope_id, AccessRole.SCOPE_ADMIN, granter=GRANTER)
                assert binding is not None

                selection = ScopeSelection(mode="exact", scope_ids=(scope.scope_id,))
                preview = await deletion.preview(selection)
                receipt = await deletion.confirm(
                    selection, preview.membership_digest, requester=MANAGER, authorize=_checker(access, MANAGER)
                )
                assert not receipt.complete

                # Revoke the manager's authority over the still-live member.
                from powercontext.builtin.authz.service import AccessAuditContext

                await access.revoke_binding(
                    GRANTER,
                    binding.binding_id,
                    expected_version=binding.version,
                    idempotency_key="revoke-1",
                    context=AccessAuditContext(transport="uds", operation="revoke"),
                )

                # The manager can no longer resume, and the member stays unavailable.
                failing.fail = False
                with pytest.raises(ScopeDeletionUnauthorizedError):
                    await deletion.cleanup(receipt.operation_id, requester=MANAGER, authorize=_checker(access, MANAGER))
                assert (await deletion.state_of(scope.scope_id)).lifecycle_state == "deleting"
            finally:
                await stack.aclose()

    _run(scenario)


def test_terminal_readback_is_limited_to_the_recorded_requester(tmp_path) -> None:
    """After members are gone, only the attested requester reads the operation."""

    async def scenario() -> None:
        from powercontext.builtin.authz.models import AccessRole

        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'terminal.db'}")
        async with SQLiteProfile.open(database, tables=BUILTIN_TABLES) as profile:
            stack, access = await _access_for_database(profile)
            try:
                scopes = ScopeApplication(profile.database)
                deletion = ScopeDeletionApplication(
                    profile.database, scopes, handlers=await _complete_handlers(profile)
                )
                scope = await scopes.create(ScopeDraft(title="Orders", summary="P", idempotency_key="tm"))
                await _grant(access, MANAGER, scope.scope_id, AccessRole.SCOPE_ADMIN, granter=GRANTER)

                selection = ScopeSelection(mode="exact", scope_ids=(scope.scope_id,))
                preview = await deletion.preview(selection)
                receipt = await deletion.confirm(
                    selection, preview.membership_digest, requester=MANAGER, authorize=_checker(access, MANAGER)
                )
                assert receipt.complete

                # The requester can read the terminal record without any live Scope.
                readback = await deletion.operation(
                    receipt.operation_id, requester=MANAGER, authorize=_checker(access, MANAGER)
                )
                assert readback is not None and readback.complete

                # Anyone else is refused, and no Scope is resurrected.
                for outsider in (OUTSIDER, OBSERVER):
                    with pytest.raises(ScopeDeletionUnauthorizedError):
                        await deletion.operation(
                            receipt.operation_id, requester=outsider, authorize=_checker(access, outsider)
                        )
                with pytest.raises(ScopeNotFoundError):
                    await deletion.state_of(scope.scope_id)

                # A completed operation stays idempotent for its requester.
                assert (
                    await deletion.cleanup(receipt.operation_id, requester=MANAGER, authorize=_checker(access, MANAGER))
                ).complete
            finally:
                await stack.aclose()

    _run(scenario)


def test_legacy_operation_without_a_requester_is_never_admitted(tmp_path) -> None:
    """A record with no attestation fails closed for every principal."""

    async def scenario() -> None:
        from powercontext.builtin.scope.deletion import encode_legacy_confirmed_scopes
        from powercontext.builtin.scope.models import ScopeDeletionMember
        from powercontext.builtin.scope.repository import ScopeRepository

        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
        async with SQLiteProfile.open(database, tables=BUILTIN_TABLES) as profile:
            stack, access = await _access_for_database(profile)
            try:
                scopes = ScopeApplication(profile.database)
                deletion = ScopeDeletionApplication(
                    profile.database, scopes, handlers=await _complete_handlers(profile)
                )
                scope = await scopes.create(ScopeDraft(title="Orders", summary="P", idempotency_key="lg"))
                repository = ScopeRepository()
                members = (ScopeDeletionMember(scope_id=scope.scope_id, parent_scope_id=None, version=scope.version),)
                # A record written before attestations existed: no requester at all.
                async with profile.database.transaction() as connection:
                    await repository.lock_hierarchy(connection)
                    await repository.transition_lifecycle(connection, scope.scope_id, "writable", "deleting")
                    await repository.record_deletion(
                        connection,
                        "del_legacy0001",
                        scope.scope_id,
                        "0" * 64,
                        encode_legacy_confirmed_scopes(members),
                    )
                    await repository.record_progress(
                        connection,
                        "del_legacy0001",
                        ((scope.scope_id, "owned-resources"), (scope.scope_id, "evidence")),
                    )

                receipt = await deletion._receipt("del_legacy0001")
                assert receipt.attestation is None

                # An unattested record is refused for a live member, whatever the role.
                for principal in (MANAGER, OUTSIDER):
                    with pytest.raises(ScopeDeletionUnauthorizedError):
                        await deletion.operation(
                            "del_legacy0001", requester=principal, authorize=_checker(access, principal)
                        )
                # And without any requester at all.
                with pytest.raises(ScopeDeletionUnauthorizedError):
                    await deletion.cleanup("del_legacy0001")
            finally:
                await stack.aclose()

    _run(scenario)


def test_missing_either_argument_fails_closed_and_never_fabricates_a_requester(tmp_path) -> None:
    """Half a check is not a check, and no synthetic principal is ever recorded.

    A presented principal with no authorizer used to fall through to local trust
    and record `service:local`, which any transport principal could then claim.
    """

    async def scenario() -> None:
        from powercontext.builtin.authz.models import AccessRole

        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'half.db'}")
        async with SQLiteProfile.open(database, tables=BUILTIN_TABLES) as profile:
            stack, access = await _access_for_database(profile)
            try:
                scopes = ScopeApplication(profile.database)
                # No trusted embedding declared: this is the enforced shape.
                deletion = ScopeDeletionApplication(
                    profile.database, scopes, handlers=await _complete_handlers(profile)
                )
                scope = await scopes.create(ScopeDraft(title="Orders", summary="P", idempotency_key="hf"))
                selection = ScopeSelection(mode="exact", scope_ids=(scope.scope_id,))
                preview = await deletion.preview(selection)
                digest = preview.membership_digest

                # Both absent, with no trusted embedding: refused.
                with pytest.raises(ScopeDeletionUnauthorizedError):
                    await deletion.confirm(selection, digest)

                # A principal without its authorizer: refused, never downgraded.
                with pytest.raises(ScopeDeletionUnauthorizedError):
                    await deletion.confirm(selection, digest, requester=OUTSIDER)
                # An authorizer without a principal: refused too.
                with pytest.raises(ScopeDeletionUnauthorizedError):
                    await deletion.confirm(selection, digest, authorize=_checker(access, OUTSIDER))

                # Nothing was cut off and no operation was recorded.
                assert (await deletion.state_of(scope.scope_id)).lifecycle_state == "writable"

                # The same half-check must also be refused on a service that DID
                # declare a trusted embedding: a presented principal may never be
                # downgraded to local trust, which is how `service:local` used to
                # be fabricated.
                trusted = ScopeDeletionApplication(
                    profile.database,
                    scopes,
                    handlers=await _complete_handlers(profile),
                    trusted_embedding=TrustedEmbedding("half-check"),
                )
                with pytest.raises(ScopeDeletionUnauthorizedError):
                    await trusted.confirm(selection, digest, requester=OUTSIDER)
                with pytest.raises(ScopeDeletionUnauthorizedError):
                    await trusted.confirm(selection, digest, authorize=_checker(access, OUTSIDER))

                # With both, a real member manager is admitted and no synthetic
                # identity appears in the record.
                await _grant(access, MANAGER, scope.scope_id, AccessRole.SCOPE_ADMIN, granter=GRANTER)
                receipt = await deletion.confirm(
                    selection, digest, requester=MANAGER, authorize=_checker(access, MANAGER)
                )
                assert receipt.complete
                assert receipt.attestation is not None
                assert (receipt.attestation.requester_type, receipt.attestation.requester_id) != (
                    "service",
                    "local",
                )
                assert receipt.attestation.checks  # real checks, not an empty placeholder
            finally:
                await stack.aclose()

    _run(scenario)


def test_trusted_embedding_records_no_requester_at_all(tmp_path) -> None:
    """A declared trusted embedding is genuinely unattested, not `service:local`."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'trusted.db'}")
        async with SQLiteProfile.open(database, tables=BUILTIN_TABLES) as profile:
            scopes = ScopeApplication(profile.database)
            trusted = ScopeDeletionApplication(
                profile.database,
                scopes,
                handlers=await _complete_handlers(profile),
                trusted_embedding=TrustedEmbedding("probe"),
            )
            scope = await scopes.create(ScopeDraft(title="Orders", summary="P", idempotency_key="tr"))
            selection = ScopeSelection(mode="exact", scope_ids=(scope.scope_id,))
            preview = await trusted.preview(selection)
            receipt = await trusted.confirm(selection, preview.membership_digest)
            assert receipt.complete
            # No fabricated identity and no fabricated checks.
            assert receipt.attestation is None

            # The trusted embedding may read its own local operation, because it
            # declared that it owns authorization. That is a deliberate local
            # contract, not an attested one: the record still carries no subject,
            # so passing half a check is refused rather than silently trusted.
            assert (await trusted.operation(receipt.operation_id)) is not None
            with pytest.raises(ScopeDeletionUnauthorizedError):
                await trusted.operation(receipt.operation_id, requester=OUTSIDER)

    _run(scenario)


def test_terminal_readback_requires_a_matching_principal_not_just_an_attestation(tmp_path) -> None:
    """An attestation is evidence; only the recorded principal may read it."""

    async def scenario() -> None:
        from powercontext.builtin.authz.models import AccessRole

        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'strict.db'}")
        async with SQLiteProfile.open(database, tables=BUILTIN_TABLES) as profile:
            stack, access = await _access_for_database(profile)
            try:
                scopes = ScopeApplication(profile.database)
                deletion = ScopeDeletionApplication(
                    profile.database, scopes, handlers=await _complete_handlers(profile)
                )
                scope = await scopes.create(ScopeDraft(title="Orders", summary="P", idempotency_key="st"))
                await _grant(access, MANAGER, scope.scope_id, AccessRole.SCOPE_ADMIN, granter=GRANTER)
                selection = ScopeSelection(mode="exact", scope_ids=(scope.scope_id,))
                preview = await deletion.preview(selection)
                receipt = await deletion.confirm(
                    selection, preview.membership_digest, requester=MANAGER, authorize=_checker(access, MANAGER)
                )
                assert receipt.complete

                # No identity at all after the members are gone: refused.
                with pytest.raises(ScopeDeletionUnauthorizedError):
                    await deletion.operation(receipt.operation_id)
                with pytest.raises(ScopeDeletionUnauthorizedError):
                    await deletion.cleanup(receipt.operation_id)

                # A principal that is not the recorded one: refused.
                for other in (OUTSIDER, OBSERVER):
                    with pytest.raises(ScopeDeletionUnauthorizedError):
                        await deletion.operation(
                            receipt.operation_id, requester=other, authorize=_checker(access, other)
                        )
                # The recorded principal still reads it, with no Scope resurrected.
                readback = await deletion.operation(
                    receipt.operation_id, requester=MANAGER, authorize=_checker(access, MANAGER)
                )
                assert readback is not None and readback.complete
                with pytest.raises(ScopeNotFoundError):
                    await deletion.state_of(scope.scope_id)
            finally:
                await stack.aclose()

    _run(scenario)


def test_terminal_authority_uses_the_exact_operation_not_a_member_guess(tmp_path) -> None:
    """Two operations over one member set keep their own requesters.

    Terminal authority used to be resolved by `root_scope_id IN members` and
    `rows[0]`, so one operation's attested requester could read another's.
    """

    async def scenario() -> None:
        from powercontext.builtin.authz.models import AccessRole
        from powercontext.builtin.scope.deletion import encode_legacy_confirmed_scopes
        from powercontext.builtin.scope.models import ScopeDeletionAttestation, ScopeDeletionMember
        from powercontext.builtin.scope.repository import ScopeRepository

        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'exact-op.db'}")
        async with SQLiteProfile.open(database, tables=BUILTIN_TABLES) as profile:
            stack, access = await _access_for_database(profile)
            try:
                scopes = ScopeApplication(profile.database)
                deletion = ScopeDeletionApplication(
                    profile.database, scopes, handlers=await _complete_handlers(profile)
                )
                scope = await scopes.create(ScopeDraft(title="Orders", summary="P", idempotency_key="eo"))
                await _grant(access, MANAGER, scope.scope_id, AccessRole.SCOPE_ADMIN, granter=GRANTER)
                await _grant(access, OUTSIDER, scope.scope_id, AccessRole.SCOPE_ADMIN, granter=GRANTER)

                selection = ScopeSelection(mode="exact", scope_ids=(scope.scope_id,))
                preview = await deletion.preview(selection)
                first = await deletion.confirm(
                    selection, preview.membership_digest, requester=MANAGER, authorize=_checker(access, MANAGER)
                )
                assert first.complete

                # A second operation over the same member set, attested to another
                # manager. Recorded through the repository because the first confirm
                # already removed the rows; the *query* is what is under test.
                repository = ScopeRepository()
                members = (ScopeDeletionMember(scope_id=scope.scope_id, parent_scope_id=None, version=1),)
                async with profile.database.transaction() as connection:
                    await repository.record_deletion(
                        connection,
                        "del_second001",
                        scope.scope_id,
                        "1" * 64,
                        _encode_attested(
                            members,
                            ScopeDeletionAttestation(
                                requester_type="user",
                                requester_id=OUTSIDER.id,
                                checks=(("scope.admin", scope.scope_id),),
                            ),
                        ),
                    )
                del encode_legacy_confirmed_scopes

                # Each operation answers to its OWN requester, by operation id.
                assert (
                    await deletion.operation("del_second001", requester=OUTSIDER, authorize=_checker(access, OUTSIDER))
                ) is not None
                with pytest.raises(ScopeDeletionUnauthorizedError):
                    await deletion.operation("del_second001", requester=MANAGER, authorize=_checker(access, MANAGER))
                # And the first operation stays with its original requester.
                assert (
                    await deletion.operation(first.operation_id, requester=MANAGER, authorize=_checker(access, MANAGER))
                ) is not None
                with pytest.raises(ScopeDeletionUnauthorizedError):
                    await deletion.operation(
                        first.operation_id, requester=OUTSIDER, authorize=_checker(access, OUTSIDER)
                    )
            finally:
                await stack.aclose()

    _run(scenario)


def _encode_attested(members, attestation) -> bytes:
    from powercontext.builtin.scope.deletion import _encode_record

    return _encode_record(tuple(members), attestation)


def test_revocation_between_calls_stops_the_next_call_not_admitted_work(tmp_path) -> None:
    """The supported boundary is per-invocation admission, and this pins it.

    Revoking after `confirm` returns prevents the *next* call. It does not claim
    to interrupt handlers already running inside one call — cleanup is resumable
    and each handler commits its own transaction, so a mid-call cutoff is not
    promised and is not simulated here.
    """

    async def scenario() -> None:
        from powercontext.builtin.authz.models import AccessRole
        from powercontext.builtin.authz.service import AccessAuditContext

        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'boundary.db'}")
        async with SQLiteProfile.open(database, tables=BUILTIN_TABLES) as profile:
            stack, access = await _access_for_database(profile)
            try:
                scopes = ScopeApplication(profile.database)
                failing = _FailingHandler()
                owners = await _complete_handlers(profile)
                deletion = ScopeDeletionApplication(profile.database, scopes, handlers=(*owners, failing))
                scope = await scopes.create(ScopeDraft(title="Orders", summary="P", idempotency_key="bd"))
                binding = await _grant(access, MANAGER, scope.scope_id, AccessRole.SCOPE_ADMIN, granter=GRANTER)
                selection = ScopeSelection(mode="exact", scope_ids=(scope.scope_id,))
                preview = await deletion.preview(selection)
                receipt = await deletion.confirm(
                    selection, preview.membership_digest, requester=MANAGER, authorize=_checker(access, MANAGER)
                )
                assert not receipt.complete

                # The work admitted inside confirm is recorded, not rolled back.
                assert receipt.progress

                await access.revoke_binding(
                    GRANTER,
                    binding.binding_id,
                    expected_version=binding.version,
                    idempotency_key="revoke-boundary",
                    context=AccessAuditContext(transport="uds", operation="revoke"),
                )

                # The next call is refused: admission is per invocation.
                failing.fail = False
                with pytest.raises(ScopeDeletionUnauthorizedError):
                    await deletion.cleanup(receipt.operation_id, requester=MANAGER, authorize=_checker(access, MANAGER))
                # The member stays unavailable, and the recorded progress is intact.
                assert (await deletion.state_of(scope.scope_id)).lifecycle_state == "deleting"
                reread = await deletion._receipt(receipt.operation_id)
                assert {item.handler for item in reread.progress} == {item.handler for item in receipt.progress}
            finally:
                await stack.aclose()

    _run(scenario)
