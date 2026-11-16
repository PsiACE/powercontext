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

"""Standing sharing rules pin exact inputs and keep each evaluation distinct.

Each scenario uses the real Runtime, SQLite storage, Scope application, Source
journal, and Artifact repository. Assertions are made through the public
applications a product would call, not through the service's internal calls.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    ApproveCandidateRequest,
    BuiltinConfig,
    CaptureSource,
    CaptureWorkingNote,
    ProposeExperienceRequest,
    RuntimeConfig,
)
from powercontext.builtin.runtime.composition import open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft, ScopeSelection
from powercontext.builtin.sharing import (
    ShareAcceptanceConflictError,
    ShareBudgetExceededError,
    ShareRuleAcceptanceError,
    ShareRuleDraft,
    ShareRuleInvalidatedError,
    ShareRuleServiceUnavailable,
    ShareRuleTerms,
    ShareRunLimits,
)
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY


def experience(lesson: str) -> ExperienceContent:
    return ExperienceContent(
        situation="A timed-out write might already have committed.",
        action="Replay the write with the same idempotency key.",
        outcome="The successful replay created no duplicate record.",
        lesson=lesson,
    )


def config(database: Path) -> BuiltinConfig:
    return BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
        runtime=RuntimeConfig(),
    )


@asynccontextmanager
async def sharing_runtime(database: Path) -> AsyncIterator[tuple[object, str, str]]:
    """Open the real Runtime and create one target Scope and one origin Scope."""

    async with open_builtin_runtime(config(database)) as runtime:
        target = await runtime.scopes.create(ScopeDraft(title="Orders", summary="Project", idempotency_key="t"))
        origin = await runtime.scopes.create(ScopeDraft(title="Alice", summary="Work", idempotency_key="o"))
        yield runtime, target.scope_id, origin.scope_id


async def agree(sharing, rule_id: str, version: int = 1) -> None:
    """Record both authenticated sides of the agreed terms before any run."""

    await sharing.accept(rule_id, version, "origin", actor="origin-admin")
    await sharing.accept(rule_id, version, "target", actor="target-owner")


def exact_rule(origin_scope_id: str, **overrides) -> ShareRuleDraft:
    draft = {
        "selection": ScopeSelection(mode="exact", scope_ids=(origin_scope_id,)),
        "source_types": ("content",),
        "terms": ShareRuleTerms(use="project-conclusion", audience="project-members"),
    }
    draft.update(overrides)
    return ShareRuleDraft(**draft)


def test_run_requires_recorded_acceptance_of_these_exact_terms(tmp_path: Path) -> None:
    """Agreement is durable state: a run needs a recorded accepted side, not an assertion."""

    async def scenario() -> None:
        async with sharing_runtime(tmp_path / "acceptance.db") as (runtime, target, origin):
            sharing = runtime.sharing.for_scope(target)
            await runtime.sources.for_scope(origin).capture(
                CaptureSource(source_id="fact", content="A fact.", metadata={})
            )
            rule = await sharing.create(exact_rule(origin))

            with pytest.raises(ShareRuleAcceptanceError):
                await sharing.resolve(rule.rule_id)

            await sharing.accept(rule.rule_id, 1, "origin", actor="origin-admin")
            with pytest.raises(ShareRuleAcceptanceError):
                await sharing.resolve(rule.rule_id)

            recorded = await sharing.accept(rule.rule_id, 1, "target", actor="target-owner")
            assert recorded.terms_digest == rule.terms_binding().digest()
            assert [item.side for item in await sharing.agree(rule.rule_id, 1)] == ["origin", "target"]

            run = await sharing.resolve(rule.rule_id)
            assert run.state == "pinned"

            # A different audience is a different agreement: the recorded acceptance
            # does not authorize it, so a new version needs its own acceptance.
            changed = await sharing.update(
                rule.rule_id,
                1,
                exact_rule(
                    origin,
                    terms=ShareRuleTerms(use="project-conclusion", audience="external-readers"),
                ),
            )
            with pytest.raises(ShareRuleAcceptanceError):
                await sharing.resolve(rule.rule_id)
            await agree(sharing, rule.rule_id, changed.version)
            assert (await sharing.resolve(rule.rule_id)).rule_version == changed.version

    import asyncio

    asyncio.run(scenario())


def test_standing_rule_discovers_material_added_later(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with sharing_runtime(tmp_path / "later.db") as (runtime, target, origin):
            sharing = runtime.sharing.for_scope(target)
            sources = runtime.sources.for_scope(origin)
            await sources.capture(CaptureSource(source_id="first", content="First report.", metadata={}))
            rule = await sharing.create(exact_rule(origin, artifact_families=("experience",)))
            await agree(sharing, rule.rule_id)

            run = await sharing.resolve(rule.rule_id)

            assert [item.source_ref.source_id for item in run.inputs] == ["first"]
            assert run.inputs[0].paths == ("exact",)
            assert run.outcome == "pending" and run.retry_of is None

            # Material captured after the rule was accepted is eligible for a new run.
            await sources.capture(CaptureSource(source_id="second", content="Second report.", metadata={}))
            latest = await sharing.resolve(rule.rule_id)
            assert [item.source_ref.source_id for item in latest.inputs] == ["first", "second"]
            assert latest.digest() != run.digest()

            # The earlier evaluation still describes exactly what it pinned.
            assert [item.source_ref.source_id for item in (await sharing.get_run(run.run_id)).inputs] == ["first"]

    import asyncio

    asyncio.run(scenario())


def test_retry_reuses_pinned_revision_after_a_newer_publication(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with sharing_runtime(tmp_path / "retry.db") as (runtime, target, origin):
            sharing = runtime.sharing.for_scope(target)
            experience_app = runtime.experience.for_scope(origin)
            review = runtime.review.for_scope(origin)
            evidence = await runtime.sources.for_scope(origin).capture(
                CaptureSource(source_id="retry-note", content="Replayed with the same key.", metadata={})
            )
            candidate = await experience_app.propose(
                ProposeExperienceRequest(
                    proposal=experience("Retry only with a verified deduplication contract."),
                    sources=(evidence.source_ref,),
                )
            )
            approved = await review.approve(
                ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
            )
            published = approved.result_artifact
            assert published is not None and published.revision == 1

            rule = await sharing.create(exact_rule(origin, artifact_families=("experience",)))
            await agree(sharing, rule.rule_id)
            run = await sharing.resolve(rule.rule_id)
            assert _pinned_revision(run) == 1

            # A newer publication is eligible for selection by a new run only.
            revised = await experience_app.propose(
                ProposeExperienceRequest(
                    proposal=experience("Retry only with a verified deduplication contract and recorded status."),
                    sources=(evidence.source_ref,),
                    artifacts=(published,),
                    target=published,
                )
            )
            current = await review.approve(
                ApproveCandidateRequest(candidate_id=revised.candidate_id, expected_version=revised.version)
            )
            assert current.result_artifact is not None and current.result_artifact.revision == 2

            retried = await sharing.retry(run.run_id)

            assert retried.retry_of == run.run_id
            assert retried.digest() == run.digest()
            assert _pinned_revision(retried) == 1

            newer = await sharing.resolve(rule.rule_id)
            assert _pinned_revision(newer) == 2

    import asyncio

    asyncio.run(scenario())


def _pinned_revision(run) -> int:
    return next(item for item in run.inputs if item.kind == "artifact").artifact.artifact.revision


def test_duplicate_notification_points_at_the_existing_evaluation(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with sharing_runtime(tmp_path / "duplicate.db") as (runtime, target, origin):
            sharing = runtime.sharing.for_scope(target)
            await runtime.sources.for_scope(origin).capture(
                CaptureSource(source_id="incident", content="One incident.", metadata={})
            )
            rule = await sharing.create(exact_rule(origin))
            await agree(sharing, rule.rule_id)

            first = await sharing.resolve(rule.rule_id, notification_key="publish:incident")
            duplicate = await sharing.resolve(rule.rule_id, notification_key="publish:incident")

            assert duplicate.run_id == first.run_id
            assert len(await sharing.list_runs(rule_id=rule.rule_id)) == 1

    import asyncio

    asyncio.run(scenario())


def test_over_budget_range_is_refused_rather_than_truncated(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with sharing_runtime(tmp_path / "budget.db") as (runtime, target, origin):
            sharing = runtime.sharing.for_scope(target)
            sources = runtime.sources.for_scope(origin)
            for index in range(3):
                await sources.capture(CaptureSource(source_id=f"r{index}", content=f"Report {index}.", metadata={}))
            rule = await sharing.create(exact_rule(origin, limits=ShareRunLimits(max_inputs=2)))
            await agree(sharing, rule.rule_id)

            with pytest.raises(ShareBudgetExceededError):
                await sharing.resolve(rule.rule_id)

            # A refused run records nothing, so no partial conclusion can be read as complete.
            assert await sharing.list_runs(rule_id=rule.rule_id) == ()

    import asyncio

    asyncio.run(scenario())


def test_invalidated_rule_stops_dependent_work_and_needs_a_new_version(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with sharing_runtime(tmp_path / "invalidated.db") as (runtime, target, origin):
            sharing = runtime.sharing.for_scope(target)
            await runtime.sources.for_scope(origin).capture(
                CaptureSource(source_id="note", content="A note.", metadata={})
            )
            rule = await sharing.create(exact_rule(origin))
            await agree(sharing, rule.rule_id)
            run = await sharing.resolve(rule.rule_id)

            await sharing.invalidate(rule.rule_id, 1)

            with pytest.raises(ShareRuleInvalidatedError):
                await sharing.resolve(rule.rule_id)
            with pytest.raises(ShareRuleInvalidatedError):
                await sharing.retry(run.run_id)
            with pytest.raises(ShareRuleInvalidatedError):
                await sharing.update(rule.rule_id, 1, exact_rule(origin))
            assert (await sharing.get_run(run.run_id)).state == "invalidated"

            # A newly valid rule is a new version with a newly resolved run.
            replacement = await sharing.create(exact_rule(origin))
            await agree(sharing, replacement.rule_id)
            assert (await sharing.resolve(replacement.rule_id)).state == "pinned"

    import asyncio

    asyncio.run(scenario())


def test_subtree_and_exact_selection_count_one_address_with_both_paths(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(config(tmp_path / "paths.db")) as runtime:
            project = await runtime.scopes.create(ScopeDraft(title="P", summary="s", idempotency_key="p"))
            child = await runtime.scopes.create(
                ScopeDraft(title="C", summary="s", parent_scope_id=project.scope_id, idempotency_key="c")
            )
            target = await runtime.scopes.create(ScopeDraft(title="T", summary="s", idempotency_key="t"))
            await runtime.sources.for_scope(child.scope_id).capture(
                CaptureSource(source_id="shared", content="One fact.", metadata={})
            )
            sharing = runtime.sharing.for_scope(target.scope_id)
            rule = await sharing.create(
                ShareRuleDraft(
                    selection=ScopeSelection(mode="subtree", root_scope_id=project.scope_id),
                    exact_scope_ids=(child.scope_id,),
                    source_types=("content",),
                    terms=ShareRuleTerms(use="project-conclusion", audience="project-members"),
                )
            )
            await agree(sharing, rule.rule_id)

            run = await sharing.resolve(rule.rule_id)

            assert len(run.inputs) == 1
            assert run.inputs[0].paths == (f"subtree:{project.scope_id}", "explicit")

    import asyncio

    asyncio.run(scenario())


def test_external_run_never_advances_the_target_local_journal(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with sharing_runtime(tmp_path / "journal.db") as (runtime, target, origin):
            sharing = runtime.sharing.for_scope(target)
            await runtime.sources.for_scope(origin).capture(
                CaptureSource(source_id="external", content="External fact.", metadata={})
            )
            local = await runtime.sources.for_scope(target).capture(
                CaptureSource(source_id="local", content="Local fact.", metadata={})
            )
            rule = await sharing.create(exact_rule(origin))
            await agree(sharing, rule.rule_id)

            await sharing.resolve(rule.rule_id)

            # The target's local journal holds only locally captured material, so an
            # external evaluation neither consumed nor advanced it.
            async with runtime.primary_database.transaction() as connection:
                position = await SourceRepository(BUILTIN_SOURCE_REGISTRY).journal_position(connection, target)
            assert position == local.sequence
            assert [item.source_ref.source_id for item in (await sharing.list_runs())[0].inputs] == ["external"]

    import asyncio

    asyncio.run(scenario())


def test_two_contributors_compose_one_agreement_per_own_origin(tmp_path: Path) -> None:
    """Coverage composes: neither contributor approves the other's Scope or every run."""

    async def scenario() -> None:
        async with open_builtin_runtime(config(tmp_path / "compose.db")) as runtime:
            project = await runtime.scopes.create(ScopeDraft(title="Orders", summary="s", idempotency_key="t"))
            alice = await runtime.scopes.create(ScopeDraft(title="Alice", summary="s", idempotency_key="a"))
            bob = await runtime.scopes.create(ScopeDraft(title="Bob", summary="s", idempotency_key="b"))
            for scope, fact in ((alice, "Alice retry note."), (bob, "Bob counterexample.")):
                await runtime.sources.for_scope(scope.scope_id).capture(
                    CaptureSource(source_id=f"note-{scope.title}", content=fact, metadata={})
                )
            sharing = runtime.sharing.for_scope(project.scope_id)
            rule = await sharing.create(
                ShareRuleDraft(
                    selection=ScopeSelection(mode="exact", scope_ids=(alice.scope_id, bob.scope_id)),
                    source_types=("content",),
                    terms=ShareRuleTerms(
                        use="project-conclusion",
                        audience="project-members",
                        audience_scope_ids=(project.scope_id,),
                    ),
                )
            )

            # Only Alice has accepted so far: Bob's material is not covered yet.
            await sharing.accept(rule.rule_id, 1, "origin", actor="alice-admin", covered_scope_ids=(alice.scope_id,))
            await sharing.accept(
                rule.rule_id, 1, "target", actor="project-owner", covered_scope_ids=(project.scope_id,)
            )
            with pytest.raises(ShareRuleAcceptanceError):
                await sharing.resolve(rule.rule_id)

            # Bob accepts his own Scope; the union covers the agreed range.
            await sharing.accept(rule.rule_id, 1, "origin", actor="bob-admin", covered_scope_ids=(bob.scope_id,))
            run = await sharing.resolve(rule.rule_id)
            assert run.origin_scopes() == tuple(sorted((alice.scope_id, bob.scope_id)))
            assert await sharing.covers_run(run.run_id)

            # Agreement is standing: new material from either contributor needs no new
            # acceptance, and coverage still holds for the recorded run.
            await runtime.sources.for_scope(alice.scope_id).capture(
                CaptureSource(source_id="later", content="A later Alice fact.", metadata={})
            )
            later = await sharing.resolve(rule.rule_id)
            assert later.digest() != run.digest()
            assert await sharing.covers_run(later.run_id)
            assert await sharing.covers_run(run.run_id)
            coverage = await sharing.coverage(rule.rule_id, 1)
            assert coverage.accepted_origin_scope_ids == tuple(sorted((alice.scope_id, bob.scope_id)))
            assert coverage.origins_covered and coverage.audiences_covered

    import asyncio

    asyncio.run(scenario())


def test_an_acceptance_cannot_widen_the_agreed_range(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with sharing_runtime(tmp_path / "widen.db") as (runtime, target, origin):
            sharing = runtime.sharing.for_scope(target)
            outsider = await runtime.scopes.create(ScopeDraft(title="Out", summary="s", idempotency_key="x"))
            rule = await sharing.create(exact_rule(origin))

            with pytest.raises(ShareRuleAcceptanceError):
                await sharing.accept(rule.rule_id, 1, "origin", actor="someone", covered_scope_ids=(outsider.scope_id,))
            with pytest.raises(ShareRuleAcceptanceError):
                # The default audience is the target Scope, so naming an unrelated
                # audience Scope is refused rather than silently becoming the audience.
                await sharing.accept(rule.rule_id, 1, "target", actor="someone", covered_scope_ids=(outsider.scope_id,))

    import asyncio

    asyncio.run(scenario())


def test_pinned_run_survives_restart_and_reads_back_exactly(tmp_path: Path) -> None:
    """A recorded evaluation must be recoverable, not regenerated, after a restart."""

    database = tmp_path / "restart.db"

    async def first() -> tuple[str, str, str]:
        async with sharing_runtime(database) as (runtime, target, origin):
            sharing = runtime.sharing.for_scope(target)
            await runtime.sources.for_scope(origin).capture(
                CaptureSource(source_id="durable", content="A durable fact.", metadata={})
            )
            rule = await sharing.create(exact_rule(origin))
            await agree(sharing, rule.rule_id)
            run = await sharing.resolve(rule.rule_id)
            await sharing.record_outcome(run.run_id, outcome="needs_evidence")
            return target, rule.rule_id, run.run_id

    async def second(target: str, rule_id: str, run_id: str) -> None:
        async with open_builtin_runtime(config(database)) as runtime:
            sharing = runtime.sharing.for_scope(target)
            reopened = await sharing.get_run(run_id)
            assert reopened.outcome == "needs_evidence"
            assert [item.source_ref.source_id for item in reopened.inputs] == ["durable"]
            # The rule version is intact, so a new run can be resolved after reopening.
            rule = await sharing.get(rule_id)
            assert rule.version == 1 and rule.status == "active"
            assert (await sharing.resolve(rule_id)).digest() == reopened.digest()

    import asyncio

    target, rule_id, run_id = asyncio.run(first())
    asyncio.run(second(target, rule_id, run_id))


def test_ineligible_rows_never_hide_eligible_material_behind_a_page(tmp_path: Path) -> None:
    """Working Notes before eligible rows must not make a run claim a smaller range.

    This is the case where two ineligible rows precede eligible content and the input
    budget is one: filtering a fixed page in Python pinned nothing while recording the
    full watermark.
    """

    async def scenario() -> None:
        async with sharing_runtime(tmp_path / "hidden.db") as (runtime, target, origin):
            sharing = runtime.sharing.for_scope(target)
            sources = runtime.sources.for_scope(origin)
            for index in range(2):
                await sources.capture_working_note(
                    CaptureWorkingNote(
                        source_id=f"note-{index}",
                        content=f"Session note {index}.",
                        session_id="s",
                        note_kind="inspection",
                        metadata={},
                    )
                )
            await sources.capture(CaptureSource(source_id="real", content="Eligible fact.", metadata={}))
            rule = await sharing.create(exact_rule(origin, limits=ShareRunLimits(max_inputs=1)))
            await agree(sharing, rule.rule_id)

            run = await sharing.resolve(rule.rule_id)

            assert [item.source_ref.source_id for item in run.inputs] == ["real"]
            assert run.source_watermarks[0].position == 3

    import asyncio

    asyncio.run(scenario())


def test_run_pins_the_rule_version_that_selected_its_inputs(tmp_path: Path) -> None:
    """A head published during discovery must not relabel one version's inputs.

    The authorizer runs between discovery and the write, so a concurrent `update`
    is reachable through a documented extension point.
    """

    async def scenario() -> None:
        from powercontext.builtin.sharing import ShareRuleVersionConflictError

        async with sharing_runtime(tmp_path / "race.db") as (runtime, target, origin):
            sources = runtime.sources.for_scope(origin)
            for index in range(3):
                await sources.capture(CaptureSource(source_id=f"q{index}", content=f"Fact {index}.", metadata={}))
            published: list[bool] = []

            async def publish(scope_id: str) -> None:
                # One-shot: a second rule version excludes these inputs entirely.
                if published:
                    return
                published.append(True)
                await runtime.sharing.for_scope(target).update(
                    rule.rule_id,
                    1,
                    ShareRuleDraft(
                        selection=ScopeSelection(mode="exact", scope_ids=(origin,)),
                        source_types=("skill-usage",),
                        terms=ShareRuleTerms(use="project-conclusion", audience="project-members"),
                        limits=ShareRunLimits(max_inputs=1),
                    ),
                )
                await runtime.sharing.for_scope(target).accept(rule.rule_id, 2, "origin", actor="origin-admin")
                await runtime.sharing.for_scope(target).accept(rule.rule_id, 2, "target", actor="target-owner")

            sharing = runtime.sharing.for_scope(target)
            rule = await sharing.create(exact_rule(origin))
            await agree(sharing, rule.rule_id)
            runtime.share_rules._authorize_origin = lambda scope_id, ref: publish(scope_id)
            try:
                with pytest.raises(ShareRuleVersionConflictError):
                    await sharing.resolve(rule.rule_id)
            finally:
                runtime.share_rules._authorize_origin = None

            # The refused call recorded nothing, so no mislabelled manifest exists.
            assert await sharing.list_runs(rule_id=rule.rule_id) == ()

    import asyncio

    asyncio.run(scenario())


def test_agreement_identity_distinguishes_selection_and_input_kinds(tmp_path: Path) -> None:
    """A digest presented as the standing agreement must separate different ranges."""

    async def scenario() -> None:
        async with open_builtin_runtime(config(tmp_path / "digest.db")) as runtime:
            project = await runtime.scopes.create(ScopeDraft(title="P", summary="s", idempotency_key="p"))
            child = await runtime.scopes.create(
                ScopeDraft(title="C", summary="s", parent_scope_id=project.scope_id, idempotency_key="c")
            )
            target = await runtime.scopes.create(ScopeDraft(title="T", summary="s", idempotency_key="t"))
            terms = ShareRuleTerms(use="project-conclusion", audience="project-members")
            sharing = runtime.sharing.for_scope(target.scope_id)

            exact = await sharing.create(
                ShareRuleDraft(selection=ScopeSelection(mode="exact", scope_ids=(project.scope_id,)), terms=terms)
            )
            subtree = await sharing.create(
                ShareRuleDraft(selection=ScopeSelection(mode="subtree", root_scope_id=project.scope_id), terms=terms)
            )
            assert exact.terms_binding().digest() != subtree.terms_binding().digest()

            content_only = await sharing.create(
                ShareRuleDraft(
                    selection=ScopeSelection(mode="exact", scope_ids=(project.scope_id,)),
                    source_types=("content",),
                    terms=terms,
                )
            )
            families = await sharing.create(
                ShareRuleDraft(
                    selection=ScopeSelection(mode="exact", scope_ids=(project.scope_id,)),
                    source_types=("content",),
                    artifact_families=("experience",),
                    terms=terms,
                )
            )
            assert content_only.terms_binding().digest() != families.terms_binding().digest()

            # A changed run limit changes what the version may select.
            wider = await sharing.create(
                ShareRuleDraft(
                    selection=ScopeSelection(mode="exact", scope_ids=(project.scope_id,)),
                    source_types=("content",),
                    terms=terms,
                    limits=ShareRunLimits(max_inputs=8),
                )
            )
            assert content_only.terms_binding().digest() != wider.terms_binding().digest()
            # The agreement identity stays independent of discovered run inputs.
            assert (
                content_only.terms_binding().digest()
                == (await sharing.get(content_only.rule_id)).terms_binding().digest()
            )
            assert child.scope_id  # the child exists but is outside the exact agreement

    import asyncio

    asyncio.run(scenario())


def test_identical_acceptance_returns_the_stored_record_and_changed_coverage_conflicts(
    tmp_path: Path,
) -> None:
    """An acceptance reports its own durable identity; different coverage is refused."""

    async def scenario() -> None:
        async with sharing_runtime(tmp_path / "accept-readback.db") as (runtime, target, origin):
            sharing = runtime.sharing.for_scope(target)
            outsider = await runtime.scopes.create(ScopeDraft(title="Out", summary="s", idempotency_key="x"))
            rule = await sharing.create(
                ShareRuleDraft(
                    selection=ScopeSelection(mode="exact", scope_ids=(origin,)),
                    exact_scope_ids=(outsider.scope_id,),
                    source_types=("content",),
                    terms=ShareRuleTerms(use="project-conclusion", audience="project-members"),
                )
            )

            first = await sharing.accept(rule.rule_id, 1, "origin", actor="origin-admin", covered_scope_ids=(origin,))
            again = await sharing.accept(rule.rule_id, 1, "origin", actor="origin-admin", covered_scope_ids=(origin,))
            assert again.acceptance_id == first.acceptance_id
            assert [item.acceptance_id for item in await sharing.agree(rule.rule_id, 1)] == [first.acceptance_id]

            # Silent widening or silent discarding would both misstate the record.
            with pytest.raises(ShareAcceptanceConflictError):
                await sharing.accept(
                    rule.rule_id,
                    1,
                    "origin",
                    actor="origin-admin",
                    covered_scope_ids=(origin, outsider.scope_id),
                )

            # A different actor may accept the remaining Scope; coverage then composes.
            second = await sharing.accept(
                rule.rule_id, 1, "origin", actor="second-admin", covered_scope_ids=(outsider.scope_id,)
            )
            assert second.acceptance_id != first.acceptance_id
            recorded = await sharing.coverage(rule.rule_id, 1)
            assert recorded.accepted_origin_scope_ids == tuple(sorted((origin, outsider.scope_id)))

    import asyncio

    asyncio.run(scenario())


def test_a_run_cannot_be_completed_after_its_rule_is_invalidated(tmp_path: Path) -> None:
    """Invalidation between retry's authority check and its insert must still stop it."""

    async def scenario() -> None:
        async with sharing_runtime(tmp_path / "retry-window.db") as (runtime, target, origin):
            sharing = runtime.sharing.for_scope(target)
            await runtime.sources.for_scope(origin).capture(
                CaptureSource(source_id="fact", content="A fact.", metadata={})
            )
            rule = await sharing.create(exact_rule(origin))
            await agree(sharing, rule.rule_id)
            run = await sharing.resolve(rule.rule_id)
            await sharing.invalidate(rule.rule_id, 1)

            with pytest.raises(ShareRuleInvalidatedError):
                await sharing.retry(run.run_id)
            with pytest.raises(ShareRuleInvalidatedError):
                await sharing.record_outcome(run.run_id, outcome="no_change")
            assert (await sharing.get_run(run.run_id)).outcome == "pending"

    import asyncio

    asyncio.run(scenario())


def test_managed_database_does_not_advertise_standing_rules_it_cannot_serve(tmp_path: Path) -> None:
    """A managed chain without the share tables must not expose a failing service.

    An unmanaged profile creates these tables at bootstrap. A managed database takes
    its schema from the frozen chain, so until that chain carries them the Runtime
    must report sharing as unavailable instead of raising a raw driver error on the
    first call.
    """

    from powercontext.builtin.persistence.migrations.deployment import server_bundle
    from powercontext.builtin.persistence.migrations.sqlite import SQLiteMigrationRunner
    from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest

    database = tmp_path / "managed.db"
    runner = SQLiteMigrationRunner(
        database,
        server_bundle(),
        configuration={
            "processing_manifest": canonical_processing_manifest(BuiltinConfig()),
            "embedding_enabled": False,
        },
    )
    plan = runner.plan()
    runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)

    async def scenario() -> None:
        async with open_builtin_runtime(
            BuiltinConfig(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
                runtime=RuntimeConfig(),
            )
        ) as runtime:
            assert runtime.share_rules is None
            with pytest.raises(ShareRuleServiceUnavailable):
                # The capability is absent, so no Scope existence is probed either.
                await runtime.sharing.for_scope("scope").resolve("rule")

    import asyncio

    asyncio.run(scenario())
