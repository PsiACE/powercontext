- Proposal Name: `unified_workloads_and_long_horizon_memory_evaluation`
- Start Date: 2026-08-13
- RFC PR: [oceanbase/powercontext#0000](https://github.com/oceanbase/powercontext/pull/0000)
- Related RFCs: [RFC 0081](0081_end_to_end_evaluation_architecture.md)

# Summary

PowerContext represents built-in end-to-end samples and long-horizon benchmark tasks as workloads. Each workload
selects a pinned task, chooses one execution profile, sets a budget, and declares how to evaluate the Memory produced
during the run.

The supported execution profiles are:

| Profile | Purpose |
| --- | --- |
| `basic` | Runs a controlled benchmark flow through public PowerContext interfaces without a general-purpose agent. |
| `bub` | Runs a white-box agent whose model, tools, context injection, capture, and checkpoints can be inspected or replaced. |
| `codex` | Runs the native Codex agent for production-shaped software engineering tasks. |

The profiles share the workload catalog, setup, evidence envelope, Memory evaluator, and report format. They do not
share an agent implementation. A source task reward remains diagnostic and does not decide Memory acceptance.

This RFC defines the common architecture and applies it to the existing Bub workloads. It does not migrate the
current LoCoMo benchmark or the SWE-Pro evaluation branch. The `basic` and `codex` profiles define where those
benchmarks can fit later without creating separate workload or report contracts.

# Motivation

RFC 0081 defines the broader end-to-end evaluation architecture, but leaves two gaps. It does not define how local
samples and long-horizon tasks share one workload contract, and it treats Bub as the initial implementation rather
than one execution option among several.

No single runtime gives useful evidence for every question:

- A controlled LoCoMo run needs stable ingestion, retrieval, answering, and scoring boundaries. A general-purpose
  agent adds variables without improving that measurement.
- Bub is useful when an experiment needs to inspect or replace each part of the agent loop. It is the white-box
  runtime for capture policy, checkpoint cadence, context injection, and failure analysis.
- SWE-Pro and Terminal-Bench need a production-shaped coding agent. Running native Codex avoids attributing Bub
  behavior to Codex.

Long-horizon evaluation also needs a success definition independent of task completion. An unsuccessful task can
still show whether PowerContext captured the investigation, preserved source provenance, created Memory, and recalled
that Memory afterward.

## Scope

The deliverable covered by this RFC is the shared workload architecture and the Bub-backed built-in workload catalog.
It includes the common manifest, selection by ID or category, Harbor execution for Bub workloads, normalized evidence,
Memory acceptance, and report rendering.

The following work is intentionally deferred:

- moving the current LoCoMo benchmark runner to the `basic` profile;
- moving any SWE-Pro evaluation branch or result set to the `codex` profile;
- changing the inputs, scoring contract, published results, or operational tooling of either benchmark;
- claiming parity between an existing benchmark and a future profile implementation.

The sections for `basic` and `codex` specify architectural boundaries. They are not migration plans or implementation
commitments in this RFC.

# Guide-level explanation

## Workload manifest

The workload manifest is the catalog entry and execution contract. Pydantic validates the manifest and runtime
settings before execution. `execution.type` is the discriminator for a closed union of profile-specific settings.

A Codex workload can be declared as follows:

```yaml
schema: powercontext.e2e-task/v1
id: swe-pro-example-codex
categories:
  - long-horizon
  - swe-pro
dataset:
  path: e2e/benchmarks/swe-pro/tasks
  task_id: scaleai/swe-bench-pro__example
  checksum: <task-checksum>
execution:
  type: codex
  model_source: codex-oauth
  model: gpt-5.6-sol
  reasoning_effort: medium
  timeout_seconds: 7200
evaluation:
  capture_events: true
  probes:
    - id: investigation
      query: What did the agent learn while investigating the failure?
```

A Bub workload can expose a larger step budget and capture cadence:

```yaml
execution:
  type: bub
  model_source: codex-oauth
  model: gpt-5.6-sol
  bub_version: 0.4.2
  acp_server_version: 0.0.2
  max_steps: 200
  max_tokens: 16384
  checkpoint_every_events: 5
```

A basic workload needs no agent configuration:

```yaml
execution:
  type: basic
```

`dataset` may identify a repository-maintained task or a versioned registry task. Repository-maintained adapters can
pin upstream data and verifier revisions without publishing to a registry. All generated tasks use the same standard
layout. Registry publication is a distribution option, not an execution dependency.

A workload may declare public PowerContext state that must exist before execution. For example, an Experience recall
workload can declare approved Experiences in `setup`. The harness creates this state through the public Client,
flushes pending setup Sources, and records a pre-execution Memory baseline. Setup never selects another profile.

Workloads have stable IDs. One command can run one ID, several IDs, or every workload in a category. Categories are
selection metadata. A workload ID identifies one complete execution contract, including its profile. Two workloads
may use the same source task with different profiles, but their results remain distinct.

## Execution profiles

### Basic

The `basic` profile runs a bounded benchmark driver through public PowerContext interfaces. It has no general-purpose
agent and does not emulate one. The driver owns benchmark operations such as ordered session ingestion, retrieval,
answer generation, and source-native scoring.

A future migration of complete LoCoMo evaluation would use this profile because the driver can keep transcript
ingestion, gold data, retrieval input, and answer scoring at explicit boundaries. This RFC does not perform that
migration. The current benchmark runner and its result contract remain unchanged. A built-in LoCoMo sample remains a
sample and does not claim a LoCoMo benchmark result.

### Bub

The `bub` profile is the white-box agent runtime. Harbor owns the task environment and ACP lifecycle. Bub runs through
its supported installation path and uses the PowerContext integration while it works.

This profile records the evidence needed to inspect model requests, tool results, prepared context, captured events,
and checkpoints. Tests and experiments may replace or configure these parts without changing the common workload or
report contracts. Built-in agent samples and Memory-policy experiments should normally use this profile.

### Codex

The `codex` profile uses Harbor's native Codex agent and the existing PowerContext Codex integration. It uses the
operator's configured Codex OAuth source without routing Codex through Bub or defining another agent.

A future SWE-Pro or Terminal-Bench migration would normally use this profile. A repository-maintained Harbor adapter
would keep the source task, environment, and verifier semantics while the harness adds Memory collection around the
native task. This RFC does not migrate the current SWE-Pro evaluation branch or its results.

## Shared execution flow

The fixed harness performs the common work before and after profile execution:

```text
manifest
  -> validated setup and isolated PowerContext scope
  -> basic | bub | codex executor
  -> normalized replay evidence
  -> Memory evaluation
  -> report rendering
```

The harness records initial Memory, applies declared setup, flushes setup Sources, and records the pre-execution
baseline. It then invokes the selected profile. Bub and Codex tasks enter through Harbor `Job.run`; the basic profile
uses the benchmark driver without creating an agent session.

After execution, the harness records final Memory and runs the declared recall probes. A failed workload still writes
the evidence collected before the failure.

## Input and instruction boundary

The pinned task or benchmark adapter owns execution input. Harbor tasks own agent-visible instructions for Bub and
Codex. A basic adapter owns its ordered input records and questions. The workload manifest references these inputs but
does not copy them.

Replay evidence records resolved input identities and, where safe, their content. Evaluation probes remain separate
from execution input. They run after the task and cannot provide hints to an agent or benchmark answerer.

## Memory acceptance

Memory acceptance uses evidence that is observable across implementations:

- the resolved task and execution profile match the manifest;
- required native evidence for the selected profile was recorded;
- the run captured eligible events or declared input records;
- the run created Memory and completed required checkpoints or flushes;
- created Memory cites declared setup Sources or Sources captured during execution;
- declared recall probes receive usable prepared context.

A deterministic workload may require expected Memory content. A long-horizon task normally measures coverage,
grounding, and recall instead of requiring a fixed final answer.

Native task rewards, verifier results, duration, and model usage remain labels, scores, or metrics. A task may fail its
native grader and still pass Memory acceptance.

# Reference-level explanation

## Workload and executor contracts

The manifest is the only harness-level workload abstraction. `execution` is a Pydantic discriminated union. Each
profile accepts only its own settings, so Bub versions and step budgets cannot leak into Codex or basic workloads.
Secrets and machine-local paths remain in validated runtime settings.

Dataset adapters produce the standard task layout and pin upstream provenance. They do not run workloads, choose a
profile, evaluate Memory, or render reports.

Dependencies flow in one direction:

```text
manifest and task provenance
  -> profile executor
  -> replay evidence
  -> Memory evaluation
  -> report rendering
```

The evaluator reads replay evidence and cannot control an executor. Report rendering reads the evaluation result and
does not recalculate acceptance.

## Evidence contract

Every workload writes one artifact directory:

| Artifact | Purpose |
| --- | --- |
| `replay.json` | Workload identity, profile, task provenance, setup, runtime observations, Memory snapshots, probes, and native evidence references. |
| `eval-report.json` | Assertions, scores, labels, metrics, and reasons. |
| `report.md` | A human-readable projection of the evaluation result. |

The common replay envelope supports offline rescoring. Profile-specific evidence remains typed within that envelope:

| Profile | Native evidence |
| --- | --- |
| `basic` | Input progress, ingestion and flush results, retrieval observations, answers, and source-native scores. |
| `bub` | ACP summaries, captured events, checkpoints, tool observations, and trajectory artifacts. |
| `codex` | Codex trajectory, plugin capture observations, Harbor task result, and verifier artifacts. |

The replay records dataset checksums, model identity, database identity, resolved inputs, and PowerContext scope state.
Final artifact sinks remove configured secrets. Native task artifacts may contain task content and require review
before publication.

## Architectural benchmark placement

- Complete LoCoMo would use `basic` if it is migrated to this architecture.
- LoCoMo-derived built-in samples may use `basic` or `bub`, but do not claim a LoCoMo benchmark result.
- SWE-Pro and Terminal-Bench would use `codex` for production-shaped runs if they are migrated.
- A Bub variant of a coding task may be added for white-box analysis. It has a separate workload ID and report.

Results from different profiles are not merged into one benchmark score. A comparison must pin the same task,
PowerContext revision, model identity, budget, and acceptance policy, and must identify the profile as a treatment
variable.

## Compatibility

Existing Bub workloads become `execution.type: bub` without changing their source task IDs. The current LoCoMo
benchmark and SWE-Pro evaluation branch remain outside the workload catalog and keep their existing commands,
artifacts, and result contracts. A later migration requires separate scope and validation against the relevant
benchmark contract.

The catalog and command surface remain shared. Users select workload IDs or categories rather than separate Make
targets for LoCoMo, Bub, Codex, or a dataset family.

# Drawbacks

The architecture has three executor contracts rather than one. As profiles are implemented, the common replay schema
must distinguish profile-specific evidence without reducing it to untyped dictionaries. Bub and Codex workloads
require Harbor and an agent model, while basic workloads may not. Long-horizon runs can consume paid model capacity
and produce large artifacts.

# Rationale and alternatives

Using Bub for every workload would make the runtime uniform, but it would add agent behavior to controlled benchmarks
and would not measure native Codex behavior. Removing Bub would also be a mistake because Codex does not expose the
same white-box controls. The three profiles preserve both controlled measurement and realistic agent execution.

Separate harnesses for each profile would duplicate selection, setup, evidence, evaluation, and reporting. The shared
manifest and artifact contracts keep those concerns in one place while allowing execution semantics to differ.

Using a source-native reward as Memory acceptance would answer whether the task was solved, not whether PowerContext
collected useful Memory. The native result remains available without replacing the Memory evaluator.

# Non-goals

This RFC does not replace RFC 0081, define a leaderboard, require registry publication, or introduce a new agent
protocol. It does not standardize private executor internals or replace source-native graders. It does not migrate,
rewrite, or retire the current LoCoMo benchmark or SWE-Pro evaluation branch. Implementing `basic` or `codex` for
those benchmarks requires separately reviewed work.

# Acceptance criteria

The proposal is complete when:

- the RFC defines the closed `basic`, `bub`, and `codex` execution architecture and their evidence boundaries;
- existing built-in workloads use the Pydantic manifest with `execution.type: bub`;
- one command selects one or more implemented workload IDs and categories;
- repository-maintained and registry-backed Bub tasks use the same provenance and task layout contracts;
- Bub remains the configurable white-box runtime and records its native evidence through Harbor;
- the LoCoMo-derived built-in case remains a sample and does not claim a LoCoMo benchmark result;
- long-horizon Memory acceptance remains independent of native task reward;
- each implemented workload replay identifies its profile and supports offline rescoring without rerunning the task;
- the current LoCoMo benchmark and SWE-Pro evaluation branch remain unchanged.
