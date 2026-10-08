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


"""Public configuration contracts, including native cross-process file effects."""

import json
import os
import subprocess
import sys

import pytest

from powercontext.cli.config_operations import (
    ConfigurationInputError,
    apply_configuration,
    configuration_schema,
    inspect_configuration,
    plan_configuration,
    read_change,
    validate_resource,
)
from powercontext.local_config import ConfigurationConflictError

EXECUTABLE = os.environ.get("POWERCONTEXT_TEST_CONFIG_EXECUTABLE")


def request(path, changes, *, target="client", revision=None, unset=()):
    return read_change(
        json.dumps({
            "schema_version": 1,
            "target": target,
            "resource": str(path),
            "base_revision": revision or inspect_configuration(path, target)["revision"],
            "set": changes,
            "unset": list(unset),
        })
    )


def test_partial_plan_apply_private_readback(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    original = "# retained\nBUSINESS_CREDENTIAL=private-fixture\nPOWERCONTEXT_CLIENT_TIMEOUT=30\n"
    path.write_text(original)
    monkeypatch.setenv("FIXTURE_TOKEN", "fixture-token-value")
    document = request(path, {"timeout": 12.0, "api_token": {"from_env": "FIXTURE_TOKEN"}})
    plan = plan_configuration(document)
    assert path.read_text() == original
    assert "private-fixture" not in json.dumps(plan)
    assert "fixture-token-value" not in json.dumps(plan)
    result = apply_configuration(document)
    assert result["activation"] == "next_invocation"
    assert "BUSINESS_CREDENTIAL=private-fixture" in path.read_text()
    assert "# retained" in path.read_text()
    assert "fixture-token-value" in path.read_text()
    assert "fixture-token-value" not in json.dumps(inspect_configuration(path, "client"))
    assert validate_resource(path, "client")["checks"] == [{"kind": "static", "passed": True}]
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ConfigurationConflictError):
        apply_configuration(document)


@pytest.mark.parametrize(
    "changes",
    [{"timeuot": 1}, {"timeout": "12"}, {"api_token": "<redacted>"}, {"server_url": "http://untrusted.example"}],
)
def test_invalid_changes_have_no_effect(tmp_path, changes):
    path = tmp_path / ".env"
    path.write_text("BUSINESS_CREDENTIAL=private-fixture\n")
    before = path.read_bytes()
    with pytest.raises(ConfigurationInputError):
        apply_configuration(request(path, changes))
    assert path.read_bytes() == before


def test_server_invariants_and_runtime_environment_isolation(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_PORT", "not-a-port")
    valid = request(path, {"http.port": 8100}, target="server")
    assert plan_configuration(valid)["would_change"]
    with pytest.raises(ConfigurationInputError):
        apply_configuration(request(path, {"http.port": 99999}, target="server"))
    assert not path.exists()
    assert configuration_schema("server")["fields"]["http.port"]["schema"]["maximum"] == 65535


def command(*args):
    prefix = [EXECUTABLE] if EXECUTABLE else [sys.executable, "-c", "from powercontext.cli.app import main; main()"]
    return [*prefix, "config", *args]


def test_native_configuration_concurrency_and_safe_show(tmp_path):
    path = tmp_path / ".env"
    path.write_text("BUSINESS_CREDENTIAL=private-fixture\n")
    requests = []
    for value in (11.0, 12.0):
        file = tmp_path / f"{value}.json"
        file.write_text(request(path, {"timeout": value}).model_dump_json())
        requests.append(file)
    planned = subprocess.run(
        command("plan", "--request-file", str(requests[0]), "--json"), capture_output=True, text=True, check=True
    )
    assert json.loads(planned.stdout)["would_change"]
    assert path.read_text() == "BUSINESS_CREDENTIAL=private-fixture\n"
    processes = [
        subprocess.Popen(
            command("apply", "--request-file", str(file), "--json"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for file in requests
    ]
    outputs = [process.communicate(timeout=30) for process in processes]
    assert sorted(process.returncode for process in processes) == [0, 1]
    assert any("revision_conflict" in stdout for stdout, _ in outputs)
    result = subprocess.run(
        command("show", "--target", "client", "--env-file", str(path), "--json"),
        capture_output=True,
        text=True,
        check=True,
    )
    view = json.loads(result.stdout)
    assert view["fields"]["timeout"]["value"] in (11.0, 12.0)
    assert "private-fixture" not in result.stdout
    validated = subprocess.run(
        command("validate", "--target", "client", "--env-file", str(path), "--json"),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(validated.stdout)["checks"] == [{"kind": "static", "passed": True}]
    text = subprocess.run(
        command("show", "--target", "client", "--env-file", str(path)), capture_output=True, text=True, check=True
    )
    assert "private-fixture" not in text.stdout


def test_multiline_unknown_assignment_survives_partial_apply(tmp_path):
    path = tmp_path / ".env"
    original = "# retained\nBUSINESS_CREDENTIAL='first\nsecond'\nPOWERCONTEXT_CLIENT_TIMEOUT=30\n"
    path.write_text(original)
    apply_configuration(request(path, {"timeout": 14.0}))
    assert "BUSINESS_CREDENTIAL='first\nsecond'\n" in path.read_text()
    assert "first" not in json.dumps(inspect_configuration(path, "client"))
