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

"""Installation and diagnostics commands for an installed PowerContext tool."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from queue import Empty, Queue
from shutil import which
from threading import Thread
from time import monotonic
from typing import Annotated, Any, cast
from urllib.parse import urlsplit, urlunsplit

import typer

from powercontext.cli.system import (
    Diagnostic,
    DiagnosticStatus,
    SetupError,
    _diagnostics_ok,
    _write_diagnostics,
    doctor,
)
from powercontext.paths import powercontext_data_dir
from powercontext.transport import is_loopback_host

from .hosts import HOST_ADAPTERS, host_adapter, run_host_setup

HELP_OPTION_NAMES = ("-h", "--help")
DEFAULT_MARKETPLACE_SOURCE = "oceanbase/powercontext"
DEFAULT_MARKETPLACE_REF = "master"
DEFAULT_CLAUDE_CODE_SERVER_URL = "http://127.0.0.1:8000"
DEFAULT_OPENCLAW_SERVER_URL = "http://127.0.0.1:8000"
PLUGIN_NAME = "powercontext"
CLAUDE_MARKETPLACE_NAME = "powercontext"
_GITHUB_REPOSITORY = re.compile(r"^[^/\s]+/[^/\s]+$")
_CODEX_REQUIRED_MCP_TOOLS = frozenset({"remember_memory", "search_memory"})
_CODEX_APP_SERVER_TIMEOUT_SECONDS = 15.0

setup_app = typer.Typer(
    name="setup",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Install and configure PowerContext integrations.",
    no_args_is_help=True,
)
doctor_app = typer.Typer(
    callback=doctor,
    name="doctor",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Check an installed PowerContext environment.",
    invoke_without_command=True,
)


@dataclass(frozen=True, slots=True)
class CodexSetupResult:
    marketplace: str
    plugin: str
    plugin_version: str
    data_dir: str
    authorization_state: str = "not_attempted"


@dataclass(frozen=True, slots=True)
class ClaudeCodeSetupResult:
    marketplace: str
    plugin: str
    plugin_version: str
    settings_file: str
    cache_dir: str
    data_dir: str
    authorization_state: str = "not_attempted"


@dataclass(frozen=True, slots=True)
class OpenClawSetupResult:
    plugin: str
    plugin_path: str
    server_url: str
    data_dir: str


SetupSource = Annotated[str, typer.Option(help="PowerContext Git source or local checkout path.")]
SetupRef = Annotated[str, typer.Option(help="Git ref used for a remote source.")]
SetupServer = Annotated[
    str | None, typer.Option(help="PowerContext Server URL; resolves host/common environment and saved settings.")
]
SetupConsent = Annotated[
    bool | None,
    typer.Option("--allow-insecure-http/--no-allow-insecure-http", help="Explicitly allow unencrypted remote HTTP."),
]
SetupJSON = Annotated[bool, typer.Option("--json", help="Write the result as JSON.")]
SetupPython = Annotated[
    str | None, typer.Option(help="Application Python executable; defaults to the project virtual environment.")
]
SetupDestination = Annotated[Path | None, typer.Option(help="Plugin directory for a directory-based integration.")]


def setup_host(
    context: typer.Context,
    source: SetupSource = DEFAULT_MARKETPLACE_SOURCE,
    ref: SetupRef = DEFAULT_MARKETPLACE_REF,
    server_url: SetupServer = None,
    allow_insecure_http: SetupConsent = None,
    json_output: SetupJSON = False,
    python: SetupPython = None,
    destination: SetupDestination = None,
) -> None:
    run_host_setup(
        context.info_name or "",
        source=source,
        ref=ref,
        server_url=server_url,
        allow_insecure_http=allow_insecure_http,
        json_output=json_output,
        python=python,
        destination=destination,
    )


def setup_claude_code(
    context: typer.Context,
    source: SetupSource = DEFAULT_MARKETPLACE_SOURCE,
    ref: SetupRef = DEFAULT_MARKETPLACE_REF,
    server_url: SetupServer = None,
    capture_prompts: Annotated[
        bool, typer.Option(help="Capture Claude Code user prompts as ordinary Source evidence.")
    ] = True,
    allow_insecure_http: SetupConsent = None,
    json_output: SetupJSON = False,
    python: SetupPython = None,
    destination: SetupDestination = None,
) -> None:
    run_host_setup(
        context.info_name or "",
        source=source,
        ref=ref,
        server_url=server_url,
        capture_prompts=capture_prompts,
        allow_insecure_http=allow_insecure_http,
        json_output=json_output,
        python=python,
        destination=destination,
    )


for _adapter in HOST_ADAPTERS:
    setup_app.command(_adapter.name, help=f"Install and configure the PowerContext {_adapter.label} integration.")(
        setup_claude_code if "capture_prompts" in _adapter.setup_options else setup_host
    )


@setup_app.command("select")
def setup_select(
    host: Annotated[
        list[str] | None,
        typer.Option(help="Integration target to install. Repeatable. Required with --json or a non-TTY."),
    ] = None,
    source: SetupSource = DEFAULT_MARKETPLACE_SOURCE,
    ref: SetupRef = DEFAULT_MARKETPLACE_REF,
    server_url: SetupServer = None,
    capture_prompts: Annotated[
        bool,
        typer.Option(help="Capture Claude Code user prompts as ordinary Source evidence."),
    ] = True,
    allow_insecure_http: SetupConsent = None,
    json_output: SetupJSON = False,
    python: SetupPython = None,
    destination: SetupDestination = None,
) -> None:
    """Install selected integration targets without scanning PATH."""

    from .hosts import run_setup_select

    run_setup_select(
        hosts=host,
        source=source,
        ref=ref,
        server_url=server_url,
        capture_prompts=capture_prompts,
        json_output=json_output,
        allow_insecure_http=allow_insecure_http,
        python=python,
        destination=destination,
    )


def _register_host_doctors() -> None:
    from .host import HOST_ADAPTERS, HostAdapter

    def register(adapter: HostAdapter) -> None:
        def doctor_host(
            json_output: SetupJSON = False,
            python: SetupPython = None,
            destination: SetupDestination = None,
            server: Annotated[bool, typer.Option(help="Also check the configured Server.")] = False,
        ) -> None:
            options = {
                key: value for key, value in {"python": python, "destination": destination}.items() if value is not None
            }
            if any(key not in adapter.setup_options for key in options):
                raise typer.BadParameter("This integration does not accept the supplied installation location.")
            diagnostics = adapter.diagnose(server=server, **options)
            _write_diagnostics(diagnostics, json_output=json_output)
            if not _diagnostics_ok(diagnostics):
                raise typer.Exit(code=1)

        doctor_app.command(adapter.name, help=f"Check {adapter.label} and its PowerContext integration.")(doctor_host)

    for adapter in HOST_ADAPTERS:
        register(adapter)


_register_host_doctors()


@doctor_app.command("integrations")
def doctor_integrations(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write the result as JSON."),
    ] = False,
) -> None:
    """Report first-class host CLI and integration status without failing on missing CLIs."""

    from .hosts import run_doctor_integrations

    run_doctor_integrations(json_output=json_output)


def install_codex_plugin(*, source: str, ref: str, server_url: str | None = None) -> CodexSetupResult:
    """Install the plugin from one local or Git marketplace source."""

    if which("codex") is None:
        raise SetupError.unavailable("Codex CLI")

    data_dir = powercontext_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SetupError(f"Cannot create PowerContext data directory {data_dir}: {error}") from error

    marketplace_source, is_local = _normalize_marketplace_source(source)
    marketplace_arguments = ["plugin", "marketplace", "add", marketplace_source]
    if not is_local:
        marketplace_arguments.extend(("--ref", ref))
    marketplace = _run_codex_json(*marketplace_arguments)
    marketplace_name = _required_string(marketplace, "marketplaceName")

    plugin = _run_codex_json("plugin", "add", f"{PLUGIN_NAME}@{marketplace_name}")
    _configure_codex_endpoint(marketplace_name, _required_string(plugin, "version"), server_url)
    from .authorization import (
        configure_codex_desktop_authorization,
        configure_stored_authorization,
        credential_path,
        read_stored_authorization,
        setup_authorization_value,
        setup_server_url,
    )

    authorization_server_url = setup_server_url("codex", server_url or DEFAULT_CLAUDE_CODE_SERVER_URL)
    authorization_state = configure_stored_authorization(
        "codex",
        server_url=authorization_server_url,
        value=setup_authorization_value("codex"),
    )
    authorization = read_stored_authorization(credential_path("codex"), server_url=authorization_server_url)
    if authorization.authorization is not None:
        try:
            configure_codex_desktop_authorization(authorization.authorization)
        except OSError as error:
            raise SetupError(f"Cannot configure Codex Desktop authorization: {error}") from error
    return CodexSetupResult(
        marketplace=marketplace_name,
        plugin=_required_string(plugin, "name"),
        plugin_version=_required_string(plugin, "version"),
        data_dir=str(data_dir),
        authorization_state=authorization_state,
    )


def _configure_codex_endpoint(marketplace: str, plugin_version: str, server_url: str | None) -> None:
    """Keep the installed native MCP URL and hook URL identical."""

    if any(part in {"", ".", ".."} or "/" in part or "\\" in part for part in (marketplace, plugin_version)):
        raise SetupError("Invalid Codex plugin cache location")
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    path = codex_home / "plugins" / "cache" / marketplace / PLUGIN_NAME / plugin_version / ".mcp.json"
    try:
        host_adapter("codex").prepare(path.parent, server_url=server_url)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SetupError(f"Cannot generate Codex plugin resources at {path.parent}") from error


def install_claude_code_plugin(
    *,
    source: str,
    ref: str,
    server_url: str,
    capture_prompts: bool,
    allow_insecure_http: bool = False,
) -> ClaudeCodeSetupResult:
    """Install and verify the plugin from one local or Git marketplace source."""

    if which("claude") is None:
        raise SetupError.unavailable("Claude Code CLI")
    server_url = _normalize_claude_server_url(server_url, allow_insecure_http=allow_insecure_http)

    _write_claude_setup_plan(_claude_setup_plan())
    marketplace_source = _normalize_claude_marketplace_source(source, ref=ref)
    marketplaces = _run_claude_json("plugin", "marketplace", "list")
    marketplace = _claude_marketplace(marketplaces, CLAUDE_MARKETPLACE_NAME)
    if marketplace is not None and not _claude_marketplace_matches(marketplace, marketplace_source):
        raise SetupError(
            f"Claude Code marketplace `{CLAUDE_MARKETPLACE_NAME}` uses {_describe_claude_marketplace_source(marketplace)}, but setup requested {marketplace_source}. Remove it with `claude plugin marketplace remove {CLAUDE_MARKETPLACE_NAME}`, then rerun setup."
        )
    marketplace_existed = marketplace is not None

    plugins = _run_claude_json("plugin", "list")
    previous_plugin = _claude_plugin(plugins, scope="user")
    plugin_existed = previous_plugin is not None
    settings_snapshot = _snapshot_claude_settings()
    marketplace_added = False
    plugin_added = False
    try:
        if not marketplace_existed:
            _run_claude("plugin", "marketplace", "add", marketplace_source, "--scope", "user")
            marketplace_added = True
        else:
            # An existing marketplace and plugin keep their cached version until
            # both are refreshed, so setup would otherwise configure a status
            # line for a cache this release never wrote.
            _run_claude("plugin", "marketplace", "update", CLAUDE_MARKETPLACE_NAME)
        if plugin_existed:
            _run_claude(
                "plugin",
                "update",
                f"{PLUGIN_NAME}@{CLAUDE_MARKETPLACE_NAME}",
                "--scope",
                "user",
            )
        _run_claude(
            "plugin",
            "install",
            f"{PLUGIN_NAME}@{CLAUDE_MARKETPLACE_NAME}",
            "--scope",
            "user",
        )
        plugin_added = not plugin_existed
        installed = _run_claude_json("plugin", "list")
        plugin = _require_enabled_claude_plugin(installed, scope="user")
        _configure_claude_plugin(
            plugin=plugin,
            server_url=server_url,
            capture_prompts=capture_prompts,
            allow_insecure_http=allow_insecure_http,
        )
    except SetupError:
        if plugin_added:
            with suppress(SetupError):
                _run_claude(
                    "plugin",
                    "uninstall",
                    f"{PLUGIN_NAME}@{CLAUDE_MARKETPLACE_NAME}",
                    "--scope",
                    "user",
                )
        with suppress(OSError):
            _restore_claude_settings(settings_snapshot)
        if marketplace_added:
            with suppress(SetupError):
                _run_claude(
                    "plugin",
                    "marketplace",
                    "remove",
                    CLAUDE_MARKETPLACE_NAME,
                )
        raise

    plan = _claude_setup_plan()
    from .authorization import configure_stored_authorization, setup_authorization_value

    authorization_state = configure_stored_authorization(
        "claude-code", server_url=server_url, value=setup_authorization_value("claude-code")
    )
    return ClaudeCodeSetupResult(
        marketplace=CLAUDE_MARKETPLACE_NAME,
        plugin=PLUGIN_NAME,
        plugin_version=_required_string(plugin, "version"),
        settings_file=plan["settings_file"],
        cache_dir=plan["cache_dir"],
        data_dir=plan["data_dir"],
        authorization_state=authorization_state,
    )


def run_codex_diagnostics() -> dict[str, Diagnostic]:
    """Collect plugin and native MCP diagnostics for the optional Codex integration."""

    executable = which("codex")
    if executable is None:
        return {
            "codex": Diagnostic(
                status=DiagnosticStatus.FAILED,
                detail="Codex CLI is not installed or is not on PATH",
            ),
            "plugin": Diagnostic(
                status=DiagnosticStatus.SKIPPED,
                detail="not checked because Codex CLI is unavailable",
            ),
        }
    try:
        result = _run_codex_json("plugin", "list")
    except SetupError as error:
        return {
            "codex": Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error)),
            "plugin": Diagnostic(status=DiagnosticStatus.SKIPPED, detail="plugin list is unavailable"),
        }
    installed = result.get("installed")
    plugin = None
    if isinstance(installed, list):
        plugin = next(
            (
                item
                for item in installed
                if isinstance(item, dict)
                and item.get("name") == PLUGIN_NAME
                and item.get("installed") is True
                and item.get("enabled") is True
            ),
            None,
        )
    diagnostics = {
        "codex": Diagnostic(status=DiagnosticStatus.OK, detail=executable),
        "plugin": Diagnostic(
            status=DiagnosticStatus.OK if plugin is not None else DiagnosticStatus.FAILED,
            detail=(
                f"{plugin.get('pluginId')} enabled={plugin.get('enabled')}"
                if plugin is not None
                else "PowerContext plugin is not installed"
            ),
        ),
    }
    if plugin is None:
        diagnostics["mcp_configuration"] = Diagnostic(
            status=DiagnosticStatus.SKIPPED,
            detail="not checked because the PowerContext plugin is unavailable",
        )
        diagnostics["authorization"] = Diagnostic(
            status=DiagnosticStatus.SKIPPED,
            detail="not checked because the PowerContext MCP entry is unavailable",
        )
        diagnostics["mcp_tools"] = Diagnostic(
            status=DiagnosticStatus.SKIPPED,
            detail="not checked because the PowerContext plugin is unavailable",
        )
        return diagnostics

    try:
        servers = _run_codex_mcp_list()
    except SetupError as error:
        diagnostics["mcp_configuration"] = Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error))
        diagnostics["authorization"] = Diagnostic(
            status=DiagnosticStatus.SKIPPED,
            detail="not checked because native MCP configuration is unavailable",
        )
        diagnostics["mcp_tools"] = Diagnostic(
            status=DiagnosticStatus.SKIPPED,
            detail="not checked because native MCP configuration is unavailable",
        )
        return diagnostics

    server = next((item for item in servers if item.get("name") == PLUGIN_NAME), None)
    transport = server.get("transport") if server is not None else None
    environment_headers = transport.get("env_http_headers") if isinstance(transport, dict) else None
    mcp_url = transport.get("url") if isinstance(transport, dict) else None
    configuration_ok = (
        server is not None
        and server.get("enabled") is True
        and isinstance(mcp_url, str)
        and bool(mcp_url)
        and environment_headers == {"Authorization": "POWERCONTEXT_CODEX_AUTHORIZATION"}
    )
    diagnostics["mcp_configuration"] = Diagnostic(
        status=DiagnosticStatus.OK if configuration_ok else DiagnosticStatus.FAILED,
        detail=(
            f"enabled with environment-backed authorization; auth_status={server.get('auth_status', 'unknown')}"
            if configuration_ok and server is not None
            else "PowerContext native MCP entry is missing, disabled, or lacks environment-backed authorization; "
            "reinstall the current plugin"
        ),
    )
    if not configuration_ok or not isinstance(mcp_url, str):
        diagnostics["authorization"] = Diagnostic(
            status=DiagnosticStatus.SKIPPED,
            detail="not checked because native MCP configuration is invalid",
        )
        diagnostics["mcp_tools"] = Diagnostic(
            status=DiagnosticStatus.SKIPPED,
            detail="not checked because native MCP configuration is invalid",
        )
        return diagnostics

    authorization_diagnostic, native_authorization = _resolve_codex_native_authorization(mcp_url)
    diagnostics["authorization"] = authorization_diagnostic
    if not authorization_diagnostic.ok:
        diagnostics["mcp_tools"] = Diagnostic(
            status=DiagnosticStatus.SKIPPED,
            detail="not checked because Codex host authorization is invalid",
        )
        return diagnostics

    try:
        native_server = _probe_codex_mcp_status(authorization=native_authorization)
    except SetupError as error:
        diagnostics["mcp_tools"] = Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error))
        return diagnostics
    tools = native_server.get("tools")
    tool_names = set(tools) if isinstance(tools, dict) else set()
    missing = sorted(_CODEX_REQUIRED_MCP_TOOLS - tool_names)
    if native_authorization is None:
        failure_hint = (
            "; check Server availability and, for an authenticated Server, set "
            "POWERCONTEXT_CODEX_AUTHORIZATION while rerunning `powercontext setup codex`"
        )
    else:
        failure_hint = "; check Server availability and whether the effective host credential is still valid"
    diagnostics["mcp_tools"] = Diagnostic(
        status=DiagnosticStatus.OK if not missing else DiagnosticStatus.FAILED,
        detail=(
            f"Codex native MCP initialized and discovered {len(tool_names)} tools"
            if not missing
            else "Codex native MCP did not discover required tools: " + ", ".join(missing) + failure_hint
        ),
    )
    return diagnostics


def _codex_authorization_checks(
    *,
    stored_state: str,
    stored_authorization: str | None,
    process_state: str,
    process_authorization: str | None,
    desktop_authorization: str | None,
) -> dict[str, str]:
    if stored_authorization is None:
        setup_managed_state = stored_state
    elif process_authorization is not None:
        setup_managed_state = "matches_current_process" if stored_authorization == process_authorization else "stale"
    elif desktop_authorization is not None:
        setup_managed_state = "matches_desktop_restart" if stored_authorization == desktop_authorization else "stale"
    else:
        setup_managed_state = "configured_but_unavailable_to_host"

    if desktop_authorization is None:
        desktop_restart_state = "not_configured"
    elif process_authorization is None:
        desktop_restart_state = "configured"
    else:
        desktop_restart_state = (
            "matches_current_process"
            if desktop_authorization == process_authorization
            else "differs_from_current_process"
        )
    return {
        "current_process": process_state,
        "setup_managed": setup_managed_state,
        "desktop_restart": desktop_restart_state,
    }


def _codex_stored_authorization_issue(stored_state: str, setup_managed_state: str) -> str | None:
    if setup_managed_state == "stale":
        return "setup-managed credential is stale"
    if stored_state not in {"configured", "not_configured"}:
        return f"stored credential state is {stored_state}"
    return None


def _codex_process_authorization_detail(stored_issue: str | None, desktop_restart_state: str) -> str:
    detail_parts = ["current process authorization is configured and will be used by the native MCP probe"]
    if stored_issue is not None:
        detail_parts.append(stored_issue)
    if desktop_restart_state == "differs_from_current_process":
        detail_parts.append(
            "Windows user authorization differs from the current process after restarting Codex Desktop"
        )
    elif desktop_restart_state == "not_configured":
        detail_parts.append("Windows user authorization is not configured for a restarted Codex Desktop")
    return "; ".join(detail_parts)


def _codex_desktop_authorization_detail(*, matches_stored: bool, stored_issue: str | None) -> str:
    detail_parts = [
        "current process authorization is not configured; Windows user authorization will be used by the native MCP "
        "probe for the environment expected after restarting Codex Desktop"
    ]
    if matches_stored:
        detail_parts.append("Windows user authorization matches the setup-managed credential")
    elif stored_issue is not None:
        detail_parts.append(stored_issue)
    return "; ".join(detail_parts)


def _resolve_codex_native_authorization(mcp_url: str) -> tuple[Diagnostic, str | None]:
    """Resolve the redacted Codex host authorization state for one MCP URL."""

    from .authorization import (
        credential_path,
        normalize_authorization,
        read_codex_desktop_authorization,
        read_stored_authorization,
    )

    authorization = read_stored_authorization(
        credential_path("codex"), server_url=mcp_url.rstrip("/").removesuffix("/mcp")
    )
    process_value = os.environ.get("POWERCONTEXT_CODEX_AUTHORIZATION")
    process_authorization: str | None = None
    comparable_process_authorization: str | None = None
    process_state = "not_configured"
    if process_value is not None:
        try:
            normalized_process_authorization = normalize_authorization(process_value)
        except ValueError:
            process_state = "invalid"
        else:
            scheme, separator, _credential = process_value.partition(" ")
            if process_value != process_value.strip() or not separator or scheme.casefold() != "bearer":
                process_state = "invalid"
            else:
                process_authorization = process_value
                comparable_process_authorization = normalized_process_authorization
                process_state = "configured"
    desktop_authorization = read_codex_desktop_authorization()
    expected_authorization = authorization.authorization
    checks = _codex_authorization_checks(
        stored_state=authorization.status,
        stored_authorization=expected_authorization,
        process_state=process_state,
        process_authorization=comparable_process_authorization,
        desktop_authorization=desktop_authorization,
    )
    stored_issue = _codex_stored_authorization_issue(authorization.status, checks["setup_managed"])

    if process_authorization is not None:
        return (
            Diagnostic(
                status=DiagnosticStatus.OK,
                detail=_codex_process_authorization_detail(stored_issue, checks["desktop_restart"]),
                checks=checks,
            ),
            process_authorization,
        )

    if process_state == "invalid":
        return (
            Diagnostic(
                status=DiagnosticStatus.FAILED,
                detail=(
                    "current process authorization is invalid; set a complete Bearer credential in "
                    "POWERCONTEXT_CODEX_AUTHORIZATION"
                ),
                checks=checks,
            ),
            None,
        )

    if desktop_authorization is not None:
        return (
            Diagnostic(
                status=DiagnosticStatus.OK,
                detail=_codex_desktop_authorization_detail(
                    matches_stored=expected_authorization == desktop_authorization,
                    stored_issue=stored_issue,
                ),
                checks=checks,
            ),
            desktop_authorization,
        )

    authorization_ok = authorization.status == "not_configured"
    authorization_detail = (
        "no host authorization is configured; the native probe will verify an unauthenticated connection"
        if authorization_ok
        else (
            "setup-managed credential is not available to the Codex host; rerun `powercontext setup codex`"
            if authorization.status == "configured"
            else f"stored credential state is {authorization.status}; rerun `powercontext setup codex`"
        )
    )
    return (
        Diagnostic(
            status=DiagnosticStatus.OK if authorization_ok else DiagnosticStatus.FAILED,
            detail=authorization_detail,
            checks=checks,
        ),
        None,
    )


def _run_codex_mcp_list() -> list[dict[str, Any]]:
    """Read Codex's resolved native MCP configuration without exposing header values."""

    command = ["codex", "mcp", "list", "--json"]
    try:
        completed = subprocess.run(  # noqa: S603 - arguments are fixed and do not contain credentials.
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError.command_unavailable(command[:-1], error) from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise SetupError.command_failed(command[:-1], detail)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise SetupError.invalid_command_output(command[:-1], "invalid JSON") from error
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise SetupError.invalid_command_output(command[:-1], "an unexpected result")
    return cast(list[dict[str, Any]], payload)


def _probe_codex_mcp_status(*, authorization: str | None = None) -> dict[str, Any]:  # noqa: C901
    """Initialize Codex app-server and return its PowerContext MCP status."""

    executable = which("codex")
    if executable is None:
        raise SetupError.unavailable("Codex CLI")
    environment = os.environ.copy()
    if authorization is None:
        environment.pop("POWERCONTEXT_CODEX_AUTHORIZATION", None)
    else:
        environment["POWERCONTEXT_CODEX_AUTHORIZATION"] = authorization
    command = [executable, "app-server", "--listen", "stdio://"]
    try:
        process = subprocess.Popen(  # noqa: S603 - arguments are fixed and contain no credentials.
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )
    except OSError as error:
        raise SetupError.command_unavailable(command, error) from error
    stdin = process.stdin
    stdout = process.stdout
    if stdin is None or stdout is None:
        process.kill()
        raise SetupError("Codex app-server did not provide stdio for the native MCP probe")

    messages: Queue[dict[str, Any] | None] = Queue()

    def read_messages() -> None:
        try:
            for line in stdout:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict):
                    messages.put(message)
        finally:
            messages.put(None)

    reader = Thread(target=read_messages, name="powercontext-codex-app-server", daemon=True)
    reader.start()

    def send(message: dict[str, Any]) -> None:
        try:
            stdin.write(json.dumps(message, ensure_ascii=True, separators=(",", ":")) + "\n")
            stdin.flush()
        except OSError as error:
            raise SetupError("Codex app-server closed during the native MCP probe") from error

    def receive(request_id: int) -> dict[str, Any]:
        deadline = monotonic() + _CODEX_APP_SERVER_TIMEOUT_SECONDS
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise SetupError("Codex native MCP probe timed out")
            try:
                message = messages.get(timeout=remaining)
            except Empty as error:
                raise SetupError("Codex native MCP probe timed out") from error
            if message is None:
                raise SetupError("Codex app-server exited before completing the native MCP probe")
            if message.get("id") == request_id:
                return message

    try:
        send({
            "method": "initialize",
            "id": 1,
            "params": {
                "clientInfo": {
                    "name": "powercontext_doctor",
                    "title": "PowerContext Doctor",
                    "version": version("powercontext"),
                }
            },
        })
        initialized = receive(1)
        if "error" in initialized:
            raise SetupError("Codex app-server rejected native MCP probe initialization")
        send({"method": "initialized", "params": {}})
        send({
            "method": "mcpServerStatus/list",
            "id": 2,
            "params": {"limit": 100, "detail": "toolsAndAuthOnly"},
        })
        response = receive(2)
        result = response.get("result")
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list):
            raise SetupError("Codex app-server returned an invalid native MCP status response")
        server = next(
            (item for item in data if isinstance(item, dict) and item.get("name") == PLUGIN_NAME),
            None,
        )
        if server is None:
            raise SetupError("Codex native MCP status does not include PowerContext")
        return server
    finally:
        with suppress(OSError):
            stdin.close()
        with suppress(OSError):
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=1)


def run_claude_code_diagnostics() -> dict[str, Diagnostic]:
    """Collect diagnostics for the optional Claude Code integration."""

    executable = which("claude")
    if executable is None:
        return {
            "claude_code": Diagnostic(
                status=DiagnosticStatus.FAILED,
                detail="Claude Code CLI is not installed or is not on PATH",
            ),
            "plugin": Diagnostic(
                status=DiagnosticStatus.SKIPPED,
                detail="not checked because Claude Code CLI is unavailable",
            ),
        }
    try:
        result = _run_claude_json("plugin", "list")
    except SetupError as error:
        return {
            "claude_code": Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error)),
            "plugin": Diagnostic(status=DiagnosticStatus.SKIPPED, detail="plugin list is unavailable"),
        }
    plugin = _claude_plugin(result)
    plugin_enabled = plugin is not None and plugin.get("enabled") is True
    return {
        "claude_code": Diagnostic(status=DiagnosticStatus.OK, detail=executable),
        "plugin": Diagnostic(
            status=DiagnosticStatus.OK if plugin_enabled else DiagnosticStatus.FAILED,
            detail=(
                f"{plugin.get('id')} enabled={plugin.get('enabled')}"
                if plugin is not None
                else "PowerContext plugin is not installed"
            ),
        ),
    }


def _normalize_marketplace_source(source: str) -> tuple[str, bool]:
    candidate = Path(source).expanduser()
    is_local = source.startswith((".", "/", "~")) or candidate.exists()
    return (str(candidate.resolve()), True) if is_local else (source, False)


def _normalize_claude_marketplace_source(source: str, *, ref: str) -> str:
    candidate = Path(source).expanduser()
    is_local = source.startswith((".", "/", "~")) or candidate.is_absolute() or candidate.exists()
    if is_local:
        return str(candidate.resolve())
    if not ref:
        return source
    if _GITHUB_REPOSITORY.fullmatch(source):
        return f"{source}@{ref}"
    return f"{source}#{ref}"


def _normalize_claude_server_url(value: str, *, allow_insecure_http: bool = False) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.username is not None or parsed.password is not None:
        raise SetupError("PowerContext Server URL must not contain credentials.")
    if parsed.hostname is None or parsed.scheme not in {"http", "https"}:
        raise SetupError("PowerContext Server URL must use HTTP or HTTPS.")
    if parsed.query or parsed.fragment:
        raise SetupError("PowerContext Server URL must not contain a query or fragment.")
    if parsed.scheme == "http" and not is_loopback_host(parsed.hostname) and not allow_insecure_http:
        raise SetupError("Unencrypted PowerContext Server URLs must be loopback addresses.")
    path = parsed.path.rstrip("/")
    if path.endswith("/mcp"):
        path = path.removesuffix("/mcp")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def _claude_config_dir() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def _claude_setup_plan() -> dict[str, str]:
    config_dir = _claude_config_dir()
    return {
        "settings_file": str(config_dir / "settings.json"),
        "cache_dir": str(config_dir / "plugins" / "cache" / CLAUDE_MARKETPLACE_NAME / PLUGIN_NAME / "<version>"),
        "data_dir": str(config_dir / "plugins" / "data" / f"{PLUGIN_NAME}-{CLAUDE_MARKETPLACE_NAME}"),
    }


def _write_claude_setup_plan(plan: dict[str, str]) -> None:
    typer.echo("Claude Code setup plan (no changes made yet):", err=True)
    typer.echo(f"  Settings entry: {plan['settings_file']}", err=True)
    typer.echo(f"  Plugin cache: {plan['cache_dir']}", err=True)
    typer.echo(f"  Plugin data: {plan['data_dir']}", err=True)
    typer.echo("  Permissions: read/write access to the Claude Code configuration directory", err=True)
    typer.echo(
        f"  Rollback: claude plugin uninstall {PLUGIN_NAME}@{CLAUDE_MARKETPLACE_NAME} --scope user",
        err=True,
    )
    typer.echo(
        f"  Rollback: claude plugin marketplace remove {CLAUDE_MARKETPLACE_NAME}",
        err=True,
    )


def _claude_marketplace(value: object, name: str) -> dict[str, Any] | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if isinstance(item, dict) and item.get("name") == name:
            return cast(dict[str, Any], item)
    return None


def _claude_marketplace_matches(marketplace: dict[str, Any], requested: str) -> bool:
    source_kind = marketplace.get("source")
    if source_kind == "directory":
        existing_path = marketplace.get("path")
        if not isinstance(existing_path, str):
            return False
        return os.path.normcase(str(Path(existing_path).resolve())) == os.path.normcase(str(Path(requested).resolve()))
    if source_kind == "github":
        requested_repo, separator, requested_ref = requested.partition("@")
        existing_repo = marketplace.get("repo")
        existing_ref = marketplace.get("ref")
        return (
            isinstance(existing_repo, str)
            and existing_repo.casefold() == requested_repo.casefold()
            and _claude_marketplace_ref_matches(existing_ref, requested_ref if separator else "")
        )
    if source_kind == "git":
        requested_url, separator, requested_ref = requested.rpartition("#")
        existing_url = marketplace.get("url")
        existing_ref = marketplace.get("ref")
        return (
            isinstance(existing_url, str)
            and existing_url == (requested_url if separator else requested)
            and _claude_marketplace_ref_matches(existing_ref, requested_ref if separator else "")
        )
    return False


def _claude_marketplace_ref_matches(existing: object, requested: str) -> bool:
    """Accept omitted Claude JSON refs while still rejecting an explicit mismatch."""

    return existing is None or existing == "" or existing == requested


def _describe_claude_marketplace_source(marketplace: dict[str, Any]) -> str:
    fields = {name: marketplace[name] for name in ("source", "path", "repo", "url", "ref") if name in marketplace}
    return json.dumps(fields, sort_keys=True)


def _claude_plugin(value: object, *, scope: str | None = None) -> dict[str, Any] | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if (
            isinstance(item, dict)
            and item.get("id") == f"{PLUGIN_NAME}@{CLAUDE_MARKETPLACE_NAME}"
            and (scope is None or item.get("scope") == scope)
        ):
            return cast(dict[str, Any], item)
    return None


def _require_enabled_claude_plugin(value: object, *, scope: str | None = None) -> dict[str, Any]:
    plugin = _claude_plugin(value, scope=scope)
    if plugin is None or plugin.get("enabled") is not True:
        raise SetupError("Claude Code did not report an enabled PowerContext plugin after installation.")
    return plugin


def _snapshot_claude_settings() -> bytes | None:
    settings_file = _claude_config_dir() / "settings.json"
    try:
        return settings_file.read_bytes()
    except FileNotFoundError:
        return None


def _configure_claude_plugin(
    *,
    plugin: dict[str, Any],
    server_url: str,
    capture_prompts: bool,
    allow_insecure_http: bool = False,
) -> None:
    """Merge non-sensitive plugin options unsupported by the Claude install CLI."""

    settings_file = _claude_config_dir() / "settings.json"
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        if isinstance(error, OSError):
            raise SetupError(f"Cannot update Claude Code settings at {settings_file}: {error}") from error
        raise SetupError(
            f"Claude Code settings at {settings_file} must contain a JSON object with object-valued plugin options."
        ) from error
    if not isinstance(settings, dict):
        raise SetupError(
            f"Claude Code settings at {settings_file} must contain a JSON object with object-valued plugin options."
        )

    plugin_configs = settings.setdefault("pluginConfigs", {})
    if not isinstance(plugin_configs, dict):
        raise SetupError(
            f"Claude Code settings at {settings_file} must contain a JSON object with object-valued plugin options."
        )
    plugin_id = f"{PLUGIN_NAME}@{CLAUDE_MARKETPLACE_NAME}"
    plugin_config = plugin_configs.setdefault(plugin_id, {})
    if not isinstance(plugin_config, dict):
        raise SetupError(
            f"Claude Code settings at {settings_file} must contain a JSON object with object-valued plugin options."
        )
    options = plugin_config.setdefault("options", {})
    if not isinstance(options, dict):
        raise SetupError(
            f"Claude Code settings at {settings_file} must contain a JSON object with object-valued plugin options."
        )
    options.update({
        "server_url": server_url,
        "capture_prompts": capture_prompts,
        "allow_insecure_http": allow_insecure_http,
    })

    install_path = _claude_plugin_install_path(plugin)
    host_adapter("claude-code").prepare(install_path)
    statusline_command = shlex.join([
        "powercontext-hook",
        "--script",
        str(install_path / "scripts" / "statusline.py"),
        "--",
        "--server-url",
        server_url,
    ])
    statusline = settings.get("statusLine")
    if statusline is None or _is_powercontext_statusline(statusline):
        settings["statusLine"] = {
            "type": "command",
            "command": statusline_command,
            "refreshInterval": 30,
        }
    try:
        _write_bytes_atomically(settings_file, (json.dumps(settings, indent=2) + "\n").encode())
    except OSError as error:
        raise SetupError(f"Cannot update Claude Code settings at {settings_file}: {error}") from error


def _claude_plugin_install_path(plugin: dict[str, Any]) -> Path:
    install_path = plugin.get("installPath")
    if isinstance(install_path, str) and install_path:
        return Path(install_path)
    version_value = _required_string(plugin, "version")
    return _claude_config_dir() / "plugins" / "cache" / CLAUDE_MARKETPLACE_NAME / PLUGIN_NAME / version_value


def _is_powercontext_statusline(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    command = value.get("command")
    if not isinstance(command, str):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    return any(
        Path(token).name == "statusline.py" and any("powercontext" in part.casefold() for part in Path(token).parts)
        for token in tokens
    )


def _restore_claude_settings(snapshot: bytes | None) -> None:
    settings_file = _claude_config_dir() / "settings.json"
    if snapshot is None:
        settings_file.unlink(missing_ok=True)
        return
    _write_bytes_atomically(settings_file, snapshot)


def _write_bytes_atomically(path: Path, content: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(descriptor, content)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary_path, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary_path.unlink()


def _run_codex_json(*arguments: str) -> dict[str, Any]:
    command = ["codex", *arguments, "--json"]
    try:
        completed = subprocess.run(  # noqa: S603 - arguments are passed directly to the fixed Codex executable.
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError.command_unavailable(command[:-1], error) from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise SetupError.command_failed(command[:-1], detail)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise SetupError.invalid_command_output(command[:-1], "invalid JSON") from error
    if not isinstance(result, dict):
        raise SetupError.invalid_command_output(command[:-1], "an unexpected result")
    return result


def _run_claude(*arguments: str) -> subprocess.CompletedProcess[str]:
    executable = which("claude")
    if executable is None:
        raise SetupError.unavailable("Claude Code CLI")
    command = [executable, *arguments]
    try:
        completed = subprocess.run(  # noqa: S603 - arguments are passed directly to the fixed Claude executable.
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError.command_unavailable(command, error) from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise SetupError.command_failed(command, detail)
    return completed


def _run_claude_json(*arguments: str) -> object:
    command = [*arguments, "--json"]
    completed = _run_claude(*command)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise SetupError.invalid_command_output(["claude", *command], "invalid JSON") from error


def _required_string(value: dict[str, Any], name: str) -> str:
    result = value.get(name)
    if not isinstance(result, str) or not result:
        raise SetupError(f"Integration CLI did not return {name}")
    return result


__all__ = [
    "ClaudeCodeSetupResult",
    "CodexSetupResult",
    "Diagnostic",
    "DiagnosticStatus",
    "OpenClawSetupResult",
    "SetupError",
    "doctor_app",
    "install_claude_code_plugin",
    "install_codex_plugin",
    "run_claude_code_diagnostics",
    "run_codex_diagnostics",
    "setup_app",
]
