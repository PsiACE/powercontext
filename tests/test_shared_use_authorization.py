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
from datetime import UTC, datetime
from typing import cast

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
    AcceptanceSide,
    SharedOrigin,
    SharedPermissionScope,
    SharedRetention,
    SharedUse,
    SharedUseAcceptance,
    SharedUseAuthorization,
    SharedUseTerms,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.sources import SourceRef, SourceSequenceLocator

DEPLOYMENT_ADMIN = PrincipalRef(type="service", id="deployment-admin")
ORIGIN_ADMIN = PrincipalRef(type="user", id="origin-admin")
TARGET_ADMIN = PrincipalRef(type="user", id="target-admin")
RUNNER = PrincipalRef(type="service", id="dream-runner")
REVIEWER = PrincipalRef(type="user", id="reviewer")
MEMBER = PrincipalRef(type="user", id="member")

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
    """An origin admin, a target admin, and a runner with only execution rights."""

    origin_approval = await _bind(service, ORIGIN_ADMIN, ORIGIN_SCOPE, AccessRole.SCOPE_ADMIN, "origin-admin")
    await _bind(service, TARGET_ADMIN, TARGET_SCOPE, AccessRole.SCOPE_ADMIN, "target-admin")
    await _bind(service, TARGET_ADMIN, AUDIENCE_SCOPE, AccessRole.SCOPE_ADMIN, "audience-admin")
    await _bind(service, RUNNER, TARGET_SCOPE, AccessRole.SCOPE_CONTRIBUTOR, "runner-contributor")
    await _bind(service, REVIEWER, TARGET_SCOPE, AccessRole.SCOPE_REVIEWER, "reviewer")
    await _bind(service, REVIEWER, ORIGIN_SCOPE, AccessRole.SCOPE_VIEWER, "reviewer-origin-reader")
    await _bind(service, MEMBER, TARGET_SCOPE, AccessRole.SCOPE_VIEWER, "member")
    if runner_reads_origin:
        await _bind(service, RUNNER, ORIGIN_SCOPE, AccessRole.SCOPE_VIEWER, "runner-origin-reader")
    return origin_approval


def _terms(
    *,
    audience_scope_ids: frozenset[str] = frozenset({TARGET_SCOPE, AUDIENCE_SCOPE}),
    origins: tuple[SharedOrigin, ...] | None = None,
    retention: SharedRetention = SharedRetention.INDEPENDENT,
    permission_scope: SharedPermissionScope = SharedPermissionScope.LOGICAL,
) -> SharedUseTerms:
    return SharedUseTerms(
        target_scope_id=TARGET_SCOPE,
        origins=origins
        or (
            SharedOrigin.of_artifact(ORIGIN_SCOPE, RETRY_TRACE),
            SharedOrigin.of_source(ORIGIN_SCOPE, INCIDENT),
        ),
        uses=frozenset({SharedUse.EVIDENCE}),
        audience_scope_ids=audience_scope_ids,
        retention=retention,
        permission_scope=permission_scope,
    )


async def _accepted(shared: SharedUseAuthorization, terms: SharedUseTerms):
    """The genuine path: each authority accepts its own half."""

    return (
        await shared.accept(ORIGIN_ADMIN, terms, context=AUDIT),
        await shared.accept(TARGET_ADMIN, terms, context=AUDIT),
    )


def test_authenticated_acceptance_admits_the_run_and_binds_its_terms() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(DEPLOYMENT_ADMIN,)) as access:
            await _seed(access, runner_reads_origin=True)
            shared = SharedUseAuthorization(access)
            terms = _terms()
            acceptances = await _accepted(shared, terms)
            assert {item.accepted_by for item in acceptances} == {ORIGIN_ADMIN, TARGET_ADMIN}

            grant = await shared.require(RUNNER, terms, acceptances=acceptances, use=SharedUse.EVIDENCE, context=AUDIT)
            assert grant.terms_digest == terms.digest
            assert (
                await shared.recheck(
                    RUNNER,
                    terms,
                    acceptances=acceptances,
                    use=SharedUse.EVIDENCE,
                    expected_digest=grant.terms_digest,
                    context=AUDIT,
                )
            ).terms_digest == terms.digest

            # Terms changed after acceptance are refused, not re-interpreted.
            narrowed = _terms(audience_scope_ids=frozenset({TARGET_SCOPE}))
            with pytest.raises(AccessConflictError):
                await shared.recheck(
                    RUNNER,
                    narrowed,
                    acceptances=acceptances,
                    use=SharedUse.EVIDENCE,
                    expected_digest=grant.terms_digest,
                    context=AUDIT,
                )

    asyncio.run(scenario())


def test_named_third_party_acceptance_without_an_act_is_denied() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(DEPLOYMENT_ADMIN,)) as access:
            await _seed(access, runner_reads_origin=True)
            shared = SharedUseAuthorization(access)
            terms = _terms()

            # Naming a privileged principal is not that principal's agreement...
            with pytest.raises(AccessDeniedError):
                await shared.require(RUNNER, terms, acceptances=(), use=SharedUse.EVIDENCE, context=AUDIT)
            # ...and neither is a hand-built acceptance claiming to be one.
            forged = SharedUseAcceptance(
                terms_digest=terms.digest,
                accepted_by=ORIGIN_ADMIN,
                sides=frozenset(AcceptanceSide),
                accepted_at=datetime.now(UTC),
                proof="0" * 64,
            )
            with pytest.raises(AccessDeniedError):
                await shared.require(RUNNER, terms, acceptances=(forged,), use=SharedUse.EVIDENCE, context=AUDIT)

    asyncio.run(scenario())


def test_read_and_write_without_agreement_does_not_authorize_disclosure() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(DEPLOYMENT_ADMIN,)) as access:
            await _seed(access, runner_reads_origin=True)
            shared = SharedUseAuthorization(access)
            terms = _terms()

            # The runner can read the origin and write the target...
            await access.require(RUNNER, AccessAction.SCOPE_READ, ResourceRef.scope(ORIGIN_SCOPE), context=AUDIT)
            await access.require(RUNNER, AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(TARGET_SCOPE), context=AUDIT)
            # ...but no agreement exists, so disclosure stays closed.
            with pytest.raises(AccessDeniedError):
                await shared.require(RUNNER, terms, acceptances=(), use=SharedUse.EVIDENCE, context=AUDIT)

    asyncio.run(scenario())


def test_acceptance_requires_authority_and_revocation_blocks_commit() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(DEPLOYMENT_ADMIN,)) as access:
            origin_binding = await _seed(access, runner_reads_origin=True)
            shared = SharedUseAuthorization(access)
            terms = _terms()

            # An actor without either authority cannot accept on anyone's behalf.
            with pytest.raises(AccessDeniedError):
                await shared.accept(RUNNER, terms, context=AUDIT)

            acceptances = await _accepted(shared, terms)
            grant = await shared.require(RUNNER, terms, acceptances=acceptances, use=SharedUse.EVIDENCE, context=AUDIT)
            await shared.require_evidence(REVIEWER, terms.origins[0], context=AUDIT)

            await access.revoke_binding(
                DEPLOYMENT_ADMIN,
                origin_binding.binding_id,
                expected_version=origin_binding.version,
                idempotency_key="withdraw-origin-authority",
                context=AUDIT,
            )

            # The recorded acceptance is unchanged; the authority behind it is gone.
            with pytest.raises(AccessDeniedError):
                await shared.recheck(
                    RUNNER,
                    terms,
                    acceptances=acceptances,
                    use=SharedUse.EVIDENCE,
                    expected_digest=grant.terms_digest,
                    context=AUDIT,
                )

    asyncio.run(scenario())


def test_origins_differing_only_in_a_component_stay_distinct() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(DEPLOYMENT_ADMIN,)) as access:
            await _seed(access, runner_reads_origin=True)
            shared = SharedUseAuthorization(access)

            split = SharedOrigin.of_source(ORIGIN_SCOPE, SourceRef(source_type="incident/x", source_id="y"))
            joined = SharedOrigin.of_source(ORIGIN_SCOPE, SourceRef(source_type="incident", source_id="x/y"))
            assert split.key != joined.key

            # Distinct origins coexist in one run instead of colliding as duplicates.
            terms = _terms(origins=(split, joined))
            acceptances = await _accepted(shared, terms)
            grant = await shared.require(RUNNER, terms, acceptances=acceptances, use=SharedUse.EVIDENCE, context=AUDIT)
            assert grant.terms_digest == terms.digest

            # A substituted origin is a different agreement, never admitted.
            substituted = _terms(origins=(split,))
            with pytest.raises(AccessConflictError):
                await shared.recheck(
                    RUNNER,
                    substituted,
                    acceptances=acceptances,
                    use=SharedUse.EVIDENCE,
                    expected_digest=grant.terms_digest,
                    context=AUDIT,
                )

            # The pinned locator is part of the identity too.
            first = SharedOrigin.of_source(
                ORIGIN_SCOPE,
                SourceRef(source_type="incident", source_id="i", locator=SourceSequenceLocator(sequence=1)),
            )
            second = SharedOrigin.of_source(
                ORIGIN_SCOPE,
                SourceRef(source_type="incident", source_id="i", locator=SourceSequenceLocator(sequence=2)),
            )
            assert first.key != second.key

    asyncio.run(scenario())


def test_unsupported_modes_are_refused_by_name_not_by_crash() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(DEPLOYMENT_ADMIN,)) as access:
            await _seed(access, runner_reads_origin=True)
            shared = SharedUseAuthorization(access)

            restored = SharedUseTerms(
                target_scope_id=TARGET_SCOPE,
                origins=(SharedOrigin.of_artifact(ORIGIN_SCOPE, RETRY_TRACE),),
                # Stored rules decode to plain strings; supported values are accepted.
                uses=cast("frozenset[SharedUse]", frozenset({"evidence"})),
                audience_scope_ids=frozenset({TARGET_SCOPE}),
                retention=cast(SharedRetention, "independent"),
                permission_scope=cast(SharedPermissionScope, "logical"),
            )
            assert restored.retention is SharedRetention.INDEPENDENT

            for unsupported in (
                _terms(permission_scope=SharedPermissionScope.REVISION_ONLY),
                _terms(retention=SharedRetention.WITHHOLD_ON_REVOCATION),
            ):
                with pytest.raises(AccessInvalidRequestError):
                    await shared.require(
                        RUNNER,
                        unsupported,
                        acceptances=await _accepted(shared, unsupported),
                        use=SharedUse.EVIDENCE,
                        context=AUDIT,
                    )
            with pytest.raises(AccessInvalidRequestError):
                SharedUseTerms(
                    target_scope_id=TARGET_SCOPE,
                    origins=(SharedOrigin.of_artifact(ORIGIN_SCOPE, RETRY_TRACE),),
                    uses=frozenset({SharedUse.EVIDENCE}),
                    audience_scope_ids=frozenset({TARGET_SCOPE}),
                    retention=cast(SharedRetention, "erase_everything"),
                )

    asyncio.run(scenario())


def test_retained_target_conclusion_is_readable_without_origin_evidence_access() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(DEPLOYMENT_ADMIN,)) as access:
            await _seed(access)
            shared = SharedUseAuthorization(access)

            await access.require(MEMBER, AccessAction.SCOPE_READ, ResourceRef.scope(TARGET_SCOPE), context=AUDIT)
            with pytest.raises(AccessDeniedError):
                await shared.require_evidence(
                    MEMBER, SharedOrigin.of_artifact(ORIGIN_SCOPE, RETRY_TRACE), context=AUDIT
                )

    asyncio.run(scenario())
