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

"""Target Scope entry point for standing sharing rules and pinned runs.

The target Scope owns its rules, so every call is bound to it. Discovery reads
external origins, which is why it runs inside a scoped operation but outside the
target's write lock: it takes no local processing progress and publishes nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.sharing.models import (
    ShareAcceptance,
    ShareAcceptanceSide,
    ShareRule,
    ShareRuleDraft,
    ShareRuleStatus,
    ShareRun,
    ShareRunCoverage,
    ShareRunOutcome,
)
from powercontext.builtin.sources import validate_scope_id

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime
    from powercontext.builtin.sharing.service import ShareRuleService


class ShareRuleServiceUnavailable(RuntimeError):
    """The Runtime was composed without standing-rule storage."""

    def __init__(self) -> None:
        super().__init__("standing sharing rules are unavailable")


class ScopedShareApplication:
    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    def _service(self) -> ShareRuleService:
        service = self._runtime.share_rules
        if service is None:
            raise ShareRuleServiceUnavailable()
        return service

    async def create(self, draft: ShareRuleDraft, /) -> ShareRule:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._service().create(self.scope_id, draft)

    async def update(self, rule_id: str, expected_version: int, draft: ShareRuleDraft, /) -> ShareRule:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._service().update(self.scope_id, rule_id, expected_version, draft)

    async def invalidate(self, rule_id: str, expected_version: int, /) -> ShareRule:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._service().invalidate(self.scope_id, rule_id, expected_version)

    async def get(self, rule_id: str, /, *, version: int | None = None) -> ShareRule:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._service().get(self.scope_id, rule_id, version=version)

    async def list(self, /, *, status: ShareRuleStatus | None = None) -> tuple[ShareRule, ...]:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._service().list(self.scope_id, status=status)

    async def accept(
        self,
        rule_id: str,
        version: int,
        side: ShareAcceptanceSide,
        /,
        *,
        actor: str,
        covered_scope_ids: tuple[str, ...] | None = None,
    ) -> ShareAcceptance:
        """Record one authenticated actor's acceptance through the target Scope.

        The authenticated boundary supplies the actor and the Scopes that actor
        actually covers; acceptance defaults to the version's whole declared side.
        """

        async with self._runtime._scoped_operation(self.scope_id):
            return await self._service().accept(
                self.scope_id, rule_id, version, side, actor=actor, covered_scope_ids=covered_scope_ids
            )

    async def agree(self, rule_id: str, version: int, /) -> tuple[ShareAcceptance, ...]:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._service().acceptances_for(self.scope_id, rule_id, version)

    async def coverage(self, rule_id: str, version: int, /) -> ShareRunCoverage:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._service().coverage(self.scope_id, rule_id, version)

    async def covers_run(self, run_id: str, /) -> bool:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._service().covers_run(self.scope_id, run_id)

    async def resolve(self, rule_id: str, /, *, notification_key: str | None = None) -> ShareRun:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._service().resolve(self.scope_id, rule_id, notification_key=notification_key)

    async def retry(self, run_id: str, /) -> ShareRun:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._service().retry(self.scope_id, run_id)

    async def get_run(self, run_id: str, /) -> ShareRun:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._service().get_run(self.scope_id, run_id)

    async def list_runs(self, /, *, rule_id: str | None = None) -> tuple[ShareRun, ...]:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._service().list_runs(self.scope_id, rule_id=rule_id)

    async def record_outcome(
        self,
        run_id: str,
        /,
        *,
        outcome: ShareRunOutcome | None = None,
        committed: ArtifactRef | None = None,
        dream_run_id: str | None = None,
        candidate_id: str | None = None,
    ) -> ShareRun:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._service().record_outcome(
                self.scope_id,
                run_id,
                outcome=outcome,
                committed=committed,
                dream_run_id=dream_run_id,
                candidate_id=candidate_id,
            )


class ShareApplication:
    """Select the scoped standing-sharing application service."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedShareApplication:
        return ScopedShareApplication(self._runtime, scope_id)
