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

"""Render declared plugin Skill projections without changing native adapters."""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "integrations/distribution/skills/targets.json"


def relative_path(value: str) -> Path:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
        raise ValueError(f"Expected contained relative resource path: {value}")  # noqa: TRY003
    return Path(path)


def render_skill(root: Path, target: Mapping[str, Any]) -> dict[str, bytes]:
    """Resolve a complete explicit projection before touching any output."""
    files = {}
    for name, source in target["files"].items():
        relative_path(name)
        path = root / relative_path(source)
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Resource source escapes selected checkout: {source}")  # noqa: TRY003
        files[name] = path.read_bytes()
    validate_skill(files)
    retired = set(target.get("retired", []))
    for name in retired:
        relative_path(name)
    if retired.intersection(files):
        raise ValueError("A resource cannot be both rendered and retired")  # noqa: TRY003
    return files


def validate_skill(files: Mapping[str, bytes]) -> None:
    """Validate discovery metadata and every declared local Markdown reference."""
    entry = files.get("SKILL.md", b"").decode("utf-8")
    frontmatter = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", entry, re.DOTALL)
    metadata = yaml.safe_load(frontmatter.group(1)) if frontmatter else None
    if (
        not isinstance(metadata, dict)
        or metadata.get("name") != "powercontext-project-context"
        or not metadata.get("description")
    ):
        raise ValueError("Skill projection lacks discoverable entry metadata")  # noqa: TRY003
    for name, data in files.items():
        if not name.endswith(".md"):
            continue
        for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", data.decode("utf-8")):
            parsed = urlsplit(link)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            resource = posixpath.normpath((PurePosixPath(name).parent / unquote(parsed.path)).as_posix())
            if resource not in files:
                raise ValueError(f"Incomplete Skill projection: {name} references {resource}")  # noqa: TRY003


def inspect_output(destination: Path, files: Mapping[str, bytes], retired: list[str]) -> list[str]:
    """Find resource drift and refuse symlink traversal before any writes."""
    changed = []
    for name in [*files, *retired]:
        path = destination / relative_path(name)
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise ValueError(f"Refusing symlink output path: {path}")  # noqa: TRY003
        if name in retired:
            if path.exists():
                if not path.is_file():
                    raise ValueError(f"Retired resource is not a regular file: {path}")  # noqa: TRY003
                changed.append(name)
        elif not path.is_file() or path.read_bytes() != files[name]:
            if path.exists() and not path.is_file():
                raise ValueError(f"Resource destination is not a regular file: {path}")  # noqa: TRY003
            changed.append(name)
    return changed


def write_output(destination: Path, files: Mapping[str, bytes], retired: list[str]) -> None:
    """Write only declared outputs after complete rendering and path inspection."""
    inspect_output(destination, files, retired)
    for name, data in files.items():
        path = destination / relative_path(name)
        if path.is_file() and path.read_bytes() == data:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as staging:
            staging.write(data)
            temporary = Path(staging.name)
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    for name in retired:
        (destination / relative_path(name)).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", help="Declared target; repeatable, defaults to all")
    parser.add_argument("--output", type=Path, help="Build root; each target gets a separate complete Skill directory")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Refresh declared generated repository files")
    mode.add_argument("--check", action="store_true", help="Inspect declared outputs without writes (default)")
    options = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        parser.error("Unsupported Skill projection manifest")
    targets = options.target or list(manifest["targets"])
    # Validate every selected projection and path before changing the first output.
    planned = []
    for name in targets:
        if name not in manifest["targets"]:
            parser.error(f"Unknown Skill target: {name}")
        target = manifest["targets"][name]
        files = render_skill(ROOT, target)
        destination = (
            options.output / name / "powercontext-project-context"
            if options.output
            else ROOT / relative_path(target["destination"])
        )
        retired = target.get("retired", [])
        changed = inspect_output(destination, files, retired)
        planned.append((name, destination, files, retired, changed))
    failed = False
    for name, destination, files, retired, changed in planned:
        if options.write or (options.output and not options.check):
            write_output(destination, files, retired)
        elif changed:
            print(f"{name}: Skill resources differ: {', '.join(changed)}")
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
