---
title: Interfaces
description: Choose between the Codex plugin, CLI, Python SDKs, HTTP, and MCP.
---

# Interfaces

PowerContext can run in a local process or behind the Server. Choose the smallest interface that matches where your
code runs and who owns persistence.

| Interface | Intended use | Install or enable |
| --- | --- | --- |
| Codex plugin | Cross-session recall and explicit Memory maintenance in Codex | `powercontext setup codex` |
| CLI | Setup, diagnostics, Server control, capability checks, and Candidate review | `powercontext[cli,server]` |
| Python Client SDK | Typed asynchronous calls to a running Server | `powercontext[client]` |
| Core SDK | In-process Source, Artifact, Trigger, and composition contracts | base package |
| HTTP | Service integration from any language | `powercontext[server]` |
| MCP | Agent tools for Memory and Candidate Review | enabled by Server |

All remote interfaces operate on the same Server and persistent Artifact storage. The Core SDK is different: the
application owns composition and resource lifetime.

## Codex plugin

The project-context skill tells Codex when to search, remember, revise, or retire Memory. The prompt hook recalls
relevant entries and captures user input as Source evidence. MCP tools perform explicit operations. The plugin does
not start or embed the Server.

See [Configure Codex](../how-to/configure-codex.md) for setup, scope selection, prompt capture, and authentication.

## CLI

Use the CLI for installation, diagnostics, Server control, and human review. The [CLI reference](cli.md) lists the
main command groups and their evidence and concurrency rules.

## Python SDKs

Use the [Python Client SDK](python-client.md) when a Server owns persistence. Use the Core SDK when the application
owns its composition root. The [Core protocol guide](../../development/core-protocol.md) describes Core contracts and
the Builtin runtime boundary. The base `powercontext` package does not select storage, scheduling, transport, or
inference for the application. Install `builtin` to use the supplied SQLite or OceanBase-backed implementation in the
same process. The generated [Python API reference](../../modules.md) lists public modules and types.

## HTTP and MCP

HTTP exposes the complete application contract. MCP provides a smaller set of agent-facing operations. See the
[HTTP and MCP reference](http-and-mcp.md) for endpoints and availability boundaries.

## Artifact generation and review

Experience generation, managed Skills, and external Skill import use explicit evidence and human review. The
[Artifact lifecycle](../explanation/artifact-lifecycle.md) explains what models may propose, what approval creates,
and what remains outside recall or execution.
