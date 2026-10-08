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

"""Qualify installed Ops and controlled native lifecycle/repair boundaries."""

from __future__ import annotations

# The selected checkout, uv binary, disposable environment, and fixture commands are explicit.
# ruff: noqa: S101, S603
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    rows = []
    with tempfile.TemporaryDirectory(prefix="ops-qualified-", dir=args.scratch) as temporary:
        scratch = Path(temporary)
        env: dict[str, str] = {
            **os.environ,
            "HOME": str(scratch / "home"),
            "TMPDIR": str(scratch),
            "UV_TOOL_DIR": str(scratch / "tools"),
            "UV_TOOL_BIN_DIR": str(scratch / "bin"),
            "UV_CACHE_DIR": str(scratch / "cache"),
            "UV_NO_INDEX": "1",
            "UV_OFFLINE": "1",
            "XDG_CONFIG_HOME": str(scratch / "config"),
        }
        env.pop("PYTHONPATH", None)
        (scratch / "home").mkdir()
        data = scratch / "retained-data.txt"
        data.write_text("Retain user evidence.\n")

        def run(name: str, command: list[str], expected: int = 0, *, environment: dict[str, str] | None = None):
            started = time.monotonic()
            result = subprocess.run(
                command, env=environment or env, capture_output=True, text=True, timeout=60, check=False, cwd=scratch
            )
            row = {
                "name": name,
                "exit": result.returncode,
                "elapsed_ms": round(1000 * (time.monotonic() - started), 2),
                "stdout": result.stdout.replace(str(scratch), "<scratch>"),
                "stderr": result.stderr.replace(str(scratch), "<scratch>"),
            }
            rows.append(row)
            assert result.returncode == expected, row
            assert data.read_text() == "Retain user evidence.\n"
            return result

        run("actual_wheel_create_manual_env", ["uv", "venv", "--python", sys.executable, str(scratch / "actual-env")])
        actual_python = str(scratch / "actual-env/bin/python")
        run(
            "actual_wheel_install_without_dependencies",
            ["uv", "pip", "install", "--python", actual_python, "--no-deps", str(args.wheel.resolve())],
        )
        ops = str(scratch / "actual-env/bin/powercontext-ops")
        runtime = str(scratch / "actual-env/bin/powercontext")
        run("actual_wheel_runtime_dependencies_broken", [runtime, "--help"], 1)
        run("actual_wheel_installed_ops_status", [ops, "status"])
        run("actual_wheel_installed_ops_doctor", [ops, "doctor"])
        run("actual_wheel_installed_ops_logs", [ops, "server", "logs"])
        run("actual_wheel_owned_service_absent_refuses_stop", [ops, "server", "stop"], 1)
        run(
            "actual_wheel_offline_unavailable_repair_retains_launchers",
            [ops, "repair", "--target", "uv-tool", "--version", "99.0.0", "--profile", "client"],
            1,
        )
        run("actual_wheel_ops_still_usable_after_repair_failure", [ops, "status"])
        fixture = scratch / "powercontext-0.1.0-py3-none-any.whl"
        with zipfile.ZipFile(fixture, "w") as archive:
            for source in (ROOT / "src/powercontext_operations").rglob("*.py"):
                archive.write(source, str(source.relative_to(ROOT / "src")))
            archive.writestr("powercontext/__init__.py", "")
            archive.writestr("powercontext/cli/__init__.py", "")
            archive.writestr("powercontext/cli/app.py", "raise ImportError('controlled broken Runtime')\n")
            archive.writestr("powercontext/service/__init__.py", "")
            archive.writestr("powercontext/service/launcher.py", "import time\ntime.sleep(300)\n")
            prefix = "powercontext-0.1.0.dist-info"
            archive.writestr(
                f"{prefix}/METADATA",
                "Metadata-Version: 2.1\nName: powercontext\nVersion: 0.1.0\nProvides-Extra: cli\nProvides-Extra: server\n",
            )
            archive.writestr(f"{prefix}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            archive.writestr(
                f"{prefix}/entry_points.txt",
                "[console_scripts]\npowercontext-ops=powercontext_operations.cli:main\npowercontext=powercontext.cli.app:main\n",
            )
            archive.writestr(f"{prefix}/RECORD", "")
        env["UV_FIND_LINKS"] = str(scratch)
        repaired = run(
            "controlled_wheel_exact_named_uv_repair",
            [ops, "repair", "--target", "uv-tool", "--version", "0.1.0", "--profile", "client"],
        )
        payload = json.loads(repaired.stdout)
        assert payload["package_version"] == "0.1.0" and payload["runtime_cli_ready"] is False
        assert payload["service_action"] == "none"
        run("controlled_wheel_exact_repaired_ops_version", [str(scratch / "bin/powercontext-ops"), "--version"])
        run(
            "controlled_wheel_create_explicit_native_fixture_env",
            ["uv", "venv", "--python", sys.executable, str(scratch / "native-env")],
        )
        python = str(scratch / "native-env/bin/python")
        run(
            "controlled_wheel_install_native_fixture_env",
            ["uv", "pip", "install", "--python", python, "--no-deps", str(fixture)],
        )
        if sys.platform == "linux":
            identifier = "powercontext-ops-experiment-" + uuid.uuid4().hex + ".service"
            native_env = {**env, "XDG_CONFIG_HOME": os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))}
            code = f"""import json,sys,time
from pathlib import Path
from powercontext_operations.adapters.systemd import SystemdUserAdapter
from powercontext_operations.cli import service_operation,repair
from powercontext_operations.model import *
a=SystemdUserAdapter(identifier={identifier!r})
assert not a.artifact_path.exists()
assert a.loaded_registration().state is ManagerOwnershipState.NOT_LOADED
d=ServiceDefinition(ownership=OWNERSHIP_MARKER,definition_version=DEFINITION_VERSION,package_version='0.1.0',python_executable=sys.executable,endpoint='http://127.0.0.1:1',data_dir={str(scratch)!r})
try:
 a.write(a.render(d));a.reload()
 started=service_operation(a,'start');assert started['manager']=='active',started
 try:
  repair(a,exact='0.1.0',profile='client',index=None)
 except ServiceError as error:
  assert 'stop the owned Server' in str(error),str(error)
 else:
  raise AssertionError('active service repair was accepted')
 restarted=service_operation(a,'restart');assert restarted['manager']=='active',restarted
 stopped=service_operation(a,'stop');assert stopped['manager']=='inactive',stopped
 removed=service_operation(a,'uninstall');assert removed['registration']=='not_installed',removed
 print(json.dumps({{'start':started,'restart':restarted,'stop':stopped,'uninstall':removed}}))
finally:
 if a.inspect().state is RegistrationState.INSTALLED:
  a.stop();a.disable();a.remove();a.reload()
"""
            run(
                "actual_systemd_controlled_process_owned_start_stop_uninstall",
                [python, "-c", code],
                environment=native_env,
            )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "wheel": args.wheel.name,
                "qualification": "actual wheel without dependencies; controlled wheel repair; real Linux manager with fixture process",
                "cases": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
