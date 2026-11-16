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

"""Authorize cross-Scope use of exact origins under agreed terms.

Reading an origin and writing a target do not by themselves authorize disclosing
material to another audience. A permitted shared use needs three independent
facts, checked against current Access relationships:

* every exact origin is accepted by an actor holding authority there,
* every audience Scope is accepted by an actor holding authority there,
* the execution principal may still read each origin and contribute to the target.

Acceptance is per part, so two contributors who each administer only their own
origin can compose into one lawful run. Terms carry no approver name: naming a
privileged principal is not agreement.

**Trusted-caller contract.** This boundary never resolves identity. ``accept``
requires the principal the owning caller already authenticated for that act; a
caller that passes a decoded request field instead would defeat it. Agreement that
must outlive one boundary instance comes only from the caller's persisted record
through :class:`SharedUseAcceptanceReader`. This module stores nothing.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Protocol, TypeVar

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.authz.errors import (
    AccessConflictError,
    AccessDeniedError,
    AccessInvalidRequestError,
)
from powercontext.builtin.authz.models import AccessAction, PrincipalRef, ResourceRef
from powercontext.builtin.authz.service import AccessAuditContext, AccessControlService
from powercontext.sources import SourceRef

_E = TypeVar("_E", bound=StrEnum)


class SharedUse(StrEnum):
    """The permitted purpose of shared origin material."""

    EVIDENCE = "evidence"


class SharedRetention(StrEnum):
    """What the target may keep after a lawful commit."""

    INDEPENDENT = "independent"
    WITHHOLD_ON_REVOCATION = "withhold_on_revocation"


class SharedPermissionScope(StrEnum):
    """Whether the agreement covers a logical Artifact or one revision only."""

    LOGICAL = "logical"
    REVISION_ONLY = "revision_only"


def _enum_member(enum: type[_E], value: object, code: str) -> _E:
    """Accept an enum member or its exact value; refuse anything else by name."""

    if isinstance(value, enum):
        return value
    if isinstance(value, str):
        try:
            return enum(value)
        except ValueError:
            pass
    raise AccessInvalidRequestError(code)


@dataclass(frozen=True, slots=True)
class SharedOrigin:
    """One exact origin: an Artifact revision or a Source, inside one Scope.

    Identity covers every field the reference carries, including a Source
    locator, so origins that differ in any component stay distinct.
    """

    scope_id: str
    artifact: ArtifactRef | None = None
    source: SourceRef | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope_id, str) or not self.scope_id.strip():
            raise AccessInvalidRequestError("shared_use_origin")
        if (self.artifact is None) == (self.source is None):
            raise AccessInvalidRequestError("shared_use_origin")
        if self.artifact is not None and not isinstance(self.artifact, ArtifactRef):
            raise AccessInvalidRequestError("shared_use_origin")
        if self.source is not None and not isinstance(self.source, SourceRef):
            raise AccessInvalidRequestError("shared_use_origin")

    @classmethod
    def of_artifact(cls, scope_id: str, artifact: ArtifactRef) -> SharedOrigin:
        return cls(scope_id=scope_id, artifact=artifact)

    @classmethod
    def of_source(cls, scope_id: str, source: SourceRef) -> SharedOrigin:
        return cls(scope_id=scope_id, source=source)

    @property
    def identity(self) -> dict[str, object]:
        """Structured origin identity; never a delimiter-joined string."""

        if self.artifact is not None:
            return {
                "kind": "artifact",
                "scope_id": self.scope_id,
                "artifact": self.artifact.model_dump(mode="json"),
            }
        if self.source is None:
            raise AccessInvalidRequestError("shared_use_origin")
        return {
            "kind": "source",
            "scope_id": self.scope_id,
            "source": self.source.model_dump(mode="json", exclude_none=True),
        }

    @property
    def key(self) -> str:
        """Canonical identity used for both duplicate detection and the digest."""

        return json.dumps(self.identity, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @property
    def approval_check(self) -> tuple[AccessAction, ResourceRef]:
        """The authority an origin authority must hold to accept this origin."""

        if self.artifact is not None:
            return AccessAction.ARTIFACT_SHARE, ResourceRef.artifact(
                self.scope_id, family=self.artifact.family, artifact_id=self.artifact.artifact_id
            )
        return AccessAction.SCOPE_ADMIN, ResourceRef.scope(self.scope_id)

    @property
    def evidence_resource(self) -> ResourceRef:
        """The resource whose read authority this origin needs.

        Access grants are logical, so this names the Scope or the Artifact
        identity, not the revision the origin pins.
        """

        if self.artifact is None:
            return ResourceRef.scope(self.scope_id)
        return ResourceRef.artifact(self.scope_id, family=self.artifact.family, artifact_id=self.artifact.artifact_id)


@dataclass(frozen=True, slots=True)
class SharedUseAcceptance:
    """What one authenticated actor accepted, and exactly which parts it covers.

    Coverage is per origin and per audience Scope, so independent contributors
    compose. The value records an act the owning caller performed; it is not an
    identity token and cannot authenticate anybody by itself.
    """

    terms_digest: str
    accepted_by: PrincipalRef
    origins: frozenset[str]
    audience_scope_ids: frozenset[str]
    accepted_at: datetime
    acceptance_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.terms_digest, str) or not self.terms_digest:
            raise AccessInvalidRequestError("shared_use_acceptance")
        if not isinstance(self.accepted_by, PrincipalRef):
            raise AccessInvalidRequestError("shared_use_acceptance")
        if not isinstance(self.accepted_at, datetime):
            raise AccessInvalidRequestError("shared_use_acceptance")
        if not self.origins and not self.audience_scope_ids:
            raise AccessInvalidRequestError("shared_use_acceptance")
        for values in (self.origins, self.audience_scope_ids):
            if not isinstance(values, frozenset) or any(not isinstance(item, str) or not item for item in values):
                raise AccessInvalidRequestError("shared_use_acceptance")

    @property
    def durable(self) -> bool:
        """Whether the caller's own record identifies this acceptance."""

        return self.acceptance_id is not None


class SharedUseAcceptanceReader(Protocol):
    """Confirm an acceptance the caller persisted for later runs.

    The standing record belongs to the caller (a run manifest or rule). This
    boundary only asks whether the given acceptance is that persisted one and
    still applies; it keeps no copy. This is the only source of agreement that
    outlives one boundary instance.
    """

    async def confirmed(self, acceptance: SharedUseAcceptance, /) -> bool: ...


@dataclass(frozen=True, slots=True)
class SharedUseTerms:
    """Origin, use, audience and retention of one shared use; no approver names."""

    target_scope_id: str
    origins: tuple[SharedOrigin, ...]
    uses: frozenset[SharedUse]
    audience_scope_ids: frozenset[str]
    retention: SharedRetention = SharedRetention.INDEPENDENT
    permission_scope: SharedPermissionScope = SharedPermissionScope.LOGICAL

    def __post_init__(self) -> None:
        if not isinstance(self.target_scope_id, str) or not self.target_scope_id.strip():
            raise AccessInvalidRequestError("shared_use_target")
        if not self.origins or any(not isinstance(origin, SharedOrigin) for origin in self.origins):
            raise AccessInvalidRequestError("shared_use_origin")
        keys = [origin.key for origin in self.origins]
        if len(set(keys)) != len(keys):
            raise AccessInvalidRequestError("shared_use_origin")
        if not self.uses:
            raise AccessInvalidRequestError("shared_use_not_agreed")
        if self.target_scope_id not in self.audience_scope_ids:
            # The target always sees its own conclusion.
            raise AccessInvalidRequestError("shared_use_audience")
        if any(not isinstance(scope_id, str) or not scope_id.strip() for scope_id in self.audience_scope_ids):
            raise AccessInvalidRequestError("shared_use_audience")
        object.__setattr__(
            self,
            "uses",
            frozenset(_enum_member(SharedUse, use, "shared_use_use") for use in self.uses),
        )
        object.__setattr__(self, "retention", _enum_member(SharedRetention, self.retention, "shared_use_retention"))
        object.__setattr__(
            self,
            "permission_scope",
            _enum_member(SharedPermissionScope, self.permission_scope, "shared_use_permission_scope"),
        )

    @property
    def origin_keys(self) -> frozenset[str]:
        return frozenset(origin.key for origin in self.origins)

    @property
    def digest(self) -> str:
        """Stable binding of exact origins, use, audience and retention."""

        origins = [origin.identity for origin in self.origins]
        origins.sort(key=lambda item: json.dumps(item, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
        encoded = json.dumps(
            {
                "audience_scope_ids": sorted(self.audience_scope_ids),
                "origins": origins,
                "permission_scope": self.permission_scope.value,
                "retention": self.retention.value,
                "target_scope_id": self.target_scope_id,
                "uses": sorted(use.value for use in self.uses),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class SharedUseGrant:
    """Content-free evidence that agreed terms were satisfied for one use."""

    terms_digest: str
    use: SharedUse
    audience_scope_ids: frozenset[str]
    origins: tuple[SharedOrigin, ...]
    retention: SharedRetention
    acceptances: tuple[SharedUseAcceptance, ...]


class SharedUseAuthorization:
    """Check agreed shared use against current Access relationships."""

    def __init__(
        self,
        access: AccessControlService,
        *,
        acceptances: SharedUseAcceptanceReader | None = None,
    ) -> None:
        self.access = access
        self.acceptances = acceptances

    async def accept(
        self,
        terms: SharedUseTerms,
        *,
        authenticated_actor: PrincipalRef | None,
        context: AccessAuditContext,
        accepted_at: datetime | None = None,
    ) -> SharedUseAcceptance:
        """Record which exact origins and audiences one actor's authority covers.

        ``authenticated_actor`` must be the principal the owning caller
        authenticated for this act. This boundary does not resolve identity, so
        passing a decoded request field here would accept on that field's behalf.
        """

        if authenticated_actor is None:
            raise AccessDeniedError
        origins = set()
        for origin in terms.origins:
            if await self._allowed(authenticated_actor, origin.approval_check, context):
                origins.add(origin.key)
        audiences = set()
        for scope_id in sorted(terms.audience_scope_ids):
            if await self._allowed(authenticated_actor, self._audience_check(scope_id), context):
                audiences.add(scope_id)
        if not origins and not audiences:
            raise AccessDeniedError
        return SharedUseAcceptance(
            terms_digest=terms.digest,
            accepted_by=authenticated_actor,
            origins=frozenset(origins),
            audience_scope_ids=frozenset(audiences),
            accepted_at=accepted_at if accepted_at is not None else datetime.now(UTC),
        )

    async def require(
        self,
        principal: PrincipalRef | None,
        terms: SharedUseTerms,
        *,
        acceptances: Sequence[SharedUseAcceptance],
        use: SharedUse,
        context: AccessAuditContext,
    ) -> SharedUseGrant:
        """Admit one use only when accepted coverage spans every part of the terms."""

        admitted = _enum_member(SharedUse, use, "shared_use_use")
        self._require_supported(terms, admitted)
        await self._require_acceptances(terms, acceptances, context)
        await self._require_checks(principal, self._execution_checks(terms), context)
        return SharedUseGrant(
            terms_digest=terms.digest,
            use=admitted,
            audience_scope_ids=terms.audience_scope_ids,
            origins=terms.origins,
            retention=terms.retention,
            acceptances=tuple(acceptances),
        )

    async def recheck(
        self,
        principal: PrincipalRef | None,
        terms: SharedUseTerms,
        *,
        acceptances: Sequence[SharedUseAcceptance],
        use: SharedUse,
        expected_digest: str,
        context: AccessAuditContext,
    ) -> SharedUseGrant:
        """Re-admit the terms a run pinned, refusing silently changed terms."""

        if terms.digest != expected_digest:
            raise AccessConflictError("shared_use_terms")
        return await self.require(principal, terms, acceptances=acceptances, use=use, context=context)

    async def require_evidence(
        self,
        principal: PrincipalRef | None,
        origin: SharedOrigin,
        *,
        context: AccessAuditContext,
    ) -> None:
        """Open one origin under its current logical read authority.

        A retained target conclusion never implies this, and this does not check
        the revision the origin pins: Access grants are logical.
        """

        action = AccessAction.ARTIFACT_READ if origin.artifact is not None else AccessAction.SCOPE_READ
        await self.access.require(principal, action, origin.evidence_resource, context=context)

    def _require_supported(self, terms: SharedUseTerms, use: SharedUse) -> None:
        if use not in terms.uses:
            raise AccessInvalidRequestError("shared_use_not_agreed")
        if terms.permission_scope == SharedPermissionScope.REVISION_ONLY:
            # Logical grants carry no revision column, so revision-only use cannot be enforced.
            raise AccessInvalidRequestError("shared_use_revision_only_unsupported")
        if terms.retention == SharedRetention.WITHHOLD_ON_REVOCATION:
            # Withholding a committed conclusion has no conditions to rebuild it.
            raise AccessInvalidRequestError("shared_use_withhold_unsupported")

    async def _require_acceptances(
        self,
        terms: SharedUseTerms,
        acceptances: Sequence[SharedUseAcceptance],
        context: AccessAuditContext,
    ) -> None:
        if not acceptances:
            raise AccessDeniedError
        for acceptance in acceptances:
            # Terms that changed after acceptance are not the accepted terms.
            if acceptance.terms_digest != terms.digest:
                raise AccessConflictError("shared_use_terms")
        if not terms.origin_keys <= {key for item in acceptances for key in item.origins}:
            raise AccessDeniedError
        if not terms.audience_scope_ids <= {scope_id for item in acceptances for scope_id in item.audience_scope_ids}:
            raise AccessDeniedError
        for acceptance in acceptances:
            # Only the authority this actor actually spoke for has to stand, so one
            # contributor's withdrawal blocks the run without covering the others.
            checks = tuple(
                origin.approval_check for origin in terms.origins if origin.key in acceptance.origins
            ) + tuple(self._audience_check(scope_id) for scope_id in sorted(acceptance.audience_scope_ids))
            await self._require_checks(acceptance.accepted_by, checks, context)
            if acceptance.durable and not await self._confirmed(acceptance):
                raise AccessDeniedError

    async def _confirmed(self, acceptance: SharedUseAcceptance) -> bool:
        reader = self.acceptances
        if reader is None:
            # Nothing can vouch for a persisted acceptance we cannot read.
            return False
        return await reader.confirmed(acceptance)

    def _audience_check(self, scope_id: str) -> tuple[AccessAction, ResourceRef]:
        return AccessAction.SCOPE_ADMIN, ResourceRef.scope(scope_id)

    def _execution_checks(self, terms: SharedUseTerms) -> tuple[tuple[AccessAction, ResourceRef], ...]:
        return (
            *(
                (
                    AccessAction.ARTIFACT_READ if origin.artifact is not None else AccessAction.SCOPE_READ,
                    origin.evidence_resource,
                )
                for origin in terms.origins
            ),
            (AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(terms.target_scope_id)),
        )

    async def _allowed(
        self,
        principal: PrincipalRef,
        check: tuple[AccessAction, ResourceRef],
        context: AccessAuditContext,
    ) -> bool:
        action, resource = check
        decision = await self.access.check(principal, action, resource, context=context)
        return decision.allowed

    async def _require_checks(
        self,
        principal: PrincipalRef | None,
        checks: Sequence[tuple[AccessAction, ResourceRef]],
        context: AccessAuditContext,
    ) -> None:
        if not checks:
            return
        if self.access.provider_capabilities.multi_requirement_check:
            await self.access.require_all(principal, checks, context=context)
            return
        for action, resource in checks:
            await self.access.require(principal, action, resource, context=context)


__all__ = (
    "SharedOrigin",
    "SharedPermissionScope",
    "SharedRetention",
    "SharedUse",
    "SharedUseAcceptance",
    "SharedUseAcceptanceReader",
    "SharedUseAuthorization",
    "SharedUseGrant",
    "SharedUseTerms",
)
