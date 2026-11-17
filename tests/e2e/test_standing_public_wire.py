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

"""One standing agreement, from the public rule to its reviewed conclusion.

Everything here is driven through the published contract with enforced access: the
target owner records a versioned rule, each origin's owner accepts its own side with
its own identity, a run pins the material that currently exists, and that same pinned
binding authorizes a foreign first-Profile run through Candidate review to a committed
conclusion the run reports back. Origin identity stays behind current authority
throughout, so a target reader sees the run's facts without learning the origins.
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
from powercontext.builtin.authz.dream import principal_identity
from powercontext.builtin.dream.models import CreateDreamRunRequest as DomainDreamRequest
from powercontext.builtin.evidence.models import ScopedEvidence
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, RuntimeConfig
from powercontext.client import PowerContextClient, ServerResponseError
from powercontext.http import (
    AcceptShareRuleRequest,
    ApproveCandidateRequest,
    CreateShareRunRequest,
    GetCandidateRequest,
    ShareRuleDraft,
    UpdateShareRuleRequest,
)
from powercontext.server.app import ServerApplication, create_app
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.dream_access import DreamAccess
from powercontext.server.middleware import AuthenticationMiddleware
from powercontext.server.standing_access import StandingAccess
from tests.e2e.dream_support import open_dream_runtime, process_pending
from tests.e2e.test_standing_shared_use import (
    ADMIN,
    ALICE,
    BOB,
    CAROL,
    RUNNER,
    _bind,
    _seed,
    _three_scopes,
    run_as,
)

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


def _app(runtime: Any, access: Any, principal: PrincipalRef):
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


def _client(app: Any, headers: dict[str, str]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=headers)


async def _sdk(client: httpx.AsyncClient) -> PowerContextClient:
    return PowerContextClient("http://testserver", http_client=client, trust_transport_security=True)


def test_standing_rule_produces_a_reviewed_conclusion_through_public_apis(tmp_path: Path) -> None:
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
                alice, bob, orders = await _three_scopes(runtime)
                await _seed(access, alice, bob, orders)
                await _bind(access, RUNNER, orders, AccessRole.SCOPE_REVIEWER, "runner-reviewer")
                # The target admin also holds read at ONE origin: that is the mixed view.
                await _bind(access, CAROL, alice, AccessRole.SCOPE_VIEWER, "carol-alice-reader")
                await runtime.profiles.put_policy(orders, generation_enabled=True, expected_version=0)
                foreign = await runtime.sources.for_scope(alice).capture(
                    CaptureSource(source_id="move", content="I moved to Shenzhen permanently.", metadata={})
                )
                # This exact capture is what the pinned run must reference.
                assert foreign.source_ref.source_id == "move"
                await runtime.sources.for_scope(bob).capture(
                    CaptureSource(source_id="old-channel", content="The old channel charged twice.", metadata={})
                )

                draft = ShareRuleDraft.model_validate({
                    "selection": {"mode": "exact", "scope_ids": [alice, bob]},
                    "source_types": ["content"],
                    "terms": {"use": "project-conclusion", "audience": "project-members"},
                })

                # The target owner records the rule and accepts its own side.
                carol_app, carol_headers = _app(runtime, access, CAROL)
                async with _client(carol_app, carol_headers) as http, await _sdk(http) as carol:
                    rule = await carol.create_share_rule(orders, draft)
                    assert rule.version == 1 and rule.status == "active"
                    # Metadata names the declared range and bound terms; no origin body.
                    assert "inputs" not in rule.model_dump(mode="json")
                    accepted_target = await carol.accept_share_rule(
                        orders, rule.rule_id, 1, AcceptShareRuleRequest.model_validate({"side": "target"})
                    )
                    # The recorded actor is the authenticated caller.
                    assert accepted_target.actor == "user:carol"
                    with pytest.raises(ServerResponseError) as stale:
                        await carol.update_share_rule(
                            orders,
                            rule.rule_id,
                            UpdateShareRuleRequest.model_validate({"expected_version": 99, "draft": draft}),
                        )
                    assert stale.value.status_code == 409

                # Each origin's owner accepts its own side, and only that side.
                for principal, origin_scope in ((ALICE, alice), (BOB, bob)):
                    origin_app, origin_headers = _app(runtime, access, principal)
                    async with (
                        _client(origin_app, origin_headers) as origin_http,
                        await _sdk(origin_http) as origin_client,
                    ):
                        acceptance = await origin_client.accept_share_rule(
                            orders,
                            rule.rule_id,
                            1,
                            AcceptShareRuleRequest.model_validate({
                                "side": "origin",
                                "covered_scope_ids": [origin_scope],
                            }),
                        )
                        assert acceptance.actor == f"user:{principal.id}"

                async with _client(carol_app, carol_headers) as http, await _sdk(http) as carol:
                    run = await carol.create_share_run(
                        orders, rule.rule_id, CreateShareRunRequest.model_validate({"notification_key": "n1"})
                    )
                    assert run.rule_version == 1
                    # Two origins pinned; CAROL may read one, so the other is withheld.
                    assert len(run.inputs or ()) == 2, run.inputs
                    assert [i.origin_scope_id for i in run.inputs or () if i.origin_scope_id] == [alice]
                    assert sum(1 for i in run.inputs or () if i.withheld) == 1
                    assert bob.encode() not in json.dumps(run.model_dump(mode="json")).encode()
                    # A repeated notification reuses the recorded run.
                    again = await carol.create_share_run(
                        orders, rule.rule_id, CreateShareRunRequest.model_validate({"notification_key": "n1"})
                    )
                    assert again.run_id == run.run_id
                    listed = await carol.list_share_runs(orders, rule.rule_id)
                    assert any(item.run_id == run.run_id for item in listed.items or ())
                    retried = await carol.retry_share_run(orders, run.run_id)
                    assert retried.rule_version == run.rule_version

                # A refused acceptance leaves no record behind.
                alice_app, alice_headers = _app(runtime, access, ALICE)
                async with _client(alice_app, alice_headers) as alice_http, await _sdk(alice_http) as alice_client:
                    with pytest.raises(ServerResponseError) as widened:
                        await alice_client.accept_share_rule(
                            orders,
                            rule.rule_id,
                            1,
                            AcceptShareRuleRequest.model_validate({
                                "side": "origin",
                                "covered_scope_ids": [alice, bob],
                            }),
                        )
                    # Refused before any row exists; the exact code is B's, so
                    # both fail-closed statuses are acceptable here.
                    assert widened.value.status_code in {403, 422}

                # The pinned binding authorizes the foreign first-Profile run.
                pinned = await runtime.share_rules.get_run(orders, run.run_id)
                selected = tuple(
                    ScopedEvidence(scope_id=item.origin_scope_id, source=item.source_ref)
                    for item in pinned.inputs
                    if item.origin_scope_id and item.source_ref
                )
                assert {item.scope_id for item in selected} == {alice, bob}
                dream = runtime.dream.for_scope(orders, principal_id=principal_identity(RUNNER))
                with run_as(RUNNER):
                    admitted = await dream.create(
                        DomainDreamRequest(
                            operation="initialize_profile",
                            scoped=selected,
                            share_run_id=run.run_id,
                            idempotency_key="public-producer-init",
                        )
                    )
                # No manual D write: the owning Dream path links the run itself.
                await process_pending(runtime)

                runner_app, runner_headers = _app(runtime, access, RUNNER)
                async with (
                    _client(runner_app, {**runner_headers, "X-PowerContext-Dream-Contract": "2"}) as http,
                    await _sdk(http) as sdk,
                ):
                    completed = await sdk.get_dream_run(orders, admitted.run_id)
                    assert completed.outcome == "proposed", completed.error
                    candidate_ref = completed.candidate
                    assert candidate_ref is not None

                # The run belongs to the target Scope, so its readback is a target
                # management read: the owner sees the linked ids on her own record.
                async with _client(carol_app, carol_headers) as http, await _sdk(http) as carol_reader:
                    linked = await carol_reader.get_share_run(orders, run.run_id)
                    assert linked.dream_run_id == admitted.run_id
                    assert linked.candidate_id == candidate_ref.candidate_id
                    # A proposal is not a conclusion.
                    assert linked.outcome == "pending"
                    assert linked.committed is None

                # A real Review approval is what commits the conclusion. The executor is
                # the Candidate's owner, and it also holds scope.review here, so the
                # reviewed decision runs as that authenticated principal.
                await _bind(access, RUNNER, orders, AccessRole.SCOPE_REVIEWER, "runner-review-2")
                app_admin, admin_headers = _app(runtime, access, RUNNER)
                async with (
                    _client(app_admin, {**admin_headers, "X-PowerContext-Dream-Contract": "2"}) as http,
                    await _sdk(http) as admin_sdk,
                ):
                    current = await admin_sdk.get_candidate(
                        GetCandidateRequest(scope_id=orders, candidate_id=candidate_ref.candidate_id)
                    )
                    approved = await admin_sdk.approve_candidate(
                        ApproveCandidateRequest(
                            scope_id=orders,
                            candidate_id=current.candidate_id,
                            expected_version=current.version,
                        )
                    )
                    assert approved.status.value == "approved"
                    committed_ref = approved.result_artifact
                    assert committed_ref is not None

                # The public readback reports that exact conclusion.
                async with _client(carol_app, carol_headers) as http, await _sdk(http) as final_sdk:
                    public_run = await final_sdk.get_share_run(orders, run.run_id)
                    assert public_run.outcome == "committed"
                    assert public_run.dream_run_id == admitted.run_id
                    assert public_run.candidate_id == candidate_ref.candidate_id
                    assert public_run.committed is not None
                    assert public_run.committed.artifact.artifact_id == committed_ref.artifact_id
                    assert public_run.committed.artifact.revision == committed_ref.revision
                    listed_final = await final_sdk.list_share_runs(orders, rule.rule_id)
                    final_row = next(item for item in listed_final.items or () if item.run_id == run.run_id)
                    assert final_row.outcome == "committed"

    asyncio.run(scenario())
