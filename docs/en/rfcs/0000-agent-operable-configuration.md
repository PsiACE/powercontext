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
and does not claim reachability. Existing text modes retain compatibility.

`plan` accepts a strict version 1 JSON document containing resource, base revision, `set`, and `unset`. Fields cannot appear
in both actions. Omitted assignments remain unchanged. It validates supported types and owning cross-field invariants,
then returns changes and restart requirements without writes or network calls. `apply` repeats validation and checks the
same revision under a local lock before atomically saving mode-0600 content. A conflict never overwrites the current file.

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

# Acceptance

Protect typed field discovery, unknown-field rejection, preview purity, partial preservation, private-value redaction,
invalid combinations, stale and concurrent writer conflicts, mode-0600 readback, multiline syntax and accurate activation.
Run public CLI scenarios and cross-process writes. Real network/model/host acceptance is separate.

# Rationale and alternatives

[Executed ablations](../../../experiments/usability/configuration/README.md) demonstrate the need for revision checking,
strict change fields and public output allowlists. They do not justify replacing `.env`, adding a TOML engine, daemon,
arbitrary provider request passthrough, or a global transaction across onboarding steps.

# Unresolved questions

Provider-specific editing, native secret backends, and observation-bound online checks require concrete consumers. They
remain independently deliverable and do not block the local static operations.
