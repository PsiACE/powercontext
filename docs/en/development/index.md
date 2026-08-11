---
title: Development guides
description: Extend PowerContext, compose its runtime, and validate implementation changes.
---

# Development guides

These guides are for contributors and application developers working below the public CLI and Client interfaces.
Start with [CONTRIBUTING.md](https://github.com/oceanbase/powercontext/blob/master/CONTRIBUTING.md) for repository
setup, tests, generated code, and pull request requirements.

## Architecture and extension points

- [Core protocol and composition](core-protocol.md) explains Source, Artifact, Trigger, composition, and package
  ownership.
- [Builtin Memory layer](memory-layer.md) covers persistence, entry evolution, search, and supported databases.
- [Pydantic AI inference](pydantic-ai-inference.md) covers the optional generation and embedding integration.

## Server implementation

- [Remote access](remote-access-implementation.md) covers Server configuration, HTTP, the Client SDK, CLI, and MCP.
- [Server web UI](server-web-ui.md) covers Server-owned pages and their security boundary.

The generated [Python API reference](../modules.md) documents public modules. For a proposed or historical design,
consult the [RFC index](../rfcs/README.md) and verify current behavior in source and tests.
