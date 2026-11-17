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

"""Admission and transactional Dream execution, independent of scheduling."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext._logging import log_safely
from powercontext.artifacts import ArtifactRef, RecordedOrigin
from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.handoff.models import HandoffContent
from powercontext.builtin.artifacts.profile.models import (
    PROFILE_ARTIFACT_ID,
    ProfileCandidateProposal,
    ProfileDreamPolicy,
    ProfileWriteContent,
)
from powercontext.builtin.artifacts.prompt.models import PromptContent
from powercontext.builtin.artifacts.skill import SkillContent
from powercontext.builtin.artifacts.topic_memory.models import TopicMemoryContent
from powercontext.builtin.catalog_changes.models import CatalogChangeCandidate, CatalogChangeProposal, TagChangeProposal
from powercontext.builtin.catalog_changes.service import CatalogChangeService
from powercontext.builtin.dream.bindings import (
    DREAM_BINDINGS,
    binding_for_request,
    operation_spec,
    operations_for_binding,
)
from powercontext.builtin.dream.generation import DreamGenerationInput, DreamGenerator
from powercontext.builtin.dream.models import (
    DREAM_PROMPT_VERSION,
    CreateDreamRunRequest,
    DreamBudget,
    DreamCandidateRef,
    DreamError,
    DreamOperation,
    DreamPlan,
    DreamRecord,
    DreamRun,
    DreamRunPage,
    DreamUsage,
    ListDreamRunsRequest,
)
from powercontext.builtin.dream.provenance import candidate_audit, proposal_fingerprint
from powercontext.builtin.dream.share_conclusion import conclusion_for, record_conclusion
from powercontext.builtin.dream.shared_use import (
    binding_for,
    require_foreign_operation,
)
from powercontext.builtin.evidence.models import (
    EvidenceResolutionError,
    ResolvedEvidence,
    reference_key,
    unique_references,
)
from powercontext.builtin.evidence.resolver import (
    EvidenceAuthorizer,
    EvidenceReference,
    EvidenceResolver,
    ScopedUseAuthorizer,
    evidence_id,
)
from powercontext.builtin.evidence.selection import select_evidence
from powercontext.builtin.inference.errors import (
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
)
from powercontext.builtin.inference.models import InferenceUsage
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.candidates import attach_candidate_audit, proposal_digest
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.dream import DreamRepository, database_now
from powercontext.builtin.persistence.errors import (
    ArtifactProcessingLeadershipLostError,
    RepositoryNotFoundError,
)
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.profile import ProfilePolicyRepository
from powercontext.builtin.persistence.tables import CANDIDATE_HEADS_TABLE, SCOPES_TABLE
from powercontext.builtin.processing_diagnostics import safe_classify_processing_error, safe_log_fields
from powercontext.builtin.review.errors import ArtifactTargetConflictError, InvalidCandidateError
from powercontext.builtin.review.generation import SkillGenerationOrigin, validate_skill_lineage
from powercontext.builtin.review.models import Candidate, CandidateStatus
from powercontext.builtin.review.service import ReviewService
from powercontext.builtin.runtime.processing_contracts import processing_error_code
from powercontext.builtin.runtime.processing_execution import InvocationAlreadyHandled, ScopeInvocation
from powercontext.builtin.sharing.models import ShareRun, ShareRunAlreadyBoundError
from powercontext.builtin.sharing.service import ShareRuleService
from powercontext.builtin.tags import TagPreconditionError

DreamPermission = Literal["read", "contribute", "write", "write_tags"]
DreamAuthorizer = Callable[[str, str, DreamPermission, EvidenceReference | None], Awaitable[None]]
EvidenceFactory = Callable[[str, EvidenceAuthorizer], EvidenceResolver]
ReviewFactory = Callable[[str, AsyncConnection | None], ReviewService]
CandidateAttester = Callable[[AsyncConnection, DreamRecord, str, str], Awaitable[None]]
# Recheck one run's agreed use from authoritative ids. F never supplies coverage,
# digests, acceptances or grants; Access loads and decides all of them.
RunUseCheck = Callable[[str, DreamRun, DreamRecord], Awaitable[None]]
# Recheck one Candidate's bound run agreement while its commit transaction is open.
CandidateUseCheck = Callable[[AsyncConnection, str, Candidate[Any]], Awaitable[None]]
CatalogFactory = Callable[[str, AsyncConnection | None], CatalogChangeService]
logger = logging.getLogger(__name__)


class DreamService:
    def __init__(
        self,
        *,
        database: AsyncDatabase,
        artifacts: ArtifactRepository,
        evidence: EvidenceFactory,
        review: ReviewFactory,
        generator: DreamGenerator | None,
        budget: DreamBudget | None = None,
        max_pending_per_scope: int = 32,
        authorize: DreamAuthorizer | None = None,
        authorization_context: Callable[[], AbstractAsyncContextManager[None]] = nullcontext,
        attest_candidate: CandidateAttester | None = None,
        authorize_candidate: CandidateAttester | None = None,
        authorize_use: ScopedUseAuthorizer | None = None,
        check_run_use: RunUseCheck | None = None,
        sharing: Callable[[], ShareRuleService | None] | None = None,
        bind_executor: Callable[[str], object] | None = None,
        clear_executor: Callable[[object], None] | None = None,
        operations: tuple[DreamOperation, ...] = (),
        processing: ScopeInvocation | None = None,
        catalog_changes: CatalogFactory | None = None,
    ) -> None:
        self.database = database
        self.repository = DreamRepository()
        self.artifacts = artifacts
        self.evidence = evidence
        self.review = review
        self.generator = generator
        self.budget = DreamBudget() if budget is None else budget
        self.max_pending_per_scope = max_pending_per_scope
        self.authorize = authorize
        self.authorization_context = authorization_context
        self.attest_candidate = attest_candidate
        self.authorize_candidate = authorize_candidate
        # Agreed cross-Scope use comes from the Access boundary that owns it. When no
        # such authority is wired, another Scope's material is not usable as evidence.
        self.authorize_use = authorize_use
        # Absent, a foreign run cannot be admitted at all: no hook means no agreement.
        self.check_run_use = check_run_use
        # D's conclusion writer. F only decides when to call it; D owns the state.
        self.sharing = sharing
        # Bind/clear the recorded executor around background work. Only a Server adapter
        # that owns the request principal may supply these.
        self.bind_executor = bind_executor
        self.clear_executor = clear_executor
        self.operations = operations
        self.processing = processing
        self.intents = ArtifactProcessingIntentRepository()
        self.catalog_changes = catalog_changes

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncConnection]:
        async with self.authorization_context(), self.database.transaction() as connection:
            if self.processing is not None:
                await self.processing.guard(connection)
            yield connection

    async def _authorize(
        self,
        scope_id: str,
        principal_id: str,
        permission: DreamPermission,
        ref: EvidenceReference | None = None,
    ) -> None:
        if self.authorize is not None:
            await self.authorize(scope_id, principal_id, permission, ref)
        elif principal_id != "runtime":
            raise DreamError("access_revoked")

    def _resolver(self, scope_id: str, principal_id: str) -> EvidenceResolver:
        async def authorize(ref: EvidenceReference) -> None:
            await self._authorize(scope_id, principal_id, "read", ref)

        async def authorize_origin(origin_scope_id: str, ref: EvidenceReference) -> None:
            # Read authority is checked at the Scope that owns the evidence.
            await self._authorize(origin_scope_id, principal_id, "read", ref)

        resolver = self.evidence(scope_id, authorize)
        resolver.authorize_origin = authorize_origin
        resolver.authorize_use = self.authorize_use
        resolver.limits = self.budget
        return resolver

    async def create(  # noqa: C901 - ordered admission checks stay together
        self, scope_id: str, principal_id: str, request: CreateDreamRunRequest
    ) -> DreamRun:
        # Refuse unusable external evidence before any queue write, body read, or model
        # call: a billed attempt that ends in a late refusal is not an admission check.
        _require_recordable_origins(scope_id, request)
        await self._authorize(scope_id, principal_id, "read")
        async with self._transaction() as connection:
            existing = await self.repository.find_request(connection, scope_id, principal_id, request)
            if existing is not None:
                return existing.run
        await self._authorize(scope_id, principal_id, "contribute")
        if request.operation not in self.operations:
            raise DreamError("capability_unavailable")
        if request.operation == "revise_prompt" and request.target is not None:
            await self.review(scope_id, None).require_prompt_target(request.target)
        shared = binding_for(request, scope_id=scope_id)
        if shared is not None:
            # Validate the pinned agreement before this run is accepted, and outside the
            # admission transaction, so the decision's own reads cannot deadlock it. The
            # per-origin read is checked here too, so the first foreign read this run
            # depends on is a decision, not a side effect of resolution.
            await self._require_shared_use(scope_id, principal_id, request, shared.share_run_id)
            await self._authorize_scoped_origins(scope_id, principal_id, request)
        resolver = self._resolver(scope_id, principal_id)
        # One execution identity, allocated once: a replayed request returns the run it
        # already created and never claims a second identity for the same pinned run.
        run_id = "dr_" + uuid4().hex
        async with self._transaction() as connection:
            # Lock the intent before business rows, matching Worker commit order.
            binding = binding_for_request(request)
            if not any(DREAM_BINDINGS.get(operation) == binding for operation in self.operations):
                raise DreamError("capability_unavailable")
            await self.intents.ensure(connection, scope_id, binding)
            await self.intents.load(connection, scope_id, binding, for_update=True)
            # Scope-local admission capacity is durable and serialized across replicas.
            result = await connection.execute(
                update(SCOPES_TABLE)
                .where(
                    SCOPES_TABLE.c.scope_id == scope_id,
                )
                .values(version=SCOPES_TABLE.c.version)
            )
            if result.rowcount != 1:
                raise DreamError("scope_not_found")
            existing = await self.repository.find_request(connection, scope_id, principal_id, request, current=True)
            if existing is not None:
                return existing.run
            profile_policy = (
                await ProfilePolicyRepository().get(connection, scope_id, for_update=True)
                if request.operation in {"revise_profile", "initialize_profile"}
                else None
            )
            if request.operation == "initialize_profile":
                # A first Profile exists only while no Profile head does; an existing one
                # is refused here, at admission, and is never overwritten.
                await self._require_no_profile(connection, scope_id)
            await self._check_target(connection, scope_id, principal_id, request.target)

            if request.tag_target is not None:
                await self._check_tag_target(connection, scope_id, principal_id, request)
            artifacts, target = _resolve_references(request)
            await resolver.resolve(
                connection,
                sources=request.sources,
                artifacts=artifacts,
                scoped=request.scoped,
                target=target,
                project=False,
                lock_memory=True,
                expose_catalog_target=request.operation == "revise_tags",
            )
            if await self.repository.pending_count(connection, scope_id) >= self.max_pending_per_scope:
                raise DreamError("capacity_exceeded")
            if shared is not None and self.sharing is not None:
                # Bind the pinned evaluation to this execution inside the admission
                # transaction, before any generation: D's claim joins it, so a rollback or
                # a cancellation leaves no orphan binding and a second execution cannot take
                # the same run. Identity only; authority was already checked above.
                service = self.sharing()
                if service is None:
                    raise DreamError("shared_use_unavailable")
                try:
                    await service.claim(connection, scope_id, shared.share_run_id, dream_run_id=run_id)
                except ShareRunAlreadyBoundError as error:
                    # Another execution owns this pinned run; refuse before billing.
                    raise DreamError("shared_use_run") from error
            intent = await self.intents.request(connection, scope_id, binding)
            record = DreamRecord(
                run=DreamRun(
                    scope_id=scope_id,
                    run_id=run_id,
                    operation=request.operation,
                    target=request.target,
                    tag_target=request.tag_target,
                    accepted_at=await database_now(connection),
                    budget=self.budget,
                ),
                request=request,
                principal_id=principal_id,
                share_run_id=request.share_run_id,
                request_generation=intent.requested_generation,
                profile_policy=None if profile_policy is None else ProfileDreamPolicy.from_policy(profile_policy),
            )
            return (await self.repository.create(connection, record)).run

    async def get(self, scope_id: str, principal_id: str, run_id: str) -> DreamRun:
        await self._authorize(scope_id, principal_id, "read")
        async with self._transaction() as connection:
            record = await self.repository.get(connection, scope_id, run_id)
        run = record.run
        if run.terminal and record.share_run_id is not None:
            # A supported read repairs a link a transient failure missed. The repair uses the
            # persisted Candidate result too, so an approval that committed but failed to book
            # its conclusion recovers with the exact address. Replay is a D no-op.
            await self._reconcile_from_persisted(run, record)
        return run

    async def _reconcile_from_persisted(self, run: DreamRun, record: DreamRecord) -> None:
        """Repair this run's conclusion from durable Dream and Candidate state alone."""

        committed: ArtifactRef | None = None
        candidate_id = None if run.candidate is None else run.candidate.candidate_id
        if candidate_id is not None:
            try:
                async with self.database.connection() as connection:
                    candidate = await self._candidate_lookup(connection, run.scope_id, candidate_id)
            except Exception:
                candidate = None
            if candidate is not None and candidate.result_artifact is not None:
                committed = candidate.result_artifact
        try:
            await self.reconcile_share_conclusion(run, committed=committed, candidate_id=candidate_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log_safely(
                logger,
                logging.WARNING,
                "Share run conclusion reconciliation failed",
                extra={
                    "event": "dream.share_conclusion.reconcile_failed",
                    "operation": run.operation,
                    "unit": "stage",
                    "outcome": "failure",
                    "scope": run.scope_id,
                    "exception_type": type(error).__name__,
                },
            )

    async def _candidate_lookup(self, connection, scope_id: str, candidate_id: str):
        """Read a Dream Candidate's persisted result through the owning repository."""

        review = self.review(scope_id, connection)
        return await review.get_candidate(candidate_id, connection=connection)

    async def list(self, scope_id: str, principal_id: str, request: ListDreamRunsRequest) -> DreamRunPage:
        await self._authorize(scope_id, principal_id, "read")
        async with self._transaction() as connection:
            return await self.repository.list(connection, scope_id, request)

    async def execute(self) -> bool:
        """Execute one accepted Run attempt within a Supervisor Scope invocation.

        Returning false leaves a non-Dream invocation to the Family processor.
        A completed attempt acknowledges this invocation and durably schedules
        any remaining Runs, even when automatic processing is disabled.
        """

        if self.processing is None:
            raise RuntimeError("Dream execution requires a Supervisor invocation")  # noqa: TRY003
        work = self.processing.assignment
        operations = operations_for_binding(work.binding_name)
        if not operations:
            raise ValueError("Dream operation and processing binding do not match")  # noqa: TRY003
        async with self._transaction() as connection:
            await self.processing.start(connection)
            record = await self.repository.next_pending(
                connection,
                work.scope_id,
                operations,
                through_generation=work.claimed_request_generation,
                binding=work.binding_name,
            )
            if record is None:
                return False
            intent = await self.processing.guard(connection)
            yield_to_sources = (
                intent.consecutive_dream_attempts >= 4
                and intent.dirty_generation > intent.clean_generation
                and work.artifact_family not in {"skill", "handoff", "prompt"}
            )
            if yield_to_sources and work.artifact_family == "topic-memory":
                from powercontext.builtin.runtime.topic_memory_scope import topic_memory_processing_block

                # Fairness only yields to runnable Source work, not a terminal window.
                yield_to_sources = (
                    await topic_memory_processing_block(connection, work.scope_id, work.binding_name) is None
                )
            if yield_to_sources:
                # The Source pass acknowledges this invocation. Keep the queued
                # Dream runnable even if that pass consumes all ordinary input.
                if intent.requested_generation == work.claimed_request_generation:
                    await self.intents.request(connection, work.scope_id, work.binding_name)
                return False
            record = await self.repository.claim(
                connection, record, model_config_id=None if self.generator is None else self.generator.config_id
            )
            if record.run.terminal:
                await self._complete_invocation(connection)
                return True
        # The recorded executor is bound for the whole attempt: the audit context and
        # every recheck in this execution must see the identity admission authorized.
        token = None
        if record.share_run_id is not None and self.bind_executor is not None:
            token = self.bind_executor(record.principal_id)
        try:
            await self._execute_attempt(record)
        finally:
            if token is not None and self.clear_executor is not None:
                self.clear_executor(token)
        return True

    async def _execute_attempt(self, record: DreamRecord) -> None:
        deadline: asyncio.Timeout | None = None
        try:
            async with self._transaction() as connection:
                remaining = _remaining_seconds(record, await database_now(connection))
            deadline = asyncio.timeout(max(remaining, 0))
            async with deadline:
                await self._execute(record)
        except (ArtifactProcessingLeadershipLostError, InvocationAlreadyHandled):
            raise
        except (InferenceTimeoutError, InferenceUnavailableError) as error:
            self._log_attempt_failure(record, error)
            await self._fail_or_retry(record, _error_code(error), retry=True)
        except Exception as error:
            if isinstance(error, TimeoutError) and deadline is not None and deadline.expired():
                self._log_attempt_failure(record, error, classification_error=DreamError("budget_exceeded"))
                await self._fail_or_retry(record, "budget_exceeded", retry=False)
            else:
                self._log_attempt_failure(record, error)
                await self._fail_or_retry(record, _error_code(error), retry=False)

    def _log_attempt_failure(
        self,
        record: DreamRecord,
        error: Exception,
        *,
        classification_error: Exception | None = None,
    ) -> None:
        work = self.processing
        diagnostic_error = error if classification_error is None else classification_error
        log_safely(
            logger,
            logging.WARNING,
            "Dream processing attempt failed",
            extra={
                "event": "dream.attempt.failed",
                "operation": record.run.operation,
                "unit": "stage",
                "outcome": "failure",
                "family": operation_spec(record.run.operation).processing_family,
                "scope": record.run.scope_id,
                "binding": None if work is None else work.assignment.binding_name,
                "worker_id": None if work is None else work.assignment.worker_id,
                "request_generation": None if work is None else work.assignment.claimed_request_generation,
                "exception_type": type(error).__name__,
                "error_code": processing_error_code(type(error)),
                **safe_log_fields(safe_classify_processing_error(diagnostic_error)),
            },
        )

    async def _complete_invocation(self, connection: AsyncConnection) -> None:
        if self.processing is None:
            raise RuntimeError("Dream execution requires a Supervisor invocation")  # noqa: TRY003
        work = self.processing.assignment
        current = await self.processing.guard(connection)
        remaining = await self.repository.next_pending(
            connection,
            work.scope_id,
            operations_for_binding(work.binding_name),
            binding=work.binding_name,
        )
        if remaining is not None and current.requested_generation == work.claimed_request_generation:
            await self.intents.request(connection, work.scope_id, work.binding_name)
        # Dream never consumes or clears the Family's ordinary Source progress.
        await self.processing.complete(connection, remaining_work=True, dream=True)

    async def _execute(self, record: DreamRecord) -> None:  # noqa: C901 - bounded execution and audit checks
        run = record.run
        if record.request is None:
            raise DreamError("dream_request_unavailable")
        if (
            self.generator is None
            or self.generator.config_id != run.model_config_id
            or run.prompt_version != DREAM_PROMPT_VERSION
        ):
            raise DreamError("capability_unavailable")
        await self._authorize(run.scope_id, record.principal_id, "contribute")
        prompt_definition = None
        if run.operation == "revise_prompt" and run.target is not None:
            prompt_definition = await self.review(run.scope_id, None).require_prompt_target(run.target)
        before_tags = ()
        if record.request.tag_target is not None:
            before_tags = (await self._catalog(run.scope_id, None).inspect_target(record.request.tag_target)).tags
        await self._preflight_target(record)
        await self._preflight_shared_use(record)
        resolved = await self._resolve(record)
        record = record.model_copy(
            update={
                "proposal_fingerprint": proposal_fingerprint(record, resolved),
                "run": run.model_copy(update={"input_manifest": resolved.manifest}),
            }
        )
        if await self._reuse_before_generation(record):
            return
        usage = run.usage.model_copy(update={"model_calls": run.usage.model_calls + 1})
        if usage.model_calls > run.budget.max_model_calls:
            raise DreamError("budget_exceeded")
        record = record.model_copy(
            update={
                "run": run.model_copy(update={"input_manifest": resolved.manifest, "usage": usage}),
            }
        )
        async with self._transaction() as connection:
            await self.repository.checkpoint(connection, record)
        if record.request is None:
            raise DreamError("dream_request_unavailable")
        await self._preflight_shared_use(record)
        generated = await self.generator.generate(
            DreamGenerationInput(
                operation=run.operation,
                target_evidence_id=_target_evidence_id(record.request),
                evidence=resolved.projection,
                profile_policy=record.profile_policy,
                prompt_definition=prompt_definition,
                tag_target=record.request.tag_target,
                before_tags=before_tags,
            )
        )
        record = record.model_copy(
            update={
                "run": record.run.model_copy(
                    update={
                        "usage": _usage(record.run.usage, generated.usage),
                    }
                )
            }
        )
        async with self._transaction() as connection:
            await self.repository.checkpoint(connection, record)
        if generated.usage.requests > 1 or (
            generated.usage.output_tokens is not None and generated.usage.output_tokens > run.budget.max_output_tokens
        ):
            raise DreamError("budget_exceeded")
        if record.request is None:
            raise DreamError("dream_request_unavailable")
        operation_spec(run.operation).validator(generated.output, record.request)
        plan = _supported_plan(record, generated.output, resolved)
        if isinstance(plan.proposal, TagChangeProposal) and plan.proposal.after_tags == before_tags:
            plan = DreamPlan(outcome="no_change", reason="The proposed labels already match the current tag set.")
        if isinstance(plan.proposal, SkillContent):
            if run.operation == "revise_skill" and run.target is not None:
                prepared = await self.review(run.scope_id, None).prepare_skill_revision(run.target, plan.proposal)
            else:
                prepared = await self.review(run.scope_id, None).prepare_skill(plan.proposal)
            plan = plan.model_copy(update={"proposal": prepared})
        await self._commit(record, plan)

    async def _preflight_shared_use(self, record: DreamRecord) -> None:
        """Recheck the pinned agreement before background work reads an origin body."""

        if record.share_run_id is None or record.request is None:
            if any(item.scope_id != record.run.scope_id for item in (record.request.scoped if record.request else ())):
                # A foreign claim with no persisted binding can never execute.
                raise DreamError("unsupported_shared_use")
            return
        await self._require_shared_use(
            record.run.scope_id,
            record.principal_id,
            record.request,
            record.share_run_id,
            record,
        )

    async def _preflight_target(self, record: DreamRecord) -> None:
        run = record.run
        if run.operation == "revise_skill" and run.target is not None:
            try:
                await self.review(run.scope_id, None).require_instruction_skill_target(run.target)
            except InvalidCandidateError as error:
                if error.detail == "unsupported_target":
                    raise DreamError("unsupported_target") from error
                raise
        if run.operation in {"revise_profile", "initialize_profile"}:
            # Both need the Scope policy; a policy-less Scope is refused before the billed
            # generation attempt rather than after it.
            async with self._transaction() as connection:
                await self._check_profile_policy(connection, record)

    async def _resolve(self, record: DreamRecord, connection: AsyncConnection | None = None) -> ResolvedEvidence:
        request = record.request
        if request is None:
            raise DreamError("dream_request_unavailable")
        # Tag basis inspection acquires a write lock even during generation.
        # Flush access audit writes after this transaction, including standalone resolution.
        async with self.authorization_context(), self.database.connection(connection) as bound:
            await self._check_target(bound, record.run.scope_id, record.principal_id, request.target)

            if request.tag_target is not None:
                await self._check_tag_target(bound, record.run.scope_id, record.principal_id, request)
            resolver = self._resolver(record.run.scope_id, record.principal_id)
            resolver.limits = record.run.budget
            artifacts, target = _resolve_references(request)
            return await resolver.resolve(
                bound,
                sources=request.sources,
                artifacts=artifacts,
                scoped=request.scoped,
                target=target,
                include_memory_text=request.operation != "derive_skill",
                pinned=record.run.input_manifest,
                lock_memory=connection is not None,
                expose_prompt_target=request.operation == "revise_prompt",
                expose_catalog_target=request.operation == "revise_tags",
            )

    async def _commit(self, record: DreamRecord, plan: DreamPlan) -> None:
        run = record.run
        async with self._transaction() as connection:
            await self.repository.lock_owned(connection, record)
            await self._authorize(run.scope_id, record.principal_id, "contribute")
            if run.operation in {"revise_profile", "initialize_profile"}:
                await self._check_profile_policy(connection, record)
            await self._preflight_shared_use(record)
            resolved = await self._resolve(record, connection)
            candidate = None
            if plan.outcome == "proposed":
                if self.attest_candidate is not None and run.operation != "revise_tags":
                    # The owning operation spec already declares the Family, so a new
                    # operation cannot be left out of attestation the way a copied map
                    # would allow.
                    family = operation_spec(run.operation).family
                    if family is None:
                        raise DreamError("capability_unavailable")
                    await self.attest_candidate(connection, record, _candidate_id(record), family)
                candidate = await self._propose(connection, record, plan, resolved)
            completed = run.model_copy(
                update={
                    "status": "succeeded",
                    "outcome": plan.outcome,
                    "reason": plan.reason,
                    "candidate": candidate,
                    "completed_at": await database_now(connection),
                }
            )
            await self.repository.finish(connection, record, completed)
            await self._complete_invocation(connection)
        await self._record_share_conclusion(completed)

    async def _record_share_conclusion(self, run: DreamRun) -> None:
        """Link this finished run to the pinned evaluation it was admitted under.

        Only outcomes D already defines are recorded: a proposal stays pending until
        approval establishes the official revision, and a failure is not concluded.
        Runs without a binding (local, legacy) are untouched.
        """

        # Everything below runs after the owning write is durable, so a failure here is
        # reported and repaired later instead of changing this run's result.
        try:
            await self.reconcile_share_conclusion(run)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log_safely(
                logger,
                logging.WARNING,
                "Share run conclusion reconciliation failed",
                extra={
                    "event": "dream.share_conclusion.reconcile_failed",
                    "operation": run.operation,
                    "unit": "stage",
                    "outcome": "failure",
                    "scope": run.scope_id,
                    "exception_type": type(error).__name__,
                },
            )

    async def reconcile_share_conclusion(
        self,
        run: DreamRun,
        *,
        committed: ArtifactRef | None = None,
        candidate_id: str | None = None,
    ) -> ShareRun | None:
        """Record or repair this run's conclusion from its persisted authoritative state.

        Safe to call repeatedly: D's one-way and idempotent rules make a second identical
        attempt a no-op, so a supported read of a finished run repairs a link a transient
        failure missed. It never generates, never approves, and never changes the result.
        """

        if not run.scope_id or self.sharing is None:
            return None
        async with self.database.connection() as connection:
            record = await self.repository.get(connection, run.scope_id, run.run_id)
        if record.share_run_id is None:
            return None
        service = self.sharing()
        if service is None:
            return None
        resolved_candidate = candidate_id
        if resolved_candidate is None and run.candidate is not None:
            resolved_candidate = run.candidate.candidate_id
        token = None
        if self.bind_executor is not None:
            # Background work and reconciled reads have no session; the recorded executor
            # is the identity D's target check must see.
            token = self.bind_executor(record.principal_id)
        try:
            async with self.authorization_context():
                stored = await record_conclusion(
                    service,
                    scope_id=run.scope_id,
                    share_run_id=record.share_run_id,
                    dream_run_id=run.run_id,
                    candidate_id=resolved_candidate,
                    outcome=conclusion_for(run.outcome),
                    committed=committed,
                    operation=run.operation,
                )
            if stored is not None and stored.dream_run_id not in {None, run.run_id}:
                # D has no atomic claim yet, so verify the binding it now holds and report a
                # different owner instead of treating this execution as concluded.
                raise DreamError("shared_use_run")
            return stored
        finally:
            if token is not None and self.clear_executor is not None:
                self.clear_executor(token)

    async def _require_shared_use(
        self,
        scope_id: str,
        principal_id: str,
        request: CreateDreamRunRequest,
        share_run_id: str,
        record: DreamRecord | None = None,
    ) -> None:
        """Ask Access whether this run may use the origin material it claims.

        The hook loads the durable rule, run and acceptances by id; this method only
        supplies the run identity and the request it must match, and refuses when no
        hook is configured. Nothing here grants, caches or synthesizes consent.
        """

        if self.check_run_use is None:
            raise DreamError("unsupported_shared_use")
        run = (
            record.run
            if record is not None
            else DreamRun(
                scope_id=scope_id,
                run_id="",
                operation=request.operation,
                target=request.target,
                accepted_at=datetime.now(UTC),
            )
        )
        binding = DreamRecord(
            run=run.model_copy(update={"share_run_id": share_run_id}),
            request=request,
            principal_id=principal_id,
        )
        await self.check_run_use(share_run_id, binding.run, binding)

    async def _authorize_scoped_origins(self, scope_id: str, principal_id: str, request: CreateDreamRunRequest) -> None:
        """Check the executing principal's current authority at each selected origin."""

        for selection in request.scoped:
            if selection.scope_id == scope_id:
                continue
            await self._authorize(selection.scope_id, principal_id, "read", selection.reference)

    async def _require_no_profile(self, connection: AsyncConnection, scope_id: str) -> None:
        """Refuse initialization of a Scope that already has a committed Profile."""

        try:
            await self.artifacts.latest(connection, scope_id, "profile", PROFILE_ARTIFACT_ID)
        except RepositoryNotFoundError:
            return
        raise DreamError("profile_exists")

    async def _check_profile_policy(self, connection: AsyncConnection, record: DreamRecord) -> None:
        policy = await ProfilePolicyRepository().get(connection, record.run.scope_id, for_update=True)
        if record.profile_policy is None or policy is None:
            raise DreamError("capability_unavailable")
        if ProfileDreamPolicy.from_policy(policy).digest != record.profile_policy.digest:
            raise DreamError("policy_changed")

    async def _propose(  # noqa: C901 - operation-specific Candidate adapters share one atomic Run commit
        self,
        connection: AsyncConnection,
        record: DreamRecord,
        plan: DreamPlan,
        resolved: ResolvedEvidence,
    ) -> DreamCandidateRef:
        if record.request is None:
            raise DreamError("dream_request_unavailable")
        selected = select_evidence(
            resolved.manifest,
            plan.evidence_ids,
            skill=record.run.operation == "derive_skill",
            target=_resolve_references(record.request)[1],
            catalog=record.run.operation == "revise_tags",
            # Only a run bound to an admitted agreement may cite another Scope's node.
            admitted_origins=record.share_run_id is not None,
        )
        if isinstance(plan.proposal, TagChangeProposal) and record.request.tag_target is not None:
            service = self._catalog(record.run.scope_id, connection)
            current = await service.inspect_target(record.request.tag_target)
            proposal = CatalogChangeProposal(
                **record.request.tag_target.model_dump(mode="python"),
                before_tags=current.tags,
                after_tags=plan.proposal.after_tags,
            )
            candidate = await service.propose(
                proposal,
                reason=plan.reason,
                sources=selected.sources,
                artifacts=selected.artifacts,
                dream_run_id=record.run.run_id,
                candidate_id=_candidate_id(record),
                audit=candidate_audit(record, proposal),
            )
            return DreamCandidateRef(candidate_id=candidate.candidate_id, version=candidate.version, kind="tag")
        review = self.review(record.run.scope_id, connection)
        if record.share_run_id is not None:
            # Foreign citations are validated against the exact origins and digests this
            # run actually admitted, taken from the resolved manifest -- never against the
            # run Scope's own Journal and never with a synthesized digest.
            admitted = tuple(
                RecordedOrigin(
                    scope_id=node.origin_scope_id,
                    source=node.source,
                    artifact=node.artifact,
                    digest=node.digest,
                )
                for node in resolved.manifest.nodes
                if node.origin_scope_id is not None and (node.source is not None or node.artifact is not None)
            )
            review.admit_foreign_origins(admitted)
        if isinstance(plan.proposal, ExperienceContent):
            candidate = await review.propose_experience(
                plan.proposal,
                sources=selected.sources,
                artifacts=selected.artifacts,
                target=record.run.target,
                reason=plan.reason,
                candidate_id=_candidate_id(record),
            )
        elif isinstance(plan.proposal, SkillContent):
            if record.run.operation == "derive_skill":
                validate_skill_lineage(SkillGenerationOrigin.EXPERIENCE, selected.sources, selected.artifacts, None)
            candidate = await review.propose_skill(
                plan.proposal,
                sources=selected.sources,
                artifacts=selected.artifacts,
                target=record.run.target,
                reason=plan.reason,
                candidate_id=_candidate_id(record),
            )
        elif isinstance(plan.proposal, ProfileWriteContent) and record.run.operation in {
            "revise_profile",
            "initialize_profile",
        }:
            await self._check_profile_policy(connection, record)
            policy = record.profile_policy
            if policy is None or self.generator is None:
                raise DreamError("capability_unavailable")
            if record.run.operation == "revise_profile" and record.run.target is None:
                raise DreamError("capability_unavailable")
            proposal = ProfileCandidateProposal(
                content=plan.proposal.content,
                dream_run_id=record.run.run_id,
                policy_snapshot=policy,
                policy_digest=policy.digest,
                generator_id=self.generator.config_id,
                generator_version=DREAM_PROMPT_VERSION,
                created_at=await database_now(connection),
            )
            candidate = await review.propose_profile_dream(
                proposal,
                sources=selected.sources,
                artifacts=selected.artifacts,
                target=record.run.target,
                reason=plan.reason,
                candidate_id=_candidate_id(record),
                origins=_recorded_origins(resolved),
            )
        elif isinstance(plan.proposal, AtomicMemoryContent) and record.run.operation == "revise_memory":
            if record.run.target is None:
                raise DreamError("invalid_generation_output")
            proposal = plan.proposal
            candidate = await review.propose_memory_dream(
                proposal,
                sources=selected.sources,
                artifacts=selected.artifacts,
                target=record.run.target,
                reason=plan.reason,
                candidate_id=_candidate_id(record),
            )
        elif isinstance(plan.proposal, TopicMemoryContent) and record.run.operation == "revise_topic_memory":
            if record.run.target is None:
                raise DreamError("invalid_generation_output")
            candidate = await review.propose_topic_memory_dream(
                plan.proposal,
                sources=selected.sources,
                artifacts=selected.artifacts,
                target=record.run.target,
                reason=plan.reason,
                candidate_id=_candidate_id(record),
            )
        elif isinstance(plan.proposal, HandoffContent) and record.run.operation == "refresh_handoff":
            if record.run.target is None:
                raise DreamError("invalid_generation_output")
            candidate = await review.propose_handoff_dream(
                plan.proposal,
                sources=selected.sources,
                artifacts=selected.artifacts,
                target=record.run.target,
                reason=plan.reason,
                candidate_id=_candidate_id(record),
            )
        elif isinstance(plan.proposal, PromptContent) and record.run.operation == "revise_prompt":
            if record.run.target is None:
                raise DreamError("invalid_generation_output")
            candidate = await review.propose_prompt_dream(
                plan.proposal,
                sources=selected.sources,
                artifacts=selected.artifacts,
                target=record.run.target,
                reason=plan.reason,
                candidate_id=_candidate_id(record),
            )
        else:
            raise DreamError("invalid_generation_output")
        await attach_candidate_audit(
            connection, record.run.scope_id, candidate, candidate_audit(record, candidate.proposal)
        )
        return DreamCandidateRef(candidate_id=candidate.candidate_id, version=candidate.version)

    async def _reuse_before_generation(self, record: DreamRecord) -> bool:
        async with self._transaction() as connection:
            await self.repository.lock_owned(connection, record)
            if record.run.operation == "revise_profile":
                await self._check_profile_policy(connection, record)
            resolved = await self._resolve(record, connection)
            for previous in await self.repository.matching_proposals(connection, record):
                ref = previous.run.candidate
                if ref is None:
                    continue
                table = CANDIDATE_HEADS_TABLE
                await connection.execute(
                    update(table)
                    .where(
                        table.c.scope_id == record.run.scope_id,
                        table.c.candidate_id == ref.candidate_id,
                    )
                    .values(version=table.c.version)
                )
                if ref.kind == "tag":
                    candidate = await self._catalog(record.run.scope_id, connection).get(ref.candidate_id, current=True)
                else:
                    candidate = await self.review(record.run.scope_id, connection).get_candidate(
                        ref.candidate_id, current=True
                    )
                audit = candidate.audit
                if (
                    audit is None
                    or audit.proposal_fingerprint != record.proposal_fingerprint
                    or audit.proposal_digest != proposal_digest(candidate.proposal)
                    or candidate.status == CandidateStatus.APPROVED
                    or not _candidate_matches_evidence(candidate, record, resolved)
                ):
                    continue
                if self.authorize_candidate is not None:
                    family = (
                        candidate.proposal.target.family
                        if isinstance(candidate, CatalogChangeCandidate)
                        else candidate.family
                    )
                    await self.authorize_candidate(connection, record, candidate.candidate_id, family)
                completed = record.run.model_copy(
                    update={
                        "status": "succeeded",
                        "outcome": "proposed" if candidate.status == CandidateStatus.PENDING else "no_change",
                        "reason": "Reused pending proposal for the same target, evidence and policy."
                        if candidate.status == CandidateStatus.PENDING
                        else "An unchanged proposal was already rejected; new evidence or a changed target is required.",
                        "candidate": DreamCandidateRef(
                            candidate_id=candidate.candidate_id, version=candidate.version, kind=ref.kind
                        ),
                        "reused": True,
                        "completed_at": await database_now(connection),
                    }
                )
                await self.repository.finish(connection, record, completed)
                await self._complete_invocation(connection)
                return True
        return False

    def _catalog(self, scope_id: str, connection: AsyncConnection | None) -> CatalogChangeService:
        if self.catalog_changes is None:
            raise DreamError("capability_unavailable")
        return self.catalog_changes(scope_id, connection)

    async def _check_tag_target(
        self, connection: AsyncConnection, scope_id: str, principal_id: str, request: CreateDreamRunRequest
    ) -> None:
        target = request.tag_target
        if target is None:
            raise DreamError("invalid_target")
        await self._authorize(scope_id, principal_id, "write_tags", target.basis_ref)
        await self._catalog(scope_id, connection).inspect_target(target)

    async def _check_target(
        self,
        connection: AsyncConnection,
        scope_id: str,
        principal_id: str,
        target: ArtifactRef | None,
    ) -> None:
        if target is None:
            return
        await self._authorize(scope_id, principal_id, "write", target)
        current = await self.artifacts.latest(
            connection,
            scope_id,
            target.family,
            target.artifact_id,
            for_update=True,
        )
        if current.as_ref() != target:
            raise DreamError("artifact_conflict")

    async def _fail_or_retry(self, record: DreamRecord, code: str, *, retry: bool) -> None:
        if code == "attempt_conflict":
            return
        try:
            async with self._transaction() as connection:
                await self.repository.lock_owned(connection, record)
                current = await self.repository.get(connection, record.run.scope_id, record.run.run_id, current=True)
                now = await database_now(connection)
                can_retry = (
                    retry
                    and current.run.attempt_count < current.run.budget.max_model_calls
                    and current.deadline_at is not None
                    and now < current.deadline_at
                )
                if can_retry:
                    queued = current.model_copy(
                        update={
                            "run": current.run.model_copy(update={"status": "queued"}),
                        }
                    )
                    await self.repository.checkpoint(connection, queued)
                else:
                    failed = current.run.model_copy(update={"status": "failed", "error": code, "completed_at": now})
                    await self.repository.finish(connection, current, failed)
                await self._complete_invocation(connection)
        except DreamError as error:
            if error.code != "attempt_conflict":
                raise


def _require_recordable_origins(scope_id: str, request: CreateDreamRunRequest) -> None:
    """Refuse another Scope's material until an agreed use can be committed with it.

    Read authority at the origin, a structural reference, an equal identity or a
    matching digest does not agree to this use, and Candidate evidence plus Artifact
    lineage still hold only Scope-local references. Admitting such a run would either
    project another Scope's body to a model without agreement (billed before any late
    refusal) or commit a conclusion whose provenance omits a body that shaped it, so
    the mode is refused here instead of after generation. The resolver keeps its own
    per-origin use check for the slice that wires agreed use and origin recording.
    """

    if not any(selection.scope_id != scope_id for selection in request.scoped):
        return
    # Another Scope's material needs an operation that admits it and a durable pinned
    # agreement; the agreement itself is checked against Access in the service.
    require_foreign_operation(request.operation)
    if request.share_run_id is None:
        raise DreamError("unsupported_shared_use")


def _recorded_origins(resolved: ResolvedEvidence) -> tuple[RecordedOrigin, ...]:
    """The exact foreign origins this run admitted, for the Candidate and its lineage."""

    return tuple(
        RecordedOrigin(
            scope_id=node.origin_scope_id,
            source=node.source,
            artifact=node.artifact,
            digest=node.digest,
        )
        for node in resolved.manifest.nodes
        if node.origin_scope_id is not None and (node.source is not None or node.artifact is not None)
    )


def _candidate_id(record: DreamRecord) -> str:
    return "cand_dream_" + record.run.run_id


def _candidate_matches_evidence(
    candidate: Candidate[Any] | CatalogChangeCandidate, record: DreamRecord, resolved: ResolvedEvidence
) -> bool:
    """A shared root alone cannot authorize stale or differently selected evidence."""
    if record.request is None:
        return False
    if isinstance(candidate, CatalogChangeCandidate):
        requested = record.request.tag_target
        if requested is None or any(
            getattr(candidate.proposal, field) != getattr(requested, field)
            for field in ("target", "expected_etag", "basis_ref")
        ):
            return False
    elif candidate.target != record.run.target:
        return False
    available = set()
    for node in resolved.manifest.nodes:
        if node.source is not None:
            available.add(reference_key(node.source))
        if node.artifact is not None:
            available.add(reference_key(node.artifact))
    return all(reference_key(reference) in available for reference in (*candidate.sources, *candidate.artifacts))


def _resolve_references(request: CreateDreamRunRequest):
    target = request.tag_target
    if target is None:
        return request.artifacts, request.target
    return unique_references((*request.artifacts, target.basis_ref)), target.basis_ref


def _target_evidence_id(request: CreateDreamRunRequest) -> str | None:
    target = request.target
    if request.tag_target is not None:
        target = request.tag_target.basis_ref
    return None if target is None else evidence_id(target)


def _supported_plan(record: DreamRecord, plan: DreamPlan, resolved: ResolvedEvidence) -> DreamPlan:
    if record.request is None:
        raise DreamError("dream_request_unavailable")
    if plan.outcome != "proposed":
        return plan
    try:
        select_evidence(
            resolved.manifest,
            plan.evidence_ids,
            skill=record.run.operation == "derive_skill",
            target=_resolve_references(record.request)[1],
            catalog=record.run.operation == "revise_tags",
            # Only a run bound to an admitted agreement may cite another Scope's node.
            admitted_origins=record.share_run_id is not None,
        )
    except EvidenceResolutionError as error:
        if error.code != "needs_evidence":
            raise
        return DreamPlan(
            outcome="needs_evidence",
            reason="The selected evidence lacks independent support beyond the target or usable root Sources for Memory-derived claims.",
        )
    return plan


def _remaining_seconds(record: DreamRecord, now: datetime) -> float:
    if record.deadline_at is None:
        raise DreamError("budget_exceeded")
    return (record.deadline_at - now).total_seconds()


def _usage(previous: DreamUsage, current: InferenceUsage) -> DreamUsage:
    first = previous.model_calls == 1
    return DreamUsage(
        model_calls=previous.model_calls + max(0, current.requests - 1),
        input_tokens=_add_tokens(previous.input_tokens, current.input_tokens, first=first),
        output_tokens=_add_tokens(previous.output_tokens, current.output_tokens, first=first),
    )


def _add_tokens(previous: int | None, current: int | None, *, first: bool) -> int | None:
    if current is None or (not first and previous is None):
        return None
    return (previous or 0) + current


def _error_code(error: Exception) -> str:
    if isinstance(error, EvidenceResolutionError) and error.code in {"invalid_memory_citation", "reference_not_found"}:
        return "evidence_unavailable"
    if isinstance(error, (DreamError, EvidenceResolutionError)):
        return error.code
    if isinstance(error, ArtifactTargetConflictError):
        return "artifact_conflict"
    if isinstance(error, TagPreconditionError):
        return "tag_precondition_failed"
    if isinstance(error, (InvalidInferenceOutputError, InvalidCandidateError)):
        return "invalid_generation_output"
    return "generation_failed"
