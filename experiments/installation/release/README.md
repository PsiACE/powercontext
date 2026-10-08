# Package installation release boundary

## Reproduce

Run from the repository root, with an existing Python and uv:

```sh
python experiments/installation/release/run.py \
  --scratch "$HOME/.cache/powercontext-installation-research/release" \
  --output experiments/installation/release/results.json
```

The harness creates small synthetic wheels, invokes the real uv executable, and executes its installed launcher.
It uses private tool/cache directories and no package network. It removes its private working directory afterward.
Results were observed on Linux with uv 0.11.14 and the installer-pinned uv 0.12.23, using Python 3.14.
Both runs produced the same case outcomes; these results do not qualify Windows/macOS. Synthetic wheels establish installer semantics, not production
PowerContext readiness or real Agent acceptance.

## Controlled ablations

| Fixed input/requirement | Removed mechanism | Observed result | Consequence |
| --- | --- | --- | --- |
| Latest must mean stable; index has stable and prerelease wheels | Explicit prerelease exclusion | Both variants choose 2.0.0 | This particular index does not expose the policy difference. |
| Latest must mean stable; same index after hiding stable wheels | Explicit prerelease exclusion | uv installs 3.0.0rc1; explicit exclusion fails | Keep `--prerelease disallow`; delegation needs an explicit policy input. |
| Installed prerelease; stable candidates available | Reinstall only the PowerContext package | Upgrade with prereleases disallowed still keeps the installed prerelease; package reinstall selects stable | Latest must add `--reinstall-package powercontext`, not just an upgrade or refresh. |
| Working 1.0.0; request unavailable 99.0.0 | Custom rollback and custom receipt | uv fails; old launcher runs; uv receipt remains byte-identical | No additional receipt/rollback layer is needed for this resolution failure. |
| Accepted wheel has failing entry point | Executable check | uv installation succeeds; launcher fails and previous installation is replaced | Package acceptance is not executable capability. Verification detects failure but does not roll package files back. |
| Distribution metadata says 5.0.0, CLI prints 1.0.0 | Compare requested/reported version | uv accepts distribution; CLI reports wrong version | Exact request comparison rejects this mismatch. `latest` needs no hypothetical additional metadata framework. |
| Failed/broken installation; reinstall known 1.0.0 | Custom repair record | Explicit uv reinstall restores launcher | Component retry works without a second persisted state authority. |
| Local wheel replaced; constraint retains old `file://...#sha256=` | Independent file verification | uv accepts changed local wheel even with refresh | A local URL fragment is not a demonstrated enforced checksum contract. Do not generalize native fixture constraints into supply-chain assurance. |

The hash case measures local file behavior only. It makes no claim about HTTP Simple API hash enforcement, PyPI
publisher provenance, or mirrors serving the same bytes. Hashes obtained from the same potentially changed index
establish transfer integrity rather than an independent publisher identity. User-selected additional indexes retain
uv's priority; `--default-index` is not an exclusive source pin.

## Verified public Lody source

Inspection snapshot: `811b573329716b23e1144e5d66211ea4ddfb0dfd` (2026-10-09). Public tree contains no
`scripts/install.sh` or `scripts/install.ps1`; a commercial distribution's private pipelines cannot be inferred from it.

- [Tag release contract](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/specs/tag-releases.md)
  and [release workflow](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/.github/workflows/release.yml)
  synchronize app versions through a PR and create changelog-only releases. They expressly do not build/publish
  installers, npm packages, or updater metadata. Repeat runs preserve existing assets.
- [Daemon installation contract](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/specs/daemon-upgrade-installation.md),
  [implementation](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/apps/cli/src/lib/machine-lifecycle.ts),
  and [synthetic tests](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/apps/cli/src/lib/machine-lifecycle-upgrade.test.ts)
  resolve the actual npm global destination, validate package name/version and entry containment, execute that entry,
  then require replacement readiness. History [#1228](https://github.com/LodyAI/Lody/commit/4b4d91244a0b627bc5cc09ffad4b448e8404a715)
  addresses restarting an old npx-cache entry after global upgrade. Its documented recovery does not roll back npm files;
  registry and Windows/WSL acceptance remain unverified.
- [Managed Agent archive runtime](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/apps/cli/src/agent/managed-agent-runtime.ts)
  does own schema-validated archive manifests, checksum/size verification, staging, metadata, and a completion marker.
  It publishes raw archives directly and may keep old compatible runtimes. That responsibility justifies its own records;
  it does not establish that a uv tool wrapper needs to duplicate uv's records.
- [Background update coordinator](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/apps/cli/src/agent/managed-runtime-update-coordinator.ts)
  prunes superseded versions after installation; failures remain separate outcomes.
- [Updater metadata](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/apps/electron/src/main/services/app-updater-metadata.ts)
  gates localized changelog content on expected version. This is display metadata, not a package identity manifest.
- [Installation profile](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/packages/shared/src/node/installation-profile.ts)
  keeps namespace/data-root identity explicit. PowerContext should likewise leave user-owned configuration/data untouched;
  its bootstrap already delegates installation to separate uv tool directories.

## Standards and smallest production slice

[uv tools](https://docs.astral.sh/uv/concepts/tools/),
[uv indexes](https://docs.astral.sh/uv/concepts/indexes/), and the
[Python Simple API](https://packaging.python.org/en/latest/specifications/simple-repository-api/)
were read on 2026-10-09. They support delegating environment ownership, package selection, index authentication and
artifact handling to uv. The Simple API defines project/file metadata and hashes; it does not turn an arbitrary mirror
into the publisher. Cargo-dist is not needed for this Python tool wrapper.

The smallest independently useful change is profile capability verification before announcing installation success:

1. Keep latest stable exclusion and exact-version comparison.
2. Execute the launcher at the explicit directory returned by `uv tool dir --bin`.
3. Check CLI startup and required command availability; for local profile run `powercontext server run --help`.
4. On verification failure, state that package installation completed but verification failed, name the failed command,
   and preserve uv/prerequisites and user data for explicit retry. Do not claim old Runtime preservation after package acceptance.

PowerContext source baseline: [`8b2ea957`](https://github.com/PsiACE/powercontext/commit/8b2ea957).
PowerContext's `create_cli` skips providers raising `ModuleNotFoundError`; its `--version` callback reads distribution
metadata. Thus a missing Server dependency can leave version reporting successful while omitting the server command.
A profile smoke contract can be literal as-code command lists for `client` and `local`, consumed by both wrappers or
validated against them. The contract should describe executable commands, not a new receipt schema. Command help
verifies import/command availability, not configured providers, HTTP readiness, migrations, or service lifecycle.

A separate release manifest, durable installation ledger, and transactional cross-component rollback have no demonstrated
necessity for the scoped bootstrap. They would introduce another source of installation truth and broader failure
semantics. Consider them only if a future contract requires independently versioned raw artifacts or restoration after
accepted-package verification failures; the current component retry contract is smaller and already explicit.
