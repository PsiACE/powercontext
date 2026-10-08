---
title: Hook Governance
description: Require exact Source acknowledgements while preserving native event, consent, budget, and output contracts.
---

- Proposal Name: `hook_governance`
- Start Date: 2026-10-09
- Status: Proposed
- RFC PR: Not opened
- Related RFC: [RFC 1733](1733-usability-and-agent-workflows.md)

# Summary

Govern automatic execution through shared observable cases and operation-specific validation. Codex, Claude Code and WorkBuddy validate the complete capture receipt before advancing a checkpoint. Native adapters retain their events, Scope binding, consent, credentials, deadlines and output. This correction needs no shared process, Client installation requirement or daemon.

# Motivation

The Server's `CaptureContentSourceResponse` requires `status: accepted`, a `content` Source reference, and a positive integer position. A positive position alone does not acknowledge the current write. At baseline `f28f8edf`, executable Codex and Claude subprocesses checkpoint after a position-only response and after a response naming an unrelated Source. Changing only receipt validation prevents both checkpoints, preserves a valid acknowledgement and keeps automatic execution non-blocking. [Reproduction and primary sources](https://github.com/oceanbase/powercontext/tree/f28f8edfcb2972ac322f4f12b1f224226a9a40ab) establish the code baseline; `experiments/usability/hooks/` contains the runnable bounded comparison. Synthetic HTTP responses exercise adapter handling, not a real host's event delivery.

# Guide-level explanation

Automatic recall and capture continue using the host's existing entry point. A valid capture receipt permits optional flushing through its acknowledged position. An invalid or undecodable receipt prevents flushing; it does not prevent the user from continuing the Agent session. The write may already have reached the Server, so this outcome must not be described as known non-persistence or automatically replayed.

Codex and Claude Code emit their existing content-free `capture_source` diagnostic with `invalid_response`. WorkBuddy retains its current silent capture-error handling and valid empty context output. Capture opt-out still makes no Source write. Installing shared guidance neither enables a new event nor grants capture permission.

# Reference-level explanation

## Acknowledgement contract

A capture is acknowledged only when all of these conditions hold:

1. The response contains exactly `status`, `source` and `position`.
2. `status` equals `accepted`.
3. `source` equals `{"name": "content", "source_id": REQUEST_SOURCE_ID}` with no extra fields.
4. `position` is a positive integer; boolean values are rejected.

Use the request's exact Source identity. Repeated acceptance of that same capture remains valid. The current API has no distinct `duplicate` status. This validation belongs at the capture response boundary and runs even when optional flushing is disabled. It does not change Server authorization or the existing idempotency coordinate.

## Native and shared responsibilities

| Concern | Shared contract | Native owner |
| --- | --- | --- |
| Receipt | Exact acknowledged Source identity and position | Request construction and transport |
| Recall | Strict prepared-context shape; empty is valid | Input selection and injection format |
| Capture | Consent and acknowledgement gate checkpoints | Native eligibility and session/event identity |
| Budget | All network steps consume the existing absolute deadline | Verified host timeout and request limits |
| Failure | Automatic operations remain non-blocking; unknown writes are not replayed | Native diagnostic channel and throttling |
| Scope/auth | Consume the resolved binding and existing authenticated connection | Host configuration and permission interaction |

A common JSON case catalog expresses public receipt inputs and whether a checkpoint may occur. It does not define private call order, require every host to expose the same events, or replace native transport with a Client call. Isolated plugins continue loading without the PowerContext package. Codex retains its existing Pydantic runtime requirements; WorkBuddy retains its existing typing compatibility dependency.

Native event IDs are used when available. Existing content-hash fallback can deduplicate identical prompt text within a session when no native event identity is supplied; this limit is explicit. The correction does not reassign existing Source identities or invent a cross-host retry protocol.

## Compatibility and recovery

The generated HTTP contract remains unchanged. Server responses already satisfying it retain behavior. Malformed, extra-field, wrong-type or unrelated-Source responses are rejected before checkpoints. No persistent configuration or user data migration is needed. Capture acknowledgement is Source acceptance, not generated Memory or completed synthesis. Cancellation, lost responses and failed validation leave the write outcome uncertain; existing explicit inspection is required before replay.

## Acceptance

`tests/fixtures/hooks/capture_receipts.json` is executed through actual copied adapter subprocesses and a loopback HTTP fixture for all three hosts. Cases cover accepted and repeated receipts, missing status/fields, rejected status, mismatched Source identity/type, boolean/zero positions, extra fields, malformed JSON, lost acknowledgement and capture opt-out. Assertions protect exit behavior, empty injection, receipt-gated checkpoints, stable retry identity and content-free native diagnostics. Claude also executes without site packages; existing dependency requirements for other hosts remain intact.

The existing adapter behavior suites retain provenance and authorization coverage. Actual Codex, Claude Code and WorkBuddy callback qualification remains separate from direct subprocess execution. Platform or latency claims require named native versions and measured evidence.

# Drawbacks

Three isolated adapters retain small copies of validation. Conformance detects behavior drift but does not eliminate source duplication. Strict wire validation rejects extra fields, matching the current API contract; incompatible Server changes require an explicit contract update.

# Rationale and alternatives

A shared Client engine would broaden isolated plugin deployment merely to validate a small wire boundary. A JSONL worker also adds protocol, process, cancellation and version ownership; no measured need exists for this correction. Duplicated narrow validation with one executable conformance catalog preserves current distribution and verifies the same rule. Resource distribution may later share this isolated helper without changing host lifecycle semantics.

# Prior art

[#1691](https://github.com/oceanbase/powercontext/pull/1691) provides closed, unmerged implementation material; it is not an established shared runtime contract. Its review demonstrates why descriptive Hook lists cannot become authorization and why target differences must remain explicit. [Plugin-visible diagnostics](../development/plugin-contract.md) remains authoritative for covered hosts.

# Unresolved questions

No additional cross-host event or protocol decision is required for receipt validation. A future shared executor must establish dependency, deadline and cancellation compatibility before migration.

# Future possibilities

Extract execution only after callers share failure semantics and deployable dependency requirements. Qualify any worker in one actual host before broader migration. Extend acknowledgement cases to additional adapters when their native contract and support evidence require the same invariant.
