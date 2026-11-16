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

"""B's authority on D's standing-rule record.

D records which standing selector and terms an actor accepted and deliberately
checks no authority. This module is the owning boundary that does, at the moments
that matter:

* before an acceptance is written, that the *authenticated* actor really holds the
  authority the coverage it claims needs,
* at discovery, that every origin a run reaches is still authorized for the
  execution principal,
* when a pinned run is used, that the accepted coverage and the accepting actors'
  authority both still hold.

Two identities stay distinct on purpose. ``ShareTermsBinding.digest()`` binds the
standing selector and terms, so an agreement survives facts discovered later.
``SharedUseTerms.digest`` binds one run's exact origins, so a run cannot silently
adopt different inputs. This module never compares the two digests, never rewrites
a standing acceptance as today's exact inputs, and never fabricates an acceptance
id: durable coverage is read back from D's rows.

**Trusted caller.** Every entry point takes the principal the owning caller already
authenticated. This boundary never resolves identity and cannot detect a caller
that passes a decoded request field instead.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING

from powercontext.artifacts import ArtifactAddress
from powercontext.builtin.authz.errors import (
    AccessControlError,
    AccessDeniedError,
    AccessInvalidRequestError,
)
from powercontext.builtin.authz.models import AccessAction, PrincipalRef, ResourceRef
from powercontext.builtin.authz.service import AccessAuditContext, AccessControlService
from powercontext.builtin.sharing.models import (
    ShareAcceptance,
    ShareAcceptanceContribution,
    ShareAcceptanceSide,
    ShareRule,
    ShareRuleAcceptanceError,
    ShareRuleInvalidatedError,
    ShareRun,
    ShareRunCoverage,
    ShareRunOriginCoverage,
)
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.sharing.service import ShareRuleService

# D's retention labels. Only these two are meaningful; anything else is refused
# rather than treated as an ignorable label.
_SUPPORTED_RETENTION = frozenset({"reference", "independent"})


@dataclass(frozen=True, slots=True)
class StandingUseGrant:
    """Content-free result of admitting one pinned run for one requested use."""

    rule_id: str
    rule_version: int
    terms_digest: str
    use: str
    audience: str
    accepted_origin_scope_ids: tuple[str, ...]
    accepted_audience_scope_ids: tuple[str, ...]
    run_origin_scope_ids: tuple[str, ...]
    acceptance_ids: tuple[str, ...]


class StandingUseAuthorization:
    """Check current authority behind D's recorded standing agreement."""

    def __init__(self, access: AccessControlService, *, transport: str = "background") -> None:
        self.access = access
        self._transport = transport
        # Rebuilt for each admission in require_run_use; never shared or reused.
        self._contributions: dict[str, ShareAcceptanceContribution] = {}

    def context(self, operation: str, request_id: str | None = None) -> AccessAuditContext:
        return AccessAuditContext(transport=self._transport, operation=operation, request_id=request_id)

    # -- acceptance -----------------------------------------------------------

    async def require_acceptance_authority(
        self,
        principal: PrincipalRef | None,
        rule: ShareRule,
        side: ShareAcceptanceSide,
        *,
        covered_scope_ids: tuple[str, ...],
        context: AccessAuditContext,
    ) -> tuple[str, ...]:
        """Return the coverage this acting principal may really accept, or refuse.

        A Scope-wide agreement needs authority over that Scope for the material the
        rule selects. An exact Artifact origin needs authority over that Artifact.
        Partial authority is refused instead of silently recording coverage the
        actor cannot back.
        """

        if principal is None:
            raise AccessDeniedError
        self._require_supported_terms(rule)
        checks = self._coverage_checks(rule, side, covered_scope_ids)
        for _, item in checks:
            if not await self._allowed(principal, *item, context):
                raise AccessDeniedError
        return tuple(scope_id for scope_id, _ in checks)

    async def accept(
        self,
        service: ShareRuleService,
        principal: PrincipalRef | None,
        rule: ShareRule,
        side: ShareAcceptanceSide,
        *,
        covered_scope_ids: tuple[str, ...] | None,
        context: AccessAuditContext,
    ) -> ShareAcceptance:
        """Check this actor's authority, then record the durable acceptance in D.

        ``covered_scope_ids=None`` means "the whole declared side", matching D's
        own default. D reserves the row and its id; B never mints one.
        """

        binding = rule.terms_binding()
        claimed = covered_scope_ids
        if claimed is None:
            claimed = tuple(binding.declared_scope_ids if side == "origin" else binding.audience_scope_ids)
        if not claimed:
            # D normalizes an empty raw audience to the target Scope, so a declared
            # side is only empty when the version itself declares nothing to cover.
            raise AccessInvalidRequestError("shared_use_audience")
        accepted = await self.require_acceptance_authority(
            principal, rule, side, covered_scope_ids=claimed, context=context
        )
        return await service.accept(
            rule.scope_id, rule.rule_id, rule.version, side, actor=principal_key(principal), covered_scope_ids=accepted
        )

    # -- run use --------------------------------------------------------------

    async def require_run_use(
        self,
        principal: PrincipalRef | None,
        service: ShareRuleService,
        *,
        scope_id: str,
        rule_id: str,
        version: int,
        run_id: str,
        use: str,
        context: AccessAuditContext,
    ) -> StandingUseGrant:
        """Admit one pinned run from authoritative ids, checking everything once.

        Loads the rule version, run, recorded agreement and coverage from D, then
        checks the current head status and selector binding, the requested use and
        declared audience against the accepted terms, the accepting actors, and the
        execution principal's origin read and target write authority together. A
        caller cannot pass a coverage, digest or acceptance of its own.
        """

        if principal is None:
            raise AccessDeniedError
        rule = await _rule(service, scope_id, rule_id, version)
        self._require_supported_terms(rule)
        if use != rule.terms.use:
            # Requested use must be the accepted one, not any non-empty label.
            raise AccessInvalidRequestError("shared_use_not_agreed")
        run = await service.get_run(rule.scope_id, run_id)
        if run.rule_id != rule_id or run.rule_version != version:
            raise AccessInvalidRequestError("shared_use_terms")
        await self._require_head_status(service, rule)

        # One pinned manifest, not the version-wide union: this answers whose consent
        # applies to the origins *this* run recorded.
        coverage = await service.run_origin_coverage(rule.scope_id, run_id)
        if coverage.terms_digest != rule.terms_binding().digest():
            raise AccessInvalidRequestError("shared_use_terms")
        if set(coverage.run_origin_scope_ids) != set(run.origin_scopes()):
            # The projection and the manifest disagree; never admit on that.
            raise AccessInvalidRequestError("shared_use_terms")
        if not coverage.origins_covered:
            raise ShareRuleAcceptanceError(rule.rule_id, rule.version, "origin:run")
        if not coverage.audiences_covered:
            # A declared audience nobody accepted is not a suppressed audience.
            raise ShareRuleAcceptanceError(rule.rule_id, rule.version, "audience:run")

        await self._require_audience_member(principal, coverage, context)
        # Per-admission memo: a record's contribution is immutable within one check and
        # is never reused across requests, so no authority decision is cached.
        self._contributions = {}
        used = await self._require_run_acceptors(service, rule, coverage, run, context)
        await self._require_checks(principal, self._execution_checks(rule, run), context)
        return StandingUseGrant(
            rule_id=rule_id,
            rule_version=version,
            terms_digest=coverage.terms_digest,
            use=rule.terms.use,
            audience=rule.terms.audience,
            accepted_origin_scope_ids=coverage.covered_origin_scope_ids,
            accepted_audience_scope_ids=coverage.accepted_audience_scope_ids,
            run_origin_scope_ids=coverage.run_origin_scope_ids,
            acceptance_ids=_unique(used),
        )

    async def _require_run_acceptors(
        self,
        service: ShareRuleService,
        rule: ShareRule,
        coverage: ShareRunOriginCoverage,
        run: ShareRun,
        context: AccessAuditContext,
    ) -> tuple[str, ...]:
        """Check the consent records that actually contribute to this pinned run.

        Each required origin needs at least one contributing acceptance whose actor
        still holds authority *where that material is*; a record that contributes
        nothing to this manifest cannot veto it. Audiences work the same way.
        """

        used: list[str] = []
        for origin in coverage.covered_origin_scope_ids:
            contributor = await self._first_valid_contributor(
                service, rule, run, origin, coverage.acceptance_ids_for(origin), context
            )
            if contributor is None:
                raise AccessDeniedError
            used.append(contributor)

        audiences = set(coverage.declared_audience_scope_ids)
        if audiences:
            records = await service.acceptances_for(rule.scope_id, rule.rule_id, rule.version)
            # Sorted so the outcome cannot depend on storage order: any one valid record
            # per required audience suffices, and an unreadable legacy row is simply not
            # a candidate rather than a fatal error.
            ordered = sorted(records, key=lambda record: record.acceptance_id)
            for scope_id in sorted(audiences):
                for record in ordered:
                    if record.side != "target" or scope_id not in record.covered_scope_ids:
                        continue
                    if await self._actor_holds(record.actor, rule, "target", (scope_id,), context):
                        used.append(record.acceptance_id)
                        break
                else:
                    raise AccessDeniedError
        return tuple(used)

    async def _first_valid_contributor(
        self,
        service: ShareRuleService,
        rule: ShareRule,
        run: ShareRun,
        origin: str,
        acceptance_ids: tuple[str, ...],
        context: AccessAuditContext,
    ) -> str | None:
        """The first acceptance for this origin whose actor still holds authority there."""

        for acceptance_id in sorted(acceptance_ids):
            contribution = await self._contribution(service, rule, run, acceptance_id)
            if origin not in contribution.contributed_origin_scope_ids:
                # This record does not contribute to this pinned manifest.
                continue
            if await self._actor_holds(contribution.actor, rule, "origin", (origin,), context):
                return contribution.acceptance_id
        return None

    async def _contribution(
        self,
        service: ShareRuleService,
        rule: ShareRule,
        run: ShareRun,
        acceptance_id: str,
    ) -> ShareAcceptanceContribution:
        """One authoritative row's contribution to this run, loaded at most once.

        A record covering N origins answers the same question for all of them, so the
        whole-run contribution is memoized per admission instead of re-derived per
        origin. The value is immutable within this check and nothing is kept after it.
        """

        cached = self._contributions.get(acceptance_id)
        if cached is None:
            cached = await service.acceptance_contribution(rule.scope_id, run.run_id, acceptance_id)
            self._contributions[acceptance_id] = cached
        return cached

    async def _actor_holds(
        self,
        actor: str,
        rule: ShareRule,
        side: ShareAcceptanceSide,
        scope_ids: tuple[str, ...],
        context: AccessAuditContext,
    ) -> bool:
        """Whether one recorded actor currently holds its claimed authority."""

        try:
            principal = principal_ref(actor)
        except AccessControlError:
            # A record this boundary cannot read is not a live consent.
            return False
        checks = tuple(item for _, item in self._coverage_checks(rule, side, scope_ids))
        try:
            await self._require_checks(principal, checks, context)
        except AccessControlError:
            return False
        return True

    async def require_acceptors_current(
        self,
        acceptances: tuple[ShareAcceptance, ...],
        rule: ShareRule,
        *,
        required_origin_scope_ids: Collection[str],
        context: AccessAuditContext,
    ) -> None:
        """Require a currently valid acceptor for each origin and audience this run needs.

        Relevance is decided by the run, not by the rule: an acceptance matters only
        when it recorded the very Scope the pinned run reached, or a declared audience.
        A Scope the run never used cannot veto it.

        Authority is checked at the required Scope, never at a structural ancestor.
        Even when the selector's range root qualifies a child, that root's
        administrator is not the child's authority: the actor must hold current
        authority where the material actually is.
        """

        # Structural range applicability (a declared root qualifying a descendant) is
        # the owning selector's rule; until D exposes it per acceptance, coverage of a
        # child by a recorded root is fail-closed rather than assumed here.
        await self._require_scope_consent(
            acceptances,
            rule,
            side="origin",
            required_scope_ids=required_origin_scope_ids,
            context=context,
        )
        await self._require_scope_consent(
            acceptances,
            rule,
            side="target",
            required_scope_ids=rule.terms_binding().audience_scope_ids,
            context=context,
        )

    async def _require_scope_consent(
        self,
        acceptances: tuple[ShareAcceptance, ...],
        rule: ShareRule,
        *,
        side: ShareAcceptanceSide,
        required_scope_ids: Collection[str],
        context: AccessAuditContext,
    ) -> None:
        """One currently valid acceptor is needed for every Scope this side requires."""

        for scope_id in sorted(set(required_scope_ids)):
            applicable = tuple(
                acceptance
                for acceptance in acceptances
                if acceptance.side == side and scope_id in acceptance.covered_scope_ids
            )
            if not applicable:
                # No recorded consent names this required Scope.
                raise AccessDeniedError
            for acceptance in applicable:
                checks = tuple(item for _, item in self._coverage_checks(rule, side, (scope_id,)))
                try:
                    await self._require_checks(principal_ref(acceptance.actor), checks, context)
                except AccessDeniedError:
                    continue
                # A redundant acceptor losing authority does not veto a valid one.
                break
            else:
                raise AccessDeniedError

    # -- origin checks D calls during discovery ------------------------------

    async def require_origin(
        self,
        principal: PrincipalRef | None,
        scope_id: str,
        reference: ArtifactAddress | SourceRef,
        *,
        context: AccessAuditContext,
    ) -> None:
        """Check one origin under the executing principal's current authority."""

        if principal is None:
            raise AccessDeniedError
        if isinstance(reference, ArtifactAddress):
            await self._require_checks(
                principal,
                (
                    (
                        AccessAction.ARTIFACT_READ,
                        ResourceRef.artifact(
                            reference.scope_id,
                            family=reference.artifact.family,
                            artifact_id=reference.artifact.artifact_id,
                        ),
                    ),
                ),
                context,
            )
            return
        await self._require_checks(principal, ((AccessAction.SCOPE_READ, ResourceRef.scope(scope_id)),), context)

    async def require_target(
        self,
        principal: PrincipalRef | None,
        scope_id: str,
        *,
        context: AccessAuditContext,
    ) -> None:
        """Require current authority in the target before a rule action or run starts.

        D uses one callback for managing a rule and for starting its evaluation, so
        this accepts either: an administrator owns the rule, an executor runs it.
        """

        if principal is None:
            raise AccessDeniedError
        for action in (AccessAction.SCOPE_ADMIN, AccessAction.SCOPE_CONTRIBUTE):
            if await self._allowed(principal, action, ResourceRef.scope(scope_id), context):
                return
        raise AccessDeniedError

    # -- helpers -------------------------------------------------------------

    def _require_supported_terms(self, rule: ShareRule) -> None:
        if rule.terms.retention not in _SUPPORTED_RETENTION:
            # An unknown retention is not a silently ignorable label.
            raise AccessInvalidRequestError("shared_use_retention")
        if not rule.terms.use or not rule.terms.audience:
            raise AccessInvalidRequestError("shared_use_not_agreed")

    async def _require_head_status(self, service: ShareRuleService, rule: ShareRule) -> None:
        head = await service.get(rule.scope_id, rule.rule_id)
        if head.status != "active" or head.version != rule.version:
            # An invalidated rule or a superseded version is not a live agreement.
            raise ShareRuleInvalidatedError(rule.rule_id)

    async def _require_audience_member(
        self,
        principal: PrincipalRef,
        coverage: ShareRunOriginCoverage,
        context: AccessAuditContext,
    ) -> None:
        """The reading principal must belong to the declared audience.

        Membership and execution rights, not its administration: an executor that may
        write the target is an authorized consumer even without `scope.admin` there.
        """

        for scope_id in sorted(coverage.declared_audience_scope_ids):
            checks = (
                (AccessAction.SCOPE_READ, ResourceRef.scope(scope_id)),
                (AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(scope_id)),
            )
            if not any([await self._allowed(principal, *item, context) for item in checks]):
                raise AccessDeniedError

    async def _require_coverage(self, rule: ShareRule, run: ShareRun, coverage: ShareRunCoverage) -> None:
        if not coverage.audiences_covered:
            # A declared audience nobody accepted is not a suppressed audience.
            raise AccessDeniedError
        # Use D's own coverage contract rather than re-deriving set membership here,
        # so a corrected expansion in D's reader is inherited instead of duplicated.
        if not coverage.origins_covered:
            raise ShareRuleAcceptanceError(rule.rule_id, rule.version, "origin:recorded")
        if not set(run.origin_scopes()) <= set(coverage.accepted_origin_scope_ids):
            # A required origin outside what the record covers breaks the agreement.
            raise ShareRuleAcceptanceError(rule.rule_id, rule.version, "origin:run")

    def _coverage_checks(
        self,
        rule: ShareRule,
        side: ShareAcceptanceSide,
        scope_ids: tuple[str, ...],
    ) -> tuple[tuple[str, tuple[AccessAction, ResourceRef]], ...]:
        """Authority one actor needs for the part of the agreement it claims.

        Origin coverage is a claim about a Scope's material, so the Scope
        administrator agrees to it: ``artifact.share`` is Artifact-level and invalid
        on a Scope resource, and ``scope.contribute`` only admits local writes.
        Audience coverage is membership of the declaring Scope, which the Scope
        administration decides; it never requires the audience member's consent.
        """

        # Both sides are a declaration about a Scope, so its administrator is who
        # agrees. Consumer eligibility is a separate check at use time; making it the
        # acceptance condition would confuse manager consent with membership.
        return tuple((scope_id, (AccessAction.SCOPE_ADMIN, ResourceRef.scope(scope_id))) for scope_id in scope_ids)

    def _execution_checks(self, rule: ShareRule, run: ShareRun) -> tuple[tuple[AccessAction, ResourceRef], ...]:
        checks: list[tuple[AccessAction, ResourceRef]] = [
            (AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(rule.scope_id))
        ]
        for item in run.inputs:
            if item.artifact is not None:
                checks.append((
                    AccessAction.ARTIFACT_READ,
                    ResourceRef.artifact(
                        item.origin_scope_id,
                        family=item.artifact.artifact.family,
                        artifact_id=item.artifact.artifact.artifact_id,
                    ),
                ))
            elif item.source_ref is not None:
                checks.append((AccessAction.SCOPE_READ, ResourceRef.scope(item.origin_scope_id)))
        return tuple(checks)

    async def _allowed(
        self,
        principal: PrincipalRef,
        action: AccessAction,
        resource: ResourceRef,
        context: AccessAuditContext,
    ) -> bool:
        decision = await self.access.check(principal, action, resource, context=context)
        return decision.allowed

    async def _require_checks(
        self,
        principal: PrincipalRef | None,
        checks: tuple[tuple[AccessAction, ResourceRef], ...],
        context: AccessAuditContext,
    ) -> None:
        if not checks:
            return
        if self.access.provider_capabilities.multi_requirement_check:
            await self.access.require_all(principal, checks, context=context)
            return
        for action, resource in checks:
            await self.access.require(principal, action, resource, context=context)


async def _rule(service: ShareRuleService, scope_id: str, rule_id: str, version: int) -> ShareRule:
    return await service.get(scope_id, rule_id, version=version)


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    """Keep first-seen order: one record covering N origins is reported once."""

    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return tuple(seen)


def principal_key(principal: PrincipalRef | None) -> str:
    """The stable actor spelling D reserves, matching its own identity form."""

    if principal is None:
        raise AccessDeniedError
    return f"{principal.type}:{principal.id}"


def principal_ref(actor: str) -> PrincipalRef:
    """Read back the canonical actor spelling, refusing forms this boundary cannot vouch for.

    Raises the Access refusal, not a sharing-domain error: parsing an identity is this
    boundary's own step, and callers decide whether an unreadable record is skipped or
    fatal. A free-form legacy actor (no ``type:`` prefix) is not a principal here.
    """

    kind, _, identifier = actor.partition(":")
    if not identifier or kind not in {"user", "service"}:
        raise AccessInvalidRequestError("shared_use_acceptance")
    return PrincipalRef(type=kind, id=identifier)


__all__ = ("StandingUseAuthorization", "StandingUseGrant", "principal_key", "principal_ref")
