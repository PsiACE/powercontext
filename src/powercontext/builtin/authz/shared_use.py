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
material to another audience. This boundary checks the independent facts a
permitted shared use needs, against the actual principal and the exact origins:

* an origin authority approved this use of every exact origin,
* a target authority accepted the material and each disclosure audience,
* the execution principal may still read each origin and contribute to the target.

The agreement itself belongs to the caller's own run record; this module stores
nothing and reads no rule. Unsupported restrictions are refused rather than
widened.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.authz.errors import (
    AccessConflictError,
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


@dataclass(frozen=True, slots=True)
class SharedOrigin:
    """One exact origin: an Artifact revision or a Source, inside one Scope."""

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
    def key(self) -> str:
        if self.artifact is not None:
            return (
                f"artifact:{self.scope_id}:{self.artifact.family}/{self.artifact.artifact_id}@{self.artifact.revision}"
            )
        if self.source is None:
            raise AccessInvalidRequestError("shared_use_origin")
        return f"source:{self.scope_id}:{self.source.source_type}/{self.source.source_id}"

    @property
    def evidence_resource(self) -> ResourceRef:
        """The resource whose read authority this origin needs."""

        if self.artifact is None:
            return ResourceRef.scope(self.scope_id)
        return ResourceRef.artifact(self.scope_id, family=self.artifact.family, artifact_id=self.artifact.artifact_id)

    @property
    def approval_resource(self) -> ResourceRef:
        """The resource an origin authority approves the use against."""

        return self.evidence_resource


@dataclass(frozen=True, slots=True)
class SharedUseTerms:
    """The agreed origin, use, audience and retention terms of one run."""

    target_scope_id: str
    origins: tuple[SharedOrigin, ...]
    uses: frozenset[SharedUse]
    audience_scope_ids: frozenset[str]
    origin_approved_by: PrincipalRef
    target_approved_by: PrincipalRef
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

    @property
    def digest(self) -> str:
        """Stable binding of exact origins, use, audience and retention."""

        encoded = json.dumps(
            {
                "audience_scope_ids": sorted(self.audience_scope_ids),
                "origin_approved_by": [self.origin_approved_by.type, self.origin_approved_by.id],
                "origins": sorted(origin.key for origin in self.origins),
                "permission_scope": self.permission_scope.value,
                "retention": self.retention.value,
                "target_approved_by": [self.target_approved_by.type, self.target_approved_by.id],
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
    """Content-free evidence that the terms were satisfied for one use."""

    terms_digest: str
    use: SharedUse
    audience_scope_ids: frozenset[str]
    origins: tuple[SharedOrigin, ...]
    retention: SharedRetention


class SharedUseAuthorization:
    """Check agreed shared use against current Access relationships."""

    def __init__(self, access: AccessControlService) -> None:
        self.access = access

    async def require(
        self,
        principal: PrincipalRef | None,
        terms: SharedUseTerms,
        *,
        use: SharedUse,
        context: AccessAuditContext,
    ) -> SharedUseGrant:
        """Admit one use of these terms or fail closed."""

        self._require_supported(terms, use)
        await self._require_checks(
            terms.origin_approved_by,
            self._origin_approval_checks(terms),
            context,
        )
        await self._require_checks(
            terms.target_approved_by,
            self._audience_approval_checks(terms),
            context,
        )
        await self._require_checks(principal, self._execution_checks(terms), context)
        return SharedUseGrant(
            terms_digest=terms.digest,
            use=use,
            audience_scope_ids=terms.audience_scope_ids,
            origins=terms.origins,
            retention=terms.retention,
        )

    async def recheck(
        self,
        principal: PrincipalRef | None,
        terms: SharedUseTerms,
        *,
        use: SharedUse,
        expected_digest: str,
        context: AccessAuditContext,
    ) -> SharedUseGrant:
        """Re-admit the terms a run pinned, refusing silently narrowed terms."""

        if terms.digest != expected_digest:
            raise AccessConflictError("shared_use_terms")
        return await self.require(principal, terms, use=use, context=context)

    async def require_evidence(
        self,
        principal: PrincipalRef | None,
        origin: SharedOrigin,
        *,
        context: AccessAuditContext,
    ) -> None:
        """Open one exact origin; a retained target conclusion never implies this."""

        action = AccessAction.ARTIFACT_READ if origin.artifact is not None else AccessAction.SCOPE_READ
        await self.access.require(principal, action, origin.evidence_resource, context=context)

    def _require_supported(self, terms: SharedUseTerms, use: SharedUse) -> None:
        if use not in terms.uses:
            raise AccessInvalidRequestError("shared_use_not_agreed")
        if terms.permission_scope is SharedPermissionScope.REVISION_ONLY:
            # Logical grants carry no revision column, so revision-only use cannot be enforced.
            raise AccessInvalidRequestError("shared_use_revision_only_unsupported")
        if terms.retention is SharedRetention.WITHHOLD_ON_REVOCATION:
            # Withholding a committed conclusion has no conditions to rebuild it.
            raise AccessInvalidRequestError("shared_use_withhold_unsupported")

    def _origin_approval_checks(self, terms: SharedUseTerms) -> tuple[tuple[AccessAction, ResourceRef], ...]:
        return tuple(
            (
                AccessAction.ARTIFACT_SHARE if origin.artifact is not None else AccessAction.SCOPE_ADMIN,
                origin.approval_resource,
            )
            for origin in terms.origins
        )

    def _audience_approval_checks(self, terms: SharedUseTerms) -> tuple[tuple[AccessAction, ResourceRef], ...]:
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
    "SharedUseAuthorization",
    "SharedUseGrant",
    "SharedUseTerms",
)
