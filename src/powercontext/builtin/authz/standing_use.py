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

D records which standing selector and terms an actor accepted; it deliberately
does not check `artifact.share`, `scope.admin`, an audience or a retention. This
module is the owning boundary that does, at the moments that matter:

* before an acceptance is written, that the *authenticated* actor really holds
  the authority the coverage it claims needs,
* at discovery, that every origin a run reaches is still authorized for the
  execution principal,
* when a pinned run is used, that the accepted coverage still holds and that the
  accepting actors' authority has not been withdrawn.

Two identities stay distinct on purpose. ``ShareTermsBinding.digest()`` binds the
standing selector and terms, so an acceptance survives facts discovered later.
``SharedUseTerms.digest`` binds one run's exact origins, so a run cannot silently
adopt a different input. This module never compares the two digests, never
rewrites a standing acceptance as today's exact inputs, and never fabricates an
acceptance id: durable coverage is read back from D's rows.

**Trusted caller.** ``accept`` and the per-use rechecks take the principal the
owning caller already authenticated. This boundary never resolves identity and
cannot detect a caller that passes a decoded request field instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from powercontext.artifacts import ArtifactAddress
from powercontext.builtin.authz.errors import AccessDeniedError, AccessInvalidRequestError
from powercontext.builtin.authz.models import AccessAction, PrincipalRef, ResourceRef
from powercontext.builtin.authz.service import AccessAuditContext, AccessControlService
from powercontext.builtin.sharing.models import (
    ShareAcceptance,
    ShareAcceptanceSide,
    ShareRule,
    ShareRuleAcceptanceError,
    ShareRun,
    ShareRunCoverage,
)
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.sharing.service import ShareRuleService

# D's retention labels and B's SharedRetention are different vocabularies; map the
# one supported pair and refuse anything this boundary cannot enforce.
_SUPPORTED_RETENTION = {"reference": "reference", "independent": "independent"}


@dataclass(frozen=True, slots=True)
class StandingUseGrant:
    """Content-free result of rechecking one pinned run against current authority."""

    rule_id: str
    rule_version: int
    terms_digest: str
    accepted_origin_scope_ids: tuple[str, ...]
    accepted_audience_scope_ids: tuple[str, ...]
    run_origin_scope_ids: tuple[str, ...]


class StandingUseAuthorization:
    """Check current authority behind D's recorded standing agreement."""

    def __init__(self, access: AccessControlService, *, transport: str = "background") -> None:
        self.access = access
        self._transport = transport

    def context(self, operation: str, request_id: str | None = None) -> AccessAuditContext:
        return AccessAuditContext(transport=self._transport, operation=operation, request_id=request_id)

    # -- authority the acceptance itself needs --------------------------------

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

        The caller passes what it claims to cover; anything outside the version's
        declared range is refused by D. This narrows it further to Scopes where the
        actor actually holds the authority for that side, so a verified identity
        cannot sign for someone else's origin.
        """

        if principal is None:
            raise AccessDeniedError
        self._require_supported_retention(rule)
        targets = self._side_resources(rule, side, covered_scope_ids)
        allowed: list[str] = []
        for scope_id, resource, action in targets:
            if await self._allowed(principal, action, resource, context):
                allowed.append(scope_id)
        if not allowed:
            raise AccessDeniedError
        if len(allowed) != len(targets):
            # Partial authority would silently record coverage the actor cannot back.
            raise AccessDeniedError
        return tuple(allowed)

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
        """Check this actor's authority, then record the durable acceptance in D."""

        claimed = covered_scope_ids
        if claimed is None:
            claimed = tuple(
                rule.terms_binding().declared_scope_ids if side == "origin" else rule.terms_binding().audience_scope_ids
            )
        accepted = await self.require_acceptance_authority(
            principal, rule, side, covered_scope_ids=claimed, context=context
        )
        # D reserves the row and returns its durable id; B never mints one.
        return await service.accept(
            rule.scope_id, rule.rule_id, rule.version, side, actor=principal_key(principal), covered_scope_ids=accepted
        )

    # -- current authority at discovery and use ------------------------------

    async def require_run_use(
        self,
        principal: PrincipalRef | None,
        rule: ShareRule,
        run: ShareRun,
        coverage: ShareRunCoverage,
        *,
        context: AccessAuditContext,
    ) -> StandingUseGrant:
        """Recheck a pinned run before its evidence is used or its result approved.

        Covers the execution principal's origin read and target write authority, the
        recorded audience coverage, and each accepting actor's still-current
        authority over exactly the coverage that actor recorded.
        """

        if principal is None:
            raise AccessDeniedError
        self._require_supported_retention(rule)
        if run.rule_id != rule.rule_id or run.rule_version != rule.version:
            raise AccessInvalidRequestError("shared_use_terms")
        if not coverage.audiences_covered:
            # A declared audience nobody accepted is not a suppressed audience.
            raise AccessDeniedError
        if not set(run.origin_scopes()) <= set(coverage.accepted_origin_scope_ids):
            raise AccessDeniedError
        for scope_id in sorted(coverage.declared_audience_scope_ids):
            if not await self._allowed(principal, AccessAction.SCOPE_ADMIN, ResourceRef.scope(scope_id), context):
                # The reading principal must actually belong to the accepted audience.
                raise AccessDeniedError
        await self._require_checks(principal, self._execution_checks(rule, run), context)
        return StandingUseGrant(
            rule_id=rule.rule_id,
            rule_version=rule.version,
            terms_digest=coverage.terms_digest,
            accepted_origin_scope_ids=coverage.accepted_origin_scope_ids,
            accepted_audience_scope_ids=coverage.accepted_audience_scope_ids,
            run_origin_scope_ids=coverage.run_origin_scope_ids,
        )

    async def require_acceptors_current(
        self,
        acceptances: tuple[ShareAcceptance, ...],
        rule: ShareRule,
        *,
        context: AccessAuditContext,
    ) -> None:
        """Recheck each accepting actor only for the coverage that actor recorded.

        One withdrawn contributor therefore blocks a run without needing anyone to
        stand in for the Scopes that contributor never covered.
        """

        for acceptance in acceptances:
            if not acceptance.covered_scope_ids:
                raise AccessDeniedError
            # Only the Scopes this actor recorded, never the run's whole origin set.
            checks = tuple(
                (action, resource)
                for _, resource, action in self._side_resources(rule, acceptance.side, acceptance.covered_scope_ids)
            )
            await self._require_checks(principal_ref(acceptance.actor), checks, context)

    # -- origin checks D calls during discovery ------------------------------

    async def require_target(
        self,
        principal: PrincipalRef | None,
        scope_id: str,
        *,
        context: AccessAuditContext,
    ) -> None:
        """Require current write authority in the target before a run starts."""

        await self._require_checks(principal, ((AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(scope_id)),), context)

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

    # -- helpers -------------------------------------------------------------

    def _require_supported_retention(self, rule: ShareRule) -> None:
        if rule.terms.retention not in _SUPPORTED_RETENTION:
            # An unknown retention is not a silently ignorable label.
            raise AccessInvalidRequestError("shared_use_retention")
        if not rule.terms.use or not rule.terms.audience:
            raise AccessInvalidRequestError("shared_use_not_agreed")

    def _side_resources(
        self,
        rule: ShareRule,
        side: ShareAcceptanceSide,
        scope_ids: tuple[str, ...],
    ) -> tuple[tuple[str, ResourceRef, AccessAction], ...]:
        if side == "origin":
            action = AccessAction.ARTIFACT_SHARE if rule.terms_binding().artifact_families else AccessAction.SCOPE_ADMIN
        else:
            action = AccessAction.SCOPE_ADMIN
        return tuple((scope_id, ResourceRef.scope(scope_id), action) for scope_id in scope_ids)

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


def principal_key(principal: PrincipalRef | None) -> str:
    """The stable actor spelling D reserves, matching its own identity form."""

    if principal is None:
        raise AccessDeniedError
    return f"{principal.type}:{principal.id}"


def principal_ref(actor: str) -> PrincipalRef:
    kind, _, identifier = actor.partition(":")
    if not identifier or kind not in {"user", "service"}:
        raise ShareRuleAcceptanceError("", 0, "actor")
    return PrincipalRef(type=kind, id=identifier)


__all__ = ("StandingUseAuthorization", "StandingUseGrant", "principal_key", "principal_ref")
