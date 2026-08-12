"""Run Harbor tasks and evaluate PowerContext Memory captured during the run."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from uuid import uuid4

from harbor.job import Job
from harbor.models.environment_type import EnvironmentType
from harbor.models.job.config import DatasetConfig, JobConfig
from harbor.models.trial.config import AgentConfig, EnvironmentConfig
from powercontext.client import PowerContextClient
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext
from pydantic_evals.reporting import EvaluationReport

from .long_horizon_models import (
    CaptureRecord,
    HarborTrialObservation,
    LongHorizonEnvironment,
    LongHorizonObservation,
    LongHorizonScenario,
    NativeArtifact,
    RecallProbeObservation,
    fingerprint,
)
from .models import MemorySnapshot
from .runner import current_commit, memory_snapshot, prepared_context, redact

Report = EvaluationReport[LongHorizonScenario, LongHorizonObservation, dict[str, str]]
Context = EvaluatorContext[LongHorizonScenario, LongHorizonObservation, dict[str, str]]
CAPTURE_EVENTS = frozenset({"user_prompt", "llm_result", "tool_result"})
NATIVE_ARTIFACT_NAMES = ("acp-summary.json", "acp-events.jsonl", "trajectory.json")


class UvNotFoundError(RuntimeError):
    """Report a missing uv executable."""

    def __init__(self) -> None:
        super().__init__("Long-horizon evaluation requires uv to build the Harbor agent wheels")


class CodexAuthNotFoundError(RuntimeError):
    """Report missing local Codex OAuth credentials."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"Long-horizon evaluation requires Codex OAuth credentials at {path}")


@dataclass
class LongHorizonEvaluator(Evaluator[LongHorizonScenario, LongHorizonObservation, dict[str, str]]):
    """Evaluate capture, grounded Memory, and recall without gating on task reward."""

    async def evaluate(self, ctx: Context) -> dict[str, bool | float | str | EvaluationReason]:
        captured_records = [
            record
            for record in ctx.output.capture_records
            if record.event in CAPTURE_EVENTS and record.status == "captured"
        ]
        eligible_records = [record for record in ctx.output.capture_records if record.event in CAPTURE_EVENTS]
        capture_coverage = len(captured_records) / len(eligible_records) if eligible_records else 0.0

        memory_before_ids = {entry.entry_id for entry in ctx.output.memory_before.entries}
        new_memory = [entry for entry in ctx.output.memory_after.entries if entry.entry_id not in memory_before_ids]
        captured_source_ids = {record.source_id for record in captured_records if record.source_id is not None}
        grounded_memory = [
            entry
            for entry in new_memory
            if entry.source_refs and all(source.source_id in captured_source_ids for source in entry.source_refs)
        ]
        groundedness = len(grounded_memory) / len(new_memory) if new_memory else 0.0

        supported_probes = [
            probe
            for probe in ctx.output.probes
            if probe.prepared_context.status == "ready" and bool(probe.prepared_context.content.strip())
        ]
        probe_coverage = len(supported_probes) / len(ctx.inputs.capture.probes)
        in_run_contexts = sum(
            record.event == "context"
            and record.content_bytes is not None
            and record.content_bytes > 0
            and bool(record.captured_events)
            and bool(record.flushed_position)
            for record in ctx.output.capture_records
        )
        completed_checkpoints = [
            record
            for record in ctx.output.capture_records
            if record.event == "checkpoint"
            and record.status != "failed"
            and record.current_cursor is not None
            and record.target_position is not None
            and record.current_cursor >= record.target_position
        ]
        native_names = {artifact.name for artifact in ctx.output.native_artifacts}
        checksum_matches = ctx.output.harbor.task_checksum == ctx.inputs.task.checksum
        thresholds = ctx.inputs.capture.thresholds

        ctx.attributes.update({
            "commit": ctx.output.environment.commit,
            "database": ctx.output.environment.database,
            "dataset": ctx.inputs.task.dataset,
            "run_id": ctx.output.run_id,
            "task_id": ctx.inputs.task.task_id,
        })
        ctx.metrics.update({
            "capture_events": len(eligible_records),
            "captured_sources": len(captured_records),
            "completed_checkpoints": len(completed_checkpoints),
            "in_run_contexts": in_run_contexts,
            "memory_entries_after": len(ctx.output.memory_after.entries),
            "memory_entries_created": len(new_memory),
            "recall_probes_supported": len(supported_probes),
        })

        results: dict[str, bool | float | str | EvaluationReason] = {
            "collection_completed": EvaluationReason(
                value=ctx.output.status == "completed",
                reason=None if ctx.output.status == "completed" else "; ".join(ctx.output.errors),
            ),
            "task_provenance_matches": EvaluationReason(
                value=checksum_matches,
                reason=None
                if checksum_matches
                else f"Expected task checksum {ctx.inputs.task.checksum!r}, observed {ctx.output.harbor.task_checksum!r}.",
            ),
            "native_acp_evidence_recorded": EvaluationReason(
                value=set(NATIVE_ARTIFACT_NAMES) <= native_names,
                reason=None
                if set(NATIVE_ARTIFACT_NAMES) <= native_names
                else f"Missing native ACP artifacts: {sorted(set(NATIVE_ARTIFACT_NAMES) - native_names)!r}.",
            ),
            "capture_coverage_accepted": EvaluationReason(
                value=capture_coverage >= thresholds.capture_coverage,
                reason=f"Observed {capture_coverage:.3f}; required {thresholds.capture_coverage:.3f}.",
            ),
            "memory_created_during_run": EvaluationReason(
                value=bool(new_memory and completed_checkpoints),
                reason=f"Created {len(new_memory)} Memory entries across {len(completed_checkpoints)} completed checkpoints.",
            ),
            "memory_grounded": EvaluationReason(
                value=groundedness >= thresholds.groundedness,
                reason=f"Observed {groundedness:.3f}; required {thresholds.groundedness:.3f}.",
            ),
            "recall_probes_supported": EvaluationReason(
                value=probe_coverage >= thresholds.probe_coverage,
                reason=f"Observed {probe_coverage:.3f}; required {thresholds.probe_coverage:.3f}.",
            ),
            "memory_recalled_during_run": EvaluationReason(
                value=in_run_contexts >= thresholds.minimum_in_run_contexts,
                reason=f"Observed {in_run_contexts}; required {thresholds.minimum_in_run_contexts}.",
            ),
            "scope_started_empty": EvaluationReason(
                value=not ctx.output.memory_before.entries,
                reason=f"Scope started with {len(ctx.output.memory_before.entries)} Memory entries.",
            ),
            "capture_coverage": capture_coverage,
            "groundedness": groundedness,
            "probe_coverage": probe_coverage,
            "task_outcome": _task_outcome(ctx.output.harbor),
        }
        for reward_name, reward in sorted(ctx.output.harbor.rewards.items()):
            results[f"harbor_reward_{reward_name}"] = float(reward)
        return results


async def evaluate_long_horizon_scenario(scenario: LongHorizonScenario, *, output_dir: Path) -> bool:
    evaluator = LongHorizonEvaluator()
    dataset = Dataset[LongHorizonScenario, LongHorizonObservation, dict[str, str]](
        name="powercontext-long-horizon-memory",
        cases=[Case(name=scenario.id, inputs=scenario, metadata={"backend": "harbor"})],
        evaluators=[evaluator],
    )

    async def run(inputs: LongHorizonScenario) -> LongHorizonObservation:
        return await run_long_horizon(inputs, output_dir=output_dir)

    report = await dataset.evaluate(run, name=f"long-horizon:{scenario.id}", max_concurrency=1, progress=False)
    observation = report.cases[0].output
    write_long_horizon_artifacts(observation, report, output_dir)
    return not report.failures and all(result.value for result in report.cases[0].assertions.values())


async def rescore_long_horizon(replay_path: Path, output_dir: Path) -> bool:
    observation = LongHorizonObservation.model_validate_json(replay_path.read_text(encoding="utf-8"))
    dataset = Dataset[LongHorizonScenario, LongHorizonObservation, dict[str, str]](
        name="powercontext-long-horizon-memory",
        cases=[Case(name=observation.scenario.id, inputs=observation.scenario, metadata={"backend": "harbor"})],
        evaluators=[LongHorizonEvaluator()],
    )

    async def recorded(_: LongHorizonScenario) -> LongHorizonObservation:
        return observation

    report = await dataset.evaluate(recorded, name=f"offline:{observation.scenario.id}", progress=False)
    write_long_horizon_artifacts(report.cases[0].output, report, output_dir)
    return not report.failures and all(result.value for result in report.cases[0].assertions.values())


async def run_long_horizon(scenario: LongHorizonScenario, *, output_dir: Path) -> LongHorizonObservation:
    started_at = datetime.now(UTC)
    run_id = f"{scenario.id}-{uuid4().hex[:12]}"
    scope_id = f"e2e:{run_id}"
    errors: list[str] = []
    capture_records: tuple[CaptureRecord, ...] = ()
    native_artifacts: tuple[NativeArtifact, ...] = ()
    harbor_observation = HarborTrialObservation()
    memory_before = MemorySnapshot()
    memory_after = MemorySnapshot()

    try:
        async with PowerContextClient(_host_url(scenario), timeout=30) as client:
            await client.get_readiness()
            memory_before = await memory_snapshot(client, scope_id)

        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="powercontext-harbor-wheels-") as temporary_directory:
            wheel_dir = Path(temporary_directory)
            _build_agent_wheels(wheel_dir)
            job = await Job.create(_job_config(scenario, run_id, scope_id, output_dir, wheel_dir))
            result = await job.run()
            harbor_observation, trial_dir = _harbor_observation(result)
            if trial_dir is not None:
                capture_records = _load_capture_records(trial_dir / "agent" / "powercontext-capture.jsonl")
                native_artifacts = _native_artifacts(trial_dir)

        async with PowerContextClient(_host_url(scenario), timeout=30) as client:
            memory_after = await memory_snapshot(client, scope_id)
            probe_observations: list[RecallProbeObservation] = []
            for probe in scenario.capture.probes:
                probe_observations.append(
                    RecallProbeObservation(
                        id=probe.id,
                        query=probe.query,
                        prepared_context=await prepared_context(client, scope_id, probe.query),
                    )
                )
            probes = tuple(probe_observations)
    except Exception as exc:
        errors.append(redact(f"{type(exc).__name__}: {exc}"))
        probes = ()
        async with PowerContextClient(_host_url(scenario), timeout=30) as client:
            with suppress(Exception):
                memory_after = await memory_snapshot(client, scope_id)

    finished_at = datetime.now(UTC)
    return LongHorizonObservation(
        run_id=run_id,
        environment=LongHorizonEnvironment(
            commit=current_commit(),
            database=os.getenv("POWERCONTEXT_E2E_DATABASE", "unknown"),
            agent_model=scenario.agent.model,
            model_source=scenario.agent.model_source,
            generation_model=os.getenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL"),
            embedding_profile=os.getenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID"),
            started_at=started_at,
            finished_at=finished_at,
        ),
        scenario=scenario,
        status="completed" if not errors else "failed",
        errors=tuple(errors),
        harbor=harbor_observation,
        capture_records=capture_records,
        native_artifacts=native_artifacts,
        memory_before=memory_before,
        memory_after=memory_after,
        probes=probes,
    )


def _job_config(
    scenario: LongHorizonScenario,
    run_id: str,
    scope_id: str,
    output_dir: Path,
    wheel_dir: Path,
) -> JobConfig:
    auth_path = _codex_auth_path()
    agent = scenario.agent
    return JobConfig(
        job_name=run_id,
        jobs_dir=output_dir / "harbor-jobs",
        n_attempts=1,
        n_concurrent_trials=1,
        quiet=True,
        environment=EnvironmentConfig(type=EnvironmentType.DOCKER, delete=True),
        agents=[
            AgentConfig(
                import_path="powercontext_e2e.harbor_agent:PowerContextBubAcpAgent",
                model_name=agent.model,
                override_timeout_sec=agent.timeout_seconds,
                override_setup_timeout_sec=agent.setup_timeout_seconds,
                extra_allowed_hosts=["auth.openai.com", "chatgpt.com"],
                kwargs={
                    "wheel_dir": str(wheel_dir),
                    "codex_auth_path": str(auth_path),
                    "bub_version": agent.bub_version,
                    "acp_server_version": agent.acp_server_version,
                },
                env={
                    "BUB_API_KEY": "null",
                    "BUB_FALLBACK_MODELS": "null",
                    "BUB_HOME": "/installed-agent/bub-home",
                    "BUB_MAX_STEPS": str(agent.max_steps),
                    "BUB_MAX_TOKENS": str(agent.max_tokens),
                    "BUB_MODEL": agent.model,
                    "BUB_MODEL_TIMEOUT_SECONDS": str(agent.timeout_seconds),
                    "CODEX_HOME": "/installed-agent/codex",
                    "POWERCONTEXT_BUB_BASE_URL": str(scenario.powercontext.container_url).rstrip("/"),
                    "POWERCONTEXT_BUB_CAPTURE_CHECKPOINT_EVERY": str(scenario.capture.checkpoint_every_events),
                    "POWERCONTEXT_BUB_CAPTURE_EVENTS": "true",
                    "POWERCONTEXT_BUB_CAPTURE_LOG": "/logs/agent/powercontext-capture.jsonl",
                    "POWERCONTEXT_BUB_CAPTURE_MAX_BYTES": str(scenario.capture.max_event_bytes),
                    "POWERCONTEXT_BUB_SCOPE_ID": scope_id,
                    "POWERCONTEXT_BUB_TIMEOUT": "30",
                },
            )
        ],
        datasets=[
            DatasetConfig(
                name=scenario.task.dataset,
                version=scenario.task.version,
                task_names=[scenario.task.task_id],
            )
        ],
    )


def _build_agent_wheels(wheel_dir: Path) -> None:
    repository = Path(__file__).resolve().parents[4]
    uv = shutil.which("uv")
    if uv is None:
        raise UvNotFoundError
    for package in (repository, repository / "integrations" / "bub"):
        subprocess.run(  # noqa: S603 - uv is resolved with shutil.which.
            [uv, "build", "--wheel", "--out-dir", str(wheel_dir), str(package)],
            check=True,
            cwd=repository,
            timeout=300,
        )


def _codex_auth_path() -> Path:
    codex_home = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    auth_path = codex_home / "auth.json"
    if not auth_path.is_file():
        raise CodexAuthNotFoundError(auth_path)
    return auth_path


def _host_url(scenario: LongHorizonScenario) -> str:
    configured_url = os.getenv("POWERCONTEXT_BUB_BASE_URL")
    return (configured_url or str(scenario.powercontext.host_url)).rstrip("/")


def _harbor_observation(result: Any) -> tuple[HarborTrialObservation, Path | None]:
    if not result.trial_results:
        return HarborTrialObservation(job_id=str(result.id)), None
    trial = result.trial_results[0]
    verifier_rewards = trial.verifier_result.rewards if trial.verifier_result is not None else {}
    exception = trial.exception_info
    trial_dir = _trial_dir(trial.trial_uri)
    return (
        HarborTrialObservation(
            job_id=str(result.id),
            trial_name=trial.trial_name,
            trial_uri=trial.trial_uri,
            task_checksum=trial.task_checksum,
            rewards=verifier_rewards or {},
            exception_type=None if exception is None else exception.exception_type,
            exception_message=None if exception is None else redact(exception.exception_message),
            started_at=trial.started_at,
            finished_at=trial.finished_at,
        ),
        trial_dir,
    )


def _trial_dir(trial_uri: str) -> Path | None:
    parsed = urlparse(trial_uri)
    if parsed.scheme != "file":
        return None
    return Path(unquote(parsed.path))


def _load_capture_records(path: Path) -> tuple[CaptureRecord, ...]:
    if not path.is_file():
        return ()
    return tuple(
        CaptureRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def _native_artifacts(trial_dir: Path) -> tuple[NativeArtifact, ...]:
    return tuple(fingerprint(path) for name in NATIVE_ARTIFACT_NAMES if (path := trial_dir / "agent" / name).is_file())


def _task_outcome(harbor: HarborTrialObservation) -> str:
    if harbor.exception_type is not None:
        return f"error:{harbor.exception_type}"
    if not harbor.rewards:
        return "unscored"
    return "passed" if any(float(reward) > 0 for reward in harbor.rewards.values()) else "not_passed"


def write_long_horizon_artifacts(
    observation: LongHorizonObservation,
    report: Report,
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "replay.json").write_text(
        observation.model_dump_json(by_alias=True, indent=2) + "\n",
        encoding="utf-8",
    )
    cases = [
        {
            "name": case.name,
            "assertions": {
                name: {"value": result.value, "reason": result.reason}
                for name, result in sorted(case.assertions.items())
            },
            "scores": {
                name: {"value": result.value, "reason": result.reason} for name, result in sorted(case.scores.items())
            },
            "labels": {
                name: {"value": result.value, "reason": result.reason} for name, result in sorted(case.labels.items())
            },
            "metrics": dict(sorted(case.metrics.items())),
            "attributes": case.attributes,
            "task_duration": case.task_duration,
            "total_duration": case.total_duration,
        }
        for case in report.cases
    ]
    payload: dict[str, Any] = {
        "schema": "powercontext.long-horizon-evaluation/v1",
        "experiment": report.name,
        "cases": cases,
        "failures": [{"name": failure.name, "error": failure.error_message} for failure in report.failures],
    }
    (output_dir / "eval-report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    reward_lines = (
        "\n".join(f"- `{name}`: `{value}`" for name, value in sorted(observation.harbor.rewards.items()))
        or "- No native reward was recorded."
    )
    (output_dir / "report.md").write_text(
        "# PowerContext long-horizon Memory evaluation\n\n"
        f"- Scenario: `{observation.scenario.id}`\n"
        f"- Harbor task: `{observation.scenario.task.dataset}@{observation.scenario.task.version}` / "
        f"`{observation.scenario.task.task_id}`\n"
        f"- Collection status: `{observation.status}`\n"
        f"- Native task outcome: `{_task_outcome(observation.harbor)}` (diagnostic only)\n\n"
        "## Harbor reward\n\n"
        f"{reward_lines}\n\n"
        "## Memory evaluation\n\n"
        f"```text\n{report.render(include_reasons=True)}\n```\n",
        encoding="utf-8",
    )
