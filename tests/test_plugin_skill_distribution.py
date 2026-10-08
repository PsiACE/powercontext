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

"""Build-time Skill projections retain native resources and foreign files."""

from __future__ import annotations

import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest
from generate_plugin_skills import MANIFEST, ROOT, inspect_output, render_skill, write_output

TARGETS = json.loads(MANIFEST.read_text())["targets"]


@pytest.mark.parametrize("host", TARGETS)
def test_complete_native_skill_projection(host: str, tmp_path: Path) -> None:
    target = TARGETS[host]
    files = render_skill(ROOT, target)
    checked_in = ROOT / target["destination"]
    assert not inspect_output(checked_in, files, target["retired"])
    first = tmp_path / "one/powercontext-project-context"
    second = tmp_path / "two/powercontext-project-context"
    write_output(first, files, [])
    write_output(second, files, [])
    observed = [
        {
            path.relative_to(directory).as_posix(): sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*")
            if path.is_file()
        }
        for directory in (first, second)
    ]
    assert observed[0] == observed[1]
    assert set(observed[0]) == set(files)


def test_incomplete_reference_rejected_before_output(tmp_path: Path) -> None:
    target = TARGETS["codex"]
    broken = {
        **target,
        "files": {
            name: source for name, source in target["files"].items() if name != "references/experience-skills.md"
        },
    }
    with pytest.raises(ValueError, match="Incomplete Skill projection"):
        render_skill(ROOT, broken)
    assert not list(tmp_path.iterdir())


def test_refresh_and_explicit_retirement_preserve_foreign_files(tmp_path: Path) -> None:
    files = render_skill(ROOT, TARGETS["claude-code"])
    destination = tmp_path / "plugin/skills/powercontext-project-context"
    write_output(destination, files, [])
    private_mcp = tmp_path / "plugin/.mcp.json"
    private_mcp.write_text('{"private":"user configuration"}')
    foreign = destination / "user-notes.md"
    foreign.write_text("User-owned notes")
    stale = destination / "references/retired.md"
    stale.write_text("Explicitly retired generated reference")
    (destination / "SKILL.md").write_text("Repository drift")
    assert set(inspect_output(destination, files, ["references/retired.md"])) == {"SKILL.md", "references/retired.md"}
    write_output(destination, files, ["references/retired.md"])
    assert not inspect_output(destination, files, ["references/retired.md"])
    assert foreign.read_text() == "User-owned notes"
    assert private_mcp.read_text() == '{"private":"user configuration"}'
    assert not stale.exists()


def test_symlink_destination_rejected_before_publication(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = tmp_path / "skill"
    try:
        destination.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("native symlinks unavailable")
    files = render_skill(ROOT, TARGETS["agent-plugin"])
    with pytest.raises(ValueError, match="symlink output"):
        write_output(destination, files, [])
    assert not list(outside.iterdir())


@pytest.mark.parametrize("unsafe", ["../escaped", "/absolute", "folder\\file", "C:/escaped", "entry:stream"])
def test_resource_path_escape_rejected(tmp_path: Path, unsafe: str) -> None:
    target = {"files": {"SKILL.md": unsafe}}
    with pytest.raises(ValueError, match="contained relative"):
        render_skill(tmp_path, target)


def test_cli_rejects_symlink_above_skill_directory(tmp_path: Path) -> None:
    outside = tmp_path / "foreign"
    outside.mkdir()
    alias = tmp_path / "output"
    try:
        alias.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("native symlinks unavailable")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate_plugin_skills.py"), "--target", "codex", "--output", str(alias)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "symlink output" in result.stderr
    assert not list(outside.iterdir())
