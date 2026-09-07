"""Read-only stories and deterministic usage samples for design evaluation."""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

DATA = json.loads((Path(__file__).with_name("content.json")).read_text())


def statistics(period: str, scenario: str, scope: str) -> dict[str, Any]:
    days = {"today": 1, "7d": 7, "30d": 30}[period]
    end = date(2026, 9, 7)
    series = []
    for offset in range(days):
        current = end - timedelta(days=days - offset - 1)
        index = (current - date(2026, 9, 1)).days % 7
        baseline = [12000, 18000, 15000, 21000, 24000, 16000, 14000][index]
        recalled = [7000, 11000, 9000, 13000, 14000, 10000, 8000][index]
        if scope == "research":
            baseline, recalled = baseline // 2, recalled // 2
        if scenario == "negative":
            recalled = baseline + 1000
        if scenario == "empty":
            baseline = recalled = 0
        series.append({
            "date": current.isoformat(),
            "label": f"{current.month}/{current.day}",
            "baseline_tokens": baseline,
            "recalled_tokens": recalled,
            "preparations": [4, 4, 3, 4, 4, 3, 2][index] if baseline else 0,
            "comparable_preparations": [3, 3, 2, 3, 3, 2, 2][index] if baseline else 0,
        })
        if scenario == "uncomparable":
            series[-1].update(baseline_tokens=0, recalled_tokens=0, comparable_preparations=0)
    totals = {
        key: sum(row[key] for row in series)
        for key in ("baseline_tokens", "recalled_tokens", "preparations", "comparable_preparations")
    }
    reduction = totals["baseline_tokens"] - totals["recalled_tokens"]
    percent = round(abs(reduction) / totals["baseline_tokens"] * 100) if totals["baseline_tokens"] else None
    model_usage = [
        {
            "purpose": purpose,
            "input_tokens": None if scenario == "unknown" else 0 if scenario == "empty" else int(inputs * days / 7),
            "output_tokens": None if scenario == "unknown" else 0 if scenario == "empty" else int(outputs * days / 7),
        }
        for purpose, inputs, outputs in [("memory_extraction", 6000, 800), ("experience_generation", 2000, 200)]
    ]
    return {
        "model_usage": model_usage,
        "generation_input": None if scenario == "unknown" else sum(row["input_tokens"] for row in model_usage),
        "generation_output": None if scenario == "unknown" else sum(row["output_tokens"] for row in model_usage),
        "embedding_input": None if scenario == "unknown" else 0 if scenario == "empty" else int(2000 * days / 7),
        **totals,
        "daily": series,
        "token_reduction": reduction,
        "percent": percent,
        "scale": max(1, *(max(row["baseline_tokens"], row["recalled_tokens"]) for row in series)),
        "start": series[0]["date"],
        "end": end.isoformat(),
        "days": days,
    }
