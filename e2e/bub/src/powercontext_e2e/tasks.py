"""Selection and sequential execution for end-to-end workloads."""

from __future__ import annotations

from pathlib import Path

from .models import E2ETask
from .runner import evaluate_task
from .settings import HarnessSettings, ModelNotConfiguredError, bub_environment


class TaskSelectionError(ValueError):
    """Report unknown IDs or categories at the manifest boundary."""

    def __init__(self, selector: str, values: set[str]) -> None:
        super().__init__(f"Unknown e2e workload {selector}: {sorted(values)!r}")


def select_tasks(
    tasks: tuple[E2ETask, ...],
    *,
    ids: tuple[str, ...] = (),
    categories: tuple[str, ...] = (),
) -> tuple[E2ETask, ...]:
    requested_ids = set(ids)
    requested_categories = set(categories)
    available_ids = {task.id for task in tasks}
    available_categories = {category for task in tasks for category in task.categories}
    if missing_ids := requested_ids - available_ids:
        raise TaskSelectionError("IDs", missing_ids)
    if missing_categories := requested_categories - available_categories:
        raise TaskSelectionError("categories", missing_categories)
    if not requested_ids and not requested_categories:
        requested_categories = {"acceptance"}
    return tuple(
        task for task in tasks if task.id in requested_ids or requested_categories.intersection(task.categories)
    )


async def run_tasks(
    tasks: tuple[E2ETask, ...],
    *,
    output_dir: Path,
    settings: HarnessSettings,
) -> bool:
    model_workload_ids = tuple(task.id for task in tasks if task.execution.model)
    if model_workload_ids and "BUB_MODEL" not in bub_environment():
        raise ModelNotConfiguredError(model_workload_ids)

    accepted = True
    for task in tasks:
        task_accepted = await evaluate_task(task, output_dir=output_dir / task.id, settings=settings)
        accepted = task_accepted and accepted
    return accepted
