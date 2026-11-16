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

"""Content-free evidence manifests and transient model projections."""

from __future__ import annotations

import hashlib
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from powercontext.artifacts import ArtifactRef
from powercontext.errors import PowerContextError
from powercontext.sources import SourceRef

EVIDENCE_TRANSFORM_VERSION = "powercontext.dream.evidence.v1"


class EvidenceResolutionError(PowerContextError, ValueError):
    """A stable admission failure with no evidence content in the message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class EvidenceLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_items: int = Field(default=32, ge=1, le=32)
    max_bytes: int = Field(default=65_536, ge=1, le=65_536)
    max_nodes: int = Field(default=128, ge=1, le=128)
    max_edges: int = Field(default=256, ge=1, le=256)
    max_depth: int = Field(default=8, ge=1, le=8)


class ScopedEvidence(BaseModel):
    """One explicitly admitted reference, resolved in the Scope that owns it.

    The reference stays unqualified, exactly as a local selection is, because the
    owning Scope is the separate field. A local reference and the same reference
    selected here from another Scope are different inputs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope_id: str
    artifact: ArtifactRef | None = None
    source: SourceRef | None = None
    role: Literal["support", "counterexample"] = "support"

    @field_validator("scope_id")
    @classmethod
    def validate_scope_id(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("scope_id must be a non-empty trimmed string")  # noqa: TRY003
        return value

    @model_validator(mode="after")
    def require_one_reference(self):
        if (self.artifact is None) == (self.source is None):
            raise ValueError("scoped evidence must select exactly one Artifact or Source")  # noqa: TRY003
        return self

    @property
    def reference(self) -> ArtifactRef | SourceRef:
        reference = self.source if self.artifact is None else self.artifact
        if reference is None:
            raise ValueError("scoped evidence has no reference")  # noqa: TRY003
        return reference


class EvidenceNode(BaseModel):
    """An immutable content digest and its exact provenance, never its body."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    kind: Literal[
        "source",
        "experience",
        "memory",
        "profile",
        "topic_memory",
        "handoff",
        "skill",
        "prompt",
        "catalog_target",
        "unresolved",
    ]
    digest: str
    source: SourceRef | None = None
    artifact: ArtifactRef | None = None
    # Set only when the owning Scope differs from the run Scope. It is omitted for a
    # local node so an existing run keeps the exact manifest and wire shape it had.
    origin_scope_id: str | None = Field(default=None, exclude_if=lambda value: value is None)

    role: Literal["root", "derived", "target", "lineage_only", "unresolved"]
    historical: bool = False


class EvidenceEdge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    derived_id: str
    upstream_id: str


class RootEvidenceGroup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    group_id: str
    sources: tuple[SourceRef, ...]
    # Unset for a group of run-Scope Sources; another Scope names its own roots.
    scope_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    independence: Literal["attested", "unknown"] = "unknown"


class EvidenceManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    transform_version: str = EVIDENCE_TRANSFORM_VERSION
    artifacts: tuple[ArtifactRef, ...] = ()
    sources: tuple[SourceRef, ...] = ()
    scoped: tuple[ScopedEvidence, ...] = Field(default=(), exclude_if=lambda value: not value)
    nodes: tuple[EvidenceNode, ...] = ()
    edges: tuple[EvidenceEdge, ...] = ()
    root_groups: tuple[RootEvidenceGroup, ...] = ()
    projection_digest: str
    projection_bytes: int
    incomplete: bool = False


class ProjectedEvidence(BaseModel):
    """Untrusted content visible to the model for this operation only."""

    evidence_id: str
    kind: Literal[
        "source", "experience", "memory", "profile", "topic_memory", "handoff", "skill", "prompt", "catalog_target"
    ]
    text: str
    historical: bool = False
    root_group_ids: tuple[str, ...] = ()


class EvidenceProjection(BaseModel):
    evidence: tuple[ProjectedEvidence, ...]
    root_groups: tuple[RootEvidenceGroup, ...]
    incomplete: bool = False


class ResolvedEvidence(BaseModel):
    manifest: EvidenceManifest
    projection: EvidenceProjection


def content_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def reference_key(value: ArtifactRef | SourceRef) -> str:
    return value.model_dump_json()


T = TypeVar("T")


def unique_references(values: tuple[T, ...]) -> tuple[T, ...]:
    """Keep stable reference order without requiring hashable domain models."""

    # Function-scoped identity preserves the caller's precise reference type.
    selected: list[T] = []
    for value in values:
        if value not in selected:
            selected.append(value)
    return tuple(selected)
