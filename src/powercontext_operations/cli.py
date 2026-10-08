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

"""Maintenance commands that remain usable without importing Runtime."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import urlsplit

from powercontext_operations.adapters import NativeServiceAdapter, native_service_adapter
from powercontext_operations.locking import service_lock
from powercontext_operations.model import (
    ManagerOwnershipState,
    ManagerState,
    RegistrationState,
    ServiceError,
    SupportState,
)


def installed_version() -> str | None:
    try:
        return version("powercontext")
    except PackageNotFoundError:
        return None


def status(adapter: NativeServiceAdapter) -> dict[str, object]:
    """Report registration and manager facts independently of Server health."""
    support, detail = adapter.support()
    registration = adapter.inspect()
    loaded = adapter.loaded_registration() if support is SupportState.SUPPORTED else None
    definition = registration.definition
    return {
        "package_version": installed_version(),
        "operations_python": os.path.abspath(sys.executable),
        "support": support.value,
        "registration": registration.state.value,
        "artifact": str(adapter.artifact_path),
        "manager_ownership": loaded.state.value if loaded else "unknown",
        "manager": adapter.manager_state().value if support is SupportState.SUPPORTED else "unknown",
        "registered_python": definition.python_executable if definition else None,
        "registered_package_version": definition.package_version if definition else None,
        "registered_python_exists": Path(definition.python_executable).is_file() if definition else None,
        "log_location": adapter.log_location(definition),
        "server_liveness": "unknown",
        "detail": registration.detail or detail,
    }


def require_owned(adapter: NativeServiceAdapter) -> None:
    support, detail = adapter.support()
    if support is not SupportState.SUPPORTED:
        raise ServiceError(detail)
    registration = adapter.inspect()
    loaded = adapter.loaded_registration()
    if registration.state is not RegistrationState.INSTALLED or registration.definition is None:
        raise ServiceError("refusing service mutation: no valid owned registration")  # noqa: TRY003
    if loaded.state not in (ManagerOwnershipState.OWNED, ManagerOwnershipState.NOT_LOADED):
        raise ServiceError("refusing service mutation: manager ownership is foreign or unknown")  # noqa: TRY003


def service_operation(adapter: NativeServiceAdapter, operation: str) -> dict[str, object]:
    with service_lock(adapter.lock_path):
        require_owned(adapter)
        if operation == "start":
            adapter.start(reload_definition=True)
        elif operation == "stop":
            adapter.stop()
        elif operation == "restart":
            adapter.stop()
            adapter.start(reload_definition=True)
        elif operation == "uninstall":
            # Preserve the artifact and its recovery evidence if any preceding stage fails.
            adapter.stop()
            adapter.disable()
            adapter.remove()
            adapter.reload()
        return status(adapter)


def runtime_check(python: str) -> dict[str, object]:
    try:
        result = subprocess.run(  # noqa: S603 - shell-free commands use owned interpreter or explicitly selected uv launchers.
            [python, "-c", "from powercontext.cli.app import main; main()", "--help"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"python": python, "cli_ready": False}
    return {"python": python, "cli_ready": result.returncode == 0}


def tool_bin(uv: str) -> Path:
    result = subprocess.run(  # noqa: S603 - shell-free commands use owned interpreter or explicitly selected uv launchers.
        [uv, "tool", "dir", "--bin"], capture_output=True, text=True, check=False
    )
    if result.returncode or not result.stdout.strip():
        raise ServiceError("uv could not report its selected tool executable directory")  # noqa: TRY003
    return Path(result.stdout.strip()).absolute()


def repair(adapter: NativeServiceAdapter, *, exact: str, profile: str, index: str | None) -> dict[str, object]:
    if not re.fullmatch(
        r"[0-9]+(?:[.][0-9]+)*(?:(?:a|b|rc)[0-9]+|[.]post[0-9]+|[.]dev[0-9]+)?(?:[+][a-zA-Z0-9]+(?:[.][a-zA-Z0-9]+)*)?",
        exact,
    ):
        raise ServiceError("repair requires an exact numeric release version")  # noqa: TRY003
    if index:
        parsed = urlsplit(index)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query:
            raise ServiceError("--index-url requires a credential-free HTTPS index URL")  # noqa: TRY003
    uv = shutil.which("uv")
    if uv is None:
        raise ServiceError("uv is missing; use the published installation guide to bootstrap it")  # noqa: TRY003
    selected_bin = tool_bin(uv)
    with service_lock(adapter.lock_path):
        registration = adapter.inspect()
        if registration.state is RegistrationState.INSTALLED:
            require_owned(adapter)
            if adapter.manager_state() is not ManagerState.INACTIVE:
                raise ServiceError("stop the owned Server explicitly before package repair")  # noqa: TRY003
        extras = "cli,server" if profile == "local" else "cli"
        command = [uv, "tool", "install", "--reinstall", f"powercontext[{extras}]=={exact}"]
        if index:
            command.extend(("--default-index", index))
        result = subprocess.run(  # noqa: S603 - shell-free commands use owned interpreter or explicitly selected uv launchers.
            command, check=False
        )
        if result.returncode:
            raise ServiceError("uv package repair failed; service registration and data were retained")  # noqa: TRY003
        result_payload = verify_repaired_launchers(selected_bin, exact=exact, profile=profile)
        result_payload["source_selection"] = "explicit-default-index" if index else "current-uv-configuration"
        result_payload["requested_default_index"] = index
        return result_payload


def verify_repaired_launchers(selected_bin: Path, *, exact: str, profile: str) -> dict[str, object]:
    suffix = ".exe" if os.name == "nt" else ""
    ops = selected_bin / f"powercontext-ops{suffix}"
    try:
        verified = subprocess.run(  # noqa: S603 - shell-free commands use owned interpreter or explicitly selected uv launchers.
            [str(ops), "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ServiceError(  # noqa: TRY003
            "package installation completed but selected Ops launcher verification failed"
        ) from error
    if verified.returncode or verified.stdout.strip() != exact:
        raise ServiceError("package installation completed but selected Ops version verification failed")  # noqa: TRY003
    launcher = selected_bin / f"powercontext{suffix}"
    try:
        ready = (
            subprocess.run(  # noqa: S603 - shell-free commands use owned interpreter or explicitly selected uv launchers.
                [str(launcher), "--help"], capture_output=True, timeout=30, check=False
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        ready = False
    return {
        "target": "uv-tool",
        "bin_directory": str(selected_bin),
        "package_version": exact,
        "profile": profile,
        "operations_launcher": str(ops),
        "runtime_launcher": str(launcher),
        "runtime_cli_ready": ready,
        "service_action": "none",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Independent package and owned native Server maintenance")
    parser.add_argument("--version", action="version", version=installed_version() or "unknown")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "doctor"):
        commands.add_parser(name)
    server = commands.add_parser("server")
    server.add_argument("operation", choices=("start", "stop", "restart", "logs", "uninstall"))
    package = commands.add_parser("repair")
    package.add_argument("--target", choices=("uv-tool",), required=True)
    package.add_argument("--version", dest="exact", required=True)
    package.add_argument("--profile", choices=("local", "client"), required=True)
    package.add_argument("--index-url")
    args = parser.parse_args(argv)
    adapter = native_service_adapter()
    try:
        if args.command == "repair":
            payload = repair(adapter, exact=args.exact, profile=args.profile, index=args.index_url)
        elif args.command == "server" and args.operation != "logs":
            payload = service_operation(adapter, args.operation)
        else:
            payload = status(adapter)
            if args.command == "doctor":
                payload["runtime"] = runtime_check(str(payload["registered_python"] or sys.executable))
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    except (ServiceError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
