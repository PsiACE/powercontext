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

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from powercontext_operations import cli
from powercontext_operations.adapters.systemd import SystemdUserAdapter
from powercontext_operations.model import DEFINITION_VERSION, OWNERSHIP_MARKER, ServiceDefinition


def test_operations_status_runs_without_runtime_dependencies(tmp_path: Path) -> None:
    environment = {
        **os.environ,
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }
    result = subprocess.run(
        [sys.executable, "-S", "-m", "powercontext_operations.cli", "status"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["registration"] == "not_installed"
    assert payload["server_liveness"] == "unknown"


def test_foreign_registration_is_retained_by_public_service_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = SystemdUserAdapter(config_home=tmp_path, identifier="powercontext-operations-test.service")
    adapter.artifact_path.parent.mkdir(parents=True)
    content = b"[Service]\nExecStart=/bin/false\n"
    adapter.artifact_path.write_bytes(content)
    monkeypatch.setattr(cli, "native_service_adapter", lambda: adapter)
    assert cli.main(["server", "stop"]) == 1
    assert adapter.artifact_path.read_bytes() == content


def test_doctor_reports_broken_runtime_instead_of_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    adapter = SystemdUserAdapter(config_home=tmp_path, identifier="powercontext-operations-test.service")
    definition = ServiceDefinition(
        ownership=OWNERSHIP_MARKER,
        definition_version=DEFINITION_VERSION,
        package_version="1.0.0",
        python_executable=str(tmp_path / "missing-python"),
        endpoint="http://127.0.0.1:1",
        data_dir=str(tmp_path / "data"),
    )
    adapter.write(adapter.render(definition))
    monkeypatch.setattr(cli, "native_service_adapter", lambda: adapter)
    assert cli.main(["doctor"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["registered_python_exists"] is False
    assert payload["runtime"]["cli_ready"] is False


@pytest.mark.parametrize("version", ["latest", "1.0;echo", "https://example.org/wheel.whl"])
def test_repair_rejects_unpinned_requirements(version: str) -> None:
    assert cli.main(["repair", "--target", "uv-tool", "--version", version, "--profile", "client"]) == 1


def test_repair_rejects_index_credentials() -> None:
    assert (
        cli.main([
            "repair",
            "--target",
            "uv-tool",
            "--version",
            "1.0.0",
            "--profile",
            "client",
            "--index-url",
            "https://user:secret@example.org/simple",
        ])
        == 1
    )


def test_failed_stop_preserves_owned_artifact_and_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from powercontext_operations.model import ServiceError

    adapter = SystemdUserAdapter(config_home=tmp_path, identifier="powercontext-operations-test.service")
    data = tmp_path / "data"
    data.mkdir()
    retained = data / "evidence"
    retained.write_bytes(b"retain")
    definition = ServiceDefinition(
        ownership=OWNERSHIP_MARKER,
        definition_version=DEFINITION_VERSION,
        package_version="1.0.0",
        python_executable=sys.executable,
        endpoint="http://127.0.0.1:1",
        data_dir=str(data),
    )
    adapter.write(adapter.render(definition))
    content = adapter.artifact_path.read_bytes()

    def fail_stop() -> None:
        raise ServiceError("controlled manager stop failure")  # noqa: TRY003

    monkeypatch.setattr(adapter, "stop", fail_stop)
    monkeypatch.setattr(cli, "native_service_adapter", lambda: adapter)
    assert cli.main(["server", "uninstall"]) == 1
    assert adapter.artifact_path.read_bytes() == content
    assert retained.read_bytes() == b"retain"


@pytest.mark.parametrize("ownership", ["owned", "foreign", "unknown"])
def test_repair_refuses_unsafe_loaded_manager_without_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ownership: str
) -> None:
    from powercontext_operations.model import ManagerOwnershipState, ManagerRegistration, ManagerState

    adapter = SystemdUserAdapter(config_home=tmp_path, identifier="powercontext-operations-test.service")
    monkeypatch.setattr(adapter, "loaded_registration", lambda: ManagerRegistration(ManagerOwnershipState(ownership)))
    monkeypatch.setattr(adapter, "manager_state", lambda: ManagerState.ACTIVE)
    monkeypatch.setattr(cli, "native_service_adapter", lambda: adapter)
    monkeypatch.setattr(cli, "tool_bin", lambda _uv: tmp_path / "bin")
    monkeypatch.setattr(cli.shutil, "which", lambda _command: sys.executable)

    def forbid_package_mutation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError

    monkeypatch.setattr(cli.subprocess, "run", forbid_package_mutation)
    assert cli.main(["repair", "--target", "uv-tool", "--version", "0.1.0", "--profile", "client"]) == 1
    assert not adapter.artifact_path.exists()
