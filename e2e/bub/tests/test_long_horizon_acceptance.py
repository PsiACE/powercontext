from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from pydantic_evals import Case, Dataset

from powercontext_e2e.long_horizon_models import (
    CaptureRecord,
    HarborTrialObservation,
    LongHorizonEnvironment,
    LongHorizonObservation,
    NativeArtifact,
    RecallProbeObservation,
    load_long_horizon_scenario,
)
from powercontext_e2e.long_horizon_runner import LongHorizonEvaluator
from powercontext_e2e.models import (
    MemoryEntrySnapshot,
    MemorySnapshot,
    PreparedContextSnapshot,
    SourceReferenceSnapshot,
)


def test_memory_acceptance_does_not_require_the_harbor_task_to_pass() -> None:
    async def evaluate() -> None:
        repository = Path(__file__).resolve().parents[3]
        scenario = load_long_horizon_scenario(
            repository / "e2e" / "bub" / "manifests" / "terminal-bench-db-wal-recovery.yaml"
        )
        recorded_at = datetime(2026, 8, 13, tzinfo=UTC)
        captured_source = "bub-event:captured"
        observation = LongHorizonObservation(
            run_id="behavior-test",
            environment=LongHorizonEnvironment(
                commit="abcdef0",
                database="sqlite",
                agent_model=scenario.agent.model,
                model_source="codex-oauth",
                started_at=recorded_at,
                finished_at=recorded_at,
            ),
            scenario=scenario,
            status="completed",
            harbor=HarborTrialObservation(
                job_id="job-id",
                trial_name="trial-name",
                task_checksum=scenario.task.checksum,
                rewards={"reward": 0},
            ),
            capture_records=(
                CaptureRecord(
                    schema="powercontext.bub-capture-event/v1",
                    recorded_at=recorded_at,
                    event="user_prompt",
                    status="captured",
                    sequence=1,
                    source_id=captured_source,
                    source_position=1,
                ),
                CaptureRecord(
                    schema="powercontext.bub-capture-event/v1",
                    recorded_at=recorded_at,
                    event="checkpoint",
                    status="advanced",
                    target_position=1,
                    current_cursor=1,
                    memory_created=True,
                ),
                CaptureRecord(
                    schema="powercontext.bub-capture-event/v1",
                    recorded_at=recorded_at,
                    event="context",
                    status="ready",
                    content_bytes=128,
                    captured_events=1,
                    flushed_position=1,
                ),
            ),
            native_artifacts=tuple(
                NativeArtifact(name=name, sha256="a" * 64, bytes=1)
                for name in ("acp-summary.json", "acp-events.jsonl", "trajectory.json")
            ),
            memory_before=MemorySnapshot(),
            memory_after=MemorySnapshot(
                entries=(
                    MemoryEntrySnapshot(
                        entry_id="entry-1",
                        entry_version_id="entry-version-1",
                        version=1,
                        kind="task-finding",
                        text="The WAL investigation produced a grounded finding.",
                        state="active",
                        source_refs=(SourceReferenceSnapshot(name="content", source_id=captured_source),),
                    ),
                )
            ),
            probes=tuple(
                RecallProbeObservation(
                    id=probe.id,
                    query=probe.query,
                    prepared_context=PreparedContextSnapshot(status="ready", content="Grounded task evidence."),
                )
                for probe in scenario.capture.probes
            ),
        )
        dataset = Dataset(
            name="long-horizon-acceptance-test",
            cases=[Case(name=scenario.id, inputs=scenario)],
            evaluators=[LongHorizonEvaluator()],
        )

        async def recorded(_):
            return observation

        report = await dataset.evaluate(recorded, progress=False)

        assert not report.failures
        assert all(result.value for result in report.cases[0].assertions.values())
        assert report.cases[0].scores["harbor_reward_reward"].value == 0
        assert report.cases[0].labels["task_outcome"].value == "not_passed"

    asyncio.run(evaluate())
