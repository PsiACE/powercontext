# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Execute public command/state prerequisites for task-oriented Skill guidance."""

from __future__ import annotations

# This controlled experiment executes repository commands and behavior tests.
# ruff: noqa: S101, S603, S607
import argparse
import json
import os
import subprocess
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASELINE = "f28f8edfcb2972ac322f4f12b1f224226a9a40ab"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    baseline = BASELINE
    subprocess.run(
        ["git", "diff", "--exit-code", baseline, "--", "src", "tests", "integrations"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    results = []
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(args.scratch / "home"),
        "TMPDIR": str(args.scratch),
        "PYTHONPATH": str(ROOT / "src"),
        "LANG": "C.UTF-8",
    }
    Path(env["HOME"]).mkdir(exist_ok=True)

    def run(name, command):
        started = time.monotonic()
        completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        row = {
            "name": name,
            "exit": completed.returncode,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
            "stdout": completed.stdout.replace(str(ROOT), "<checkout>").replace(str(args.scratch), "<scratch>"),
            "stderr": completed.stderr.replace(str(ROOT), "<checkout>").replace(str(args.scratch), "<scratch>"),
        }
        results.append(row)
        assert completed.returncode == 0, row

    for group in (
        ("candidate", "approve"),
        ("candidate", "show"),
        ("experience", "generate"),
        ("skill", "generate"),
        ("skill", "export"),
        ("external-skill", "import"),
        ("doctor",),
    ):
        run(
            "public_cli_" + "_".join(group),
            [args.python, "-c", "from powercontext.cli.app import main; main()", *group, "--help"],
        )
    tests = [
        "tests/builtin/review/test_service.py::test_memory_write_remains_direct_and_does_not_create_a_candidate",
        "tests/builtin/review/test_service.py::test_experience_candidate_revise_approve_and_retrieval_gate",
        "tests/builtin/review/test_service.py::test_managed_skill_uses_review_gate_and_exact_replacement_lineage",
        "tests/builtin/review/test_service.py::test_rejected_candidate_is_terminal_and_scope_evidence_isolated",
        "tests/e2e/test_handoff_runtime.py::test_handoff_runtime_supports_temporary_transfer_and_durable_milestones",
        "tests/e2e/test_standard_skill_lifecycle.py::test_standard_skill_package_review_revision_usage_and_governance",
    ]
    run(
        "actual_state_contracts",
        [args.python, "-m", "pytest", "-q", *tests, "--basetemp=" + str(args.scratch / "pytest-state")],
    )
    catalog = tomllib.loads((ROOT / "integrations/capabilities.toml").read_text(encoding="utf-8"))
    selected = [entry for entry in catalog["integrations"] if entry["id"] in {"codex", "openclaw", "hermes", "dsh"}]
    args.output.write_text(
        json.dumps(
            {
                "baseline": baseline,
                "qualification": "Actual CLI help and Runtime/HTTP SQLite behavior tests. No live model routing, native host discovery, or execution success claim.",
                "host_contracts": selected,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(results)} public command/state runs recorded")


if __name__ == "__main__":
    main()
