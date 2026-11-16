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

"""A published search hit must not disclose where its evidence came from.

A revision can record origins in other Scopes. Reading such an origin needs authority at
that Scope, so a reader who may search the target Scope must still not receive those Scope
identities, references, or digests. The hit keeps the content and the direct evidence it
already published, and the recorded origins stay internal.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import httpx
from starlette.middleware import Middleware

from powercontext.artifacts import ArtifactRef, RecordedOrigin
from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.authz import (
    AccessAction,
    AccessAuditContext,
    AccessRole,
    CreateBinding,
    PrincipalRef,
    ResourceRef,
)
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.evidence.models import content_digest
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.scope import ScopeDraft
from powercontext.client import PowerContextClient
from powercontext.server.app import ServerApplication, create_app
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.middleware import AuthenticationMiddleware
from tests.e2e.dream_support import open_dream_runtime

ADMIN = PrincipalRef(type="service", id="origin-admin")
VIEWER = PrincipalRef(type="user", id="origin-viewer")
AUDIT = AccessAuditContext(transport="test", operation="origin-disclosure", request_id="origin-disclosure")
DEPLOYMENT_ID = "origin-disclosure"
EXPERIENCE: dict[str, Any] = {
    "situation": "A write timed out on the old channel.",
    "action": "Replay with the same idempotency key.",
    "outcome": "No duplicate record was created.",
    "lesson": "Retry only with a deduplication contract.",
}


def test_enforced_search_discloses_no_origin_address(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'disclosure.db'}")
        async with (
            open_builtin_access_control(
                database, bootstrap_administrators=(ADMIN,), deployment_id=DEPLOYMENT_ID
            ) as access,
            open_dream_runtime(BuiltinConfig(database=database, runtime=RuntimeConfig())) as runtime,
        ):
            assert runtime.scopes is not None
            target = (
                await runtime.scopes.create(ScopeDraft(title="Orders", summary="Target", idempotency_key="t"))
            ).scope_id
            alice = (
                await runtime.scopes.create(ScopeDraft(title="Alice", summary="Origin", idempotency_key="a"))
            ).scope_id
            bob = (await runtime.scopes.create(ScopeDraft(title="Bob", summary="Origin", idempotency_key="b"))).scope_id
            artifact = await runtime.records.for_scope(target).create_artifact(
                "experience", ArtifactWrite(content=EXPERIENCE)
            )
            local_ref = ArtifactRef(family="experience", artifact_id=artifact.artifact_id, revision=artifact.revision)
            shard = "retry-trace"
            origins = (
                RecordedOrigin(
                    scope_id=alice,
                    artifact=ArtifactRef(family="experience", artifact_id=shard, revision=2),
                    digest=content_digest(b"alice"),
                ),
                RecordedOrigin(
                    scope_id=bob,
                    artifact=ArtifactRef(family="experience", artifact_id=shard, revision=2),
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
            approved = await review.approve(candidate.candidate_id, candidate.version)
            committed_ref = approved.result_artifact
            assert committed_ref is not None
            async with runtime._provider.database.transaction() as connection:
                committed = await runtime._provider.repositories.artifacts.get(connection, target, committed_ref)
                await runtime._provider.experience_index.replace(connection, target, committed)
            # The recorded origins are real and remain readable internally.
            assert {(item.scope_id, item.digest) for item in committed.lineage.origins} == {
                (alice, content_digest(b"alice")),
                (bob, content_digest(b"bob")),
            }

            # The reader may search the target Scope and nothing else.
            await access.create_binding(
                ADMIN,
                CreateBinding(
                    subject=VIEWER,
                    resource=ResourceRef.scope(target),
                    role=AccessRole.SCOPE_VIEWER,
                    idempotency_key="target-viewer",
                ),
                context=AUDIT,
            )
            denied = await access.check(VIEWER, AccessAction.SCOPE_READ, ResourceRef.scope(alice), context=AUDIT)
            assert denied.allowed is False

            provider = StaticBearerAuthenticationProvider("viewer-token", VIEWER)
            app = create_app(
                application=cast(ServerApplication, runtime),
                access_control=access,
                access_mode="enforced",
                authentication_provider=provider,
                middleware=[Middleware(AuthenticationMiddleware, provider=provider)],
            )
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                    headers={"Authorization": "Bearer viewer-token"},
                ) as http,
            ):
                response = await http.post(f"/v1/scopes/{target}/artifacts/experience/search", json={"query": "retry"})
                assert response.status_code == 200, response.text
                body = response.json()
                assert body["results"], body
                # The target Scope legitimately returns more than one hit, and the committed
                # revision carrying the origins is not necessarily first, so every hit is checked
                # rather than one fixed position.
                assert {hit["artifact_id"] for hit in body["results"]} >= {committed_ref.artifact_id}
                carrier = next(hit for hit in body["results"] if hit["artifact_id"] == committed_ref.artifact_id)
                assert carrier["content"]["lesson"] == EXPERIENCE["lesson"]
                for hit in body["results"]:
                    # The permitted target content and direct evidence are unchanged.
                    assert sorted(hit["lineage"]) == [
                        "artifacts",
                        "publication_digest",
                        "publication_source",
                        "sources",
                    ]
                    assert "origins" not in hit["lineage"]
                # No origin address, reference, or digest crosses the boundary in any hit.
                serialized = json.dumps(body["results"])
                for scope_id in (alice, bob):
                    assert scope_id not in serialized, serialized
                for digest in (content_digest(b"alice"), content_digest(b"bob")):
                    assert digest not in serialized, serialized

                # The SDK sees the same bytes.
                async with PowerContextClient(
                    "http://testserver", http_client=http, trust_transport_security=True
                ) as sdk:
                    from powercontext.http import SearchArtifactsRequest

                    sdk_body = await sdk.search_artifacts(
                        target, "experience", SearchArtifactsRequest.model_validate({"query": "retry"})
                    )
                assert "origins" not in json.dumps(sdk_body.model_dump(mode="json"))

    asyncio.run(scenario())
