---
title: Client Connections and Project Bindings
description: Explain saved Client endpoints and explicitly manage existing Codex checkout Scope bindings.
---

- Proposal Name: `client_connections_and_project_bindings`
- Start Date: 2026-10-09
- Status: Proposed
- RFC PR: Not opened
- Related RFC: [RFC 1733](1733-usability-and-agent-workflows.md)

# Summary

Expose existing Client transport resolution and Codex project binding through reusable public operations and CLI commands.
Retain version 1 `clients.json`, environment credential ownership, and Server-owned Scope bindings. Named connections and
additional host projections are independent extensions rather than prerequisites.

# Motivation

The existing resolver has important safety behavior but cannot explain which input selected an endpoint. Host setup may
reject a conflict that one explicit request safely overrides. Codex checkout bindings are already durable Server records;
a second local catalog would introduce identity synchronization without solving a demonstrated problem.

# Guide-level explanation

`powercontext connection inspect` reports the selected host's endpoint, consent, source names, credential reference state
and resource revision without making network calls. `connection configure` saves endpoint/consent and an optional Client
token environment-variable reference, rejects runtime overrides that would undo the choice, and requires the observed
revision. It does not reconfigure native MCP or claim host activation.

`powercontext project inspect`, `bind`, and `unbind` operate on an existing Codex checkout binding at the selected Server.
Bind accepts an exact existing Scope ID; it neither creates a Scope by display name nor transfers one from a different Server.
Explicitly choose the endpoint and project. A changed endpoint resolves against that Server's bindings.

# Reference-level explanation

Keep constructor, host environment, common environment, saved value, and default precedence. Saved plaintext HTTP consent
is endpoint-bound, including the API/MCP suffix relationship. Endpoint URL values reject embedded credentials, queries and
fragments. Credential references initially name environment variables for the Client CLI only; no value is stored or printed.
Missing referenced credentials fail before a request. Existing explicit/environment token inputs keep their precedence.

Project identity is the canonical exact checkout root hashed with the existing Codex workspace algorithm. Separate Git
worktrees remain separate. Remote URL similarity and display names do not merge projects. Bind writes through the existing
Scope service and acknowledges only its returned exact binding; inspect disables fallback-to-default. Unbind reports the
existing service result. Failed or uncertain network writes are never automatically replayed.

Connection writes compare a byte revision under a local cooperating-writer lock and publish one complete JSON document.
Unknown file versions are rejected before writes. Preserve other host entries and unknown fields. Failed operations retain
the old file. This lock does not claim coordination with arbitrary external editors or multi-file host installers.

# Acceptance

Protect source precedence, same-versus-changed endpoint consent, environment conflict rejection, missing token references,
concurrent revision conflicts, cross-process readback, exact worktree identity, bind/unbind and endpoint switching through
actual API behavior. Native host activation remains outside this contract.

# Rationale and alternatives

[Executed evidence](../../../experiments/usability/connections/README.md) supports extending existing operations. No new
named-connection database or local Scope replica is required. The CLI and HTTP client expose these operations; current
host adapters retain their native contracts until individually migrated.

# Unresolved questions

Named connections, native credential backends, and other host project bindings require separate supported consumers and
acceptance. They do not block this implementation.
