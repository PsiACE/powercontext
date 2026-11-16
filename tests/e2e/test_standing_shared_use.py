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

"""Standing shared use: durable agreement, restart, and current authority.

Every scenario uses the real Runtime, SQLite storage, Scope application, Source
journal, D's standing-rule record, and the built-in Access service. Assertions go
through the public applications a product would call.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from powercontext.builtin.authz import (
    AccessAuditContext,
    AccessBinding,
    AccessControlService,
    AccessDeniedError,
    AccessRole,
    CreateBinding,
    PrincipalRef,
    ResourceRef,
)
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.authz.standing_use import StandingUseAuthorization, principal_key
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, RuntimeConfig
from powercontext.builtin.runtime.application import BuiltinRuntime
from powercontext.builtin.runtime.composition import open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft, ScopeSelection
from powercontext.builtin.sharing import ShareRuleAcceptanceError, ShareRuleDraft, ShareRuleTerms

ADMIN = PrincipalRef(type="service", id="deployment-admin")
ALICE = PrincipalRef(type="user", id="alice")
BOB = PrincipalRef(type="user", id="bob")
CAROL = PrincipalRef(type="user", id="carol")
RUNNER = PrincipalRef(type="service", id="share-runner")
MEMBER = PrincipalRef(type="user", id="member")

AUDIT = AccessAuditContext(transport="background", operation="standing_use_test", request_id="req-standing")


def config(database: Path) -> BuiltinConfig:
    return BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
        runtime=RuntimeConfig(),
    )


@asynccontextmanager
async def runtime_only(database: Path) -> AsyncIterator[BuiltinRuntime]:
    async with open_builtin_runtime(config(database)) as runtime:
        yield runtime


@asynccontextmanager
async def access_only(database: Path) -> AsyncIterator[AccessControlService]:
    async with open_builtin_access_control(
        SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"), bootstrap_administrators=(ADMIN,)
    ) as access:
        yield access


def _scopes(runtime: BuiltinRuntime):
    assert runtime.scopes is not None
    return runtime.scopes


def _service(runtime: BuiltinRuntime):
    """Standing rules and Scope creation must be available in this scenario."""

    assert runtime.share_rules is not None and runtime.scopes is not None
    return runtime.share_rules


async def _bind(access: AccessControlService, subject: PrincipalRef, scope_id: str, role: AccessRole, key: str):
    return await access.create_binding(
        ADMIN,
        CreateBinding(
            subject=subject,
            resource=ResourceRef.scope(scope_id),
            role=role,
            idempotency_key=key,
        ),
        context=AUDIT,
    )


async def _seed_authority(access: AccessControlService, alice: str, bob: str, orders: str) -> dict[str, AccessBinding]:
    """Each contributor administers its own origin; the target and runner are separate."""

    return {
        "alice": await _bind(access, ALICE, alice, AccessRole.SCOPE_ADMIN, "alice-origin"),
        "bob": await _bind(access, BOB, bob, AccessRole.SCOPE_ADMIN, "bob-origin"),
        "carol": await _bind(access, CAROL, orders, AccessRole.SCOPE_ADMIN, "carol-target"),
        "runner": await _bind(access, RUNNER, orders, AccessRole.SCOPE_CONTRIBUTOR, "runner-contributor"),
        "runner_alice": await _bind(access, RUNNER, alice, AccessRole.SCOPE_VIEWER, "runner-alice-reader"),
        "runner_bob": await _bind(access, RUNNER, bob, AccessRole.SCOPE_VIEWER, "runner-bob-reader"),
        "member": await _bind(access, MEMBER, orders, AccessRole.SCOPE_VIEWER, "member"),
    }


def two_owner_rule(alice: str, bob: str) -> ShareRuleDraft:
    """One standing range over both contributors, with its declared terms."""

    return ShareRuleDraft(
        selection=ScopeSelection(mode="exact", scope_ids=(alice, bob)),
        source_types=("content",),
        terms=ShareRuleTerms(use="project-conclusion", audience="project-members", audience_scope_ids=()),
    )


async def _accept_both_owners(standing, service, rule, alice: str, bob: str, orders: str):
    """The genuine path: each contributor accepts only its own origin."""

    return (
        await standing.accept(service, ALICE, rule, "origin", covered_scope_ids=(alice,), context=AUDIT),
        await standing.accept(service, BOB, rule, "origin", covered_scope_ids=(bob,), context=AUDIT),
        await standing.accept(service, CAROL, rule, "target", covered_scope_ids=(orders,), context=AUDIT),
    )


def test_two_owner_agreement_survives_restart_and_admits_a_later_fact(tmp_path: Path) -> None:
    """Acceptance is durable, composes per contributor, and covers material added later."""

    async def scenario() -> None:
        database = tmp_path / "standing.db"
        async with runtime_only(database) as runtime:
            alice = (
                await _scopes(runtime).create(ScopeDraft(title="Alice", summary="W", idempotency_key="a"))
            ).scope_id
            bob = (await _scopes(runtime).create(ScopeDraft(title="Bob", summary="W", idempotency_key="b"))).scope_id
            orders = (
                await _scopes(runtime).create(ScopeDraft(title="Orders", summary="P", idempotency_key="o"))
            ).scope_id
            async with access_only(database) as access:
                await _seed_authority(access, alice, bob, orders)
                standing = StandingUseAuthorization(access)
                sharing = runtime.sharing.for_scope(orders)
                rule = await sharing.create(two_owner_rule(alice, bob))
                acceptances = await _accept_both_owners(standing, _service(runtime), rule, alice, bob, orders)
                # D owns identity: ids are its durable rows, not anything B minted.
                assert all(item.acceptance_id for item in acceptances)
                assert acceptances[0].actor == principal_key(ALICE)

                await runtime.sources.for_scope(alice).capture(
                    CaptureSource(source_id="first", content="A fact.", metadata={})
                )
                first = await sharing.resolve(rule.rule_id)
                assert [(item.source_ref and item.source_ref.source_id) for item in first.inputs] == ["first"]

        # A fresh process: new Access service, new Runtime composition, same rows.
        async with runtime_only(database) as runtime, access_only(database) as access:
            bindings = await _seed_authority(access, alice, bob, orders)
            standing = StandingUseAuthorization(access)
            sharing = runtime.sharing.for_scope(orders)
            rule = await sharing.get(first.rule_id)

            # The recorded agreement is readable and unchanged after restart.
            recorded = await sharing.agree(rule.rule_id, rule.version)
            assert {item.acceptance_id for item in recorded} == {item.acceptance_id for item in acceptances}

            # A fact captured after the agreement is eligible without re-acceptance.
            await runtime.sources.for_scope(bob).capture(
                CaptureSource(source_id="later", content="Bob's counterexample.", metadata={})
            )
            later = await sharing.resolve(rule.rule_id)
            assert sorted(item.source_ref.source_id for item in later.inputs if item.source_ref) == ["first", "later"]

            coverage = await sharing.coverage(rule.rule_id, rule.version)
            grant = await standing.require_run_use(RUNNER, rule, later, coverage, context=AUDIT)
            assert grant.rule_version == rule.version
            await standing.require_acceptors_current(recorded, rule, context=AUDIT)

            # Dropping Bob's counterexample from the terms is a different agreement:
            # Alice's recorded coverage cannot stand in for the run that reached Bob.
            with pytest.raises(ValueError, match="exact Scope selection must be unique"):
                ScopeSelection(mode="exact", scope_ids=(alice, alice))

            # A withdrawn contributor blocks the pinned run it still covered.
            alice_binding = bindings["alice"]
            await access.revoke_binding(
                ADMIN,
                alice_binding.binding_id,
                expected_version=alice_binding.version,
                idempotency_key="revoke-alice",
                context=AUDIT,
            )
            with pytest.raises(AccessDeniedError):
                await standing.require_acceptors_current(recorded, rule, context=AUDIT)
            # The runner's own origin read and target write are unaffected by Alice's
            # origin-administration loss; require_run_use covers execution authority.
            await standing.require_run_use(RUNNER, rule, later, coverage, context=AUDIT)

    asyncio.run(scenario())


def test_acceptance_needs_current_origin_authority(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "authority.db"
        async with runtime_only(database) as runtime:
            alice = (
                await _scopes(runtime).create(ScopeDraft(title="Alice", summary="W", idempotency_key="a"))
            ).scope_id
            bob = (await _scopes(runtime).create(ScopeDraft(title="Bob", summary="W", idempotency_key="b"))).scope_id
            orders = (
                await _scopes(runtime).create(ScopeDraft(title="Orders", summary="P", idempotency_key="o"))
            ).scope_id
            async with access_only(database) as access:
                await _seed_authority(access, alice, bob, orders)
                standing = StandingUseAuthorization(access)
                sharing = runtime.sharing.for_scope(orders)
                rule = await sharing.create(two_owner_rule(alice, bob))

                # Alice cannot sign for Bob's origin, and a member signs for nothing.
                with pytest.raises(AccessDeniedError):
                    await standing.accept(
                        _service(runtime),
                        ALICE,
                        rule,
                        "origin",
                        covered_scope_ids=(alice, bob),
                        context=AUDIT,
                    )
                with pytest.raises(AccessDeniedError):
                    await standing.accept(
                        _service(runtime),
                        MEMBER,
                        rule,
                        "origin",
                        covered_scope_ids=(bob,),
                        context=AUDIT,
                    )
                assert await sharing.agree(rule.rule_id, rule.version) == ()

                # The one supported retention passes for the contributor who owns it.
                await standing.require_acceptance_authority(
                    ALICE, rule, "origin", covered_scope_ids=(alice,), context=AUDIT
                )

    asyncio.run(scenario())


def test_run_use_requires_current_origin_target_and_audience_authority(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "current.db"
        async with runtime_only(database) as runtime:
            alice = (
                await _scopes(runtime).create(ScopeDraft(title="Alice", summary="W", idempotency_key="a"))
            ).scope_id
            bob = (await _scopes(runtime).create(ScopeDraft(title="Bob", summary="W", idempotency_key="b"))).scope_id
            orders = (
                await _scopes(runtime).create(ScopeDraft(title="Orders", summary="P", idempotency_key="o"))
            ).scope_id
            async with access_only(database) as access:
                bindings = await _seed_authority(access, alice, bob, orders)
                standing = StandingUseAuthorization(access)
                sharing = runtime.sharing.for_scope(orders)
                rule = await sharing.create(two_owner_rule(alice, bob))
                await _accept_both_owners(standing, _service(runtime), rule, alice, bob, orders)
                await runtime.sources.for_scope(alice).capture(
                    CaptureSource(source_id="fact", content="A fact.", metadata={})
                )
                run = await sharing.resolve(rule.rule_id)
                coverage = await sharing.coverage(rule.rule_id, rule.version)
                await standing.require_run_use(RUNNER, rule, run, coverage, context=AUDIT)

                # Losing target write authority stops the run.
                runner_binding = bindings["runner"]
                await access.revoke_binding(
                    ADMIN,
                    runner_binding.binding_id,
                    expected_version=runner_binding.version,
                    idempotency_key="revoke-runner",
                    context=AUDIT,
                )
                with pytest.raises(AccessDeniedError):
                    await standing.require_run_use(RUNNER, rule, run, coverage, context=AUDIT)

                # A member outside the accepted audience cannot read the conclusion.
                with pytest.raises(AccessDeniedError):
                    await standing.require_run_use(MEMBER, rule, run, coverage, context=AUDIT)

    asyncio.run(scenario())


def test_one_contributor_cannot_admit_a_run_that_reached_another(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "compose.db"
        async with runtime_only(database) as runtime:
            alice = (
                await _scopes(runtime).create(ScopeDraft(title="Alice", summary="W", idempotency_key="a"))
            ).scope_id
            bob = (await _scopes(runtime).create(ScopeDraft(title="Bob", summary="W", idempotency_key="b"))).scope_id
            orders = (
                await _scopes(runtime).create(ScopeDraft(title="Orders", summary="P", idempotency_key="o"))
            ).scope_id
            async with access_only(database) as access:
                await _seed_authority(access, alice, bob, orders)
                standing = StandingUseAuthorization(access)
                sharing = runtime.sharing.for_scope(orders)
                rule = await sharing.create(two_owner_rule(alice, bob))
                await runtime.sources.for_scope(alice).capture(
                    CaptureSource(source_id="alice-fact", content="Alice's fact.", metadata={})
                )
                await runtime.sources.for_scope(bob).capture(
                    CaptureSource(source_id="bob-fact", content="Bob's fact.", metadata={})
                )

                # Alice's coverage alone cannot admit a run that reached Bob.
                await standing.accept(
                    _service(runtime), ALICE, rule, "origin", covered_scope_ids=(alice,), context=AUDIT
                )
                await standing.accept(
                    _service(runtime), CAROL, rule, "target", covered_scope_ids=(orders,), context=AUDIT
                )
                with pytest.raises(ShareRuleAcceptanceError):
                    await sharing.resolve(rule.rule_id)

                # Bob's own consent composes; nobody had to sign for both.
                await standing.accept(_service(runtime), BOB, rule, "origin", covered_scope_ids=(bob,), context=AUDIT)
                run = await sharing.resolve(rule.rule_id)
                assert sorted(item.origin_scope_id for item in run.inputs) == sorted([alice, bob])

    asyncio.run(scenario())
