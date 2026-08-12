from __future__ import annotations

from pathlib import Path

from powercontext_e2e.models import load_tasks
from powercontext_e2e.tasks import select_tasks


def test_workloads_can_be_selected_by_multiple_ids_or_category() -> None:
    repository = Path(__file__).resolve().parents[3]
    tasks = load_tasks(repository / "e2e" / "bub" / "tasks")

    assert {task.execution.type for task in tasks} == {"bub"}

    selected_ids = select_tasks(
        tasks,
        ids=("locomo-support-group,terminal-bench-db-wal-recovery",),
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
