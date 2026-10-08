---
title: Agent-Operable Configuration
description: Discover, inspect, preview and apply typed local .env changes with private output and revision conflicts.
---

- Proposal Name: `agent_operable_configuration`
- Start Date: 2026-10-09
- Status: Proposed
- RFC PR: Not opened
- Related RFC: [RFC 1733](1733-usability-and-agent-workflows.md)

# Summary

Extend existing `powercontext config` with schema discovery, structured inspection/static validation, and non-interactive
partial plan/apply operations. Keep `.env` persistence and its shell-free parser. Begin with an explicit supported field
catalog derived from the owning settings models, not a competing configuration language.

# Motivation

Agents need a deterministic local resource and structured operations instead of simulating a questionnaire. Atomic
replacement alone loses concurrent changes. Generic settings ignore unknown environment names, which is appropriate for
runtime extension but insufficient for validating a change request. Ordinary output must exclude unknown private values.

# Guide-level explanation

`config schema --json` discovers writable typed fields and environment names. `show --json` reports revision, resource,
public settings, source/provenance, and secret presence without credential values. `validate --json` reports static checks
and does not claim reachability. Text inspection retains assignment names but redacts every unknown value, including previously visible values. Server text validation retains its existing runtime preflight; JSON validation is static.

`plan` accepts a strict version 1 JSON document containing `schema_version: 1`, `target: server|client`, resource, base revision, `set`, and `unset`. Fields cannot appear
in both actions. Omitted assignments remain unchanged. It validates supported types and owning cross-field invariants,
then returns changes and restart requirements without writes or network calls. `apply` repeats validation and checks the
same revision under a local lock before atomically saving mode-0600 content. A conflict never overwrites the current file.

For a Client file, `config show --target client --env-file .env --json` returns the byte revision. Save an explicit intent document:

```json
{"schema_version":1,"target":"client","resource":"/absolute/project/.env","base_revision":"missing","set":{"timeout":12.0},"unset":[]}
```

`config plan --request-file change.json --json` previews it; `config apply --request-file change.json --json` authorizes that local write. Use the inspected revision for existing files. Request-file `-` reads stdin. For credentials, `"api_token":{"from_env":"POWERCONTEXT_NEW_TOKEN"}` or `{"from_file":"/private/token"}` supplies a controlled value; references are resolved afresh at plan/apply and are not echoed. This stores the value in the existing private `.env`, unlike connection token references that remain references.

Client operations run with `[cli]` alone. Server operations require Server extras. Client activation is `next_invocation`; Server activation is `restart_required`. No command starts a process.

# Reference-level explanation

Resource paths are explicit and canonical; no upward directory search is introduced. Existing `.env` syntax is parsed
without executing shell code. Preserve comments and all omitted values, including provider credentials. Reject unknown
fields, versions, malformed inputs and masked credential replacement values before effects. Initial typed public fields
cover listener, Dashboard, access/authentication, MCP, logging and Client connection controls. Sensitive changes use
controlled input; ordinary plan/show/error outputs report only presence and changed field names.

Read revision identifies complete file bytes, including comments and private assignments. A distinct missing-file revision
allows explicit creation. A cooperating-writer lock covers read/check/replace; arbitrary external editors do not participate
in this protocol. Publish only complete snapshots and clean only owned temporary files. Configuration writes are local and
have no remote management authority.

Apply reports persistence separately from activation: `saved_revision`, `activation: restart_required`, and static check
results. No running process is declared active without observation. No service is started, model is probed, or business
record written implicitly. JSON is composable output, not permission for these effects. A dedicated connection workflow
can use the same concepts without introducing named connections.

## Acceptance

Protect typed field discovery, unknown-field rejection, preview purity, partial preservation, private-value redaction,
invalid combinations, stale and concurrent writer conflicts, mode-0600 readback, multiline syntax and accurate activation.
Run public CLI scenarios and cross-process writes. Real network/model/host acceptance is separate.

Linux acceptance executes real subprocess concurrency and a fresh `[cli]` wheel with Typer 0.27.3, no Click and no SQLAlchemy. The Windows portability workflow includes the configuration suite; Windows/macOS execution has not been observed locally.

# Drawbacks

Only the public bounded scalar field catalog is writable. Provider/database editing remains with existing setup interfaces. Unix files use mode 0600; Windows relies on the user's filesystem ACLs and does not claim Unix permission enforcement. Locks coordinate these writers, not arbitrary editors. Changed assignments are normalized; unrelated comments and multiline assignments remain intact.

# Rationale and alternatives

[Executed ablations](https://github.com/PsiACE/powercontext/blob/cce34000/experiments/usability/configuration/README.md) demonstrate the need for revision checking,
strict change fields and public output allowlists. They do not justify replacing `.env`, adding a TOML engine, daemon,
arbitrary provider request passthrough, or a global transaction across onboarding steps.

# Prior art

[Cida](https://github.com/Xuanwo/cida/blob/a48745e79632f93d6763605d5718ab4b7cea1122/Sources/Cida/ConfigurationFields.swift) derives schema/parser/display from owning fields. [Jiandao](https://github.com/Xuanwo/jiandao/blob/76a3fbc0501ac9c6f5cb5aa88a16d0923bff97b3/src/utils/setup-document.ts) separates strict setup intent from storage. [Agenvo](https://github.com/Xuanwo/agenvo/blob/235978f9fd9cf70fd75b03292619262d5e86a6e6/docs/installation.md) separates saved configuration, deployment and reachability. These sources were inspected, not executed as acceptance evidence.

# Unresolved questions

Provider-specific editing, native secret backends, and observation-bound online checks require concrete consumers. They
remain independently deliverable and do not block the local static operations.

# Future possibilities

Provider-owned editing and independently authorized online checks can extend this contract when required. Native secret storage and observed running-process activation remain separate work.

