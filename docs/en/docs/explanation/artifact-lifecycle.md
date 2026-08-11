---
title: Artifact lifecycle
description: Understand how Experience and Skill Candidates become approved Artifacts.
---

# Artifact lifecycle

PowerContext separates model-assisted generation, human review, approved Artifact revisions, recall, and export.
Generating a Candidate does not approve, install, recall, or execute it.

## Experience generation

After Review approval, deterministic `searchable_text` is stored on the existing generic Artifact head and enters the
backend's rebuildable FTS index. The approved Experience can then appear in `PreparedContext` for the same scope.
Pending and rejected Candidates, all managed Skills, and historical Experience revisions remain excluded.

An integration can capture a completed task as a Content Source with metadata `"kind": "task-outcome"`. When the
Experience schedule is configured, APScheduler scans bounded Source windows and asks the configured schema-bound
pipeline for situation, action, outcome, and lesson proposals. Each proposal cites exact Sources and enters the
Review Inbox as a pending Experience Candidate.

Experience incubation has its own persisted Source cursor, independent from Memory extraction. Candidate writes and
cursor advancement commit together. If generation or writing fails, the window remains available for retry. Ordinary
prompt Sources are not Task Outcomes and this job ignores them.

Scheduling stops at the review boundary. It does not approve an Experience, include pending content in
`PreparedContext`, derive a managed Skill, export a Skill for Codex, or execute instructions. Skill authoring and
export are explicit steps after the supporting Experience is approved.

## Managed Skill approval and export

A configured generator can produce complete managed Skill content through `generate_skill`. A person or integration
that already has complete typed content can use `propose_skill`. The proposal contains a name, discovery description,
instructions, validation checks, and exact Source or Artifact lineage. It remains a Candidate until a reviewer
approves the exact Candidate version.

Approval creates an immutable Skill Revision. It does not install the Skill or grant execution authority. To make an
approved Revision available to Codex, export it explicitly into a new repository or user Skill directory with
`skill export --target codex`. The command writes `SKILL.md` and `powercontext.json`; the manifest records the exact
Artifact reference and rendered-content hash. It refuses to replace an existing destination, so an update requires a
new, intentional export.

Codex can discover a repository-local export under `.agents/skills/<name>/SKILL.md`. The Artifact Revision remains
the content authority. The directory is a host-local projection that can be rebuilt from the same Revision.

## External Skills

External Skills remain authoritative in their original local packages. With explicitly configured Codex roots, the
Server can scan a scope-local, rebuildable Registry and report the name, description, provider, Agent kind, host,
installation scope, locator, and whole-package fingerprint. Exact resolution succeeds only when the same package is
still readable on the configured host and its fingerprint still matches. It does not install a package or fall back
to a different version.

Discovery does not enter Review. An explicit `import_external_skill` request with the exact identity and fingerprint
captures a bounded `SKILL.md` snapshot as Source evidence and asks the configured model for a new managed Skill
Candidate. `mode=import` and `mode=fork` record the caller's intent. Both create a new managed identity only after
Review approval, and neither changes the external registration. Package scripts and assets are not copied into the
managed Artifact.

## Authority and gates

| Surface | Content authority | Model gate | Review gate | Current availability |
| --- | --- | --- | --- | --- |
| External Agent-native Skill | Original package | No for scan/list/resolve; yes for import/fork | No for discovery; yes after import/fork | Host-local Registry and exact resolve |
| Experience | Exact approved Artifact Revision | Yes for generate/evolve; no for typed `propose` | Yes | Exact read and approved-head FTS recall in `PreparedContext` |
| Managed Skill | Exact approved Artifact Revision | Yes for generate/evolve/import/fork; no for typed `propose` | Yes | Exact read and explicit Codex projection |
| Codex projection | Its source managed Skill Revision | No | No additional review | Rebuildable host-local copy |
