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

"""A named Scope's archive state cuts off its writes and leaves children alone."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import Experience, ExperienceContent
from powercontext.builtin.artifacts.experience.models import ExperienceDraft
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.candidates import CandidateRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.scope.application import ScopeApplication
from powercontext.builtin.scope.errors import (
    ScopeLifecycleConflictError,
    ScopeNotWritableError,
    ScopeVersionConflictError,
)
from powercontext.builtin.scope.models import ScopeDraft


def _experience(lesson: str) -> ExperienceContent:
    return ExperienceContent(situation="Retry", action="Idempotency key", outcome="Once", lesson=lesson)


def _run(test) -> None:
    asyncio.run(test())


def test_archive_cuts_off_this_scope_writes_and_leaves_children_writable(tmp_path) -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'scope.db'}"), tables=BUILTIN_TABLES
        ) as profile:
            app = ScopeApplication(profile.database)
            root = await app.create(ScopeDraft(title="Orders", summary="Project", idempotency_key="root"))
            child = await app.create(
                ScopeDraft(title="Interface", summary="Library", parent_scope_id=root.scope_id, idempotency_key="child")
            )
            sources = SourceRepository(app_source_registry())
            artifacts = ArtifactRepository((Experience,))

            async with profile.database.transaction() as connection:
                await sources.add(connection, root.scope_id, capture("before"))
            async with profile.database.transaction() as connection:
                created = await artifacts.create(
                    connection,
                    root.scope_id,
                    "retry",
                    ExperienceDraft(content=_experience("Before")),
                )
            assert created.revision == 1

            archived = await app.archive(root.scope_id, root.version)
            assert archived.lifecycle_state == "archived"
            # A child keeps its own state: archive never cascades down the tree.
            assert (await app.get(child.scope_id)).lifecycle_state == "writable"

            with pytest.raises(ScopeNotWritableError):
                async with profile.database.transaction() as connection:
                    await sources.add(connection, root.scope_id, capture("after"))

            with pytest.raises(ScopeNotWritableError):
                async with profile.database.transaction() as connection:
                    await artifacts.revise(
                        connection,
                        root.scope_id,
                        created,
                        ExperienceDraft(content=_experience("After")),
                    )

            with pytest.raises(ScopeNotWritableError):
                async with profile.database.transaction() as connection:
                    await CandidateRepository({"experience": ExperienceContent}).create(
                        connection,
                        root.scope_id,
                        "candidate",
                        "experience",
                        _experience("Queued"),
                        sources=(),
                        artifacts=(ArtifactRef(family="experience", artifact_id="retry", revision=1),),
                        target=None,
                        reason="Queued before archive",
                    )

            # The archived Scope keeps its readable history and exact identity.
            assert (await app.get(root.scope_id)).scope_id == root.scope_id
            # A child Scope still accepts ordinary content.
            async with profile.database.transaction() as connection:
                await sources.add(connection, child.scope_id, capture("child"))

    _run(scenario)


def test_reopen_restores_writes_and_keeps_the_same_scope_identity(tmp_path) -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'reopen.db'}"), tables=BUILTIN_TABLES
        ) as profile:
            app = ScopeApplication(profile.database)
            scope = await app.create(ScopeDraft(title="Orders", summary="Project", idempotency_key="s"))
            sources = SourceRepository(app_source_registry())

            archived = await app.archive(scope.scope_id, scope.version)
            reopened = await app.reopen(scope.scope_id, archived.version)
            assert reopened.scope_id == scope.scope_id
            assert reopened.lifecycle_state == "writable"
            async with profile.database.transaction() as connection:
                await sources.add(connection, scope.scope_id, capture("resumed"))
            assert (await app.get(scope.scope_id)).scope_id == scope.scope_id

    _run(scenario)


def test_archive_serializes_against_stale_versions_and_repeat_requests(tmp_path) -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'conflict.db'}"), tables=BUILTIN_TABLES
        ) as profile:
            app = ScopeApplication(profile.database)
            scope = await app.create(ScopeDraft(title="Orders", summary="Project", idempotency_key="s"))

            archived = await app.archive(scope.scope_id, scope.version)
            # The state change advances the version, so a second caller that only
            # read the pre-archive descriptor cannot silently reapply it.
            with pytest.raises(ScopeVersionConflictError):
                await app.archive(scope.scope_id, scope.version)
            # A current version on the wrong state is a lifecycle conflict, not a
            # version conflict: the caller's view is fresh and the request is invalid.
            with pytest.raises(ScopeLifecycleConflictError):
                await app.archive(scope.scope_id, archived.version)
            reopened = await app.reopen(scope.scope_id, archived.version)
            with pytest.raises(ScopeLifecycleConflictError):
                await app.reopen(scope.scope_id, reopened.version)

    _run(scenario)


def app_source_registry():
    from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY

    return BUILTIN_SOURCE_REGISTRY


def capture(text: str):
    from powercontext.builtin.sources import ContentSource
    from powercontext.sources import SourceMaterialization

    return ContentSource(
        name=f"src_{uuid.uuid4().hex[:12]}",
        materialization=SourceMaterialization.CAPTURED,
        content=text,
    )
