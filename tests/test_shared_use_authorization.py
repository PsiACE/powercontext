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

ADMIN = PrincipalRef(type="service", id="deployment-admin")
ALICE = PrincipalRef(type="user", id="alice")
BOB = PrincipalRef(type="user", id="bob")
CAROL = PrincipalRef(type="user", id="carol")
DAVE = PrincipalRef(type="user", id="dave")
RUNNER = PrincipalRef(type="service", id="dream-runner")
REVIEWER = PrincipalRef(type="user", id="reviewer")
MEMBER = PrincipalRef(type="user", id="member")

ALICE_WORK = "alice-work"
BOB_WORK = "bob-work"
TARGET = "orders"
AUDIENCE = "operations"
EXTRA_AUDIENCE = "reliability"

RETRY_TRACE = ArtifactRef(family="experience", artifact_id="retry-trace", revision=3)
DUPLICATE = SourceRef(source_type="incident", source_id="duplicate-callback")
RETRY_TRACE_4 = ArtifactRef(family="experience", artifact_id="retry-trace", revision=4)
AUDIT = AccessAuditContext(transport="background", operation="shared_use_test", request_id="req-shared")


async def _bind(service: AccessControlService, subject: PrincipalRef, scope_id: str, role: AccessRole, key: str):
    return await service.create_binding(
        ADMIN,
        CreateBinding(
            subject=subject,
            resource=ResourceRef.scope(scope_id),
            role=role,
            idempotency_key=key,
        ),
        context=AUDIT,
    )


async def _seed(service: AccessControlService, *, runner_reads_origins: bool = True):
    """Two independent contributors, one target owner, one execution identity."""

    alice_binding = await _bind(service, ALICE, ALICE_WORK, AccessRole.SCOPE_ADMIN, "alice-origin")
    await _bind(service, BOB, BOB_WORK, AccessRole.SCOPE_ADMIN, "bob-origin")
    await _bind(service, CAROL, TARGET, AccessRole.SCOPE_ADMIN, "carol-target")
    await _bind(service, DAVE, AUDIENCE, AccessRole.SCOPE_ADMIN, "dave-audience")
    await _bind(service, DAVE, EXTRA_AUDIENCE, AccessRole.SCOPE_ADMIN, "dave-extra-audience")
    await _bind(service, RUNNER, TARGET, AccessRole.SCOPE_CONTRIBUTOR, "runner-contributor")
    await _bind(service, REVIEWER, TARGET, AccessRole.SCOPE_REVIEWER, "reviewer")
    # A reviewer opens the original under its own origin grant, never implicitly.
    await _bind(service, REVIEWER, ALICE_WORK, AccessRole.SCOPE_VIEWER, "reviewer-origin-reader")
    await _bind(service, MEMBER, TARGET, AccessRole.SCOPE_VIEWER, "member")
    if runner_reads_origins:
        await _bind(service, RUNNER, ALICE_WORK, AccessRole.SCOPE_VIEWER, "runner-alice-reader")
        await _bind(service, RUNNER, BOB_WORK, AccessRole.SCOPE_VIEWER, "runner-bob-reader")
    return alice_binding


def _terms(
    *,
    audience_scope_ids: frozenset[str] = frozenset({TARGET, AUDIENCE}),
    origins: tuple[SharedOrigin, ...] | None = None,
    retention: SharedRetention = SharedRetention.INDEPENDENT,
    permission_scope: SharedPermissionScope = SharedPermissionScope.LOGICAL,
) -> SharedUseTerms:
    return SharedUseTerms(
        target_scope_id=TARGET,
        origins=origins
        or (
            SharedOrigin.of_artifact(ALICE_WORK, RETRY_TRACE),
            SharedOrigin.of_source(BOB_WORK, DUPLICATE),
        ),
        uses=frozenset({SharedUse.EVIDENCE}),
        audience_scope_ids=audience_scope_ids,
        retention=retention,
        permission_scope=permission_scope,
    )


async def _accept(shared: SharedUseAuthorization, actor: PrincipalRef, terms: SharedUseTerms):
    return await shared.accept(terms, authenticated_actor=actor, context=AUDIT)


def test_independent_contributors_compose_into_one_lawful_run() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(ADMIN,)) as access:
            await _seed(access)
            shared = SharedUseAuthorization(access)
            terms = _terms()

            # Each owner speaks only for what it administers; nobody administers both.
            alice = await _accept(shared, ALICE, terms)
            bob = await _accept(shared, BOB, terms)
            target = await _accept(shared, CAROL, terms)
            audience = await _accept(shared, DAVE, terms)
            assert len(alice.origins) == 1 and len(bob.origins) == 1
            assert alice.audience_scope_ids == frozenset() and target.audience_scope_ids == {TARGET}

            grant = await shared.require(
                RUNNER,
                terms,
                acceptances=(alice, bob, target, audience),
                use=SharedUse.EVIDENCE,
                context=AUDIT,
            )
            assert grant.terms_digest == terms.digest
            assert grant.use is SharedUse.EVIDENCE

            # A bound retry still passes; omitting one contributor's consent does not.
            assert (
                await shared.recheck(
                    RUNNER,
                    terms,
                    acceptances=(alice, bob, target, audience),
                    use=SharedUse.EVIDENCE,
                    expected_digest=grant.terms_digest,
                    context=AUDIT,
                )
            ).terms_digest == terms.digest
            with pytest.raises(AccessDeniedError):
                await shared.recheck(
                    RUNNER,
                    terms,
                    acceptances=(alice, target, audience),
                    use=SharedUse.EVIDENCE,
                    expected_digest=grant.terms_digest,
                    context=AUDIT,
                )

    asyncio.run(scenario())


def test_coverage_for_an_unrelated_origin_does_not_admit_the_run() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(ADMIN,)) as admin_only:
            await _seed(admin_only)
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(ADMIN,)) as access:
            # A contributor consents to a *different* revision than the run pins.
            await _bind(access, ALICE, ALICE_WORK, AccessRole.SCOPE_ADMIN, "alice-origin")
            await _bind(access, BOB, BOB_WORK, AccessRole.SCOPE_ADMIN, "bob-origin")
            await _bind(access, CAROL, TARGET, AccessRole.SCOPE_ADMIN, "carol-target")
            await _bind(access, DAVE, AUDIENCE, AccessRole.SCOPE_ADMIN, "dave-audience")
            await _bind(access, RUNNER, TARGET, AccessRole.SCOPE_CONTRIBUTOR, "runner")
            await _bind(access, RUNNER, ALICE_WORK, AccessRole.SCOPE_VIEWER, "runner-alice")
            await _bind(access, RUNNER, BOB_WORK, AccessRole.SCOPE_VIEWER, "runner-bob")
            shared = SharedUseAuthorization(access)
            terms = _terms()

            other_run = _terms(origins=(SharedOrigin.of_artifact(ALICE_WORK, RETRY_TRACE_4),))
            alice = await _accept(shared, ALICE, other_run)
            bob = await _accept(shared, BOB, terms)
            target = await _accept(shared, CAROL, terms)
            audience = await _accept(shared, DAVE, terms)

            # Alice's consent covers revision 4, so it cannot stand in for revision 3.
            with pytest.raises(AccessConflictError):
                await shared.require(
                    RUNNER,
                    terms,
                    acceptances=(alice, bob, target, audience),
                    use=SharedUse.EVIDENCE,
                    context=AUDIT,
                )
            with pytest.raises(AccessDeniedError):
                await shared.require(
                    RUNNER,
                    terms,
                    acceptances=(bob, target, audience),
                    use=SharedUse.EVIDENCE,
                    context=AUDIT,
                )

    asyncio.run(scenario())


def test_revoking_one_contributor_blocks_commit_without_covering_the_others() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(ADMIN,)) as access:
            alice_binding = await _seed(access)
            shared = SharedUseAuthorization(access)
            terms = _terms()
            acceptances = (
                await _accept(shared, ALICE, terms),
                await _accept(shared, BOB, terms),
                await _accept(shared, CAROL, terms),
                await _accept(shared, DAVE, terms),
            )
            grant = await shared.require(RUNNER, terms, acceptances=acceptances, use=SharedUse.EVIDENCE, context=AUDIT)

            await access.revoke_binding(
                ADMIN,
                alice_binding.binding_id,
                expected_version=alice_binding.version,
                idempotency_key="withdraw-alice",
                context=AUDIT,
            )

            # Alice's own origin is still recorded, but the authority behind it is gone.
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


def test_read_and_write_without_consent_does_not_authorize_disclosure() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(ADMIN,)) as access:
            await _seed(access)
            shared = SharedUseAuthorization(access)
            terms = _terms()

            # The runner can read both origins and write the target...
            await access.require(RUNNER, AccessAction.SCOPE_READ, ResourceRef.scope(ALICE_WORK), context=AUDIT)
            await access.require(RUNNER, AccessAction.SCOPE_READ, ResourceRef.scope(BOB_WORK), context=AUDIT)
            await access.require(RUNNER, AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(TARGET), context=AUDIT)
            # ...but no contributor agreed, so disclosure stays closed.
            with pytest.raises(AccessDeniedError):
                await shared.require(RUNNER, terms, acceptances=(), use=SharedUse.EVIDENCE, context=AUDIT)

            # An actor with no authority at all cannot accept on anyone's behalf.
            with pytest.raises(AccessDeniedError):
                await _accept(shared, RUNNER, terms)
            with pytest.raises(AccessDeniedError):
                await shared.accept(terms, authenticated_actor=None, context=AUDIT)

    asyncio.run(scenario())


def test_origins_differing_only_in_a_component_stay_distinct() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(ADMIN,)) as access:
            await _seed(access, runner_reads_origins=False)
            await _bind(access, ALICE, ALICE_WORK, AccessRole.SCOPE_VIEWER, "runner-unused")
            shared = SharedUseAuthorization(access)

            split = SharedOrigin.of_source(ALICE_WORK, SourceRef(source_type="incident/x", source_id="y"))
            joined = SharedOrigin.of_source(ALICE_WORK, SourceRef(source_type="incident", source_id="x/y"))
            assert split.key != joined.key
            assert split.identity != joined.identity

            # Both are covered by one genuine acceptance of Alice's own Scope work.
            await _bind(access, RUNNER, ALICE_WORK, AccessRole.SCOPE_ADMIN, "alice-both")
            terms = SharedUseTerms(
                target_scope_id=TARGET,
                origins=(split, joined),
                uses=frozenset({SharedUse.EVIDENCE}),
                audience_scope_ids=frozenset({TARGET}),
            )
            acceptance = await _accept(shared, ALICE, terms)
            assert len(acceptance.origins) == 2

            # The pinned locator is part of the identity too.
            first = SharedOrigin.of_source(
                ALICE_WORK, SourceRef(source_type="incident", source_id="i", locator=SourceSequenceLocator(sequence=1))
            )
            second = SharedOrigin.of_source(
                ALICE_WORK, SourceRef(source_type="incident", source_id="i", locator=SourceSequenceLocator(sequence=2))
            )
            assert first.key != second.key

    asyncio.run(scenario())


def test_unsupported_modes_and_malformed_values_are_refused_by_name() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(ADMIN,)) as access:
            await _seed(access)
            shared = SharedUseAuthorization(access)

            # Stored rules decode to plain strings; supported values are accepted.
            restored = SharedUseTerms(
                target_scope_id=TARGET,
                origins=(SharedOrigin.of_artifact(ALICE_WORK, RETRY_TRACE),),
                uses=cast("frozenset[SharedUse]", frozenset({"evidence"})),
                audience_scope_ids=frozenset({TARGET}),
                retention=cast(SharedRetention, "independent"),
                permission_scope=cast(SharedPermissionScope, "logical"),
            )
            assert restored.retention is SharedRetention.INDEPENDENT

            for unsupported in (
                _terms(permission_scope=SharedPermissionScope.REVISION_ONLY),
                _terms(retention=SharedRetention.WITHHOLD_ON_REVOCATION),
            ):
                acceptance = await _accept(shared, ALICE, unsupported)
                with pytest.raises(AccessInvalidRequestError):
                    await shared.require(
                        RUNNER,
                        unsupported,
                        acceptances=(acceptance,),
                        use=SharedUse.EVIDENCE,
                        context=AUDIT,
                    )

            acceptance = await _accept(shared, ALICE, _terms())
            with pytest.raises(AccessInvalidRequestError):
                await shared.require(
                    RUNNER, _terms(), acceptances=(acceptance,), use=cast(SharedUse, "erase"), context=AUDIT
                )
            with pytest.raises(AccessInvalidRequestError):
                SharedUseTerms(
                    target_scope_id=TARGET,
                    origins=(SharedOrigin.of_artifact(ALICE_WORK, RETRY_TRACE),),
                    uses=frozenset({SharedUse.EVIDENCE}),
                    audience_scope_ids=frozenset({TARGET}),
                    retention=cast(SharedRetention, "erase_everything"),
                )
            with pytest.raises(AccessInvalidRequestError):
                # A hand-built acceptance missing its actor is refused, not a crash.
                SharedUseAcceptance(
                    terms_digest=_terms().digest,
                    accepted_by=cast(PrincipalRef, None),
                    origins=frozenset({"x"}),
                    audience_scope_ids=frozenset(),
                    accepted_at=datetime.now(UTC),
                )

    asyncio.run(scenario())


def test_decoded_terms_and_acceptance_containers_normalize() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(ADMIN,)) as access:
            await _seed(access)
            shared = SharedUseAuthorization(access)

            # A documented JSON terms shape decodes to lists, not frozensets.
            decoded = SharedUseTerms(
                target_scope_id=TARGET,
                origins=(SharedOrigin.of_artifact(ALICE_WORK, RETRY_TRACE),),
                uses=cast("frozenset[SharedUse]", frozenset({"evidence"})),
                audience_scope_ids=cast("frozenset[str]", [TARGET, AUDIENCE]),
            )
            assert decoded.audience_scope_ids == frozenset({TARGET, AUDIENCE})

            # Genuine acts, then rebuilt from the documented list shape.
            restored = [
                SharedUseAcceptance(
                    terms_digest=item.terms_digest,
                    accepted_by=item.accepted_by,
                    origins=cast("frozenset[str]", list(item.origins)),
                    audience_scope_ids=cast("frozenset[str]", list(item.audience_scope_ids)),
                    accepted_at=item.accepted_at,
                )
                for item in (
                    await _accept(shared, ALICE, decoded),
                    await _accept(shared, CAROL, decoded),
                    await _accept(shared, DAVE, decoded),
                )
            ]
            assert all(item.audience_scope_ids <= decoded.audience_scope_ids for item in restored)
            # No acceptance_id means the reader is never consulted.
            assert not any(item.durable for item in restored)

            grant = await shared.require(
                RUNNER, decoded, acceptances=tuple(restored), use=SharedUse.EVIDENCE, context=AUDIT
            )
            assert grant.terms_digest == decoded.digest

    asyncio.run(scenario())


def test_retained_target_conclusion_is_readable_without_origin_evidence_access() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(ADMIN,)) as access:
            await _seed(access)
            shared = SharedUseAuthorization(access)

            await access.require(MEMBER, AccessAction.SCOPE_READ, ResourceRef.scope(TARGET), context=AUDIT)
            with pytest.raises(AccessDeniedError):
                await shared.require_evidence(MEMBER, SharedOrigin.of_artifact(ALICE_WORK, RETRY_TRACE), context=AUDIT)
            await shared.require_evidence(REVIEWER, SharedOrigin.of_artifact(ALICE_WORK, RETRY_TRACE), context=AUDIT)

    asyncio.run(scenario())
