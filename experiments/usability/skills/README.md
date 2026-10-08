# Task-oriented Skill evidence

Task guidance must report the stage actually completed. Memory writes, temporary/durable Handoff, candidate review,
package export/installation, and execution have different evidence and permission boundaries. Existing public
operations establish those distinctions; a generic tool catalog does not by itself define a complete task journey.

## Reproduce

```bash
mkdir -p "$HOME/.cache/powercontext-usability/skills/scratch"
git worktree add --detach "$HOME/.cache/powercontext-usability/skills/baseline" \
  5d5813a8a4ffb446ff26ab05fae60eb1b08a72fb
cd "$HOME/.cache/powercontext-usability/skills/baseline"
TMPDIR="$HOME/.cache/powercontext-usability/skills/scratch" uv sync --locked
python experiments/usability/skills/run.py \
  --python "$PWD/.venv/bin/python" \
  --scratch "$HOME/.cache/powercontext-usability/skills/scratch" \
  --output "$HOME/.cache/powercontext-usability/skills/baseline-results.json"
```

The selected interpreter needs existing Runtime/test dependencies. Source baseline is pinned to
`f28f8edfcb2972ac322f4f12b1f224226a9a40ab`; modified Runtime, tests, or integrations are rejected before execution.
The harness captures actual command help and executes six public behavior workflows against local Runtime/SQLite or
HTTP TestClient boundaries. [results.json](results.json) preserves outputs and selected actual host capability records.
No external model request or native Agent host runs in this harness.

## Executed task boundaries

| Intent/state ablation | Executed evidence | Guidance implication |
| --- | --- | --- |
| Direct Memory save vs candidate proposal | Memory write persists directly without a candidate | Do not require review or substitute Source capture for explicit save |
| Experience pending vs approved | Candidate revise/approve/retrieval gate behavior | Generation/proposal is not an approved revision |
| Managed Skill current revision vs replacement | Exact lineage and review gate behavior | Preserve current version and exact approved reference |
| Rejected candidate vs revisable pending state | Terminal rejection and Scope evidence isolation | Do not reopen a rejected proposal or broaden evidence Scope |
| Temporary Handoff vs durable milestone | Complete prepared carrier and explicit commit behavior | Transfer does not imply durable commit or executed continuation |
| Skill package vs usage/governance | Real archive manifest/download/readback, revision, idempotent usage, retirement conflicts | Export/readback is separate from invocation; recorded usage must match exact package identity |
| Available CLI vs imagined command | Actual candidate/Experience/Skill/export/import/doctor help executes | Guidance must use installed command arguments and target capabilities |
| Broad vs restricted host catalog | Actual Codex/OpenClaw/Hermes/DSH capability records captured | Do not project another host's review or inventory operations onto a restricted target |

The tests execute local handling and persistence; generation-related checks may use controlled inputs. They do not
establish that a live model selects tools correctly, that a host discovers the Skill, or that generated instructions
succeed when executed. Resource checks and the Skill validator establish format/reachable resources only.

## Independently reviewable content

[maintenance entry](fixtures/powercontext-install/SKILL.md) is independently loadable before Runtime and includes a
missing-command bootstrap branch plus ownership/repair guidance. [product entry](fixtures/powercontext-project-context/SKILL.md)
loads only the selected Memory, Handoff, or Experience/Skill reference. These native Skill folders are concrete content
fixtures; they do not add an executor or wrapper schema.

Independent GPT-6.1 Sol text review used realistic requests and minimal relevant references. It found a missing
fresh-install starting path, now supplied through official installation sources and concrete uv local/Client-only
requirements. Its judgments about ordinary summary, Memory save, temporary/durable Handoff, restricted review, and
external import concern guidance interpretation only. The separate review artifact records scenarios and limits;
none of these judgments establish real host/model execution.

## Primary source research

Magpie here means [yetone/magpie](https://github.com/yetone/magpie), pinned to
`23d9e5def8c5595c7a8a49df8f2a376084980927`, rather than an unrelated project with the same name.
[Skill storage/link/copy ownership](https://github.com/yetone/magpie/blob/23d9e5def8c5595c7a8a49df8f2a376084980927/internal/library/skills.go)
keeps source metadata and distinguishes owned installation trees. Its replacement prepares a complete new copy,
moves the old copy aside, and restores it if switching fails; ownership alone is not execution success.
[Update inspection](https://github.com/yetone/magpie/blob/23d9e5def8c5595c7a8a49df8f2a376084980927/internal/library/skillcheck.go)
checks source/content identity separately from installation.
[Plugin host execution](https://github.com/yetone/magpie/blob/23d9e5def8c5595c7a8a49df8f2a376084980927/internal/plugin/host.go)
is an implementation boundary, not permission granted by a Markdown Skill.

These source observations motivate precise local package/result identity. PowerContext's existing domain owners
already separate candidate approval, Artifact revision, package projection, installation, and usage; Skills should
explain those operations rather than duplicate them. No Magpie native execution is claimed.

## Final native resources and workflow qualification

`skills/powercontext-install/` is an independently loadable native Skill folder. It starts with the published
installation guide, prefers Bash/PowerShell scripts only when advertised and available, and retains the guide's uv
path for older releases. Installed help/capabilities gate optional new Ops commands. Native project resources use
the declared canonical/override distribution manifest; `make plugin-skills` renders applicable host projections.
The manifest records Runtime contract baseline `f28f8edf` and the optional maintenance-entry condition.

`qualify.py` executes the final projection check, seven actual CLI discovery cases, and six public state workflows
in one pytest run. From the selected product branch, run `uv sync --locked` and use `--python "$PWD/.venv/bin/python"`
with scratch/results under `$HOME/.cache/powercontext-usability/skills/`. Reproduce with `python experiments/usability/skills/qualify.py --python <configured-python>
--scratch <cache>/skills-qualification --output <results.json>`. Source drift from the pinned Runtime baseline is
rejected; intentional Skill/distribution changes are hashed in `qualification.json`. Memory direct write, temporary
and durable Handoff, Experience revise/approve/reject, exact Skill replacement lineage, archive download/readback,
usage idempotency, digest conflict and deprecated-revision gates executed successfully. These are controlled
Runtime/HTTP behavior tests with configured fixtures, not real model routing or native Agent execution.

Optional maintenance commands have separate actual-wheel and real-Linux-manager evidence in Ops commit
`4995cf4cd92caa1ff854e99be477b5469b7840c7`; the Skill branch does not pretend older Runtime wheels contain them.
Independent GPT-6.1 Sol scenario text review is recorded separately at `090a3cce` under
`experiments/usability/skills-review/`, including the missing fresh-install path and its resolved bootstrap branch.
Text review establishes the guidance interpretation under selected catalogs, not live host discovery or execution.
