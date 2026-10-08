---
title: Independently Available Local Operations
description: Inspect, control owned native services, and repair PowerContext packages when Runtime imports fail.
---

- Proposal Name: `local_installation_operations`
- Start Date: 2026-10-09
- Status: Proposed
- RFC PR: Not opened
- Related RFCs: [RFC 1733](1733-usability-and-agent-workflows.md), [RFC 1299](1299_local_server_availability_and_service_installation.md)

# Summary

Ship `powercontext-ops` in the existing release wheel as an independently imported, standard-library-only entry.
It inspects local installation and native service facts, controls only verified owned registrations, and performs
explicit exact-version package repair through uv. The existing `powercontext` launcher remains unchanged. No daemon,
new package, installation ledger, adoption database, or universal launcher dispatcher is required.

The maintenance entry works while the Server is stopped or Runtime imports/dependencies fail, provided its own
modules and Python interpreter remain present. Removing the wheel, whole environment, or interpreter requires the
shell bootstrap/package-manager recovery path. This bounded independence does not promise arbitrary self-repair.

# Motivation

RFC 1299 already defines exact native ownership, stop-before-remove, separate registration/manager/liveness facts,
and retained data. Current native models/adapters use the standard library, but their package parent and normal CLI
import Runtime dependencies. The Service controller also imports Server configuration before executing status.
The existing retry bootstrap already demonstrates a separate namespace surviving absent Runtime imports.

The [operations experiment](https://github.com/PsiACE/powercontext/blob/aa6aaa97baf9adc9c9ca3a1643ee719b853ea47c/experiments/usability/operations/README.md) executes stopped-Server and
broken-import failures, a same-environment sibling-entry ablation, and exact owned/foreign/missing definition inspection.
The installed-wheel qualification verifies the actual built wheel with Runtime dependencies absent. A separately
labeled controlled wheel verifies named uv repair and real Linux systemd ownership/start/stop/uninstall using a
fixture process. Existing behavior tests protect removal and partial failure. These results do not qualify a live
PowerContext Server on macOS or Windows; matching native acceptance remains required for those claims.

# Guide-level explanation

```text
powercontext-ops
+-- status
+-- doctor
+-- server
|   `-- start | stop | restart | logs | uninstall
`-- repair --target uv-tool --version EXACT --profile local|client [--index-url HTTPS]
```

`status` reads local software presence, definition, registration, manager ownership/state, and exact log location.
`doctor` adds a bounded Runtime CLI startup probe using the recorded service interpreter, or the invoking interpreter
when no service is registered. Output is JSON. Report facts independently, including unsupported manager and unknown
state. The probe does not start a Server; neither command changes configuration or data. Server liveness remains
explicitly unknown until checked through the existing Runtime health interface.
Local inspection remains available when the native manager or endpoint is unavailable.

Server lifecycle uses the existing one-per-user native identity and recorded launcher/endpoint. Before mutations,
verify both the on-disk artifact and any loaded manager object. Refuse foreign or unknown ownership. Never infer
ownership from a port, display name, or PID. A remote endpoint receives connection diagnostics, not local lifecycle.
Logs use the exact verified registration's native selector/path, never unrelated manager metadata or environments.

Start/restart requests report native manager completion separately from Server liveness/readiness. Stop does not
remove registration or data. Uninstall stops first and removes only the verified registration; package environments,
configuration, and business data remain. Package removal and data purging are outside this initial command set.

# Reference-level explanation

## Repair and distribution

Default repair addresses uv's named `powercontext` tool and delegates environment/package replacement to uv.
An exact release and Runtime profile are required; no `latest`, version substitution, or automatic source fallback.
An explicit default index follows uv's normal additional-index priority and authentication configuration. Package,
uv bootstrap, and Python download controls remain independent. Report the requested explicit index or current uv
configuration as source selection; no stable public uv API proves the original installation source. Reusing a
private source requires the operator to supply the same explicit index or configuration. Do not reconstruct private
receipts or claim preservation of unknown original provenance.

Manual-environment repair is outside this bounded command set. Do not infer ownership from the invoking
interpreter. `--target uv-tool` selects the named tool under the current uv configuration; it can differ from the
installation invoking Ops. Resolve the selected executable directory through `uv tool dir --bin`, verify its Ops
launcher and exact package version after installation, and report its Runtime launcher and startup result. Never
claim that a different invoking installation was repaired.

Repair never starts, stops, or restarts a service automatically. An installed owned service must be demonstrably inactive
before replacement; otherwise require an explicit stop. Keep stopped services stopped. Serialize native controls and
repair against the existing native-registration lock. After uv succeeds, report the actual installed version and
CLI startup result separately. A failed verification leaves the observed package/registration state visible; it is not a
successful repair or a promise of program rollback. No operation changes database format or claims data recovery.

The existing wheel supplies one small sibling namespace and console entry. Cleanly relocate shared stdlib native
owners and update existing imports; do not copy a second ownership implementation or import through the Runtime's
package parent. Preserve recorded Server launcher paths and RFC 1299 metadata compatibility. Legacy service and
normal CLI commands keep their existing behavior during this bounded delivery. Later unified command migration
requires an explicit caller/distribution plan, rather than permanent aliases introduced here.

## Acceptance

Build/install the actual release wheel and execute `powercontext-ops` with Server stopped, Runtime dependency imports
blocked, and Runtime startup failing. Verify local status, exact ownership refusal, logs, stop/start/restart, and
uninstall through matching native managers. Controlled manager/file fixtures establish local protocol handling only.
Exercise exact uv repair, source failure, unavailable release, active-service refusal, and retained state after repair failure; inspect actual resulting software and retained configuration/data. Qualify whole-environment
loss as a bootstrap recovery boundary, not standalone command success.

# Drawbacks

A separate executable is a small discoverability cost and an incremental step toward RFC 1733's independent local
operations.

# Rationale and alternatives

A managed-runtime record and universal dispatcher would add authorities not justified by the demonstrated
import failure. The same-wheel entry keeps release coordination simple while stating its recovery limits honestly.

# Prior art

[Lody's verified installation handoff](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/specs/daemon-upgrade-installation.md)
separates package installation, explicit executable/version verification, and worker readiness; failed handoff does
not imply package rollback. [Its daemon controls](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/apps/cli/src/commands/daemon.ts)
verify instance identity rather than stopping by stale PID alone. PowerContext reuses native managers instead of
adopting Lody's supervisor or authenticated daemon-control protocol.

# Unresolved questions

No new daemon or Runtime launcher ownership is required. Native support remains limited to platforms qualified by matching lifecycle tests; whole-environment loss uses bootstrap.

# Future possibilities

A later command migration can expose the same maintenance operations through one launcher, with a separate distribution/caller compatibility plan. Program removal and data recovery need their own explicit contracts.
