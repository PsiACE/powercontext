---
title: CLI
description: PowerContext CLI commands and their operating boundaries.
---

# CLI

The CLI configures integrations, diagnoses installations, controls a local Server, and operates on content through a
configured Server.

## Setup and diagnostics

```text
powercontext setup codex
powercontext doctor
powercontext server run
powercontext ready
powercontext capabilities
```

The optional `server` role adds `powercontext server run`. It does not create a second content profile inside the
CLI. See [Install and run](../how-to/install-and-run.md) for installation and process management.

## Candidate review

```text
powercontext candidate list --scope-id project:example
powercontext candidate list --scope-id project:example --family skill
powercontext candidate show --scope-id project:example CANDIDATE_ID
powercontext candidate approve --scope-id project:example --expected-version 1 CANDIDATE_ID
powercontext candidate reject --scope-id project:example --expected-version 1 --reason unsupported CANDIDATE_ID
powercontext candidate revise experience --scope-id project:example --expected-version 1 \
  --situation SITUATION --action ACTION --outcome OUTCOME --lesson LESSON CANDIDATE_ID
powercontext candidate revise skill --scope-id project:example --expected-version 1 \
  --name NAME --description DESCRIPTION --instructions-file instructions.md --validation CHECK CANDIDATE_ID
```

Review writes require the current `expected_version` so a stale reviewer cannot replace newer work.

## Experience and Skill commands

```text
powercontext experience generate --scope-id project:example --source-ref content/SOURCE_ID
powercontext skill generate --scope-id project:example --origin experience \
  --artifact-ref experience/EXPERIENCE_ID@REVISION
powercontext skill show --scope-id project:example --revision 1 SKILL_ID
powercontext skill export --target codex --scope-id project:example --revision 1 \
  --destination .agents/skills/example-skill SKILL_ID
```

Generation and revision commands accept repeatable `--source-ref TYPE/ID` and
`--artifact-ref FAMILY/ID@REVISION` options instead of serialized request files. `--target FAMILY/ID@REVISION`
automatically includes the target in Artifact evidence. Managed Skill revision accepts exactly one of inline
`--instructions` or `--instructions-file`; `--validation` can be repeated.

See [Artifact lifecycle](../explanation/artifact-lifecycle.md) for the generation, approval, and export boundaries.

## External Skills

```text
powercontext external-skill scan --scope-id project:example
powercontext external-skill list --scope-id project:example
powercontext external-skill resolve --scope-id project:example --fingerprint SHA256 EXTERNAL_SKILL_ID
powercontext external-skill import --scope-id project:example --fingerprint SHA256 \
  --mode import EXTERNAL_SKILL_ID
```

Scanning and resolving do not install a package. Import creates a managed Skill Candidate and leaves the external
registration unchanged.
