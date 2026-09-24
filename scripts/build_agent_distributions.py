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

"""Assemble integrations that use an installed PowerContext client."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations/distribution"))

from agent_distribution import ROOT, assemble, require_runners, write_package
from powercontext_integrations.resources import ASSETS, install_resources, tool_catalog
from powercontext_integrations.targets import load_targets


def catalog_rows(targets):
    profiles = json.loads((ASSETS / "resources.json").read_text())
    return [
        target.model_dump(mode="json")
        | {
            "tools": tool_catalog(target.target),
            "tool_transport": (
                "mcp"
                if "mcp" in profiles.get(target.target, {})
                else "native"
                if "runtime" in profiles.get(target.target, {})
                else "sdk"
            ),
        }
        for target in targets
    ]


def catalog_table(rows, *, headers="| Target | Language | Tools (baseline + extensions) | Hook registrations |"):
    lines = [headers, "| --- | --- | --- | --- |"]
    for row in rows:
        counts = Counter(tool["origin"] for tool in row["tools"])
        tools = (
            f"{counts['baseline']} + {counts['extension']}"
            if row["tool_transport"] == "native"
            else "Server MCP"
            if row["tool_transport"] == "mcp"
            else "SDK"
        )
        events = ", ".join(f"`{hook['event']}`" for hook in row["hooks"]) or "—"
        lines.append(f"| {row['label']} | {row['language']} | {tools} | {events} |")
    return "\n".join(lines)


def update_catalog_docs(rows):
    for locale in ("en", "zh"):
        path = ROOT / f"docs/{locale}/docs/integrations/capabilities.md"
        start, end = "<!-- integration-catalog:start -->", "<!-- integration-catalog:end -->"
        text = path.read_text(encoding="utf-8")
        before, _, rest = text.partition(start)
        _, marker, after = rest.partition(end)
        if not marker:
            message = f"Missing integration catalog markers: {path}"
            raise ValueError(message)
        table = catalog_table(rows, headers=rest.strip().splitlines()[0])
        path.write_text(f"{before}{start}\n\n{table}\n\n{end}{after}", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append")
    parser.add_argument("--output", type=Path, default=ROOT / "build/agent-distributions")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--in-place", action="store_true", help="Generate resources in repository plugin directories.")
    parser.add_argument(
        "--list", action="store_true", help="Report executable hook bindings and selected tool bindings."
    )
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    if not args.list and not args.in_place:
        require_runners()
    targets = load_targets()
    unknown = set(args.target or ()) - {target.target for target in targets}
    if unknown:
        parser.error(f"unknown targets: {sorted(unknown)}")
    targets = [target for target in targets if not args.target or target.target in args.target]
    if args.list:
        rows = catalog_rows(targets)
        if args.format == "markdown":
            print(catalog_table(rows))
            return
        print(
            json.dumps(
                {
                    "languages": dict(Counter(target.language for target in targets)),
                    "targets": rows,
                },
                indent=2,
            )
        )
        return
    for target in targets:
        if args.in_place:
            install_resources(target.target, ROOT / target.source / target.resource_dir, preserve_mcp=False)
            continue
        files = assemble(target)
        write_package(args.output / target.target, files, check=args.check)
        print(f"{target.target}: {len(files)} files, {len(target.hooks)} managed hook registrations")
    if args.in_place:
        update_catalog_docs(catalog_rows(load_targets()))


if __name__ == "__main__":
    main()
