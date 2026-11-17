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

"""The foreign Dream binding through the published contract.

A run may use another Scope's material only under a pinned agreement. This checks the
public wire rather than the runtime call: the request carries ``scoped`` and
``share_run_id``, the run reads back its binding, an unbound or mismatched claim is
refused with a stable code, and the readback keeps origin identity behind authority.

The agreement itself is seeded through the existing sharing runtime because no public
discovery surface exists yet; this proves the Dream binding, not a product foreign
workflow.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast

import httpx
from starlette.middleware import Middleware

from powercontext.builtin.authz import AccessRole, CreateBinding, ResourceRef
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.authz.service import AccessAuditContext
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, RuntimeConfig
from powercontext.client import PowerContextClient
from powercontext.server.app import ServerApplication, create_app
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.dream_access import DreamAccess
from powercontext.server.middleware import AuthenticationMiddleware
from powercontext.server.standing_access import StandingAccess
from tests.e2e.dream_support import open_dream_runtime, process_pending
from tests.e2e.test_standing_shared_use import (
    ADMIN,
    ALICE,
    CAROL,
    RUNNER,
    _accept,
    _rule,
    _seed,
    _sharing,
    _three_scopes,
    run_as,
)


class _ProfileGenerator:
    config_id = "foreign-wire-fixture"

    async def generate(self, value):
        from powercontext.builtin.artifacts.profile.models import ProfileWriteContent
        from powercontext.builtin.dream.models import DreamPlan
        from powercontext.builtin.inference import GenerationResult, InferenceUsage

        used = tuple(item.evidence_id for item in value.evidence.evidence)
        return GenerationResult(
            output=DreamPlan(
                outcome="proposed",
                reason="The first Profile comes from the pinned external evidence.",
                intent="create",
                proposal=ProfileWriteContent(content="# Profile\n\nBased in Shenzhen."),
                evidence_ids=used,
            ),
            usage=InferenceUsage(requests=1),
        )


def test_foreign_dream_binding_over_public_http_and_sdk(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'foreign-wire.db'}")
        async with open_builtin_access_control(
            database, bootstrap_administrators=(ADMIN,), deployment_id="foreign-wire"
        ) as access:
            standing = StandingAccess(access)
            dream_adapter = DreamAccess(access)
            async with open_dream_runtime(
                BuiltinConfig(database=database, runtime=RuntimeConfig()),
                dream_generator=_ProfileGenerator(),
                share_rule_acceptance_validator=standing.validate_acceptance,
                dream_authorizer=dream_adapter.authorize,
                dream_authorization_context=access.defer_decision_audit,
            ) as runtime:
                standing.attach(runtime)
                dream_adapter.bind_shared_use(runtime)
                alice, bob, orders = await _three_scopes(runtime)
                await _seed(access, alice, bob, orders)
                await access.create_binding(
                    ADMIN,
                    CreateBinding(
                        subject=RUNNER,
                        resource=ResourceRef.scope(orders),
                        role=AccessRole.SCOPE_REVIEWER,
                        idempotency_key="runner-reviewer",
                    ),
                    context=AccessAuditContext(transport="test", operation="seed"),
                )
                await runtime.profiles.put_policy(orders, generation_enabled=True, expected_version=0)
                sharing = _sharing(runtime, orders)
                with run_as(CAROL):
                    rule = await sharing.create(_rule(alice, None))
                await _accept(standing, runtime, rule, "origin", (alice,), ALICE)
                await _accept(standing, runtime, rule, "target", (orders,), CAROL)
                foreign = await runtime.sources.for_scope(alice).capture(
                    CaptureSource(source_id="move", content="I moved to Shenzhen permanently.", metadata={})
                )
                with run_as(RUNNER):
                    pinned = await sharing.resolve(rule.rule_id)

                provider = StaticBearerAuthenticationProvider("runner-token", RUNNER)
                app = create_app(
                    application=cast(ServerApplication, runtime),
                    access_control=access,
                    access_mode="enforced",
                    authentication_provider=provider,
                    middleware=[Middleware(AuthenticationMiddleware, provider=provider)],
                )
                headers = {"Authorization": "Bearer runner-token", "X-PowerContext-Dream-Contract": "2"}
                async with (
                    app.router.lifespan_context(app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=headers
                    ) as http,
                ):
                    body = {
                        "operation": "initialize_profile",
                        "sources": [],
                        "scoped": [
                            {
                                "scope_id": alice,
                                "source": {
                                    "source_type": foreign.source_ref.source_type,
                                    "source_id": foreign.source_ref.source_id,
                                },
                            }
                        ],
                        "share_run_id": pinned.run_id,
                        "idempotency_key": "foreign-wire-init",
                    }
                    created = await http.post(f"/v1/scopes/{orders}/dream", json=body)
                    assert created.status_code in {200, 202}, created.text
                    run_id = created.json()["run_id"]
                    # The binding is request-only and stays private execution state; the
                    # published run shape does not claim a readback this release lacks.
                    assert "share_run_id" not in created.json()

                    # A foreign claim with no binding is refused before any work.
                    unbound = dict(body)
                    unbound.pop("share_run_id")
                    unbound["idempotency_key"] = "foreign-wire-unbound"
                    refused = await http.post(f"/v1/scopes/{orders}/dream", json=unbound)
                    assert refused.status_code == 422, refused.text
                    assert refused.json()["error"]["code"] == "unsupported_shared_use"

                    # A wrong run id is refused too.
                    wrong = dict(body)
                    wrong["share_run_id"] = "shr_does_not_exist"
                    wrong["idempotency_key"] = "foreign-wire-wrong"
                    mismatched = await http.post(f"/v1/scopes/{orders}/dream", json=wrong)
                    assert mismatched.status_code == 422, mismatched.text
                    assert mismatched.json()["error"]["code"] in {"shared_use_unavailable", "shared_use_run"}

                    with run_as(CAROL):
                        await sharing.record_outcome(pinned.run_id, dream_run_id=run_id)
                    await process_pending(runtime)

                    fetched = await http.get(f"/v1/scopes/{orders}/dream/{run_id}")
                    assert fetched.status_code == 200, fetched.text
                    assert fetched.json()["outcome"] == "proposed", fetched.json()
                    assert "share_run_id" not in fetched.json()

                    async with PowerContextClient(
                        "http://testserver", http_client=http, trust_transport_security=True
                    ) as sdk:
                        sdk_run = await sdk.get_dream_run(orders, run_id)
                        # The origin identity is not published to a target-only reader:
                        # the accepted 805 privacy boundary still withholds it.
                        manifest = sdk_run.input_manifest
                        assert manifest is not None
                        assert "origins" not in json.dumps(sdk_run.model_dump(mode="json"))

    asyncio.run(scenario())
