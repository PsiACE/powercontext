---
title: Independent usability workflows
description: Six bounded implementations of RFC 1733, their public contracts, evidence, and delivery boundaries.
---

# Independent usability workflows

[RFC 1733](../rfcs/1733-usability-and-agent-workflows.md) assigns installation, local operations, Client connections, Server configuration, and task guidance to distinct owners. The six proposals below implement independently useful contracts. They do not require a daemon, a new configuration language, or a shared host execution engine.

| Topic and RFC | Delivered behavior | Feature branch |
| --- | --- | --- |
| [Hook governance](../rfcs/0000-hook-governance.md) | Codex, Claude Code and WorkBuddy validate exact capture receipts before checkpoints; one executable case catalog protects the invariant | [fix/hook-capture-receipts](https://github.com/PsiACE/powercontext/tree/fix/hook-capture-receipts) |
| [Plugin resources](../rfcs/0000-plugin-resource-distribution.md) | Declarative file projections preserve native Skill entries and references; build/check detects drift and rejects unsafe paths | [feat/plugin-skill-projections](https://github.com/PsiACE/powercontext/tree/feat/plugin-skill-projections) |
| [Connections and projects](../rfcs/0000-client-connections-and-project-bindings.md) | Explain Client endpoint selection, save revision-checked connections, and inspect/bind/unbind existing Codex checkout Scopes | [feat/client-connections](https://github.com/PsiACE/powercontext/tree/feat/client-connections) |
| [Agent configuration](../rfcs/0000-agent-operable-configuration.md) | Discover typed fields and perform private, revision-checked `.env` plan/apply operations | [feat/agent-operable-configuration](https://github.com/PsiACE/powercontext/tree/feat/agent-operable-configuration) |
| [Local operations](../rfcs/0000-local-installation-operations.md) | A stdlib-only `powercontext-ops` entry inspects and controls owned native services and repairs an explicit named uv tool | [feat/independent-local-operations](https://github.com/PsiACE/powercontext/tree/feat/independent-local-operations) |
| [Task Skills](../rfcs/0000-task-oriented-skills.md) | Installation/maintenance and product task guidance select available tools, verify writes, and report distinct artifact states | [feat/task-oriented-skills](https://github.com/PsiACE/powercontext/tree/feat/task-oriented-skills) |

The [combined branch](https://github.com/PsiACE/powercontext/tree/feat/usability-workflows) integrates these contracts on upstream `f28f8edf`. The Task Skills branch uses resource generation to publish shared guidance; the other feature branches can be reviewed independently. Connections and configuration share a small identical local file revision/lock primitive. Local operations relocates the existing stdlib environment-file parser and native service owners; integration updates their callers without creating a second ownership implementation.

## Observe, plan, apply, verify

Installation scripts are delivered separately in [#1892](https://github.com/oceanbase/powercontext/pull/1892). Use the published installation guide's script when available; an unmerged branch does not establish a deployed download URL. These new commands require a build containing the corresponding feature. Discover actual command help before using them with an older release.

Inspect Client policy without contacting a Server:

```bash
powercontext connection inspect --host client
```

Use the returned revision when saving an explicit endpoint. A Client credential reference names an environment variable and never stores its value. Inspection describes Client transport selection; separately configured native MCP activation remains unobserved.

```bash
powercontext connection configure --host client \
  --server-url https://server.example \
  --expected-revision REVISION --api-token-env POWERCONTEXT_TOKEN
```

Project operations use an existing Scope ID issued by that same Server. Exact Git worktrees remain separate identities. Inspecting an unbound checkout does not substitute a default Scope.

```bash
powercontext project inspect --project /path/to/checkout --server-url https://server.example
powercontext project bind --project /path/to/checkout --server-url https://server.example --scope-id SCOPE_ID
```

For configuration, select an explicit file and target. Server fields require Server extras; Client fields work with `[cli]` alone.

```bash
powercontext config schema --target client --json
powercontext config show --target client --env-file /path/to/client.env --json
powercontext config plan --request-file change.json --json
powercontext config apply --request-file change.json --json
```

A change document names its resource, observed byte revision, and typed changes. Use `missing` only for an absent file:

```json
{
  "schema_version": 1,
  "target": "client",
  "resource": "/path/to/client.env",
  "base_revision": "missing",
  "set": {"timeout": 12.0},
  "unset": []
}
```

Unknown fields fail before writes. Unknown existing assignments remain private and preserved. Secret changes require controlled environment/file input; unlike a connection credential reference, applying a configuration secret stores its resolved value in the private `.env` file. A successful save reports restart or next-invocation requirements; it does not claim a running process has adopted the value.

Independent maintenance remains available when Runtime dependencies fail, as long as the installed Ops modules and Python interpreter remain present:

```bash
powercontext-ops status
powercontext-ops doctor
powercontext-ops server logs
```

Lifecycle commands verify native ownership. `logs` reports the exact log location/selector. Repair requires `--target uv-tool --profile local|client --version EXACT`, uses explicit or current uv source configuration, and keeps services stopped. It does not repair an arbitrary manual environment or reconstruct an unknown original package source. A missing whole environment needs bootstrap recovery.

## Authoring and evidence

`integrations/distribution/skills/targets.json` selects common Skill sources and explicit native overrides. Run `make plugin-skills` after authoring those sources and `make plugin-skills-check` to verify outputs. The generator does not edit user host installations, MCP credentials, Hook registrations, or exposed tools.

Each RFC records the chosen contract, alternatives, primary sources and acceptance boundary. Reproducible experiments live under `experiments/usability/`; pinned baseline instructions distinguish historical ablations from current implementation checks. Magpie and Lody inform native discovery and ownership boundaries; Cida, Jiandao and Agenvo inform typed field ownership, explicit change documents and saved-versus-active state. Source inspection is not execution of those applications.

Evidence includes actual adapter subprocesses against loopback HTTP fixtures, concurrent CLI processes, SQLite/HTTP Scope workflows, built-wheel execution without Runtime dependencies, and disposable Linux systemd lifecycle execution. Synthetic host events do not qualify interactive Agent callbacks. The Linux native lifecycle uses a fixture process; macOS/Windows maintenance and live model/host workflows need matching qualification. The three-platform installation CI for #1892 covers that installation change only.

Full RFC 1733 onboarding, named connection catalogs, native secret stores, live user-plugin synchronization, additional host binding schemes, and a local daemon remain separate contracts. They are not prerequisites for these delivered operations.
