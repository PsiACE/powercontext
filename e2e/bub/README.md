# Bub end-to-end harness

This harness runs committed scenarios through Bub and a real PowerContext Server. Every scenario uses one isolated
PowerContext scope. Every step uses a new Bub session, so durable state crosses the boundary only through PowerContext.

It supports three evidence modes:

- `acceptance` calls real Bub tools without a model. It blocks on process completion, Memory extraction, and prepared
  context recall.
- `live` sends the same inputs to a real Bub model. It adds agent and model spans, and records answer quality as a
  diagnostic score or judge label.
- `rescore` reads `replay.json` and runs the same Pydantic Evals oracle without rerunning Bub or PowerContext.

It also supports a `long-horizon` mode. This mode runs a pinned Terminal-Bench task through Harbor's ACP runner and
Bub, captures the completed user, model, and tool events into PowerContext, and evaluates whether useful Memory was
created and recalled. Passing the benchmark task is not an acceptance requirement. Harbor's native verifier reward is
preserved as a score and `task_outcome` label so task performance can be compared with Memory quality without
conflating the two.

Each run writes `replay.json`, `eval-report.json`, and `report.md`. The replay is self-contained and contains the
scenario, public Memory snapshots, prepared context, outputs, and Pydantic Evals-compatible spans. Known runtime
secrets are redacted when these files are written. CI also scans the complete evidence directory with TruffleHog
before publishing a summary or artifact; evidence is not published when that scan does not complete cleanly. Treat
local evidence as potentially sensitive until it has been inspected.

## Run against an existing Server

```bash
export POWERCONTEXT_BUB_BASE_URL=http://127.0.0.1:8000
export POWERCONTEXT_E2E_DATABASE=sqlite
make harness-acceptance
```

Run one real-provider scenario:

```bash
export BUB_MODEL=openai:model-name
export BUB_API_KEY=replace-me
export BUB_API_BASE=https://provider.example/v1
make harness-live
```

`BUB_MODEL` is reused by PowerContext and the Pydantic Evals judge for OpenAI and DeepSeek providers.
`POWERCONTEXT_E2E_JUDGE_MODEL` selects an explicit judge. Server generation settings take precedence, and embedding
always uses its own PowerContext profile.

## Run the complete environment

Docker Compose starts PowerContext and runs the same committed scenarios used in CI. SQLite is the default:

```bash
make harness-compose-acceptance
```

Run the same acceptance set against OceanBase:

```bash
POWERCONTEXT_E2E_DATABASE=oceanbase make harness-compose-acceptance
```

`make harness-compose-live` uses the provider variables above. Evidence is written below `.powercontext-e2e/bub/`;
set `POWERCONTEXT_E2E_OUTPUT` to keep it elsewhere. Compose containers, networks, and volumes are removed after both
successful and failed runs. `make harness-compose-down` remains available as an idempotent manual cleanup.

## Run the Harbor long-horizon evaluation

The committed manifest at `e2e/bub/manifests/terminal-bench-db-wal-recovery.yaml` pins the Terminal-Bench dataset,
task checksum, Bub and ACP server versions, model source, step budget, capture cadence, recall probes, and acceptance
thresholds. The default model is `openai:gpt-5.4`; Bub reads the existing Codex OAuth document instead of requiring an
OpenAI API key.

The complete run requires:

- Docker Compose with support for privileged Linux containers;
- a valid Codex OAuth document at `${CODEX_HOME:-$HOME/.codex}/auth.json`, or at
  `POWERCONTEXT_E2E_CODEX_AUTH`;
- generation and embedding inference configured for the PowerContext Server; and
- enough disk and time to pull and execute the pinned Terminal-Bench image.

For example, configure the PowerContext Server provider in the environment, then run:

```bash
export OPENROUTER_API_KEY=replace-me
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openrouter:deepseek/deepseek-v4-pro
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=openrouter:qwen/qwen3-embedding-4b
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=openrouter-qwen3-embedding-4b-2560-unit
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=2560
make harness-long-horizon
```

The harness container is the Harbor control plane. It starts an isolated Docker daemon and creates the actual
Terminal-Bench task container inside it. Bub and the PowerContext integration are installed in that task container and
run through Harbor's official ACP runner. A TCP proxy exposes the Compose PowerContext service only to the nested task
network. The task therefore retains its original image, setup, verifier, and isolation boundary while the host only
needs to run the harness.

The acceptance result requires all of the following observable outcomes:

- the manifest checksum matches the Harbor trial task;
- Harbor's native `acp-summary.json`, `acp-events.jsonl`, and `trajectory.json` artifacts exist;
- completed Bub events were captured at the configured coverage;
- a completed checkpoint created Memory after the initially empty run scope;
- the new Memory cites sources captured during this run;
- the committed recall probes return prepared context; and
- prepared Memory was recalled during at least one model call in the run.

Evidence is written below `.powercontext-e2e/bub/sqlite/long-horizon/` by default:

- `replay.json` is the self-contained long-horizon observation and can be rescored offline;
- `eval-report.json` is the stable Pydantic Evals-compatible assertions, scores, labels, metrics, and attributes;
- `report.md` is the human-readable summary; and
- `harbor-jobs/` retains the native Harbor trial evidence, including the ACP trajectory and capture log.

Use the same offline scoring command as other scenarios:

```bash
REPLAY=.powercontext-e2e/bub/sqlite/long-horizon/replay.json make harness-rescore
```

The Codex OAuth document is mounted read-only into the Harbor harness and copied with mode `0600` into the ephemeral
task container. It is not included in capture logs or reports. The long-horizon Compose teardown removes the nested
Docker data volume, including that ephemeral copy. Run only committed, reviewed manifests in the privileged harness.
Native ACP artifacts can contain arbitrary task command output; review them before sharing, and do not retain or
publish raw container storage.
