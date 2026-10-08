# Agent-operable configuration evidence

Baseline: [`f28f8edf`](https://github.com/PsiACE/powercontext/commit/f28f8edf). The executable reproduction checkout is [`cce34000`](https://github.com/PsiACE/powercontext/commit/cce34000), which contains both harnesses and unchanged baseline runtime source. Run in an isolated worktree at that exact commit; running this harness against a feature checkout measures that checkout rather than the baseline:

```sh
git worktree add --detach "$HOME/.cache/powercontext-usability/baseline" cce34000
cd "$HOME/.cache/powercontext-usability/baseline"
```

Then run:

```sh
uv run --locked python experiments/usability/configuration/run.py \
  --scratch "$HOME/.cache/powercontext-usability/config" \
  --output experiments/usability/configuration/results.json
```

The actual public configuration CLI/parser/writer was executed on Linux. This establishes local static/file behavior,
not remote model availability, running-process activation, or native host acceptance.

| Fixed requirement | Removed mechanism | Observation |
| --- | --- | --- |
| Two writers preserve unrelated concurrent updates | Revision comparison; retain existing atomic replacement | File remains parseable but second stale snapshot loses first writer's port change. Atomicity alone does not prevent lost updates. |
| Ordinary inspection contains no private values | Public field allowlist; retain name-based redaction | Synthetic `BUSINESS_CREDENTIAL` appears in current show. Unknown assignments must be preserved but excluded from structured safe inspection. |
| Reject misspelled change requests before effects | Change-field schema; retain generic Settings validation | `HTTP_PORRT` is ignored and validation succeeds. Runtime environment extensibility is not a strict public change-document schema. |
| Imported .env cannot execute code | Shell evaluation versus existing parser | Existing parser rejects expansion without execution. Retain it rather than introducing another configuration language. |

Primary source inspected, not executed:

- [Cida field owner](https://github.com/Xuanwo/cida/blob/a48745e79632f93d6763605d5718ab4b7cea1122/Sources/Cida/ConfigurationFields.swift)
  shares schema/parsing/display field definitions; [checks](https://github.com/Xuanwo/cida/blob/a48745e79632f93d6763605d5718ab4b7cea1122/Sources/Cida/ModelServiceCheck.swift)
  associate observations with configuration fingerprints. We adopt field discovery and precise static evidence without
  claiming desktop activation semantics for a separately running Server.
- [Jiandao intent document](https://github.com/Xuanwo/jiandao/blob/76a3fbc0501ac9c6f5cb5aa88a16d0923bff97b3/src/utils/setup-document.ts)
  rejects unknown fields and separates public intent from storage. It treats masked keys as preservation signals;
  PowerContext should preserve omitted fields and never accept masked credentials as replacement values.
- [Agenvo installation](https://github.com/Xuanwo/agenvo/blob/235978f9fd9cf70fd75b03292619262d5e86a6e6/docs/installation.md)
  separates deployment, connector registration, authorization and reachability.
- [Pydantic settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) defines field validation and source
  precedence. A local configuration file must be validated as that file, with inherited variables reported separately.

Smallest production contract: add `config schema`, structured `show/validate`, strict typed `plan/apply` over existing
.env resources, preserve omitted assignments, redact non-public values, reject stale byte revisions under a cooperating
writer lock, and report saved versus restart-required. Explicit resource selection is deterministic; no speculative TOML
engine, model network probes, daemon, or global transactional onboarding is needed.
