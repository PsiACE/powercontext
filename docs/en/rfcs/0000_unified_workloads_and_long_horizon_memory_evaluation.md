- Proposal Name: `unified_workloads_and_long_horizon_memory_evaluation`
- Start Date: 2026-08-13
- RFC PR: [oceanbase/powercontext#0000](https://github.com/oceanbase/powercontext/pull/0000)
- Related RFCs: [RFC 0081](0081_end_to_end_evaluation_architecture.md)

# Summary

PowerContext will represent every sampled end-to-end scenario and long-horizon agent task as a workload. A workload
selects a Harbor task, sets its execution budget, and declares how the resulting Memory is evaluated. Local scenarios,
pinned samples, and registry datasets use the same manifest, runner, evidence, and report contracts.

All workloads follow one execution path:

```text
workload -> public PowerContext setup -> Harbor Job -> ACP -> Bub -> PowerContext -> evidence -> Memory evaluator -> report
```

The acceptance result answers whether the run captured useful, grounded, and recallable Memory. A source-native task
reward remains diagnostic and does not decide Memory acceptance.

# Motivation

RFC 0081 defines the broader end-to-end evaluation architecture, but it does not settle how local samples and
registry-backed long-horizon tasks share one workload contract. Without that contract, each task source can introduce
its own runner, command, or report shape.

Long-horizon evaluation also needs a distinct success definition. The run remains useful when the agent does not solve
the source task. It can still show whether PowerContext captured the investigation, preserved source provenance,
created Memory during the task, and recalled that Memory afterward. Task completion and Memory quality are related
observations, not the same outcome.

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

A workload may declare public PowerContext state that must exist before Harbor starts. For example, an Experience
recall workload declares approved Experiences in `setup`. The common runner creates this state through the public
Client in the workload scope and records the resulting Source and Artifact references. It flushes pending setup
Sources before recording the pre-execution Memory baseline. Setup does not select another agent or runner.

Workloads have stable IDs. One command can run one ID, several IDs, or every workload in a category. Categories are
selection metadata and do not select a different runner.

LoCoMo input in this catalog is a pinned built-in sample. It checks the workload flow against fixed conversation data
but does not claim a LoCoMo benchmark result.

## One execution flow

The harness creates an isolated PowerContext scope and records its initial Memory. It applies declared setup through
the public Client, flushes setup Sources, and records a pre-execution Memory baseline. Harbor resolves the dataset,
creates the task environment, and runs the task through its ACP agent support. Bub receives the task instructions and
uses the PowerContext integration while it works. The integration captures eligible events and advances Memory
checkpoints.

After Harbor finishes, the harness records native ACP evidence, final Memory, and the result of each recall probe. The
same evaluator produces machine-readable and reviewer-readable reports. A failed workload still writes the evidence
collected before the failure.

There is no direct Bub runner beside Harbor. Local and registry tasks both enter through `Job.run`.

## Instruction boundary

The Harbor task owns agent-visible instructions. A local task stores them in its task directory, while a registry task
obtains them from the pinned dataset entry. The workload manifest does not copy those instructions because doing so
would create two sources of truth.

Replay evidence records the resolved instruction identity and content, subject to redaction. A reviewer can therefore
confirm what the agent received without making the manifest authoritative for task content.

Evaluation probes are separate. The harness submits them to PowerContext after task execution to test the resulting
Memory. They are not agent hints and do not enter the task environment during execution.

## Memory acceptance

Memory acceptance checks observable evidence from the collection path:

- the expected Harbor task, instructions, and ACP artifacts were recorded;
- enough eligible agent events were captured;
- the run created Memory and completed any required checkpoints;
- created Memory cites declared setup Sources or Sources captured from the agent trajectory; and
- the declared recall probes receive usable prepared context.

A deterministic built-in sample may also declare expected Memory content. A long-horizon task normally evaluates
coverage, grounding, and recall instead of requiring a fixed answer.

The Harbor reward, verifier result, duration, and model usage remain labels, scores, or metrics. A task may fail its
native grader and still pass Memory acceptance.

# Reference-level explanation

## Workload and dependency contracts

The manifest is the only harness-level task abstraction. Dataset adapters resolve Harbor tasks but do not define a
second workload schema. Agent configuration and PowerContext evaluation settings belong to the validated manifest.
Secrets and machine-local paths remain in validated runtime settings.

Agent-driven approved Experience recall is a live workload in the same catalog. Deterministic Experience and Skill
lifecycle behavior remains in `tests/e2e/`, where it is exercised through public product interfaces without a model.

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
result and does not recalculate acceptance. A future evaluation integration may consume this result, but it remains
downstream of the workload runner.

## Evidence contract

Each workload produces one artifact directory:

| Artifact | Purpose |
| --- | --- |
| `replay.json` | Workload, run identity, setup results, resolved instructions, captured events, initial and pre-execution Memory snapshots, probes, and native evidence |
| `eval-report.json` | Assertions, scores, labels, metrics, and reasons |
| `report.md` | A compact human-readable projection of the same evaluation result |

The replay is sufficient for offline rescoring. It records the dataset checksum, model identity, database, instruction
evidence, and PowerContext scope state needed to interpret the result. Secrets are removed before artifacts are written.

## Compatibility

Existing local samples keep their Harbor task layout and task IDs. Registry tasks retain their source-native task and
verifier. LoCoMo remains a pinned sample rather than a benchmark claim. These workloads differ only in dataset source
and declared acceptance thresholds.

# Drawbacks

Harbor becomes a required dependency for agent workload execution. Long-horizon workloads take more time and may use
paid model capacity. Their evidence can contain user-visible task content, so retention and redaction are part of the
workload contract.

# Rationale and alternatives

A separate direct Bub runner would duplicate lifecycle and evidence behavior. Keeping Harbor as the only runner lets
local samples and registry tasks exercise the same path.

Using the source-native reward as acceptance would answer whether the agent solved the task, not whether PowerContext
collected useful Memory. The native result is preserved without replacing the Memory evaluator.

Copying registry instructions into manifests would make workloads easier to read in isolation but would allow the copy
to diverge from the pinned task. Recording the resolved instruction in replay evidence provides inspectability without
creating another authoritative input.

# Non-goals

This RFC does not replace RFC 0081, define a benchmark leaderboard, introduce a general dataset registry, or create a
new agent protocol. It does not replace source-native graders or require tests for private runner details.

# Acceptance criteria

The proposal is complete when:

- local tasks and registry tasks use the same manifest, Harbor entrypoint, ACP agent, evaluator, and artifact schemas;
- agent-driven approved Experience recall uses the common workload catalog and has no direct Codex runner;
- one command selects workloads by one or more IDs and by category;
- the LoCoMo-derived case remains a pinned built-in sample in the common catalog;
- replay evidence identifies the instructions that the agent received;
- long-horizon acceptance measures captured, grounded, and recallable Memory independently of the native task reward;
  and
- the same replay can be scored online or offline without rerunning the task.
