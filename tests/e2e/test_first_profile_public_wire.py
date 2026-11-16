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

"""First-Profile initialization through the public contract.

The runtime gained an explicit initialization operation; this checks the published wire: the
capability list advertises it, the request enum accepts it with Sources-only input, a target is
refused server-side, the run and Candidate read back with their documented shapes, and approval
commits the ordinary Profile identity at revision one. Generation is a deterministic local fixture,
not a real-provider claim.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import httpx
from starlette.middleware import Middleware

from powercontext.builtin.artifacts.profile.models import ProfileWriteContent
from powercontext.builtin.authz import (
    AccessAuditContext,
    AccessRole,
    CreateBinding,
    PrincipalRef,
    ResourceRef,
)
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.dream.bindings import operation_spec
from powercontext.builtin.dream.generation import DreamGenerationInput
from powercontext.builtin.dream.models import DreamPlan
from powercontext.builtin.inference import GenerationResult, InferenceUsage
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, RuntimeConfig
from powercontext.builtin.scope import ScopeDraft
from powercontext.client import PowerContextClient
from powercontext.http import ArtifactDreamingOperationCapability as Capability
from powercontext.http import DreamOperation
from powercontext.server.app import ServerApplication, create_app
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.dream_access import DreamAccess
from powercontext.server.middleware import AuthenticationMiddleware
from tests.e2e.dream_support import open_dream_runtime, process_pending

ADMIN = PrincipalRef(type="service", id="first-admin")
AUTHOR = PrincipalRef(type="user", id="first-author")
AUDIT = AccessAuditContext(transport="http", operation="first-profile-wire", request_id="first-profile-wire")
DEPLOYMENT_ID = "first-profile-wire"


class _InitializeGenerator:
    """Deterministic fixture: proposes the first Profile from the Scope's own Source."""

    config_id = "first-profile-wire"

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, value: DreamGenerationInput) -> GenerationResult[DreamPlan]:
        self.calls += 1
        used = next(item.evidence_id for item in value.evidence.evidence if item.kind == "source")
        return GenerationResult(
            output=DreamPlan(
                outcome="proposed",
                reason="The first Profile comes from this Scope's own evidence.",
                intent="create",
                proposal=ProfileWriteContent(content="# Profile\n\nBased in Shenzhen."),
                evidence_ids=(used,),
            ),
            usage=InferenceUsage(requests=1),
        )


def test_first_profile_initialization_over_the_public_contract(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'first-profile.db'}")
        generator = _InitializeGenerator()
        async with open_builtin_access_control(
            database, bootstrap_administrators=(ADMIN,), deployment_id=DEPLOYMENT_ID
        ) as access:
            adapter = DreamAccess(access)
            async with open_dream_runtime(
                BuiltinConfig(database=database, runtime=RuntimeConfig()),
                dream_generator=generator,
                dream_authorizer=adapter.authorize,
                dream_authorization_context=access.defer_decision_audit,
                dream_candidate_attester=adapter.attest_candidate,
            ) as runtime:
                adapter.bind(runtime)
                assert runtime.scopes is not None
                scope_id = (
                    await runtime.scopes.create(ScopeDraft(title="Orders", summary="init", idempotency_key="o"))
                ).scope_id
                for subject, role, key in (
                    (ADMIN, AccessRole.SCOPE_REVIEWER, "admin-reviewer"),
                    (AUTHOR, AccessRole.SCOPE_CONTRIBUTOR, "author-contributor"),
                    (AUTHOR, AccessRole.SCOPE_REVIEWER, "author-reviewer"),
                ):
                    await access.create_binding(
                        ADMIN,
                        CreateBinding(
                            subject=subject,
                            resource=ResourceRef.scope(scope_id),
                            role=role,
                            idempotency_key=key,
                        ),
                        context=AUDIT,
                    )
                await runtime.profiles.put_policy(scope_id, generation_enabled=True, expected_version=0)
                source = await runtime.sources.for_scope(scope_id).capture(
                    CaptureSource(source_id="move", content="I moved to Shenzhen permanently.", metadata={})
                )

                provider = StaticBearerAuthenticationProvider("author-token", AUTHOR)
                app = create_app(
                    application=cast(ServerApplication, runtime),
                    access_control=access,
                    access_mode="enforced",
                    authentication_provider=provider,
                    middleware=[Middleware(AuthenticationMiddleware, provider=provider)],
                )
                headers = {"Authorization": "Bearer author-token", "X-PowerContext-Dream-Contract": "2"}
                async with (
                    app.router.lifespan_context(app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=headers
                    ) as http,
                ):
                    # The runtime advertises the operation, and the wire enum accepts that value:
                    # this is the exact pairing whose mismatch broke a Dream-capable Server.
                    runtime_capabilities = await runtime.capabilities()
                    operations = {item.operation for item in runtime_capabilities.artifact_dreaming_operations}
                    assert "initialize_profile" in operations
                    for operation in operations:
                        Capability.model_validate({
                            "operation": operation,
                            "output_kind": operation_spec(operation).output_kind,
                            "effect": operation_spec(operation).effect,
                        })
                    assert DreamOperation.INITIALIZE_PROFILE.value == "initialize_profile"

                    # A target is refused for this operation: it creates the baseline.
                    targeted = await http.post(
                        f"/v1/scopes/{scope_id}/dream",
                        json={
                            "operation": "initialize_profile",
                            "sources": [
                                {"source_type": source.source_ref.source_type, "source_id": source.source_ref.source_id}
                            ],
                            "target": {"family": "profile", "artifact_id": "profile", "revision": 1},
                            "idempotency_key": "first-profile-targeted",
                        },
                    )
                    assert targeted.status_code == 422, targeted.text
                    details = targeted.json()["error"]["details"]["errors"]
                    assert any("invalid_target" in item["msg"] for item in details), targeted.text

                    # Sources-only initialization is accepted.
                    created = await http.post(
                        f"/v1/scopes/{scope_id}/dream",
                        json={
                            "operation": "initialize_profile",
                            "sources": [
                                {"source_type": source.source_ref.source_type, "source_id": source.source_ref.source_id}
                            ],
                            "idempotency_key": "first-profile-init",
                        },
                    )
                    assert created.status_code in {200, 202}, created.text
                    run_id = created.json()["run_id"]
                    assert created.json()["operation"] == "initialize_profile"
                    assert created.json()["target"] is None

                    await process_pending(runtime)

                    # The run reads back with the documented shape, and the Candidate is a Profile proposal.
                    fetched = await http.get(f"/v1/scopes/{scope_id}/dream/{run_id}")
                    assert fetched.status_code == 200, fetched.text
                    run = fetched.json()
                    assert run["operation"] == "initialize_profile"
                    assert run["target"] is None
                    assert run["outcome"] == "proposed", run
                    candidate_id = run["candidate"]["candidate_id"]

                    candidate = await http.post(
                        "/v1/candidates/get", json={"scope_id": scope_id, "candidate_id": candidate_id}
                    )
                    assert candidate.status_code == 200, candidate.text
                    proposal = candidate.json()["proposal"]
                    assert proposal.get("dream_run_id")
                    assert proposal["source_window"] is None

                    # Approval is the ordinary reviewed path and commits the Profile identity at revision 1.
                    approved = await http.post(
                        "/v1/candidates/approve",
                        json={
                            "scope_id": scope_id,
                            "candidate_id": candidate_id,
                            "expected_version": candidate.json()["version"],
                        },
                    )
                    assert approved.status_code == 200, approved.text

                    async with PowerContextClient(
                        "http://testserver",
                        token="author-token",  # noqa: S106 - local test credential
                        http_client=http,
                        trust_transport_security=True,
                    ) as sdk:
                        revision = await sdk.get_artifact_revision(scope_id, "profile", "profile", 1)
                        assert revision is not None
                        assert revision.revision == 1
                        assert "Shenzhen" in revision.content["content"]

                    assert generator.calls >= 1

    asyncio.run(scenario())
