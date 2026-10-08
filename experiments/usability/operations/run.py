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

"""Measure maintenance boundaries without touching user service registrations."""

from __future__ import annotations

# Subprocesses and assertions execute the explicitly selected checkout experiment.
# ruff: noqa: S101, S603, S607
import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASELINE = "f28f8edfcb2972ac322f4f12b1f224226a9a40ab"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    options.scratch.mkdir(parents=True, exist_ok=True)
    baseline = BASELINE
    subprocess.run(["git", "diff", "--exit-code", baseline, "--", "src"], cwd=ROOT, check=True, capture_output=True)
    results = []
    with tempfile.TemporaryDirectory(prefix="ops-boundary-", dir=options.scratch) as temporary:
        scratch = Path(temporary)
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(scratch / "home"),
            "TMPDIR": str(scratch),
            "XDG_CONFIG_HOME": str(scratch / "config"),
            "POWERCONTEXT_HOME": str(scratch / "data"),
            "PYTHONPATH": str(ROOT / "src"),
            "LANG": "C.UTF-8",
        }
        for key in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
            if key in os.environ:
                env[key] = os.environ[key]
        (scratch / "home").mkdir()
        data = scratch / "data"
        data.mkdir()
        retained = data / "retained-evidence.txt"
        retained.write_text("Business evidence must survive maintenance.\n", encoding="utf-8")

        def run(name: str, code: str = "", *, standalone=False, expected=0, command=None, environment=None):
            started = time.monotonic()
            command = command or [options.python, *(["-S"] if standalone else []), "-c", code]
            completed = subprocess.run(
                command,
                cwd=scratch,
                env=env if environment is None else environment,
                capture_output=True,
                text=True,
                timeout=20,
            )
            row = {
                "name": name,
                "exit": completed.returncode,
                "expected_exit": expected,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
            for stream in ("stdout", "stderr"):
                row[stream] = row[stream].replace(str(scratch), "<scratch>").replace(str(ROOT), "<checkout>")
            results.append(row)
            assert completed.returncode == expected, row
            assert retained.read_text(encoding="utf-8") == "Business evidence must survive maintenance.\n"
            return completed

        run(
            "public_service_status_stopped_server",
            "from powercontext.service.cli import app; app(['status', '--json'])",
            expected=1,
        )
        blocker = """import importlib.abc, sys
class BrokenRuntime(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.startswith('powercontext.server'):
   raise ImportError('controlled Runtime import failure')
sys.meta_path.insert(0, BrokenRuntime())
"""
        run(
            "public_status_broken_runtime",
            blocker + "from powercontext.service.cli import app; app(['status', '--json'])",
            expected=1,
        )
        run(
            "public_cli_without_runtime_dependencies",
            "from powercontext.cli.app import create_cli; create_cli()(['--help'])",
            standalone=True,
            expected=1,
        )
        # Structural ablation: relocate exact native model/adapter source into a
        # sibling namespace. This is a prototype, not a shipped entry point.
        package = scratch / "independent_ops"
        (package / "adapters").mkdir(parents=True)
        for initializer in (package / "__init__.py", package / "adapters/__init__.py"):
            initializer.write_text("", encoding="utf-8")
        for relative in ("model.py", "_windows_command.py", "adapters/base.py", "adapters/systemd.py"):
            source = subprocess.check_output(
                ["git", "show", f"{baseline}:src/powercontext/service/{relative}"], cwd=ROOT, text=True
            )
            destination = package / relative
            destination.write_text(source.replace("powercontext.service.", "independent_ops."), encoding="utf-8")
        env["PYTHONPATH"] = str(scratch)
        inspect = """import json,sys
from pathlib import Path
from independent_ops.adapters.systemd import SystemdUserAdapter
from independent_ops.model import DEFINITION_VERSION,OWNERSHIP_MARKER,ServiceDefinition
adapter=SystemdUserAdapter(config_home=Path('registration'), identifier='powercontext-experiment.service')
definition=ServiceDefinition(ownership=OWNERSHIP_MARKER,definition_version=DEFINITION_VERSION,
 package_version='1.0.0',python_executable=sys.executable,endpoint='http://127.0.0.1:1',data_dir=str(Path('data').absolute()))
"""
        run(
            "isolated_native_definition_inspection",
            inspect
            + "adapter.write(adapter.render(definition)); print(json.dumps({'registration':adapter.inspect().state.value}))",
            standalone=True,
        )
        run(
            "isolated_foreign_definition_rejected",
            inspect
            + "adapter.artifact_path.write_text('[Service]\\nExecStart=/bin/false\\n'); print(json.dumps({'registration':adapter.inspect().state.value}))",
            standalone=True,
        )
        run(
            "isolated_missing_definition_detected",
            inspect
            + "adapter.artifact_path.unlink(); print(json.dumps({'registration':adapter.inspect().state.value}))",
            standalone=True,
        )
        # Same-wheel sibling entry needs neither dispatcher ownership nor an installation record.
        (package / "cli.py").write_text(
            "import argparse,json\nfrom pathlib import Path\nfrom independent_ops.adapters.systemd import SystemdUserAdapter\ndef main():\n parser=argparse.ArgumentParser();parser.add_argument('command',choices=['status']);parser.parse_args()\n adapter=SystemdUserAdapter(config_home=Path('registration'),identifier='powercontext-experiment.service')\n print(json.dumps({'registration':adapter.inspect().state.value}))\n",
            encoding="utf-8",
        )
        broken = scratch / "powercontext"
        broken.mkdir()
        (broken / "__init__.py").write_text(
            "raise ImportError('controlled Runtime package is broken')\n", encoding="utf-8"
        )
        launcher = scratch / "powercontext-ops"
        launcher.write_text("from independent_ops.cli import main\nmain()\n", encoding="utf-8")
        run(
            "same_environment_standalone_entry_broken_runtime",
            f"import runpy,sys;sys.argv=['powercontext-ops','status'];runpy.run_path({str(launcher)!r},run_name='__main__')",
            standalone=True,
        )
        wheels = scratch / "wheels"
        wheels.mkdir()
        wheel = wheels / "powercontext_ops_fixture-0.1.0-py3-none-any.whl"
        metadata = "powercontext_ops_fixture-0.1.0.dist-info"
        with zipfile.ZipFile(wheel, "w") as archive:
            for source in (*package.rglob("*.py"), broken / "__init__.py"):
                archive.write(source, str(source.relative_to(scratch)))
            archive.writestr(
                f"{metadata}/METADATA", "Metadata-Version: 2.1\nName: powercontext-ops-fixture\nVersion: 0.1.0\n"
            )
            archive.writestr(f"{metadata}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            archive.writestr(
                f"{metadata}/entry_points.txt", "[console_scripts]\npowercontext-ops = independent_ops.cli:main\n"
            )
            archive.writestr(f"{metadata}/RECORD", "")
        installed_env = {key: value for key, value in env.items() if key != "PYTHONPATH"}
        installed_env.update(
            UV_TOOL_DIR=str(scratch / "tools"),
            UV_TOOL_BIN_DIR=str(scratch / "bin"),
            UV_CACHE_DIR=str(scratch / "cache"),
        )
        uv = shutil.which("uv")
        assert uv
        run(
            "same_wheel_fixture_install",
            command=[
                uv,
                "tool",
                "install",
                "--python",
                options.python,
                "--no-python-downloads",
                "--no-index",
                "--find-links",
                str(wheels),
                "powercontext-ops-fixture==0.1.0",
            ],
            environment=installed_env,
        )
        result = run(
            "installed_same_wheel_ops_broken_runtime",
            command=[str(scratch / "bin/powercontext-ops"), "status"],
            environment=installed_env,
        )
        assert json.loads(result.stdout)["registration"] == "not_installed"
        budget = ROOT / "src/powercontext_service_bootstrap"

        shutil.copytree(
            budget, scratch / "powercontext_service_bootstrap", ignore=shutil.ignore_patterns("__pycache__")
        )
        bootstrap = """from pathlib import Path
from powercontext_service_bootstrap.__main__ import main
state=Path('retry-state.json');token=Path('retry-token');token.write_text('enabled')
args=['--retry-state',str(state),'--retry-token',str(token),'--retry-limit','2','--retry-window-seconds','60','--']
print('attempts=',[main(args),main(args)],'token_exists=',token.exists())
"""
        run("existing_retry_bootstrap_survives_missing_runtime", bootstrap, standalone=True)
        shutil.rmtree(broken)
        behavior_env = env | {"PYTHONPATH": str(ROOT / "src")}
        run(
            "existing_owned_service_behavior_contract",
            command=[
                options.python,
                "-m",
                "pytest",
                "-q",
                str(ROOT / "tests/test_service.py"),
                "-k",
                "uninstall_stops_before or uninstall_rejects_foreign or partial_failure or missing_recorded_python or service_status_json",
                "--basetemp=" + str(scratch / "pytest-native-contract"),
            ],
            environment=behavior_env,
        )
        native = subprocess.run(
            ["/usr/bin/systemctl", "--user", "is-system-running"],
            env=behavior_env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        native_observation = {
            "command": "systemctl --user is-system-running",
            "exit": native.returncode,
            "stdout": native.stdout,
            "stderr": native.stderr,
            "qualification": "Read-only actual manager availability; no native lifecycle mutation.",
        }
    options.output.write_text(
        json.dumps(
            {
                "baseline": baseline,
                "qualification": "Actual subprocess imports/status; controlled source relocation and registration files; no native manager mutation or Runtime repair claim.",
                "results": results,
                "native_manager_observation": native_observation,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(results)} maintenance boundary cases recorded")


if __name__ == "__main__":
    main()
