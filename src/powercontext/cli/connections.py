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


"""Explicit local connection configuration and Server-owned Codex project binding CLI."""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from powercontext.client.client import PowerContextClient
from powercontext.client.connections import (
    bind_project,
    configure_connection,
    inspect_connection,
    inspect_project,
    unbind_project,
)
from powercontext.client.errors import ClientError
from powercontext.client.settings import ClientSettings

connection_app = typer.Typer(
    name="connection", no_args_is_help=True, help="Inspect and configure saved Client connections."
)
project_app = typer.Typer(name="project", no_args_is_help=True, help="Manage existing Codex checkout Scope bindings.")


def _output(operation: Callable[[], dict[str, Any]]) -> None:
    try:
        value = operation()
    except (OSError, ValueError) as error:
        typer.echo(f"Connection operation failed ({type(error).__name__}); inspect the resource and inputs.", err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(value, indent=2, ensure_ascii=False))


@connection_app.command("inspect")
def inspect_command(host: Annotated[str, typer.Option(help="Client or supported host entry.")] = "client") -> None:
    """Return one non-secret connection snapshot as JSON without network calls."""
    _output(lambda: inspect_connection(host))


@connection_app.command("configure")
def configure_command(
    server_url: Annotated[str, typer.Option(help="Endpoint to save.")],
    expected_revision: Annotated[str, typer.Option(help="Revision returned by connection inspect.")],
    host: Annotated[str, typer.Option(help="Client or supported host entry.")] = "client",
    allow_insecure_http: Annotated[bool, typer.Option(help="Explicitly allow plaintext non-loopback HTTP.")] = False,
    api_token_env: Annotated[
        str | None, typer.Option(help="Client credential environment-variable name, never its value.")
    ] = None,
    clear_token_reference: Annotated[bool, typer.Option(help="Remove the saved Client credential reference.")] = False,
) -> None:
    """Save one connection; no native host configuration or network is changed."""
    _output(
        lambda: configure_connection(
            host,
            server_url=server_url,
            expected_revision=expected_revision,
            allow_insecure_http=allow_insecure_http,
            api_token_env=api_token_env,
            clear_token_reference=clear_token_reference,
        )
    )


async def _project_operation(
    operation: Callable[[PowerContextClient], Awaitable[dict[str, Any]]],
    *,
    server_url: str | None,
    allow_insecure_http: bool | None,
) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if server_url is not None:
        overrides["server_url"] = server_url
    if allow_insecure_http is not None:
        overrides["allow_insecure_http"] = allow_insecure_http
    settings = ClientSettings(**overrides)
    async with PowerContextClient(
        settings.server_url,
        token=settings.api_token.get_secret_value() if settings.api_token else None,
        timeout=settings.timeout,
        allow_insecure_http=settings.allow_insecure_http,
    ) as client:
        return await operation(client)


def _run_project(
    operation: Callable[[PowerContextClient], Awaitable[dict[str, Any]]],
    server_url: str | None,
    allow_insecure_http: bool | None,
) -> None:
    try:
        result = asyncio.run(
            _project_operation(operation, server_url=server_url, allow_insecure_http=allow_insecure_http)
        )
    except (ClientError, OSError, ValueError, subprocess.SubprocessError) as error:
        # Do not expose server payloads or input values through ordinary diagnostics.
        typer.echo(f"Project operation failed ({type(error).__name__}); no automatic retry was performed.", err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


@project_app.command("inspect")
def project_inspect(
    project: Annotated[Path, typer.Option(help="Exact checkout directory.")],
    server_url: Annotated[str, typer.Option(help="Explicit API endpoint.")],
    allow_insecure_http: Annotated[bool | None, typer.Option(help="Explicit plaintext HTTP consent.")] = None,
) -> None:
    """Resolve the existing binding; an unbound project never falls back to default."""
    _run_project(lambda client: inspect_project(client, project), server_url, allow_insecure_http)


@project_app.command("bind")
def project_bind(
    project: Annotated[Path, typer.Option(help="Exact checkout directory.")],
    scope_id: Annotated[str, typer.Option(help="Existing Scope ID issued by the selected Server.")],
    server_url: Annotated[str, typer.Option(help="Explicit API endpoint.")],
    allow_insecure_http: Annotated[bool | None, typer.Option(help="Explicit plaintext HTTP consent.")] = None,
) -> None:
    """Persist an exact existing Scope binding on the selected Server."""
    _run_project(lambda client: bind_project(client, project, scope_id), server_url, allow_insecure_http)


@project_app.command("unbind")
def project_unbind(
    project: Annotated[Path, typer.Option(help="Exact checkout directory.")],
    server_url: Annotated[str, typer.Option(help="Explicit API endpoint.")],
    allow_insecure_http: Annotated[bool | None, typer.Option(help="Explicit plaintext HTTP consent.")] = None,
) -> None:
    """Clear the exact binding without deleting the Scope."""
    _run_project(lambda client: unbind_project(client, project), server_url, allow_insecure_http)
