---
title: Plugin architecture and distribution
description: Shared execution, templates, host adapters, and setup.
---

# Plugin architecture and distribution

PowerContext integrations have three responsibilities: the installed client executes domain operations, native
adapters handle host events and permissions, and templates supply repeatable package resources. The server owns
Scope and persisted artifacts. A plugin does not install a second client or carry its own domain HTTP executor.

## Execution

`powercontext.client.integration` accepts an operation ID, arguments, connection, absolute deadline, and optional
Scope binding. The client validates requests and responses against its operation contract. Python adapters call
it directly; TypeScript and Hermes use the serial JSON Lines worker exposed by `powercontext-hook`.

Automatic prompt hooks resolve Scope, prepare context, and capture the prompt only with consent. A checkpoint starts
only after Source acknowledgement. Cursor progress, call limits, and the shared deadline bound processing. Unknown
writes remain unknown and cannot authorize automatic replay. Source acceptance does not prove that Memory exists.

Adapters own native event timing, tool visibility, user confirmation, Scope identity, and output channels. Keep these
behaviors explicit: Pi checkpoints at lifecycle boundaries; OpenClaw limits tools to eligible sessions; MiniMax reads
its named MCP endpoint and respects private overrides. DSH alone uses a bounded GET for OpenAPI discovery.

Failures use typed results, never exception-text matching. Preserve rejection, conflict, invalid input, invalid response,
and unknown write outcomes. Empty context is a valid result. Automatic recall fails open; invalid tool-input binding
fails closed. Diagnostics omit credentials, prompts, response bodies, and stack traces.

`powercontext_integrations.host.HostAdapter` is the common boundary for setup, resource preparation, and doctor. Single-host
setup and `setup select` call the same `install()` method: check the installed client, resolve connection policy, run the
native installer, verify installation, then save connection settings. Failed verification does not persist the new
connection. Each host supplies its native module, accepted options, and whether its installer already verifies the result.
Native installers retain their rollback rules and call `prepare()` at the appropriate staging or cache boundary.

Setup, doctor, configuration selection, and distribution read the same Target catalog from the selected repository source. Every target has
`powercontext setup <target>` and `powercontext doctor <target>`, including Python packages and portable plugins.
Python owns installation and diagnostics. Native callbacks supply installation/discovery and effective configuration;
TypeScript only registers commands and forwards configuration to `powercontext-hook --doctor`.

Common diagnostics check client prerequisites and transport policy. Optional `doctor <target> --server` and native
`/pc doctor` use the same read-only liveness, readiness, and context-schema checks. Dependency failures remain visible
on HTTP 503; a failed liveness probe skips subsequent checks. Diagnostic output excludes private response text.
Installation checks and Server probes have distinct inputs: a running host supplies its actual connection, while the
CLI reads discoverable configuration. An unreadable native configuration fails instead of guessing an endpoint.
Setup failures use `SetupError` with shared constructors for repeated command and input failures.

```text
Repository rules (source / ref)
     Target catalog + templates
          |
     HostAdapter (Python)
          +-- setup  -> prerequisites -> native install -> verify -> save
          +-- doctor -> prerequisites + native discovery + transport
          |                  +-- optional Server checks
          +-- build  -> native adapter + generated MCP / Skills / bridge

Host command -> connection -> Client Server checks -> native display
```

## Agent Plugin baseline

- `integrations/agent-plugin/powercontext/` is the editable baseline: standard Skills, workflow references, and MCP configuration.
  `integrations/agent-plugin/operations.json` selects the minimum shared toolkit; request schemas and descriptions come
  from the client contract. Other hosts project this baseline rather than define different workflows.
- `integrations/distribution/powercontext_integrations/assets/` holds native format templates and bindings. `resources.json`
  supplies native MCP fields and registration formats; `tool-bindings.json` preserves existing tool names. Scope resolution,
  direct current-work Handoff, Memory inventory, and candidate inspection follow the same methodology.
- `resources.py` serves setup and distribution. DSH registers baseline references as runtime Skills; other Skill hosts
  receive files. Native MCP wrappers, endpoint paths, schema metadata, and credential fields are format adaptations.
  WorkBuddy's settings merge uses the same projection. Host code retains interactive approval and tool visibility.
- `integrations/distribution/powercontext_integrations/assets/targets/` declares source layout and native events, handlers, operations, effects, and
  failure behavior. Operation IDs come from the client contract. There is no separate capability manifest or source probe.
- `scripts/build_agent_distributions.py` overlays rendered resources on native adapters and records file hashes.
  Outside the baseline, Skills, MCP files, tool schemas, guidance, and bridges are generated outputs. Native packages
  include `tools.generated.json`; SDK adapters read it without embedding duplicate schemas in JavaScript bundles.

```text
Agent Plugin baseline + API contract
              |
       Shared generation
              |
       Native host adapter
       +-- tool / Skill / MCP format
       +-- hooks -> Core Hook
       `-- approval + output
```

`integrations/distribution/` owns host rules and templates independently of the client release. The client supplies
`powercontext-hook` and shared Server diagnostics. Setup projects resources before native installation and refreshes
installed caches where required. Prompt hooks supply the resolved Scope even when retrieval is empty, so MCP callers
reuse the same binding without a host-specific resolver command.
Existing MCP configuration survives regeneration; an explicit endpoint change updates only its URL. Credentials and
user settings stay with the host's configuration flow. Restart the host after setup.

## Develop and distribute

Install `powercontext[cli]` and expose `uvx`, `npx`, and `powercontext-hook` on PATH. No additional management package
is installed. Setup loads Python rules directly from `integrations/distribution/` in the selected `--source/--ref`.
Remote sources use a shared Git checkout; local sources are read in place. An explicit remote setup refreshes only an
unmodified managed checkout. Successful setup saves its source alongside the connection and installation location.
Doctor reuses that source without fetching; `--source/--ref` can explicitly select another source.

The client retains the source loader, Core Hook, and shared Server diagnostics. Profiles, templates, native installation,
and discovery remain repository code. `powercontext-hook --doctor` and the default CLI doctor need no integration source.
For development, use the checkout directly:

```bash
make agent-resources
make agent-distributions
uv run python scripts/build_agent_distributions.py --list
powercontext setup pi --source /path/to/powercontext
powercontext setup minimax --source /path/to/powercontext
powercontext setup langchain --source /path/to/powercontext --python /app/.venv/bin/python
powercontext setup agent-plugin --source /path/to/powercontext --destination /app/plugins/powercontext
powercontext doctor langchain --server
```

`agent-resources` materializes template outputs for local development and native source loading. `agent-distributions`
produces standalone packages under `build/agent-distributions/<target>/`; packages contain their rendered resources
and adapters. Build before using a raw checkout with a host's plugin command. The builder preserves foreign or modified
output files. To change generated content, edit its template and rebuild.

Python packages install through `uvx` into the explicit application interpreter, the saved interpreter, or the current
project's `.venv`, in that order. Setup never selects the CLI's tool environment implicitly. MiniMax uses its native
plugin directory and verifies discovery with `mcode plugin list`; portable plugins require a destination and registration
with the loading agent. Setup records installation locations for later doctor calls. A multi-target selection with more
than one directory target places each under `<destination>/<target>`.

The catalog command reports profiles used by the builder, including language and hook operations. It describes declared
native bindings, not runtime service availability or every dynamically permitted tool. Use `powercontext doctor integrations`
for installed host status and each host's actual tool catalog for its current permissions.

To add a host, implement its native event, approval, and output adapter, add a Target profile, and bind its native formats.
Use the baseline toolkit and workflows. Adapt host response envelopes at the boundary; do not fork the methodology.

## Method and validation

Start from observable constraints: who selects Scope, which event owns a write, what confirms completion, and which
boundary stops execution. Keep event, operation, effect, failure policy, and transport independent. Model-generated
content uses the same operation boundary; text cannot grant permissions or replace a confirmed result.

Use ablation to justify a boundary: remove validation, deadline, binding, or unknown-outcome handling in an isolated
fixture and confirm the relevant failure becomes observable. Preserve that behavior in the existing Core or adapter
test. Do not maintain a second evaluation pipeline, guessed support table, or tests for generated implementation details.

Verify the distribution boundary with only the client installed: add a Target to a separate source checkout and select
it with setup. Doctor must reuse that source while the client version and Core remain unchanged.

Run `make check` and the affected Python or native package tests. Distribution tests cover resource completeness,
reproducibility, and preservation of user configuration. Host tests cover event mapping, permissions, and outputs;
shared Core tests cover validation, deadlines, and uncertain writes. Live model quality is a separate measurement.
