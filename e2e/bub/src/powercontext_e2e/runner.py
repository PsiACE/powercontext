"""Run every end-to-end workload through Harbor, ACP, and Bub."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from uuid import uuid4

from harbor.job import Job
from harbor.models.environment_type import EnvironmentType
from harbor.models.job.config import JobConfig
from harbor.models.trial.config import AgentConfig, EnvironmentConfig, ResourceMode, ServiceVolumeConfig
from powercontext.client import PowerContextClient
from powercontext.http import (
    ApproveArtifactCandidateRequest,
    ExperienceProposal,
    ListMemoryEntriesRequest,
    PrepareContextRequest,
    ProposeExperienceRequest,
)

from .evidence import load_resolved_instructions, redact, write_evaluation_report, write_evidence
from .models import (
    ApprovedExperienceObservation,
    CaptureRecord,
    CaseEvaluation,
    E2ETask,
    EvaluationReport,
    EvaluationValue,
    HarborTrialObservation,
    MemoryEntrySnapshot,
    MemorySnapshot,
    NativeArtifact,
    PreparedContextSnapshot,
    RecallProbeObservation,
    ResolvedInstruction,
    RunEnvironment,
    SourceReferenceSnapshot,
    TaskObservation,
    WorkloadSetupObservation,
    fingerprint,
)
from .report import render_report
from .settings import HarnessSettings

CAPTURE_EVENTS = frozenset({"user_prompt", "llm_result", "tool_result"})


class CodexAuthNotFoundError(RuntimeError):
    """Report missing local Codex OAuth credentials."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"The selected e2e workload requires Codex OAuth credentials at {path}")


@dataclass(frozen=True, slots=True)
class MemoryEvaluator:
    """Evaluate one workload through the common Memory acceptance contract."""

    def evaluate(self, task: E2ETask, observation: TaskObservation, *, experiment: str) -> EvaluationReport:
        evaluation = task.evaluation
        eligible_records = [record for record in observation.capture_records if record.event in CAPTURE_EVENTS]
        captured_records = [record for record in eligible_records if record.status == "captured"]
        capture_coverage = len(captured_records) / len(eligible_records) if eligible_records else 0.0

        memory_before_ids = {entry.entry_id for entry in observation.memory_before.entries}
        new_memory = [entry for entry in observation.memory_after.entries if entry.entry_id not in memory_before_ids]
        captured_source_ids = {record.source_id for record in captured_records if record.source_id is not None}
        grounded_memory = [
            entry
            for entry in new_memory
            if entry.source_refs and all(source.source_id in captured_source_ids for source in entry.source_refs)
        ]
        groundedness = len(grounded_memory) / len(new_memory) if new_memory else 0.0

        probes_by_id = {probe.id: probe for probe in observation.probes}
        supported_probes = [
            probe_spec
            for probe_spec in evaluation.probes
            if (probe := probes_by_id.get(probe_spec.id)) is not None
            and probe.prepared_context.status == "ready"
            and bool(probe.prepared_context.content.strip())
            and _contains_fragments(probe.prepared_context.content, probe_spec.expected_context)
        ]
        probe_coverage = len(supported_probes) / len(evaluation.probes)
        in_run_contexts = sum(
            record.event == "context"
            and record.content_bytes is not None
            and record.content_bytes > 0
            and bool(record.captured_events)
            and bool(record.flushed_position)
            for record in observation.capture_records
        )
        completed_checkpoints = [
            record
            for record in observation.capture_records
            if record.event == "checkpoint"
            and record.status != "failed"
            and record.current_cursor is not None
            and record.target_position is not None
            and record.current_cursor >= record.target_position
        ]
        native_names = {Path(artifact.name).name for artifact in observation.native_artifacts}
        missing_native_artifacts = sorted(set(evaluation.required_native_artifacts) - native_names)
        summary_artifacts = {
            artifact.name for artifact in observation.native_artifacts if Path(artifact.name).name == "acp-summary.json"
        }
        instruction_artifacts = {instruction.artifact for instruction in observation.resolved_instructions}
        instructions_recorded = bool(summary_artifacts) and instruction_artifacts == summary_artifacts
        expected_experience_ids = tuple(experience.id for experience in task.setup.approved_experiences)
        observed_experience_ids = tuple(experience.id for experience in observation.setup.approved_experiences)
        setup_completed = expected_experience_ids == observed_experience_ids
        expected_memory_found = all(
            any(fragment.casefold() in entry.text.casefold() for entry in observation.memory_after.entries)
            for fragment in evaluation.expected_memory
        )
        thresholds = evaluation.thresholds

        attributes = {
            "commit": observation.environment.commit,
            "database": observation.environment.database,
            "dataset": task.dataset.name or str(task.dataset.path),
            "harbor_task_id": task.dataset.task_id,
            "run_id": observation.run_id,
            "workload_id": task.id,
        }
        metrics = {
            "capture_events": len(eligible_records),
            "captured_sources": len(captured_records),
            "completed_checkpoints": len(completed_checkpoints),
            "in_run_contexts": in_run_contexts,
            "resolved_instructions": len(observation.resolved_instructions),
            "approved_experiences": len(observation.setup.approved_experiences),
            "memory_entries_after": len(observation.memory_after.entries),
            "memory_entries_created": len(new_memory),
            "recall_probes_supported": len(supported_probes),
        }

        assertions = {
            "collection_completed": EvaluationValue(
                value=observation.status == "completed",
                reason=None if observation.status == "completed" else "; ".join(observation.errors),
            ),
            "task_provenance_matches": EvaluationValue(
                value=observation.harbor.task_checksum == task.dataset.checksum,
                reason=f"Expected {task.dataset.checksum!r}; observed {observation.harbor.task_checksum!r}.",
            ),
            "native_acp_evidence_recorded": EvaluationValue(
                value=not missing_native_artifacts,
                reason=(
                    "All required native ACP artifacts were recorded."
                    if not missing_native_artifacts
                    else f"Missing native ACP artifacts: {missing_native_artifacts!r}."
                ),
            ),
            "resolved_instructions_recorded": EvaluationValue(
                value=instructions_recorded,
                reason=(
                    f"Resolved {len(instruction_artifacts)} instructions from {len(summary_artifacts)} ACP summaries."
                ),
            ),
            "workload_setup_completed": EvaluationValue(
                value=setup_completed,
                reason=(
                    f"Prepared {len(observed_experience_ids)} of {len(expected_experience_ids)} declared Experiences."
                ),
            ),
            "capture_coverage_accepted": EvaluationValue(
                value=capture_coverage >= thresholds.capture_coverage,
                reason=f"Observed {capture_coverage:.3f}; required {thresholds.capture_coverage:.3f}.",
            ),
            "memory_created_during_run": EvaluationValue(
                value=bool(new_memory) and (not evaluation.require_checkpoint or bool(completed_checkpoints)),
                reason=f"Created {len(new_memory)} Memory entries and completed {len(completed_checkpoints)} checkpoints.",
            ),
            "memory_grounded": EvaluationValue(
                value=groundedness >= thresholds.groundedness,
                reason=f"Observed {groundedness:.3f}; required {thresholds.groundedness:.3f}.",
            ),
            "recall_probes_supported": EvaluationValue(
                value=probe_coverage >= thresholds.probe_coverage,
                reason=f"Observed {probe_coverage:.3f}; required {thresholds.probe_coverage:.3f}.",
            ),
            "memory_recalled_during_run": EvaluationValue(
                value=in_run_contexts >= thresholds.minimum_in_run_contexts,
                reason=f"Observed {in_run_contexts}; required {thresholds.minimum_in_run_contexts}.",
            ),
            "scope_started_empty": EvaluationValue(
                value=not observation.memory_before.entries,
                reason=f"Scope started with {len(observation.memory_before.entries)} Memory entries.",
            ),
        }
        if evaluation.expected_memory:
            assertions["expected_memory_recorded"] = EvaluationValue(
                value=expected_memory_found,
                reason=f"Expected Memory fragments: {list(evaluation.expected_memory)!r}.",
            )
        scores = {
            "capture_coverage": EvaluationValue(value=capture_coverage),
            "groundedness": EvaluationValue(value=groundedness),
            "probe_coverage": EvaluationValue(value=probe_coverage),
            **{
                f"harbor_reward_{name}": EvaluationValue(value=float(reward))
                for name, reward in sorted(observation.harbor.rewards.items())
            },
        }
        labels = {"task_outcome": EvaluationValue(value=_task_outcome(observation.harbor))}
        return EvaluationReport(
            experiment=experiment,
            cases=(
                CaseEvaluation(
                    name=task.id,
                    assertions=assertions,
                    scores=scores,
                    labels=labels,
                    metrics=metrics,
                    attributes=attributes,
                ),
            ),
        )


async def evaluate_task(
    task: E2ETask,
    *,
    output_dir: Path,
    settings: HarnessSettings | None = None,
) -> bool:
    settings = settings or HarnessSettings()
    observation = await run_task(task, output_dir=output_dir, settings=settings)
    report = MemoryEvaluator().evaluate(task, observation, experiment=f"e2e:{task.id}")
    write_artifacts(observation, report, output_dir, settings=settings)
    return report.accepted


async def rescore_replay(
    replay_path: Path,
    output_dir: Path,
    settings: HarnessSettings | None = None,
) -> bool:
    settings = settings or HarnessSettings()
    observation = TaskObservation.model_validate_json(replay_path.read_text(encoding="utf-8"))
    report = MemoryEvaluator().evaluate(observation.task, observation, experiment=f"offline:{observation.task.id}")
    write_artifacts(observation, report, output_dir, settings=settings)
    return report.accepted


async def run_task(task: E2ETask, *, output_dir: Path, settings: HarnessSettings) -> TaskObservation:
    started_at = datetime.now(UTC)
    run_id = f"{task.id}-{uuid4().hex[:12]}"
    scope_id = f"e2e:{run_id}"
    errors: list[str] = []
    capture_records: tuple[CaptureRecord, ...] = ()
    native_artifacts: tuple[NativeArtifact, ...] = ()
    resolved_instructions: tuple[ResolvedInstruction, ...] = ()
    setup_observation = WorkloadSetupObservation()
    harbor_observation = HarborTrialObservation()
    memory_before = MemorySnapshot()
    memory_after = MemorySnapshot()
    probes: tuple[RecallProbeObservation, ...] = ()
    host_url = settings.server_url(str(task.powercontext.host_url))

    try:
        async with PowerContextClient(host_url, timeout=30) as client:
            await client.get_readiness()
            setup_observation = await prepare_workload(client, scope_id, task)
            memory_before = await memory_snapshot(client, scope_id)

        output_dir.mkdir(parents=True, exist_ok=True)
        job = await Job.create(_job_config(task, run_id, scope_id, output_dir, settings))
        result = await job.run()
        harbor_observation, trial_dir = _harbor_observation(result, settings)
        if harbor_observation.exception_type is not None:
            errors.append(f"{harbor_observation.exception_type}: {harbor_observation.exception_message or ''}".strip())
        if trial_dir is not None:
            capture_records = _load_capture_records(trial_dir)
            native_artifacts = _native_artifacts(trial_dir)
            resolved_instructions = load_resolved_instructions(trial_dir, settings)

        async with PowerContextClient(host_url, timeout=30) as client:
            memory_after = await memory_snapshot(client, scope_id)
            probe_observations: list[RecallProbeObservation] = []
            for probe in task.evaluation.probes:
                probe_observations.append(
                    RecallProbeObservation(
                        id=probe.id,
                        query=probe.query,
                        prepared_context=await prepared_context(client, scope_id, probe.query),
                    )
                )
            probes = tuple(probe_observations)
    except Exception as exc:
        errors.append(redact(f"{type(exc).__name__}: {exc}", settings))
        async with PowerContextClient(host_url, timeout=30) as client:
            with suppress(Exception):
                memory_after = await memory_snapshot(client, scope_id)

    return TaskObservation(
        run_id=run_id,
        environment=RunEnvironment(
            commit=settings.commit_id(),
            database=settings.database,
            agent_model=task.agent.model,
            model_source=task.agent.model_source,
            generation_model=settings.generation_model,
            embedding_profile=settings.embedding_profile,
            started_at=started_at,
            finished_at=datetime.now(UTC),
        ),
        task=task,
        status="completed" if not errors else "failed",
        errors=tuple(errors),
        harbor=harbor_observation,
        capture_records=capture_records,
        native_artifacts=native_artifacts,
        resolved_instructions=resolved_instructions,
        setup=setup_observation,
        memory_before=memory_before,
        memory_after=memory_after,
        probes=probes,
    )


async def prepare_workload(
    client: PowerContextClient,
    scope_id: str,
    task: E2ETask,
) -> WorkloadSetupObservation:
    approved_experiences: list[ApprovedExperienceObservation] = []
    for experience in task.setup.approved_experiences:
        candidate = await client.propose_experience(
            ProposeExperienceRequest(
                scope_id=scope_id,
                proposal=ExperienceProposal(
                    situation=experience.situation,
                    action=experience.action,
                    outcome=experience.outcome,
                    lesson=experience.lesson,
                ),
                source_refs=[],
                artifact_refs=[],
                reason="Prepare declared context for an isolated end-to-end workload.",
            )
        )
        approved = await client.approve_artifact_candidate(
            ApproveArtifactCandidateRequest(
                scope_id=scope_id,
                candidate_id=candidate.candidate_id,
                expected_version=candidate.version,
            )
        )
        artifact = approved.result_artifact
        if artifact is None:
            raise RuntimeError(f"Experience approval produced no Artifact for {experience.id}")  # noqa: TRY003
        approved_experiences.append(
            ApprovedExperienceObservation(
                id=experience.id,
                artifact_id=artifact.artifact_id,
                revision=artifact.revision,
            )
        )
    return WorkloadSetupObservation(approved_experiences=tuple(approved_experiences))


def _job_config(
    task: E2ETask,
    run_id: str,
    scope_id: str,
    output_dir: Path,
    settings: HarnessSettings,
) -> JobConfig:
    repository = settings.repository_path()
    mounts: list[ServiceVolumeConfig] = [
        {
            "type": "bind",
            "source": str(repository),
            "target": "/opt/powercontext/source",
            "read_only": True,
            "bind": {"create_host_path": False},
        }
    ]
    if task.agent.model_source == "codex-oauth":
        auth_path = settings.codex_auth_path()
        if not auth_path.is_file():
            raise CodexAuthNotFoundError(auth_path)
        mounts.append({
            "type": "bind",
            "source": str(auth_path),
            "target": "/run/powercontext/codex-auth.json",
            "read_only": True,
            "bind": {"create_host_path": False},
        })

    agent_env = {
        "BUB_API_KEY": "null",
        "BUB_FALLBACK_MODELS": "null",
        "BUB_HOME": "/installed-agent/bub-home",
        "BUB_MAX_STEPS": str(task.agent.max_steps),
        "BUB_MAX_TOKENS": str(task.agent.max_tokens),
        "BUB_MODEL_TIMEOUT_SECONDS": str(task.agent.timeout_seconds),
        "CODEX_HOME": "/installed-agent/codex",
        "POWERCONTEXT_BUB_BASE_URL": str(task.powercontext.container_url).rstrip("/"),
        "POWERCONTEXT_BUB_CAPTURE_CHECKPOINT_EVERY": str(task.evaluation.checkpoint_every_events),
        "POWERCONTEXT_BUB_CAPTURE_EVENTS": str(task.evaluation.capture_events).lower(),
        "POWERCONTEXT_BUB_CAPTURE_LOG": "/logs/agent/powercontext-capture.jsonl",
        "POWERCONTEXT_BUB_CAPTURE_MAX_BYTES": str(task.evaluation.max_event_bytes),
        "POWERCONTEXT_BUB_SCOPE_ID": scope_id,
        "POWERCONTEXT_BUB_TIMEOUT": str(task.powercontext.timeout_seconds),
    }
    if task.agent.model is not None:
        agent_env["BUB_MODEL"] = task.agent.model
    if settings.agent_proxy_url is not None:
        proxy_url = settings.agent_proxy_url.get_secret_value()
        agent_env.update({
            "HTTP_PROXY": proxy_url,
            "HTTPS_PROXY": proxy_url,
            "NO_PROXY": "127.0.0.1,localhost,host-gateway,powercontext",
            "http_proxy": proxy_url,
            "https_proxy": proxy_url,
            "no_proxy": "127.0.0.1,localhost,host-gateway,powercontext",
        })

    return JobConfig(
        job_name=run_id,
        jobs_dir=output_dir / "harbor-jobs",
        n_attempts=1,
        n_concurrent_trials=1,
        quiet=True,
        environment=EnvironmentConfig(
            type=EnvironmentType.DOCKER,
            delete=True,
            cpu_enforcement_policy=ResourceMode.IGNORE,
            memory_enforcement_policy=ResourceMode.IGNORE,
            extra_docker_compose=[repository / "e2e" / "bub" / "harbor-task-overlay.yaml"],
            mounts=mounts,
        ),
        agents=[
            AgentConfig(
                import_path="powercontext_e2e.harbor_agent:PowerContextBubAcpAgent",
                override_timeout_sec=task.agent.timeout_seconds,
                override_setup_timeout_sec=task.agent.setup_timeout_seconds,
                extra_allowed_hosts=["auth.openai.com", "chatgpt.com"],
                kwargs={
                    "bub_version": task.agent.bub_version,
                    "acp_server_version": task.agent.acp_server_version,
                },
                env=agent_env,
            )
        ],
        datasets=[task.dataset.to_config(repository)],
    )


async def memory_snapshot(client: PowerContextClient, scope_id: str) -> MemorySnapshot:
    response = await client.list_memory_entries(ListMemoryEntriesRequest(scope_id=scope_id))
    return MemorySnapshot(
        entries=tuple(
            MemoryEntrySnapshot(
                entry_id=entry.citation.entry_id,
                entry_version_id=entry.citation.entry_version_id,
                version=entry.version,
                kind=entry.kind,
                text=entry.text,
                state=entry.state.value,
                source_refs=tuple(
                    SourceReferenceSnapshot(name=source.name, source_id=source.source_id)
                    for source in entry.source_refs
                ),
            )
            for entry in response.entries
        )
    )


async def prepared_context(client: PowerContextClient, scope_id: str, query: str) -> PreparedContextSnapshot:
    prepared = await client.prepare_context(PrepareContextRequest(scope_id=scope_id, query=query))
    return PreparedContextSnapshot(status=prepared.status.value, content=prepared.content or "")


def _harbor_observation(result: Any, settings: HarnessSettings) -> tuple[HarborTrialObservation, Path | None]:
    if not result.trial_results:
        return HarborTrialObservation(job_id=str(result.id)), None
    trial = result.trial_results[0]
    rewards = trial.verifier_result.rewards if trial.verifier_result is not None else {}
    exception = trial.exception_info
    return (
        HarborTrialObservation(
            job_id=str(result.id),
            trial_name=trial.trial_name,
            trial_uri=trial.trial_uri,
            task_checksum=trial.task_checksum,
            rewards=rewards or {},
            exception_type=None if exception is None else exception.exception_type,
            exception_message=None if exception is None else redact(exception.exception_message, settings),
            started_at=trial.started_at,
            finished_at=trial.finished_at,
        ),
        _trial_dir(trial.trial_uri),
    )


def _trial_dir(trial_uri: str) -> Path | None:
    parsed = urlparse(trial_uri)
    return Path(unquote(parsed.path)) if parsed.scheme == "file" else None


def _load_capture_records(trial_dir: Path) -> tuple[CaptureRecord, ...]:
    records: list[CaptureRecord] = []
    for path in sorted(trial_dir.rglob("powercontext-capture.jsonl")):
        records.extend(
            CaptureRecord.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    return tuple(records)


def _native_artifacts(trial_dir: Path) -> tuple[NativeArtifact, ...]:
    names = {"acp-summary.json", "acp-events.jsonl", "trajectory.json"}
    return tuple(
        fingerprint(path, relative_to=trial_dir) for path in sorted(trial_dir.rglob("*")) if path.name in names
    )


def _contains_fragments(value: str, expected: tuple[str, ...]) -> bool:
    folded = value.casefold()
    return all(fragment.casefold() in folded for fragment in expected)


def _task_outcome(harbor: HarborTrialObservation) -> str:
    if harbor.exception_type is not None:
        return f"error:{harbor.exception_type}"
    if not harbor.rewards:
        return "unscored"
    return "passed" if any(float(reward) > 0 for reward in harbor.rewards.values()) else "not_passed"


def write_artifacts(
    observation: TaskObservation,
    report: EvaluationReport,
    output_dir: Path,
    *,
    settings: HarnessSettings | None = None,
) -> None:
    settings = settings or HarnessSettings()
    output_dir.mkdir(parents=True, exist_ok=True)
    write_evidence(output_dir / "replay.json", observation.model_dump_json(by_alias=True, indent=2) + "\n", settings)
    write_evaluation_report(
        output_dir / "eval-report.json",
        report=report,
        settings=settings,
    )
    write_evidence(
        output_dir / "report.md",
        render_report(observation, report),
        settings,
    )
