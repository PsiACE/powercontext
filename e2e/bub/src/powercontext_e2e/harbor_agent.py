"""Harbor ACP agent that installs the local PowerContext Bub integration."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from harbor.agents.installed import acp as harbor_acp
from harbor.environments.base import BaseEnvironment

AGENT_ID = "powercontext-bub-acp"
REMOTE_AGENT_ENV = "/installed-agent/bub-env"
REMOTE_CODEX_HOME = "/installed-agent/codex"
REMOTE_WHEEL_DIR = "/installed-agent/wheels"


class AgentInputError(ValueError):
    """Report invalid local inputs required by the Harbor agent."""

    def __init__(self, name: str, requirement: str) -> None:
        super().__init__(f"{name} must {requirement}")


class HostGatewayError(RuntimeError):
    """Report failure to resolve the container-to-host gateway."""

    def __init__(self) -> None:
        super().__init__("Could not resolve the container host gateway")


class CodexAuthFormatError(TypeError):
    """Report a Codex auth document without the OAuth token object."""

    def __init__(self) -> None:
        super().__init__("codex_auth_path does not contain Codex OAuth tokens")


class PowerContextBubAcpAgent(harbor_acp.AcpAgent):
    """Run Bub through Harbor's ACP client with locally built integration wheels."""

    def __init__(
        self,
        *,
        wheel_dir: str,
        codex_auth_path: str,
        bub_version: str,
        acp_server_version: str,
        **kwargs: Any,
    ) -> None:
        self._wheel_dir = Path(wheel_dir).resolve()
        self._codex_auth_path = Path(codex_auth_path).resolve()
        self._bub_version = bub_version
        self._acp_server_version = acp_server_version
        _require_agent_inputs(self._wheel_dir, self._codex_auth_path)
        super().__init__(
            registry_entry={
                "id": AGENT_ID,
                "name": "PowerContext Bub ACP",
                "version": f"bub-{bub_version}",
                "description": "Bub with the local PowerContext integration",
                "distribution": {"uvx": {"package": f"bub-acp-server=={acp_server_version}"}},
            },
            distribution_preference=["uvx"],
            auth_policy="disabled",
            permission_mode="allow",
            **kwargs,
        )

    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_root(
            environment,
            command=self._build_dependencies_command("uvx"),
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        await environment.exec(command=f"mkdir -p {REMOTE_CODEX_HOME}", user="root")
        await environment.upload_dir(source_dir=self._wheel_dir, target_dir=REMOTE_WHEEL_DIR)
        await environment.upload_file(
            source_path=self._codex_auth_path,
            target_path=f"{REMOTE_CODEX_HOME}/auth.json",
        )

        agent_user = shlex.quote(str(environment.default_user or "root"))
        await self.exec_as_root(
            environment,
            command=(
                f"mkdir -p {REMOTE_CODEX_HOME} /installed-agent/bub-home && "
                f"chown -R {agent_user} {REMOTE_CODEX_HOME} /installed-agent/bub-home {REMOTE_WHEEL_DIR} && "
                f"chmod 700 {REMOTE_CODEX_HOME} && chmod 600 {REMOTE_CODEX_HOME}/auth.json"
            ),
        )

        wheel_arguments = " ".join(
            shlex.quote(f"{REMOTE_WHEEL_DIR}/{wheel.name}") for wheel in sorted(self._wheel_dir.glob("*.whl"))
        )
        uv = f"{self._RUNNER_VENV_PATH}/bin/uv"
        await self.exec_as_agent(
            environment,
            command=(
                f"{uv} venv --python 3.12 {REMOTE_AGENT_ENV} && "
                f"{uv} pip install --python {REMOTE_AGENT_ENV}/bin/python "
                f"{shlex.quote(f'bub=={self._bub_version}')} "
                f"{shlex.quote(f'bub-acp-server=={self._acp_server_version}')} {wheel_arguments}"
            ),
        )

        launcher_path = self.logs_dir / "acp-launch.sh"
        launcher_path.write_text(
            f'#!/usr/bin/env sh\nset -eu\nexec {REMOTE_AGENT_ENV}/bin/bub-acp-server "$@"\n',
            encoding="utf-8",
        )
        await environment.upload_file(source_path=launcher_path, target_path=self._LAUNCHER_REMOTE_PATH)
        harbor_runner = Path(harbor_acp.__file__).with_name("acp_runner.py")
        await environment.upload_file(source_path=harbor_runner, target_path=self._RUNNER_REMOTE_PATH)
        await environment.exec(
            command=f"chmod +x {self._LAUNCHER_REMOTE_PATH} {self._RUNNER_REMOTE_PATH}",
            user="root",
        )
        self._selected_distribution_kind = "uvx"
        await self._resolve_host_gateway(environment)

    async def _resolve_host_gateway(self, environment: BaseEnvironment) -> None:
        configured_url = self._extra_env.get("POWERCONTEXT_BUB_BASE_URL", "")
        if "host-gateway" not in configured_url:
            return
        script = (
            "import socket, struct; "
            "rows=open('/proc/net/route', encoding='ascii').read().splitlines()[1:]; "
            "gateway=next(row.split()[2] for row in rows if row.split()[1]=='00000000'); "
            "print(socket.inet_ntoa(struct.pack('<L', int(gateway, 16))))"
        )
        result = await environment.exec(command=f"python3 -c {shlex.quote(script)}")
        gateway = (result.stdout or "").strip()
        if result.return_code != 0 or not gateway:
            raise HostGatewayError
        self._extra_env["POWERCONTEXT_BUB_BASE_URL"] = configured_url.replace("host-gateway", gateway)


def _require_agent_inputs(wheel_dir: Path, codex_auth_path: Path) -> None:
    wheels = sorted(wheel_dir.glob("*.whl")) if wheel_dir.is_dir() else []
    wheel_names = {wheel.name.replace("-", "_") for wheel in wheels}
    if not any(name.startswith("powercontext_") and not name.startswith("powercontext_bub_") for name in wheel_names):
        raise AgentInputError("wheel_dir", "contain a PowerContext wheel")
    if not any(name.startswith("powercontext_bub_") for name in wheel_names):
        raise AgentInputError("wheel_dir", "contain a powercontext-bub wheel")
    if not codex_auth_path.is_file():
        raise AgentInputError("codex_auth_path", "point to an existing Codex auth file")
    try:
        auth_payload = json.loads(codex_auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentInputError("codex_auth_path", "contain valid JSON") from exc
    if not isinstance(auth_payload, dict) or not isinstance(auth_payload.get("tokens"), dict):
        raise CodexAuthFormatError
