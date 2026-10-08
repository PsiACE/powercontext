# Independently available operations evidence

The smallest supported maintenance boundary is a standalone sibling entry in the existing wheel. It can inspect
native definitions without importing Runtime or replacing the normal launcher. Whole environment/interpreter removal
requires external bootstrap. Additional dispatcher ownership, environment records, and adoption state are unnecessary
for the demonstrated import failure.

## Reproduce

```bash
mkdir -p "$HOME/.cache/powercontext-usability/operations/scratch"
git worktree add --detach "$HOME/.cache/powercontext-usability/operations/baseline" \
  3f2e7323a9cf52d0bda936a108123b1f9bff29e4
cd "$HOME/.cache/powercontext-usability/operations/baseline"
TMPDIR="$HOME/.cache/powercontext-usability/operations/scratch" uv sync --locked
python experiments/usability/operations/run.py \
  --python "$PWD/.venv/bin/python" \
  --scratch "$HOME/.cache/powercontext-usability/operations/scratch" \
  --output "$HOME/.cache/powercontext-usability/operations/baseline-results.json"
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

## Installed implementation qualification

`qualify.py` executes 14 cases recorded in `qualification.json`. In a checkout of the selected feature commit,
run `uv sync --locked` first. Use `$HOME/.cache/powercontext-usability/operations/` for scratch and result files.
Build the selected branch wheel with `uv build
--wheel --out-dir <scratch>/wheels`, then run `python experiments/usability/operations/qualify.py --wheel <wheel>
--scratch <scratch>/qualification --output <results.json>`. It creates disposable environments and one uniquely
named Linux user unit, verifies the identifier is unoccupied, and removes that unit on exit. It does not modify the
normal `powercontext.service` registration.

The actual built PowerContext wheel is installed without dependencies into a selected disposable manual venv.
Runtime help fails while installed Ops status, doctor and native log-location reporting work. Offline unavailable
repair fails and the invoking environment remains usable. A separate controlled wheel with the production Ops
namespace and a broken fixture Runtime verifies exact named-uv repair into the explicitly different selected tool
installation. This does not qualify release dependency resolution or program rollback.

The real running Linux user systemd manager loads the exact production-rendered ownership metadata and executes
a controlled sleeping fixture process: start/restart become active, active repair is refused even when the owned artifact is deleted while the job
remains loaded, explicit stop becomes
inactive, and uninstall removes the owned artifact and manager registration. This is actual native manager
execution with a fixture process, not a live PowerContext Server, Windows Task Scheduler or macOS launchd claim.
Data evidence is checked unchanged after every command. Affected service, environment, configuration, diagnostic
and Ops public behavior tests passed 327 cases with nine platform skips. Focused Ops tests cover foreign refusal,
missing interpreter, unpinned/source rejection and stop-failure retention. Eleven focused Ops cases include
three regressions that prohibit package mutation for active/foreign/unknown loaded managers with no artifact. Whole environment/interpreter deletion still requires bootstrap recovery.
