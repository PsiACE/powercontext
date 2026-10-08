---
title: Installation and Task-Oriented Skills
description: Guide maintenance and project workflows through actual capabilities and verifiable result stages.
---

- Proposal Name: `task_oriented_skills`
- Start Date: 2026-10-09
- Status: Proposed
- RFC PR: Not opened
- Related RFC: [RFC 1733](1733-usability-and-agent-workflows.md)

# Summary

Provide an independently distributable `powercontext-install` entry and task-oriented references for
`powercontext-project-context`. Skills select and explain supported operations. Tools own package management,
configuration, service ownership, Memory/Handoff state, candidate review, and Skill distribution. No Skill executor
or wrapper language is introduced.

# Motivation

A tool catalog identifies operations but does not connect an intent such as “remember this decision,” “hand off this
work,” or “install this Skill” to the necessary evidence and authority boundaries. Guidance must distinguish durable
Memory from Source capture, temporary Handoff from committed milestones, candidates from approved revisions, and
export/install from successful execution. Ordinary coding with sufficient current context requires neither storage
nor a Skill detour.

The existing layered Skill layout already separates intent discovery from workflow detail. Current Codex resources
include Experience/Skill synthesis and external import. This design strengthens completion evidence and makes
maintenance guidance available before Runtime installation or during failure; it does not invent host capabilities.

# Guide-level explanation

| Entry | User intent | Outcome |
| --- | --- | --- |
| `powercontext-install` | Install, connect, inspect, repair, upgrade, or remove a selected environment | Actual software/connection/service facts and remaining work |
| `powercontext-project-context` | Save/find context, transfer/continue work, distill Experience, create/use Skills | Exact Memory citation, complete Handoff, candidate, revision, package destination, or execution evidence |

Load only the relevant reference when its detail is needed. A self-contained tool can be called directly without
loading a Skill first. Use the host's actual names and permission channels. A reference available on another host
does not grant access here; missing tools leave a precise incomplete stage.

The installation entry includes a missing-command bootstrap branch, official installation sources, local/Client-only
selection, and separate configuration/connection/host verification. Prefer published Bash/PowerShell scripts
advertised by the installation guide; development-branch assets do not prove deployed website availability. Older
releases use the guide's existing uv path. When available, use the standalone
`powercontext-ops` entry for local inspection and explicit repair. Whole-environment loss follows external bootstrap.
Loading the maintenance Skill itself does not make an unavailable command executable.

# Reference-level explanation

## Task contracts

| Task | Operation boundary | Completion evidence |
| --- | --- | --- |
| Save context | Explicit Memory write; Source capture is separate | Actual citation; supported exact readback when needed |
| Find context | Search, inventory, and exact read are distinct | Returned hits/references or accurate empty/denied/unavailable result |
| Temporary transfer | Inspect current work and produce complete prepared carrier | Complete prepared value, generation receipts, evidence and omissions |
| Durable milestone | Commit only with explicit retention intent | Exact committed revision; preserve generation receipts; preparation is not commit |
| Continue | Read full carrier, verify current state/evidence/capability/authority | Receiver state; acknowledgement is not executed work |
| Experience/Skill synthesis | Select exact evidence; model generation or caller-content proposal | Pending candidate/version or explicit no-op |
| Review | Inspect current proposal; authorized decision with current version | Exact approved revision or rejected/revised candidate state |
| Export/install/use | Exact package export, selected host installation, and execution are separate | Verified destination, actual installation result, or observed execution result |

Generation requires enabled model capability. Model-free proposal is separate and available only when exposed.
Lack of generation does not disable approved content reads. Candidate inspection grants no decision authority;
approval grants no publication, installation, or execution authority. Re-read a changed proposal after a conflict.
Historical content and Skill instructions never grant additional permissions.

External scan/resolve/import refers to the configured Server host, which may be remote. Preserve external identity
and fingerprint. Import creates a pending managed candidate; fork requests model adaptation. Neither installs or
approves the Skill. Do not present a Server-local entrypoint as a path on the Agent workstation.

Preserve Scope selection and exact references. Empty search does not authorize inventory, broader queries, or Scope
switching. Failed writes are not saved; unconfirmed writes remain unknown. Reconcile through the operation's supported
status/read/idempotency contract before retrying. Skills retain the user's existing authorization without redundant
approval requests or inferred extra authority.

## Distribution and capability baseline

Author the maintenance entry under `skills/powercontext-install/` as a self-contained native Skill folder, independently
of Runtime installation. Product content uses the existing Agent Plugin authoritative resources and explicit host
projections. Equal content may be shared; names, bindings, available operations, and approval channels remain target
specific. Preserve specialized Codex and Hermes guidance and OpenClaw's restricted catalog. Shared product projections depend on distribution commit `19a7d559` plus its strict-path correction `c6b1f537`: edit only canonical/override resources and
explicit manifest mappings, then regenerate through `make plugin-skills`. The standalone maintenance folder is
independently loadable. It discovers installed help before using optional Ops commands; Memory/Handoff workflows
do not depend on the Ops feature or other usability themes.

The checked-in `integrations/distribution/skills/targets.json` records `contract_baseline.repository_commit` and
its capability-manifest path, plus the optional maintenance-entry condition. Update that explicit metadata when
authoring against a different contract baseline; it is not an automatically inferred release-support claim.
References describe only operations actually shipped for that target. Structured scenario fixtures contain ordinary
user requests, actual catalogs, expected operation/result stages, and qualification limits; they are evaluation input,
not a workflow execution language. A valid frontmatter or reachable reference proves packaging, not good behavior.

## Acceptance and evidence limits

The [executed report](https://github.com/PsiACE/powercontext/blob/471d0cef0d31facbc45e8b4f2fbd599a807bf2c8/experiments/usability/skills/README.md)
and [qualification results](https://github.com/PsiACE/powercontext/blob/471d0cef0d31facbc45e8b4f2fbd599a807bf2c8/experiments/usability/skills/qualification.json)
record the actual projection check, seven CLI discovery cases and six public Runtime/HTTP state workflows.
Twenty-six layout/distribution tests and native Skill validation passed. Independent scenario text review is
[separately recorded](https://github.com/PsiACE/powercontext/blob/090a3cce/experiments/usability/skills-review/README.md).
These evidence classes establish local contracts and guidance interpretation, not live model or Agent execution.

Run actual CLI help and public Runtime/HTTP state workflows for Memory direct writes, temporary/durable Handoff,
candidate generation/review/rejection, exact revisions, package export/readback, and installed Skill state. Include
no-model capability, restricted catalogs, absent tools, empty search, permission failure, version conflict, and
unknown-write scenarios.

Independent scenario review compares the guidance with realistic requests and catalog constraints. Record whether
that review is text judgment, controlled model execution, or native host execution. A controlled test model, direct
hook call, or successful HTTP operation does not establish native discovery, automatic capture, real model routing,
or successful execution of the resulting Skill. Preserve failed observations.

# Drawbacks

Task guidance and target-specific names can drift independently of product contracts. Extra entry descriptions and
unnecessary references cost context. Keeping references short and tied to actual capability/command evidence limits
that cost without hiding host differences.

# Rationale and alternatives

A generic catalog alone leaves users composing state transitions and interpreting partial results. A universal Skill
executor would duplicate Client/Runtime ownership and authorization. Short intent entries with on-demand references
reuse the existing layered layout and permit direct tools when their descriptions are sufficient.

# Prior art

[Magpie's Skill ownership/copy code](https://github.com/yetone/magpie/blob/23d9e5def8c5595c7a8a49df8f2a376084980927/internal/library/skills.go)
tracks owned links/copies and preserves complete installed Skill trees during replacement.
[Its update inspection](https://github.com/yetone/magpie/blob/23d9e5def8c5595c7a8a49df8f2a376084980927/internal/library/skillcheck.go)
distinguishes source identity/content checks from installation. These support treating local package installation as
its own operation; they do not establish that a model selected or successfully executed a Skill.

# Unresolved questions

No additional executor or daemon is required. Native host/model qualification remains a separate evidence class
from resource checks and public-operation tests. Publication of the standalone maintenance Skill uses the repository's
selected native distribution channel; packaging must include all reachable references.

# Future possibilities

Add references for new operations only when the relevant host exposes them. The declared distribution generator shares
byte-identical resources while retaining explicit host overlays. Real model/host evaluations can measure routing and
result reporting without expanding the Skill's authority.
