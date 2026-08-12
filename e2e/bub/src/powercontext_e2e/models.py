"""Manifest and evidence contracts for end-to-end workloads."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from harbor.models.job.config import DatasetConfig
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Provenance(EvidenceModel):
    source: str
    revision: str
    selection: str
    case_ids: tuple[str, ...] = Field(min_length=1)


class HarborDatasetSpec(EvidenceModel):
    path: Path | None = None
    name: str | None = None
    version: str | None = None
    task_id: str = Field(min_length=1)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_one_source(self) -> HarborDatasetSpec:
        if (self.path is None) == (self.name is None):
            raise ValueError("A Harbor dataset requires exactly one of path or name")  # noqa: TRY003
        if self.path is not None and self.version is not None:
            raise ValueError("A local Harbor dataset cannot declare a version")  # noqa: TRY003
        return self

    def to_config(self, repository: Path) -> DatasetConfig:
        if self.path is not None:
            return DatasetConfig(path=repository / self.path, task_names=[self.task_id])
        return DatasetConfig(name=self.name, version=self.version, task_names=[self.task_id])


class BubAcpAgentSpec(EvidenceModel):
    model: str | None = None
    model_source: Literal["none", "codex-oauth"] = "none"
    bub_version: str = Field(min_length=1)
    acp_server_version: str = Field(min_length=1)
    max_steps: int = Field(default=50, ge=1, le=200)
    max_tokens: int = Field(default=16384, ge=256)
    timeout_seconds: int = Field(default=3600, ge=60)
    setup_timeout_seconds: int = Field(default=900, ge=60)

    @model_validator(mode="after")
    def require_model_for_oauth(self) -> BubAcpAgentSpec:
        if self.model_source == "codex-oauth" and self.model is None:
            raise ValueError("Codex OAuth tasks require an agent model")  # noqa: TRY003
        return self


class PowerContextEndpointSpec(EvidenceModel):
    host_url: HttpUrl
    container_url: HttpUrl
    timeout_seconds: float = Field(default=30, gt=0, le=300)


class CaptureThresholds(EvidenceModel):
    capture_coverage: float = Field(default=0, ge=0, le=1)
    groundedness: float = Field(default=0, ge=0, le=1)
    probe_coverage: float = Field(default=1, ge=0, le=1)
    minimum_in_run_contexts: int = Field(default=0, ge=0)


class RecallProbeSpec(EvidenceModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    query: str = Field(min_length=1, max_length=8192)
    expected_context: tuple[str, ...] = ()


class ApprovedExperienceSpec(EvidenceModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    situation: str = Field(min_length=1, max_length=8000)
    action: str = Field(min_length=1, max_length=8000)
    outcome: str = Field(min_length=1, max_length=8000)
    lesson: str = Field(min_length=1, max_length=8000)


class WorkloadSetupSpec(EvidenceModel):
    approved_experiences: tuple[ApprovedExperienceSpec, ...] = ()

    @model_validator(mode="after")
    def require_unique_experience_ids(self) -> WorkloadSetupSpec:
        experience_ids = [experience.id for experience in self.approved_experiences]
        if len(experience_ids) != len(set(experience_ids)):
            raise ValueError("Approved Experience IDs must be unique")  # noqa: TRY003
        return self


class MemoryEvaluationSpec(EvidenceModel):
    capture_events: bool = False
    checkpoint_every_events: int = Field(default=5, ge=1, le=100)
    max_event_bytes: int = Field(default=8192, ge=512, le=32768)
    require_checkpoint: bool = False
    expected_memory: tuple[str, ...] = ()
    required_native_artifacts: tuple[str, ...] = ("acp-summary.json", "acp-events.jsonl", "trajectory.json")
    probes: tuple[RecallProbeSpec, ...] = Field(min_length=1)
    thresholds: CaptureThresholds = Field(default_factory=CaptureThresholds)

    @model_validator(mode="after")
    def require_unique_probe_ids(self) -> MemoryEvaluationSpec:
        probe_ids = [probe.id for probe in self.probes]
        if len(probe_ids) != len(set(probe_ids)):
            raise ValueError("Recall probe IDs must be unique")  # noqa: TRY003
        return self


class E2ETask(EvidenceModel):
    schema_: Literal["powercontext.e2e-task/v1"] = Field(alias="schema")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    categories: tuple[str, ...] = Field(min_length=1)
    provenance: Provenance | None = None
    dataset: HarborDatasetSpec
    agent: BubAcpAgentSpec
    powercontext: PowerContextEndpointSpec
    setup: WorkloadSetupSpec = Field(default_factory=WorkloadSetupSpec)
    evaluation: MemoryEvaluationSpec


class RunEnvironment(EvidenceModel):
    commit: str
    database: str
    agent_model: str | None = None
    model_source: Literal["none", "codex-oauth"]
    generation_model: str | None = None
    embedding_profile: str | None = None
    started_at: datetime
    finished_at: datetime


class SourceReferenceSnapshot(EvidenceModel):
    name: str
    source_id: str


class MemoryEntrySnapshot(EvidenceModel):
    entry_id: str
    entry_version_id: str
    version: int
    kind: str
    text: str
    state: str
    source_refs: tuple[SourceReferenceSnapshot, ...] = ()


class MemorySnapshot(EvidenceModel):
    entries: tuple[MemoryEntrySnapshot, ...] = ()


class PreparedContextSnapshot(EvidenceModel):
    status: str
    content: str = ""


class CaptureRecord(EvidenceModel):
    schema_: Literal["powercontext.bub-capture-event/v1"] = Field(alias="schema")
    recorded_at: datetime
    event: Literal["user_prompt", "llm_result", "tool_result", "checkpoint", "context"]
    status: str
    sequence: int | None = None
    source_id: str | None = None
    source_position: int | None = None
    error: str | None = None
    final: bool | None = None
    target_position: int | None = None
    previous_cursor: int | None = None
    current_cursor: int | None = None
    high_watermark: int | None = None
    processed_source_count: int | None = None
    memory_created: bool | None = None
    content_bytes: int | None = None
    captured_events: int | None = None
    flushed_position: int | None = None


class RecallProbeObservation(EvidenceModel):
    id: str
    query: str
    prepared_context: PreparedContextSnapshot


class HarborTrialObservation(EvidenceModel):
    job_id: str | None = None
    trial_name: str | None = None
    trial_uri: str | None = None
    task_checksum: str | None = None
    rewards: dict[str, float | int] = Field(default_factory=dict)
    exception_type: str | None = None
    exception_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class NativeArtifact(EvidenceModel):
    name: str
    sha256: str
    bytes: int = Field(ge=0)


class ResolvedInstruction(EvidenceModel):
    step: str | None = None
    artifact: str
    content: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ApprovedExperienceObservation(EvidenceModel):
    id: str
    source_id: str
    artifact_id: str
    revision: int = Field(ge=1)


class WorkloadSetupObservation(EvidenceModel):
    approved_experiences: tuple[ApprovedExperienceObservation, ...] = ()


class TaskObservation(EvidenceModel):
    schema_: Literal["powercontext.e2e-evidence/v1"] = Field(
        default="powercontext.e2e-evidence/v1",
        alias="schema",
    )
    run_id: str
    environment: RunEnvironment
    task: E2ETask
    status: Literal["completed", "failed"]
    errors: tuple[str, ...] = ()
    harbor: HarborTrialObservation
    capture_records: tuple[CaptureRecord, ...] = ()
    native_artifacts: tuple[NativeArtifact, ...] = ()
    resolved_instructions: tuple[ResolvedInstruction, ...] = ()
    setup: WorkloadSetupObservation = Field(default_factory=WorkloadSetupObservation)
    memory_initial: MemorySnapshot = Field(default_factory=MemorySnapshot)
    memory_before: MemorySnapshot
    memory_after: MemorySnapshot
    probes: tuple[RecallProbeObservation, ...] = ()


class EvaluationValue(EvidenceModel):
    value: bool | float | str
    reason: str | None = None


class CaseEvaluation(EvidenceModel):
    name: str
    assertions: dict[str, EvaluationValue] = Field(default_factory=dict)
    scores: dict[str, EvaluationValue] = Field(default_factory=dict)
    labels: dict[str, EvaluationValue] = Field(default_factory=dict)
    metrics: dict[str, int | float] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)


class EvaluationReport(EvidenceModel):
    schema_: Literal["powercontext.e2e-evaluation/v1"] = Field(
        default="powercontext.e2e-evaluation/v1",
        alias="schema",
    )
    experiment: str
    cases: tuple[CaseEvaluation, ...]
    failures: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return not self.failures and all(
            bool(result.value) for case in self.cases for result in case.assertions.values()
        )

    def render(self) -> str:
        lines: list[str] = []
        for case in self.cases:
            lines.append(case.name)
            for name, result in case.assertions.items():
                status = "PASS" if result.value else "FAIL"
                reason = f" — {result.reason}" if result.reason else ""
                lines.append(f"  [{status}] {name}{reason}")
            for name, result in case.scores.items():
                lines.append(f"  [SCORE] {name}: {result.value}")
            for name, result in case.labels.items():
                lines.append(f"  [LABEL] {name}: {result.value}")
        return "\n".join(lines)


def load_tasks(path: Path) -> tuple[E2ETask, ...]:
    task_paths = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
    tasks = tuple(E2ETask.model_validate(yaml.safe_load(item.read_text(encoding="utf-8"))) for item in task_paths)
    ids = [task.id for task in tasks]
    if not tasks:
        raise ValueError(f"No e2e workload manifests found at {path}")  # noqa: TRY003
    if len(ids) != len(set(ids)):
        raise ValueError("E2E workload IDs must be unique")  # noqa: TRY003
    for task in tasks:
        _validate_provenance(task)
    return tasks


def fingerprint(path: Path, *, relative_to: Path | None = None) -> NativeArtifact:
    content = path.read_bytes()
    name = path.relative_to(relative_to).as_posix() if relative_to is not None else path.name
    return NativeArtifact(name=name, sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))


def _validate_provenance(task: E2ETask) -> None:
    if task.provenance is None:
        return
    source = Path(task.provenance.source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != task.provenance.revision:
        raise ValueError(f"Task source fingerprint changed: {source}")  # noqa: TRY003
