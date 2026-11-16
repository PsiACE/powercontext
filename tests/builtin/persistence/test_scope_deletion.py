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
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.scope_lifecycle import require_writable
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
from powercontext.builtin.scope import ScopeApplication, ScopeDeletionApplication, ScopeDraft, ScopeSelection
from powercontext.builtin.scope.errors import (
    ScopeDeletionBindingRepairError,
    ScopeDeletionConflictError,
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


@asynccontextmanager
async def _family(tmp_path, *, handlers=None):
    """A root with two children, plus the deletion service under test."""

    async with SQLiteProfile.open(
        SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'deletion.db'}"), tables=BUILTIN_TABLES
    ) as opened:
        scopes = ScopeApplication(opened.database)
        deletion = ScopeDeletionApplication(
            opened.database, scopes, handlers=default_handlers() if handlers is None else handlers
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
        async with _family(tmp_path, handlers=(ScopeResourceHandler(), failing)) as (
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
            tmp_path, handlers=(UnsupportedScopeResourceHandler("index-store", "no index adapter"),)
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
        async with _family(tmp_path, handlers=(ScopeResourceHandler(), failing)) as (
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
