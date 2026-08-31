---
title: Configure OpenCode
description: Install the PowerContext OpenCode plugin and control its local behavior.
---

# Configure OpenCode

## Installation status

OpenCode is not yet exposed by the distribution installer. Its checked-in plugin remains available for development
and packaging work, but there is no supported end-user installation command until its host artifact is migrated. After
a development installation, start the Server and open a new OpenCode session:

```bash
powercontext server run
opencode
```

## Understand the behavior

For each normal user turn, the plugin asks `POST /v1/context/prepare` for one bounded context value and independently
captures eligible prompt text through `POST /v1/sources/content`. Prepared context is labelled as untrusted history
and inserted transiently before model dispatch; it is not stored in the OpenCode transcript.

Named `pc_*` tools expose curated Memory, Handoff, Experience, Skill, and read-only Candidate operations. OpenCode
asks for confirmation before a durable mutation. Candidate approval and rejection remain explicit human CLI or
Dashboard actions.

## Configure the connection

Set variables before starting OpenCode:

```bash
export POWERCONTEXT_OPENCODE_BASE_URL=http://127.0.0.1:8000
export POWERCONTEXT_OPENCODE_SCOPE_ID=project:example
export POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS=true
opencode
```

For a Server using optional bearer authentication, set the complete header in
`POWERCONTEXT_OPENCODE_AUTHORIZATION`. Never put credentials in the URL. Plain HTTP is accepted only for loopback
hosts. Set `POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS=false` when prompts must not be persisted as Source evidence.

## Verify the installation

```bash
powercontext doctor
powercontext doctor opencode
```

The integration-specific doctor checks the OpenCode version, resolved plugin configuration, and the owned Skill.
An unavailable Server does not block OpenCode and is checked separately by the default doctor command.
