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

"""Target-owned standing rules, bounded discovery, and pinned evaluation runs.

This service answers three separate questions and refuses to conflate them:

- Which future material may this target Scope consider? A rule version states it.
- What exactly did one evaluation read? A pinned run records it.
- What is the maintained conclusion? The committed Artifact revision, together
  with its own lineage, which this service never writes.

Discovery reads each origin Scope's own Source journal and current Artifact
heads. It never advances a cursor, processing intent, or journal position: the
target's local Source Journal cursor describes locally captured material only, so
external processing cannot consume it. Every origin is checked by the injected
authority callback before it is pinned and again before a retry reads it, because
a recorded rule term is an agreement, not a grant. This module therefore owns no
authorization policy, no second conclusion store, and no revision lineage.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.evidence.models import content_digest
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.share_rules import (
    ShareAcceptanceRepository,
    ShareRuleRepository,
    ShareRunRepository,
)
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE
from powercontext.builtin.scope.application import ScopeApplication
from powercontext.builtin.scope.errors import ScopeNotFoundError
from powercontext.builtin.scope.models import ScopeSelection as ShareSelection
from powercontext.builtin.scope.repository import ScopeRepository
from powercontext.builtin.sharing.models import (
    ShareAcceptance,
    ShareAcceptanceSide,
    ShareBudgetExceededError,
    ShareInput,
    ShareRule,
    ShareRuleAcceptanceError,
    ShareRuleDraft,
    ShareRuleInvalidatedError,
    ShareRuleNotFoundError,
    ShareRuleStatus,
    ShareRuleVersionConflictError,
    ShareRun,
    ShareRunCoverage,
    ShareRunNotFoundError,
    ShareRunOutcome,
    ShareRunStateError,
    ShareSourceWatermark,
    ShareTermsBinding,
)
from powercontext.builtin.source_eligibility import is_generation_eligible
from powercontext.sources import SourceRef

ShareAuthorizer = Callable[[str, "ArtifactAddress | SourceRef"], Awaitable[None]]
"""Check one origin under the execution principal's current authority.

The callback's shape matches the Dream evidence authorizer, so the Access owner
can pass an existing check rather than a new policy surface.
"""

ShareOriginCheck = Callable[[str], Awaitable[None]]
"""Check that the target Scope may still start or continue this rule's work."""


class ShareRuleService:
    def __init__(
        self,
        database: AsyncDatabase,
        scopes: ScopeApplication,
        sources: SourceRepository,
        artifacts: ArtifactRepository,
        *,
        authorize_origin: ShareAuthorizer | None = None,
        authorize_target: ShareOriginCheck | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self._database = database
        self._scopes = scopes
        self._scope_repository = ScopeRepository()
        self._sources = sources
        self._artifacts = artifacts
        self._authorize_origin = authorize_origin
        self._authorize_target = authorize_target
        self._clock = (lambda: datetime.now(UTC)) if clock is None else clock
        self._id_factory = (lambda prefix: f"{prefix}_{uuid4().hex}") if id_factory is None else id_factory
        self.rules = ShareRuleRepository()
        self.runs = ShareRunRepository()
        self.acceptances = ShareAcceptanceRepository()

    # -- rules ---------------------------------------------------------------

    async def create(self, scope_id: str, draft: ShareRuleDraft, /) -> ShareRule:
        await self._require_target(scope_id)
        async with self._database.transaction() as connection:
            await self._require_scope(connection, scope_id)
            await self._require_origins(connection, draft)
            rule = ShareRule(
                scope_id=scope_id,
                rule_id=self._id_factory("rule"),
                version=1,
                selection=draft.selection,
                exact_scope_ids=draft.exact_scope_ids,
                source_types=draft.source_types,
                artifact_families=draft.artifact_families,
                terms=draft.terms,
                limits=draft.limits,
                updated_at=self._now(),
            )
            return await self.rules.create(connection, rule)

    async def update(
        self,
        scope_id: str,
        rule_id: str,
        expected_version: int,
        draft: ShareRuleDraft,
        /,
    ) -> ShareRule:
        await self._require_target(scope_id)
        async with self._database.transaction() as connection:
            current = await self._require_rule(connection, scope_id, rule_id, for_update=True)
            if current.version != expected_version:
                raise ShareRuleVersionConflictError(rule_id, expected_version, current.version)
            if current.status == "invalidated":
                raise ShareRuleInvalidatedError(rule_id)
            await self._require_origins(connection, draft)
            return await self.rules.append(
                connection,
                current.model_copy(
                    update={
                        "version": current.version + 1,
                        "selection": draft.selection,
                        "exact_scope_ids": draft.exact_scope_ids,
                        "source_types": draft.source_types,
                        "artifact_families": draft.artifact_families,
                        "terms": draft.terms,
                        "limits": draft.limits,
                        "updated_at": self._now(),
                    }
                ),
                expected_version,
            )

    async def invalidate(self, scope_id: str, rule_id: str, expected_version: int, /) -> ShareRule:
        """Stop dependent work without rewriting any accepted rule version."""

        await self._require_target(scope_id)
        async with self._database.transaction() as connection:
            current = await self._require_rule(connection, scope_id, rule_id, for_update=True)
            if current.version != expected_version:
                raise ShareRuleVersionConflictError(rule_id, expected_version, current.version)
            invalidated = await self.rules.invalidate(connection, scope_id, rule_id, expected_version, self._now())
            if invalidated is None:
                raise ShareRuleVersionConflictError(rule_id, expected_version, current.version)
            for run in await self.runs.list(connection, scope_id, rule_id=rule_id):
                if run.state == "pinned" and run.outcome == "pending":
                    await self.runs.replace(
                        connection,
                        run.model_copy(update={"state": "invalidated"}),
                        expected_state="pinned",
                    )
            return invalidated

    async def get(self, scope_id: str, rule_id: str, /, *, version: int | None = None) -> ShareRule:
        async with self._database.transaction() as connection:
            if version is None:
                return await self._require_rule(connection, scope_id, rule_id)
            rule = await self.rules.version(connection, scope_id, rule_id, version)
            if rule is None:
                raise ShareRuleNotFoundError(scope_id, rule_id, version)
            return rule

    async def list(self, scope_id: str, /, *, status: ShareRuleStatus | None = None) -> tuple[ShareRule, ...]:
        async with self._database.transaction() as connection:
            await self._require_scope(connection, scope_id)
            return await self.rules.list(connection, scope_id, status=status)

    # -- acceptance ----------------------------------------------------------

    async def accept(
        self,
        scope_id: str,
        rule_id: str,
        version: int,
        side: ShareAcceptanceSide,
        *,
        actor: str,
        covered_scope_ids: tuple[str, ...] | None = None,
    ) -> ShareAcceptance:
        """Record that one authenticated actor accepted this version, covering given Scopes.

        `actor` must be the identity that actually ran the call, supplied by the
        authenticated boundary (B checks that identity's authority and supplies the
        Scopes it may cover). The record binds the version's standing selector and
        terms, so a later request naming an approver cannot create or reuse one, and
        so accepting does not depend on which inputs a future run will discover.
        """

        await self._require_target(scope_id)
        async with self._database.transaction() as connection:
            rule = await self.rules.version(connection, scope_id, rule_id, version)
            if rule is None:
                raise ShareRuleNotFoundError(scope_id, rule_id, version)
            binding = rule.terms_binding()
            acceptance = ShareAcceptance(
                scope_id=scope_id,
                rule_id=rule_id,
                version=version,
                side=side,
                actor=actor,
                terms_digest=binding.digest(),
                covered_scope_ids=self._coverage(binding, rule, side, covered_scope_ids),
                acceptance_id=self._id_factory("accept"),
                accepted_at=self._now(),
            )
            return await self.acceptances.reserve(connection, acceptance)

    @staticmethod
    def _coverage(
        binding: ShareTermsBinding,
        rule: ShareRule,
        side: ShareAcceptanceSide,
        covered_scope_ids: tuple[str, ...] | None,
    ) -> tuple[str, ...]:
        """Keep an acceptance inside the range and audience this version declared."""

        declared = tuple(binding.declared_scope_ids) if side == "origin" else tuple(binding.audience_scope_ids)
        requested = declared if covered_scope_ids is None else covered_scope_ids
        if not requested:
            raise ShareRuleAcceptanceError(rule.rule_id, rule.version, f"{side}_scope")
        unknown = tuple(sorted(set(requested) - set(declared)))
        if unknown:
            # An acceptance cannot widen the agreed range: unsupported coverage fails closed.
            raise ShareRuleAcceptanceError(rule.rule_id, rule.version, f"{side}_scope:{unknown[0]}")
        return tuple(sorted(set(requested)))

    async def acceptances_for(self, scope_id: str, rule_id: str, version: int, /) -> tuple[ShareAcceptance, ...]:
        async with self._database.transaction() as connection:
            return await self.acceptances.all(connection, scope_id, rule_id, version)

    # -- runs ----------------------------------------------------------------

    async def resolve(self, scope_id: str, rule_id: str, /, *, notification_key: str | None = None) -> ShareRun:
        """Discover currently eligible material under the active rule and pin one run.

        A repeated notification key returns the evaluation already recorded for it,
        so a duplicate notification never starts a second evaluation. A range that
        cannot be covered completely is refused before anything is pinned, so a
        bounded run is never mistaken for a complete conclusion.
        """

        await self._require_target(scope_id)
        async with self._database.transaction() as connection:
            rule = await self._require_rule(connection, scope_id, rule_id, for_update=True)
            if notification_key is not None:
                existing = await self.runs.notification(connection, scope_id, rule_id, notification_key)
                if existing is not None:
                    return existing
        inputs, origins, watermarks, baseline = await self._discover(scope_id, rule)
        used = tuple(sorted({item.origin_scope_id for item in inputs}))
        await self._require_accepted(rule, used)
        async with self._database.transaction() as connection:
            rule = await self._require_rule(connection, scope_id, rule_id, for_update=True)
            if rule.status == "invalidated":
                raise ShareRuleInvalidatedError(rule_id)
            await self._require_accepted(rule, used, connection=connection)
            if notification_key is not None:
                existing = await self.runs.notification(connection, scope_id, rule_id, notification_key)
                if existing is not None:
                    return existing
            return await self.runs.create(
                connection,
                ShareRun(
                    scope_id=scope_id,
                    run_id=self._id_factory("share"),
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    terms=rule.terms,
                    inputs=inputs,
                    baseline=baseline,
                    baseline_digest=None if baseline is None else content_digest(baseline.model_dump_json().encode()),
                    source_watermarks=watermarks,
                    origins=origins,
                    limits=rule.limits,
                    notification_key=notification_key,
                    created_at=self._now(),
                ),
            )

    async def retry(self, scope_id: str, run_id: str, /) -> ShareRun:
        """Reuse one run's exact manifest instead of resolving newer revisions.

        A retry rechecks that the recorded rule version is still accepted and
        rechecks current authority at every recorded origin. It never re-discovers
        material, so a newly published revision waits for a new run.
        """

        await self._require_target(scope_id)
        async with self._database.transaction() as connection:
            original = await self._require_run(connection, scope_id, run_id)
            if original.state == "invalidated":
                raise ShareRuleInvalidatedError(original.rule_id)
            rule = await self.rules.version(connection, scope_id, original.rule_id, original.rule_version)
            if rule is None:
                raise ShareRuleInvalidatedError(original.rule_id)
            await self._require_accepted(rule, original.origin_scopes(), connection=connection)
        await self._authorize_origins(original.inputs)
        async with self._database.transaction() as connection:
            await self._require_scope(connection, scope_id)
            return await self.runs.create(
                connection,
                ShareRun(
                    scope_id=scope_id,
                    run_id=self._id_factory("share"),
                    rule_id=original.rule_id,
                    rule_version=original.rule_version,
                    terms=original.terms,
                    inputs=original.inputs,
                    baseline=original.baseline,
                    baseline_digest=original.baseline_digest,
                    source_watermarks=original.source_watermarks,
                    origins=original.origins,
                    limits=original.limits,
                    notification_key=None,
                    retry_of=original.run_id,
                    created_at=self._now(),
                ),
            )

    async def get_run(self, scope_id: str, run_id: str, /) -> ShareRun:
        async with self._database.transaction() as connection:
            return await self._require_run(connection, scope_id, run_id)

    async def list_runs(self, scope_id: str, /, *, rule_id: str | None = None) -> tuple[ShareRun, ...]:
        async with self._database.transaction() as connection:
            await self._require_scope(connection, scope_id)
            return await self.runs.list(connection, scope_id, rule_id=rule_id)

    async def record_outcome(
        self,
        scope_id: str,
        run_id: str,
        /,
        *,
        outcome: ShareRunOutcome | None = None,
        committed: ArtifactRef | None = None,
        dream_run_id: str | None = None,
        candidate_id: str | None = None,
    ) -> ShareRun:
        """Record what an evaluation concluded, using the record that proves it.

        `no_change` and `needs_evidence` are evaluation results; a pending or
        rejected Candidate and an approved commit stay distinguishable because each
        points at the existing record that establishes it. No second conclusion
        store is created here.
        """

        await self._require_target(scope_id)
        async with self._database.transaction() as connection:
            run = await self._require_run(connection, scope_id, run_id)
            if run.state == "invalidated":
                raise ShareRuleInvalidatedError(run.rule_id)
            resolved = run.outcome if outcome is None else outcome
            if committed is not None:
                await self._artifacts.get(connection, scope_id, committed)
                resolved = "committed"
            elif resolved == "committed":
                raise ShareRunStateError(run_id, run.state)
            return await self.runs.replace(
                connection,
                run.model_copy(
                    update={
                        "outcome": resolved,
                        "dream_run_id": dream_run_id if dream_run_id is not None else run.dream_run_id,
                        "candidate_id": candidate_id if candidate_id is not None else run.candidate_id,
                        "committed": None
                        if committed is None
                        else ArtifactAddress(scope_id=scope_id, artifact=committed),
                    }
                ),
            )

    # -- discovery -----------------------------------------------------------

    async def _discover(
        self, scope_id: str, rule: ShareRule
    ) -> tuple[tuple[ShareInput, ...], tuple[str, ...], tuple[ShareSourceWatermark, ...], ArtifactAddress | None]:
        """Read the agreed range once and pin every eligible address it covers."""

        resolved = await self._scopes.resolve_selection(rule.selection)
        origins = tuple(dict.fromkeys((*(scope.scope_id for scope in resolved), *rule.exact_scope_ids)))
        if len(origins) > rule.limits.max_origins:
            raise ShareBudgetExceededError("origins", rule.limits.max_origins, len(origins))
        draft = rule.draft()
        selected = frozenset(origin for origin in origins if origin in {scope.scope_id for scope in resolved})
        inputs: dict[str, ShareInput] = {}
        watermarks: list[ShareSourceWatermark] = []
        revisions = 0
        async with self._database.transaction(consistent_snapshot=True) as connection:
            for origin in origins:
                await self._require_scope(connection, origin)
                if origin == scope_id:
                    # A rule selects external material. The target's own content and
                    # revisions never supply new facts for revising itself.
                    continue
                paths = draft.selection_paths(origin, selected)
                if not paths:
                    continue
                position = await self._sources.journal_position(connection, origin)
                watermarks.append(ShareSourceWatermark(origin_scope_id=origin, position=position))
                for stored in await self._sources.list(
                    connection,
                    origin,
                    through=position,
                    limit=rule.limits.max_inputs + 1,
                ):
                    if rule.source_types and stored.ref.source_type not in rule.source_types:
                        continue
                    if not is_generation_eligible(stored.value):
                        continue
                    _merge(
                        inputs,
                        ShareInput(
                            kind="source",
                            origin_scope_id=origin,
                            paths=paths,
                            digest=content_digest(stored.value.model_dump_json().encode()),
                            materialization=stored.value.materialization,
                            source_ref=SourceRef(
                                source_type=stored.ref.source_type,
                                source_id=stored.ref.source_id,
                            ),
                        ),
                    )
                    self._bounded(len(inputs), rule.limits.max_inputs, "inputs")
                for family in rule.artifact_families:
                    for ref in await self._heads(connection, origin, family):
                        revisions += 1
                        self._bounded(revisions, rule.limits.max_artifact_revisions, "artifact_revisions")
                        artifact = await self._artifacts.get(connection, origin, ref)
                        _merge(
                            inputs,
                            ShareInput(
                                kind="artifact",
                                origin_scope_id=origin,
                                paths=paths,
                                digest=content_digest(artifact.model_dump_json().encode()),
                                artifact=ArtifactAddress(scope_id=origin, artifact=ref),
                            ),
                        )
                        self._bounded(len(inputs), rule.limits.max_inputs, "inputs")
            baseline = await self._baseline(connection, scope_id, rule)
        pinned = tuple(sorted(inputs.values(), key=ShareInput.address_key))
        await self._authorize_origins(pinned)
        return pinned, origins, tuple(watermarks), baseline

    async def _heads(self, connection: AsyncConnection, scope_id: str, family: str) -> tuple[ArtifactRef, ...]:
        """Read the current revision of each identity in one external Scope."""

        rows = (
            await connection.execute(
                select(
                    ARTIFACT_HEADS_TABLE.c.family, ARTIFACT_HEADS_TABLE.c.artifact_id, ARTIFACT_HEADS_TABLE.c.revision
                )
                .where(
                    ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                    ARTIFACT_HEADS_TABLE.c.family == family,
                )
                .order_by(ARTIFACT_HEADS_TABLE.c.artifact_id)
            )
        ).all()
        return tuple(ArtifactRef(family=str(row[0]), artifact_id=str(row[1]), revision=int(row[2])) for row in rows)

    async def _baseline(self, connection: AsyncConnection, scope_id: str, rule: ShareRule) -> ArtifactAddress | None:
        """Pin the target's current maintained conclusion, not an origin revision."""

        if not rule.artifact_families:
            return None
        for family in rule.artifact_families:
            heads = await self._heads(connection, scope_id, family)
            if len(heads) == 1:
                return ArtifactAddress(scope_id=scope_id, artifact=heads[0])
        return None

    # -- checks --------------------------------------------------------------

    async def _require_target(self, scope_id: str) -> None:
        if self._authorize_target is not None:
            await self._authorize_target(scope_id)

    async def _authorize_origins(self, inputs: tuple[ShareInput, ...]) -> None:
        if self._authorize_origin is None:
            return
        for item in inputs:
            reference: ArtifactAddress | SourceRef | None = (
                item.artifact if item.artifact is not None else item.source_ref
            )
            if reference is None:
                raise ShareRunStateError(item.address_key(), "unreferenced")
            await self._authorize_origin(item.origin_scope_id, reference)

    async def _require_accepted(
        self,
        rule: ShareRule,
        required_origins: tuple[str, ...],
        *,
        connection: AsyncConnection | None = None,
    ) -> None:
        """Require recorded acceptance covering the origins used and declared audiences.

        Agreement is durable state, so this reads the record; no request field can
        assert it. Coverage composes across accepting actors, which is what lets two
        contributors agree to their own Scopes and still admit one shared run.
        """

        binding = rule.terms_binding()
        digest = binding.digest()
        if connection is None:
            async with self._database.transaction() as own:
                origins, audiences = await self.acceptances.coverage(
                    own, rule.scope_id, rule.rule_id, rule.version, digest
                )
        else:
            origins, audiences = await self.acceptances.coverage(
                connection, rule.scope_id, rule.rule_id, rule.version, digest
            )
        covered = await self._covered_origins(rule, origins)
        for origin in required_origins:
            if origin not in covered:
                raise ShareRuleAcceptanceError(rule.rule_id, rule.version, f"origin:{origin}")
        for audience in binding.audience_scope_ids:
            if audience not in audiences:
                raise ShareRuleAcceptanceError(rule.rule_id, rule.version, f"audience:{audience}")

    async def coverage(self, scope_id: str, rule_id: str, rule_version: int, /) -> ShareRunCoverage:
        """Report whether this rule version's recorded agreement covers a run's inputs.

        This is the reader other packages confirm with. It matches a run's actual
        origins against the authenticated accepted coverage; a caller-supplied
        digest is never treated as consent.
        """

        async with self._database.transaction() as connection:
            rule = await self.rules.version(connection, scope_id, rule_id, rule_version)
            if rule is None:
                raise ShareRuleNotFoundError(scope_id, rule_id, rule_version)
            origins, audiences = await self.acceptances.coverage(
                connection, scope_id, rule_id, rule_version, rule.terms_binding().digest()
            )
            runs = await self.runs.list(connection, scope_id, rule_id=rule_id)
        return ShareRunCoverage(
            rule_id=rule_id,
            rule_version=rule_version,
            terms_digest=rule.terms_binding().digest(),
            accepted_origin_scope_ids=origins,
            accepted_audience_scope_ids=audiences,
            declared_audience_scope_ids=rule.terms.audience_scope_ids,
            run_origin_scope_ids=tuple(sorted({origin for run in runs for origin in run.origin_scopes()})),
        )

    async def covers_run(self, scope_id: str, run_id: str, /) -> bool:
        """Confirm one recorded run's actual origins are inside the accepted agreement."""

        async with self._database.transaction() as connection:
            run = await self._require_run(connection, scope_id, run_id)
            rule = await self.rules.version(connection, scope_id, run.rule_id, run.rule_version)
            if rule is None:
                return False
            origins, audiences = await self.acceptances.coverage(
                connection, scope_id, run.rule_id, run.rule_version, rule.terms_binding().digest()
            )
        return all(origin in origins for origin in run.origin_scopes()) and all(
            audience in audiences for audience in rule.terms.audience_scope_ids
        )

    @staticmethod
    def _declared_origins(rule: ShareRule) -> tuple[str, ...]:
        """Name the origins a version declares before discovery, excluding its target."""

        return tuple(origin for origin in rule.declared_origins() if origin != rule.scope_id)

    async def _covered_origins(self, rule: ShareRule, accepted: tuple[str, ...]) -> set[str]:
        """Expand accepted ranges into the exact origins they authorise.

        Accepting a declared range covers the materials that range qualifies now and
        later, so descendants of an accepted subtree root count as covered while an
        exact Scope stays exact.
        """

        covered = set(accepted)
        root_scope_id = rule.selection.root_scope_id
        if root_scope_id is not None and root_scope_id in accepted:
            subtree = await self._scopes.resolve_selection(ShareSelection(mode="subtree", root_scope_id=root_scope_id))
            covered.update(scope.scope_id for scope in subtree)
        return covered

    async def _require_scope(self, connection: AsyncConnection, scope_id: str) -> None:
        if await self._scope_repository.get(connection, scope_id) is None:
            raise ScopeNotFoundError(scope_id)

    async def _require_origins(self, connection: AsyncConnection, draft: ShareRuleDraft) -> None:
        for origin in (*draft.selection.scope_ids, *draft.exact_scope_ids):
            await self._require_scope(connection, origin)
        root_scope_id = draft.selection.root_scope_id
        if root_scope_id is not None:
            await self._require_scope(connection, root_scope_id)

    async def _require_rule(
        self, connection: AsyncConnection, scope_id: str, rule_id: str, *, for_update: bool = False
    ) -> ShareRule:
        rule = await self.rules.head(connection, scope_id, rule_id, for_update=for_update)
        if rule is None:
            raise ShareRuleNotFoundError(scope_id, rule_id)
        return rule

    async def _require_run(self, connection: AsyncConnection, scope_id: str, run_id: str) -> ShareRun:
        run = await self.runs.get(connection, scope_id, run_id)
        if run is None:
            raise ShareRunNotFoundError(scope_id, run_id)
        return run

    @staticmethod
    def _bounded(observed: int, maximum: int, limit: str) -> None:
        if observed > maximum:
            raise ShareBudgetExceededError(limit, maximum, observed)

    def _now(self) -> datetime:
        return self._clock()


def _merge(inputs: dict[str, ShareInput], item: ShareInput) -> None:
    """Count one exact address once while retaining every path that selected it."""

    key = item.address_key()
    existing = inputs.get(key)
    if existing is None:
        inputs[key] = item
        return
    inputs[key] = existing.model_copy(update={"paths": tuple(dict.fromkeys((*existing.paths, *item.paths)))})


__all__ = ["ShareAuthorizer", "ShareOriginCheck", "ShareRuleService"]
