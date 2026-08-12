"""Render the human-readable evaluation report through Marko."""

from __future__ import annotations

from marko import Markdown, block
from marko.element import Element
from marko.md_renderer import MarkdownRenderer

from .models import EvaluationReport, TaskObservation


def render_report(observation: TaskObservation, report: EvaluationReport) -> str:
    markdown = Markdown(renderer=MarkdownRenderer)
    document = block.Document()
    children: list[Element] = []
    children.extend(_nodes(markdown, "# PowerContext end-to-end Memory evaluation"))
    children.append(block.BlankLine(0))
    children.extend(
        _nodes(
            markdown,
            "\n".join((
                f"- Task: `{observation.task.id}`",
                f"- Harbor dataset: `{observation.task.dataset.name or observation.task.dataset.path}`",
                f"- Collection status: `{observation.status}`",
                f"- Native task outcome: `{_task_outcome(report)}` (diagnostic only)",
            )),
        )
    )
    children.append(block.BlankLine(0))
    children.extend(_nodes(markdown, "## Harbor reward"))
    children.append(block.BlankLine(0))
    reward_lines = (
        "\n".join(f"- `{name}`: `{value}`" for name, value in sorted(observation.harbor.rewards.items()))
        or "- No native reward was recorded."
    )
    children.extend(_nodes(markdown, reward_lines))
    children.append(block.BlankLine(0))
    children.extend(_nodes(markdown, "## Memory evaluation"))
    children.append(block.BlankLine(0))
    children.extend(_nodes(markdown, f"```text\n{report.render()}\n```"))
    document.children = children
    return markdown.render(document)


def _nodes(markdown: Markdown, source: str) -> list[Element]:
    return list(markdown.parse(source).children)


def _task_outcome(report: EvaluationReport) -> str:
    value = report.cases[0].labels.get("task_outcome")
    return str(value.value) if value is not None else "unscored"
