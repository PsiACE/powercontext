---
title: Install and run
description: Install PowerContext and selected integrations, then run the local Server.
---

# Install and run

## Install the application and integrations

On macOS or Linux, select a Runtime profile and each host explicitly:

```bash
curl -fsSL https://raw.githubusercontent.com/oceanbase/powercontext/master/install.sh | bash -s -- \
  --profile local \
  --host codex \
  --host claude-code \
  --yes
```

On Windows PowerShell:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/oceanbase/powercontext/master/install.ps1))) `
  --profile local --host codex --host claude-code --yes
```

The installer obtains `uv` when necessary, creates a dedicated per-user virtual environment, exposes the
`powercontext` executable, installs selected integrations through their native host marketplaces, and verifies the
observable host state. Use `--no-hosts` for a Runtime-only installation. Omit `--yes` to review and confirm the plan in
an interactive terminal.

## Run the local Server

```bash
powercontext server run
```

With no environment variables, the Server:

- binds to `127.0.0.1:8000`;
- enables Streamable HTTP MCP at `/mcp`;
- enables the Dashboard at `/`; when no scopes are configured, the page shows an explicit empty state;
- creates a persistent SQLite database in the operating system's user data directory;
- supports explicit Memory operations without an inference provider.

After startup, the terminal prints the Dashboard URL, such as `http://127.0.0.1:8000/`. The Dashboard shares the
Server listener and port with the HTTP API and MCP. If Dashboard initialization fails, the Server logs a warning with
the direct cause and continues serving the other interfaces. Set `POWERCONTEXT_SERVER_DASHBOARD_ENABLED=false` to
disable the Dashboard explicitly.

`Ctrl-C` performs a clean shutdown. Restarting the command reopens the same database.

## Use embedded seekDB

Embedded seekDB is available on Linux and macOS when a compatible `pylibseekdb` wheel is available. Windows does not
support this embedded backend. Install or replace the Runtime with the seekDB profile:

```bash
curl -fsSL https://raw.githubusercontent.com/oceanbase/powercontext/master/install.sh | bash -s -- \
  --profile seekdb --no-hosts --yes
```

When switching from SQLite, remove `POWERCONTEXT_SERVER_DATABASE_URL` and
`POWERCONTEXT_SERVER_DATABASE_VEC1_EXTENSION` from `.env`, or unset them in the shell. Those settings are not valid
for seekDB. Then select the backend and start the Server:

```bash
unset POWERCONTEXT_SERVER_DATABASE_URL
unset POWERCONTEXT_SERVER_DATABASE_VEC1_EXTENSION
export POWERCONTEXT_SERVER_DATABASE_KIND=seekdb
powercontext server run
```

PowerContext always uses seekDB's built-in `test` database. Leave `POWERCONTEXT_SERVER_DATABASE_PATH` unset to store
the instance in the `seekdb` subdirectory of the PowerContext user data directory. If `POWERCONTEXT_HOME` is set, the
default is `$POWERCONTEXT_HOME/seekdb`; set `POWERCONTEXT_SERVER_DATABASE_PATH` only when a different location is
required.

In another terminal, verify that the Server and database are ready:

```bash
powercontext doctor
powercontext ready
powercontext capabilities
```

## Verify the installation

```bash
powercontext doctor
powercontext doctor codex
powercontext doctor dsh
powercontext doctor pi
powercontext ready
powercontext capabilities
```

`doctor` checks the installed package, Server liveness, and Server readiness without requiring an integration. Server
readiness covers the database and each configured inference provider. Runtime or database failures return
`not_ready`; an inference failure returns `degraded` without removing database-backed operations from traffic.
`doctor codex`, `doctor dsh`, and `doctor pi` separately check their optional host CLI and PowerContext integration. The content commands exercise the
public HTTP SDK path. `ready` and `capabilities` show the readiness and enabled capabilities of the running service. For complete status definitions and
recovery steps, see [Troubleshoot](troubleshoot.md).

## Update or replace an installation

Repeat the installer with the desired profile and hosts. During development, pass `--ref` to install both the Runtime
and marketplace from the same Git ref:

```bash
curl -fsSL https://raw.githubusercontent.com/oceanbase/powercontext/master/install.sh | bash -s -- \
  --ref <git-ref> --profile local --host codex --yes
```

Restart the Server and open a new host session after updating. Existing SQLite data remains in the user data
directory unless `POWERCONTEXT_HOME` or the database URL changes.

## Install a Python role

An application that imports the async Client SDK should add it to that application's environment:

```bash
uv add "powercontext[client] @ git+https://github.com/oceanbase/powercontext.git@master"
```

Use `builtin` for in-process Python composition, `server` for the service, `client` for the Python SDK, or `cli` for
the Server-backed command line.
An extra that is only present in the isolated `uv tool` environment is not importable by an unrelated Python project.
