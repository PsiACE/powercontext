---
title: PowerContext documentation
description: Learn PowerContext, complete common tasks, and look up interface behavior.
---

# PowerContext documentation

PowerContext stores project-scoped context for agents. It runs as a local or remote Server and exposes the same
durable Memory through Codex, Python, HTTP, and MCP.

## Get started

Start with the [Codex quickstart](tutorials/codex-quickstart.md) if you are installing PowerContext for yourself. The
tutorial begins with a Git installation and ends with a second Codex session restoring the first session's work.

## How-to guides

- [Install and run](how-to/install-and-run.md): install from Git, start the Server, and update it.
- [Configure Codex](how-to/configure-codex.md): install the plugin and control project scope and prompt capture.
- [Troubleshoot](how-to/troubleshoot.md): diagnose credentials, plugin, Server, database, and hook failures.

## Concepts

- [Artifact lifecycle](explanation/artifact-lifecycle.md): understand generation, review, approval, recall, and export.
- [Core protocol and composition](../development/core-protocol.md): understand Source, Artifact, Trigger, and ownership
  boundaries.

## Reference

- [Interfaces](reference/interfaces.md): choose between Codex, CLI, Client SDK, Core SDK, HTTP, and MCP.
- [CLI](reference/cli.md): look up command groups and their operating rules.
- [Python Client SDK](reference/python-client.md): call a running Server from Python.
- [HTTP and MCP](reference/http-and-mcp.md): check endpoints and transport availability.
- [Configuration](reference/configuration.md): look up defaults and environment variables.
- [Python API](../modules.md): browse generated reference for public modules and types.

For implementation work, use the [development guides](../development/index.md). For design history and proposals,
see the [RFC index](../rfcs/README.md). RFCs may describe behavior that has not been implemented.
