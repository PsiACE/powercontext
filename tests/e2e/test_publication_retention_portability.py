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

"""A retained publication copy survives its origin's Scope and stays portable.

The consuming Scope keeps the copy it was authorized to retain plus the exact origin address and digest.
Deleting the origin through the real Scope deletion pipeline must therefore leave the survivor readable and
exportable, and the resulting archive must restore on its own -- while a genuinely malformed publication is
still refused.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.publication import ArtifactPublicationRequest
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig
from powercontext.builtin.runtime.composition import open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft, ScopeSelection
from powercontext.server.settings import ServerSettings
from tests.e2e.server_storage import prepare_server_database

_LESSON: dict[str, Any] = {"situation": "s", "action": "a", "outcome": "o", "lesson": "Retry with one idempotency key."}


def _config(database: Path) -> BuiltinConfig:
    return BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"), runtime=RuntimeConfig())


async def _allow(_scope_ids: tuple[str, ...]) -> None:
    """The test's own authorization callback; production supplies a real decision."""


def test_retained_copy_survives_origin_scope_deletion_and_round_trips(tmp_path: Path) -> None:
    database = tmp_path / "retention.db"
    other = tmp_path / "restored.db"
    bundle = tmp_path / "history.pcbundle"

    async def produce() -> tuple[str, str]:
        await asyncio.to_thread(prepare_server_database, ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}")))
        async with open_builtin_runtime(_config(database)) as runtime:
            assert runtime.scopes is not None and runtime.publications is not None
            work = await runtime.scopes.create(ScopeDraft(title="Alice work", summary="s", idempotency_key="w"))
            project = await runtime.scopes.create(ScopeDraft(title="Orders", summary="s", idempotency_key="p"))
            created = await runtime.records.for_scope(work.scope_id).create_artifact(
                "experience", ArtifactWrite(content=_LESSON)
            )
            published = await runtime.publications.publish(
                ArtifactPublicationRequest(
                    source=ArtifactAddress(
                        scope_id=work.scope_id,
                        artifact=ArtifactRef(family="experience", artifact_id=created.artifact_id, revision=1),
                    ),
                    target_scope_id=project.scope_id,
                    idempotency_key="orders-copy",
                )
            )

            # Delete the origin through the real pipeline, not a direct SQL purge.
            deletions = runtime.scope_deletions
            assert deletions is not None
            preview = await deletions.preview(ScopeSelection(mode="exact", scope_ids=(work.scope_id,)))
            receipt = await deletions.confirm(
                ScopeSelection(mode="exact", scope_ids=(work.scope_id,)),
                preview.membership_digest,
                single_scope=True,
            )
            finished = await deletions.cleanup(receipt.operation_id)
            assert finished.state == "complete"

            # The origin's content and its own metadata are gone; the survivor is not.
            assert await runtime.records.for_scope(project.scope_id).get_artifact(
                "experience", published.target.artifact.artifact_id
            )

            archive = runtime.archive
            assert archive is not None
            await archive.export((project.scope_id,), bundle, authorize=_allow)
            # Inspection raises on an invalid bundle, so a returned result is the validity statement.
            inspection = await archive.inspect(bundle)
            assert inspection.record_count > 0
            assert project.scope_id in inspection.scopes
            return project.scope_id, published.target.artifact.artifact_id

    async def consume(scope_id: str, artifact_id: str) -> None:
        await asyncio.to_thread(prepare_server_database, ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{other}")))
        async with open_builtin_runtime(_config(other)) as runtime:
            archive = runtime.archive
            assert archive is not None
            restored = await archive.restore(bundle)
            assert restored.status == "ready"
            survivor = await runtime.records.for_scope(scope_id).get_artifact("experience", artifact_id)
            assert survivor.revision == 1

    scope_id, artifact_id = asyncio.run(produce())
    asyncio.run(consume(scope_id, artifact_id))


def test_retained_publication_requires_a_complete_address() -> None:
    """The relaxed checks must not become a blanket waiver of missing references."""

    from powercontext.builtin.portability.bundle import _publication_source_retained

    complete = {
        "source_scope_id": "scp_a",
        "source_family": "experience",
        "source_artifact_id": "art_a",
        "source_revision": 2,
        "content_digest": "sha256:abc",
    }
    assert _publication_source_retained(complete) is True

    for broken in (
        {**complete, "source_scope_id": ""},
        {**complete, "source_artifact_id": ""},
        {**complete, "source_revision": 0},
        {**complete, "source_revision": True},
        {k: v for k, v in complete.items() if k != "content_digest"},
        {**complete, "content_digest": " sha256:abc"},
    ):
        assert _publication_source_retained(broken) is False, broken
