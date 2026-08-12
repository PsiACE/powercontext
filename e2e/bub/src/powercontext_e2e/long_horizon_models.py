"""Manifest and evidence contracts for long-horizon Memory evaluation."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, HttpUrl, model_validator

from .models import EvidenceModel, MemorySnapshot, PreparedContextSnapshot


class DuplicateProbeError(ValueError):
    """Report duplicate recall probe IDs in one manifest."""

    def __init__(self) -> None:
        super().__init__("Recall probe IDs must be unique")


class HarborTaskSpec(EvidenceModel):
    dataset: str = Field(min_length=1)
    version: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    checksum: str = Field(min_length=1)


class BubAcpAgentSpec(EvidenceModel):
    protocol: Literal["acp"] = "acp"
    implementation: Literal["bub"] = "bub"
    model: str = Field(pattern=r"^openai[:/].+")
    model_source: Literal["codex-oauth"] = "codex-oauth"
    bub_version: str = Field(min_length=1)
    acp_server_version: str = Field(min_length=1)
    max_steps: int = Field(default=50, ge=1, le=200)
    max_tokens: int = Field(default=16384, ge=256)
    timeout_seconds: int = Field(default=3600, ge=60)
    setup_timeout_seconds: int = Field(default=900, ge=60)


class PowerContextEndpointSpec(EvidenceModel):
    host_url: HttpUrl
    container_url: HttpUrl


class CaptureThresholds(EvidenceModel):
    capture_coverage: float = Field(default=0.9, ge=0, le=1)
    groundedness: float = Field(default=0.8, ge=0, le=1)
    probe_coverage: float = Field(default=0.5, ge=0, le=1)
    minimum_in_run_contexts: int = Field(default=1, ge=0)


class RecallProbeSpec(EvidenceModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    query: str = Field(min_length=1, max_length=8192)


class CaptureSpec(EvidenceModel):
    checkpoint_every_events: int = Field(default=5, ge=1, le=100)
    max_event_bytes: int = Field(default=8192, ge=512, le=32768)
    probes: tuple[RecallProbeSpec, ...] = Field(min_length=1)
    thresholds: CaptureThresholds = Field(default_factory=CaptureThresholds)


class LongHorizonScenario(EvidenceModel):
    schema_: Literal["powercontext.long-horizon/v1"] = Field(alias="schema")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    task: HarborTaskSpec
    agent: BubAcpAgentSpec
    powercontext: PowerContextEndpointSpec
    capture: CaptureSpec

    @model_validator(mode="after")
    def require_unique_probe_ids(self) -> LongHorizonScenario:
        probe_ids = [probe.id for probe in self.capture.probes]
        if len(probe_ids) != len(set(probe_ids)):
            raise DuplicateProbeError
        return self


class LongHorizonEnvironment(EvidenceModel):
    commit: str
    database: str
    agent_model: str
    model_source: Literal["codex-oauth"]
    generation_model: str | None = None
    embedding_profile: str | None = None
    started_at: datetime
    finished_at: datetime


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


class LongHorizonObservation(EvidenceModel):
    schema_: Literal["powercontext.long-horizon-evidence/v1"] = Field(
        default="powercontext.long-horizon-evidence/v1",
        alias="schema",
    )
    run_id: str
    environment: LongHorizonEnvironment
    scenario: LongHorizonScenario
    status: Literal["completed", "failed"]
    errors: tuple[str, ...] = ()
    harbor: HarborTrialObservation
    capture_records: tuple[CaptureRecord, ...] = ()
    native_artifacts: tuple[NativeArtifact, ...] = ()
    memory_before: MemorySnapshot
    memory_after: MemorySnapshot
    probes: tuple[RecallProbeObservation, ...] = ()


def load_long_horizon_scenario(path: Path) -> LongHorizonScenario:
    return LongHorizonScenario.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def fingerprint(path: Path) -> NativeArtifact:
    content = path.read_bytes()
    return NativeArtifact(name=path.name, sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
