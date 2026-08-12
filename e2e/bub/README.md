# End-to-end workload harness

This directory contains PowerContext's end-to-end workload catalog. LoCoMo is used as a pinned input sample, not as a
benchmark suite. The Terminal-Bench case retains its native task and verifier, while PowerContext acceptance is based
on Memory collection, grounding, and recall rather than the native task reward.

The common architecture reserves `basic`, `bub`, and `codex` execution profiles. This harness currently implements
only `bub`; migrating the complete LoCoMo benchmark or the separate SWE-Pro evaluation is outside its scope.

Every workload follows one execution path:

```text
Pydantic manifest and settings
  -> declared PowerContext setup through the public Client
  -> Harbor Job
  -> Harbor ACP runner
  -> Bub ACP server
  -> PowerContext
  -> Pydantic Memory evaluation
  -> Pydantic JSON evidence and Marko report
```

Harbor owns task and agent execution. Local multi-step Harbor tasks model independent capture and recall sessions;
registry-backed tasks such as Terminal-Bench use the same `Job.run` call. The harness does not contain a second Bub
runner. Pydantic validates configuration, manifests, observations, and evaluation reports. Marko renders the Markdown
summary.

## Layout

```text
e2e/bub/
  tasks/                  # PowerContext manifests and evaluation expectations
  harbor-tasks/           # Local Harbor tasks used by built-in samples
  src/powercontext_e2e/   # One Harbor runner and one Memory evaluator
```

All manifests use the same schema:

```yaml
schema: powercontext.e2e-task/v1
id: project-database-decision
categories:
  - acceptance
  - sample
dataset:
  path: e2e/bub/harbor-tasks
  task_id: project-database-decision
  checksum: <harbor-task-checksum>
execution:
  type: bub
  model_source: none
  bub_version: 0.4.2
  acp_server_version: 0.0.2
powercontext:
  host_url: http://127.0.0.1:8000
  container_url: http://host-gateway:8000
  timeout_seconds: 30
evaluation:
  expected_memory:
    - OceanBase
  probes:
    - id: database-decision
      query: What database did this project select, and why?
      expected_context:
        - OceanBase
```

The dataset can be a local Harbor dataset path or a registry dataset name and version. `execution` selects the
implemented profile and its budget. Optional `setup` entries prepare declared public PowerContext state in the
isolated workload scope.
`evaluation` declares only externally observable Memory behavior.

The built-in manifests are:

| ID | Dataset | Categories | Purpose |
| --- | --- | --- | --- |
| `approved-experience-recall` | local Harbor multi-step task | `live`, `experience` | Approved Experience injection and Memory collection |
| `locomo-support-group` | local Harbor multi-step task | `acceptance`, `sample` | Pinned LoCoMo-derived sample |
| `project-database-decision` | local Harbor multi-step task | `acceptance`, `sample`, `smoke` | Durable project decision |
| `terminal-bench-db-wal-recovery` | `terminal-bench@2.0` | `long-horizon`, `terminal-bench` | Long-running capture and recall |

## Run tasks

Against an existing PowerContext Server, run the default `acceptance` category:

```bash
export POWERCONTEXT_E2E_SERVER_URL=http://127.0.0.1:8000
make harness-run
```

Select multiple IDs or categories with comma-separated values:

```bash
POWERCONTEXT_E2E_IDS=locomo-support-group,project-database-decision make harness-run

POWERCONTEXT_E2E_CATEGORIES=acceptance,sample make harness-run
```

ID and category selection are additive. The same selectors work in the fixed Compose harness:

```bash
make harness-compose-run

POWERCONTEXT_E2E_DATABASE=oceanbase \
POWERCONTEXT_E2E_IDS=locomo-support-group,project-database-decision \
make harness-compose-run
```

The live approved Experience workload uses the same command and fixed harness:

```bash
POWERCONTEXT_E2E_IDS=approved-experience-recall make harness-compose-run
```

It requires Codex OAuth plus configured PowerContext generation and embedding inference. The manifest declares three
approved Experiences. The common runner proposes and approves them through the public Client in the isolated scope,
flushes the setup Sources, records the pre-execution Memory baseline, then starts one Harbor multi-step task. Every
step uses the existing Bub ACP agent and PowerContext integration. The native verifiers record a reward for each step
without stopping later steps after a failed task assertion. Memory acceptance checks that the Experiences reached the
agent through prepared context and that the agent run produced grounded, recallable Memory. Setup Sources and Sources
captured from the agent trajectory are both valid provenance, while only Memory added after the pre-execution
baseline counts as created during the run. No separate Codex runner or Experience-specific agent participates in this
path.

Each selected workload writes the same layout:

```text
<output>/<workload-id>/
  replay.json
  eval-report.json
  report.md
  harbor-jobs/
```

`replay.json` is a self-contained Pydantic observation. It identifies the `bub` execution profile and records the
scope's initial Memory, the pre-execution baseline after declared setup has been processed, and the instructions
resolved by Harbor's ACP runner.
`eval-report.json` uses
`powercontext.e2e-evaluation/v1`. `report.md` is rendered from the report model with Marko. Native Harbor and ACP
evidence remains under `harbor-jobs/`.

## Long-horizon task

The Terminal-Bench manifest pins its task checksum, Bub and ACP server versions, Codex OAuth model source, step
budget, capture cadence, recall probes, and acceptance thresholds. Run it with the same command:

```bash
POWERCONTEXT_E2E_CATEGORIES=long-horizon make harness-compose-run
```

This task requires privileged Linux containers, enough time and disk for the task image, a valid Codex OAuth document
at `${CODEX_HOME:-$HOME/.codex}/auth.json`, and configured PowerContext generation and embedding inference. For
example:

```bash
export OPENROUTER_API_KEY=replace-me
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openrouter:deepseek/deepseek-v4-pro
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_TIMEOUT_SECONDS=120
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=openrouter:qwen/qwen3-embedding-4b
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=openrouter-qwen3-embedding-4b-2560-unit
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=2560
POWERCONTEXT_E2E_CATEGORIES=long-horizon make harness-compose-run
```

If the agent task container requires an outbound proxy, set `POWERCONTEXT_E2E_AGENT_PROXY_URL` to a URL reachable
from that container. In the fixed nested-container harness, `host-gateway` addresses the harness container, so a
proxy exposed there can be passed as `http://host-gateway:<port>`. The typed setting is also treated as a secret when
evidence is written.

Agent setup uses Bub's supported installation path: `uv tool install` installs Bub with the local PowerContext plugin,
then `bub install bub-acp-server` adds the ACP server to the same environment. Harbor uploads and runs its native ACP
client. The Terminal-Bench task keeps its original image, setup, verifier, and isolation boundary. The harness ignores
dataset CPU and memory limits because it evaluates Memory behavior rather than benchmark resource compliance. This
also keeps the fixed harness usable in nested container runtimes that cannot create additional cgroups.

Long-horizon acceptance requires observable Memory behavior:

- the manifest checksum matches Harbor's resolved task;
- native ACP evidence exists;
- completed Bub events were captured at the configured coverage;
- a checkpoint created Memory in an initially empty scope;
- new Memory cites sources captured during the run;
- recall probes return prepared context.

In-run context injections remain a reported metric, but they do not gate this single-session task because extraction
may complete only at the final checkpoint. Harbor rewards are diagnostic scores and do not gate Memory acceptance.

## Rescore evidence

Every workload uses the same offline command:

```bash
REPLAY=.powercontext-e2e/bub/sqlite/run/terminal-bench-db-wal-recovery/replay.json \
make harness-rescore
```

Configuration and environment values are loaded with Pydantic Settings. The Bub plugin uses Bub's Pydantic settings
extension and accepts the same fields in the `powercontext` section of `bub.yml`. Known configured secrets are
redacted at every final evidence sink. CI scans evidence with TruffleHog before publishing it. Native ACP artifacts can
contain arbitrary command output and should be reviewed before sharing.
