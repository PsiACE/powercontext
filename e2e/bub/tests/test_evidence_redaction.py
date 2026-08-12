from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from powercontext_e2e.models import (
    HarborTrialObservation,
    MemoryEntrySnapshot,
    MemorySnapshot,
    RunEnvironment,
    TaskObservation,
    load_tasks,
)
from powercontext_e2e.runner import MemoryEvaluator, write_artifacts


def test_final_evidence_redacts_configured_secrets_and_preserves_the_public_schema(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sensitive_value = "provider-runtime-secret-sentinel"
    monkeypatch.setenv("BUB_API_KEY", sensitive_value)
    repository = Path(__file__).resolve().parents[3]
    task = next(
        task for task in load_tasks(repository / "e2e" / "bub" / "tasks") if task.id == "project-database-decision"
    )
    recorded_at = datetime(2026, 8, 13, tzinfo=UTC)
    observation = TaskObservation(
        run_id="evidence-test",
        environment=RunEnvironment(
            commit="abcdef0",
            database="sqlite",
            model_source="none",
            started_at=recorded_at,
            finished_at=recorded_at,
        ),
        task=task,
        status="failed",
        errors=(f"Provider returned {sensitive_value}",),
        harbor=HarborTrialObservation(
            task_checksum=task.dataset.checksum,
            exception_type="ProviderError",
            exception_message=f"Provider returned {sensitive_value}",
        ),
        memory_before=MemorySnapshot(),
        memory_after=MemorySnapshot(
            entries=(
                MemoryEntrySnapshot(
                    entry_id="entry-1",
                    entry_version_id="entry-version-1",
                    version=1,
                    kind="project-decision",
                    text="The project selected OceanBase.",
                    state="active",
                ),
            )
        ),
    )
    report = MemoryEvaluator().evaluate(task, observation, experiment="evidence-test")

    write_artifacts(observation, report, tmp_path)

    artifacts = {path.name: path.read_text(encoding="utf-8") for path in tmp_path.iterdir()}
    assert set(artifacts) == {"eval-report.json", "replay.json", "report.md"}
    assert all(sensitive_value not in content for content in artifacts.values())
    assert all("[REDACTED]" in content for content in artifacts.values())
    replay = json.loads(artifacts["replay.json"])
    evaluation = json.loads(artifacts["eval-report.json"])
    assert replay["schema"] == "powercontext.e2e-evidence/v1"
    assert evaluation["schema"] == "powercontext.e2e-evaluation/v1"
