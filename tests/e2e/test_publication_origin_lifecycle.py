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

"""A published copy outlives the origin's physical content.

A consuming Scope keeps an independently retained copy plus the exact origin address and digest that
justified it. Deleting the origin must therefore not be blocked by, nor erase, that record — and
replaying the already completed publication must not reach back for a body that no longer exists.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.publication import ArtifactPublicationRequest
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig
from powercontext.builtin.runtime.composition import open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft

_LESSON: dict[str, Any] = {
    "situation": "s",
    "action": "a",
    "outcome": "o",
    "lesson": "Retry with one idempotency key.",
}

# Purge order for one Scope's own Artifact content. These are the origin's rows, not the survivor's.
_PURGE = (
    "DELETE FROM pc_artifact_lineage_sources WHERE scope_id=:s AND artifact_id=:a",
    "DELETE FROM pc_artifact_lineage_artifacts WHERE scope_id=:s AND artifact_id=:a",
    "DELETE FROM pc_artifact_heads WHERE scope_id=:s AND artifact_id=:a",
    "DELETE FROM pc_artifact_handles WHERE scope_id=:s AND artifact_id=:a",
    "DELETE FROM pc_artifacts WHERE scope_id=:s AND artifact_id=:a",
)


def _config(database: Path) -> BuiltinConfig:
    return BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"), runtime=RuntimeConfig())


async def _purge_origin(runtime, scope_id: str, artifact_id: str) -> None:
    async with runtime.primary_database.transaction() as connection:
        for statement in _PURGE:
            await connection.execute(text(statement), {"s": scope_id, "a": artifact_id})


async def _publish(runtime, source, target_scope_id: str, key: str):
    assert runtime.publications is not None
    return await runtime.publications.publish(
        ArtifactPublicationRequest(source=source, target_scope_id=target_scope_id, idempotency_key=key)
    )


def test_published_copy_survives_origin_purge_with_exact_provenance(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(_config(tmp_path / "publication.db")) as runtime:
            assert runtime.scopes is not None and runtime.publications is not None
            work = await runtime.scopes.create(ScopeDraft(title="Alice work", summary="s", idempotency_key="w"))
            project = await runtime.scopes.create(ScopeDraft(title="Orders", summary="s", idempotency_key="p"))
            created = await runtime.records.for_scope(work.scope_id).create_artifact(
                "experience", ArtifactWrite(content=_LESSON)
            )
            source = ArtifactAddress(
                scope_id=work.scope_id,
                artifact=ArtifactRef(family="experience", artifact_id=created.artifact_id, revision=1),
            )
            request = ArtifactPublicationRequest(
                source=source, target_scope_id=project.scope_id, idempotency_key="orders-copy"
            )
            published = await runtime.publications.publish(request)

            # The origin's physical content may be removed while the copy stays readable.
            await _purge_origin(runtime, work.scope_id, created.artifact_id)

            survivor = await runtime.records.for_scope(project.scope_id).get_artifact(
                "experience", published.target.artifact.artifact_id
            )
            assert survivor.revision == published.target.artifact.revision

            # Replaying the completed request must not refetch the purged origin.
            replayed = await runtime.publications.publish(request)
            assert replayed.target == published.target
            assert replayed.content_digest == published.content_digest

            # A new publication of the same purged origin cannot start.
            with pytest.raises(Exception):  # noqa: B017 - the runtime's own not-found error
                await _publish(runtime, source, project.scope_id, "later-request")

            # The retained provenance still names the exact origin and digest it was published from.
            async with runtime.primary_database.transaction() as connection:
                row = (
                    await connection.execute(
                        text(
                            "SELECT source_scope_id, source_family, source_artifact_id, source_revision, "
                            "content_digest FROM pc_artifact_publications WHERE target_artifact_id=:target"
                        ),
                        {"target": published.target.artifact.artifact_id},
                    )
                ).mappings().one()
            assert row["source_scope_id"] == work.scope_id
            assert row["source_artifact_id"] == created.artifact_id
            assert row["source_revision"] == 1
            assert row["content_digest"] == published.content_digest

    asyncio.run(scenario())


def test_surviving_copy_and_provenance_survive_restart_and_two_hops(tmp_path: Path) -> None:
    """The same guarantees hold for a republished copy and after a process restart."""

    database = tmp_path / "chain.db"
    carried: dict[str, object] = {}

    async def first_run() -> None:
        async with open_builtin_runtime(_config(database)) as runtime:
            assert runtime.scopes is not None and runtime.publications is not None
            work = await runtime.scopes.create(ScopeDraft(title="Alice work", summary="s", idempotency_key="w"))
            mid = await runtime.scopes.create(ScopeDraft(title="Middle", summary="s", idempotency_key="m"))
            final = await runtime.scopes.create(ScopeDraft(title="Orders", summary="s", idempotency_key="f"))
            created = await runtime.records.for_scope(work.scope_id).create_artifact(
                "experience", ArtifactWrite(content=_LESSON)
            )
            source = ArtifactAddress(
                scope_id=work.scope_id,
                artifact=ArtifactRef(family="experience", artifact_id=created.artifact_id, revision=1),
            )
            first = await _publish(runtime, source, mid.scope_id, "hop-1")
            second = await _publish(runtime, first.target, final.scope_id, "hop-2")

            await _purge_origin(runtime, work.scope_id, created.artifact_id)

            carried.update({
                "final_scope": final.scope_id,
                "final_artifact": second.target.artifact.artifact_id,
                "digest": second.content_digest,
                "source": first.target,
            })

    async def second_run() -> None:
        async with open_builtin_runtime(_config(database)) as runtime:
            final_scope = carried["final_scope"]
            final_artifact = carried["final_artifact"]
            assert isinstance(final_scope, str) and isinstance(final_artifact, str)
            survivor = await runtime.records.for_scope(final_scope).get_artifact(
                "experience", final_artifact
            )
            assert survivor.revision == 1
            replayed = await _publish(runtime, carried["source"], final_scope, "hop-2")
            assert replayed.target.artifact.artifact_id == final_artifact
            assert replayed.content_digest == carried["digest"]

    asyncio.run(first_run())
    asyncio.run(second_run())
