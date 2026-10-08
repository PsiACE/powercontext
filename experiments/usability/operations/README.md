# Independently available operations evidence

The smallest supported maintenance boundary is a standalone sibling entry in the existing wheel. It can inspect
native definitions without importing Runtime or replacing the normal launcher. Whole environment/interpreter removal
requires external bootstrap. Additional dispatcher ownership, environment records, and adoption state are unnecessary
for the demonstrated import failure.

## Reproduce

```bash
python experiments/usability/operations/run.py \
  --python /home/psiace/.lody/repos/local---0b794dd4dd93/worktrees/script-installation/.venv/bin/python \
  --scratch /home/psiace/.cache/powercontext-usability/operations \
  --output experiments/usability/operations/results.json
```

The selected Python must provide the existing Runtime/test dependencies. The harness pins source baseline
`f28f8edfcb2972ac322f4f12b1f224226a9a40ab` and rejects modified Runtime sources before execution. Native source used in
the relocation ablation is loaded through pinned `git show`. Disposable HOME/config/tool/cache/registration paths
isolate mutations. Existing current-user session address settings are preserved only for native manager availability.
No existing service is registered, stopped, removed, or repaired.

## Executed outcomes

[results.json](results.json) records exit codes, elapsed milliseconds, and normalized outputs. The experiments use
real Python processes, actual uv installation of a controlled wheel, and standard-library native source copied only
inside the experiment's disposable namespace. The wheel is an experiment fixture, not a PowerContext release wheel.

| Ablation | Observed result | Interpretation |
| --- | --- | --- |
| Existing public service status, Server stopped | Structured status exits nonzero with separate facts | Stopped Server does not itself prevent local inspection |
| Existing public status, injected Runtime import failure | Import failure prevents status execution | Current normal CLI does not establish import-failure independence |
| Existing CLI with third-party packages unavailable (`-S`) | Fails on Runtime dependency import | Healthy Python alone is insufficient for the existing CLI |
| Stdlib native owners in a sibling namespace | Owned definition is installed; foreign is invalid; absent is not installed | Clean relocation preserves useful ownership inspection without Runtime |
| Sibling standalone command in same Python environment, broken Runtime package | Executes and reports absent registration | Universal dispatcher and installation record are not prerequisites |
| Controlled single wheel containing both broken Runtime root and sibling Ops | Real uv installs the wheel; generated `powercontext-ops status` executes | Same-wheel packaging does not force the sibling command to import Runtime |
| Existing independent retry bootstrap with broken/missing Runtime | Retry budget executes, bounded failure removes retry token | Independent stdlib namespace is already a repository pattern |
| Existing ownership/removal/error behavior checks | Nine focused tests pass | Preserve existing stop-before-remove and foreign refusal semantics |

Every experiment checks that retained business evidence remains byte-identical. This verifies that these executed
inspection/bootstrap operations do not touch it, not a claim of upgrade/data recovery. The harness also records
`systemctl --user is-system-running`: actual manager availability is read-only evidence. Controlled definition files
and existing mock-manager tests do not qualify real native start/stop/uninstall or other platforms.

## Contract derived from the experiments

Ship a sibling stdlib `powercontext-ops` entry in the same wheel. Keep the existing `powercontext` launcher unchanged.
Inspect local software/registration/manager facts; add bounded Runtime startup probes for doctor. Reuse relocated
native ownership implementation before every lifecycle mutation. Repair only the explicitly selected exact package
/profile through uv; preserve explicit source controls and configuration/data. Require an explicit stop before
replacing a Runtime backing an active owned service; never start/restart automatically. Report package installation,
CLI startup, manager state, liveness, and readiness separately. Bootstrap handles missing whole wheel/environment or
interpreter. No universal supervisor, new package, or installation transaction ledger is justified by these cases.

## Primary source research

Lody source is pinned to `811b573329716b23e1144e5d66211ea4ddfb0dfd`:

- [daemon controls](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/apps/cli/src/commands/daemon.ts)
  verify recorded instance and live Host identity before authenticated shutdown; stale PID does not establish ownership.
- [upgrade installer](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/apps/cli/src/lib/machine-lifecycle.ts)
  resolves the installed npm package entry, ensures it remains inside the package, and compares installed/reported
  concrete versions rather than using whichever command PATH resolves.
- [readiness/handoff](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/apps/cli/src/commands/daemon-shared.ts)
  distinguishes installation from worker readiness and drains the exact failed replacement before recovery.
- [upgrade specification](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/specs/daemon-upgrade-installation.md)
  states that handoff recovery does not roll back npm package files.

History inspected includes [startup hardening](https://github.com/LodyAI/Lody/commit/932c83dfdd550b6a784be9c22cf79f05f345c616)
and [release-version identity](https://github.com/LodyAI/Lody/commit/60ce0f34c98a2bca8700c073082c4b0ae7424d62).
These are source/history observations, not executed Lody daemon qualification. PowerContext already has native user
service managers and exact ownership metadata; importing Lody's daemon supervisor would solve a different problem.
