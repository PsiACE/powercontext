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

"""Compare pinned complete Skill projections with universal baseline copies."""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASELINE = "f28f8edfcb2972ac322f4f12b1f224226a9a40ab"
SKILL = "skills/powercontext-project-context"
HOSTS = ("agent-plugin", "claude-code", "workbuddy", "codex")


def read_tree(revision: str, host: str) -> dict[str, bytes]:
    root = (
        f"integrations/{host}/" + ("powercontext" if host == "agent-plugin" else "plugins/powercontext") + f"/{SKILL}"
    )
    files = subprocess.check_output(  # noqa: S603 - fixed Git command and explicit source selection
        ["/usr/bin/git", "ls-tree", "-r", "--name-only", revision, root], cwd=ROOT, text=True
    ).splitlines()
    return {
        name.removeprefix(root + "/"): subprocess.check_output(  # noqa: S603 - fixed Git command and explicit source selection
            ["/usr/bin/git", "show", f"{revision}:{name}"], cwd=ROOT
        )
        for name in files
    }


def missing_links(files: dict[str, bytes]) -> list[str]:
    missing = []
    for name, data in files.items():
        if name.endswith(".md"):
            for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", data.decode()):
                if (
                    ":" not in link
                    and not link.startswith("#")
                    and (Path(name).parent / link.split("#")[0]).as_posix() not in files
                ):
                    missing.append(f"{name}:{link}")
    return sorted(missing)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default=BASELINE)
    revision = parser.parse_args().baseline
    trees = {host: read_tree(revision, host) for host in HOSTS}
    common = trees["agent-plugin"]
    results = []
    for host, original in trees.items():
        # Vary source selection only, holding destination host and its expected bytes fixed.
        universal = dict(common)
        selective = {name: common[name] if common.get(name) == data else data for name, data in original.items()}
        for variant, files in (("universal_baseline", universal), ("explicit_projection", selective)):
            results.append({
                "host": host,
                "variant": variant,
                "file_count": len(files),
                "preserves_native_resources": files == original,
                "missing_links": missing_links(files),
                "native_resources_lost": sorted(set(original) - set(files)),
                "digest": sha256(
                    b"".join(name.encode() + b"\0" + data for name, data in sorted(files.items()))
                ).hexdigest(),
            })
    # A single authoritative entry update should reach only equal-contract targets.
    marker = b"\nSynthetic distribution change.\n"
    equal = [host for host in HOSTS if trees[host]["SKILL.md"] == common["SKILL.md"]]
    duplicated = {host: dict(files) for host, files in trees.items()}
    duplicated["agent-plugin"]["SKILL.md"] += marker
    projected = {host: dict(files) for host, files in trees.items()}
    for host in equal:
        projected[host]["SKILL.md"] += marker
    results.append({
        "scenario": "single_authoritative_edit",
        "equal_contract_targets": equal,
        "duplicate_targets_updated": [host for host in equal if marker in duplicated[host]["SKILL.md"]],
        "projected_targets_updated": [host for host in equal if marker in projected[host]["SKILL.md"]],
        "specialized_codex_preserved": projected["codex"] == trees["codex"],
    })
    broken = dict(trees["codex"])
    del broken["references/experience-skills.md"]
    results.append({"scenario": "incomplete_package", "missing_links": missing_links(broken)})
    print(
        json.dumps(
            {
                "baseline": revision,
                "python": platform.python_version(),
                "qualification": "pinned resource bytes and pure in-memory projections; no actual host discovery",
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
