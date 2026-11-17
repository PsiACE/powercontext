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

"""Bind Dream and Review authorization to the current Server request principal."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typing_extensions import override

from powercontext.builtin.authz.dream import (
    DreamAuthorization,
    evidence_resource,
    principal_identity,
)
from powercontext.builtin.authz.dream import (
    _principal as executor_principal,
)
from powercontext.builtin.authz.errors import AccessControlError, AccessDeniedError, AccessIdentityRequiredError
from powercontext.builtin.authz.models import AccessAction, ResourceRef
from powercontext.builtin.authz.service import AccessAuditContext
from powercontext.builtin.authz.standing_use import StandingUseAuthorization
from powercontext.builtin.catalog_changes.models import TagDreamTarget
from powercontext.builtin.dream.models import DreamError
from powercontext.builtin.dream.shared_use import require_matching_inputs
from powercontext.builtin.evidence.resolver import EvidenceReference
from powercontext.builtin.persistence.dream import DreamRepository
from powercontext.builtin.review.models import Candidate
from powercontext.builtin.sharing.models import ShareError
from powercontext.server.context import (
    bind_executing_principal,
    current_executing_principal,
    current_principal,
    current_request_id,
    reset_executing_principal,
)

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime


class DreamAccess(DreamAuthorization):
    """Add request-principal Review checks to Dream authorization."""

    def bind(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime
        self._bind_checks(runtime)

    def bind_shared_use(self, runtime: BuiltinRuntime) -> None:
        """Bind only the agreed-use gates, for a Server that composed the rest itself."""

        self._runtime = runtime
        runtime.configure_dream_shared_use(
            check_run_use=self.require_run_use,
            check_candidate_use=self.require_candidate_use,
            authorize_origin_use=self.authorize_origins,
            bind_executor=self._bind_executor,
            clear_executor=self._clear_executor,
        )

    @staticmethod
    def _bind_executor(principal_id: str):
        """Bind the run's recorded executor for background agreed-use rechecks."""

        return bind_executing_principal(executor_principal(principal_id))

    @staticmethod
    def _clear_executor(token) -> None:
        reset_executing_principal(token)

    def _bind_checks(self, runtime: BuiltinRuntime) -> None:
        runtime.configure_evidence_authorization(
            dream=self.authorize,
            review=self.review,
            review_many=self.review_many,
            context=self.access.defer_decision_audit,
            attest_candidate=self.attest_candidate,
            authorize_candidate=self.authorize_candidate,
            catalog_action=self.catalog_action,
            review_action=self.review_action,
            check_run_use=self.require_run_use,
            check_candidate_use=self.require_candidate_use,
        )

    async def require_run_use(self, share_run_id: str, run, record, *, admit: bool = True) -> None:
        """Admit one Dream run's foreign inputs under its pinned agreement.

        The durable run fixes rule, version and exact pinned inputs; the request must
        match them, and B then decides the requested use with the authenticated
        principal. No coverage, digest or acceptance actor is taken from the caller.
        """

        service = self._runtime.share_rules if self._runtime is not None else None
        if service is None:
            raise DreamError("shared_use_unavailable")
        try:
            pinned = await service.get_run(record.run.scope_id, share_run_id)
        except ShareError as error:
            raise DreamError("shared_use_unavailable") from error
        if admit and pinned.dream_run_id is not None and pinned.dream_run_id != record.run.run_id:
            # One pinned evaluation is claimed by exactly one Dream execution. A later
            # recheck of an already-bound run only re-authorizes that same binding.
            raise DreamError("shared_use_run")
        if record.request is None:
            raise DreamError("dream_request_unavailable")
        await require_matching_inputs(pinned, record.request, scope_id=record.run.scope_id)
        # Background execution has no session principal, so the recorded executor is the
        # identity this recheck runs as: the same one admission authorized.
        principal = executor_principal(record.principal_id)
        try:
            await StandingUseAuthorization(self.access).require_run_use(
                principal,
                service,
                scope_id=pinned.scope_id,
                rule_id=pinned.rule_id,
                version=pinned.rule_version,
                run_id=pinned.run_id,
                use=pinned.terms.use,
                context=AccessAuditContext(
                    request_id=current_request_id(), transport="background", operation="dream_run_use"
                ),
            )
        except (AccessControlError, ShareError) as error:
            # A refused or invalidated agreement is a handled Dream failure, not a crash.
            raise DreamError("shared_use_unavailable") from error

    async def authorize_origins(self, origin_scope_id: str, ref) -> None:
        """Executor read authority at one origin, for the actual body read."""

        principal = current_executing_principal()
        if principal is None:
            raise AccessIdentityRequiredError
        # Deferred so the audit row is flushed after the evidence-read transaction, which
        # is the same discipline the review evidence checks use on this connection.
        async with self.access.defer_decision_audit():
            await StandingUseAuthorization(self.access).require_origin(
                principal,
                origin_scope_id,
                ref,
                context=AccessAuditContext(
                    request_id=current_request_id(), transport="background", operation="dream_origin_read"
                ),
            )

    async def require_candidate_use(self, connection, scope_id: str, candidate) -> None:
        """Recheck the bound agreement of a Dream Candidate before its commit."""

        if not candidate.candidate_id.startswith("cand_dream_"):
            return
        record = await DreamRepository().get(connection, scope_id, candidate.candidate_id.removeprefix("cand_dream_"))
        if record.share_run_id is not None:
            # A commit-time recheck re-authorizes the binding the run already holds; it is
            # not a new claim on the pinned evaluation.
            await self.require_run_use(record.share_run_id, record.run, record, admit=False)

    @override
    def _request_id(self) -> str | None:
        return current_request_id()

    async def review(self, scope_id: str, ref: EvidenceReference) -> None:
        principal = current_principal()
        if principal is None:
            raise AccessIdentityRequiredError
        await self.authorize(scope_id, principal_identity(principal), "read", ref)

    async def review_many(self, scope_id, refs, connection):
        if not refs:
            return
        access = self.access.for_read(connection)
        if not access.provider_capabilities.multi_requirement_check:
            for ref in refs:
                await self.review(scope_id, ref)
            return
        principal = current_principal()
        if principal is None:
            raise AccessIdentityRequiredError
        checks = {}
        for ref in refs:
            resource = evidence_resource(scope_id, ref)
            action = AccessAction.SCOPE_READ if resource == ResourceRef.scope(scope_id) else AccessAction.ARTIFACT_READ
            checks[action, resource.key] = (action, resource)
        try:
            await access.require_all(
                principal,
                tuple(checks.values()),
                context=AccessAuditContext(
                    request_id=current_request_id(), transport="background", operation="artifact_dreaming"
                ),
            )
        except AccessDeniedError as error:
            raise DreamError("access_revoked") from error
        except AccessControlError as error:
            raise DreamError("access_unavailable") from error

    async def catalog_action(self, scope_id: str, action: str, target: TagDreamTarget) -> None:
        principal = current_principal()
        if principal is None:
            raise AccessIdentityRequiredError
        await self.access.require(
            principal,
            AccessAction.SCOPE_REVIEW,
            ResourceRef.scope(scope_id),
            context=AccessAuditContext(
                request_id=current_request_id(), transport="http", operation="review_catalog_change"
            ),
        )
        if action != "reject":
            await self.authorize(scope_id, principal_identity(principal), "write_tags", target.basis_ref)

    async def review_action(self, scope_id: str, action: str, candidate: Candidate[Any]) -> None:
        principal = current_principal()
        if principal is None:
            raise AccessIdentityRequiredError
        await self.access.require(
            principal,
            AccessAction.SCOPE_REVIEW,
            ResourceRef.scope(scope_id),
            context=AccessAuditContext(request_id=current_request_id(), transport="http", operation="review_candidate"),
        )
        if action == "reject":
            return
        if action == "revise":
            owner = await self.access.candidate_owner(scope_id, candidate.candidate_id)
            if owner is None or owner.proposed_owner != principal:
                raise AccessDeniedError
        if candidate.target is not None:
            await self.authorize(scope_id, principal_identity(principal), "write", candidate.target)
