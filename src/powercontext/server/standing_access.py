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

"""Bind standing shared-use authority to the current Server request principal.

D's record says which standing selector and terms an actor accepted. This adapter
supplies the two things D deliberately does not: the *authenticated* principal and
the current Access checks behind it. It never reads an actor from a request field
and never mints an acceptance id.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

from powercontext.artifacts import ArtifactAddress
from powercontext.builtin.authz.errors import (
    AccessDeniedError,
    AccessIdentityRequiredError,
    AccessInvalidRequestError,
    AccessUnavailableError,
)
from powercontext.builtin.authz.service import AccessControlService
from powercontext.builtin.authz.standing_use import StandingUseAuthorization, StandingUseGrant, principal_key
from powercontext.server.context import (
    current_executing_principal,
    current_principal,
    current_request_id,
)

if TYPE_CHECKING:
    from typing import TypeAlias

    from powercontext.builtin.runtime.application import BuiltinRuntime
    from powercontext.builtin.sharing.models import ShareAcceptance, ShareAcceptanceSide, ShareRule
    from powercontext.builtin.sharing.service import ShareRuleService
    from powercontext.sources import SourceRef

    OriginReference: TypeAlias = ArtifactAddress | SourceRef


# Task-local: an acceptance call has already been authorized by B over exactly the
# coverage the actor claims, so D's blanket target check must not veto a contributor
# who administers only its own origin. A ContextVar keeps this per-request instead of
# mutating a shared callback.
_ACCEPTING: ContextVar[bool] = ContextVar("powercontext_standing_accepting", default=False)


class StandingAccess:
    """Server adapter: authenticated principal plus B's authority checks.

    Every method fails closed when no principal is bound to the request, because a
    scheduled or background call has no session to authorize standing use with.
    """

    def __init__(self, access: AccessControlService) -> None:
        self.standing = StandingUseAuthorization(access)
        self.access = access

    def bind(self, runtime: BuiltinRuntime) -> None:
        """Bind the complete contract after construction.

        Composition passes the same three checks to ``open_builtin_runtime``; this is
        the documented path for embedding code that already holds a Runtime. It binds
        every check, including the acceptance validator, so a post-construction bind
        is exactly as enforcing as factory construction.
        """

        runtime.configure_standing_use_authorization(
            self.authorize_origin,
            authorize_target=self.authorize_target,
            validate_acceptance=self.validate_acceptance,
        )

    def attach(self, runtime: BuiltinRuntime) -> None:
        """Retain the Runtime the checks were composed with, for run-use entrypoints."""

        self._runtime = runtime

    @property
    def service(self) -> ShareRuleService:
        service = getattr(self, "_runtime", None)
        if service is None or service.share_rules is None:
            raise AccessUnavailableError("standing_rules_unavailable")
        return service.share_rules

    async def authorize_origin(self, scope_id: str, reference: OriginReference) -> None:
        """Discovery reached this origin: require the caller's current read authority."""

        await self.standing.require_origin(
            self._principal(), scope_id, reference, context=self._context("share_rule_discovery")
        )

    async def authorize_target(self, scope_id: str) -> None:
        """A rule action or run start: require current authority in the target.

        D reuses this callback for managing a rule, starting its evaluation and
        recording an acceptance. During an acceptance B has already checked the
        actor's authority over the exact coverage it claims, and a contributor that
        administers only its own origin must not be required to administer the
        target as well.
        """

        if _ACCEPTING.get():
            return
        # D also calls this from background conclusion recording, where no session
        # principal exists; the recorded executor is that identity, never an ambient one.
        principal = current_principal() or current_executing_principal()
        if principal is None:
            raise AccessIdentityRequiredError
        await self.standing.require_target(principal, scope_id, context=self._context("share_run_target"))

    async def validate_acceptance(
        self,
        rule: ShareRule,
        side: ShareAcceptanceSide,
        actor: str,
        covered_scope_ids: tuple[str, ...],
    ) -> str:
        """Decide the durable actor for one acceptance, before D writes a row.

        This is D's injected acceptance validator, so every supported write path --
        the public ``ScopedShareApplication`` and the service itself -- is bound to the
        authenticated caller, not only the hand-held helper. The caller's claim is
        never trusted: the recorded identity is the authenticated principal, and that
        principal must currently hold the authority its claimed coverage needs.

        An ``actor`` naming somebody else is refused rather than reinterpreted, so an
        alternate raw path cannot impersonate; a form this boundary cannot read is
        refused before any row exists instead of leaving an unreadable enforced record.
        """

        principal = self._principal()
        canonical = principal_key(principal)
        if actor and actor != canonical:
            raise AccessDeniedError
        binding = rule.terms_binding()
        claimed = covered_scope_ids or tuple(
            binding.declared_scope_ids if side == "origin" else binding.audience_scope_ids
        )
        if not claimed:
            raise AccessInvalidRequestError("shared_use_audience")
        await self.standing.require_acceptance_authority(
            principal,
            rule,
            side,
            covered_scope_ids=tuple(claimed),
            context=self._context("share_rule_accept"),
        )
        return canonical

    async def accept(
        self,
        service: ShareRuleService,
        rule: ShareRule,
        side: ShareAcceptanceSide,
        *,
        covered_scope_ids: tuple[str, ...] | None = None,
    ) -> ShareAcceptance:
        """Record the authenticated caller's own acceptance of this rule version.

        The actor's authority over the part it claims is checked here, before D
        reserves the row, so no target-wide administration is implied.
        """

        token = _ACCEPTING.set(True)
        try:
            return await self.standing.accept(
                service,
                self._principal(),
                rule,
                side,
                covered_scope_ids=covered_scope_ids,
                context=self._context("share_rule_accept"),
            )
        finally:
            _ACCEPTING.reset(token)

    async def require_run_use(
        self,
        service: ShareRuleService,
        *,
        scope_id: str,
        rule_id: str,
        version: int,
        run_id: str,
        use: str,
    ) -> StandingUseGrant:
        """Admit one pinned run for one requested use as the authenticated caller.

        Loads the rule version, run, recorded agreement and coverage from D, then
        checks the head status, requested use, acceptors and the caller's own origin
        read and target write together. No coverage or digest is taken from the
        caller.
        """

        return await self.standing.require_run_use(
            self._principal(),
            service,
            scope_id=scope_id,
            rule_id=rule_id,
            version=version,
            run_id=run_id,
            use=use,
            context=self._context("share_run_use"),
        )

    def _principal(self):
        principal = current_principal()
        if principal is None:
            raise AccessIdentityRequiredError
        return principal

    def _context(self, operation: str):
        return self.standing.context(operation, current_request_id())


__all__ = ("StandingAccess",)
