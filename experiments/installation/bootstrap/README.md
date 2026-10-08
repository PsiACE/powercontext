# Bootstrap boundary evidence

The runtime bootstrap can remain shell code delegated to uv. Host selection is an optional operation with a different success condition. Removing that operation eliminates the terminal/Git prerequisite and mixed runtime/host completion status; it does not change the package command for an explicitly runtime-only installation. These results support a separate runtime-only default, subject to an intentional contract change to RFC 1892. They do not justify silently ignoring `--host`.

## Reproduction and scope

Run `python experiments/installation/bootstrap/run.py` from any directory. The harness reads the installer from pinned Git revision `8b2ea957` (override explicitly with `--baseline REV`) and creates a temporary directory beneath `~/.cache/powercontext-installation-research/bootstrap`, deleting it at completion. It requires Bash and standard Linux command-line tools. `results.json` is the bounded run on baseline `8b2ea957`; it contains no downloads or personal configuration. The interpreter-free PATH belongs only to child processes; Python runs the harness.

Bash is executed natively on Linux. uv, Python discovery, the installed CLI and a failing host adapter are executable fixtures. No actual package installation, Windows, macOS, network mirror, or host application is exercised by this harness. A separate native read-only check ran uv 0.11.14's `python find --system --no-project --no-python-downloads '>=3.11,<4'` and found a managed CPython 3.13. These observations do not qualify platform support.

The independent variable is the host boundary: the transformed script removes terminal host-choice validation, the Git prerequisite, and host setup sequencing/status. It retains argument validation, region/source policy, dependency discovery, tool installation and installed-version verification. Inputs and tool fixtures are identical within each pair. The experimental variant retains host parsing to hold inputs constant; production runtime-only code must reject unsupported host arguments or explicitly dispatch them.

| Input | Baseline | Host boundary removed | Interpretation |
| --- | --- | --- | --- |
| No choice, stdin not a terminal, Git absent | Exit 1, no Runtime write | Exit 0, Runtime write | Host prompting prevents unattended runtime bootstrap |
| `--no-hosts`, same environment | Exit 0 | Exit 0, identical uv command trace | Removing hosts adds no runtime installation capability for this input |
| `--host codex`, adapter exits 17 | Exit 1, Runtime preserved | Exit 0, Runtime preserved, no setup | Smaller scope separates success; selected-host equivalence is intentionally lost |
| Python-free PATH, shell help | Exit 0 | N/A | Shell entry can report help without Python |
| Same PATH, direct Python engine start | Exit 127 | N/A | Engine cannot replace the pre-Python bootstrap boundary |

Command traces express the observable subprocess interface, not an internal implementation ownership requirement. Elapsed time is deliberately not compared: fixture commands cannot establish network, package-resolution, or real-host performance. Baseline scripts contain 369 Bash and 346 PowerShell lines; this is a maintenance surface measurement, not a speed claim.

The third candidate removes only implicit host selection, retaining Git checks and setup for explicit `--host`. With no choice it exits 0 and writes the Runtime; with `--no-hosts` it matches baseline commands and success; with `--host codex` and adapter failure it matches baseline exit 1, Runtime preservation, setup invocation and recovery error. It therefore preserves explicit host failure semantics while eliminating the unattended terminal prerequisite. The full runtime-only candidate is simpler but loses integrated host behavior and requires a deliberate CLI migration.

### As-code choice

| Candidate | Fact/policy ownership | What it establishes | Additional machinery |
| --- | --- | --- | --- |
| Shared declarative conformance cases | Public inputs and observable outcomes | Both entry points satisfy the same behavior when executed natively | Case loader and existing platform runner |
| Shared defaults/policy file | Constants such as profiles and source endpoints | One source for selected values only | Distribution/versioning of another bootstrap asset, or embedding generation |
| Generated shell adapters | Generator owns emitted sequences | Parity only when the generator and emitted artifacts are exercised | Generator, checked-in outputs, regeneration/check discipline |
| Python engine after uv | Runtime policy owns sequencing | Central implementation after prerequisites exist | Engine distribution/versioning and a shell bootstrap retained |

Choose behavior-driven conformance first. Current differences are public choices, validation, failure states and side effects rather than a large independently maintained fact database. A defaults file cannot prove those behaviors; a generator would still need the same cases and would add an ownership surface without demonstrated benefit. The case contract should assert installed release/profile, intended source passed to uv, preservation on failure, no implicit configuration/service writes, and selected-host result. It should not assert function names, source imports, internal call order, or total subprocess counts. Native platform execution remains required; sharing data does not turn Linux fixtures into Windows evidence.

## Immutable source investigation

All repository files below were read through `/usr/bin/gh api` using the active account, with `GH_TOKEN` and `GITHUB_TOKEN` unset. Repository main revisions were resolved on 2026-10-09. Cache snapshots remain outside the repository.

### Magpie

At [`023f5aaad2ecd41ae04390166b9cac9a0b300d81`](https://github.com/yetone/magpie/tree/023f5aaad2ecd41ae04390166b9cac9a0b300d81), [`site/public/install.sh`](https://github.com/yetone/magpie/blob/023f5aaad2ecd41ae04390166b9cac9a0b300d81/site/public/install.sh) is a POSIX shell binary installer. It selects platform assets, fetches a site release feed, verifies SHA-256 before replacing the binary, and reports launch/PATH instructions. Optional `--proxy` applies to both feed and artifact fetches; optional `--mirror` rewrites only GitHub artifact URLs. The authoritative feed remains independent of the artifact mirror. It has no Python prerequisite and does not orchestrate Agent installation.

[`site/install.test.mjs`](https://github.com/yetone/magpie/blob/023f5aaad2ecd41ae04390166b9cac9a0b300d81/site/install.test.mjs) executes shell with simulated uname/curl/feed, checking Termux platform selection, spaces, missing assets, checksum rejection and proxy/mirror behavior. These are fixture tests, not native Android qualification. [`release.yml`](https://github.com/yetone/magpie/blob/023f5aaad2ecd41ae04390166b9cac9a0b300d81/.github/workflows/release.yml) dispatches builds/signing to `yetone/magpie-releases`; this source workflow alone cannot prove checksums correspond to independently trusted releases.

Meaningful history: [`bc141ad83418762e277a4ac8febced6f7a47ad7d`](https://github.com/yetone/magpie/commit/bc141ad83418762e277a4ac8febced6f7a47ad7d) adds explicit proxy/mirror controls and tampered-mirror rejection; [`69368007b044c98b0b56e74df5c2096cc20bade1`](https://github.com/yetone/magpie/commit/69368007b044c98b0b56e74df5c2096cc20bade1) adds Android asset selection and avoids substituting Linux binaries. Transfer the separation of authoritative release identity and transport, plus preservation on verification failure. Do not transfer its binary asset machinery to uv-managed Python wheels without a corresponding distribution need.

### Bub

At [`b4a61bf1326729a024161d22ba20019b8500f907`](https://github.com/bubbuild/bub/tree/b4a61bf1326729a024161d22ba20019b8500f907), [`install.sh`](https://github.com/bubbuild/bub/blob/b4a61bf1326729a024161d22ba20019b8500f907/website/public/install.sh) and [`install.ps1`](https://github.com/bubbuild/bub/blob/b4a61bf1326729a024161d22ba20019b8500f907/website/public/install.ps1) bootstrap uv, download a preset catalog, launch an embedded Python resolver through `uv run --no-project`, then create a dedicated venv and delegate plugin installation to Bub. Interactive mode adds `inquirer-textual==0.8.0`. Thus Bub is evidence that Python policy can begin after uv bootstrap, not evidence that an engine eliminates bootstrap. Its catalog/prompt feature incurs an additional resolver execution and optional prompt dependency; no startup cost is measured here.

[`tests/test_install_scripts.py`](https://github.com/bubbuild/bub/blob/b4a61bf1326729a024161d22ba20019b8500f907/tests/test_install_scripts.py) combines executable shell fixtures with direct embedded-resolver tests; a PowerShell parser check runs only if available. [`on-release-main.yml`](https://github.com/bubbuild/bub/blob/b4a61bf1326729a024161d22ba20019b8500f907/.github/workflows/on-release-main.yml) builds/publishes the Python package with uv trusted publishing.

Meaningful history: [`d599384206e28ec11309e8e702a39d8997eda69a`](https://github.com/bubbuild/bub/commit/d599384206e28ec11309e8e702a39d8997eda69a) fixes prompt terminal streams; [`3db09b6e2d9bf9e107285a6381f4a5898f96428d`](https://github.com/bubbuild/bub/commit/3db09b6e2d9bf9e107285a6381f4a5898f96428d) fixes macOS system Bash compatibility. These are concrete costs of shell portability and piped interactivity. Adopt their regression focus, not their preset engine unless PowerContext owns an equivalent catalog problem.

### uv and cargo-dist

At [`d3c092a30ed2712d15c855af652d5741d4d5c7c5`](https://github.com/astral-sh/uv/tree/d3c092a30ed2712d15c855af652d5741d4d5c7c5), [`dist-workspace.toml`](https://github.com/astral-sh/uv/blob/d3c092a30ed2712d15c855af652d5741d4d5c7c5/dist-workspace.toml) pins cargo-dist 0.32.0, generates shell/PowerShell installers for uv binaries, specifies platform targets, install paths, hosting and release attestations. [`release.yml`](https://github.com/astral-sh/uv/blob/d3c092a30ed2712d15c855af652d5741d4d5c7c5/.github/workflows/release.yml) performs dist plan/build/host alongside uv-specific builds and publication. These generators solve native artifact distribution; adding cargo-dist to PowerContext would not generate its wheel/extras/host policy from these settings.

[`installer.md`](https://github.com/astral-sh/uv/blob/d3c092a30ed2712d15c855af652d5741d4d5c7c5/docs/reference/installer.md) documents `UV_INSTALL_DIR`, `UV_NO_MODIFY_PATH` and ephemeral `UV_UNMANAGED_INSTALL` (which also disables self-update). Preserve these controls and delegate platform artifact selection to the official uv installer. Do not use unmanaged installation by default merely to avoid PATH modification; its self-update consequence changes user behavior.

## Proposed independent implementation slice

Keep the current uv bootstrap and native shell entry points. Make implicit host selection opt-in in the as-code contract: install without a host-choice terminal or Git prerequisite by default, and retain explicit `--host` dispatch after Runtime verification with its existing nonzero failure and recovery semantics. Print the existing release-pinned `powercontext setup` next step. A fully runtime-only script is a separate possible migration requiring an argument error and documentation migration. Never accept and ignore `--host`.

Before centralizing policy into a new engine or generating scripts, establish a shell-independent case catalog for public inputs, effective uv requirement, prerelease policy, installed-version verification, side effects and exit results. Run that same catalog through both native entry points. Generation is justified only if a concrete generator reduces policy duplication while its emitted scripts remain reviewable; a catalog alone supplies executable parity without creating a runtime engine.

Falsified alternatives: a direct Python engine on a clean Python-free machine; calling the host-free ablation equivalent for selected-host inputs; treating current Bub as a minimal shell-only reference; and treating cargo-dist's uv binary configuration as a PowerContext package-policy generator. Not falsified: an engine bootstrapped through uv, generated narrow shell adapters, or explicit integrated host setup. This experiment supplies no evidence that those alternatives are faster or more reliable than existing delegation.
