"""Thin Harbor ACP adapter for Bub's native plugin environment."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from harbor.agents.installed import acp as harbor_acp
from harbor.environments.base import BaseEnvironment

AGENT_ID = "powercontext-bub-acp"
REMOTE_BIN_DIR = "/installed-agent/bin"
REMOTE_BUB_PROJECT = "/installed-agent/bub-project"
REMOTE_CODEX_AUTH = "/run/powercontext/codex-auth.json"
REMOTE_CODEX_HOME = "/installed-agent/codex"
REMOTE_SOURCE = "/opt/powercontext/source"
REMOTE_TOOL_DIR = "/installed-agent/tools"


class PowerContextBubAcpAgent(harbor_acp.AcpAgent):
    """Install Bub through its supported uv tool and plugin commands."""

    def __init__(self, *, bub_version: str, acp_server_version: str, **kwargs: Any) -> None:
        self._bub_version = bub_version
        self._acp_server_version = acp_server_version
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
        await self.exec_as_root(environment, command=_install_command(self._bub_version, self._acp_server_version))
        agent_user = shlex.quote(str(environment.default_user or "root"))
        await self.exec_as_root(
            environment,
            command=(
                f"chown -R {agent_user} {REMOTE_BIN_DIR} {REMOTE_BUB_PROJECT} {REMOTE_CODEX_HOME} {REMOTE_TOOL_DIR}"
            ),
        )

        launcher_path = self.logs_dir / "acp-launch.sh"
        launcher_path.write_text(
            "#!/usr/bin/env sh\n"
            "set -eu\n"
            f'bub_bin=$(dirname "$(readlink -f {REMOTE_BIN_DIR}/bub)")\n'
            'exec "$bub_bin/bub-acp-server" "$@"\n',
            encoding="utf-8",
        )
        await environment.upload_file(source_path=launcher_path, target_path=self._LAUNCHER_REMOTE_PATH)
        runner_path = Path(harbor_acp.__file__).with_name("acp_runner.py")
        await environment.upload_file(source_path=runner_path, target_path=self._RUNNER_REMOTE_PATH)
        await environment.exec(
            command=f"chmod a+rx {self._LAUNCHER_REMOTE_PATH} {self._RUNNER_REMOTE_PATH}",
            user="root",
        )
        self._selected_distribution_kind = "uvx"


def _install_command(bub_version: str, acp_server_version: str) -> str:
    uv = f"{harbor_acp.AcpAgent._RUNNER_VENV_PATH}/bin/uv"
    tool_environment = f"UV_TOOL_BIN_DIR={shlex.quote(REMOTE_BIN_DIR)} UV_TOOL_DIR={shlex.quote(REMOTE_TOOL_DIR)}"
    return (
        "set -eu; "
        f"mkdir -p {REMOTE_BIN_DIR} {REMOTE_BUB_PROJECT} {REMOTE_CODEX_HOME}; "
        f"cp {REMOTE_CODEX_AUTH} {REMOTE_CODEX_HOME}/auth.json; "
        f"chmod 600 {REMOTE_CODEX_HOME}/auth.json; "
        f"{tool_environment} {uv} tool install --force "
        f"--with {REMOTE_SOURCE} --with {REMOTE_SOURCE}/integrations/bub "
        f"{shlex.quote(f'bub=={bub_version}')}; "
        f"CODEX_HOME={REMOTE_CODEX_HOME} BUB_PROJECT={REMOTE_BUB_PROJECT} "
        f"{tool_environment} {REMOTE_BIN_DIR}/bub install "
        f"{shlex.quote(f'bub-acp-server=={acp_server_version}')}"
    )
