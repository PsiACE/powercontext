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

"""Standing sharing rules, one run's pinned inputs, and its evaluation outcome.

A rule states where future material may be found. A run records exactly what one
evaluation pinned. The committed Artifact revision stays the maintained
conclusion and the only revision lineage. Keeping these three values distinct
prevents a retry from silently adopting a newer revision and prevents a rule
edit from rewriting a recorded evaluation.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

import rfc8785
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.scope.models import ScopeSelection
from powercontext.sources import SourceMaterialization, SourceRef

MAX_SHARE_ID_LENGTH = 64
MAX_SHARE_LABEL_LENGTH = 128
MAX_SHARE_NOTIFICATION_KEY_LENGTH = 128
MAX_SHARE_PATH_LENGTH = 256

ShareRuleStatus = Literal["active", "invalidated"]
ShareAcceptanceSide = Literal["origin", "target"]
ShareInputKind = Literal["source", "artifact"]
ShareInputRole = Literal["evidence", "baseline"]
ShareMaterialization = Literal["captured", "referenced"]
ShareRunState = Literal["pinned", "invalidated"]
ShareRunOutcome = Literal["pending", "no_change", "needs_evidence", "rejected", "committed"]
ShareRetention = Literal["reference", "independent"]


class ShareError(Exception):
    """Base failure for standing sharing rules and pinned runs."""

    code = "share_error"


class ShareRuleNotFoundError(ShareError, LookupError):
    code = "share_rule_not_found"

    def __init__(self, scope_id: str, rule_id: str, version: int | None = None) -> None:
        self.scope_id = scope_id
        self.rule_id = rule_id
        self.version = version
        super().__init__(self.code)


class ShareRunNotFoundError(ShareError, LookupError):
    code = "share_run_not_found"

    def __init__(self, scope_id: str, run_id: str) -> None:
        self.scope_id = scope_id
        self.run_id = run_id
        super().__init__(self.code)


class ShareRuleVersionConflictError(ShareError, RuntimeError):
    code = "share_rule_version_conflict"

    def __init__(self, rule_id: str, expected: int, actual: int) -> None:
        self.rule_id = rule_id
        self.expected = expected
        self.actual = actual
        super().__init__(self.code)


class ShareRuleInvalidatedError(ShareError, RuntimeError):
    """A rule stopped governing future work; a new rule version must be published."""

    code = "share_rule_invalidated"

    def __init__(self, rule_id: str) -> None:
        self.rule_id = rule_id
        super().__init__(self.code)


class ShareBudgetExceededError(ShareError, RuntimeError):
    """The agreed range cannot be covered completely; nothing was pinned."""

    code = "share_budget_exceeded"

    def __init__(self, limit: str, maximum: int, observed: int) -> None:
        self.limit = limit
        self.maximum = maximum
        self.observed = observed
        super().__init__(self.code)


class ShareRuleAcceptanceError(ShareError, RuntimeError):
    """The recorded agreed terms lack a required accepted side."""

    code = "share_rule_acceptance"

    def __init__(self, rule_id: str, version: int, side: str) -> None:
        self.rule_id = rule_id
        self.version = version
        self.side = side
        super().__init__(self.code)


class ShareRuleServiceUnavailable(ShareError, RuntimeError):
    """The Runtime was composed without standing-rule storage."""

    code = "share_rule_unavailable"

    def __init__(self) -> None:
        super().__init__(self.code)


class ShareAcceptanceConflictError(ShareError, RuntimeError):
    """An already accepted actor/side cannot change its recorded coverage in place."""

    code = "share_acceptance_conflict"

    def __init__(self, rule_id: str, version: int, actor: str, side: str) -> None:
        self.rule_id = rule_id
        self.version = version
        self.actor = actor
        self.side = side
        super().__init__(self.code)


class ShareRunStateError(ShareError, RuntimeError):
    """A recorded evaluation cannot move to the requested state."""

    code = "share_run_state"

    def __init__(self, run_id: str, state: str) -> None:
        self.run_id = run_id
        self.state = state
        super().__init__(self.code)


class _ShareValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ShareAcceptance(_ShareValue):
    """One authenticated actor's acceptance of an immutable rule version.

    The record names the identity that accepted, and the exact origin or audience
    Scopes that acceptance covers. Coverage composes: several contributors can
    each accept their own subset, and one run is admitted when their union covers
    the origins it reached and the audiences the version declares. The digest
    binds the standing selector and terms, never one run's discovered inputs, so a
    contributor agrees once rather than approving every future run.
    """

    scope_id: str
    rule_id: str
    version: int = Field(ge=1)
    side: ShareAcceptanceSide
    actor: str
    terms_digest: str
    covered_scope_ids: tuple[str, ...] = ()
    acceptance_id: str
    accepted_at: datetime

    @field_validator("actor")
    @classmethod
    def validate_actor(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("an accepting actor must be a non-empty identity")  # noqa: TRY003
        return value

    @field_validator("covered_scope_ids")
    @classmethod
    def validate_covered(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError("an acceptance must cover at least one Scope")  # noqa: TRY003
        if len(set(values)) != len(values):
            raise ValueError("covered Scopes must be unique")  # noqa: TRY003
        return tuple(sorted(values))


class ShareRuleTerms(_ShareValue):
    """Terms the rule records, for the authority check that actually enforces them.

    This module never grants access. It carries the labels an accepted agreement
    binds so a run can be rechecked against them before it reads an origin or
    publishes a conclusion. Audience Scopes are explicit because coverage composes
    per actor rather than as one approval of everything.
    """

    use: str = Field(min_length=1, max_length=MAX_SHARE_LABEL_LENGTH)
    audience: str = Field(min_length=1, max_length=MAX_SHARE_LABEL_LENGTH)
    audience_scope_ids: tuple[str, ...] = ()
    retention: ShareRetention = "reference"

    @field_validator("use", "audience")
    @classmethod
    def validate_label(cls, value: str) -> str:
        if value != value.strip() or not value:
            raise ValueError("share rule terms must be non-empty without surrounding whitespace")  # noqa: TRY003
        return value

    @field_validator("audience_scope_ids")
    @classmethod
    def validate_audience_scopes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("audience Scopes must be unique")  # noqa: TRY003
        return tuple(sorted(values))


class ShareTermsBinding(_ShareValue):
    """Canonical standing agreement one acceptance binds.

    The digest covers everything that determines what one rule version may select —
    the selection mode and root, the declared Scopes, the Source types, the Artifact
    families, the run limits, and the use, audience and retention terms. It excludes
    the inputs any run discovers, so agreement survives new records while a run's own
    manifest digest still identifies precisely what that run pinned.
    """

    target_scope_id: str
    selection_mode: str
    root_scope_id: str | None
    selected_scope_ids: tuple[str, ...]
    exact_scope_ids: tuple[str, ...]
    source_types: tuple[str, ...]
    artifact_families: tuple[str, ...]
    use: str
    audience: str
    audience_scope_ids: tuple[str, ...]
    retention: ShareRetention
    max_origins: int
    max_inputs: int
    max_artifact_revisions: int
    max_scanned_sources: int

    @property
    def declared_scope_ids(self) -> tuple[str, ...]:
        """Name the Scopes this version declares, whatever its selection mode."""

        declared = {*self.selected_scope_ids, *self.exact_scope_ids}
        if self.root_scope_id is not None:
            declared.add(self.root_scope_id)
        return tuple(sorted(declared))

    def digest(self) -> str:
        payload = {
            "target_scope_id": self.target_scope_id,
            "selection": {
                "mode": self.selection_mode,
                "root_scope_id": self.root_scope_id,
                "selected_scope_ids": sorted(self.selected_scope_ids),
            },
            "exact_scope_ids": sorted(self.exact_scope_ids),
            "source_types": sorted(self.source_types),
            "artifact_families": sorted(self.artifact_families),
            "use": self.use,
            "audience": self.audience,
            "audience_scope_ids": sorted(self.audience_scope_ids),
            "retention": self.retention,
            "limits": {
                "max_origins": self.max_origins,
                "max_inputs": self.max_inputs,
                "max_artifact_revisions": self.max_artifact_revisions,
                "max_scanned_sources": self.max_scanned_sources,
            },
        }
        return "sha256:" + hashlib.sha256(rfc8785.dumps(payload)).hexdigest()


class ShareRunLimits(_ShareValue):
    """Bound one run's work; an over-budget range is refused, never truncated.

    `max_scanned_sources` caps the journal rows one origin may be scanned for, so
    ineligible rows cannot hide eligible ones behind a fixed page. Exceeding any
    bound refuses the whole run rather than pinning a partial conclusion.
    """

    max_origins: int = Field(default=16, ge=1, le=64)
    max_inputs: int = Field(default=32, ge=1, le=128)
    max_artifact_revisions: int = Field(default=16, ge=1, le=128)
    max_scanned_sources: int = Field(default=512, ge=1, le=4_096)


class ShareRuleDraft(_ShareValue):
    """One complete rule version, replacing the previous version atomically."""

    selection: ScopeSelection
    exact_scope_ids: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()
    artifact_families: tuple[str, ...] = ()
    terms: ShareRuleTerms
    limits: ShareRunLimits = Field(default_factory=ShareRunLimits)

    @field_validator("exact_scope_ids")
    @classmethod
    def validate_exact_scope_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("exact Scope selection must be unique")  # noqa: TRY003
        return tuple(sorted(values))

    @field_validator("source_types")
    @classmethod
    def validate_source_types(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("source types must be unique")  # noqa: TRY003
        return tuple(sorted(values))

    @field_validator("artifact_families")
    @classmethod
    def validate_artifact_families(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("artifact families must be unique")  # noqa: TRY003
        return tuple(sorted(values))

    @model_validator(mode="after")
    def validate_scope_terms(self) -> ShareRuleDraft:
        # An agreement names both ends: the target and the origins it may use.
        if self.selection.mode == "all":
            raise ValueError("a rule must name a bounded Scope range")  # noqa: TRY003
        overlap = set(self.selection.scope_ids) & set(self.exact_scope_ids)
        if overlap:
            raise ValueError("an extra Scope must not repeat the exact selection")  # noqa: TRY003
        return self

    def selection_paths(self, scope_id: str, resolved_scope_ids: frozenset[str] = frozenset()) -> tuple[str, ...]:
        """Return every path by which this draft selects one origin Scope.

        A descendant of the rule's subtree keeps the path that selected it, and an
        explicitly named Scope keeps its own path, so one address discovered twice
        keeps both paths and still counts as one piece of evidence.
        """

        paths = []
        root_scope_id = self.selection.root_scope_id
        if root_scope_id is not None and scope_id in resolved_scope_ids:
            paths.append(f"subtree:{root_scope_id}")
        if scope_id in self.selection.scope_ids:
            paths.append("exact")
        if scope_id in self.exact_scope_ids:
            paths.append("explicit")
        return tuple(paths)


class ShareRule(_ShareValue):
    """One immutable rule version as accepted for a target Scope."""

    scope_id: str
    rule_id: str
    version: int = Field(ge=1)
    status: ShareRuleStatus = "active"
    selection: ScopeSelection
    exact_scope_ids: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()
    artifact_families: tuple[str, ...] = ()
    terms: ShareRuleTerms
    limits: ShareRunLimits = Field(default_factory=ShareRunLimits)
    updated_at: datetime

    def declared_origins(self) -> tuple[str, ...]:
        """Name the ranges this version agrees to consider, in canonical order.

        A subtree's descendants are resolved when a run is pinned, so acceptance
        binds the declared range (the root and every explicit Scope) and each run
        records the exact origins it actually reached.
        """

        root_scope_id = self.selection.root_scope_id
        declared = [*self.selection.scope_ids, *self.exact_scope_ids]
        if root_scope_id is not None:
            declared.append(root_scope_id)
        return tuple(sorted(set(declared)))

    def terms_binding(self) -> ShareTermsBinding:
        return ShareTermsBinding(
            target_scope_id=self.scope_id,
            selection_mode=self.selection.mode,
            root_scope_id=self.selection.root_scope_id,
            selected_scope_ids=self.selection.scope_ids,
            exact_scope_ids=self.exact_scope_ids,
            source_types=self.source_types,
            artifact_families=self.artifact_families,
            use=self.terms.use,
            audience=self.terms.audience,
            # An unstated audience is the target Scope itself, so an agreement always
            # names its readers and a default can never widen disclosure.
            audience_scope_ids=self.terms.audience_scope_ids or (self.scope_id,),
            retention=self.terms.retention,
            max_origins=self.limits.max_origins,
            max_inputs=self.limits.max_inputs,
            max_artifact_revisions=self.limits.max_artifact_revisions,
            max_scanned_sources=self.limits.max_scanned_sources,
        )

    def draft(self) -> ShareRuleDraft:
        return ShareRuleDraft(
            selection=self.selection,
            exact_scope_ids=self.exact_scope_ids,
            source_types=self.source_types,
            artifact_families=self.artifact_families,
            terms=self.terms,
            limits=self.limits,
        )


class ShareInput(_ShareValue):
    """One exact pinned origin, counted once regardless of how many paths found it."""

    kind: ShareInputKind
    origin_scope_id: str
    role: ShareInputRole = "evidence"
    paths: tuple[str, ...]
    digest: str
    materialization: SourceMaterialization | None = None
    source_ref: SourceRef | None = None
    artifact: ArtifactAddress | None = None

    @model_validator(mode="after")
    def validate_address(self) -> ShareInput:
        if (self.source_ref is None) == (self.artifact is None):
            raise ValueError("a pinned input names exactly one Source or Artifact address")  # noqa: TRY003
        if self.artifact is not None and self.artifact.scope_id != self.origin_scope_id:
            raise ValueError("a pinned Artifact address must belong to its origin Scope")  # noqa: TRY003
        if self.kind == "source" and self.source_ref is None:
            raise ValueError("a Source input requires a Source reference")  # noqa: TRY003
        if self.kind == "artifact" and self.artifact is None:
            raise ValueError("an Artifact input requires an exact Artifact address")  # noqa: TRY003
        return self

    def address_key(self) -> str:
        """Identify one exact origin address for counting deduplication."""

        if self.artifact is not None:
            ref: ArtifactRef = self.artifact.artifact
            return f"artifact|{self.artifact.scope_id}|{ref.family}|{ref.artifact_id}|{ref.revision}"
        source = self.source_ref
        if source is None:
            raise ValueError("a pinned input has neither address")  # noqa: TRY003
        return f"source|{self.origin_scope_id}|{source.source_type}|{source.source_id}"


class ShareSourceWatermark(_ShareValue):
    """The journal position one origin Scope had reached when a run was pinned."""

    origin_scope_id: str
    position: int = Field(ge=0)


class ShareRunCoverage(_ShareValue):
    """Read-only answer to "does the recorded agreement cover this run?".

    Both sides are reported from durable records: the accepted coverage per rule
    version, and the origins a recorded run actually reached. A caller cannot make
    a run covered by asserting it.
    """

    rule_id: str
    rule_version: int = Field(ge=1)
    terms_digest: str
    accepted_origin_scope_ids: tuple[str, ...] = ()
    accepted_audience_scope_ids: tuple[str, ...] = ()
    declared_audience_scope_ids: tuple[str, ...] = ()
    run_origin_scope_ids: tuple[str, ...] = ()

    @property
    def origins_covered(self) -> bool:
        return set(self.run_origin_scope_ids) <= set(self.accepted_origin_scope_ids)

    @property
    def audiences_covered(self) -> bool:
        return set(self.declared_audience_scope_ids) <= set(self.accepted_audience_scope_ids)


class ShareRunOriginCoverage(_ShareValue):
    """Which accepted records cover the origins one pinned run actually recorded.

    A consumer needs to know whose consent applies to this run, not the union over all
    historical runs. Every recorded origin is paired with the acceptance rows that
    cover it; an origin with no covering row is reported as uncovered. Structural
    coverage is selection only and never authorizes reading the origin.
    """

    run_id: str
    rule_id: str
    rule_version: int = Field(ge=1)
    terms_digest: str
    run_origin_scope_ids: tuple[str, ...] = ()
    covered_origin_scope_ids: tuple[str, ...] = ()
    uncovered_origin_scope_ids: tuple[str, ...] = ()
    acceptance_ids_by_origin: tuple[tuple[str, tuple[str, ...]], ...] = ()
    declared_audience_scope_ids: tuple[str, ...] = ()
    accepted_audience_scope_ids: tuple[str, ...] = ()

    @property
    def origins_covered(self) -> bool:
        return not self.uncovered_origin_scope_ids

    @property
    def audiences_covered(self) -> bool:
        return set(self.declared_audience_scope_ids) <= set(self.accepted_audience_scope_ids)

    def acceptance_ids_for(self, origin_scope_id: str) -> tuple[str, ...]:
        """Return the acceptance rows whose contributor covers this origin."""

        return next(
            (ids for scope_id, ids in self.acceptance_ids_by_origin if scope_id == origin_scope_id),
            (),
        )


class ShareAcceptanceContribution(_ShareValue):
    """The origins one authoritative acceptance row actually contributes.

    A record states the Scopes it covers; this reports the effective origins those
    Scopes contribute for the rule version, including a declared root's descendants
    and never more than the record itself accepted.
    """

    acceptance_id: str
    actor: str
    side: ShareAcceptanceSide
    terms_digest: str
    covered_scope_ids: tuple[str, ...] = ()
    contributed_origin_scope_ids: tuple[str, ...] = ()


class ShareRun(_ShareValue):
    """One recorded evaluation of a pinned rule version against pinned inputs."""

    scope_id: str
    run_id: str
    rule_id: str
    rule_version: int = Field(ge=1)
    state: ShareRunState = "pinned"
    outcome: ShareRunOutcome = "pending"
    terms: ShareRuleTerms
    inputs: tuple[ShareInput, ...] = ()
    baseline: ArtifactAddress | None = None
    baseline_digest: str | None = None
    source_watermarks: tuple[ShareSourceWatermark, ...] = ()
    origins: tuple[str, ...] = ()
    limits: ShareRunLimits = Field(default_factory=ShareRunLimits)
    notification_key: str | None = None
    retry_of: str | None = None
    dream_run_id: str | None = None
    candidate_id: str | None = None
    committed: ArtifactAddress | None = None
    created_at: datetime

    @property
    def retry(self) -> bool:
        return self.retry_of is not None

    def origin_scopes(self) -> tuple[str, ...]:
        return tuple(sorted({item.origin_scope_id for item in self.inputs}))

    def digest(self) -> str:
        """Cover exactly what a retry must reuse; a newer revision changes it."""

        payload = {
            "scope_id": self.scope_id,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "terms": self.terms.model_dump(mode="json"),
            "inputs": [
                {
                    "kind": item.kind,
                    "origin_scope_id": item.origin_scope_id,
                    "role": item.role,
                    "address": item.address_key(),
                    "digest": item.digest,
                    "paths": list(item.paths),
                }
                for item in sorted(self.inputs, key=ShareInput.address_key)
            ],
            "baseline": None if self.baseline is None else self.baseline.model_dump(mode="json"),
            "baseline_digest": self.baseline_digest,
            "source_watermarks": sorted((item.origin_scope_id, item.position) for item in self.source_watermarks),
        }
        return "sha256:" + hashlib.sha256(rfc8785.dumps(payload)).hexdigest()
