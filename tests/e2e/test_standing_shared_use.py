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

"""Standing shared use through the configured Access path, not the raw helper.

Each scenario opens the real Runtime, SQLite storage, Scope application, Source
journal and D's standing-rule record, then binds the same Server adapter
production binds. Assertions go through the public applications a product calls.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import pytest
from pydantic import ValidationError

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.profile.models import ProfileWriteContent
from powercontext.builtin.authz import (
    AccessAction,
    AccessAuditContext,
    AccessBinding,
    AccessControlService,
    AccessDeniedError,
    AccessIdentityRequiredError,
    AccessInvalidRequestError,
    AccessRole,
    CreateBinding,
    PrincipalRef,
    ResourceRef,
)
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.authz.dream import principal_identity
from powercontext.builtin.authz.standing_use import principal_key
from powercontext.builtin.dream.models import (
    CreateDreamRunRequest,
    DreamError,
    DreamPlan,
    GetDreamRunRequest,
)
from powercontext.builtin.evidence.models import ScopedEvidence
from powercontext.builtin.inference.models import GenerationResult, InferenceUsage
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    ApproveCandidateRequest,
    BuiltinConfig,
    CaptureSource,
    GetCandidateRequest,
    RuntimeConfig,
)
from powercontext.builtin.runtime.application import BuiltinRuntime
from powercontext.builtin.runtime.composition import open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft, ScopeSelection
from powercontext.builtin.sharing import (
    ShareAcceptanceSide,
    ShareRule,
    ShareRuleAcceptanceError,
    ShareRuleDraft,
    ShareRuleInvalidatedError,
    ShareRuleTerms,
)
from powercontext.builtin.sharing.service import ShareRuleService
from powercontext.server.dream_access import DreamAccess
from powercontext.server.standing_access import StandingAccess
from tests.e2e.dream_support import open_dream_runtime, process_pending

ADMIN = PrincipalRef(type="service", id="deployment-admin")
ALICE = PrincipalRef(type="user", id="alice")
BOB = PrincipalRef(type="user", id="bob")
CAROL = PrincipalRef(type="user", id="carol")
RUNNER = PrincipalRef(type="service", id="share-runner")
MEMBER = PrincipalRef(type="user", id="member")

AUDIT = AccessAuditContext(transport="background", operation="standing_use_test", request_id="req-standing")


def config(database: Path) -> BuiltinConfig:
    return BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"), runtime=RuntimeConfig())


@asynccontextmanager
async def composed(database: Path) -> AsyncIterator[tuple[BuiltinRuntime, AccessControlService, StandingAccess]]:
    """Compose the Runtime and Access together, as production wiring does.

    Both share one database file, and the standing checks are passed into the
    Runtime's composition seam rather than attached afterwards.
    """

    async with access_only(database) as access:
        adapter = StandingAccess(access)
        async with open_builtin_runtime(
            config(database),
            share_rule_authorizer=adapter.authorize_origin,
            share_rule_target_check=adapter.authorize_target,
            share_rule_acceptance_validator=adapter.validate_acceptance,
        ) as runtime:
            adapter.attach(runtime)
            yield runtime, access, adapter


@asynccontextmanager
async def bound_after_construction(
    database: Path,
) -> AsyncIterator[tuple[BuiltinRuntime, AccessControlService, StandingAccess]]:
    """The documented embedding path: build the Runtime, then bind the adapter."""

    async with access_only(database) as access:
        adapter = StandingAccess(access)
        async with open_builtin_runtime(config(database)) as runtime:
            adapter.bind(runtime)
            adapter.attach(runtime)
            yield runtime, access, adapter


@asynccontextmanager
async def access_only(database: Path) -> AsyncIterator[AccessControlService]:
    async with open_builtin_access_control(
        SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"), bootstrap_administrators=(ADMIN,)
    ) as access:
        yield access


@contextmanager
def run_as(principal: PrincipalRef) -> Iterator[None]:
    """Enter the authenticated context a real request establishes."""

    from powercontext.server.context import bind_principal, reset_principal

    token = bind_principal(principal)
    try:
        yield
    finally:
        reset_principal(token)


def _service(runtime: BuiltinRuntime) -> ShareRuleService:
    assert runtime.share_rules is not None
    return runtime.share_rules


def _sharing(runtime: BuiltinRuntime, scope_id: str):
    assert runtime.scopes is not None
    return runtime.sharing.for_scope(scope_id)


async def _three_scopes(runtime: BuiltinRuntime) -> tuple[str, str, str]:
    assert runtime.scopes is not None
    alice = (await runtime.scopes.create(ScopeDraft(title="Alice", summary="W", idempotency_key="a"))).scope_id
    bob = (await runtime.scopes.create(ScopeDraft(title="Bob", summary="W", idempotency_key="b"))).scope_id
    orders = (await runtime.scopes.create(ScopeDraft(title="Orders", summary="P", idempotency_key="o"))).scope_id
    return alice, bob, orders


async def _bind(access: AccessControlService, subject: PrincipalRef, scope_id: str, role: AccessRole, key: str):
    return await access.create_binding(
        ADMIN,
        CreateBinding(subject=subject, resource=ResourceRef.scope(scope_id), role=role, idempotency_key=key),
        context=AUDIT,
    )


async def _seed(access: AccessControlService, alice: str, bob: str, orders: str) -> dict[str, AccessBinding]:
    """Two contributors, one target owner, one executor, one outsider."""

    return {
        "alice": await _bind(access, ALICE, alice, AccessRole.SCOPE_ADMIN, "alice-origin"),
        "bob": await _bind(access, BOB, bob, AccessRole.SCOPE_ADMIN, "bob-origin"),
        "carol": await _bind(access, CAROL, orders, AccessRole.SCOPE_ADMIN, "carol-target"),
        "runner": await _bind(access, RUNNER, orders, AccessRole.SCOPE_CONTRIBUTOR, "runner-contributor"),
        "runner_alice": await _bind(access, RUNNER, alice, AccessRole.SCOPE_VIEWER, "runner-alice-reader"),
        "runner_bob": await _bind(access, RUNNER, bob, AccessRole.SCOPE_VIEWER, "runner-bob-reader"),
        "member": await _bind(access, MEMBER, orders, AccessRole.SCOPE_VIEWER, "member"),
    }


async def _trusted_freeform_accept(service: ShareRuleService, rule: ShareRule, origin_scope_id: str):
    """Reproduce the durable state an unconfigured trusted Runtime would have written.

    The validator is a bound-callback view, so this temporarily clears it on the
    service instance the test owns -- the same shape an unbound embedding produces.
    """

    validator = service._authorize_acceptance
    target_check = service._authorize_target
    service._authorize_acceptance = None
    service._authorize_target = None
    try:
        return await service.accept(
            rule.scope_id,
            rule.rule_id,
            rule.version,
            "origin",
            actor="legacy-alice",
            covered_scope_ids=(origin_scope_id,),
        )
    finally:
        service._authorize_acceptance = validator
        service._authorize_target = target_check


async def _accept(
    standing: StandingAccess,
    runtime: BuiltinRuntime,
    rule: ShareRule,
    side: ShareAcceptanceSide,
    covered: tuple[str, ...],
    actor: PrincipalRef,
):
    with run_as(actor):
        return await standing.accept(
            _service(runtime),
            rule,
            side,
            covered_scope_ids=covered,
        )


async def _use(
    standing: StandingAccess,
    runtime: BuiltinRuntime,
    rule: ShareRule,
    run_id: str,
    actor: PrincipalRef = RUNNER,
    use: str | None = None,
):
    with run_as(actor):
        return await standing.require_run_use(
            _service(runtime),
            scope_id=rule.scope_id,
            rule_id=rule.rule_id,
            version=rule.version,
            run_id=run_id,
            use=rule.terms.use if use is None else use,
        )


def _rule(
    alice: str,
    bob: str | None,
    *,
    terms: ShareRuleTerms | None = None,
    artifact_families: tuple[str, ...] = (),
) -> ShareRuleDraft:
    origins = (alice,) if bob is None else (alice, bob)
    return ShareRuleDraft(
        selection=ScopeSelection(mode="exact", scope_ids=origins),
        source_types=("content",),
        artifact_families=artifact_families,
        terms=terms or ShareRuleTerms(use="project-conclusion", audience="project-members", audience_scope_ids=()),
    )


def test_scope_wide_experience_range_accepts_scope_authority(tmp_path: Path) -> None:
    """A Scope-wide future-Artifact agreement needs Scope authority, not artifact.share.

    Reviewer failure 1: asking `artifact.share` of a Scope resource raised
    AccessInvalidRequestError, so no Scope-wide agreement could be recorded.
    """

    async def scenario() -> None:
        database = tmp_path / "artifact_range.db"
        async with composed(database) as (runtime, access, standing):
            alice, bob, orders = await _three_scopes(runtime)
            await _seed(access, alice, bob, orders)
            sharing = _sharing(runtime, orders)
            with run_as(CAROL):
                rule = await sharing.create(
                    _rule(
                        alice,
                        None,
                        artifact_families=("experience",),
                        terms=ShareRuleTerms(use="project-conclusion", audience="project-members"),
                    )
                )

            # Alice administers her own origin Scope, so she may agree to the range
            # that future Experiences inside it fall under.
            recorded = await _accept(standing, runtime, rule, "origin", (alice,), ALICE)
            assert recorded.covered_scope_ids == (alice,)
            assert recorded.actor == principal_key(ALICE)

            # Bob holds nothing there and cannot sign for that range.
            with run_as(BOB), pytest.raises(AccessDeniedError):
                await standing.standing.require_acceptance_authority(
                    BOB, rule, "origin", covered_scope_ids=(alice,), context=AUDIT
                )

            # The agreement admits a run over that Scope's material.
            await runtime.sources.for_scope(alice).capture(
                CaptureSource(source_id="fact", content="A fact.", metadata={})
            )
            await _accept(standing, runtime, rule, "target", (orders,), CAROL)
            with run_as(RUNNER):
                run = await sharing.resolve(rule.rule_id)
            await _use(standing, runtime, rule, run.run_id)

    asyncio.run(scenario())


def test_non_admin_audience_member_is_an_authorized_consumer(tmp_path: Path) -> None:
    """An explicit audience is membership of that Scope, not its administration.

    Reviewer failure 2: the runner held scope.read and scope.contribute at the
    target but not scope.admin, and admission refused it as though consumer
    participation were manager consent.
    """

    async def scenario() -> None:
        database = tmp_path / "audience.db"
        async with composed(database) as (runtime, access, standing):
            alice, bob, orders = await _three_scopes(runtime)
            await _seed(access, alice, bob, orders)
            sharing = _sharing(runtime, orders)
            with run_as(CAROL):
                rule = await sharing.create(
                    _rule(
                        alice,
                        bob,
                        terms=ShareRuleTerms(
                            use="project-conclusion",
                            audience="project-members",
                            audience_scope_ids=(orders,),
                        ),
                    )
                )
            await _accept(standing, runtime, rule, "origin", (alice,), ALICE)
            await _accept(standing, runtime, rule, "origin", (bob,), BOB)
            await _accept(standing, runtime, rule, "target", (orders,), CAROL)
            await runtime.sources.for_scope(alice).capture(
                CaptureSource(source_id="fact", content="A fact.", metadata={})
            )
            with run_as(RUNNER):
                run = await sharing.resolve(rule.rule_id)

            # The runner is a member consumer: read + contribute, no admin.
            assert not (
                await access.check(RUNNER, AccessAction.SCOPE_ADMIN, ResourceRef.scope(orders), context=AUDIT)
            ).allowed
            grant = await _use(standing, runtime, rule, run.run_id)
            assert grant.use == "project-conclusion" and grant.audience == "project-members"

            # An executor with no membership in the audience is still refused.
            # Read on the origin is not audience membership of the target.
            outsider = PrincipalRef(type="user", id="outsider")
            await _bind(access, outsider, alice, AccessRole.SCOPE_VIEWER, "outsider-reads-alice")
            with pytest.raises(AccessDeniedError):
                await _use(standing, runtime, rule, run.run_id, actor=outsider)

    asyncio.run(scenario())


def test_configured_binding_blocks_discovery_after_revocation(tmp_path: Path) -> None:
    """The bound origin callback really runs, and a withdrawn read stops discovery."""

    async def scenario() -> None:
        database = tmp_path / "discovery.db"
        async with composed(database) as (runtime, access, standing):
            alice, bob, orders = await _three_scopes(runtime)
            bindings = await _seed(access, alice, bob, orders)
            # The same binding call production makes, before any agreement.
            StandingAccess(access).bind(runtime)
            sharing = _sharing(runtime, orders)
            # Rule creation is a target-owned write: do it as the target owner.
            with run_as(CAROL):
                rule = await sharing.create(
                    _rule(
                        alice,
                        bob,
                        terms=ShareRuleTerms(
                            use="project-conclusion",
                            audience="project-members",
                            audience_scope_ids=(orders,),
                        ),
                    )
                )
            await _accept(standing, runtime, rule, "origin", (alice,), ALICE)
            await _accept(standing, runtime, rule, "origin", (bob,), BOB)
            await _accept(standing, runtime, rule, "target", (orders,), CAROL)
            await runtime.sources.for_scope(alice).capture(
                CaptureSource(source_id="fact", content="A fact.", metadata={})
            )

            # Inside the executor's authenticated context the bound callback admits it.
            with run_as(RUNNER):
                run = await sharing.resolve(rule.rule_id)
            assert [item.origin_scope_id for item in run.inputs] == [alice]

            # Withdrawing the executor's read on a required origin blocks discovery.
            await access.revoke_binding(
                ADMIN,
                bindings["runner_alice"].binding_id,
                expected_version=bindings["runner_alice"].version,
                idempotency_key="revoke-runner-alice",
                context=AUDIT,
            )
            await runtime.sources.for_scope(alice).capture(
                CaptureSource(source_id="second", content="More.", metadata={})
            )
            with run_as(RUNNER), pytest.raises(AccessDeniedError):
                await sharing.resolve(rule.rule_id)

    asyncio.run(scenario())


def test_run_use_checks_head_acceptors_and_current_authority_together(tmp_path: Path) -> None:
    """One entrypoint from authoritative ids: use, acceptors and authority at once."""

    async def scenario() -> None:
        database = tmp_path / "run_use.db"
        async with composed(database) as (runtime, access, standing):
            alice, bob, orders = await _three_scopes(runtime)
            bindings = await _seed(access, alice, bob, orders)
            sharing = _sharing(runtime, orders)
            with run_as(CAROL):
                rule = await sharing.create(
                    _rule(
                        alice,
                        bob,
                        terms=ShareRuleTerms(
                            use="project-conclusion",
                            audience="project-members",
                            audience_scope_ids=(orders,),
                        ),
                    )
                )
            acceptances = (
                await _accept(standing, runtime, rule, "origin", (alice,), ALICE),
                await _accept(standing, runtime, rule, "origin", (bob,), BOB),
                await _accept(standing, runtime, rule, "target", (orders,), CAROL),
            )
            await runtime.sources.for_scope(alice).capture(
                CaptureSource(source_id="fact", content="A fact.", metadata={})
            )
            with run_as(RUNNER):
                run = await sharing.resolve(rule.rule_id)
            grant = await _use(standing, runtime, rule, run.run_id)
            # Only the records that actually admitted this manifest are reported: the
            # run reached Alice alone, plus the target consent it declares. Bob's
            # origin consent is not part of this run's authority.
            alice_origin, bob_origin, carol_target = (item.acceptance_id for item in acceptances)
            assert set(grant.acceptance_ids) == {alice_origin, carol_target}
            assert bob_origin not in grant.acceptance_ids

            # A different requested use is not silently treated as supported.
            with pytest.raises(AccessInvalidRequestError):
                await _use(standing, runtime, rule, run.run_id, use="anything-else")

            # Withdrawing the authority that admitted a *required* origin blocks the
            # same single call. Alice is the one this run actually reached.
            await access.revoke_binding(
                ADMIN,
                bindings["alice"].binding_id,
                expected_version=bindings["alice"].version,
                idempotency_key="revoke-alice",
                context=AUDIT,
            )
            with pytest.raises(AccessDeniedError):
                await _use(standing, runtime, rule, run.run_id)

    asyncio.run(scenario())


def test_later_fact_needs_no_reacceptance_and_invalidation_closes_the_rule(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "later.db"
        async with composed(database) as (runtime, access, standing):
            alice, bob, orders = await _three_scopes(runtime)
            await _seed(access, alice, bob, orders)
            sharing = _sharing(runtime, orders)
            with run_as(CAROL):
                rule = await sharing.create(
                    _rule(
                        alice,
                        bob,
                        terms=ShareRuleTerms(
                            use="project-conclusion",
                            audience="project-members",
                            audience_scope_ids=(orders,),
                        ),
                    )
                )
            await _accept(standing, runtime, rule, "origin", (alice,), ALICE)
            await _accept(standing, runtime, rule, "origin", (bob,), BOB)
            await _accept(standing, runtime, rule, "target", (orders,), CAROL)
            await runtime.sources.for_scope(alice).capture(
                CaptureSource(source_id="first", content="First.", metadata={})
            )
            with run_as(RUNNER):
                first = await sharing.resolve(rule.rule_id)

            # Material captured later is eligible without anyone re-accepting.
            await runtime.sources.for_scope(bob).capture(
                CaptureSource(source_id="later", content="Counterexample.", metadata={})
            )
            with run_as(RUNNER):
                later = await sharing.resolve(rule.rule_id)
            assert sorted(item.origin_scope_id for item in later.inputs) == sorted([alice, bob])
            await _use(standing, runtime, rule, later.run_id)
            # The earlier evaluation still describes exactly what it pinned.
            await _use(standing, runtime, rule, first.run_id)

            # An invalidated rule is not a live agreement.
            with run_as(CAROL):
                await sharing.invalidate(rule.rule_id, rule.version)
            with pytest.raises(ShareRuleInvalidatedError):
                await _use(standing, runtime, rule, later.run_id)

    asyncio.run(scenario())


def test_unused_contributor_withdrawal_does_not_veto_the_pinned_run(tmp_path: Path) -> None:
    """Only the acceptors whose coverage this run needed are re-checked.

    A run that reached only Alice must keep working after Bob's unused origin
    authority is withdrawn; the same withdrawal blocks a run that really reached him.
    """

    async def scenario() -> None:
        database = tmp_path / "unused.db"
        async with composed(database) as (runtime, access, standing):
            alice, bob, orders = await _three_scopes(runtime)
            bindings = await _seed(access, alice, bob, orders)
            sharing = _sharing(runtime, orders)
            with run_as(CAROL):
                rule = await sharing.create(
                    _rule(
                        alice,
                        bob,
                        terms=ShareRuleTerms(
                            use="project-conclusion",
                            audience="project-members",
                            audience_scope_ids=(orders,),
                        ),
                    )
                )
            await _accept(standing, runtime, rule, "origin", (alice,), ALICE)
            await _accept(standing, runtime, rule, "origin", (bob,), BOB)
            await _accept(standing, runtime, rule, "target", (orders,), CAROL)

            # Only Alice has captured anything, so the pinned run reaches her alone.
            await runtime.sources.for_scope(alice).capture(
                CaptureSource(source_id="alice-only", content="Alice's fact.", metadata={})
            )
            with run_as(RUNNER):
                alice_only = await sharing.resolve(rule.rule_id)
            assert alice_only.origin_scopes() == (alice,)
            await _use(standing, runtime, rule, alice_only.run_id)

            await access.revoke_binding(
                ADMIN,
                bindings["bob"].binding_id,
                expected_version=bindings["bob"].version,
                idempotency_key="revoke-unused-bob",
                context=AUDIT,
            )

            # Bob never contributed an origin this run used, so it stays admissible.
            await _use(standing, runtime, rule, alice_only.run_id)

            # A run that really reached Bob is blocked by the same withdrawal.
            await runtime.sources.for_scope(bob).capture(
                CaptureSource(source_id="bob-fact", content="Bob's counterexample.", metadata={})
            )
            with run_as(RUNNER):
                both = await sharing.resolve(rule.rule_id)
            assert sorted(both.origin_scopes()) == sorted([alice, bob])
            with pytest.raises(AccessDeniedError):
                await _use(standing, runtime, rule, both.run_id)

    asyncio.run(scenario())


def test_grant_reports_only_the_consent_this_pinned_run_used(tmp_path: Path) -> None:
    """F1: identity and consent belong to THIS manifest, not the version's union.

    Run 1 pins Alice alone. After Bob captures material, run 2 pins both. Run 1's
    grant must still report only Alice's origin and only the records that admitted
    it -- a later run, and a version v2 elsewhere, cannot pollute that projection.
    """

    async def scenario() -> None:
        database = tmp_path / "grant_identity.db"
        async with composed(database) as (runtime, access, standing):
            alice, bob, orders = await _three_scopes(runtime)
            await _seed(access, alice, bob, orders)
            sharing = _sharing(runtime, orders)
            with run_as(CAROL):
                rule = await sharing.create(
                    _rule(
                        alice,
                        bob,
                        terms=ShareRuleTerms(
                            use="project-conclusion",
                            audience="project-members",
                            audience_scope_ids=(orders,),
                        ),
                    )
                )
            alice_consent = await _accept(standing, runtime, rule, "origin", (alice,), ALICE)
            bob_consent = await _accept(standing, runtime, rule, "origin", (bob,), BOB)
            carol_consent = await _accept(standing, runtime, rule, "target", (orders,), CAROL)

            await runtime.sources.for_scope(alice).capture(
                CaptureSource(source_id="fact", content="A fact.", metadata={})
            )
            with run_as(RUNNER):
                first = await sharing.resolve(rule.rule_id)
            assert first.origin_scopes() == (alice,)
            grant = await _use(standing, runtime, rule, first.run_id)
            assert grant.run_origin_scope_ids == (alice,)
            assert set(grant.acceptance_ids) == {alice_consent.acceptance_id, carol_consent.acceptance_id}
            assert bob_consent.acceptance_id not in grant.acceptance_ids

            # Bob's later material belongs to a new run, not to run 1.
            await runtime.sources.for_scope(bob).capture(
                CaptureSource(source_id="later", content="Bob's counterexample.", metadata={})
            )
            with run_as(RUNNER):
                second = await sharing.resolve(rule.rule_id)
            assert sorted(second.origin_scopes()) == sorted([alice, bob])

            again = await _use(standing, runtime, rule, first.run_id)
            assert again.run_origin_scope_ids == (alice,)
            assert bob_consent.acceptance_id not in again.acceptance_ids
            # Run 2 legitimately needs both contributors.
            both = await _use(standing, runtime, rule, second.run_id)
            assert bob_consent.acceptance_id in both.acceptance_ids

            # A v2 elsewhere cannot pollute v1's projection.
            with run_as(CAROL):
                v2 = await sharing.update(
                    rule.rule_id,
                    1,
                    _rule(
                        alice,
                        bob,
                        terms=ShareRuleTerms(
                            use="project-conclusion",
                            audience="project-members",
                            audience_scope_ids=(orders,),
                        ),
                    ),
                )
            assert v2.version == 2
            # A superseded version is no longer a live agreement, so v2 cannot be used
            # to keep admitting v1's run either -- the head check refuses explicitly.
            with pytest.raises(ShareRuleInvalidatedError):
                await _use(standing, runtime, rule, first.run_id)

    asyncio.run(scenario())


def test_subtree_root_admits_child_only_with_authority_at_the_child(tmp_path: Path) -> None:
    """Structural coverage is selection, never permission.

    An accepted range root admits a descendant only when the accepting actor holds
    current authority AT that descendant. Root administration alone must deny.
    """

    async def scenario() -> None:
        database = tmp_path / "subtree_consent.db"
        async with composed(database) as (runtime, access, standing):
            assert runtime.scopes is not None
            root = (await runtime.scopes.create(ScopeDraft(title="Root", summary="W", idempotency_key="r"))).scope_id
            child = (
                await runtime.scopes.create(
                    ScopeDraft(title="Child", summary="W", parent_scope_id=root, idempotency_key="c")
                )
            ).scope_id
            orders = (
                await runtime.scopes.create(ScopeDraft(title="Orders", summary="P", idempotency_key="o"))
            ).scope_id
            await _bind(access, ALICE, root, AccessRole.SCOPE_ADMIN, "alice-root")
            await _bind(access, CAROL, orders, AccessRole.SCOPE_ADMIN, "carol")
            await _bind(access, RUNNER, orders, AccessRole.SCOPE_CONTRIBUTOR, "runner")
            await _bind(access, RUNNER, child, AccessRole.SCOPE_VIEWER, "runner-child-reader")
            sharing = _sharing(runtime, orders)
            with run_as(CAROL):
                rule = await sharing.create(
                    ShareRuleDraft(
                        selection=ScopeSelection(mode="subtree", root_scope_id=root),
                        source_types=("content",),
                        terms=ShareRuleTerms(
                            use="project-conclusion",
                            audience="project-members",
                            audience_scope_ids=(orders,),
                        ),
                    )
                )
            await _accept(standing, runtime, rule, "origin", (root,), ALICE)
            await _accept(standing, runtime, rule, "target", (orders,), CAROL)
            await runtime.sources.for_scope(child).capture(
                CaptureSource(source_id="child-fact", content="Agreed descendant.", metadata={})
            )
            with run_as(RUNNER):
                run = await sharing.resolve(rule.rule_id)
            assert run.origin_scopes() == (child,)

            # NEGATIVE: root administration alone is not authority at the child.
            with pytest.raises(AccessDeniedError):
                await _use(standing, runtime, rule, run.run_id)

            # POSITIVE: the same actor with current authority AT the child admits it.
            await _bind(access, ALICE, child, AccessRole.SCOPE_ADMIN, "alice-child")
            grant = await _use(standing, runtime, rule, run.run_id)
            assert grant.run_origin_scope_ids == (child,)

    asyncio.run(scenario())


def _binding_query(scope_id: str):
    from powercontext.builtin.authz import BindingSearchRequest

    return BindingSearchRequest(management_resource=ResourceRef.scope(scope_id))


def test_unreadable_legacy_consent_does_not_veto_a_valid_canonical_one(tmp_path: Path) -> None:
    """A legacy free-form actor must not abort admission for a valid canonical record.

    Supported state: an unconfigured trusted Runtime writes free-form rows, then the
    same database reopens with the enforced adapter and real actors record canonical
    rows. One valid required consent suffices; an unreadable one is not a candidate.
    A sole unreadable consent still denies clearly.
    """

    async def scenario() -> None:
        database = tmp_path / "mixed_state.db"
        async with composed(database) as (runtime, access, standing):
            alice, bob, orders = await _three_scopes(runtime)
            await _seed(access, alice, bob, orders)
            sharing = _sharing(runtime, orders)
            with run_as(CAROL):
                rule = await sharing.create(
                    _rule(
                        alice,
                        bob,
                        terms=ShareRuleTerms(
                            use="project-conclusion",
                            audience="project-members",
                            audience_scope_ids=(orders,),
                        ),
                    )
                )
            service = _service(runtime)
            # A free-form row, exactly what the unconfigured trusted path stores. Written
            # through the service directly to reproduce that durable state; the enforced
            # validator would refuse such a spelling (covered by the F7 tests).
            assert _service(runtime) is service
            legacy = await _trusted_freeform_accept(service, rule, alice)
            # A genuine authenticated acceptance for the same required origin.
            canonical = await _accept(standing, runtime, rule, "origin", (alice,), ALICE)
            await _accept(standing, runtime, rule, "target", (orders,), CAROL)

            await runtime.sources.for_scope(alice).capture(
                CaptureSource(source_id="fact", content="A fact.", metadata={})
            )
            with run_as(RUNNER):
                run = await sharing.resolve(rule.rule_id)

            # Admission does not depend on which row D returns first.
            grant = await _use(standing, runtime, rule, run.run_id)
            assert canonical.acceptance_id in grant.acceptance_ids
            assert len(grant.acceptance_ids) == len(set(grant.acceptance_ids))

            # With only the unreadable record left, the run is clearly denied.
            alice_bindings = await access.list_bindings(ADMIN, _binding_query(alice), context=AUDIT)
            alice_binding = next(
                item for item in alice_bindings.items if item.subject == ALICE and item.role is AccessRole.SCOPE_ADMIN
            )
            await access.revoke_binding(
                ADMIN,
                alice_binding.binding_id,
                expected_version=alice_binding.version,
                idempotency_key="revoke-canonical-origin",
                context=AUDIT,
            )
            with pytest.raises(AccessDeniedError):
                await _use(standing, runtime, rule, run.run_id)
            assert legacy.acceptance_id

    asyncio.run(scenario())


async def _park_inside_contribution(park_run_id: str, parked, release):
    """Park the parked run inside its first contribution read, before the reader runs.

    Patches the authorizer class the test drives, exactly like the review gate, and
    restores it afterwards. The underlying read is the real one.
    """

    from powercontext.builtin.authz.standing_use import StandingUseAuthorization

    original = StandingUseAuthorization._contribution
    armed = True

    async def gate(self, service, rule, run, acceptance_id, *rest):
        nonlocal armed
        if armed and run.run_id == park_run_id:
            armed = False
            parked.set()
            await release.wait()
        return await original(self, service, rule, run, acceptance_id, *rest)

    # The signature differs between the pre-fix and fixed readers; the test only
    # observes scheduling, so the assignment is necessarily untyped here.
    StandingUseAuthorization._contribution = gate  # ty: ignore[invalid-assignment]
    return original


def test_concurrent_admissions_on_one_authorizer_keep_their_own_memo(tmp_path: Path) -> None:
    """Two in-flight admissions on the SAME authorizer must not share contributions.

    A per-invocation memo belongs to its call. When the memo lived on the instance, a
    second admission replaced it mid-flight and the first then read a contribution
    computed for the other run, denying a lawfully admissible manifest.
    """

    async def scenario() -> None:
        database = tmp_path / "concurrent.db"
        async with composed(database) as (runtime, access, standing):
            assert runtime.scopes is not None
            root = (await runtime.scopes.create(ScopeDraft(title="Root", summary="W", idempotency_key="r"))).scope_id
            children = [
                (
                    await runtime.scopes.create(
                        ScopeDraft(title=f"C{i}", summary="W", parent_scope_id=root, idempotency_key=f"c{i}")
                    )
                ).scope_id
                for i in range(2)
            ]
            orders = (
                await runtime.scopes.create(ScopeDraft(title="Orders", summary="P", idempotency_key="o"))
            ).scope_id
            await _bind(access, ALICE, root, AccessRole.SCOPE_ADMIN, "alice-root")
            for index, child in enumerate(children):
                await _bind(access, ALICE, child, AccessRole.SCOPE_ADMIN, f"alice-child-{index}")
                await _bind(access, RUNNER, child, AccessRole.SCOPE_VIEWER, f"runner-child-{index}")
            await _bind(access, CAROL, orders, AccessRole.SCOPE_ADMIN, "carol")
            await _bind(access, RUNNER, orders, AccessRole.SCOPE_CONTRIBUTOR, "runner")
            sharing = _sharing(runtime, orders)
            with run_as(CAROL):
                rule = await sharing.create(
                    ShareRuleDraft(
                        selection=ScopeSelection(mode="subtree", root_scope_id=root),
                        source_types=("content",),
                        terms=ShareRuleTerms(
                            use="project-conclusion",
                            audience="project-members",
                            audience_scope_ids=(orders,),
                        ),
                    )
                )
            await _accept(standing, runtime, rule, "origin", (root,), ALICE)
            await _accept(standing, runtime, rule, "target", (orders,), CAROL)

            # Small manifest first (one child), then a second pin reaching both.
            await runtime.sources.for_scope(children[0]).capture(
                CaptureSource(source_id="small", content="Small manifest fact.", metadata={})
            )
            with run_as(RUNNER):
                small = await sharing.resolve(rule.rule_id)
            await runtime.sources.for_scope(children[1]).capture(
                CaptureSource(source_id="large", content="Large manifest fact.", metadata={})
            )
            with run_as(RUNNER):
                large = await sharing.resolve(rule.rule_id)
            assert small.origin_scopes() == (children[0],)
            assert sorted(large.origin_scopes()) == sorted(children)

            service = _service(runtime)

            async def admit(service_view, run_id):
                with run_as(RUNNER):
                    return await standing.require_run_use(
                        service_view,
                        scope_id=orders,
                        rule_id=rule.rule_id,
                        version=rule.version,
                        run_id=run_id,
                        use=rule.terms.use,
                    )

            # Serial control: both are lawfully admissible at this state.
            small_grant = await admit(service, small.run_id)
            large_grant = await admit(service, large.run_id)
            assert small_grant.run_origin_scope_ids == (children[0],)
            assert sorted(large_grant.run_origin_scope_ids) == sorted(children)

            # Forced interleaving on the same authorizer: the large admission parks at
            # its first contribution read, the small admission completes, then it resumes.
            parked = asyncio.Event()
            release = asyncio.Event()
            original = await _park_inside_contribution(large.run_id, parked, release)
            try:
                big = asyncio.create_task(admit(service, large.run_id))
                await asyncio.wait_for(parked.wait(), timeout=30)
                concurrent_small = await admit(service, small.run_id)
                release.set()
                concurrent_large = await asyncio.wait_for(big, timeout=30)
            finally:
                from powercontext.builtin.authz.standing_use import StandingUseAuthorization

                StandingUseAuthorization._contribution = original

            assert concurrent_small.run_origin_scope_ids == (children[0],)
            # The lawfully admissible large manifest stays admissible.
            assert sorted(concurrent_large.run_origin_scope_ids) == sorted(children)
            assert concurrent_large.terms_digest == large_grant.terms_digest

            # Control: without the second child's authority the large manifest denies,
            # while the small one is unaffected.
            index = children.index(children[1])
            bindings = await access.list_bindings(ADMIN, _binding_query(children[index]), context=AUDIT)
            binding = next(item for item in bindings.items if item.subject == ALICE)
            await access.revoke_binding(
                ADMIN,
                binding.binding_id,
                expected_version=binding.version,
                idempotency_key="revoke-second-child",
                context=AUDIT,
            )
            assert (await admit(service, small.run_id)).run_origin_scope_ids == (children[0],)
            with pytest.raises(AccessDeniedError):
                await admit(service, large.run_id)

    asyncio.run(scenario())


def test_grant_reports_one_record_once_for_multiple_origins(tmp_path: Path) -> None:
    """A root record covering several origins is reported once, not once per origin."""

    async def scenario() -> None:
        database = tmp_path / "dedup.db"
        async with composed(database) as (runtime, access, standing):
            assert runtime.scopes is not None
            root = (await runtime.scopes.create(ScopeDraft(title="Root", summary="W", idempotency_key="r"))).scope_id
            children = [
                (
                    await runtime.scopes.create(
                        ScopeDraft(title=f"C{i}", summary="W", parent_scope_id=root, idempotency_key=f"c{i}")
                    )
                ).scope_id
                for i in range(3)
            ]
            orders = (
                await runtime.scopes.create(ScopeDraft(title="Orders", summary="P", idempotency_key="o"))
            ).scope_id
            await _bind(access, ALICE, root, AccessRole.SCOPE_ADMIN, "alice-root")
            for index, child in enumerate(children):
                await _bind(access, ALICE, child, AccessRole.SCOPE_ADMIN, f"alice-child-{index}")
                await _bind(access, RUNNER, child, AccessRole.SCOPE_VIEWER, f"runner-child-{index}")
            await _bind(access, CAROL, orders, AccessRole.SCOPE_ADMIN, "carol")
            await _bind(access, RUNNER, orders, AccessRole.SCOPE_CONTRIBUTOR, "runner")
            sharing = _sharing(runtime, orders)
            with run_as(CAROL):
                rule = await sharing.create(
                    ShareRuleDraft(
                        selection=ScopeSelection(mode="subtree", root_scope_id=root),
                        source_types=("content",),
                        terms=ShareRuleTerms(
                            use="project-conclusion",
                            audience="project-members",
                            audience_scope_ids=(orders,),
                        ),
                    )
                )
            # ONE root acceptance covering all three descendants.
            root_consent = await _accept(standing, runtime, rule, "origin", (root,), ALICE)
            target_consent = await _accept(standing, runtime, rule, "target", (orders,), CAROL)
            for index, child in enumerate(children):
                await runtime.sources.for_scope(child).capture(
                    CaptureSource(source_id=f"fact{index}", content="Fact.", metadata={})
                )
            with run_as(RUNNER):
                run = await sharing.resolve(rule.rule_id)
            assert sorted(run.origin_scopes()) == sorted(children)

            grant = await _use(standing, runtime, rule, run.run_id)
            assert len(grant.acceptance_ids) == len(set(grant.acceptance_ids))
            assert set(grant.acceptance_ids) == {root_consent.acceptance_id, target_consent.acceptance_id}

    asyncio.run(scenario())


def test_redundant_invalid_acceptor_does_not_veto_a_valid_one(tmp_path: Path) -> None:
    """Two records cover the same required origin: one valid is enough."""

    async def scenario() -> None:
        database = tmp_path / "redundant.db"
        async with composed(database) as (runtime, access, standing):
            alice, bob, orders = await _three_scopes(runtime)
            bindings = await _seed(access, alice, bob, orders)
            await _bind(access, MEMBER, alice, AccessRole.SCOPE_ADMIN, "member-alice-admin")
            sharing = _sharing(runtime, orders)
            with run_as(CAROL):
                rule = await sharing.create(
                    _rule(
                        alice,
                        bob,
                        terms=ShareRuleTerms(
                            use="project-conclusion",
                            audience="project-members",
                            audience_scope_ids=(orders,),
                        ),
                    )
                )
            alice_consent = await _accept(standing, runtime, rule, "origin", (alice,), ALICE)
            # A second, redundant consent for the same origin.
            member_consent = await _accept(standing, runtime, rule, "origin", (alice,), MEMBER)
            await _accept(standing, runtime, rule, "target", (orders,), CAROL)
            await runtime.sources.for_scope(alice).capture(
                CaptureSource(source_id="fact", content="A fact.", metadata={})
            )
            with run_as(RUNNER):
                run = await sharing.resolve(rule.rule_id)

            # Losing the redundant acceptor leaves the run admissible.
            member_binding = await access.list_bindings(ADMIN, _binding_query(alice), context=AUDIT)
            redundant = next(
                item for item in member_binding.items if item.subject == MEMBER and item.role is AccessRole.SCOPE_ADMIN
            )
            await access.revoke_binding(
                ADMIN,
                redundant.binding_id,
                expected_version=redundant.version,
                idempotency_key="revoke-redundant",
                context=AUDIT,
            )
            grant = await _use(standing, runtime, rule, run.run_id)
            assert alice_consent.acceptance_id in grant.acceptance_ids
            assert member_consent.acceptance_id not in grant.acceptance_ids

            # Losing the only valid consent for that origin denies.
            await access.revoke_binding(
                ADMIN,
                bindings["alice"].binding_id,
                expected_version=bindings["alice"].version,
                idempotency_key="revoke-only-consent",
                context=AUDIT,
            )
            with pytest.raises(AccessDeniedError):
                await _use(standing, runtime, rule, run.run_id)

    asyncio.run(scenario())


def test_post_construction_bind_enforces_the_same_acceptance_contract(tmp_path: Path) -> None:
    """Binding after construction must enforce acceptance exactly like the factory.

    This is the documented embedding path, not an unconfigured local SDK: the
    Runtime is built plainly and the adapter is bound afterwards. A target
    contributor naming the origin admin and the target owner must record nothing.
    """

    async def scenario() -> None:
        database = tmp_path / "post_bind.db"
        async with bound_after_construction(database) as (runtime, access, standing):
            alice, bob, orders = await _three_scopes(runtime)
            await _seed(access, alice, bob, orders)
            sharing = _sharing(runtime, orders)
            with run_as(CAROL):
                rule = await sharing.create(
                    _rule(
                        alice,
                        bob,
                        terms=ShareRuleTerms(
                            use="project-conclusion",
                            audience="project-members",
                            audience_scope_ids=(orders,),
                        ),
                    )
                )

            # FORGED: a target contributor naming other principals writes nothing.
            with run_as(RUNNER):
                with pytest.raises(AccessDeniedError):
                    await sharing.accept(rule.rule_id, rule.version, "origin", actor="user:alice")
                with pytest.raises(AccessDeniedError):
                    await sharing.accept(rule.rule_id, rule.version, "target", actor="user:carol")
            assert await sharing.agree(rule.rule_id, rule.version) == ()

            # LEGITIMATE: each authenticated approver records its own part.
            with run_as(ALICE):
                await sharing.accept(
                    rule.rule_id,
                    rule.version,
                    "origin",
                    actor=principal_key(ALICE),
                    covered_scope_ids=(alice,),
                )
            with run_as(BOB):
                await sharing.accept(
                    rule.rule_id,
                    rule.version,
                    "origin",
                    actor=principal_key(BOB),
                    covered_scope_ids=(bob,),
                )
            with run_as(CAROL):
                await sharing.accept(
                    rule.rule_id,
                    rule.version,
                    "target",
                    actor=principal_key(CAROL),
                    covered_scope_ids=(orders,),
                )
            recorded = await sharing.agree(rule.rule_id, rule.version)
            assert {item.actor for item in recorded} == {principal_key(ALICE), principal_key(BOB), principal_key(CAROL)}

            # MISSING PRINCIPAL: fails closed on the same public path.
            with pytest.raises(AccessIdentityRequiredError):
                await sharing.accept(rule.rule_id, rule.version, "origin", actor=principal_key(ALICE))

            await runtime.sources.for_scope(alice).capture(
                CaptureSource(source_id="fact", content="A fact.", metadata={})
            )
            with run_as(RUNNER):
                run = await sharing.resolve(rule.rule_id)
            await _use(standing, runtime, rule, run.run_id)

    asyncio.run(scenario())


def test_public_accept_path_refuses_impersonation_and_unknown_actor_forms(tmp_path: Path) -> None:
    """Every supported acceptance write is bound to the authenticated caller.

    The public ScopedShareApplication path -- not only the hand-held helper -- must
    refuse a caller who names somebody else, and must refuse an identity form it
    cannot vouch for before any durable row exists.
    """

    async def scenario() -> None:
        database = tmp_path / "accept_paths.db"
        async with composed(database) as (runtime, access, standing):
            alice, bob, orders = await _three_scopes(runtime)
            await _seed(access, alice, bob, orders)
            sharing = _sharing(runtime, orders)
            with run_as(CAROL):
                rule = await sharing.create(
                    _rule(
                        alice,
                        bob,
                        terms=ShareRuleTerms(
                            use="project-conclusion",
                            audience="project-members",
                            audience_scope_ids=(orders,),
                        ),
                    )
                )

            # RUNNER may write the target but administers neither origin, so naming
            # Alice or Carol through the public path must record nothing.
            with run_as(RUNNER):
                with pytest.raises(AccessDeniedError):
                    await sharing.accept(rule.rule_id, rule.version, "origin", actor="user:alice")
                with pytest.raises(AccessDeniedError):
                    await sharing.accept(rule.rule_id, rule.version, "target", actor="user:carol")
            assert await sharing.agree(rule.rule_id, rule.version) == ()

            # An identity form this boundary cannot read is refused before writing,
            # not persisted as an unreadable enforced record.
            with run_as(ALICE):
                with pytest.raises(AccessDeniedError):
                    await sharing.accept(rule.rule_id, rule.version, "origin", actor="alice")
                # The canonical spelling is the authenticated caller and is recorded,
                # covering only the origin this actor really administers.
                recorded = await sharing.accept(
                    rule.rule_id,
                    rule.version,
                    "origin",
                    actor=principal_key(ALICE),
                    covered_scope_ids=(alice,),
                )
            assert recorded.actor == principal_key(ALICE)
            assert [item.actor for item in await sharing.agree(rule.rule_id, rule.version)] == [principal_key(ALICE)]

            # The legitimate separate approvers still work through the public path.
            with run_as(BOB):
                await sharing.accept(
                    rule.rule_id,
                    rule.version,
                    "origin",
                    actor=principal_key(BOB),
                    covered_scope_ids=(bob,),
                )
            with run_as(CAROL):
                await sharing.accept(
                    rule.rule_id,
                    rule.version,
                    "target",
                    actor=principal_key(CAROL),
                    covered_scope_ids=(orders,),
                )
            await runtime.sources.for_scope(alice).capture(
                CaptureSource(source_id="fact", content="A fact.", metadata={})
            )
            with run_as(RUNNER):
                run = await sharing.resolve(rule.rule_id)
            await _use(standing, runtime, rule, run.run_id)

            # Missing identity still fails closed on the public path.
            with pytest.raises(AccessIdentityRequiredError):
                await sharing.accept(rule.rule_id, rule.version, "origin", actor=principal_key(BOB))

    asyncio.run(scenario())


def test_extra_exact_origin_outside_the_range_root_is_its_own_authority(tmp_path: Path) -> None:
    """A pinned run that reached only an exact extra Scope needs that Scope's acceptor.

    The rule selects a subtree root plus an exact Scope outside it. When the run
    reaches only that extra Scope, the unused subtree root's authority cannot veto
    it -- but revoking the extra Scope's own acceptor still blocks it.
    """

    async def scenario() -> None:
        database = tmp_path / "extra.db"
        async with composed(database) as (runtime, access, standing):
            assert runtime.scopes is not None
            alice_root = (
                await runtime.scopes.create(ScopeDraft(title="Alice", summary="W", idempotency_key="ar"))
            ).scope_id
            alice_child = (
                await runtime.scopes.create(
                    ScopeDraft(title="Child", summary="W", parent_scope_id=alice_root, idempotency_key="ac")
                )
            ).scope_id
            bob_extra = (
                await runtime.scopes.create(ScopeDraft(title="Bob", summary="W", idempotency_key="bx"))
            ).scope_id
            orders = (
                await runtime.scopes.create(ScopeDraft(title="Orders", summary="P", idempotency_key="o"))
            ).scope_id
            # The child is structural, so the root's range really does qualify it; this
            # scenario is about an extra exact Scope outside that range, not about
            # whether the expansion itself works.
            child_descriptor = await runtime.scopes.get(alice_child)
            assert child_descriptor.parent_scope_id == alice_root
            assert runtime.sharing is not None
            bindings = {
                "alice": await _bind(access, ALICE, alice_root, AccessRole.SCOPE_ADMIN, "alice-root"),
                "bob": await _bind(access, BOB, bob_extra, AccessRole.SCOPE_ADMIN, "bob-extra"),
                "carol": await _bind(access, CAROL, orders, AccessRole.SCOPE_ADMIN, "carol"),
                "runner": await _bind(access, RUNNER, orders, AccessRole.SCOPE_CONTRIBUTOR, "runner"),
            }
            await _bind(access, RUNNER, bob_extra, AccessRole.SCOPE_VIEWER, "runner-bob")
            sharing = _sharing(runtime, orders)
            with run_as(CAROL):
                rule = await sharing.create(
                    ShareRuleDraft(
                        selection=ScopeSelection(mode="subtree", root_scope_id=alice_root),
                        exact_scope_ids=(bob_extra,),
                        source_types=("content",),
                        terms=ShareRuleTerms(
                            use="project-conclusion",
                            audience="project-members",
                            audience_scope_ids=(orders,),
                        ),
                    )
                )
            await _accept(standing, runtime, rule, "origin", (alice_root,), ALICE)
            await _accept(standing, runtime, rule, "origin", (bob_extra,), BOB)
            await _accept(standing, runtime, rule, "target", (orders,), CAROL)

            # Only the extra Scope has material, so the pinned run reached it alone.
            await runtime.sources.for_scope(bob_extra).capture(
                CaptureSource(source_id="extra-only", content="Outside the range.", metadata={})
            )
            with run_as(RUNNER):
                run = await sharing.resolve(rule.rule_id)
            assert run.origin_scopes() == (bob_extra,)
            await _use(standing, runtime, rule, run.run_id)

            # The unused subtree root's authority is irrelevant to this run.
            await access.revoke_binding(
                ADMIN,
                bindings["alice"].binding_id,
                expected_version=bindings["alice"].version,
                idempotency_key="revoke-unused-root",
                context=AUDIT,
            )
            await _use(standing, runtime, rule, run.run_id)

            # Revoking the extra Scope's own acceptor blocks that same run.
            await access.revoke_binding(
                ADMIN,
                bindings["bob"].binding_id,
                expected_version=bindings["bob"].version,
                idempotency_key="revoke-extra-acceptor",
                context=AUDIT,
            )
            with pytest.raises(AccessDeniedError):
                await _use(standing, runtime, rule, run.run_id)

    asyncio.run(scenario())


def test_one_contributor_cannot_admit_a_run_that_reached_another(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "compose.db"
        async with composed(database) as (runtime, access, standing):
            alice, bob, orders = await _three_scopes(runtime)
            await _seed(access, alice, bob, orders)
            sharing = _sharing(runtime, orders)
            with run_as(CAROL):
                rule = await sharing.create(
                    _rule(
                        alice,
                        bob,
                        terms=ShareRuleTerms(
                            use="project-conclusion",
                            audience="project-members",
                            audience_scope_ids=(orders,),
                        ),
                    )
                )
            await runtime.sources.for_scope(alice).capture(
                CaptureSource(source_id="alice-fact", content="Alice.", metadata={})
            )
            await runtime.sources.for_scope(bob).capture(
                CaptureSource(source_id="bob-fact", content="Bob.", metadata={})
            )
            await _accept(standing, runtime, rule, "origin", (alice,), ALICE)
            await _accept(standing, runtime, rule, "target", (orders,), CAROL)
            with run_as(RUNNER), pytest.raises(ShareRuleAcceptanceError):
                await sharing.resolve(rule.rule_id)

            await _accept(standing, runtime, rule, "origin", (bob,), BOB)
            with run_as(RUNNER):
                run = await sharing.resolve(rule.rule_id)
            assert sorted(item.origin_scope_id for item in run.inputs) == sorted([alice, bob])

    asyncio.run(scenario())


def test_foreign_dream_run_is_admitted_only_under_its_pinned_agreement(tmp_path: Path) -> None:
    """A Dream run carries another Scope's Source only under its bound pinned run.

    Durable run identity, B's current-rights admission and the exact pinned inputs are
    all required. A forged run id, a mismatched selection, an unbound request and an
    operation without foreign admission are refused before generation. Fixture only.
    """

    class FixtureGenerator:
        config_id = "foreign-dream-fixture"

        async def generate(self, value):
            used = tuple(item.evidence_id for item in value.evidence.evidence if item.kind == "source")
            return GenerationResult(
                output=DreamPlan(
                    outcome="proposed",
                    reason="Reconciled the two channels from the pinned evidence.",
                    intent="create",
                    proposal=ExperienceContent(
                        situation="A timed-out write may already have committed.",
                        action="Replay only on the new channel with an idempotency key.",
                        outcome="The replay created no duplicate record.",
                        lesson="Check transaction status on the old channel first.",
                    ),
                    evidence_ids=used,
                ),
                usage=InferenceUsage(requests=1),
            )

    async def scenario() -> None:
        # Access and the Runtime share one store, as the enforced Server composes them, so
        # the commit-time recheck can bind to the transaction it is deciding inside.
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'foreign_dream.db'}")
        async with open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access:
            adapter = StandingAccess(access)
            dream_adapter = DreamAccess(access)
            async with open_dream_runtime(
                BuiltinConfig(database=database, runtime=RuntimeConfig()),
                dream_generator=FixtureGenerator(),
                share_rule_authorizer=adapter.authorize_origin,
                share_rule_target_check=adapter.authorize_target,
                share_rule_acceptance_validator=adapter.validate_acceptance,
                dream_authorizer=dream_adapter.authorize,
                dream_authorization_context=access.defer_decision_audit,
                dream_candidate_attester=dream_adapter.attest_candidate,
            ) as runtime:
                adapter.attach(runtime)
                dream_adapter.bind_shared_use(runtime)

                alice, bob, orders = await _three_scopes(runtime)
                await _seed(access, alice, bob, orders)
                # The executor reads its own target as well as the pinned origin.
                await _bind(access, RUNNER, orders, AccessRole.SCOPE_VIEWER, "runner-target-reader")
                sharing = _sharing(runtime, orders)
                with run_as(CAROL):
                    rule = await sharing.create(_rule(alice, None))
                await _accept(adapter, runtime, rule, "origin", (alice,), ALICE)
                await _accept(adapter, runtime, rule, "target", (orders,), CAROL)

                foreign = await runtime.sources.for_scope(alice).capture(
                    CaptureSource(source_id="retry-trace", content="The old channel charged twice.", metadata={})
                )
                local = await runtime.sources.for_scope(orders).capture(
                    CaptureSource(source_id="retry-trace", content="The new channel retries safely.", metadata={})
                )
                with run_as(RUNNER):
                    pinned = await sharing.resolve(rule.rule_id)

                selection = (
                    ScopedEvidence(scope_id=orders, source=local.source_ref),
                    ScopedEvidence(scope_id=alice, source=foreign.source_ref),
                )
                dream = runtime.dream.for_scope(orders, principal_id=principal_identity(RUNNER))

                with run_as(RUNNER), pytest.raises(DreamError) as forged:
                    await dream.create(
                        CreateDreamRunRequest(
                            operation="refine_experience",
                            artifacts=(),
                            scoped=selection,
                            share_run_id="shr_not_pinned",
                            idempotency_key="forged",
                        )
                    )
                assert forged.value.code == "shared_use_unavailable"

                # The real pinned run, but the request widens it with an origin the
                # agreement never pinned.
                extra = await runtime.sources.for_scope(bob).capture(
                    CaptureSource(source_id="unpinned", content="Not pinned.", metadata={})
                )
                with run_as(RUNNER), pytest.raises(DreamError) as widened:
                    await dream.create(
                        CreateDreamRunRequest(
                            operation="refine_experience",
                            artifacts=(),
                            scoped=(*selection, ScopedEvidence(scope_id=bob, source=extra.source_ref)),
                            share_run_id=pinned.run_id,
                            idempotency_key="mismatched",
                        )
                    )
                assert widened.value.code == "shared_use_inputs"

                # The positive path: the same shape, bound to its pinned run, is admitted
                # and its Candidate can be approved. The pinned run is linked to this Dream
                # execution by the target's own administrator.
                with run_as(RUNNER):
                    admitted = await dream.create(
                        CreateDreamRunRequest(
                            operation="refine_experience",
                            artifacts=(),
                            scoped=selection,
                            share_run_id=pinned.run_id,
                            idempotency_key="admitted",
                        )
                    )
                assert admitted.status == "queued", admitted.error
                # The target's administrator links the pinned evaluation to this execution.
                with run_as(CAROL):
                    await sharing.record_outcome(pinned.run_id, dream_run_id=admitted.run_id)
                try:
                    await process_pending(runtime)
                except Exception as error:
                    raise AssertionError(repr(error)) from error
                completed = await dream.get(GetDreamRunRequest(run_id=admitted.run_id))
                if completed.outcome != "proposed":
                    raise AssertionError({
                        "status": completed.status,
                        "error": completed.error,
                        "reason": completed.reason,
                    })
                assert completed.candidate is not None
                owner = await access.candidate_owner(orders, completed.candidate.candidate_id)
                assert owner is not None

                with run_as(RUNNER), pytest.raises(DreamError) as unbound:
                    await dream.create(
                        CreateDreamRunRequest(
                            operation="refine_experience",
                            artifacts=(),
                            scoped=selection,
                            idempotency_key="unbound",
                        )
                    )
                assert unbound.value.code == "unsupported_shared_use"

                # An operation with no foreign admission refuses even when bound to a run
                # the same agreement covers, so one operation's admission never widens
                # another's.
                # An operation with no foreign admission refuses, so one operation's
                # admission never widens another's.
                with run_as(RUNNER), pytest.raises(ValidationError):
                    CreateDreamRunRequest(
                        operation="revise_memory",
                        artifacts=(),
                        scoped=selection,
                        share_run_id=pinned.run_id,
                        idempotency_key="other-op",
                    )

    asyncio.run(scenario())


def test_foreign_dream_phases_fail_closed_after_revocation(tmp_path: Path) -> None:
    """Revoking agreed use between phases stops the run at the next phase.

    Admission, the background body read, Candidate persistence and approval each
    recheck the pinned agreement, so no phase can complete on stale consent. The
    deterministic local fixture generator is used; no provider is contacted.
    """

    class CountingGenerator:
        config_id = "foreign-phase-fixture"

        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, value):
            self.calls += 1
            used = tuple(item.evidence_id for item in value.evidence.evidence if item.kind == "source")
            return GenerationResult(
                output=DreamPlan(
                    outcome="proposed",
                    reason="Reconciled the two channels from the pinned evidence.",
                    intent="create",
                    proposal=ExperienceContent(
                        situation="A timed-out write may already have committed.",
                        action="Replay only on the new channel with an idempotency key.",
                        outcome="The replay created no duplicate record.",
                        lesson="Check transaction status on the old channel first.",
                    ),
                    evidence_ids=used,
                ),
                usage=InferenceUsage(requests=1),
            )

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'phases.db'}")
        async with open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access:
            adapter = StandingAccess(access)
            dream_adapter = DreamAccess(access)
            generator = CountingGenerator()
            async with open_dream_runtime(
                BuiltinConfig(database=database, runtime=RuntimeConfig()),
                dream_generator=generator,
                share_rule_authorizer=adapter.authorize_origin,
                share_rule_target_check=adapter.authorize_target,
                share_rule_acceptance_validator=adapter.validate_acceptance,
                dream_authorizer=dream_adapter.authorize,
                dream_authorization_context=access.defer_decision_audit,
            ) as runtime:
                adapter.attach(runtime)
                dream_adapter.bind_shared_use(runtime)

                alice, bob, orders = await _three_scopes(runtime)
                await _seed(access, alice, bob, orders)
                await _bind(access, RUNNER, orders, AccessRole.SCOPE_VIEWER, "phase-target-reader")
                sharing = _sharing(runtime, orders)
                with run_as(CAROL):
                    rule = await sharing.create(_rule(alice, None))
                await _accept(adapter, runtime, rule, "origin", (alice,), ALICE)
                await _accept(adapter, runtime, rule, "target", (orders,), CAROL)

                foreign = await runtime.sources.for_scope(alice).capture(
                    CaptureSource(source_id="retry-trace", content="The old channel charged twice.", metadata={})
                )
                local = await runtime.sources.for_scope(orders).capture(
                    CaptureSource(source_id="retry-trace", content="The new channel retries safely.", metadata={})
                )
                with run_as(RUNNER):
                    pinned = await sharing.resolve(rule.rule_id)
                selection = (
                    ScopedEvidence(scope_id=orders, source=local.source_ref),
                    ScopedEvidence(scope_id=alice, source=foreign.source_ref),
                )
                dream = runtime.dream.for_scope(orders, principal_id=principal_identity(RUNNER))

                # Revoking the origin agreement after admission stops the run before any
                # origin body reaches the model.
                with run_as(RUNNER):
                    queued = await dream.create(
                        CreateDreamRunRequest(
                            operation="refine_experience",
                            artifacts=(),
                            scoped=selection,
                            share_run_id=pinned.run_id,
                            idempotency_key="revoke-after-admission",
                        )
                    )
                before_invalidation = generator.calls
                with run_as(CAROL):
                    await sharing.invalidate(rule.rule_id, rule.version)
                await process_pending(runtime)
                stopped = await dream.get(GetDreamRunRequest(run_id=queued.run_id))
                assert stopped.status == "failed"
                assert generator.calls == before_invalidation

                # A Candidate already stored also cannot commit after the rule is invalidated:
                # approval rechecks the same pinned agreement.
                with run_as(CAROL):
                    healthy = await sharing.create(_rule(alice, None))
                await _accept(adapter, runtime, healthy, "origin", (alice,), ALICE)
                await _accept(adapter, runtime, healthy, "target", (orders,), CAROL)
                with run_as(RUNNER):
                    healthy_run = await sharing.resolve(healthy.rule_id)
                    queued_candidate = await dream.create(
                        CreateDreamRunRequest(
                            operation="refine_experience",
                            artifacts=(),
                            scoped=selection,
                            share_run_id=healthy_run.run_id,
                            idempotency_key="revoke-before-approval",
                        )
                    )
                with run_as(CAROL):
                    await sharing.record_outcome(healthy_run.run_id, dream_run_id=queued_candidate.run_id)
                await process_pending(runtime)
                pending_run = await dream.get(GetDreamRunRequest(run_id=queued_candidate.run_id))
                assert pending_run.outcome == "proposed", pending_run.error
                assert pending_run.candidate is not None
                with run_as(CAROL):
                    await sharing.invalidate(healthy.rule_id, healthy.version)
                with run_as(RUNNER), pytest.raises(DreamError) as blocked_commit:
                    await runtime.review.for_scope(orders).approve(
                        ApproveCandidateRequest(
                            candidate_id=pending_run.candidate.candidate_id,
                            expected_version=pending_run.candidate.version,
                        )
                    )
                assert blocked_commit.value.code in {"access_unavailable", "shared_use_unavailable"}
                # No conclusion was committed for that Candidate.
                still_pending = await runtime.review.for_scope(orders).get(
                    GetCandidateRequest(candidate_id=pending_run.candidate.candidate_id)
                )
                assert still_pending.status == "pending"
                assert still_pending.result_artifact is None

                # The invalidated rule also blocks a fresh admission outright.
                with run_as(RUNNER), pytest.raises(DreamError):
                    await dream.create(
                        CreateDreamRunRequest(
                            operation="refine_experience",
                            artifacts=(),
                            scoped=selection,
                            share_run_id=pinned.run_id,
                            idempotency_key="invalidated-rule",
                        )
                    )
                # The refused admission added no further handoff: only the lawful
                # second run generated, exactly once.
                assert generator.calls == before_invalidation + 1

    asyncio.run(scenario())


@pytest.mark.skip(
    reason="known blocker: ReviewService._validate_evidence resolves Sources only in the run "
    "Scope, so an admitted foreign Source is refused at Candidate persistence; the fix belongs in "
    "Review's owning validation and is not part of this unit",
)
def test_foreign_sources_initialize_a_first_profile_under_its_pinned_run(tmp_path: Path) -> None:
    """A first Profile may be initialized from foreign Sources under a pinned run.

    The target still has no baseline, the committed identity is the canonical
    ``profile@1``, and the local idempotency control is unchanged. Fixture generator.
    """

    class ProfileGenerator:
        config_id = "foreign-init-fixture"

        async def generate(self, value):
            used = tuple(item.evidence_id for item in value.evidence.evidence)
            return GenerationResult(
                output=DreamPlan(
                    outcome="proposed",
                    reason="The first Profile comes from the pinned external evidence.",
                    intent="create",
                    proposal=ProfileWriteContent(content="# Profile\n\nBased in Shenzhen."),
                    evidence_ids=used,
                ),
                usage=InferenceUsage(requests=1),
            )

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'foreign_init.db'}")
        async with open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access:
            adapter = StandingAccess(access)
            dream_adapter = DreamAccess(access)
            async with open_dream_runtime(
                BuiltinConfig(database=database, runtime=RuntimeConfig()),
                dream_generator=ProfileGenerator(),
                share_rule_authorizer=adapter.authorize_origin,
                share_rule_target_check=adapter.authorize_target,
                share_rule_acceptance_validator=adapter.validate_acceptance,
                dream_authorizer=dream_adapter.authorize,
                dream_authorization_context=access.defer_decision_audit,
            ) as runtime:
                adapter.attach(runtime)
                dream_adapter.bind_shared_use(runtime)

                alice, bob, orders = await _three_scopes(runtime)
                await _seed(access, alice, bob, orders)
                await _bind(access, RUNNER, orders, AccessRole.SCOPE_VIEWER, "init-target-reader")
                await runtime.profiles.put_policy(orders, generation_enabled=True, expected_version=0)
                sharing = _sharing(runtime, orders)
                with run_as(CAROL):
                    rule = await sharing.create(_rule(alice, None))
                await _accept(adapter, runtime, rule, "origin", (alice,), ALICE)
                await _accept(adapter, runtime, rule, "target", (orders,), CAROL)
                foreign = await runtime.sources.for_scope(alice).capture(
                    CaptureSource(source_id="move", content="I moved to Shenzhen permanently.", metadata={})
                )
                with run_as(RUNNER):
                    pinned = await sharing.resolve(rule.rule_id)

                dream = runtime.dream.for_scope(orders, principal_id=principal_identity(RUNNER))
                request = CreateDreamRunRequest(
                    operation="initialize_profile",
                    artifacts=(),
                    scoped=(ScopedEvidence(scope_id=alice, source=foreign.source_ref),),
                    share_run_id=pinned.run_id,
                    idempotency_key="foreign-init",
                )
                with run_as(RUNNER):
                    admitted = await dream.create(request)
                    # The same request replays the accepted run, not a second Profile.
                    assert await dream.create(request) == admitted
                with run_as(CAROL):
                    await sharing.record_outcome(pinned.run_id, dream_run_id=admitted.run_id)
                assert admitted.target is None
                await process_pending(runtime)
                completed = await dream.get(GetDreamRunRequest(run_id=admitted.run_id))
                assert completed.outcome == "proposed", (completed.status, completed.error)
                assert completed.candidate is not None
                with run_as(RUNNER):
                    approved = await runtime.review.for_scope(orders).approve(
                        ApproveCandidateRequest(
                            candidate_id=completed.candidate.candidate_id,
                            expected_version=completed.candidate.version,
                        )
                    )
                # One canonical identity, revision one, with the pinned origin recorded.
                assert approved.result_artifact is not None
                assert (
                    approved.result_artifact.family,
                    approved.result_artifact.artifact_id,
                    approved.result_artifact.revision,
                ) == ("profile", "profile", 1)
                committed = await runtime.profiles.get(orders)
                assert committed.content.generation.mode == "initialize_review_approved"

    asyncio.run(scenario())
