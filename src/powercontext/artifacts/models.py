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

"""Domain values shared by artifact families."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from powercontext.errors import InvalidArtifactReferenceError
from powercontext.limits import (
    MAX_ARTIFACT_FAMILY_LENGTH,
    MAX_ARTIFACT_HANDLE_KEY_LENGTH,
    MAX_ARTIFACT_ID_LENGTH,
    MAX_SCOPE_ID_LENGTH,
)
from powercontext.sources.models import SourceRef

_HANDLE_KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

ContentT = TypeVar("ContentT", covariant=True)
_LEGACY_MEMORY_FAMILY = "memory"


def _reject_legacy_memory_citations(value: Any) -> Any:
    if isinstance(value, Mapping) and "memory_citations" in value:
        raise ValueError("memory_citations is no longer supported; cite Atomic Memory revisions in artifacts")  # noqa: TRY003
    return value


class ArtifactRef(BaseModel):
    """A stable reference to one exact artifact revision."""

    family: str
    artifact_id: str
    revision: StrictInt = Field(ge=1)

    @field_validator("family", "artifact_id")
    @classmethod
    def validate_identity(cls, value: str, info) -> str:
        _validate_reference_part(info.field_name, value)
        if info.field_name == "family" and value == _LEGACY_MEMORY_FAMILY:
            # Legacy collections were archived; their entries are cited as Atomic Memory revisions.
            raise InvalidArtifactReferenceError("family", "legacy Memory collections cannot be referenced")
        maximum = MAX_ARTIFACT_FAMILY_LENGTH if info.field_name == "family" else MAX_ARTIFACT_ID_LENGTH
        if len(value) > maximum:
            raise InvalidArtifactReferenceError(info.field_name, f"must not exceed {maximum} characters")
        return value


class StableHandle(BaseModel):
    """A human-copyable, scope-local handle for an Artifact revision."""

    family: str
    key: str

    @field_validator("family")
    @classmethod
    def validate_family(cls, value: str) -> str:
        _validate_reference_part("family", value)
        if len(value) > MAX_ARTIFACT_FAMILY_LENGTH:
            raise InvalidArtifactReferenceError(
                "family",
                f"must not exceed {MAX_ARTIFACT_FAMILY_LENGTH} characters",
            )
        return value

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if len(value) > MAX_ARTIFACT_HANDLE_KEY_LENGTH:
            raise InvalidArtifactReferenceError(
                "key",
                f"must not exceed {MAX_ARTIFACT_HANDLE_KEY_LENGTH} characters",
            )
        if _HANDLE_KEY_PATTERN.fullmatch(value) is None:
            raise InvalidArtifactReferenceError(
                "key",
                "must contain lowercase ASCII letters, digits, and single hyphens",
            )
        return value


class _ArtifactValue(BaseModel):
    """Shared immutable configuration for artifact-family content values.

    ``strict=True`` is deliberately omitted: Experience values are produced by
    generators and HTTP mapping through the lenient coercion path today, and
    tightening them is a behavior change outside this family's contract.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class ArtifactAddress(BaseModel):
    """A complete address for one exact Artifact revision across Scope boundaries."""

    scope_id: str
    artifact: ArtifactRef

    @field_validator("scope_id")
    @classmethod
    def validate_scope_id(cls, value: str) -> str:
        _validate_reference_part("scope_id", value)
        if len(value) > MAX_SCOPE_ID_LENGTH:
            raise InvalidArtifactReferenceError(
                "scope_id",
                f"must not exceed {MAX_SCOPE_ID_LENGTH} characters",
            )
        return value


class SourceAddress(BaseModel):
    """A complete address for one exact Source across Scope boundaries."""

    scope_id: str
    source: SourceRef

    @field_validator("scope_id")
    @classmethod
    def validate_scope_id(cls, value: str) -> str:
        _validate_reference_part("scope_id", value)
        if len(value) > MAX_SCOPE_ID_LENGTH:
            raise InvalidArtifactReferenceError(
                "scope_id",
                f"must not exceed {MAX_SCOPE_ID_LENGTH} characters",
            )
        return value


_ORIGIN_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class RecordedOrigin(BaseModel):
    """One exact reference a revision was produced from, with the digest first read.

    The reference keeps its owning Scope beside it, so the same local identity in two
    Scopes stays two origins. It is recorded instead of linked: removing the origin
    later must not delete an independently retained conclusion, and two origins with
    equal digests are still two origins because a digest is not provenance.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    source: SourceRef | None = None
    artifact: ArtifactRef | None = None
    digest: str

    @field_validator("scope_id")
    @classmethod
    def validate_scope_id(cls, value: str) -> str:
        return SourceAddress.validate_scope_id(value)

    @field_validator("digest")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if _ORIGIN_DIGEST_PATTERN.fullmatch(value) is None:
            raise InvalidArtifactReferenceError("digest", "must be a sha256 digest")
        return value

    @model_validator(mode="after")
    def require_one_reference(self):
        if (self.source is None) == (self.artifact is None):
            raise ValueError("a recorded origin must name exactly one Artifact or Source")  # noqa: TRY003
        return self

    @property
    def reference(self) -> ArtifactRef | SourceRef:
        reference = self.source if self.artifact is None else self.artifact
        if reference is None:
            raise ValueError("recorded origin has no reference")  # noqa: TRY003
        return reference


class ArtifactLineage(BaseModel):
    """The direct evidence used to produce one artifact revision."""

    _reject_legacy_citations = model_validator(mode="before")(_reject_legacy_memory_citations)

    sources: tuple[SourceRef, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    # Where the evidence of this revision actually came from, including other Scopes.
    # Omitted when empty, so a local revision keeps the serialization it always had.
    origins: tuple[RecordedOrigin, ...] = Field(default=(), exclude_if=lambda value: not value)
    publication_source: ArtifactAddress | None = None
    publication_digest: str | None = None

    @model_validator(mode="after")
    def require_complete_publication_provenance(self):
        if (self.publication_source is None) != (self.publication_digest is None):
            raise ValueError("publication source and digest must be provided together")  # noqa: TRY003
        return self


class ArtifactDraft(BaseModel, Generic[ContentT]):
    """Content and complete evidence supplied for one Artifact write."""

    _reject_legacy_citations = model_validator(mode="before")(_reject_legacy_memory_citations)

    family: ClassVar[str] = "artifact"

    content: ContentT
    sources: tuple[SourceRef, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    origins: tuple[RecordedOrigin, ...] = Field(default=(), exclude_if=lambda value: not value)

    @model_validator(mode="after")
    def validate_family(self):
        _validate_reference_part("family", self.family)
        return self


class Artifact(BaseModel, Generic[ContentT]):
    """An immutable snapshot in an artifact lifecycle."""

    family: ClassVar[str] = "artifact"

    artifact_id: str
    revision: StrictInt = Field(ge=1)
    content: ContentT
    lineage: ArtifactLineage = Field(default_factory=ArtifactLineage)

    @field_validator("artifact_id")
    @classmethod
    def validate_artifact_id(cls, value: str) -> str:
        _validate_reference_part("artifact_id", value)
        if len(value) > MAX_ARTIFACT_ID_LENGTH:
            raise InvalidArtifactReferenceError(
                "artifact_id",
                f"must not exceed {MAX_ARTIFACT_ID_LENGTH} characters",
            )
        return value

    def as_ref(self) -> ArtifactRef:
        """Return an exact reference to this revision."""

        return ArtifactRef(family=self.family, artifact_id=self.artifact_id, revision=self.revision)


def _validate_reference_part(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidArtifactReferenceError(field_name, "must be a non-empty string")
    if value != value.strip():
        raise InvalidArtifactReferenceError(field_name, "must not contain leading or trailing whitespace")
