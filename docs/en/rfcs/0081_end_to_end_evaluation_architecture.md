- Proposal Name: `unified_workloads_and_long_horizon_memory_evaluation`
- Start Date: 2026-08-07
- RFC PR: [oceanbase/powercontext#81](https://github.com/oceanbase/powercontext/pull/81)

# Summary

PowerContext will represent every sampled end-to-end scenario and long-horizon agent task as a workload. A workload
selects a Harbor task, sets its execution budget, and declares how the resulting Memory is evaluated. Local scenarios,
pinned samples, and registry datasets use the same manifest, runner, evidence, and report contracts.

All workloads follow one execution path:

```text
workload -> Harbor Job -> ACP -> Bub -> PowerContext -> evidence -> Memory evaluator -> report
```

The acceptance result answers whether the run captured useful, grounded, and recallable Memory. A source-native task
reward remains diagnostic and does not decide Memory acceptance.

# Motivation

PowerContext has small local scenarios, a pinned LoCoMo-derived sample, and tasks obtained from registries such as
Terminal-Bench. Their inputs and native graders differ, but those differences do not justify separate runners or report
formats. Separate paths make results harder to compare and let behavior drift between task types.

A long-horizon task is useful even when the agent does not solve it. The run can still show whether PowerContext
captured the investigation, preserved source provenance, created Memory during the task, and recalled that Memory
afterward. Task completion and Memory quality are related observations, not the same outcome.

# Guide-level explanation

## One workload model

The workload manifest is the catalog entry and execution contract:

```yaml
schema: powercontext.e2e-task/v1
id: terminal-bench-db-wal-recovery
categories:
  - long-horizon
  - terminal-bench
dataset:
  name: terminal-bench
  version: "2.0"
  task_id: db-wal-recovery
  checksum: <task-checksum>
agent:
  model_source: codex-oauth
  max_steps: 50
evaluation:
  capture_events: true
  checkpoint_every_events: 5
  probes:
    - id: investigation
      query: What records and SQLite table were found in /app/main.db?
```

`dataset` can identify a local Harbor task or a versioned registry task. The remaining workload fields keep the same
meaning in both cases. Pydantic validates manifests and runtime settings before execution.

Workloads have stable IDs. One command can run one ID, several IDs, or every workload in a category. Categories are
selection metadata and do not select a different runner.

LoCoMo input in this catalog is a pinned built-in sample. It checks the workload flow against fixed conversation data
but does not claim a LoCoMo benchmark result.

## One execution flow

The harness creates an isolated PowerContext scope and records its initial Memory. Harbor resolves the dataset, creates
the task environment, and runs the task through its ACP agent support. Bub receives the task instructions and uses the
PowerContext integration while it works. The integration captures eligible events and advances Memory checkpoints.

After Harbor finishes, the harness records native ACP evidence, final Memory, and the result of each recall probe. The
same evaluator then produces machine-readable and reviewer-readable reports. A failed workload still writes the
evidence collected before the failure.

There is no direct Bub runner beside Harbor. Local and registry tasks both enter through `Job.run`.

## Instruction boundary

The Harbor task owns agent-visible instructions:

- A local task stores them in its Harbor task directory, including step instructions for a multi-step task.
- A registry task obtains them from the pinned dataset entry.

The workload manifest does not copy those instructions. Duplicating them would create two sources of truth and could
silently change the task being evaluated. The evidence records the resolved instruction identity and content, subject
to redaction, so a reviewer can see what the agent received.

Evaluation probes are separate. The harness submits them to PowerContext after task execution to test the resulting
Memory. They are not agent hints and do not enter the task environment during execution.

## Memory acceptance

Memory acceptance checks observable evidence from the completed collection path:

- the expected Harbor task and ACP artifacts were recorded;
- enough eligible agent events were captured;
- the run created Memory and completed any required checkpoints;
- created Memory cites captured sources; and
- the declared recall probes receive usable prepared context.

A deterministic built-in sample may also declare expected Memory content. A long-horizon task normally evaluates
coverage, grounding, and recall instead of requiring a fixed answer.

The Harbor reward, verifier result, duration, and model usage remain labels, scores, or metrics. They become blocking
only if a workload explicitly defines them as an external budget. In particular, a task may fail its native grader and
still pass Memory acceptance.

# Reference-level explanation

## Workload and dependency contracts

The manifest is the only harness-level task abstraction. Dataset adapters resolve Harbor tasks but do not define a
second workload schema. Agent configuration and PowerContext evaluation settings belong to the same validated
manifest, while secrets and machine-local paths remain in validated runtime settings.

Dependencies flow in one direction:

```text
manifest
  -> Harbor task and Job
  -> ACP agent execution
  -> Bub and PowerContext capture
  -> replay evidence
  -> Memory evaluation
  -> report rendering
```

The evaluator consumes replay evidence and has no control over Harbor or Bub. Report rendering consumes the evaluation
result and does not recalculate acceptance. No generic evaluation framework is required to own this lifecycle. A future
integration may consume the stable evaluation result, but it must remain downstream of the workload runner.

## Evidence contract

Each workload produces one artifact directory containing:

| Artifact | Purpose |
| --- | --- |
| `replay.json` | Validated workload, resolved run identity, captured events, Memory snapshots, probes, and native evidence |
| `eval-report.json` | Assertions, scores, labels, metrics, and reasons |
| `report.md` | A compact human-readable projection of the same evaluation result |

The replay is sufficient for offline rescoring. It records the dataset checksum, model identity, database, instruction
evidence, and PowerContext scope state needed to interpret the result. Secrets are removed before artifacts are written.

# Drawbacks

Harbor becomes a required dependency for agent workload execution. Long-horizon workloads also take more time and may
use paid model capacity. Their evidence can contain user-visible task content, so retention and redaction must be
treated as part of the workload contract.

# Rationale and alternatives

A separate direct Bub runner would duplicate lifecycle and evidence behavior. Keeping Harbor as the only runner lets
local samples and registry tasks exercise the same path.

Using the source-native reward as the acceptance result would answer whether the agent solved the task, not whether
PowerContext collected useful Memory. The native result is preserved without replacing the Memory evaluator.

Copying registry instructions into manifests would make workloads easier to read in isolation but would allow the copy
to diverge from the pinned task. Recording the resolved instruction in replay evidence provides inspectability without
creating another authoritative input.

# Non-goals

This RFC does not define a benchmark leaderboard, a general dataset registry, a new agent protocol, or an evaluation
platform. It does not replace source-native graders. It also does not require tests for private runner details that a
user cannot observe.

# Acceptance criteria

The proposal is complete when:

- local tasks and registry tasks use the same manifest, Harbor entrypoint, ACP agent, evaluator, and artifact schemas;
- one command selects workloads by one or more IDs and by category;
- the LoCoMo-derived case remains a pinned built-in sample in the common catalog;
- replay evidence identifies the instructions that the agent received;
- long-horizon acceptance measures captured, grounded, and recallable Memory independently of the native task reward;
  and
- the same replay can be scored online or offline without rerunning the task.
