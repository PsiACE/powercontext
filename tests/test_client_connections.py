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


"""Observable connection source, persistence, credential and project behavior."""
# ruff: noqa: S106 - synthetic environment-variable references, not credentials.

import asyncio
import json
import os
import subprocess
import sys

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.client import PowerContextClient
from powercontext.client.connections import (
    bind_project,
    configure_connection,
    inspect_connection,
    inspect_project,
    project_binding_key,
    unbind_project,
)
from powercontext.client.settings import ClientSettings
from powercontext.local_config import ConfigurationConflictError
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings

CLIENT_EXECUTABLE = os.environ.get("POWERCONTEXT_TEST_CLIENT_EXECUTABLE")


@pytest.fixture
def connection_file(tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("POWERCONTEXT_"):
            monkeypatch.delenv(name)
    path = tmp_path / "clients.json"
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(path))
    return path


def test_saved_connection_readback_and_endpoint_bound_credential(connection_file, monkeypatch):
    monkeypatch.setenv("PROJECT_TOKEN", "synthetic-private-token")
    result = configure_connection(
        "client",
        server_url="https://one.example",
        allow_insecure_http=False,
        expected_revision="missing",
        api_token_env="PROJECT_TOKEN",
    )
    assert result["server_url_source"] == "saved"
    assert result["credential"] == {"reference": "PROJECT_TOKEN", "state": "available"}
    assert "synthetic-private-token" not in json.dumps(result)
    token = ClientSettings().api_token
    assert token is not None and token.get_secret_value() == "synthetic-private-token"
    assert ClientSettings(server_url="https://two.example").api_token is None
    if os.name != "nt":
        assert connection_file.stat().st_mode & 0o777 == 0o600
    updated = configure_connection(
        "client", server_url="https://two.example", allow_insecure_http=False, expected_revision=result["revision"]
    )
    assert updated["credential"]["reference"] is None
    assert "api_token_env" not in json.loads(connection_file.read_text())["hosts"]["client"]


def test_missing_reference_fails_before_request_and_direct_token_keeps_precedence(connection_file, monkeypatch):
    configure_connection(
        "client",
        server_url="https://one.example",
        allow_insecure_http=False,
        expected_revision="missing",
        api_token_env="UNAVAILABLE_TOKEN",
    )
    with pytest.raises(ValidationError, match="credential reference is unavailable"):
        ClientSettings()
    monkeypatch.setenv("POWERCONTEXT_CLIENT_API_TOKEN", "synthetic-direct")
    token = ClientSettings().api_token
    assert token is not None and token.get_secret_value() == "synthetic-direct"


def test_plaintext_consent_and_environment_conflicts_preserve_file(connection_file, monkeypatch):
    with pytest.raises(ValueError, match="loopback"):
        configure_connection(
            "codex", server_url="http://remote.example", allow_insecure_http=False, expected_revision="missing"
        )
    assert not connection_file.exists()
    saved = configure_connection(
        "codex", server_url="http://remote.example", allow_insecure_http=True, expected_revision="missing"
    )
    monkeypatch.setenv("POWERCONTEXT_CODEX_SERVER_URL", "https://override.example")
    current = inspect_connection("codex")
    assert current["server_url_source"] == "POWERCONTEXT_CODEX_SERVER_URL"
    assert current["allow_insecure_http"] is False
    before = connection_file.read_bytes()
    with pytest.raises(ValueError, match="conflicts"):
        configure_connection(
            "codex", server_url="https://chosen.example", allow_insecure_http=False, expected_revision=saved["revision"]
        )
    assert connection_file.read_bytes() == before


def test_stale_revision_preserves_unrelated_hosts(connection_file):
    first = configure_connection(
        "codex", server_url="https://first.example", allow_insecure_http=False, expected_revision="missing"
    )
    configure_connection(
        "client", server_url="https://second.example", allow_insecure_http=False, expected_revision=first["revision"]
    )
    before = connection_file.read_bytes()
    with pytest.raises(ConfigurationConflictError):
        configure_connection(
            "codex", server_url="https://third.example", allow_insecure_http=False, expected_revision=first["revision"]
        )
    assert connection_file.read_bytes() == before
    assert inspect_connection("codex")["server_url"] == "https://first.example"


def test_native_cli_concurrent_configure_and_restart_readback(connection_file):
    environment = dict(os.environ)
    base = (
        [CLIENT_EXECUTABLE]
        if CLIENT_EXECUTABLE
        else [sys.executable, "-c", "from powercontext.cli.app import main; main()"]
    ) + ["connection"]
    initial = subprocess.run([*base, "inspect"], env=environment, text=True, capture_output=True, check=True)
    revision = json.loads(initial.stdout)["revision"]
    writers = [
        subprocess.Popen(
            [*base, "configure", "--server-url", f"https://{name}.example", "--expected-revision", revision],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for name in ["one", "two"]
    ]
    results = [writer.communicate(timeout=30) for writer in writers]
    assert sorted(writer.returncode for writer in writers) == [0, 1]
    assert all("synthetic-private" not in out + err for out, err in results)
    readback = subprocess.run([*base, "inspect"], env=environment, text=True, capture_output=True, check=True)
    assert json.loads(readback.stdout)["server_url"] in {"https://one.example", "https://two.example"}


def test_project_bind_inspect_unbind_and_server_switch_use_actual_scope_api(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    apps = [
        create_server_app(
            settings=ServerSettings(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / (str(index) + '.db')}"),
                mcp=McpConfig(enabled=False),
            )
        )
        for index in range(2)
    ]
    with TestClient(apps[0]) as first, TestClient(apps[1]):
        scope = first.post(
            "/v1/scopes", json={"title": "Project", "summary": "fixture", "idempotency_key": "project"}
        ).json()["scope_id"]

        async def journey():
            async with (
                httpx.AsyncClient(transport=httpx.ASGITransport(app=apps[0])) as http,
                PowerContextClient("https://first.example", http_client=http) as client,
            ):
                acknowledged = await bind_project(client, project, scope)
                assert acknowledged["scope_id"] == scope
                assert (await inspect_project(client, project))["scope"]["scope_id"] == scope
                async with (
                    httpx.AsyncClient(transport=httpx.ASGITransport(app=apps[1])) as other_http,
                    PowerContextClient("https://second.example", http_client=other_http) as other,
                ):
                    from powercontext.client.errors import ServerResponseError

                    with pytest.raises(ServerResponseError):
                        await inspect_project(other, project)
                assert (await unbind_project(client, project))["cleared"] is True
                assert (await unbind_project(client, project))["cleared"] is False

        asyncio.run(journey())


def test_project_key_keeps_checkout_subdirectory_and_separate_worktrees_distinct(tmp_path):
    import shutil

    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is required for the worktree identity contract")
    checkout = tmp_path / "checkout"
    subprocess.run([git, "init", "-q", str(checkout)], check=True)
    subprocess.run(
        [
            git,
            "-C",
            str(checkout),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    subdirectory = checkout / "nested"
    subdirectory.mkdir()
    worktree = tmp_path / "worktree"
    subprocess.run([git, "-C", str(checkout), "worktree", "add", "-qb", "other", str(worktree)], check=True)
    assert project_binding_key(checkout) == project_binding_key(subdirectory)
    assert project_binding_key(checkout) != project_binding_key(worktree)
