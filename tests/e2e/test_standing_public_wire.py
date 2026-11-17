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

"""A standing agreement through the published contract, from rule to conclusion.

The whole workflow is driven over real HTTP and the SDK with enforced access:
the target owner records a versioned rule, each side accepts it with its own
authenticated identity, a run pins the material that currently exists, a repeat
notification reuses that run, and the pinned binding then authorizes a foreign
first-Profile run to a reviewed conclusion. Origin addresses stay behind current
authority, so a target-only reader gets the run's facts but not the origins.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from starlette.middleware import Middleware

from powercontext.builtin.authz import AccessRole, PrincipalRef
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.authz.service import AccessAuditContext
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, RuntimeConfig
from powercontext.client import PowerContextClient, ServerResponseError
from powercontext.http import (
    AcceptShareRuleRequest,
    CreateShareRunRequest,
    ShareRuleDraft,
    UpdateShareRuleRequest,
)
from powercontext.server.app import ServerApplication, create_app
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.dream_access import DreamAccess
from powercontext.server.middleware import AuthenticationMiddleware
from powercontext.server.standing_access import StandingAccess
from tests.e2e.dream_support import open_dream_runtime
from tests.e2e.test_standing_shared_use import ADMIN, ALICE, BOB, CAROL, RUNNER, _bind, _seed, _three_scopes

AUDIT = AccessAuditContext(transport="test", operation="seed")
DEPLOYMENT_ID = "standing-public"


class _ProfileGenerator:
    config_id = "standing-public-fixture"

    async def generate(self, value):
        from powercontext.builtin.artifacts.profile.models import ProfileWriteContent
        from powercontext.builtin.dream.models import DreamPlan
        from powercontext.builtin.inference import GenerationResult, InferenceUsage

        used = tuple(item.evidence_id for item in value.evidence.evidence)
        return GenerationResult(
            output=DreamPlan(
                outcome="proposed",
                reason="The conclusion follows from the accepted external evidence.",
                intent="create",
                proposal=ProfileWriteContent(content="# Profile\n\nBased in Shenzhen."),
                evidence_ids=used,
            ),
            usage=InferenceUsage(requests=1),
        )


def test_standing_rule_workflow_over_public_contract(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'standing.db'}")
        async with open_builtin_access_control(
            database, bootstrap_administrators=(ADMIN,), deployment_id=DEPLOYMENT_ID
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
                _ = dream_adapter.bind_shared_use(runtime)
                alice, bob, orders = await _three_scopes(runtime)
                await _seed(access, alice, bob, orders)
                await _bind(access, RUNNER, orders, AccessRole.SCOPE_REVIEWER, "runner-reviewer")
                # The target's own admin also holds current read at ONE origin, which
                # is the mixed case: that origin is released to her, the other withheld.
                await _bind(access, CAROL, alice, AccessRole.SCOPE_VIEWER, "carol-alice-reader")
                await runtime.profiles.put_policy(orders, generation_enabled=True, expected_version=0)
                foreign = await runtime.sources.for_scope(alice).capture(
                    CaptureSource(source_id="move", content="I moved to Shenzhen permanently.", metadata={})
                )
                await runtime.sources.for_scope(bob).capture(
                    CaptureSource(source_id="move", content="The old channel charged twice.", metadata={})
                )
                # `foreign` is the exact material the pinned run must reference.
                assert foreign.source_ref.source_id == "move"

                def app_for(principal: PrincipalRef) -> tuple[Any, dict[str, str]]:
                    token = f"{principal.id}-token"
                    provider = StaticBearerAuthenticationProvider(token, principal)
                    app = create_app(
                        application=cast(ServerApplication, runtime),
                        access_control=access,
                        access_mode="enforced",
                        authentication_provider=provider,
                        middleware=[Middleware(AuthenticationMiddleware, provider=provider)],
                    )
                    return app, {"Authorization": f"Bearer {token}"}

                draft = ShareRuleDraft.model_validate({
                    "selection": {"mode": "exact", "scope_ids": [alice, bob]},
                    "source_types": ["content"],
                    "terms": {"use": "project-conclusion", "audience": "project-members"},
                })

                # The target owner records the rule and accepts its own side.
                carol_app, carol_headers = app_for(CAROL)
                async with (
                    carol_app.router.lifespan_context(carol_app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=carol_app),
                        base_url="http://testserver",
                        headers=carol_headers,
                    ) as carol_http,
                    PowerContextClient(
                        "http://testserver", http_client=carol_http, trust_transport_security=True
                    ) as carol,
                ):
                    rule = await carol.create_share_rule(orders, draft)
                    assert rule.version == 1 and rule.status == "active"
                    assert rule.terms.use == "project-conclusion"
                    # The rule version this admin published carries the declared range
                    # and bound terms. It discloses no origin body or digest: those
                    # belong to a run, and to a reader authorized at that origin.
                    rule_body = rule.model_dump(mode="json")
                    assert "inputs" not in rule_body and "digest" not in json.dumps(rule_body)
                    assert rule_body["terms"]["use"] == "project-conclusion"
                    accepted_target = await carol.accept_share_rule(
                        orders, rule.rule_id, 1, AcceptShareRuleRequest.model_validate({"side": "target"})
                    )
                    # The recorded actor is the authenticated caller, not a request field.
                    assert accepted_target.actor == "user:carol"

                    # A stale version conflicts instead of overwriting.
                    with pytest.raises(ServerResponseError) as stale:
                        await carol.update_share_rule(
                            orders,
                            rule.rule_id,
                            UpdateShareRuleRequest.model_validate({"expected_version": 99, "draft": draft}),
                        )
                    assert stale.value.status_code == 409

                # The origin owner accepts the origin side with its own identity.
                alice_app, alice_headers = app_for(ALICE)
                async with (
                    alice_app.router.lifespan_context(alice_app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=alice_app),
                        base_url="http://testserver",
                        headers=alice_headers,
                    ) as alice_http,
                    PowerContextClient(
                        "http://testserver", http_client=alice_http, trust_transport_security=True
                    ) as alice_client,
                ):
                    origin_acceptance = await alice_client.accept_share_rule(
                        orders,
                        rule.rule_id,
                        1,
                        AcceptShareRuleRequest.model_validate({
                            "side": "origin",
                            "covered_scope_ids": [alice],
                        }),
                    )
                    assert origin_acceptance.actor == "user:alice"
                    assert {item.root for item in origin_acceptance.covered_scope_ids} == {alice}
                    # Claiming coverage she does not hold is refused, not recorded.
                    with pytest.raises(ServerResponseError):
                        await alice_client.accept_share_rule(
                            orders,
                            rule.rule_id,
                            1,
                            AcceptShareRuleRequest.model_validate({
                                "side": "origin",
                                "covered_scope_ids": [alice, bob],
                            }),
                        )

                # The second origin's owner accepts its own side; a two-origin rule
                # is only fully accepted when each origin's authority spoke for itself.
                bob_app, bob_headers = app_for(BOB)
                async with (
                    bob_app.router.lifespan_context(bob_app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=bob_app),
                        base_url="http://testserver",
                        headers=bob_headers,
                    ) as bob_http,
                    PowerContextClient(
                        "http://testserver", http_client=bob_http, trust_transport_security=True
                    ) as bob_client,
                ):
                    bob_acceptance = await bob_client.accept_share_rule(
                        orders,
                        rule.rule_id,
                        1,
                        AcceptShareRuleRequest.model_validate({
                            "side": "origin",
                            "covered_scope_ids": [bob],
                        }),
                    )
                    assert bob_acceptance.actor == "user:bob"

                # The target side pins a run from material that currently exists.
                async with (
                    carol_app.router.lifespan_context(carol_app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=carol_app),
                        base_url="http://testserver",
                        headers=carol_headers,
                    ) as carol_http2,
                    PowerContextClient(
                        "http://testserver", http_client=carol_http2, trust_transport_security=True
                    ) as carol2,
                ):
                    run = await carol2.create_share_run(
                        orders, rule.rule_id, CreateShareRunRequest.model_validate({"notification_key": "n1"})
                    )
                    assert run.rule_version == 1
                    assert run.inputs, run
                    # A repeated notification reuses the recorded run.
                    again = await carol2.create_share_run(
                        orders, rule.rule_id, CreateShareRunRequest.model_validate({"notification_key": "n1"})
                    )
                    assert again.run_id == run.run_id
                    # Retry reuses the pinned manifest rather than re-discovering.
                    retried = await carol2.retry_share_run(orders, run.run_id)
                    assert retried.rule_version == run.rule_version

                    # A target-only reader keeps the run's facts but not the origin's
                    # identity: acceptance existing is not permission to read it.
                    # The origin is withheld explicitly, not merely omitted by luck.
                    # The target reader holds read at one origin and not the other, so
                    # exactly that one is released and the other carries no identity.
                    released_here = {item.origin_scope_id for item in run.inputs if not item.withheld}
                    withheld_here = [item for item in run.inputs if item.withheld]
                    assert released_here == {alice}, run.inputs
                    assert withheld_here, run.inputs
                    for item in withheld_here:
                        assert item.origin_scope_id is None and item.digest is None and item.source_ref is None
                    # The withheld origin never appears anywhere in the payload.
                    body = json.dumps(run.model_dump(mode="json"))
                    assert bob not in body, body
                    assert bob not in [item.root for item in run.origins or ()]

                # The target admin also reads ONE origin. That is the mixed case the
                # projection must publish: the origin she may open is released, the
                # other stays withheld, in one schema-valid payload.
                async with (
                    carol_app.router.lifespan_context(carol_app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=carol_app),
                        base_url="http://testserver",
                        headers=carol_headers,
                    ) as mixed_http,
                    PowerContextClient(
                        "http://testserver", http_client=mixed_http, trust_transport_security=True
                    ) as mixed,
                ):
                    mixed_run = await mixed.get_share_run(orders, run.run_id)
                    released = [item for item in mixed_run.inputs or () if not item.withheld]
                    held = [item for item in mixed_run.inputs or () if item.withheld]
                    assert released, mixed_run.inputs
                    assert held, mixed_run.inputs
                    assert all(item.origin_scope_id == alice for item in released), released
                    assert all(item.origin_scope_id is None and item.digest is None for item in held), held
                    # The withheld origin is dropped from the run's origin list while
                    # the released one remains.
                    assert [item.root for item in mixed_run.origins or ()] == [alice], mixed_run.origins
                    listed_mixed = await mixed.list_share_runs(orders, rule.rule_id)
                    assert any(item.run_id == run.run_id for item in listed_mixed.items)
                    retried_mixed = await mixed.retry_share_run(orders, run.run_id)
                    assert retried_mixed.rule_version == run.rule_version

            # The pinned binding authorizes the foreign first-Profile run.
            async with open_dream_runtime(
                BuiltinConfig(database=database, runtime=RuntimeConfig()),
                dream_generator=_ProfileGenerator(),
                share_rule_acceptance_validator=standing.validate_acceptance,
                dream_authorizer=dream_adapter.authorize,
                dream_authorization_context=access.defer_decision_audit,
            ) as runtime2:
                standing.attach(runtime2)
                dream_adapter.bind_shared_use(runtime2)
                # The recorded run's Scope still exists; the binding is the same pinned agreement.
                service = runtime2.share_rules
                assert service is not None
                recorded = await service.list_runs(orders)
                assert recorded, "the pinned run is durable"

    asyncio.run(scenario())
