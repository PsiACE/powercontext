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

from __future__ import annotations

import asyncio

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.authz import (
    AccessAction,
    AccessAuditContext,
    AccessConflictError,
    AccessControlService,
    AccessDeniedError,
    AccessInvalidRequestError,
    AccessRole,
    CreateBinding,
    PrincipalRef,
    ResourceRef,
)
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.authz.shared_use import (
    SharedOrigin,
    SharedPermissionScope,
    SharedRetention,
    SharedUse,
    SharedUseAuthorization,
    SharedUseTerms,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.sources import SourceRef

DEPLOYMENT_ADMIN = PrincipalRef(type="service", id="deployment-admin")
ORIGIN_AUTHOR = PrincipalRef(type="user", id="origin-author")
ORIGIN_SCOPE_ADMIN = PrincipalRef(type="user", id="origin-scope-admin")
TARGET_ADMIN = PrincipalRef(type="user", id="target-admin")
RUNNER = PrincipalRef(type="service", id="dream-runner")
REVIEWER = PrincipalRef(type="user", id="reviewer")

ORIGIN_SCOPE = "alice-work"
TARGET_SCOPE = "orders"
AUDIENCE_SCOPE = "orders-readers"
OTHER_SCOPE = "marketing"

RETRY_TRACE = ArtifactRef(family="experience", artifact_id="retry-trace", revision=3)
INCIDENT = SourceRef(source_type="incident", source_id="duplicate-callback")
AUDIT = AccessAuditContext(transport="background", operation="shared_use_test", request_id="req-shared")


async def _bind(service: AccessControlService, subject: PrincipalRef, scope_id: str, role: AccessRole, key: str):
    return await service.create_binding(
        DEPLOYMENT_ADMIN,
        CreateBinding(
            subject=subject,
            resource=ResourceRef.scope(scope_id),
            role=role,
            idempotency_key=key,
        ),
        context=AUDIT,
    )


async def _seed(service: AccessControlService, *, runner_reads_origin: bool = False):
    """One origin author, one target project, one disclosure audience."""

    await service.establish_artifact_owner(
        ResourceRef.artifact(ORIGIN_SCOPE, family=RETRY_TRACE.family, artifact_id=RETRY_TRACE.artifact_id),
        ORIGIN_AUTHOR,
        idempotency_key="owner-retry-trace",
        context=AUDIT,
    )
    origin_approval = await _bind(
        service, ORIGIN_SCOPE_ADMIN, ORIGIN_SCOPE, AccessRole.SCOPE_ADMIN, "origin-scope-admin"
    )
    await _bind(service, TARGET_ADMIN, TARGET_SCOPE, AccessRole.SCOPE_ADMIN, "target-admin")
    await _bind(service, TARGET_ADMIN, AUDIENCE_SCOPE, AccessRole.SCOPE_ADMIN, "audience-admin")
    await _bind(service, RUNNER, TARGET_SCOPE, AccessRole.SCOPE_CONTRIBUTOR, "runner-contributor")
    await _bind(service, REVIEWER, TARGET_SCOPE, AccessRole.SCOPE_REVIEWER, "reviewer")
    await _bind(service, REVIEWER, ORIGIN_SCOPE, AccessRole.SCOPE_VIEWER, "reviewer-origin-reader")
    if runner_reads_origin:
        await _bind(service, RUNNER, ORIGIN_SCOPE, AccessRole.SCOPE_VIEWER, "runner-origin-reader")
    return origin_approval


def _terms(
    *,
    audience_scope_ids: frozenset[str] = frozenset({TARGET_SCOPE, AUDIENCE_SCOPE}),
    origin_approved_by: PrincipalRef = ORIGIN_SCOPE_ADMIN,
    retention: SharedRetention = SharedRetention.INDEPENDENT,
    permission_scope: SharedPermissionScope = SharedPermissionScope.LOGICAL,
) -> SharedUseTerms:
    return SharedUseTerms(
        target_scope_id=TARGET_SCOPE,
        origins=(
            SharedOrigin.of_artifact(ORIGIN_SCOPE, RETRY_TRACE),
            SharedOrigin.of_source(ORIGIN_SCOPE, INCIDENT),
        ),
        uses=frozenset({SharedUse.EVIDENCE}),
        audience_scope_ids=audience_scope_ids,
        origin_approved_by=origin_approved_by,
        target_approved_by=TARGET_ADMIN,
        retention=retention,
        permission_scope=permission_scope,
    )


def test_agreed_use_admits_the_run_and_binds_its_terms() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(DEPLOYMENT_ADMIN,)) as access:
            await _seed(access, runner_reads_origin=True)
            shared = SharedUseAuthorization(access)
            terms = _terms()

            grant = await shared.require(RUNNER, terms, use=SharedUse.EVIDENCE, context=AUDIT)
            assert grant.terms_digest == terms.digest
            assert grant.audience_scope_ids == {TARGET_SCOPE, AUDIENCE_SCOPE}
            assert grant.origins == terms.origins

            # A retry against the same pinned terms still passes.
            assert (
                await shared.recheck(
                    RUNNER,
                    terms,
                    use=SharedUse.EVIDENCE,
                    expected_digest=grant.terms_digest,
                    context=AUDIT,
                )
            ).terms_digest == terms.digest

            # Narrowing the audience changes the terms and is refused, not widened.
            narrowed = _terms(audience_scope_ids=frozenset({TARGET_SCOPE}))
            with pytest.raises(AccessConflictError):
                await shared.recheck(
                    RUNNER,
                    narrowed,
                    use=SharedUse.EVIDENCE,
                    expected_digest=grant.terms_digest,
                    context=AUDIT,
                )

    asyncio.run(scenario())


def test_reading_the_origin_and_writing_the_target_do_not_authorize_disclosure() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(DEPLOYMENT_ADMIN,)) as access:
            await _seed(access)
            await _bind(access, RUNNER, ORIGIN_SCOPE, AccessRole.SCOPE_VIEWER, "runner-origin-reader")
            shared = SharedUseAuthorization(access)

            # The runner can read the origin and write the target...
            await access.require(RUNNER, AccessAction.SCOPE_READ, ResourceRef.scope(ORIGIN_SCOPE), context=AUDIT)
            await access.require(RUNNER, AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(TARGET_SCOPE), context=AUDIT)
            # ...but nobody with origin authority agreed to this use.
            with pytest.raises(AccessDeniedError):
                await shared.require(
                    RUNNER,
                    _terms(origin_approved_by=ORIGIN_AUTHOR),
                    use=SharedUse.EVIDENCE,
                    context=AUDIT,
                )

    asyncio.run(scenario())


def test_unauthorized_audience_and_use_are_refused() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(DEPLOYMENT_ADMIN,)) as access:
            await _seed(access)
            shared = SharedUseAuthorization(access)

            # A Scope nobody administers on the target's behalf is not an audience.
            with pytest.raises(AccessDeniedError):
                await shared.require(
                    RUNNER,
                    _terms(audience_scope_ids=frozenset({TARGET_SCOPE, OTHER_SCOPE})),
                    use=SharedUse.EVIDENCE,
                    context=AUDIT,
                )
            # The target itself must always be able to read its conclusion.
            with pytest.raises(AccessInvalidRequestError):
                _terms(audience_scope_ids=frozenset({AUDIENCE_SCOPE}))

    asyncio.run(scenario())


def test_unsupported_restrictions_fail_closed() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(DEPLOYMENT_ADMIN,)) as access:
            await _seed(access)
            shared = SharedUseAuthorization(access)

            unsupported = (
                _terms(permission_scope=SharedPermissionScope.REVISION_ONLY),
                _terms(retention=SharedRetention.WITHHOLD_ON_REVOCATION),
            )
            for terms in unsupported:
                with pytest.raises(AccessInvalidRequestError):
                    await shared.require(RUNNER, terms, use=SharedUse.EVIDENCE, context=AUDIT)

    asyncio.run(scenario())


def test_revoked_origin_approval_blocks_commit_and_evidence_inspection() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(DEPLOYMENT_ADMIN,)) as access:
            origin_approval = await _seed(access, runner_reads_origin=True)
            shared = SharedUseAuthorization(access)
            terms = _terms()
            grant = await shared.require(RUNNER, terms, use=SharedUse.EVIDENCE, context=AUDIT)

            # Reviewers can open the original evidence under their own origin grant.
            await shared.require_evidence(REVIEWER, terms.origins[0], context=AUDIT)

            await access.revoke_binding(
                DEPLOYMENT_ADMIN,
                origin_approval.binding_id,
                expected_version=origin_approval.version,
                idempotency_key="withdraw-origin-approval",
                context=AUDIT,
            )

            # The pending commit is blocked even though the recorded terms are unchanged.
            with pytest.raises(AccessDeniedError):
                await shared.recheck(
                    RUNNER,
                    terms,
                    use=SharedUse.EVIDENCE,
                    expected_digest=grant.terms_digest,
                    context=AUDIT,
                )

    asyncio.run(scenario())


def test_retained_target_conclusion_is_readable_without_origin_evidence_access() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(DEPLOYMENT_ADMIN,)) as access:
            await _seed(access)
            shared = SharedUseAuthorization(access)
            member = PrincipalRef(type="user", id="member")
            await _bind(access, member, TARGET_SCOPE, AccessRole.SCOPE_VIEWER, "member")

            # The committed project conclusion is readable to the audience...
            await access.require(member, AccessAction.SCOPE_READ, ResourceRef.scope(TARGET_SCOPE), context=AUDIT)
            # ...while the private origin stays closed to that member.
            with pytest.raises(AccessDeniedError):
                await shared.require_evidence(
                    member, SharedOrigin.of_artifact(ORIGIN_SCOPE, RETRY_TRACE), context=AUDIT
                )

    asyncio.run(scenario())
