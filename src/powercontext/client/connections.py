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


"""Inspectable v1 Client connections and explicit existing project Scope bindings."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from shutil import which
from typing import Any

from powercontext.client.client import PowerContextClient
from powercontext.client.settings import normalize_server_url
from powercontext.client.transport_policy import (
    client_config_file,
    inspect_client_transport,
    normalize_client_url,
    parse_client_settings,
)
from powercontext.http import (
    ClearScopeBindingRequest,
    ResolveScopeBindingRequest,
    ScopeBinding,
    ScopeBindingKey,
    SetScopeBindingRequest,
)
from powercontext.local_config import configuration_lock, publish_configuration, read_configuration

HOSTS = ("client", "codex", "claude-code", "dsh", "openclaw", "opencode", "pi", "hermes", "workbuddy")


def inspect_connection(host: str = "client") -> dict[str, Any]:
    """Read non-secret saved and selected transport state without network calls."""
    _validate_host(host)
    path = client_config_file().resolve()
    content, revision = read_configuration(path)
    raw = json.loads(content) if content is not None else {"version": 1, "hosts": {}}
    saved = parse_client_settings(raw, host)
    selected = inspect_client_transport(host, saved_settings=saved)
    reference = saved.get("api_token_env")
    applicable = saved.get("server_url") == selected.server_url
    return {
        "schema_version": 1,
        "host": host,
        "resource": str(path),
        "revision": revision,
        **asdict(selected),
        "saved": {key: value for key, value in saved.items() if key != "api_token_env"},
        "credential": {
            "reference": reference,
            "state": "not_applicable"
            if reference and not applicable
            else "available"
            if reference and os.environ.get(reference)
            else "missing"
            if reference
            else "unset",
        },
        "activation": "unknown",
    }


def configure_connection(
    host: str,
    *,
    server_url: str,
    allow_insecure_http: bool,
    expected_revision: str,
    api_token_env: str | None = None,
    clear_token_reference: bool = False,
) -> dict[str, Any]:
    """Persist one v1 entry after rejecting conflicting runtime overrides and stale revisions."""
    _validate_host(host)
    if not isinstance(allow_insecure_http, bool):
        raise ValueError("Consent must be a boolean")  # noqa: TRY003, TRY004
    endpoint = (
        normalize_server_url(server_url, allow_insecure_http=allow_insecure_http).removesuffix("/mcp").rstrip("/")
    )
    if api_token_env is not None and (
        host != "client" or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_token_env) is None
    ):
        raise ValueError("Token references must name an environment variable for the client entry")  # noqa: TRY003
    if clear_token_reference and api_token_env is not None:
        raise ValueError("Cannot set and clear the token reference together")  # noqa: TRY003
    _validate_runtime_overrides(host, endpoint, allow_insecure_http)
    path = client_config_file().resolve()
    with configuration_lock(path):
        content, _ = read_configuration(path)
        document: Any = json.loads(content) if content is not None else {"version": 1, "hosts": {}}
        if (
            not isinstance(document, dict)
            or type(document.get("version")) is not int
            or document["version"] != 1
            or not isinstance(document.get("hosts"), dict)
        ):
            raise ValueError("Client configuration must have version 1 and a hosts object")  # noqa: TRY003
        # Validate the entry through its supported reader before preserving any existing reference.
        previous = parse_client_settings(document, host)
        entry = document["hosts"].setdefault(host, {})
        if not isinstance(entry, dict):
            raise ValueError("Client host configuration must be an object")  # noqa: TRY003, TRY004
        if previous.get("server_url") != endpoint:
            entry.pop("api_token_env", None)
        entry.update(server_url=endpoint, allow_insecure_http=allow_insecure_http)
        if api_token_env is not None:
            entry["api_token_env"] = api_token_env
        if clear_token_reference:
            entry.pop("api_token_env", None)
        revision = publish_configuration(
            path,
            (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode(),
            expected_revision=expected_revision,
        )
        result = inspect_connection(host)
        result["saved_revision"] = revision
        result["activation"] = "host_reload_may_be_required"
        return result


def _validate_runtime_overrides(host: str, endpoint: str, allow_insecure_http: bool) -> None:
    """Reject settings that would undo the saved endpoint or explicit consent on restart."""
    prefix = "POWERCONTEXT_" + ("CLAUDE" if host == "claude-code" else host.upper().replace("-", "_")) + "_"
    keys = (prefix + "BASE_URL", prefix + "SERVER_URL", prefix + "ENDPOINT", "POWERCONTEXT_CLIENT_SERVER_URL")
    if host == "claude-code":
        keys += ("CLAUDE_PLUGIN_OPTION_SERVER_URL",)
    for key in keys:
        value = os.environ.get(key)
        if value and normalize_client_url(value).removesuffix("/mcp").rstrip("/") != endpoint:
            raise ValueError(f"{key} conflicts with the selected endpoint; align or unset it first")  # noqa: TRY003
    for key in (prefix + "ALLOW_INSECURE_HTTP", "POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP"):
        if key in os.environ:
            from powercontext.client.transport_policy import parse_client_boolean

            if parse_client_boolean(os.environ[key]) != allow_insecure_http:
                raise ValueError(f"{key} conflicts with the selected consent; align or unset it first")  # noqa: TRY003


def project_binding_key(project: Path) -> ScopeBindingKey:
    """Use the exact existing Codex checkout key; separate worktrees stay separate."""
    root = project.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Project must be an existing directory")  # noqa: TRY003
    git = which("git")
    if git is not None:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell evaluation.
            [git, "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            root = Path(result.stdout.strip()).resolve(strict=True)
    return ScopeBindingKey(integration="codex", kind="workspace", external_id=sha256(os.fsencode(root)).hexdigest())


async def inspect_project(client: PowerContextClient, project: Path) -> dict[str, Any]:
    """Resolve this Server's exact checkout binding without default fallback."""
    key = project_binding_key(project)
    scope = await client.resolve_scope_binding(ResolveScopeBindingRequest(binding_keys=[key], allow_default=False))
    return {"key": key.model_dump(), "scope": scope.model_dump(mode="json")}


async def bind_project(client: PowerContextClient, project: Path, scope_id: str) -> dict[str, Any]:
    """Bind to an existing Server-issued Scope and return its acknowledgment."""
    key = project_binding_key(project)
    response = await client.set_scope_binding(SetScopeBindingRequest(root=ScopeBinding(key=key, scope_id=scope_id)))
    return response.model_dump(mode="json")


async def unbind_project(client: PowerContextClient, project: Path) -> dict[str, Any]:
    """Clear this Server's exact binding without changing Scope contents."""
    response = await client.clear_scope_binding(ClearScopeBindingRequest(key=project_binding_key(project)))
    return response.model_dump(mode="json")


def _validate_host(host: str) -> None:
    if host not in HOSTS:
        raise ValueError("Unsupported connection host")  # noqa: TRY003
