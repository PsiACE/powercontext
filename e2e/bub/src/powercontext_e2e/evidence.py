"""Stable, redacted evidence serialization shared by all task adapters."""

from __future__ import annotations

import json
from pathlib import Path

from .models import EvaluationReport
from .settings import HarnessSettings

REDACTED = "[REDACTED]"


def redact(value: str, settings: HarnessSettings | None = None) -> str:
    """Redact configured runtime secrets from diagnostic text."""

    for secret in (settings or HarnessSettings()).evidence_secrets():
        value = value.replace(secret, REDACTED)
        value = value.replace(json.dumps(secret, ensure_ascii=False)[1:-1], REDACTED)
    return value


def write_evidence(path: Path, content: str, settings: HarnessSettings | None = None) -> None:
    path.write_text(redact(content, settings), encoding="utf-8")


def write_evaluation_report(
    path: Path,
    *,
    report: EvaluationReport,
    settings: HarnessSettings | None = None,
) -> None:
    write_evidence(
        path,
        report.model_dump_json(by_alias=True, indent=2) + "\n",
        settings,
    )
