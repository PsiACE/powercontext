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

"""Scope archive and confirmed deletion through the public Server.

The domain owns the lifecycle rules; this checks the published wire: archive
stops writes in the named Scope while children keep their own state, a deletion
preview is bounded and carries a digest, confirmation refuses a stale digest and
a stale set, cutoff is immediate, and an ordinary reader is denied management.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from starlette.middleware import Middleware

from powercontext.builtin.authz import (
    AccessAuditContext,
    AccessRole,
    CreateBinding,
    PrincipalRef,
    ResourceRef,
)
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig
from powercontext.builtin.scope import ScopeDraft
from powercontext.client import PowerContextClient, ServerResponseError
from powercontext.http import ConfirmScopeDeletionRequest, ScopeDeletionPreviewRequest
from powercontext.server.app import ServerApplication, create_app
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.middleware import AuthenticationMiddleware
from tests.e2e.dream_support import open_dream_runtime

ADMIN = PrincipalRef(type="service", id="lifecycle-admin")
MANAGER = PrincipalRef(type="user", id="lifecycle-manager")
READER = PrincipalRef(type="user", id="lifecycle-reader")
OBSERVER = PrincipalRef(type="user", id="lifecycle-observer")
AUDIT = AccessAuditContext(transport="http", operation="lifecycle", request_id="lifecycle")
DEPLOYMENT_ID = "lifecycle-public"


def test_scope_archive_and_confirmed_deletion_over_public_contract(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}")
        async with (
            open_builtin_access_control(
                database, bootstrap_administrators=(ADMIN,), deployment_id=DEPLOYMENT_ID
            ) as access,
            open_dream_runtime(BuiltinConfig(database=database, runtime=RuntimeConfig())) as runtime,
        ):
            assert runtime.scopes is not None
            root = await runtime.scopes.create(ScopeDraft(title="Orders", summary="r", idempotency_key="r"))
            child = await runtime.scopes.create(
                ScopeDraft(title="Interface", summary="c", parent_scope_id=root.scope_id, idempotency_key="c")
            )
            for subject, role, key in (
                (MANAGER, AccessRole.SCOPE_ADMIN, "manager-admin"),
                (MANAGER, AccessRole.SCOPE_CONTRIBUTOR, "manager-contrib"),
                (READER, AccessRole.SCOPE_VIEWER, "reader-viewer"),
            ):
                for scope_id in (root.scope_id, child.scope_id):
                    await access.create_binding(
                        ADMIN,
                        CreateBinding(
                            subject=subject,
                            resource=ResourceRef.scope(scope_id),
                            role=role,
                            idempotency_key=f"{key}-{scope_id}",
                        ),
                        context=AUDIT,
                    )

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

            manager_app, manager_headers = app_for(MANAGER)
            async with (
                manager_app.router.lifespan_context(manager_app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=manager_app),
                    base_url="http://testserver",
                    headers=manager_headers,
                ) as http,
            ):
                async with PowerContextClient(
                    "http://testserver", http_client=http, trust_transport_security=True
                ) as sdk:
                    # Archive is version conditional and stops this Scope.
                    from powercontext.http import ScopeLifecycleTransitionRequest

                    archived = await sdk.archive_scope(
                        child.scope_id,
                        ScopeLifecycleTransitionRequest(expected_version=child.version),
                    )
                    assert archived.lifecycle_state == "archived"
                    # The parent keeps its own state.
                    parent_now = await sdk.get_scope(root.scope_id)
                    assert parent_now.lifecycle_state == "writable"

                    # A stale version conflicts instead of overwriting.
                    with pytest.raises(ServerResponseError) as stale:
                        await sdk.archive_scope(
                            child.scope_id,
                            ScopeLifecycleTransitionRequest(expected_version=child.version),
                        )
                    assert stale.value.status_code == 409

                # A preview is bounded and carries a stable digest.

                preview_response = await http.post(
                    "/v1/scope-deletions/preview",
                    json={"selection": {"mode": "subtree", "root_scope_id": root.scope_id}},
                )
                assert preview_response.status_code == 200, preview_response.text
                preview = preview_response.json()
                assert {member["scope_id"] for member in preview["members"]} == {root.scope_id, child.scope_id}
                digest = preview["membership_digest"]

                # The budget is deployment-owned, so a per-request bound is refused by the contract
                # rather than silently ignored.
                rejected_limit = await http.post(
                    "/v1/scope-deletions/preview",
                    json={
                        "selection": {"mode": "subtree", "root_scope_id": root.scope_id},
                        "limits": {"max_nodes": 1},
                    },
                )
                assert rejected_limit.status_code == 422, rejected_limit.text

                # A stale digest is refused before any cutoff.
                stale_digest = await http.post(
                    "/v1/scope-deletions",
                    json={
                        "selection": {"mode": "subtree", "root_scope_id": root.scope_id},
                        "confirmed_digest": "0" * 64,
                    },
                )
                assert stale_digest.status_code == 409, stale_digest.text

                # A single-Scope delete refuses a parent that still has children.
                single = await http.post(
                    "/v1/scope-deletions",
                    json={
                        "selection": {"mode": "exact", "scope_ids": [root.scope_id]},
                        "confirmed_digest": (
                            await http.post(
                                "/v1/scope-deletions/preview",
                                json={"selection": {"mode": "exact", "scope_ids": [root.scope_id]}},
                            )
                        ).json()["membership_digest"],
                        "single_scope": True,
                    },
                )
                assert single.status_code == 409, single.text

                # Confirm the subtree it actually previewed.
                confirmed = await http.post(
                    "/v1/scope-deletions",
                    json={
                        "selection": {"mode": "subtree", "root_scope_id": root.scope_id},
                        "confirmed_digest": digest,
                    },
                )
                # The operation is accepted durably, so the declared success is 202
                # whether or not cleanup already finished in this call.
                assert confirmed.status_code == 202, confirmed.text
                receipt = confirmed.json()
                # The real SDK accepts that success status, so a completed cleanup
                # never surfaces to a caller as an error.
                async with PowerContextClient(
                    "http://testserver", http_client=http, trust_transport_security=True
                ) as sdk:
                    # A fresh childless Scope exercises the SDK's own success path,
                    # including an immediate complete cleanup.
                    spare = await runtime.scopes.create(
                        ScopeDraft(title="Spare", summary="sdk delete", idempotency_key="spare")
                    )
                    for role, key in (
                        (AccessRole.SCOPE_ADMIN, "manager-admin"),
                        (AccessRole.SCOPE_CONTRIBUTOR, "manager-contrib"),
                    ):
                        await access.create_binding(
                            ADMIN,
                            CreateBinding(
                                subject=MANAGER,
                                resource=ResourceRef.scope(spare.scope_id),
                                role=role,
                                idempotency_key=f"{key}-{spare.scope_id}",
                            ),
                            context=AUDIT,
                        )
                    sdk_preview = await sdk.preview_scope_deletion(
                        ScopeDeletionPreviewRequest.model_validate({
                            "selection": {"mode": "exact", "scope_ids": [spare.scope_id]}
                        })
                    )
                    sdk_receipt = await sdk.confirm_scope_deletion(
                        ConfirmScopeDeletionRequest.model_validate({
                            "selection": {"mode": "exact", "scope_ids": [spare.scope_id]},
                            "confirmed_digest": sdk_preview.membership_digest,
                        })
                    )
                    assert sdk_receipt.state in {"complete", "cleanup_pending"}
                    resumed = await sdk.resume_scope_deletion_cleanup(sdk_receipt.operation_id)
                    assert resumed.state == "complete"
                assert receipt["state"] in {"complete", "cleanup_pending"}
                assert receipt["membership_digest"] == digest
                operation_id = receipt["operation_id"]

                # The operation is readable and its progress is body-free.
                status = await http.get(f"/v1/scope-deletions/{operation_id}")
                assert status.status_code == 200, status.text
                for item in status.json()["progress"]:
                    assert item["handler"]
                    assert "last_error_code" in item

                # Cutoff is effective: the Scope no longer serves ordinary reads.
                gone = await http.get(f"/v1/scopes/{root.scope_id}")
                assert gone.status_code in {403, 404, 409}, gone.text

            # An ordinary reader cannot manage the lifecycle.
            reader_app, reader_headers = app_for(READER)
            async with (
                reader_app.router.lifespan_context(reader_app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=reader_app),
                    base_url="http://testserver",
                    headers=reader_headers,
                ) as reader_http,
            ):
                denied = await reader_http.post(
                    "/v1/scope-deletions/preview",
                    json={"selection": {"mode": "subtree", "root_scope_id": root.scope_id}},
                )
                assert denied.status_code == 403, denied.text
                denied_status = await reader_http.get(f"/v1/scope-deletions/{operation_id}")
                assert denied_status.status_code == 403, denied_status.text

            # Observation is not destructive authority: a server-wide observer with no
            # management of the confirmed members cannot read or resume the operation.
            await access.create_binding(
                ADMIN,
                CreateBinding(
                    subject=OBSERVER,
                    resource=ResourceRef.server(DEPLOYMENT_ID),
                    role=AccessRole.SERVER_OBSERVER,
                    idempotency_key="observer-only",
                ),
                context=AUDIT,
            )
            observer_app, observer_headers = app_for(OBSERVER)
            async with (
                observer_app.router.lifespan_context(observer_app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=observer_app),
                    base_url="http://testserver",
                    headers=observer_headers,
                ) as observer_http,
            ):
                for method, path in (
                    ("get", f"/v1/scope-deletions/{operation_id}"),
                    ("post", f"/v1/scope-deletions/{operation_id}/cleanup"),
                    ("post", f"/v1/scope-deletions/{operation_id}/coverage-repair"),
                ):
                    observed = await getattr(observer_http, method)(path)
                    assert observed.status_code == 403, (path, observed.text)

    asyncio.run(scenario())
