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

"""Standing sharing rules, bounded discovery, and pinned evaluation runs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from powercontext.builtin.sharing.models import (
    ShareAcceptance,
    ShareAcceptanceConflictError,
    ShareAcceptanceSide,
    ShareBudgetExceededError,
    ShareError,
    ShareInput,
    ShareInputKind,
    ShareInputRole,
    ShareMaterialization,
    ShareRetention,
    ShareRule,
    ShareRuleAcceptanceError,
    ShareRuleDraft,
    ShareRuleInvalidatedError,
    ShareRuleNotFoundError,
    ShareRuleServiceUnavailable,
    ShareRuleStatus,
    ShareRuleTerms,
    ShareRuleVersionConflictError,
    ShareRun,
    ShareRunLimits,
    ShareRunNotFoundError,
    ShareRunOutcome,
    ShareRunState,
    ShareRunStateError,
    ShareSourceWatermark,
    ShareTermsBinding,
)

if TYPE_CHECKING:
    from powercontext.builtin.sharing.service import ShareAuthorizer, ShareOriginCheck, ShareRuleService


def __getattr__(name: str) -> Any:
    if name in {"ShareAuthorizer", "ShareOriginCheck", "ShareRuleService"}:
        from powercontext.builtin.sharing import service

        return getattr(service, name)
    raise AttributeError(name)


__all__ = [
    "ShareAcceptance",
    "ShareAcceptanceConflictError",
    "ShareAcceptanceSide",
    "ShareAuthorizer",
    "ShareBudgetExceededError",
    "ShareError",
    "ShareInput",
    "ShareInputKind",
    "ShareInputRole",
    "ShareMaterialization",
    "ShareOriginCheck",
    "ShareRetention",
    "ShareRule",
    "ShareRuleAcceptanceError",
    "ShareRuleDraft",
    "ShareRuleInvalidatedError",
    "ShareRuleNotFoundError",
    "ShareRuleService",
    "ShareRuleServiceUnavailable",
    "ShareRuleStatus",
    "ShareRuleTerms",
    "ShareRuleVersionConflictError",
    "ShareRun",
    "ShareRunLimits",
    "ShareRunNotFoundError",
    "ShareRunOutcome",
    "ShareRunState",
    "ShareRunStateError",
    "ShareSourceWatermark",
    "ShareTermsBinding",
]
