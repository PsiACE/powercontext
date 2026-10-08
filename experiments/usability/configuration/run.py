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


"""Execute .env editing ablations against PowerContext baseline f28f8edf."""
# ruff: noqa: S101

import argparse
import json
import tempfile
from pathlib import Path

# Historical f28f8edf import: execute in the pinned cce34000 worktree documented beside this harness.
from powercontext.cli.env_file import parse_environment  # ty: ignore[unresolved-import]
from typer.testing import CliRunner

from powercontext.cli.app import create_cli
from powercontext.cli.config import app, write_environment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    observations = []
    with tempfile.TemporaryDirectory(dir=args.scratch) as temporary:
        path = Path(temporary) / "server.env"
        path.write_text("POWERCONTEXT_SERVER_HTTP_PORT=8000\nBUSINESS_CREDENTIAL=synthetic-private\n")
        first_snapshot = parse_environment(path.read_text())
        second_snapshot = parse_environment(path.read_text())
        first_snapshot["POWERCONTEXT_SERVER_HTTP_PORT"] = "8100"
        second_snapshot["POWERCONTEXT_SERVER_DASHBOARD_ENABLED"] = "true"
        write_environment(
            path, "\n".join(f"{name}={value}" for name, value in first_snapshot.items()) + "\n", backup=False
        )
        write_environment(
            path, "\n".join(f"{name}={value}" for name, value in second_snapshot.items()) + "\n", backup=False
        )
        visible = parse_environment(path.read_text())
        assert visible["POWERCONTEXT_SERVER_HTTP_PORT"] == "8000"
        observations.append({
            "case": "atomic_replace_without_revision",
            "first_change_preserved": False,
            "document_readable": True,
        })
        show = CliRunner().invoke(create_cli([app]), ["config", "show", "--env-file", str(path)])
        assert show.exit_code == 0 and "synthetic-private" in show.output
        observations.append({
            "case": "heuristic_redaction_without_public_allowlist",
            "unknown_private_value_visible": True,
        })
        path.write_text("POWERCONTEXT_SERVER_HTTP_PORRT=8100\n")
        validation = CliRunner().invoke(create_cli([app]), ["config", "validate", "--env-file", str(path)])
        assert validation.exit_code == 0
        observations.append({"case": "settings_without_change_field_validation", "misspelled_field_accepted": True})
        try:
            parse_environment("TOKEN=$(touch must-not-exist)\n")
        except ValueError:
            rejected = True
        else:
            rejected = False
        assert rejected
        observations.append({"case": "shell_free_parser", "expansion_rejected": True})
    args.output.write_text(json.dumps({"baseline": "f28f8edf", "cases": observations}, indent=2) + "\n")


if __name__ == "__main__":
    main()
