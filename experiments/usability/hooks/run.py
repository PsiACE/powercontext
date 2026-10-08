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

"""Execute pinned Python Hook adapters against bounded synthetic HTTP events."""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import subprocess
import sys
import tarfile
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import ClassVar

from typing_extensions import override

ROOT = Path(__file__).resolve().parents[3]
BASELINE = "f28f8edfcb2972ac322f4f12b1f224226a9a40ab"
HOSTS = {"codex": ("hooks/recall.py", "CODEX"), "claude-code": ("hooks/user_prompt_submit.py", "CLAUDE")}


def strict_receipt(source: str) -> str:
    start = source.index("def _capture_prompt(")
    end = source.index("\ndef _flush_through(", start)
    block = source[start:end].replace("    return _post_json(", "    response = _post_json(", 1)
    block += """    reference = response.get("source")
    if (set(response) != {"status", "source", "position"}
        or response.get("status") != "accepted"
        or not isinstance(reference, dict)
        or reference != {"name": "content", "source_id": source_id}):
        raise TypeError
    return response

"""
    return source[:start] + block + source[end:]


class Server(BaseHTTPRequestHandler):
    receipt = "valid"
    writes: ClassVar[list[str]] = []
    captures: ClassVar[list[dict[str, object]]] = []

    @override
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.writes.append(self.path)
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
            self.captures.append(payload)
            if self.receipt == "position_only":
                result = {"position": 7}
            else:
                result = {
                    "status": "accepted",
                    "position": 7,
                    "source": {
                        "name": "content",
                        "source_id": payload["source_id"] if self.receipt == "valid" else "unrelated-source",
                    },
                }
        elif self.path == "/v1/memory/flush":
            result = {"current_cursor": 7}
        else:
            self.send_error(404)
            return
        data = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default=BASELINE)
    baseline = parser.parse_args().baseline
    cache = Path.home() / ".cache/powercontext-usability/hooks"
    cache.mkdir(parents=True, exist_ok=True)
    archive = subprocess.check_output(  # noqa: S603 - fixed Git command and explicit source selection
        ["/usr/bin/git", "archive", baseline, *[f"integrations/{host}/plugins/powercontext" for host in HOSTS]],
        cwd=ROOT,
    )
    results = []
    with tempfile.TemporaryDirectory(dir=cache) as directory:
        work = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(archive)) as snapshot:
            snapshot.extractall(work, filter="data")
        project = work / "project"
        project.mkdir()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        mcp = work / "integrations/codex/plugins/powercontext/.mcp.json"
        configuration = json.loads(mcp.read_text())
        configuration["mcpServers"]["powercontext"]["url"] = f"http://127.0.0.1:{server.server_port}/mcp/"
        mcp.write_text(json.dumps(configuration))
        try:
            for host, (relative, prefix) in HOSTS.items():
                path = work / f"integrations/{host}/plugins/powercontext" / relative
                original = path.read_text()
                for receipt in ("valid", "position_only", "wrong_source"):
                    for variant, source in (("native", original), ("receipt_contract", strict_receipt(original))):
                        path.write_text(source)
                        Server.receipt = receipt
                        Server.writes = []
                        Server.captures = []
                        env = {
                            key: value
                            for key, value in os.environ.items()
                            if not key.startswith(("POWERCONTEXT", "CLAUDE_", "CODEX_"))
                        }
                        env.update({
                            "HOME": str(work),
                            "USERPROFILE": str(work),
                            "POWERCONTEXT_DIAGNOSTIC_STATE_FILE": str(work / f"{host}-{receipt}-{variant}.json"),
                            f"POWERCONTEXT_{prefix}_SERVER_URL": f"http://127.0.0.1:{server.server_port}",
                            f"POWERCONTEXT_{prefix}_SCOPE_ID": "project:contract",
                            f"POWERCONTEXT_{prefix}_FLUSH_ON_CAPTURE": "true",
                        })
                        payload = {
                            "hook_event_name": "UserPromptSubmit",
                            "cwd": str(project),
                            "prompt": "synthetic prompt",
                            "session_id": "session",
                            "turn_id": "turn",
                            "prompt_id": "prompt",
                        }
                        process = subprocess.run(  # noqa: S603 - isolated pinned adapter under test
                            [sys.executable, str(path)],
                            input=json.dumps(payload),
                            text=True,
                            capture_output=True,
                            env=env,
                            timeout=10,
                        )
                        output = json.loads(process.stdout) if process.stdout else {}
                        results.append({
                            "host": host,
                            "receipt": receipt,
                            "variant": variant,
                            "exit": process.returncode,
                            "capture_received": bool(Server.captures),
                            "checkpoint_requested": "/v1/memory/flush" in Server.writes,
                            "diagnostic": json.loads(output["systemMessage"]) if "systemMessage" in output else None,
                        })
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    print(
        json.dumps(
            {
                "baseline": baseline,
                "python": platform.python_version(),
                "platform": platform.platform(),
                "qualification": "native subprocess adapters; synthetic HTTP Server; no actual Agent host",
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
