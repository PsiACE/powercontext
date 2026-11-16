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

* an authenticated actor holding origin authority accepted this use of these
  exact origins,
* an authenticated actor holding target authority accepted this material and
  each disclosure audience,
* the execution principal may still read each origin and contribute to the target.

Terms carry no approver name, so naming a privileged principal is not agreement.
An acceptance exists only because some actor ran :meth:`SharedUseAuthorization.accept`
and its authority was checked then; a durable cross-actor agreement is supplied by
the caller's own record and confirmed through :class:`SharedUseAcceptanceReader`.
This module stores nothing.
"""

from __future__ import annotations

import hmac
import json
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Protocol

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.authz.errors import (
    AccessConflictError,
    AccessDeniedError,
    AccessInvalidRequestError,
)
from powercontext.builtin.authz.models import AccessAction, PrincipalRef, ResourceRef
from powercontext.builtin.authz.service import AccessAuditContext, AccessControlService
from powercontext.sources import SourceRef


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


class AcceptanceSide(StrEnum):
    """Which half of the agreement one acceptance covers."""

    ORIGIN = "origin"
    TARGET = "target"


def _enum_member(enum: type[StrEnum], value: object, code: str) -> StrEnum:
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
    """One actor's acceptance of exact terms, observed when its authority held.

    Constructed only by :meth:`SharedUseAuthorization.accept`; ``proof`` binds the
    acting actor, the signature makes a hand-built value detectable, and a
    persisted ``acceptance_id`` defers to the caller's own record instead.
    """

    terms_digest: str
    accepted_by: PrincipalRef
    sides: frozenset[AcceptanceSide]
    accepted_at: datetime
    acceptance_id: str | None = None
    proof: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not self.terms_digest or not self.sides:
            raise AccessInvalidRequestError("shared_use_acceptance")

    @property
    def durable(self) -> bool:
        """Whether the caller's own record identifies this acceptance."""

        return self.acceptance_id is not None

    @property
    def material(self) -> str:
        return json.dumps(
            {
                "accepted_at": self.accepted_at.isoformat(),
                "accepted_by": [self.accepted_by.type, self.accepted_by.id],
                "acceptance_id": self.acceptance_id,
                "sides": sorted(side.value for side in self.sides),
                "terms_digest": self.terms_digest,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


class SharedUseAcceptanceReader(Protocol):
    """Confirm an acceptance the caller persisted for later runs.

    The standing record belongs to the caller (a run manifest or rule). This
    boundary only asks whether the given acceptance is that persisted one and
    still applies; it keeps no copy.
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
        if not self.origins:
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
        clock: datetime | None = None,
    ) -> None:
        self.access = access
        self.acceptances = acceptances
        self._clock = clock
        # Binds each in-process acceptance to this enforcement boundary, so a
        # hand-built value cannot stand in for an actor's agreement. Standing
        # cross-process agreement is the caller's persisted record instead.
        self._acceptance_key = secrets.token_bytes(32)

    async def accept(
        self,
        actor: PrincipalRef | None,
        terms: SharedUseTerms,
        *,
        context: AccessAuditContext,
    ) -> SharedUseAcceptance:
        """Record which halves this actor's current authority covers.

        The actor is checked here, so accepting on somebody else's behalf is
        impossible: this returns what *this* principal holds, or refuses.
        """

        sides = set()
        if await self._any_allowed(actor, self._origin_approval_checks(terms), context):
            sides.add(AcceptanceSide.ORIGIN)
        if await self._any_allowed(actor, self._target_approval_checks(terms), context):
            sides.add(AcceptanceSide.TARGET)
        if actor is None or not sides:
            raise AccessDeniedError
        acceptance = SharedUseAcceptance(
            terms_digest=terms.digest,
            accepted_by=actor,
            sides=frozenset(sides),
            accepted_at=self._clock if self._clock is not None else datetime.now(UTC),
        )
        return SharedUseAcceptance(
            terms_digest=acceptance.terms_digest,
            accepted_by=acceptance.accepted_by,
            sides=acceptance.sides,
            accepted_at=acceptance.accepted_at,
            proof=self._prove(acceptance),
        )

    def _prove(self, acceptance: SharedUseAcceptance) -> str:
        return hmac.new(self._acceptance_key, acceptance.material.encode(), sha256).hexdigest()

    def _acceptance_stands(self, acceptance: SharedUseAcceptance) -> bool:
        """An acceptance counts only if this boundary issued it or the caller persisted it."""

        if acceptance.durable:
            return False  # Deferred to the persisted record checked by the caller's reader.
        return bool(acceptance.proof) and hmac.compare_digest(acceptance.proof, self._prove(acceptance))

    async def require(
        self,
        principal: PrincipalRef | None,
        terms: SharedUseTerms,
        *,
        acceptances: Sequence[SharedUseAcceptance],
        use: SharedUse,
        context: AccessAuditContext,
    ) -> SharedUseGrant:
        """Admit one use only when both halves are accepted by acting actors."""

        self._require_supported(terms, use)
        await self._require_acceptances(terms, acceptances, context)
        await self._require_checks(principal, self._execution_checks(terms), context)
        return SharedUseGrant(
            terms_digest=terms.digest,
            use=use,
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
            if not acceptance.durable and not self._acceptance_stands(acceptance):
                # A named privileged principal is not that principal's agreement.
                raise AccessDeniedError
        covered = frozenset(side for acceptance in acceptances for side in acceptance.sides)
        if covered != frozenset(AcceptanceSide):
            raise AccessDeniedError
        for acceptance in acceptances:
            # The authority each accepting actor held must still stand.
            await self._require_checks(
                acceptance.accepted_by,
                self._sides_checks(terms, acceptance.sides),
                context,
            )
            if acceptance.durable and not await self._confirmed(acceptance):
                raise AccessDeniedError

    async def _confirmed(self, acceptance: SharedUseAcceptance) -> bool:
        reader = self.acceptances
        if reader is None:
            # Nothing can vouch for a persisted acceptance we cannot read.
            return False
        return await reader.confirmed(acceptance)

    def _sides_checks(
        self,
        terms: SharedUseTerms,
        sides: frozenset[AcceptanceSide],
    ) -> tuple[tuple[AccessAction, ResourceRef], ...]:
        checks: tuple[tuple[AccessAction, ResourceRef], ...] = ()
        if AcceptanceSide.ORIGIN in sides:
            checks = (*checks, *self._origin_approval_checks(terms))
        if AcceptanceSide.TARGET in sides:
            checks = (*checks, *self._target_approval_checks(terms))
        return checks

    def _origin_approval_checks(self, terms: SharedUseTerms) -> tuple[tuple[AccessAction, ResourceRef], ...]:
        return tuple(
            (
                AccessAction.ARTIFACT_SHARE if origin.artifact is not None else AccessAction.SCOPE_ADMIN,
                origin.evidence_resource,
            )
            for origin in terms.origins
        )

    def _target_approval_checks(self, terms: SharedUseTerms) -> tuple[tuple[AccessAction, ResourceRef], ...]:
        return tuple(
            (AccessAction.SCOPE_ADMIN, ResourceRef.scope(scope_id)) for scope_id in sorted(terms.audience_scope_ids)
        )

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

    async def _any_allowed(
        self,
        principal: PrincipalRef | None,
        checks: Sequence[tuple[AccessAction, ResourceRef]],
        context: AccessAuditContext,
    ) -> bool:
        """Whether the actor holds every authority of one side."""

        if not checks:
            return False
        for action, resource in checks:
            decision = await self.access.check(principal, action, resource, context=context)
            if not decision.allowed:
                return False
        return True

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
    "AcceptanceSide",
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
