# Connection and project identity evidence

Baseline: [`f28f8edf`](https://github.com/PsiACE/powercontext/commit/f28f8edf). Run from that checkout:

```sh
uv run --locked python experiments/usability/connections/run.py \
  --scratch "$HOME/.cache/powercontext-usability/config" \
  --output experiments/usability/connections/results.json
```

Linux, real Git linked worktrees, and the actual PowerContext FastAPI/SQLite application were executed. ASGI test
transport is in-process; this is not a remote-host, real network, or native MCP acceptance test.

| Fixed requirement | Mechanism removed or compared | Observation |
| --- | --- | --- |
| Consent approved only for old endpoint | Endpoint equality check versus saved boolean alone | Existing resolver denies new endpoint; a bare saved boolean would allow it. `/mcp` shares API endpoint consent. |
| Persist explicit endpoint with conflicting runtime environment | Setup conflict validation versus invocation override alone | Invocation selects explicit endpoint; persistent setup rejects conflict before mutation. |
| Two linked worktrees must remain independent | Checkout root versus shared Git repository identity | Existing Codex keys differ; no new project identity database is needed. |
| Switch to a different Server without reusing old Scope | Server-owned binding versus a carried local Scope ID | New Server returns 404 for both binding lookup with no default and old Scope bind. |

No tested requirement needs named local connections, a duplicated Scope catalog, or a new configuration engine.
The useful extension is inspectable source selection and explicit existing-binding operations. A Client credential
reference should initially name an environment variable, stay non-secret in the host document, and resolve only at
request construction. Native host credential migration is not demonstrated and remains outside this slice.

Primary source inspected, not executed:

- [Magpie settings](https://github.com/yetone/magpie/blob/23d9e5def8c5595c7a8a49df8f2a376084980927/internal/settings/settings.go)
  and [concurrent save tests](https://github.com/yetone/magpie/blob/23d9e5def8c5595c7a8a49df8f2a376084980927/internal/settings/save_concurrent_test.go)
  coordinate atomic complete snapshots. Its secret ownership and cross-platform file semantics do not automatically
  transfer to PowerContext. Current main advanced from the earlier `023f5aaa` reference; this report pins inspected code.
- [Lody installation profile](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/packages/shared/src/node/installation-profile.ts)
  names namespace and durable root explicitly. Its public release contract does not establish commercial host behavior.
- [Agenvo connector configuration](https://github.com/Xuanwo/agenvo/blob/235978f9fd9cf70fd75b03292619262d5e86a6e6/packages/connector/src/config.ts)
  validates connector choices. [Native operations change](https://github.com/Xuanwo/agenvo/commit/235978f9fd9cf70fd75b03292619262d5e86a6e6)
  retains native identities/lifecycle boundaries and does not replay uncertain writes. These are source claims, not our execution.
- [HTTP semantics](https://www.rfc-editor.org/rfc/rfc9110.html) distinguishes resource identity and conditional changes;
  redirect permission must not be inferred from an endpoint's stored consent.

Smallest production contract: v1 per-host connection inspect/configure with source/provenance, revision conflicts,
explicit Client token environment reference, and `project inspect/bind/unbind` using Codex's existing exact checkout key
and existing Server Scope API. Do not create Scopes by display name or make old local Scope IDs follow endpoint changes.
