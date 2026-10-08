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


"""Execute connection identity ablations against PowerContext baseline f28f8edf."""
# ruff: noqa: S101, S603, S607

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

# Historical f28f8edf import: execute in the pinned cce34000 worktree documented beside this harness.
from powercontext.cli.env_file import environment_context  # ty: ignore[unresolved-import]

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.cli.transport import resolve_setup_endpoint
from powercontext.client.transport_policy import resolve_client_transport
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    observations = []
    with tempfile.TemporaryDirectory(dir=args.scratch) as temporary:
        root = Path(temporary)
        config = root / "clients.json"
        config.write_text(
            json.dumps({
                "version": 1,
                "hosts": {"codex": {"server_url": "http://old.example", "allow_insecure_http": True}},
            })
        )
        clear = {name for name in os.environ if name.startswith("POWERCONTEXT_")}
        with environment_context({"POWERCONTEXT_CLIENT_CONFIG_FILE": str(config)}, clear=clear):
            same = resolve_client_transport("codex", server_url="http://old.example/mcp")
            switched = resolve_client_transport("codex", server_url="http://new.example")
            assert same[1] is True and switched[1] is False
            observations.append({
                "case": "endpoint_bound_consent",
                "same_endpoint_allowed": same[1],
                "changed_endpoint_allowed": switched[1],
                "without_endpoint_binding_allowed": True,
            })
            with environment_context({"POWERCONTEXT_CLIENT_SERVER_URL": "https://runtime.example"}):
                chosen = resolve_client_transport("codex", server_url="https://chosen.example")[0]
                try:
                    resolve_setup_endpoint("codex", server_url="https://chosen.example")
                except ValueError:
                    conflict = True
                else:
                    conflict = False
                assert conflict
                observations.append({
                    "case": "runtime_override_conflict",
                    "request_endpoint": chosen,
                    "persistent_setup_rejected": conflict,
                })
        checkout = root / "checkout"
        checkout.mkdir()
        subprocess.run(["git", "init", "-q", str(checkout)], check=True)
        subprocess.run(
            [
                "git",
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
        other = root / "worktree"
        subprocess.run(["git", "-C", str(checkout), "worktree", "add", "-qb", "other", str(other)], check=True)
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "integrations/codex/plugins/powercontext/scripts"))
        import scope_binding

        key = scope_binding.workspace_binding_key(str(checkout))
        different = scope_binding.workspace_binding_key(str(other))
        assert key != different
        observations.append({"case": "separate_worktrees", "same_binding_key": key == different})
        apps = [
            create_server_app(
                settings=ServerSettings(
                    database=SQLiteConfig(url=f"sqlite+aiosqlite:///{root / (str(index) + '.db')}"),
                    mcp=McpConfig(enabled=False),
                )
            )
            for index in range(2)
        ]
        with TestClient(apps[0]) as first, TestClient(apps[1]) as second:
            first_scope = first.post(
                "/v1/scopes", json={"title": "Project", "summary": "fixture", "idempotency_key": "project"}
            ).json()["scope_id"]
            assert first.put("/v1/scope-bindings", json={"key": key, "scope_id": first_scope}).status_code == 200
            resolved = second.post("/v1/scope-bindings/resolve", json={"binding_keys": [key], "allow_default": False})
            stale = second.put("/v1/scope-bindings", json={"key": key, "scope_id": first_scope})
            assert resolved.status_code == 404 and stale.status_code == 404
            observations.append({
                "case": "server_owned_scope_identity",
                "new_server_binding_status": resolved.status_code,
                "old_scope_on_new_server_status": stale.status_code,
            })
    args.output.write_text(json.dumps({"baseline": "f28f8edf", "cases": observations}, indent=2) + "\n")


if __name__ == "__main__":
    main()
