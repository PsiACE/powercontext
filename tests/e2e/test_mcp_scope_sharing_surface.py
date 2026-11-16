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

"""An Agent can query and resolve an authorized range through the same Server.

The workflow is the user-visible one from the Scope sharing proposal: read a conclusion in one
Scope, find supporting material in another that a Scope explicitly selects, and open the exact
revision. It runs over the real MCP transport against the real HTTP application and SQLite, so it
survives a rewrite of either the projection or the domain implementation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from powercontext.builtin.authz import (
    AccessAuditContext,
    AccessRole,
    CreateBinding,
    PrincipalRef,
    ResourceRef,
)
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, McpConfig, MetricsConfig, ServerSettings
from tests.e2e.server_storage import prepare_server_database

ADMIN = PrincipalRef(type="service", id="admin")
VIEWER = PrincipalRef(type="user", id="alice")
DEPLOYMENT_ID = "mcp-scope-sharing"


def test_mcp_queries_one_scope_and_opens_the_exact_revision(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'sharing.db'}"),
            mcp=McpConfig(enabled=True),
        )
    )

    def create_http_client(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
        **_: object,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers=headers,
            timeout=timeout,
            auth=auth,
            follow_redirects=True,
        )

    async def scenario() -> None:
        transport = StreamableHttpTransport("http://testserver/mcp/", httpx_client_factory=create_http_client)
        async with app.router.lifespan_context(app), Client(transport) as client:
            tools = {tool.name for tool in await client.list_tools()}
            assert {"search_artifacts", "resolve_scope_selection", "get_artifact_revision"} <= tools

            async def create_scope(title: str, key: str, **relationships: object) -> str:
                created = await client.call_tool(
                    "create_scope",
                    {"title": title, "summary": f"{title} evidence", "idempotency_key": key, **relationships},
                )
                return (created.structured_content or {})["scope_id"]

            orders = await create_scope("Orders", "orders")
            interface = await create_scope("Interface", "interface", parent_scope_id=orders)
            runbook = await create_scope("Runbook", "runbook")

            await client.call_tool(
                "remember_memory",
                {
                    "scope_id": runbook,
                    "kind": "fact",
                    "text": "On the old channel, check transaction status before retrying.",
                },
            )

            # A query is bounded to the Scope it names; structural children are not searched.
            orders_hits = await client.call_tool(
                "search_artifacts", {"scope_id": orders, "family": "atomic-memory", "query": "transaction"}
            )
            assert (orders_hits.structured_content or {})["results"] == []

            runbook_hits = await client.call_tool(
                "search_artifacts", {"scope_id": runbook, "family": "atomic-memory", "query": "transaction"}
            )
            results = (runbook_hits.structured_content or {})["results"]
            assert len(results) == 1
            hit = results[0]
            assert (hit["family"], hit["revision"]) == ("atomic-memory", 1)

            # A hit opens through the exact-revision read, not a re-search.
            opened = await client.call_tool(
                "get_artifact_revision",
                {"scope_id": runbook, "family": hit["family"], "artifact_id": hit["artifact_id"], "revision": 1},
            )
            content = (opened.structured_content or {})["content"]
            assert "transaction status" in content["text"]

            # Resolution distinguishes the structural subtree from the external selection.
            subtree = await client.call_tool(
                "resolve_scope_selection", {"selection": {"mode": "subtree", "root_scope_id": orders}}
            )
            assert {item["scope_id"] for item in (subtree.structured_content or {})["items"]} == {orders, interface}

            exact = await client.call_tool(
                "resolve_scope_selection", {"selection": {"mode": "exact", "scope_ids": [orders, runbook]}}
            )
            assert {item["scope_id"] for item in (exact.structured_content or {})["items"]} == {orders, runbook}

    asyncio.run(scenario())


def test_mcp_artifact_query_keeps_each_scope_authorization_independent(tmp_path: Path) -> None:
    """Finding a Scope and querying it are separate decisions.

    A principal authorized on one Scope queries that Scope through MCP while the same call is
    refused for another, so the newly available tool exposes navigation without widening access.
    """

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'sharing-access.db'}")
        settings = ServerSettings(
            database=database,
            access=AccessControlConfig(mode="enforced", deployment_id=DEPLOYMENT_ID),
            metrics=MetricsConfig(enabled=False),
            mcp=McpConfig(enabled=True),
        )
        await asyncio.to_thread(prepare_server_database, settings)
        async with open_builtin_access_control(
            database,
            bootstrap_administrators=(ADMIN,),
            deployment_id=DEPLOYMENT_ID,
        ) as access_control:
            seed_app = create_server_app(
                settings=settings,
                access_control=access_control,
                authentication_provider=StaticBearerAuthenticationProvider("admin-token", ADMIN),
            )
            async with (
                seed_app.router.lifespan_context(seed_app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=seed_app),
                    base_url="http://testserver",
                    headers={"Authorization": "Bearer admin-token"},
                ) as seed,
            ):

                async def seed_scope(title: str) -> str:
                    created = await seed.post(
                        "/v1/scopes",
                        json={"title": title, "summary": f"{title} evidence", "idempotency_key": title.lower()},
                    )
                    assert created.status_code == 201, created.text
                    scope_id = created.json()["scope_id"]
                    remembered = await seed.post(
                        "/v1/memory/remember",
                        json={"scope_id": scope_id, "kind": "fact", "text": f"{title} note."},
                    )
                    assert remembered.status_code == 200, remembered.text
                    return scope_id

                readable_id = await seed_scope("Readable")
                hidden_id = await seed_scope("Hidden")

            await access_control.create_binding(
                ADMIN,
                CreateBinding(
                    subject=VIEWER,
                    resource=ResourceRef.scope(readable_id),
                    role=AccessRole.SCOPE_VIEWER,
                    idempotency_key="alice-readable-viewer",
                ),
                context=AccessAuditContext(transport="test", operation="seed"),
            )
            app = create_server_app(
                settings=settings,
                access_control=access_control,
                authentication_provider=StaticBearerAuthenticationProvider("alice-token", VIEWER),
            )

            def create_http_client(
                headers: dict[str, str] | None = None,
                timeout: httpx.Timeout | None = None,
                auth: httpx.Auth | None = None,
                **_: object,
            ) -> httpx.AsyncClient:
                return httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                    headers={"Authorization": "Bearer alice-token", **(headers or {})},
                    timeout=timeout,
                    auth=auth,
                    follow_redirects=True,
                )

            transport = StreamableHttpTransport("http://testserver/mcp/", httpx_client_factory=create_http_client)
            async with app.router.lifespan_context(app), Client(transport) as client:
                found = await client.call_tool(
                    "search_artifacts",
                    {"scope_id": readable_id, "family": "atomic-memory", "query": "Readable"},
                )
                assert len((found.structured_content or {})["results"]) == 1

                denied = await client.call_tool(
                    "search_artifacts",
                    {"scope_id": hidden_id, "family": "atomic-memory", "query": "Hidden"},
                    raise_on_error=False,
                )
                assert denied.is_error
                assert "403" in denied.content[0].text

    asyncio.run(scenario())
