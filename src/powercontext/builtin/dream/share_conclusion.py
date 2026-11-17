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

"""Link one pinned share run to the Dream execution it admitted.

D owns the conclusion: its outcome is one-way, its replay is idempotent, and only a
real committed revision may mark a run committed. This module decides only *when*
that existing call is made, isolates its failure from the owning business result, and
lets a later replay or a read of the same result reconcile it.

The bookkeeping is deliberately recoverable rather than fire-and-forget: D's own
one-way and idempotent rules make a second identical attempt safe, so a missed link is
repaired from persisted Dream/Candidate state without repeating generation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from powercontext._logging import log_safely
from powercontext.artifacts import ArtifactRef
from powercontext.builtin.sharing.models import ShareRun
from powercontext.builtin.sharing.service import ShareRuleService

logger = logging.getLogger(__name__)

# What a finished Dream run establishes about the pinned evaluation.
DreamConclusion = Literal["no_change", "needs_evidence", "rejected"]


def _log_link_failure(error: BaseException, *, scope_id: str, operation: str, policy_version: int | None) -> None:
    """Report one bookkeeping failure with registered fields only.

    The exception *type* is diagnostic; its message and values are not recorded, so no
    origin reference, digest or provider text reaches the log.
    """

    log_safely(
        logger,
        logging.WARNING,
        "Share run conclusion was not recorded",
        extra={
            "event": "dream.share_conclusion.failed",
            "operation": operation,
            "unit": "stage",
            "outcome": "failure",
            "scope": scope_id,
            "policy_version": policy_version,
            "exception_type": type(error).__name__,
        },
    )


async def record_conclusion(
    service: ShareRuleService,
    *,
    scope_id: str,
    share_run_id: str,
    dream_run_id: str,
    candidate_id: str | None = None,
    outcome: DreamConclusion | None = None,
    committed: ArtifactRef | None = None,
    operation: str = "share_run_conclusion",
) -> ShareRun | None:
    """Record this execution's conclusion on the pinned run.

    Returns the stored run, or ``None`` when the link could not be recorded. Cancellation
    always propagates: it is the caller shutting down, not a bookkeeping failure.
    """

    try:
        return await service.record_outcome(
            scope_id,
            share_run_id,
            outcome=outcome,
            committed=committed,
            dream_run_id=dream_run_id,
            candidate_id=candidate_id,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        _log_link_failure(error, scope_id=scope_id, operation=operation, policy_version=None)
        return None


def conclusion_for(outcome: str | None) -> DreamConclusion | None:
    """Map a Dream outcome onto the conclusion D already defines, or nothing.

    A proposal and a failure are deliberately not concluded: only approval establishes
    the official revision, and D has no failure outcome to record.
    """

    if outcome == "no_change":
        return "no_change"
    if outcome == "needs_evidence":
        return "needs_evidence"
    return None


__all__ = ["DreamConclusion", "conclusion_for", "record_conclusion"]
