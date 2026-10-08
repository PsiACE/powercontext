---
title: Client Connections and Project Bindings
description: Explain saved Client endpoints and explicitly manage existing Codex checkout Scope bindings.
---

- Proposal Name: `client_connections_and_project_bindings`
- Start Date: 2026-10-09
- Status: Implemented
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
and resource revision as JSON without making network calls. All connection/project commands emit JSON on success and diagnostics on stderr. `connection configure` saves endpoint/consent and an optional Client
token environment-variable reference, rejects runtime overrides that would undo the choice, and requires the observed
revision. It does not reconfigure native MCP or claim host activation.

`powercontext project inspect`, `bind`, and `unbind` operate on an existing Codex checkout binding at the selected Server.
Bind accepts an exact existing Scope ID; it neither creates a Scope by display name nor transfers one from a different Server.
Use `project bind --project /path/to/checkout --server-url https://server.example --scope-id SCOPE_ID`, then `project inspect` or `project unbind` with the same explicit endpoint/project. A changed endpoint resolves against that Server's bindings.

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

# Drawbacks

The file lock coordinates cooperating writers only. Native host files may need an explicit reload, and environment credential references are process-dependent. Project operations currently target Codex workspace keys only.

# Rationale and alternatives

[Executed evidence](https://github.com/PsiACE/powercontext/blob/cce34000/experiments/usability/connections/README.md) supports extending existing operations. No new
named-connection database or local Scope replica is required. The CLI and HTTP client expose these operations; current
host adapters retain their native contracts until individually migrated.

# Prior art

The pinned Magpie, Lody and Agenvo source inspections in the evidence report motivate explicit identity and separate saved/native outcomes. Their applications were not executed for this qualification.

# Unresolved questions

Named connections, native credential backends, and other host project bindings require separate supported consumers and
acceptance. They do not block this implementation.

# Future possibilities

Named connections, additional host binding keys, and native secret stores can extend this contract when supported consumers and native acceptance exist.

Qualification: focused tests cover persistence, conflicts, credentials and actual in-process SQLite/HTTP API binding behavior. A separately built `[cli]` wheel resolved Typer 0.27.3 with neither Click nor SQLAlchemy installed and passed the native concurrent-configure/readback subprocess scenario. Native Agent behavior and Windows/macOS were not executed.
