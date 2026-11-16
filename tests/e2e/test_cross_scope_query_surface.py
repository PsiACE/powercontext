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

"""Cross-Scope query through the public Server, SDK, and MCP entry points.

The runtime aggregation is E's; this checks only what a caller can observe: which origins answered,
that the same local identity in two Scopes stays two results, that an older revision stays
openable through the existing exact read, that losing an origin degrades partial coverage instead
of hiding it, and that the budgets and Family validation behave as the contract states.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from powercontext.builtin.authz import PrincipalRef
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.client import ForbiddenResponseError, PowerContextClient, ServerResponseError
from powercontext.http import (
    CreateAccessBindingRequest,
    CreateArtifactRequest,
    CrossScopeArtifactQueryRequest,
    ReplaceArtifactRequest,
)
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, McpConfig, MetricsConfig, ServerSettings
from tests.e2e.server_storage import prepare_server_database

ADMIN = PrincipalRef(type="service", id="admin")
VIEWER = PrincipalRef(type="user", id="viewer")
DEPLOYMENT_ID = "cross-scope-surface"
# The same local Artifact ID is created in two Scopes on purpose: identity is the origin address.
SHARED_ID = "retry-trace"
QUERY = "retry trace idempotency"

_EXPERIENCE = {
    "situation": "A retry trace shows a duplicate callback on the old channel.",
    "action": "Retry with an idempotency key and drop the stale channel.",
    "outcome": "The callback runs once against the current endpoint.",
    "lesson": "Bound the retry trace to one idempotency key.",
}


def test_cross_scope_query_keeps_origin_identity_coverage_and_exact_reads(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'cross-scope.db'}")
        settings = ServerSettings(
            database=database,
            access=AccessControlConfig(mode="enforced", deployment_id=DEPLOYMENT_ID),
            metrics=MetricsConfig(enabled=False),
            mcp=McpConfig(enabled=True),
        )
        await asyncio.to_thread(prepare_server_database, settings)

        async with open_builtin_access_control(
            database, bootstrap_administrators=(ADMIN,), deployment_id=DEPLOYMENT_ID
        ) as access:
            admin_app = create_server_app(
                settings=settings,
                access_control=access,
                authentication_provider=StaticBearerAuthenticationProvider("admin-token", ADMIN),
            )
            viewer_app = create_server_app(
                settings=settings,
                access_control=access,
                authentication_provider=StaticBearerAuthenticationProvider("viewer-token", VIEWER),
            )
            async with (
                admin_app.router.lifespan_context(admin_app),
                _client(admin_app, "admin-token") as admin,
            ):
                interface = await admin.create_scope(_scope("Interface", "interface"))
                helpdesk = await admin.create_scope(_scope("Help", "help"))
                orders = await admin.create_scope(_scope("Orders", "orders"))
                created: dict[str, str] = {}
                for scope_id in (interface.scope_id, helpdesk.scope_id):
                    artifact = await admin.create_artifact(
                        scope_id,
                        CreateArtifactRequest.model_validate({"family": "experience", "content": _EXPERIENCE}),
                    )
                    created[scope_id] = artifact.artifact_id
                # The query Scope needs its own read grant; each origin is authorized separately.
                for scope_id in (orders.scope_id, interface.scope_id, helpdesk.scope_id):
                    await admin.create_access_binding(
                        CreateAccessBindingRequest.model_validate({
                            "subject": {"type": VIEWER.type, "id": VIEWER.id},
                            "resource": {"type": "scope", "scope_id": scope_id},
                            "role": "scope.viewer",
                            "idempotency_key": f"viewer-{scope_id}",
                        })
                    )

                async with (
                    viewer_app.router.lifespan_context(viewer_app),
                    _client(viewer_app, "viewer-token") as viewer,
                ):
                    origins = [interface.scope_id, helpdesk.scope_id]
                    complete = await viewer.search_cross_scope_artifacts(
                        orders.scope_id, _query("experience", origins, limit=4)
                    )
                    assert complete.coverage.complete is True
                    assert [origin.root for origin in complete.coverage.searched_origins] == origins
                    # Each hit carries its own complete origin address, so two Scopes holding
                    # identically written evidence stay two distinct results, never one merged hit.
                    assert [
                        (
                            hit.origin.scope_id,
                            hit.origin.artifact.family,
                            hit.origin.artifact.artifact_id,
                            hit.origin.artifact.revision,
                        )
                        for hit in complete.hits
                    ] == [
                        (interface.scope_id, "experience", created[interface.scope_id], 1),
                        (helpdesk.scope_id, "experience", created[helpdesk.scope_id], 1),
                    ]
                    assert len({hit.origin.artifact.artifact_id for hit in complete.hits}) == 2
                    # Ranks are per origin, never a cross-origin score comparison.
                    assert [hit.origin_rank for hit in complete.hits] == [1, 1]

                    # The Interface publishes a new head; the earlier hit still names revision one
                    # and opens through the existing exact-revision read, not a new endpoint.
                    await admin.replace_artifact(
                        interface.scope_id,
                        "experience",
                        created[interface.scope_id],
                        ReplaceArtifactRequest.model_validate({
                            "content": {**_EXPERIENCE, "lesson": "Always reuse the idempotency key."}
                        }),
                        expected_etag='"revision:1"',
                    )
                    after = await viewer.search_cross_scope_artifacts(
                        orders.scope_id, _query("experience", origins, limit=4)
                    )
                    assert [hit.origin.artifact.revision for hit in after.hits] == [2, 1]
                    opened = await viewer.get_artifact_revision(
                        interface.scope_id, "experience", created[interface.scope_id], 1
                    )
                    assert opened.content["lesson"] == _EXPERIENCE["lesson"]  # revision one, not the new head

                    # The budget is a ceiling on the Family's effective window, not a work request.
                    top_one = await viewer.search_cross_scope_artifacts(
                        orders.scope_id,
                        _query("experience", [interface.scope_id], limit=1, max_candidates=256, payload_limit=1),
                    )
                    assert top_one.coverage.complete is True
                    assert len(top_one.hits) == 1

                    # The Family's own request is validated as sent, in both coverage modes.
                    for coverage in ("complete", "partial"):
                        with pytest.raises(ServerResponseError) as rejected:
                            await viewer.search_cross_scope_artifacts(
                                orders.scope_id,
                                _query("experience", origins, limit=4, payload_limit=201, coverage=coverage),
                            )
                        assert rejected.value.status_code == 422

                    # Revoking one origin fails a complete query and reports the gap in partial mode.
                    await _revoke(admin, helpdesk.scope_id)
                    with pytest.raises(ForbiddenResponseError):
                        await viewer.search_cross_scope_artifacts(
                            orders.scope_id, _query("experience", origins, limit=4)
                        )
                    partial = await viewer.search_cross_scope_artifacts(
                        orders.scope_id, _query("experience", origins, limit=4, coverage="partial")
                    )
                    assert partial.coverage.complete is False
                    assert partial.coverage.reason == "origin_failure"
                    assert [origin.root for origin in partial.coverage.failed_origins] == [helpdesk.scope_id]
                    assert {hit.origin.scope_id for hit in partial.hits} == {interface.scope_id}

    asyncio.run(scenario())


def test_cross_scope_query_reuses_the_owning_runtime_over_mcp(tmp_path: Path) -> None:
    """MCP calls the same Server operation, so it observes the same origins and coverage."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'cross-scope-mcp.db'}")
        app = create_server_app(
            settings=ServerSettings(
                database=database,
                metrics=MetricsConfig(enabled=False),
                mcp=McpConfig(enabled=True),
            )
        )

        def create_http_client(headers=None, timeout=None, auth=None, **_: object) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers=headers,
                timeout=timeout,
                auth=auth,
                follow_redirects=True,
            )

        transport = StreamableHttpTransport("http://testserver/mcp/", httpx_client_factory=create_http_client)
        async with app.router.lifespan_context(app), Client(transport) as client:
            tools = {tool.name for tool in await client.list_tools()}
            assert "search_cross_scope_artifacts" in tools

            async def create_scope(title: str, key: str) -> str:
                created = await client.call_tool(
                    "create_scope", {"title": title, "summary": f"{title} evidence", "idempotency_key": key}
                )
                return (created.structured_content or {})["scope_id"]

            interface = await create_scope("Interface", "interface")
            orders = await create_scope("Orders", "orders")
            # Artifact create is not an MCP tool; seed it over the same Server's HTTP operation.
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http:
                seeded = await http.post(
                    f"/v1/scopes/{interface}/artifacts",
                    json={"family": "experience", "content": _EXPERIENCE},
                )
                assert seeded.status_code == 201, seeded.text
            result = await client.call_tool(
                "search_cross_scope_artifacts",
                {
                    "scope_id": orders,
                    "family": "experience",
                    "payload": {"query": QUERY},
                    "scopes": [interface],
                },
            )
            payload = result.structured_content or {}
            assert payload["coverage"]["complete"] is True
            assert [hit["origin"]["scope_id"] for hit in payload["hits"]] == [interface]

    asyncio.run(scenario())


def _scope(title: str, key: str):
    from powercontext.http import CreateScopeRequest

    return CreateScopeRequest(title=title, summary=f"{title} evidence", idempotency_key=key)


def _query(
    family: str,
    origins: list[str],
    *,
    limit: int,
    payload_limit: int | None = None,
    coverage: str = "complete",
    max_candidates: int | None = None,
) -> CrossScopeArtifactQueryRequest:
    payload: dict[str, object] = {"query": QUERY}
    if payload_limit is not None:
        payload["limit"] = payload_limit
    document: dict[str, object] = {
        "family": family,
        "payload": payload,
        "scopes": origins,
        "limit": limit,
        "coverage": coverage,
    }
    if max_candidates is not None:
        document["max_candidates"] = max_candidates
    return CrossScopeArtifactQueryRequest.model_validate(document)


async def _revoke(admin, scope_id: str) -> None:
    from powercontext.http import ListAccessBindingsRequest, RevokeAccessBindingRequest

    page = await admin.list_access_bindings(
        ListAccessBindingsRequest.model_validate({
            "management_resource": {"type": "scope", "scope_id": scope_id},
            "subject": {"type": VIEWER.type, "id": VIEWER.id},
        })
    )
    assert page.items
    for binding in page.items:
        await admin.revoke_access_binding(
            RevokeAccessBindingRequest(
                binding_id=binding.binding_id,
                expected_version=binding.version,
                idempotency_key=f"revoke-viewer-{binding.binding_id}",
            )
        )


def _client(app, token: str):
    @asynccontextmanager
    async def open_client():
        async with (
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
            PowerContextClient(
                "http://testserver", token=token, http_client=transport, trust_transport_security=True
            ) as client,
        ):
            yield client

    return open_client()
