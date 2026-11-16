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

"""Recorded origins are persisted exactly, and no public readback claims them yet.

The runtime records the full admitted origin set on a Candidate and on the committed revision, and
reads it back from persistence with its exact Scope identity and digest. The Candidate, revision and
create payloads must keep their existing shapes rather than gaining a key the domain never
published. Search is the surface that does decode origins, so its disclosure boundary is covered
separately in ``test_origin_disclosure_boundary.py``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from powercontext.artifacts import ArtifactRef, RecordedOrigin
from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.evidence.models import content_digest
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.scope import ScopeDraft
from powercontext.client import PowerContextClient
from powercontext.server import mapping
from powercontext.server.app import create_app
from tests.e2e.dream_support import open_dream_runtime

EXPERIENCE: dict[str, Any] = {
    "situation": "A write timed out on the old channel.",
    "action": "Replay with the same idempotency key.",
    "outcome": "No duplicate record was created.",
    "lesson": "Retry only with a deduplication contract.",
}


def test_recorded_origins_persist_exactly_and_stay_out_of_public_payloads(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'origins.db'}")
        async with open_dream_runtime(BuiltinConfig(database=database, runtime=RuntimeConfig())) as runtime:
            assert runtime.scopes is not None
            target = (
                await runtime.scopes.create(ScopeDraft(title="Orders", summary="Target", idempotency_key="t"))
            ).scope_id
            # Two origin Scopes deliberately share one local Artifact id: only the Scope tells them apart.
            alice = (
                await runtime.scopes.create(ScopeDraft(title="Alice", summary="Origin", idempotency_key="a"))
            ).scope_id
            bob = (await runtime.scopes.create(ScopeDraft(title="Bob", summary="Origin", idempotency_key="b"))).scope_id
            artifact = await runtime.records.for_scope(target).create_artifact(
                "experience", ArtifactWrite(content=EXPERIENCE)
            )
            local_ref = ArtifactRef(family="experience", artifact_id=artifact.artifact_id, revision=artifact.revision)
            shared_id = "retry-trace"
            origins = (
                RecordedOrigin(
                    scope_id=alice,
                    artifact=ArtifactRef(family="experience", artifact_id=shared_id, revision=2),
                    digest=content_digest(b"alice"),
                ),
                RecordedOrigin(
                    scope_id=bob,
                    artifact=ArtifactRef(family="experience", artifact_id=shared_id, revision=2),
                    digest=content_digest(b"bob"),
                ),
            )

            review = RelationalContexts(database=runtime._provider.database).review(target)
            candidate = await review.propose_experience_from_origins(
                ExperienceContent(**EXPERIENCE),
                sources=(),
                artifacts=(local_ref,),
                origins=origins,
                target=None,
                reason="Reconciled the two channels.",
            )
            # The admitted origin set is real: equal local ids stay two origins with their own digest.
            assert [
                (item.scope_id, item.artifact and item.artifact.artifact_id, item.digest) for item in candidate.origins
            ] == [(alice, shared_id, content_digest(b"alice")), (bob, shared_id, content_digest(b"bob"))]

            approved = await review.approve(candidate.candidate_id, candidate.version)
            committed_ref = approved.result_artifact
            assert committed_ref is not None
            async with runtime._provider.database.transaction() as connection:
                committed = await runtime._provider.repositories.artifacts.get(connection, target, committed_ref)
            # The committed revision keeps the exact origins, including a Scope that was not cited.
            assert [
                (item.scope_id, item.artifact and item.artifact.artifact_id, item.digest)
                for item in committed.lineage.origins
            ] == [(alice, shared_id, content_digest(b"alice")), (bob, shared_id, content_digest(b"bob"))]

            # No published read surface claims those addresses, so neither payload may carry them.
            assert "origins" not in mapping.candidate_response(candidate).model_dump(mode="json")

            app = create_app(application=runtime, infrastructure_only=False)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http,
            ):
                revision = await http.get(
                    f"/v1/scopes/{target}/artifacts/experience/{committed_ref.artifact_id}"
                    f"/revisions/{committed_ref.revision}"
                )
                assert revision.status_code == 200, revision.text
                created = await http.post(
                    f"/v1/scopes/{target}/artifacts", json={"family": "experience", "content": EXPERIENCE}
                )
                assert created.status_code == 201, created.text
                async with PowerContextClient(
                    "http://testserver", http_client=http, trust_transport_security=True
                ) as sdk:
                    sdk_revision = await sdk.get_artifact_revision(
                        target, "experience", committed_ref.artifact_id, committed_ref.revision
                    )
            assert sdk_revision is not None

            for body in (revision.json(), created.json(), sdk_revision.model_dump(mode="json")):
                # An ordinary read/write carries no origin addresses and no empty origins key.
                assert "origins" not in json.dumps(body), body

    asyncio.run(scenario())
