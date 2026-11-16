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

"""One cross-Scope query keeps origins, coverage, and exact revisions honest."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.artifacts.search import ArtifactSearchExecutionContext
from powercontext.builtin.artifacts.experience import Experience, ExperienceContent, ExperienceDraft
from powercontext.builtin.authz import (
    AccessAuditContext,
    AccessDeniedError,
    AccessRole,
    CreateBinding,
    PrincipalRef,
    ResourceRef,
)
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite, BaseValueNotFoundError
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_contexts, open_builtin_runtime
from powercontext.builtin.runtime.cross_scope_query import (
    CrossScopeQueryBoundsError,
    CrossScopeQueryRequest,
    CrossScopeQueryUnsupported,
)
from powercontext.builtin.scope import ScopeDraft

ADMIN = PrincipalRef(type="service", id="admin")
VIEWER = PrincipalRef(type="user", id="viewer")
AUDIT = AccessAuditContext(transport="local", operation="cross-scope-query", request_id="cross-scope")
# The same local Artifact ID exists in two Scopes on purpose: identity is the origin address.
RETRY_TRACE = "retry-trace"
QUERY = "retry trace idempotency"


def _experience(lesson: str) -> ExperienceContent:
    return ExperienceContent(
        situation="A retry trace shows a duplicate callback on the old channel.",
        action="Retry with an idempotency key and drop the stale channel.",
        outcome="The callback runs once against the current endpoint.",
        lesson=lesson,
    )


async def _seed(config: BuiltinConfig) -> tuple[str, str, str]:
    async with open_builtin_contexts(config) as contexts:
        interface = await contexts.scopes.create(
            ScopeDraft(title="Interface", summary="Interface evidence", idempotency_key="interface")
        )
        helpdesk = await contexts.scopes.create(
            ScopeDraft(title="Help", summary="Help evidence", idempotency_key="help")
        )
        orders = await contexts.scopes.create(
            ScopeDraft(title="Orders", summary="Queries both libraries", idempotency_key="orders")
        )
        async with contexts.database.transaction() as connection:
            for scope_id, lesson in (
                (interface.scope_id, "Bound the retry trace to one idempotency key."),
                (helpdesk.scope_id, "Ask for the idempotency key from the retry trace."),
            ):
                artifact = cast(
                    Experience,
                    await contexts.repositories.artifacts.create(
                        connection, scope_id, RETRY_TRACE, ExperienceDraft(content=_experience(lesson))
                    ),
                )
                await contexts.experience_index.replace(connection, scope_id, artifact)
    return interface.scope_id, helpdesk.scope_id, orders.scope_id


def _grant_request(scope_id: str, key: str) -> CreateBinding:
    return CreateBinding(
        subject=VIEWER,
        resource=ResourceRef.scope(scope_id),
        role=AccessRole.SCOPE_VIEWER,
        idempotency_key=f"{key}-{scope_id}",
    )


def test_cross_scope_query_keeps_origin_identity_coverage_and_exact_revision(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'content.db'}"),
            runtime=RuntimeConfig(artifact_processing_families=()),
        )
        policy = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'policy.db'}")
        interface, helpdesk, orders = await _seed(config)
        async with (
            open_builtin_access_control(
                policy, bootstrap_administrators=(ADMIN,), deployment_id="cross-scope"
            ) as access,
            open_builtin_runtime(config) as runtime,
        ):
            await access.create_binding(ADMIN, _grant_request(interface, "viewer"), context=AUDIT)
            helpdesk_grant = await access.create_binding(ADMIN, _grant_request(helpdesk, "viewer"), context=AUDIT)
            context = ArtifactSearchExecutionContext(VIEWER, access, AUDIT)
            query = runtime.cross_scope.for_scope(orders)

            def request(*, payload=None, **overrides) -> CrossScopeQueryRequest:
                return CrossScopeQueryRequest(
                    family="experience",
                    payload={"query": QUERY} if payload is None else payload,
                    scopes=(interface, helpdesk),
                    **overrides,
                )

            complete = await query.search(request(limit=4), execution_context=context)
            assert complete.coverage.complete is True
            assert complete.coverage.searched_origins == (interface, helpdesk)
            # Same local Artifact ID in both Scopes stays two distinct hits, and each keeps its origin.
            assert [hit.identity for hit in complete.hits] == [
                (interface, "experience", RETRY_TRACE, 1),
                (helpdesk, "experience", RETRY_TRACE, 1),
            ]
            assert [(hit.origin_rank, hit.origin.scope_id) for hit in complete.hits] == [(1, interface), (1, helpdesk)]

            # A budget that narrows the caller's declared window is never a complete search.
            declared = {"query": QUERY, "limit": 10}
            with pytest.raises(CrossScopeQueryBoundsError):
                await query.search(request(limit=4, max_candidates=6, payload=declared), execution_context=context)
            narrowed = await query.search(
                request(limit=4, max_candidates=6, coverage="partial", payload=declared),
                execution_context=context,
            )
            assert narrowed.coverage.complete is False
            assert narrowed.coverage.reason == "candidate_budget"
            assert narrowed.coverage.bounded_origins == (interface, helpdesk)

            # The source publishes a new revision; the earlier hit still names revision one.
            published = await runtime.records.for_scope(interface).replace_artifact(
                "experience",
                RETRY_TRACE,
                '"revision:1"',
                ArtifactWrite(
                    content=_experience("Always reuse the idempotency key for the retry trace.").model_dump()
                ),
            )
            assert published.revision == 2
            after = await query.search(request(limit=4), execution_context=context)
            assert [hit.identity for hit in after.hits] == [
                (interface, "experience", RETRY_TRACE, 2),
                (helpdesk, "experience", RETRY_TRACE, 1),
            ]
            exact = await query.open_hit(complete.hits[0].origin, execution_context=context)
            assert exact.revision == 1
            assert exact.scope_id == interface
            assert exact.content["lesson"] == "Bound the retry trace to one idempotency key."

            # Losing origin authority fails a complete query and truncates a partial one honestly.
            await access.revoke_binding(
                ADMIN,
                helpdesk_grant.binding_id,
                expected_version=helpdesk_grant.version,
                idempotency_key="revoke-helpdesk",
                context=AUDIT,
            )
            with pytest.raises(AccessDeniedError):
                await query.search(request(limit=4), execution_context=context)
            with pytest.raises(AccessDeniedError):
                await query.open_hit(
                    ArtifactAddress(
                        scope_id=helpdesk,
                        artifact=ArtifactRef(family="experience", artifact_id=RETRY_TRACE, revision=1),
                    ),
                    execution_context=context,
                )
            partial = await query.search(request(limit=4, coverage="partial"), execution_context=context)
            assert partial.coverage.complete is False
            assert partial.coverage.reason == "origin_failure"
            assert partial.coverage.failed_origins == (helpdesk,)
            assert {hit.origin.scope_id for hit in partial.hits} == {interface}

            # A revision-limited search cannot be enforced by the current-head projection.
            with pytest.raises(CrossScopeQueryUnsupported):
                await query.search(
                    request(
                        limit=4,
                        restrict_to=ArtifactRef(family="experience", artifact_id=RETRY_TRACE, revision=1),
                    ),
                    execution_context=context,
                )

            # An unavailable exact revision reports absence instead of a current-revision substitute.
            with pytest.raises(BaseValueNotFoundError):
                await query.open_hit(
                    ArtifactAddress(
                        scope_id=interface,
                        artifact=ArtifactRef(family="experience", artifact_id=RETRY_TRACE, revision=9),
                    ),
                    execution_context=context,
                )

            # The budget is a ceiling, not a work request: a declared top-1 window stays top-1 even
            # when the allocation is far larger, and must never become an invalid Family limit.
            await runtime.records.for_scope(interface).create_artifact(
                "experience",
                ArtifactWrite(content=_experience("The retry trace confirms the idempotency key.").model_dump()),
            )
            top_one_runs = []
            for budget in (64, 256):
                top_one = await query.search(
                    CrossScopeQueryRequest(
                        family="experience",
                        payload={"query": QUERY, "limit": 1},
                        scopes=(interface,),
                        limit=10,
                        max_candidates=budget,
                    ),
                    execution_context=context,
                )
                assert top_one.coverage.complete is True
                assert len(top_one.hits) == 1
                assert top_one.hits[0].origin_rank == 1
                top_one_runs.append(top_one.hits)
            # A larger allocation returns the same single declared top hit, not more of them.
            assert top_one_runs[0] == top_one_runs[1]
            # The Family still owns its own limit contract: an invalid declared window is reported
            # as such, and the allocation never rewrites a valid declared window.
            with pytest.raises(ValidationError):
                await query.search(
                    CrossScopeQueryRequest(
                        family="experience",
                        payload={"query": QUERY, "limit": 201},
                        scopes=(interface,),
                        limit=10,
                        max_candidates=256,
                    ),
                    execution_context=context,
                )

    asyncio.run(scenario())
