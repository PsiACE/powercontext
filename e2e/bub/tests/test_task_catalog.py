from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from powercontext_e2e.models import load_tasks
from powercontext_e2e.settings import HarnessSettings, ModelNotConfiguredError
from powercontext_e2e.tasks import run_tasks, select_tasks


def test_workloads_can_be_selected_by_multiple_ids_or_category() -> None:
    repository = Path(__file__).resolve().parents[3]
    tasks = load_tasks(repository / "e2e" / "bub" / "tasks")

    assert {task.execution.type for task in tasks} == {"bub"}
    assert {task.id for task in tasks if task.execution.model} == {
        "approved-experience-recall",
        "terminal-bench-db-wal-recovery",
    }

    selected_ids = select_tasks(
        tasks,
        ids=("locomo-support-group", "terminal-bench-db-wal-recovery"),
    )
    acceptance = select_tasks(tasks, categories=("acceptance",))
    samples = select_tasks(tasks, categories=("sample",))
    live = select_tasks(tasks, categories=("live",))

    assert [task.id for task in selected_ids] == [
        "locomo-support-group",
        "terminal-bench-db-wal-recovery",
    ]
    assert [task.id for task in acceptance] == [
        "locomo-support-group",
        "project-database-decision",
    ]
    assert [task.id for task in samples] == ["locomo-support-group", "project-database-decision"]
    assert [task.id for task in live] == ["approved-experience-recall"]


def test_model_backed_workloads_require_a_runtime_model(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BUB_MODEL", raising=False)
    repository = Path(__file__).resolve().parents[3]
    tasks = load_tasks(repository / "e2e" / "bub" / "tasks")
    model_tasks = tuple(task for task in tasks if task.execution.model)
    settings = HarnessSettings(repository=repository)

    with pytest.raises(ModelNotConfiguredError, match="approved-experience-recall, terminal-bench-db-wal-recovery"):
        asyncio.run(run_tasks(model_tasks, output_dir=tmp_path, settings=settings))
