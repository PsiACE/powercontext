# Installation design evidence

RFC 1892 uses native shell bootstrap, uv-owned package environments, opt-in Agent setup, and explicit installed-command
verification. These studies test the boundaries that can invalidate that design. They are reproducible design evidence;
maintained product acceptance lives in `tests/fixtures/installation/`, `tests/test_installation_contract.py`, and
`tests/native/`. Experimental variants are not distribution entry points.

| Study | Question and controlled change | Result | Design consequence |
| --- | --- | --- | --- |
| [Bootstrap](bootstrap/README.md) | Remove all host handling, then remove only implicit selection | Removing only implicit selection permits unattended install while preserving explicit host failure | Runtime installation has no terminal/Git prerequisite; `--host` remains meaningful |
| [Sources](sources/README.md) | Shell filename preflight versus real uv; explicit/configured/default source faults | Valid encoded wheel links fail the shell probe and install with uv | uv owns package interpretation; automatic mirror availability is checked before installation |
| [Release and recovery](release/README.md) | Remove stable policy, executable checks and custom repair records | Prerelease-only selection changes; accepted broken entry points replace old launchers; explicit retry repairs them | Keep stable policy and profile checks; report partial state without promising rollback |

The source study executes 46 cases per uv version and the release study executes 11, using both existing uv 0.11.14
and the bootstrap-pinned uv 0.12.23. Bootstrap ablations use executable fixtures on Linux. Source experiments use real
uv, synthetic wheels, loopback HTTP and separate Bash/PowerShell helper probes. Release experiments use real uv and
local wheels. These are not native macOS/Windows, real Agent workflow, public mirror uptime, or performance claims.
Each study documents exact reproduction commands, inputs, source revisions and measured outputs.

The retained source policy allows an automatic China mirror to fall back to PyPI when its availability request fails.
A reachable but stale mirror stays selected; uv reports an unavailable exact requirement. This avoids switching sources
after an installation attempt may have mutated files. Explicit sources never receive distribution-owned substitution.
Existing uv configuration conservatively suppresses automatic package/Python mirrors, even for unrelated settings.

## Primary-source comparison

| Project and pinned source | Responsibility actually owned | Applicable practice |
| --- | --- | --- |
| [Magpie installer](https://github.com/yetone/magpie/blob/023f5aaad2ecd41ae04390166b9cac9a0b300d81/site/public/install.sh) | Direct binary selection, download, checksum and replacement | Separate release identity from transport; verify before reporting completion |
| [Lody daemon contract](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/specs/daemon-upgrade-installation.md) | npm install destination and replacement readiness | Execute the actual installed entry point; describe recovery limits accurately |
| [Lody managed runtime](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/apps/cli/src/agent/managed-agent-runtime.ts) | Raw archives, manifests, staging and completion records | Add installation records when owning artifacts independently, not when duplicating uv |
| [Bub installers](https://github.com/bubbuild/bub/tree/b4a61bf1326729a024161d22ba20019b8500f907/website/public) | Shell bootstrap followed by a Python preset resolver | Keep clean-machine bootstrap and native terminal/platform regressions explicit |
| [uv distribution configuration](https://github.com/astral-sh/uv/blob/d3c092a30ed2712d15c855af652d5741d4d5c7c5/dist-workspace.toml) | Generated installers for uv's native binaries | Delegate uv artifact selection to its installer; this does not generate PowerContext package policy |

The recommendations are an inference from those ownership boundaries and the executed experiments, not a claim that
every project should use the same installer. The reports link relevant implementation history and tests. Lody's public
tag-release workflow explicitly excludes installer publishing, and Magpie delegates release building elsewhere; neither
public tree establishes its complete production release pipeline.

## Independent delivery boundaries

- Host opt-in owns unattended installation and explicit integration completion; it depends on the existing uv bootstrap.
- Source resolution owns index interpretation and source precedence; it does not require host or capability changes.
- Capability verification owns truthful Runtime completion; optional host execution retains its separate failure result.
- Shared native conformance cases and the bilingual RFC document and qualify the integrated distribution.

A generator, Python installer engine, custom receipt or immutable component manifest remains possible for a future
independent-artifact contract. None is required to satisfy the measured package-bootstrap behavior. Sharing executable
cases provides an as-code contract while preserving the small set of existing owners.
