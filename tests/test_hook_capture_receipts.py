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

"""Standalone Python hooks checkpoint only exact acknowledged captures."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest
from typing_extensions import override

ROOT = Path(__file__).resolve().parents[1]
HOSTS = {
    "codex": ("recall.py", "CODEX"),
    "claude-code": ("user_prompt_submit.py", "CLAUDE"),
    "workbuddy": ("workbuddy_powercontext_hook.py", "WORKBUDDY"),
}
CASES = json.loads((ROOT / "tests/fixtures/hooks/capture_receipts.json").read_text())["cases"]


def receipt(mode: str, source_id: str) -> dict[str, Any]:
    value: dict[str, Any] = {"status": "accepted", "source": {"name": "content", "source_id": source_id}, "position": 7}
    if mode == "position_only":
        return {"position": 7}
    if mode == "missing_status":
        del value["status"]
    elif mode == "rejected":
        value["status"] = "rejected"
    elif mode == "wrong_source":
        value["source"]["source_id"] = "unrelated-source"
    elif mode == "wrong_type":
        value["source"]["name"] = "unknown"
    elif mode == "boolean_position":
        value["position"] = True
    elif mode == "zero_position":
        value["position"] = 0
    elif mode == "extra_field":
        value["unexpected"] = "synthetic"
    return value


def assert_native_output(stdout: str, host: str, case: dict[str, Any], prompt: str, address: str) -> None:
    """Verify empty injection and the host's existing content-free failure channel."""
    output = json.loads(stdout) if stdout else {}
    assert not output.get("hookSpecificOutput", {}).get("additionalContext")
    if not case["checkpoint"] and case.get("capture", True) and host != "workbuddy":
        assert "systemMessage" in output
        diagnostic = json.loads(output["systemMessage"])
        assert diagnostic["event"] == "capture_source"
        assert diagnostic["outcome"] == case.get("diagnostic", "invalid_response")
        assert prompt not in output["systemMessage"]
        assert address not in output["systemMessage"]


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_hook_capture_receipt(host: str, case: dict[str, Any], tmp_path: Path) -> None:
    captured = []
    checkpoints = []

    class Server(BaseHTTPRequestHandler):
        @override
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/v1/scope-bindings/resolve":
                result = {"scope_id": "project:contract"}
            elif self.path == "/v1/context/prepare":
                result = {
                    "schema": "powercontext.prepared-context.v1",
                    "status": "empty",
                    "content": None,
                    "content_bytes": 0,
                }
            elif self.path == "/v1/sources/content":
                captured.append(payload)
                if case["receipt"] == "lost_response":
                    self.close_connection = True
                    return
                result = receipt(case["receipt"], payload["source_id"])
            elif self.path == "/v1/memory/flush":
                checkpoints.append(payload)
                result = {"current_cursor": 7}
            else:
                self.send_error(404)
                return
            content = (
                b"undecodable receipt"
                if self.path == "/v1/sources/content" and case["receipt"] == "malformed_json"
                else json.dumps(result).encode()
            )
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    plugin = tmp_path / "plugin"
    shutil.copytree(
        ROOT / f"integrations/{host}/plugins/powercontext",
        plugin,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.pyc"),
    )
    project = tmp_path / "project"
    project.mkdir()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    filename, prefix = HOSTS[host]
    address = f"http://127.0.0.1:{server.server_port}"
    if host == "codex":
        path = plugin / ".mcp.json"
        configuration = json.loads(path.read_text())
        configuration["mcpServers"]["powercontext"]["url"] = address + "/mcp/"
        path.write_text(json.dumps(configuration))
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POWERCONTEXT", "CLAUDE_", "CODEX_", "WORKBUDDY_", "PYTHON"))
    }
    environment.update({
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "PYTHONIOENCODING": "utf-8",
        "POWERCONTEXT_DIAGNOSTIC_STATE_FILE": str(tmp_path / "diagnostic.json"),
        f"POWERCONTEXT_{prefix}_SERVER_URL": address,
        f"POWERCONTEXT_{prefix}_SCOPE_ID": "project:contract",
        f"POWERCONTEXT_{prefix}_FLUSH_ON_CAPTURE": "true",
        f"POWERCONTEXT_{prefix}_CAPTURE_PROMPTS": str(case.get("capture", True)).lower(),
    })
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "cwd": str(project),
        "prompt": "private synthetic prompt",
        "session_id": "session",
        "turn_id": "turn",
        "prompt_id": "prompt",
    }
    command = [sys.executable, *(["-S"] if host == "claude-code" else []), str(plugin / "hooks" / filename)]
    try:
        for _ in range(case.get("repetitions", 1)):
            completed = subprocess.run(
                command, input=json.dumps(payload), env=environment, text=True, capture_output=True, timeout=10
            )
            assert completed.returncode == 0, completed.stderr
            assert_native_output(completed.stdout, host, case, payload["prompt"], address)
        assert len(captured) == (case.get("repetitions", 1) if case.get("capture", True) else 0)
        assert bool(checkpoints) == case["checkpoint"]
        assert len({item["source_id"] for item in captured}) <= 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
