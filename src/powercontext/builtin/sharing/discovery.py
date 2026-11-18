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

"""One bounded, app-triggered evaluation of a standing sharing agreement.

This is the application surface an app trigger calls. It discovers the material an
agreed bounded range currently holds, fixes exactly what it found in one immutable pin,
and admits the existing Dream run for it. It is deliberately thin: enumeration, Scopes,
Access, evidence and admission stay with their existing owners, and nothing here is a
second scheduler, subscriber, execution state machine or query engine.

Two rules shape the whole module:

- **A notification is an identity, not a request to recompute.** A retry resolves the
  evaluation its durable key already recorded, before any enumeration and without
  treating today's Scope state as input. It reuses the recorded pin and run and never
  enqueues or generates twice.
- **The caller sees a run reference, not the pin.** Every entry point authorizes the
  caller first, and the result names only the target run. Raw pins, foreign Scope ids,
  addresses and digests stay inside the owning services.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.authz.models import AccessAction, ResourceRef
from powercontext.builtin.dream.models import CreateDreamRunRequest, DreamOperation
from powercontext.builtin.evidence.models import content_digest
from powercontext.builtin.sharing.models import (
    ShareInput,
    ShareRejectedError,
    ShareSnapshotDraft,
    membership_digest,
)

if TYPE_CHECKING:
    from powercontext.builtin.authz.models import PrincipalRef
    from powercontext.builtin.authz.service import AccessAuditContext, AccessControlService
    from powercontext.builtin.dream.service import DreamService
    from powercontext.builtin.persistence.artifacts import ArtifactRepository
    from powercontext.builtin.persistence.database import AsyncDatabase
    from powercontext.builtin.persistence.sources import SourceRepository
    from powercontext.builtin.sharing.service import ShareAgreementService

# Explicit, small budgets. Overflow refuses instead of returning a truncated discovery,
# because a pin that silently omits part of an agreed range would misstate the agreement.
MAX_DISCOVERY_SCOPES = 32
MAX_DISCOVERY_ITEMS = 64


class ShareDiscoveryRefused(ShareRejectedError):
    """A discovery request cannot be answered under the current agreement."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ShareDiscoveryResult(BaseModel):
    """What an authorized target caller may see about one evaluated notification.

    Only the target run reference and its state: enough to follow the run through the
    existing Review path, and nothing about the origins that contributed to it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    rule_id: str
    notification_key: str
    run_id: str
    operation: str
    replayed: bool
    status: str = Field(min_length=1)


class ShareDiscoveryApplication:
    """Trigger one standing evaluation of an agreed range for the Profile workflow."""

    # The one operation this slice supports. Anything else refuses rather than running a
    # workflow whose discovery inputs were never designed for it.
    SUPPORTED_OPERATION: DreamOperation = "initialize_profile"

    def __init__(
        self,
        database: AsyncDatabase,
        *,
        access: AccessControlService,
        agreements: ShareAgreementService,
        sources: SourceRepository,
        artifacts: ArtifactRepository,
        dreams: DreamService,
    ) -> None:
        self._database = database
        self._access = access
        self._agreements = agreements
        self._sources = sources
        self._artifacts = artifacts
        self._dreams = dreams

    async def trigger(
        self,
        scope_id: str,
        rule_id: str,
        notification_key: str,
        /,
        *,
        principal: PrincipalRef | None,
        context: AccessAuditContext,
    ) -> ShareDiscoveryResult:
        """Evaluate one notification of a standing agreement, at most once.

        A repeated key never evaluates again: it returns the run the first evaluation
        recorded, after the caller is authorized for it. When no evaluation exists yet the
        range is enumerated under explicit budgets, each contributing read is authorized
        before its material is fetched, and the exact result is pinned and admitted.
        """

        if principal is None:
            raise ShareDiscoveryRefused("access_identity_required")
        # Authority first, before any lookup or output. A caller who may not contribute to
        # the target never learns whether this key was evaluated.
        await self._access.require(
            principal, AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(scope_id), context=context
        )
        replay = await self._resolve_recorded(scope_id, rule_id, notification_key)
        if replay is not None:
            return replay
        return await self._evaluate(scope_id, rule_id, notification_key, principal, context)

    async def _resolve_recorded(
        self, scope_id: str, rule_id: str, notification_key: str, /
    ) -> ShareDiscoveryResult | None:
        """Resolve the evaluation this durable key already recorded, if any.

        This reads the recorded pin binding only. It deliberately does not enumerate,
        re-derive the membership, or consult today's Scope versions: a retry answers what
        was decided, not what would be decided now.
        """

        async with self._database.transaction() as connection:
            row = await self._agreements.repository.snapshot_by_notification(
                connection, scope_id, rule_id, notification_key
            )
            if row is None:
                return None
            run_id = self._agreements.repository.decode_snapshot(row).dream_run_id
        if run_id is None:
            # Pinned but never claimed: interrupted after the pin, before admission. The
            # cold path adopts this exact pin instead of recording a second one.
            return None
        run = await self._dreams.get(scope_id, "runtime", run_id)
        return ShareDiscoveryResult(
            scope_id=scope_id,
            rule_id=rule_id,
            notification_key=notification_key,
            run_id=run.run_id,
            operation=str(run.operation),
            replayed=True,
            status=str(run.status),
        )

    async def _evaluate(
        self,
        scope_id: str,
        rule_id: str,
        notification_key: str,
        principal: PrincipalRef,
        context: AccessAuditContext,
        /,
    ) -> ShareDiscoveryResult:
        """Discover, pin and admit one evaluation of the agreed range."""

        rule = await self._agreements.get(scope_id, rule_id)
        if rule.terms.use != "project-conclusion":
            # The one workflow this slice drives exists for the target's own conclusion.
            raise ShareDiscoveryRefused("shared_use_ineligible_operation")
        async with self._database.transaction() as connection:
            # One resolution of the declared range, used for both enumeration and the
            # membership this pin must state. There is no second traversal.
            declared = await self._agreements.declared_scopes(connection, rule)
        if len(declared) > MAX_DISCOVERY_SCOPES:
            raise ShareDiscoveryRefused("shared_use_discovery_scope_budget")
        inputs, artifacts = await self._discover(declared, principal, context)
        if not inputs:
            raise ShareDiscoveryRefused("shared_use_discovery_empty")
        versions = tuple(sorted((member.scope_id, int(member.version)) for member in declared))
        draft = ShareSnapshotDraft(
            rule_id=rule.rule_id,
            rule_version=rule.version,
            terms_digest=rule.terms_digest,
            membership_digest=membership_digest(versions),
            inputs=inputs,
            notification_key=notification_key,
        )
        # The pin re-proves the whole authority contract; discovery only proposed inputs.
        pinned = await self._agreements.pin(scope_id, draft, principal=principal, context=context)
        run = await self._dreams.admit_bound(
            scope_id,
            "runtime",
            # The request selects the discovered Artifacts so the ordinary admission
            # validation is satisfied; a bound run still admits only its pin.
            CreateDreamRunRequest(
                operation=self.SUPPORTED_OPERATION,
                artifacts=artifacts,
                idempotency_key=notification_key,
            ),
            snapshot_id=pinned.snapshot_id,
        )
        return ShareDiscoveryResult(
            scope_id=scope_id,
            rule_id=rule_id,
            notification_key=notification_key,
            run_id=run.run_id,
            operation=str(run.operation),
            replayed=False,
            status=str(run.status),
        )

    async def _discover(
        self, declared, principal: PrincipalRef, context: AccessAuditContext, /
    ) -> tuple[tuple[ShareInput, ...], tuple[ArtifactRef, ...]]:
        """Enumerate eligible input in the declared range under an explicit item budget.

        Experience artifacts are what this workflow consumes, so they are enumerated from
        the owning Artifact heads (references only) and pinned with the exact material the
        owning reader returns. Their direct lineage Sources are pinned alongside, because
        that is the material the evidence resolver will actually reach.

        Nothing here comes from the caller: the range is the rule's own, and every
        contributing read is authorized before the material is used, so discovery never
        becomes a way to see a Scope the caller may not read.
        """

        inputs: list[ShareInput] = []
        artifacts: list[ArtifactRef] = []
        for member in declared:
            async with self._database.transaction() as connection:
                heads = await self._artifacts.active_heads(
                    connection,
                    member.scope_id,
                    families=("experience",),
                    limit=MAX_DISCOVERY_ITEMS - len(inputs) + 1,
                )
            for head in heads:
                await self._access.require(
                    principal, AccessAction.SCOPE_READ, ResourceRef.scope(member.scope_id), context=context
                )
                async with self._database.transaction() as connection:
                    artifact = await self._artifacts.get(connection, member.scope_id, head)
                inputs.append(
                    ShareInput(
                        kind="artifact",
                        origin_scope_id=member.scope_id,
                        artifact=head,
                        digest=content_digest(artifact.model_dump_json().encode()),
                    )
                )
                artifacts.append(head)
                for source_ref in artifact.lineage.sources:
                    async with self._database.transaction() as connection:
                        stored = await self._sources.get_many(connection, member.scope_id, (source_ref,))
                    inputs.append(
                        ShareInput(
                            kind="source",
                            origin_scope_id=member.scope_id,
                            source=source_ref,
                            digest=content_digest(stored[0].value.model_dump_json().encode()),
                        )
                    )
                if len(inputs) > MAX_DISCOVERY_ITEMS:
                    # Refuse rather than pin a partial view of the agreed range.
                    raise ShareDiscoveryRefused("shared_use_discovery_item_budget")
        return tuple(inputs), tuple(artifacts)


__all__ = [
    "MAX_DISCOVERY_ITEMS",
    "MAX_DISCOVERY_SCOPES",
    "ShareDiscoveryApplication",
    "ShareDiscoveryRefused",
    "ShareDiscoveryResult",
]
