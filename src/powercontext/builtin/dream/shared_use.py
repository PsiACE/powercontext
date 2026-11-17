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

"""Bind one Dream run to a pinned share run whose agreed use covers its inputs.

The agreed-use decision belongs to Access Control; this module only states which
operations may carry foreign evidence, what each operation's use label is, and
which pinned inputs the request must equal before Access is asked. It never grants
authority, never mints consent, and never caches a decision.
"""

from __future__ import annotations

from dataclasses import dataclass

from powercontext.builtin.dream.models import CreateDreamRunRequest, DreamError, DreamOperation
from powercontext.builtin.evidence.models import ScopedEvidence
from powercontext.builtin.sharing.models import ShareRun

# Operations that may carry another Scope's material at all. Every other operation
# keeps refusing it, so agreement for one operation never admits a sibling.
FOREIGN_EVIDENCE_OPERATIONS: frozenset[DreamOperation] = frozenset({"refine_experience", "initialize_profile"})


class SharedUseBindingError(DreamError):
    """A request does not match the pinned agreement it claims."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SharedUseBinding:
    """Identity of the pinned agreement one run was admitted under."""

    share_run_id: str
    rule_id: str
    rule_version: int
    use: str


def require_foreign_operation(operation: DreamOperation) -> None:
    """Refuse another Scope's material on an operation that has no admission for it."""

    if operation not in FOREIGN_EVIDENCE_OPERATIONS:
        raise DreamError("unsupported_shared_use")


def binding_for(request: CreateDreamRunRequest, *, scope_id: str) -> SharedUseBinding | None:
    """Return the claimed binding, refusing shapes this operation cannot carry."""

    foreign = tuple(item for item in request.scoped if item.scope_id != scope_id)
    if not foreign:
        # A pure local request needs no agreement and keeps its old behaviour.
        return None
    require_foreign_operation(request.operation)
    if request.share_run_id is None:
        raise DreamError("unsupported_shared_use")
    return SharedUseBinding(
        share_run_id=request.share_run_id,
        rule_id="",
        rule_version=0,
        use="",
    )


def request_inputs(run: ShareRun, *, scope_id: str) -> tuple[tuple[str, str | None, str | None, int | None], ...]:
    """Pinned inputs of one run, as comparable identity tuples.

    The comparison is exact on the owning Scope, kind, reference and revision, so a
    request can neither widen nor substitute what the run pinned.
    """

    pinned: list[tuple[str, str | None, str | None, int | None]] = []
    for item in run.inputs:
        if item.origin_scope_id == scope_id:
            continue
        if item.source_ref is not None:
            pinned.append((item.origin_scope_id, item.source_ref.model_dump_json(), None, None))
        elif item.artifact is not None:
            pinned.append((
                item.origin_scope_id,
                None,
                item.artifact.artifact.model_dump_json(),
                item.artifact.artifact.revision,
            ))
    return tuple(sorted(pinned))


def request_scoped_inputs(
    scoped: tuple[ScopedEvidence, ...], *, scope_id: str
) -> tuple[tuple[str, str | None, str | None, int | None], ...]:
    selected: list[tuple[str, str | None, str | None, int | None]] = []
    for item in scoped:
        if item.scope_id == scope_id:
            continue
        if item.source is not None:
            selected.append((item.scope_id, item.source.model_dump_json(), None, None))
        elif item.artifact is not None:
            selected.append((
                item.scope_id,
                None,
                item.artifact.model_dump_json(),
                item.artifact.revision,
            ))
    return tuple(sorted(selected))


async def require_matching_inputs(pinned: ShareRun, request: CreateDreamRunRequest, *, scope_id: str) -> None:
    """Refuse a request whose foreign selection is not exactly what the run pinned."""

    if request_inputs(pinned, scope_id=scope_id) != request_scoped_inputs(request.scoped, scope_id=scope_id):
        raise SharedUseBindingError("shared_use_inputs")


__all__ = [
    "FOREIGN_EVIDENCE_OPERATIONS",
    "SharedUseBinding",
    "SharedUseBindingError",
    "binding_for",
    "request_inputs",
    "request_scoped_inputs",
    "require_foreign_operation",
    "require_matching_inputs",
]
