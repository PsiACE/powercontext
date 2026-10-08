"""Run bounded shell-boundary ablations without network or user-state writes."""

import json
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CACHE = Path.home() / ".cache/powercontext-installation-research/bootstrap"


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
    source = (ROOT / "website/public/install.sh").read_text()
    results = []
    with tempfile.TemporaryDirectory(prefix="boundary-", dir=CACHE) as directory:
        work = Path(directory)
        tools = work / "tools"
        tools.mkdir()
        for name in ("uname", "mktemp", "rm", "dirname", "cat", "sh", "curl"):
            (tools / name).symlink_to(shutil.which(name))
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
                    git.symlink_to(shutil.which("git"))
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
        # Engine startup changes only the interpreter prerequisite: same Python-free PATH.
        for name, command in [
            ("shell_help", ["/bin/bash", str(ROOT / "website/public/install.sh"), "--help"]),
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
                    "baseline": "8b2ea957",
                },
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
