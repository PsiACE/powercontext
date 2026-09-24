---
title: Integration capabilities
description: Tool and hook registrations generated from executable distribution bindings.
---

# Integration capabilities

The table is generated from the tool catalog used by native registration and setup. Extensions preserve existing native tools alongside the shared baseline. MCP tools are discovered from the Server; SDK integrations expose application APIs rather than a fixed model toolkit. Hook rows use the bindings consumed by command manifests, WorkBuddy setup, or the TypeScript event registrar. Empty cells mean there is no distribution-managed event registration: Bub, Hermes, and framework callbacks are discovered through native class interfaces. Their method names are not maintained in a second catalog. Host policy still determines runtime availability.

<!-- integration-catalog:start -->

| Target | Language | Tools (baseline + extensions) | Hook registrations |
| --- | --- | --- | --- |
| Codex | python | Server MCP | `SessionStart`, `PreToolUse`, `UserPromptSubmit`, `Stop` |
| Claude Code | python | Server MCP | `UserPromptSubmit` |
| DeepSeek Harness | typescript | 14 + 9 | `agent/pre-step`, `tools/pre-execute` |
| OpenClaw | typescript | 14 + 0 | `before_prompt_build`, `agent_end`, `before_compaction`, `session_end` |
| OpenCode | typescript | 14 + 9 | `chat.message`, `experimental.chat.messages.transform`, `experimental.chat.system.transform`, `event` |
| Pi | typescript | 14 + 20 | `before_agent_start`, `agent_end`, `session_before_compact`, `session_before_switch`, `session_shutdown` |
| Hermes | python | 14 + 21 | — |
| WorkBuddy | python | Server MCP | `UserPromptSubmit` |
| MiniMax | python | Server MCP | `UserPromptSubmit` |
| Bub | python | SDK | — |
| LangChain | python | SDK | — |
| LangGraph | python | SDK | — |
| Pydantic AI | python | SDK | — |
| OpenDAL | python | SDK | — |
| Agent Plugin | none | Server MCP | — |

<!-- integration-catalog:end -->

Inspect individual tool names and operations as JSON, or render the comparison directly:

```bash
uv run python scripts/build_agent_distributions.py --list
uv run python scripts/build_agent_distributions.py --list --format markdown
powercontext doctor integrations --json
```

All targets use Python setup and doctor through HostAdapter. The table describes packaged integrations; doctor checks local installation and configuration. Host policy and session state determine which tools are available at runtime. `make agent-resources` refreshes this table together with plugin resources. See [plugin architecture and distribution](../../development/plugin-distribution.md).
