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

"""Run bounded shell-boundary ablations without network or user-state writes."""

import argparse
import json
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CACHE = Path.home() / ".cache/powercontext-installation-research/bootstrap"


def executable(name: str) -> str:
    """Require a native experiment prerequisite with a clear diagnostic."""
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f"Required experiment executable is unavailable: {name}")  # noqa: TRY003
    return path


def runtime_only(source: str) -> str:
    """Remove only host orchestration, retaining package/bootstrap policy."""
    start = source.index('    if [[ "$NO_HOSTS" == false && ${#HOSTS[@]} -eq 0 && ! -t 0 ]]; then')
    end = source.index("\n}\n\ndownload()", start)
    source = source[:start] + source[end:]
    start = source.index('    if [[ "$NO_HOSTS" == false ]]; then\n        command -v git')
    end = source.index("    TEMP_DIR=", start)
    source = source[:start] + source[end:]
    start = source.index("    local setup_status=0")
    end = source.index('    if [[ "$PROFILE" == local ]]; then', start)
    source = source[:start] + source[end:]
    start = source.index("    if ((setup_status != 0)); then")
    end = source.index("\n}", start)
    return source[:start] + source[end:]


def explicit_hosts(source: str) -> str:
    """Keep selected-host setup while removing implicit host prompting."""
    start = source.index('    if [[ "$NO_HOSTS" == false && ${#HOSTS[@]} -eq 0 && ! -t 0 ]]; then')
    end = source.index("\n}\n\ndownload()", start)
    source = source[:start] + source[end:]
    return source.replace('if [[ "$NO_HOSTS" == false ]]; then', "if [[ ${#HOSTS[@]} -gt 0 ]]; then")


def main() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="8b2ea957", help="Git revision containing the installer")
    baseline = parser.parse_args().baseline
    revision = subprocess.check_output([executable("git"), "rev-parse", baseline], cwd=ROOT, text=True).strip()  # noqa: S603
    source = subprocess.check_output(  # noqa: S603 - revision is resolved locally
        [executable("git"), "show", f"{revision}:website/public/install.sh"], cwd=ROOT, text=True
    )
    results = []
    with tempfile.TemporaryDirectory(prefix="boundary-", dir=CACHE) as directory:
        work = Path(directory)
        tools = work / "tools"
        tools.mkdir()
        for name in ("uname", "mktemp", "rm", "dirname", "cat", "sh", "curl"):
            (tools / name).symlink_to(executable(name))
        trace = work / "trace"
        installed = work / "installed"
        installed.mkdir()
        cli = installed / "powercontext"
        cli.write_text(
            '#!/bin/sh\nif [ "$1" = --version ]; then echo 1.2.0; else echo setup >> "$TRACE"; exit "${HOST_STATUS:-0}"; fi\n'
        )
        cli.chmod(0o755)
        uv = tools / "uv"
        uv.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$*" >> "$TRACE"\ncase "$1 $2" in\n"python find") echo /fixture/python;;\n"tool dir") echo "$TOOL_BIN";;\n"tool install") echo installed > "$INSTALL_STATE";;\nesac\n'
        )
        uv.chmod(0o755)
        environment = {
            "PATH": str(tools),
            "HOME": str(work),
            "TMPDIR": str(work),
            "TRACE": str(trace),
            "TOOL_BIN": str(installed),
            "INSTALL_STATE": str(work / "state"),
            "UV_DEFAULT_INDEX": "https://packages.invalid/simple",
            "TZ": "UTC",
            "LANG": "C",
        }
        scripts = {"baseline": source, "runtime_only": runtime_only(source), "explicit_hosts": explicit_hosts(source)}
        for scenario, args in [
            ("no_choice", []),
            ("runtime_explicit", ["--no-hosts"]),
            ("host_failure", ["--host", "codex"]),
        ]:
            for variant, script in scripts.items():
                # Add Git only to host-failure inputs; both variants receive identical tools.
                git = tools / "git"
                if scenario == "host_failure" and not git.exists():
                    git.symlink_to(executable("git"))
                trace.write_text("")
                (work / "state").unlink(missing_ok=True)
                path = work / "install.sh"
                path.write_text(script)
                result = subprocess.run(  # noqa: S603 - commands and arguments are fixed experiment fixtures
                    ["/bin/bash", str(path), *args],
                    env=environment | {"HOST_STATUS": "17"},
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                results.append({
                    "scenario": scenario,
                    "variant": variant,
                    "status": result.returncode,
                    "runtime_written": (work / "state").exists(),
                    "commands": trace.read_text().splitlines(),
                    "error": result.stderr.strip(),
                })
        (work / "help.sh").write_text(source)
        # Engine startup changes only the interpreter prerequisite: same Python-free PATH.
        for name, command in [
            ("shell_help", ["/bin/bash", str(work / "help.sh"), "--help"]),
            ("python_engine_help", ["/bin/sh", "-c", 'python3 -c "print(123)"']),
        ]:
            result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15)  # noqa: S603
            results.append({"scenario": name, "status": result.returncode, "error": result.stderr.strip()})
    print(
        json.dumps(
            {
                "environment": {
                    "os": platform.platform(),
                    "python_harness": platform.python_version(),
                    "bash": subprocess.check_output(["/bin/bash", "--version"], text=True).splitlines()[0],
                    "baseline": revision,
                },
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
