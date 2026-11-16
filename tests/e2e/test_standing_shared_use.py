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

from powercontext.builtin.authz import (
    AccessAction,
    AccessAuditContext,
    AccessBinding,
    AccessControlService,
    AccessDeniedError,
    AccessInvalidRequestError,
    AccessRole,
    CreateBinding,
    PrincipalRef,
    ResourceRef,
)
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.authz.standing_use import principal_key
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, RuntimeConfig
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
from powercontext.server.standing_access import StandingAccess

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
        ) as runtime:
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
            assert grant.acceptance_ids == tuple(item.acceptance_id for item in acceptances)

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
