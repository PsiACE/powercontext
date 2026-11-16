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

"""A legally created scoped Dream run must read back over the public boundary.

A run may name its own Scope as the owner of an explicitly admitted reference. That record is
pinned with a ``scoped`` entry and per-node/per-group origin, so the wire must preserve it: a run
the runtime accepted before the contract knew the field would otherwise fail its own HTTP readback.
The foreign-Scope case stays refused, and an ordinary local run keeps the exact shape it had.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import httpx

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.authz import (
    AccessAuditContext,
    AccessRole,
    CreateBinding,
    PrincipalRef,
    ResourceRef,
)
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.dream.models import CreateDreamRunRequest
from powercontext.builtin.evidence.models import ScopedEvidence
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, RuntimeConfig
from powercontext.builtin.scope import ScopeDraft
from powercontext.client import PowerContextClient
from powercontext.server.app import ServerApplication
from powercontext.server.dream_access import DreamAccess
from tests.e2e.dream_support import open_dream_runtime, process_pending

ADMIN = PrincipalRef(type="service", id="admin")
AUTHOR = PrincipalRef(type="user", id="author")
AUDIT = AccessAuditContext(transport="http", operation="dream-readback", request_id="dream-readback")
DEPLOYMENT_ID = "dream-readback"


class _NoChangeProposal:
    config_id = "dream-readback"

    async def generate(self, value):
        from powercontext.builtin.dream.models import DreamPlan
        from powercontext.builtin.inference.models import GenerationResult

        return GenerationResult(
            output=DreamPlan(
                outcome="no_change",
                intent="corroborate",
                reason="The recorded evidence already states the conclusion.",
                evidence_ids=tuple(item.evidence_id for item in value.evidence.evidence),
            )
        )


def test_scoped_dream_run_reads_back_through_http_and_sdk(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'dream-readback.db'}")
        async with open_builtin_access_control(
            database, bootstrap_administrators=(ADMIN,), deployment_id=DEPLOYMENT_ID
        ) as access:
            adapter = DreamAccess(access)
            async with open_dream_runtime(
                BuiltinConfig(database=database, runtime=RuntimeConfig()),
                dream_generator=_NoChangeProposal(),
                dream_authorizer=adapter.authorize,
                dream_authorization_context=access.defer_decision_audit,
                dream_candidate_attester=adapter.attest_candidate,
            ) as runtime:
                assert runtime.scopes is not None
                scope_id = (
                    await runtime.scopes.create(
                        ScopeDraft(title="Orders", summary="Scoped readback", idempotency_key="readback")
                    )
                ).scope_id
                for role, key in (
                    (AccessRole.SCOPE_CONTRIBUTOR, "author-contributor"),
                    (AccessRole.SCOPE_REVIEWER, "author-reviewer"),
                    (AccessRole.SCOPE_ADMIN, "author-admin"),
                ):
                    await access.create_binding(
                        ADMIN,
                        CreateBinding(
                            subject=AUTHOR,
                            resource=ResourceRef.scope(scope_id),
                            role=role,
                            idempotency_key=key,
                        ),
                        context=AUDIT,
                    )
                source = await runtime.sources.for_scope(scope_id).capture(
                    CaptureSource(source_id="retry-trace", content="The new channel retries safely.", metadata={})
                )
                # A same-Scope qualified selection: legal today only through the configured runtime,
                # because no public create field exposes it yet.
                created = await runtime.dream.for_scope(scope_id, principal_id=_principal(AUTHOR)).create(
                    CreateDreamRunRequest(
                        operation="refine_experience",
                        scoped=(ScopedEvidence(scope_id=scope_id, source=source.source_ref),),
                        idempotency_key="scoped-readback",
                    )
                )
                await process_pending(runtime)
                stored = await runtime.dream.for_scope(scope_id, principal_id=_principal(AUTHOR)).get(
                    _get(created.run_id)
                )
                assert stored.input_manifest is not None
                # F records the run's own Scope as the owner of an explicit selection.
                assert [(item.scope_id, item.source == source.source_ref) for item in stored.input_manifest.scoped] == [
                    (scope_id, True)
                ]

                app = _app(runtime, access)
                async with (
                    app.router.lifespan_context(app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://testserver",
                        headers={"Authorization": "Bearer author-token", "X-PowerContext-Dream-Contract": "2"},
                    ) as client,
                ):
                    fetched = await client.get(f"/v1/scopes/{scope_id}/dream/{created.run_id}")
                    assert fetched.status_code == 200, fetched.text
                    manifest = fetched.json()["input_manifest"]
                    # The complete address survives, and each reference keeps its identity.
                    assert manifest["scoped"] == [
                        {
                            "scope_id": scope_id,
                            "artifact": None,
                            "source": {"source_type": "content", "source_id": "retry-trace"},
                        }
                    ]
                    assert manifest["nodes"], manifest
                    # The run names its own Scope, so no node or group carries a foreign origin key.
                    assert all("origin_scope_id" not in node for node in manifest["nodes"]), manifest["nodes"]
                    assert all("scope_id" not in group for group in manifest["root_groups"]), manifest["root_groups"]
                    # The scoped record keeps the unset other address as null: that null is the
                    # stored shape, not an absence to strip.
                    assert manifest["scoped"][0]["source"] is not None
                    assert "artifact" in manifest["scoped"][0]

                    listed = await client.get(f"/v1/scopes/{scope_id}/dream")
                    assert listed.status_code == 200, listed.text
                    runs = [item for item in listed.json()["runs"] if item["run_id"] == created.run_id]
                    assert runs and runs[0]["input_manifest"]["scoped"] == manifest["scoped"]

                    # The SDK must observe the same record: a model-validated read, not a raw dict.
                    async with PowerContextClient(
                        "http://testserver",
                        token="author-token",  # noqa: S106 - local test credential
                        http_client=client,
                        trust_transport_security=True,
                    ) as sdk:
                        sdk_run = await sdk.get_dream_run(scope_id, created.run_id)
                        sdk_manifest = sdk_run.input_manifest
                        assert sdk_manifest is not None
                        # The SDK model keeps the same origin identity the HTTP body carried.
                        sdk_entry = sdk_manifest.scoped[0]
                        assert sdk_entry.scope_id == scope_id
                        sdk_source = sdk_entry.source
                        assert sdk_source is not None
                        assert (sdk_source.source_type, sdk_source.source_id, sdk_source.locator) == (
                            "content",
                            "retry-trace",
                            None,
                        )
                        sdk_page = await sdk.list_dream_runs(scope_id)
                        listed_run = next(run for run in sdk_page.runs if run.run_id == created.run_id)
                        assert listed_run.input_manifest is not None
                        assert listed_run.input_manifest.scoped == sdk_manifest.scoped

                    # A foreign or scoped HTTP create stays unsupported before any model runs.
                    rejected = await client.post(
                        f"/v1/scopes/{scope_id}/dream",
                        json={
                            "operation": "refine_experience",
                            "scoped": [
                                {
                                    "scope_id": scope_id,
                                    "source": {"source_type": "content", "source_id": "retry-trace"},
                                }
                            ],
                            "idempotency_key": "scoped-over-http",
                        },
                    )
                    assert rejected.status_code == 422, rejected.text

    asyncio.run(scenario())


def test_ordinary_local_dream_wire_shape_is_unchanged(tmp_path: Path) -> None:
    """A local run carries no scoped entry at all, so its response shape stays as accepted."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'dream-local.db'}")
        async with open_builtin_access_control(
            database, bootstrap_administrators=(ADMIN,), deployment_id=DEPLOYMENT_ID
        ) as access:
            adapter = DreamAccess(access)
            async with open_dream_runtime(
                BuiltinConfig(database=database, runtime=RuntimeConfig()),
                dream_generator=_NoChangeProposal(),
                dream_authorizer=adapter.authorize,
                dream_authorization_context=access.defer_decision_audit,
                dream_candidate_attester=adapter.attest_candidate,
            ) as runtime:
                assert runtime.scopes is not None
                scope_id = (
                    await runtime.scopes.create(
                        ScopeDraft(title="Local", summary="Local readback", idempotency_key="local")
                    )
                ).scope_id
                await access.create_binding(
                    ADMIN,
                    CreateBinding(
                        subject=AUTHOR,
                        resource=ResourceRef.scope(scope_id),
                        role=AccessRole.SCOPE_CONTRIBUTOR,
                        idempotency_key="local-contributor",
                    ),
                    context=AUDIT,
                )
                source = await runtime.sources.for_scope(scope_id).capture(
                    CaptureSource(source_id="local-note", content="A local observation.", metadata={})
                )
                artifact = await runtime.records.for_scope(scope_id).create_artifact(
                    "experience",
                    ArtifactWrite(
                        content={
                            "situation": "A write timed out.",
                            "action": "Replay with the same idempotency key.",
                            "outcome": "No duplicate record was created.",
                            "lesson": "Retry only with a deduplication contract.",
                        }
                    ),
                )
                created = await runtime.dream.for_scope(scope_id, principal_id=_principal(AUTHOR)).create(
                    CreateDreamRunRequest(
                        operation="refine_experience",
                        artifacts=(
                            ArtifactRef(
                                family="experience", artifact_id=artifact.artifact_id, revision=artifact.revision
                            ),
                        ),
                        sources=(source.source_ref,),
                        idempotency_key="local-readback",
                    )
                )
                await process_pending(runtime)

                app = _app(runtime, access)
                async with (
                    app.router.lifespan_context(app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://testserver",
                        headers={"Authorization": "Bearer author-token", "X-PowerContext-Dream-Contract": "2"},
                    ) as client,
                ):
                    fetched = await client.get(f"/v1/scopes/{scope_id}/dream/{created.run_id}")
                    assert fetched.status_code == 200, fetched.text
                    manifest = fetched.json()["input_manifest"]
                    # An ordinary local run never gains an empty scoped entry, and a local node or
                    # root group omits its origin key entirely rather than emitting null.
                    assert "scoped" not in manifest
                    assert manifest["nodes"], manifest
                    assert all("origin_scope_id" not in node for node in manifest["nodes"]), manifest["nodes"]
                    assert manifest["root_groups"], manifest
                    assert all("scope_id" not in group for group in manifest["root_groups"]), manifest["root_groups"]

    asyncio.run(scenario())


def _principal(principal: PrincipalRef) -> str:
    from powercontext.builtin.authz.dream import principal_identity

    return principal_identity(principal)


def _get(run_id: str):
    from powercontext.builtin.runtime import GetDreamRunRequest

    return GetDreamRunRequest(run_id=run_id)


def _app(runtime, access):
    from starlette.middleware import Middleware

    from powercontext.server.app import create_app
    from powercontext.server.authentication import StaticBearerAuthenticationProvider
    from powercontext.server.middleware import AuthenticationMiddleware

    provider = StaticBearerAuthenticationProvider("author-token", AUTHOR)
    return create_app(
        application=cast(ServerApplication, runtime),
        access_control=access,
        access_mode="enforced",
        authentication_provider=provider,
        middleware=[Middleware(AuthenticationMiddleware, provider=provider)],
    )
