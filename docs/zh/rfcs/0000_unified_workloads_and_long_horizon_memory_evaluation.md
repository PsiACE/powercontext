- Proposal Name: `unified_workloads_and_long_horizon_memory_evaluation`
- Start Date: 2026-08-13
- RFC PR: [oceanbase/powercontext#0000](https://github.com/oceanbase/powercontext/pull/0000)
- Related RFCs: [RFC 0081](0081_end_to_end_evaluation_architecture.md)

# 摘要

PowerContext 将内置端到端样例和长程 benchmark task 表示为 workload。每个 workload 选择固定的 task、指定一种
execution profile、设置预算，并声明如何评估本次运行产生的 Memory。

支持三种 execution profile：

| Profile | 用途 |
| --- | --- |
| `basic` | 不使用通用 agent，通过 PowerContext 公开接口运行受控 benchmark 流程。 |
| `bub` | 运行白盒 agent，可以检查或替换 model、tool、context injection、capture 与 checkpoint。 |
| `codex` | 使用原生 Codex agent 运行接近实际使用方式的软件工程任务。 |

三种 profile 共用 workload catalog、setup、evidence envelope、Memory evaluator 与 report 格式，但不共用 agent 实现。
任务原生 reward 只用于诊断，不决定 Memory acceptance。

# 动机

RFC 0081 定义了更广泛的端到端评估架构，但仍有两个缺口。它没有确定本地样例与长程任务如何共享一套 workload
contract，也把 Bub 当作首个实现，而不是多种执行方式中的一种。

没有一种 runtime 能为所有评估问题提供合适的 evidence：

- 受控的 LoCoMo 运行需要稳定的 ingestion、retrieval、answering 与 scoring 边界。引入通用 agent 只会增加变量。
- 当实验需要检查或替换 agent loop 的每个部分时，Bub 很合适。它是 capture policy、checkpoint cadence、context
  injection 与故障分析的白盒 runtime。
- SWE-Pro 与 Terminal-Bench 需要接近实际使用方式的 coding agent。直接运行原生 Codex，可以避免把 Bub 的行为归因给
  Codex。

长程评估还需要与任务完成情况分开的成功定义。即使任务没有完成，一次运行仍然可以说明 PowerContext 是否采集了调查
过程、保留了 source provenance、创建了 Memory，以及这些 Memory 能否在任务结束后被召回。

# 使用方式

## Workload manifest

Workload manifest 同时是 catalog entry 与执行契约。Manifest 与运行时配置都在执行前经过 Pydantic 校验。
`execution.type` 是封闭的 profile-specific settings 联合的判别字段。

Codex workload 可以这样声明：

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

Bub workload 可以声明更大的 step budget 和 capture cadence：

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

Basic workload 不需要 agent 配置：

```yaml
execution:
  type: basic
```

`dataset` 可以指向仓库自行维护的 task，也可以指向带版本的 registry task。仓库内的 adapter 可以固定上游数据与
verifier revision，无需发布到 registry。所有生成的 task 使用相同的标准布局。发布到 registry 是一种分发方式，不是
执行依赖。

Workload 可以声明 execution 前必须存在的公开 PowerContext 状态。例如，Experience recall workload 可以在 `setup` 中
声明 approved Experience。Harness 通过公开 Client 创建这些状态，flush 待处理的 setup Source，并记录 execution 前的
Memory baseline。Setup 不会选择另一种 profile。

每个 workload 都有稳定 ID。一个命令可以运行单个 ID、多个 ID，或一个 category 下的全部 workload。Category 只是选择
条件。Workload ID 标识包含 profile 在内的完整执行契约。两个 workload 可以使用同一个 source task 和不同 profile，但
它们的结果必须保持独立。

## Execution profile

### Basic

`basic` profile 通过 PowerContext 公开接口运行有界的 benchmark driver。它不使用通用 agent，也不模拟 agent。Driver
负责 ordered session ingestion、retrieval、answer generation 与 source-native scoring 等 benchmark operation。

完整 LoCoMo 评估属于这一类，因为 driver 可以明确隔离 transcript ingestion、gold data、retrieval input 与 answer
scoring。内置的 LoCoMo sample 仍然只是 sample，除非它的 manifest 选择完整且固定的 benchmark contract。

### Bub

`bub` profile 是白盒 agent runtime。Harbor 管理 task environment 与 ACP lifecycle，Bub 通过受支持的安装方式运行，并在
工作过程中使用 PowerContext integration。

该 profile 记录检查 model request、tool result、prepared context、captured event 与 checkpoint 所需的 evidence。测试和
实验可以替换或配置这些部分，而不改变公共 workload 与 report contract。内置 agent sample 和 Memory policy experiment
通常应使用该 profile。

### Codex

`codex` profile 使用 Harbor 原生 Codex agent 与现有 PowerContext Codex integration。它使用 operator 配置的 Codex OAuth
source，不经过 Bub，也不定义另一个 agent。

SWE-Pro 与 Terminal-Bench 通常应使用该 profile。仓库自行维护的 Harbor adapter 保留 source task、environment 与
verifier 语义。Harness 在原生任务外围增加 Memory collection 与 evaluation，不替换原生 grader。

## 共享执行流程

固定 harness 在 profile execution 前后完成公共工作：

```text
manifest
  -> validated setup and isolated PowerContext scope
  -> basic | bub | codex executor
  -> normalized replay evidence
  -> Memory evaluation
  -> report rendering
```

Harness 记录初始 Memory，应用声明的 setup，flush setup Source，并记录 execution 前的 baseline。随后调用选定 profile。
Bub 与 Codex task 从 Harbor `Job.run` 进入；basic profile 使用 benchmark driver，不创建 agent session。

Execution 结束后，harness 记录最终 Memory 并执行声明的 recall probe。Workload 中途失败时，已经采集的 evidence 仍会写入
artifact。

## 输入与指令边界

固定的 task 或 benchmark adapter 拥有 execution input。对于 Bub 和 Codex，Harbor task 拥有 agent 可见的 instruction。
Basic adapter 拥有有序 input record 与 question。Workload manifest 只引用这些输入，不复制内容。

Replay evidence 记录最终解析出的 input identity，并在安全的情况下记录内容。Evaluation probe 与 execution input 相互独立。
Probe 在任务结束后运行，不能向 agent 或 benchmark answerer 提供提示。

## Memory acceptance

Memory acceptance 使用跨实现可观察的 evidence：

- 最终解析出的 task 和 execution profile 与 manifest 一致；
- 记录了所选 profile 要求的原生 evidence；
- 本次运行采集了符合条件的 event 或声明的 input record；
- 本次运行创建了 Memory，并完成要求的 checkpoint 或 flush；
- 新建 Memory 引用了声明的 setup Source 或 execution 期间采集的 Source；
- 声明的 recall probe 能获得可用的 prepared context。

确定性的 workload 可以要求预期 Memory 内容。长程任务通常评估 coverage、grounding 与 recall，不要求固定的最终答案。

任务原生 reward、verifier result、运行时长与 model usage 保留为 label、score 或 metric。任务可以没有通过原生 grader，
同时通过 Memory acceptance。

# 设计

## Workload 与 executor contract

Manifest 是 harness 层唯一的 workload 抽象。`execution` 是 Pydantic discriminated union。每个 profile 只接受自己的
setting，Bub version 与 step budget 不能进入 Codex 或 basic workload。Secret 与本机路径留在经过校验的运行时配置中。

Dataset adapter 生成标准 task layout，并固定 upstream provenance。它不运行 workload，不选择 profile，不评估 Memory，
也不渲染 report。

依赖保持单向：

```text
manifest and task provenance
  -> profile executor
  -> replay evidence
  -> Memory evaluation
  -> report rendering
```

Evaluator 只读取 replay evidence，不能控制 executor。Report renderer 只读取 evaluation result，不重新计算 acceptance。

## Evidence contract

每个 workload 生成一个 artifact 目录：

| Artifact | 用途 |
| --- | --- |
| `replay.json` | Workload identity、profile、task provenance、setup、runtime observation、Memory snapshot、probe 与原生 evidence reference。 |
| `eval-report.json` | Assertion、score、label、metric 与判断理由。 |
| `report.md` | Evaluation result 的可读表示。 |

公共 replay envelope 支持离线重新评分。每种 profile 的原生 evidence 在 envelope 中保持类型信息：

| Profile | 原生 evidence |
| --- | --- |
| `basic` | Input progress、ingestion 与 flush result、retrieval observation、answer 和 source-native score。 |
| `bub` | ACP summary、captured event、checkpoint、tool observation 与 trajectory artifact。 |
| `codex` | Codex trajectory、plugin capture observation、Harbor task result 与 verifier artifact。 |

Replay 记录 dataset checksum、model identity、database identity、最终输入与 PowerContext scope state。最终 artifact sink 会移除
已配置的 secret。原生 task artifact 可能包含任务内容，发布前需要检查。

## Benchmark 归属

- 完整 LoCoMo 使用 `basic` 作为 benchmark profile。
- LoCoMo 衍生的内置 sample 可以使用 `basic` 或 `bub`，但不代表 LoCoMo benchmark 结果。
- SWE-Pro 与 Terminal-Bench 使用 `codex` 运行接近实际使用方式的评估。
- Coding task 可以增加单独的 Bub variant 用于白盒分析，但必须使用独立 workload ID 与 report。

不同 profile 的结果不能合并为一个 benchmark score。对比实验必须固定相同的 task、PowerContext revision、model identity、
budget 与 acceptance policy，并把 profile 明确记录为 treatment variable。

## 兼容性

现有 Bub workload 改为 `execution.type: bub`，source task ID 保持不变。现有 LoCoMo benchmark 逻辑可以移到 `basic`
profile，并保留固定数据与 scoring contract。长程 Codex workload 使用 Harbor 原生 Codex 支持，不经过 Bub ACP adapter。

Catalog 与命令入口保持统一。用户通过 workload ID 或 category 选择任务，不为 LoCoMo、Bub、Codex 或 dataset family 维护
不同 Make target。

# 代价

Harness 需要维护三种 executor，而不是一种。公共 replay schema 必须区分 profile-specific evidence，不能把它们压成无类型
dictionary。Bub 与 Codex workload 需要 Harbor 和 agent model，basic workload 则可能不需要。长程运行可能消耗付费模型
额度，并产生较大的 artifact。

# 理由与替代方案

所有 workload 都使用 Bub 可以统一 runtime，但会把 agent 行为引入受控 benchmark，也无法测量原生 Codex 行为。移除 Bub
同样不合理，因为 Codex 不提供同等的白盒控制能力。三种 profile 分别保留受控测量、白盒分析和真实 agent execution。

为每种 profile 维护独立 harness 会重复 selection、setup、evidence、evaluation 与 reporting。公共 manifest 和 artifact
contract 将这些职责放在一处，同时允许 execution semantics 不同。

直接用任务原生 reward 作为 Memory acceptance，只能回答任务是否完成，不能回答 PowerContext 是否采集到有效 Memory。
原生结果会保留，但不会取代 Memory evaluator。

# 非目标

本 RFC 不替代 RFC 0081，不定义 leaderboard，不要求发布到 registry，也不引入新的 agent protocol。它不统一 executor 私有
实现，也不替代任务原生 grader。

# 验收条件

满足以下条件时，本提案完成：

- 一个 Pydantic workload manifest 支持封闭的 `basic`、`bub` 与 `codex` execution union；
- 一个命令可以跨 profile 按一个或多个 workload ID 及 category 选择任务；
- 仓库自行维护的 task 与 registry task 使用相同的 provenance 和 task layout contract；
- Bub 保持为可配置的白盒 runtime，并记录原生 evidence；
- Codex workload 使用 Harbor 原生 Codex agent 与现有 PowerContext integration；
- basic workload 不使用通用 agent；
- 完整 LoCoMo 与 LoCoMo 衍生 sample 使用正确的 benchmark 或 sample scope 进行报告；
- 长程 Memory acceptance 与任务原生 reward 保持独立；
- 每份 replay 都标识选定 profile，并能在不重新运行任务的情况下离线评分。
