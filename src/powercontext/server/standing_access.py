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

from typing import TYPE_CHECKING

from powercontext.artifacts import ArtifactAddress
from powercontext.builtin.authz.errors import AccessIdentityRequiredError
from powercontext.builtin.authz.service import AccessControlService
from powercontext.builtin.authz.standing_use import StandingUseAuthorization
from powercontext.server.context import current_principal, current_request_id

if TYPE_CHECKING:
    from typing import TypeAlias

    from powercontext.builtin.runtime.application import BuiltinRuntime
    from powercontext.builtin.sharing.models import ShareAcceptance, ShareAcceptanceSide, ShareRule
    from powercontext.builtin.sharing.service import ShareRuleService
    from powercontext.sources import SourceRef

    OriginReference: TypeAlias = ArtifactAddress | SourceRef


class StandingAccess:
    """Server adapter: authenticated principal plus B's authority checks.

    Every method fails closed when no principal is bound to the request, because a
    scheduled or background call has no session to authorize standing use with.
    """

    def __init__(self, access: AccessControlService) -> None:
        self.standing = StandingUseAuthorization(access)
        self.access = access

    def bind(self, runtime: BuiltinRuntime) -> None:
        runtime.configure_standing_use_authorization(self.authorize_origin, authorize_target=self.authorize_target)

    async def authorize_origin(self, scope_id: str, reference: OriginReference) -> None:
        """Discovery reached this origin: require the caller's current read authority."""

        await self.standing.require_origin(
            self._principal(), scope_id, reference, context=self._context("share_rule_discovery")
        )

    async def authorize_target(self, scope_id: str) -> None:
        """A run is starting or continuing in the target: require current write authority."""

        await self.standing.require_target(self._principal(), scope_id, context=self._context("share_run_target"))

    async def accept(
        self,
        service: ShareRuleService,
        rule: ShareRule,
        side: ShareAcceptanceSide,
        *,
        covered_scope_ids: tuple[str, ...] | None = None,
    ) -> ShareAcceptance:
        """Record the authenticated caller's own acceptance of this rule version."""

        return await self.standing.accept(
            service,
            self._principal(),
            rule,
            side,
            covered_scope_ids=covered_scope_ids,
            context=self._context("share_rule_accept"),
        )

    def _principal(self):
        principal = current_principal()
        if principal is None:
            raise AccessIdentityRequiredError
        return principal

    def _context(self, operation: str):
        return self.standing.context(operation, current_request_id())


__all__ = ("StandingAccess",)
