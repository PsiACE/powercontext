---
title: HTTP and MCP
description: PowerContext Server endpoints and transport availability.
---

# HTTP and MCP

The Server provides:

| Path | Purpose |
| --- | --- |
| `/openapi.json` | OpenAPI document for the complete HTTP contract |
| `/health/ready` | Readiness check |
| `/v1/capabilities` | Enabled Server capabilities |
| `/mcp` | Streamable HTTP MCP endpoint |

HTTP is the complete application contract. MCP is a selected agent-facing projection of Memory and Candidate Review
operations. The five Candidate Review operations use the same validation, `expected_version` concurrency checks, and
approval transaction over HTTP and MCP.

Experience and Skill generation, exact reads, external Registry operations, and low-level proposal operations are
HTTP-only.

`POST /v1/context/prepare` and the matching Python Client method expose the final, temporary `PreparedContext` over
HTTP. The Runtime recalls active Memory and approved Experience heads, and it owns their shared selection and total
output budget. This operation is not projected as an MCP tool. The public schema is
`powercontext.prepared-context.v1`; Experience items contain an exact Artifact reference in the prepared content.
