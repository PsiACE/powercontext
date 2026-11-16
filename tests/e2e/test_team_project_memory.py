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

"""Team and project memory as ordinary Scopes, through the enforced Server.

One story, in the order a team actually performs it: two contributors' work Scopes feed an official
project Profile, a designated member reads the conclusion and the personal Profile stays separate, a
maintainer corrects the conclusion while a stale proposal is refused, and a later contributor record
produces a new conclusion. The project Skill is a member-visible capability that does not depend on
anyone's personal Profile.

Everything here goes through the public HTTP surface with Access enforced, so a passing test states
that the Server, the Runtime, persistence, and the authorization boundary agreed. The Profile
generator is a local deterministic fixture: it proves the pipeline carries evidence and commits the
reviewed outcome, not that a real model writes good prose.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from powercontext.builtin.authz import PrincipalRef
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, McpConfig, ServerSettings
from tests.e2e.server_storage import prepare_server_database

ADMIN = PrincipalRef(type="service", id="admin")
ALICE = PrincipalRef(type="user", id="alice")
BOB = PrincipalRef(type="user", id="bob")
MAINTAINER = PrincipalRef(type="user", id="maintainer")
READER = PrincipalRef(type="user", id="reader")
OUTSIDER = PrincipalRef(type="user", id="outsider")
DEPLOYMENT_ID = "team-project-memory"

PROJECT_KEY = {"integration": "project", "kind": "project", "external_id": "orders"}
PROFILE_PATH = "artifacts/profile/profile"


class _DeterministicProfileGenerator:
    """A local stand-in for the configured model boundary.

    It reads the Source window the Server hands it and returns Markdown derived from that text, so the
    test asserts that admitted evidence reaches generation and that review commits the outcome. It is
    not evidence that a real model writes this conclusion.
    """

    config_id = "team-project-memory-fixture"
    first = "# Orders project\n\n- Retry with an idempotency key on the new channel.\n"
    corrected = (
        "# Orders project\n\n- Retry with an idempotency key on the new channel.\n"
        "- Check transaction status before retrying on the old channel.\n"
    )

    async def generate(self, value):
        assert value.sources, "the Server must supply the admitted Source window"
        old_channel = any("old channel" in text for text in value.sources)
        return self.corrected if old_channel else self.first


class _Harness:
    """One database, one Access store, and a client per authenticated principal."""

    def __init__(self, database: SQLiteConfig, access) -> None:
        self._database = database
        self._access = access

    @asynccontextmanager
    async def client(self, principal: PrincipalRef) -> AsyncIterator[httpx.AsyncClient]:
        app = create_server_app(
            settings=ServerSettings(
                database=self._database,
                access=AccessControlConfig(mode="enforced", deployment_id=DEPLOYMENT_ID),
                mcp=McpConfig(enabled=False),
            ),
            access_control=self._access,
            authentication_provider=StaticBearerAuthenticationProvider("test-token", principal),
            profile_generator=_DeterministicProfileGenerator(),
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Authorization": "Bearer test-token"},
            ) as client,
        ):
            yield client


async def _grant(client: httpx.AsyncClient, scope_id: str, principal: PrincipalRef, role: str) -> None:
    response = await client.post(
        "/v1/access/bindings/create",
        json={
            "subject": {"type": principal.type, "id": principal.id},
            "resource": {"type": "scope", "scope_id": scope_id},
            "role": role,
            "idempotency_key": f"{principal.id}-{role}-{scope_id}",
        },
    )
    assert response.status_code == 201, response.text


async def _create_scope(client: httpx.AsyncClient, title: str, key: str, **extra) -> str:
    response = await client.post(
        "/v1/scopes",
        json={"title": title, "summary": f"{title} memory boundary", "idempotency_key": key, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()["scope_id"]


async def _enable_reviewed_profile(client: httpx.AsyncClient, scope_id: str) -> None:
    response = await client.put(
        f"/v1/scopes/{scope_id}/profile-policy",
        json={"generation_enabled": True, "activation_mode": "review_required", "expected_version": 0},
    )
    assert response.status_code == 200, response.text


def test_team_project_memory_runs_on_ordinary_scopes(tmp_path: Path) -> None:
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'team-project.db'}")

    async def scenario() -> None:
        await asyncio.to_thread(prepare_server_database, ServerSettings(database=database))
        async with open_builtin_access_control(
            database, bootstrap_administrators=(ADMIN,), deployment_id=DEPLOYMENT_ID
        ) as access:
            harness = _Harness(database, access)
            async with harness.client(ADMIN) as admin:
                project_id = await _create_scope(
                    admin,
                    "Orders project",
                    "orders-project",
                    external_references=[{"kind": "project", "value": "orders"}],
                )
                alice_work_id = await _create_scope(
                    admin,
                    "Alice work",
                    "alice-work",
                    external_references=[{"kind": "user", "value": "alice"}],
                )
                bob_work_id = await _create_scope(admin, "Bob work", "bob-work")

                # A project is an ordinary Scope plus an application mapping; no team entity exists.
                binding = await admin.put("/v1/scope-bindings", json={"key": PROJECT_KEY, "scope_id": project_id})
                assert binding.status_code == 200, binding.text
                resolved = await admin.post(
                    "/v1/scope-bindings/resolve", json={"binding_keys": [PROJECT_KEY], "allow_default": False}
                )
                assert resolved.status_code == 200, resolved.text
                assert resolved.json()["scope_id"] == project_id

                # Members see what their grants allow; the outsider has none.
                for principal in (ALICE, BOB, MAINTAINER, READER):
                    await _grant(admin, project_id, principal, "scope.contributor")
                await _grant(admin, project_id, MAINTAINER, "scope.reviewer")
                # Configuring the Profile policy is Scope administration, distinct from deciding a Candidate.
                await _grant(admin, project_id, MAINTAINER, "scope.admin")
                for principal in (ALICE, BOB):
                    await _grant(
                        admin, alice_work_id if principal is ALICE else bob_work_id, principal, "scope.contributor"
                    )
                # The reader may look at a contributor's work Scope; it grants no project conclusion.
                await _grant(admin, alice_work_id, READER, "scope.viewer")

                await _enable_reviewed_profile(admin, project_id)

            # Two contributors each supply their own work record in their own work Scope.
            async with harness.client(ALICE) as alice, harness.client(BOB) as bob:
                for client, work_id, record in (
                    (
                        alice,
                        alice_work_id,
                        "A callback retried on the new channel succeeded with an idempotency key.",
                    ),
                    (bob, bob_work_id, "The same retry on the old channel charged the customer twice."),
                ):
                    captured = await client.post(f"/v1/scopes/{work_id}/sources", json={"content": record})
                    assert captured.status_code == 201, captured.text
                    readable = await client.get(
                        f"/v1/scopes/{work_id}/sources/{captured.json()['source_type']}/{captured.json()['source_id']}"
                    )
                    assert readable.status_code == 200, readable.text
                    assert readable.json()["content_digest"] == captured.json()["content_digest"]

                # Each contributor can also contribute the observation to the official project Scope.
                contributed = await alice.post(
                    f"/v1/scopes/{project_id}/sources",
                    json={"content": "A callback retried on the new channel succeeded with an idempotency key."},
                )
                assert contributed.status_code == 201, contributed.text

            # The maintainer produces and approves the official conclusion.
            async with harness.client(MAINTAINER) as maintainer:
                pending = await maintainer.post("/v1/profile/flush", json={"scope_id": project_id})
                assert pending.status_code == 200, pending.text
                proposal = pending.json()
                assert proposal["status"] == "review_pending"
                assert proposal["candidate_id"] is not None

                # Nothing official exists until a reviewer decides.
                uncommitted = await maintainer.get(f"/v1/scopes/{project_id}/{PROFILE_PATH}")
                assert uncommitted.status_code == 404

                approved = await maintainer.post(
                    "/v1/candidates/approve",
                    json={
                        "scope_id": project_id,
                        "candidate_id": proposal["candidate_id"],
                        "expected_version": 1,
                    },
                )
                assert approved.status_code == 200, approved.text
                assert approved.json()["status"] == "approved"
                official = approved.json()["result_artifact"]
                assert official["family"] == "profile" and official["revision"] == 1

            # A designated member reads the conclusion through Prepare, with its exact address.
            async with harness.client(READER) as reader:
                ready = await reader.post(
                    "/v1/context/prepare",
                    json={
                        "scope_id": project_id,
                        "query": "can we retry a callback",
                        "assembly": {"sections": [{"family": "profile", "limit": 1}]},
                        "include_receipt": True,
                    },
                )
                assert ready.status_code == 200, ready.text
                context = ready.json()
                assert context["status"] == "ready"
                assert "idempotency key" in context["content"]
                assert f'Scope: "{project_id}"' in context["content"]
                assert 'family="profile", id="profile", revision=1' in context["content"]
                cited = {
                    (item["artifact_address"]["scope_id"], item["artifact_address"]["artifact"]["revision"])
                    for item in context["receipt"]["selected"]
                }
                assert cited == {(project_id, 1)}

                exact = await reader.get(f"/v1/scopes/{project_id}/{PROFILE_PATH}/revisions/1")
                assert exact.status_code == 200, exact.text
                assert exact.json()["content"]["content"] == _DeterministicProfileGenerator.first

            # An authenticated member without a grant reads nothing, so authority is real.
            async with harness.client(OUTSIDER) as outsider:
                denied = await outsider.get(f"/v1/scopes/{project_id}/{PROFILE_PATH}")
                assert denied.status_code == 403, denied.text

            # A later fact arrives and the maintainer corrects the official conclusion.
            async with harness.client(MAINTAINER) as maintainer:
                followup = await maintainer.post(
                    f"/v1/scopes/{project_id}/sources",
                    json={"content": "On the old channel, check transaction status before retrying."},
                )
                assert followup.status_code == 201, followup.text

                head = await maintainer.get(f"/v1/scopes/{project_id}/{PROFILE_PATH}")
                stale_etag = head.headers["ETag"]
                assert head.json()["revision"] == 1

                reassessed = await maintainer.post("/v1/profile/flush", json={"scope_id": project_id})
                assert reassessed.status_code == 200, reassessed.text
                assert reassessed.json()["candidate_id"] is not None

                # A second, independent correction lands first, so the pending proposal is stale.
                corrected = await maintainer.put(
                    f"/v1/scopes/{project_id}/{PROFILE_PATH}",
                    headers={"If-Match": stale_etag},
                    json={
                        "content": {"content": "# Orders project\n\n- Check transaction status on the old channel.\n"}
                    },
                )
                assert corrected.status_code == 200, corrected.text
                assert corrected.json()["revision"] == 2

                conflict = await maintainer.post(
                    "/v1/candidates/approve",
                    json={
                        "scope_id": project_id,
                        "candidate_id": reassessed.json()["candidate_id"],
                        "expected_version": 1,
                    },
                )
                assert conflict.status_code == 409, conflict.text
                # A stale proposal never overwrites the revision a maintainer just committed.
                assert (await maintainer.get(f"/v1/scopes/{project_id}/{PROFILE_PATH}")).json()["revision"] == 2

                # The stale proposal is still decidable: it can be rejected, not approved.
                rejected = await maintainer.post(
                    "/v1/candidates/reject",
                    json={
                        "scope_id": project_id,
                        "candidate_id": reassessed.json()["candidate_id"],
                        "expected_version": 1,
                        "reason": "A maintainer corrected the conclusion first; reassess against the new baseline.",
                    },
                )
                assert rejected.status_code == 200, rejected.text
                assert rejected.json()["status"] == "rejected"

                # The official revision keeps its history and is not silently rewritten.
                history = await maintainer.get(f"/v1/scopes/{project_id}/{PROFILE_PATH}/revisions")
                assert history.status_code == 200, history.text
                assert {item["revision"] for item in history.json()["items"]} == {2, 1}
                current = await maintainer.get(f"/v1/scopes/{project_id}/{PROFILE_PATH}")
                assert current.json()["revision"] == 2

            # The project Skill is a team capability, independent of any personal Profile.
            async with harness.client(MAINTAINER) as maintainer:
                skill = await maintainer.post(
                    f"/v1/scopes/{project_id}/artifacts",
                    json={
                        "family": "skill",
                        "content": {
                            "name": "incident-retry-triage",
                            "description": "Decide whether a failed callback may be retried.",
                            "instructions": "Check the channel. Retry with an idempotency key only on the new channel.",
                            "validation": ["Confirm the channel before retrying."],
                        },
                    },
                )
                assert skill.status_code == 201, skill.text
                skill_ref = skill.json()

            async with harness.client(READER) as reader:
                loaded = await reader.get(
                    f"/v1/scopes/{project_id}/artifacts/{skill_ref['family']}/{skill_ref['artifact_id']}"
                )
                assert loaded.status_code == 200, loaded.text
                assert loaded.json()["content"]["name"] == "incident-retry-triage"

                # The personal Profile of a contributor is a separate Scope with its own conclusion.
                personal = await reader.get(f"/v1/scopes/{alice_work_id}/{PROFILE_PATH}")
                assert personal.status_code == 404, personal.text
                personal_prepare = await reader.post(
                    "/v1/context/prepare",
                    json={
                        "scope_id": alice_work_id,
                        "query": "any profile",
                        "assembly": {"sections": [{"family": "profile", "limit": 1}]},
                    },
                )
                assert personal_prepare.status_code == 200, personal_prepare.text
                assert personal_prepare.json()["status"] == "empty"

    asyncio.run(scenario())
