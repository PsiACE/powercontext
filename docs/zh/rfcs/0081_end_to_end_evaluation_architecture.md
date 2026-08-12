- Proposal Name: `unified_workloads_and_long_horizon_memory_evaluation`
- Start Date: 2026-08-07
- RFC PR: [oceanbase/powercontext#81](https://github.com/oceanbase/powercontext/pull/81)

# 摘要

PowerContext 将所有内置端到端样例与长程 agent 任务表示为 workload。Workload 选择一个 Harbor task，规定执行预算，
并声明如何评估本次运行产生的 Memory。本地场景、固定采样和 registry dataset 使用相同的 manifest、runner、evidence
与 report contract。

所有 workload 只经过一条执行链：

```text
workload -> Harbor Job -> ACP -> Bub -> PowerContext -> evidence -> Memory evaluator -> report
```

Acceptance 判断本次运行是否采集到了有用、有来源依据且可以召回的 Memory。任务自己的 reward 只用于诊断，不决定
Memory acceptance。

# 动机

PowerContext 现有本地小型场景、一个固定的 LoCoMo 衍生样例，以及从 Terminal-Bench 等 registry 获取的任务。它们的输入
与原生 verifier 不同，但这些差异不需要多套 runner 或 report。多条链路会增加比较成本，也容易让不同任务类型的行为
逐渐分叉。

即使 agent 没有解决长程任务，这次运行仍然有评估价值。我们可以检查 PowerContext 是否采集了调查过程，是否保留了
source provenance，是否在任务期间创建了 Memory，以及这些 Memory 能否在任务结束后被召回。任务完成情况与 Memory
质量是两个相关但不同的结果。

# 使用方式

## 一种 workload 模型

Workload manifest 同时是 catalog entry 与执行契约：

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

`dataset` 可以指向本地 Harbor task，也可以指向带版本的 registry task。其他字段在两种来源下含义一致。Manifest
与运行时配置都在执行前经过 Pydantic 校验。

每个 workload 都有稳定 ID。一个命令可以运行单个 ID、多个 ID，或一个 category 下的全部 workload。Category 只是选择
条件，不会切换 runner。

Catalog 中的 LoCoMo 数据是固定的内置采样。它用固定 conversation 检查 workload 链路，不代表 LoCoMo benchmark 结果。

## 一条执行链

Harness 创建隔离的 PowerContext scope，并记录初始 Memory。Harbor 解析 dataset、创建任务环境，再通过 ACP agent 支持
运行任务。Bub 接收任务指令，并在工作过程中使用 PowerContext integration。Integration 捕获符合条件的事件，并推进
Memory checkpoint。

Harbor 结束后，harness 记录原生 ACP evidence、最终 Memory 和每个 recall probe 的结果。统一 evaluator 随后生成机器可读
与供评审阅读的 report。Workload 中途失败时，已经采集的 evidence 仍会写入 artifact。

Harbor 之外不保留直接调用 Bub 的 runner。本地任务与 registry task 都从 `Job.run` 进入。

## 指令边界

Agent 可见的指令属于 Harbor task：

- 本地任务把指令放在 Harbor task 目录中，多阶段任务分别保存每一步的 instruction；
- registry task 从固定版本的 dataset entry 获取指令。

Workload manifest 不复制这些指令，否则会产生两个事实来源，并可能在不易察觉的情况下改变被测任务。Evidence 会记录
最终解析出的 instruction identity 与经过脱敏的内容，让评审者可以确认 agent 实际收到了什么。

Evaluation probe 与任务指令相互独立。Harness 在任务结束后向 PowerContext 提交 probe，用它检查本次运行产生的 Memory。
Probe 不是给 agent 的提示，任务运行期间也不会进入任务环境。

## Memory acceptance

Memory acceptance 检查完整采集链产生的可观察 evidence：

- 记录了预期的 Harbor task 与 ACP artifact；
- 足够多的 agent event 被成功采集；
- 本次运行创建了 Memory，并完成要求的 checkpoint；
- 新建 Memory 引用了本次采集的 source；
- 声明的 recall probe 能获得可用的 prepared context。

确定性的内置样例还可以声明预期的 Memory 内容。长程任务通常评估 coverage、grounding 与 recall，不要求固定答案。

Harbor reward、verifier result、运行时长与 model usage 保留为 label、score 或 metric。只有 workload 明确声明外部预算时，
这些数据才会阻断 acceptance。任务可以没有通过原生 verifier，同时通过 Memory acceptance。

# 设计

## Workload 与依赖契约

Manifest 是 harness 层唯一的任务抽象。Dataset adapter 负责解析 Harbor task，但不定义第二套 workload schema。Agent
配置与 PowerContext evaluation settings 属于同一个经过校验的 manifest；secret 与本机路径留在经过校验的运行时配置中。

依赖保持单向：

```text
manifest
  -> Harbor task and Job
  -> ACP agent execution
  -> Bub and PowerContext capture
  -> replay evidence
  -> Memory evaluation
  -> report rendering
```

Evaluator 只读取 replay evidence，不控制 Harbor 或 Bub。Report renderer 只读取 evaluation result，不重新计算 acceptance。
这条链不需要通用 evaluation framework 来接管生命周期。未来的集成可以消费稳定的 evaluation result，但必须位于 workload
runner 的下游。

## Evidence 契约

每个 workload 生成一个 artifact 目录：

| Artifact | 用途 |
| --- | --- |
| `replay.json` | 经过校验的 workload、运行身份、采集事件、Memory snapshot、probe 与原生 evidence |
| `eval-report.json` | Assertion、score、label、metric 与判断理由 |
| `report.md` | 同一个 evaluation result 的简短可读表示 |

`replay.json` 足以支持离线重新评分。它记录 dataset checksum、model identity、database、instruction evidence，以及解释结果
所需的 PowerContext scope state。所有 artifact 在写入前都要移除 secret。

# 代价

Harbor 会成为 agent workload 执行的必要依赖。长程任务也需要更多时间，并可能消耗付费模型额度。Evidence 可能包含
用户可见的任务内容，因此保留期限与脱敏规则属于 workload contract 的一部分。

# 理由与替代方案

保留一条直接调用 Bub 的 runner 会重复实现 lifecycle 与 evidence 行为。只使用 Harbor，可以让本地样例和 registry task
经过相同路径。

直接用任务原生 reward 作为 acceptance，只能回答 agent 是否解决了任务，不能回答 PowerContext 是否采集到有效 Memory。
我们保留原生结果，但不让它取代 Memory evaluator。

把 registry instruction 复制进 manifest 虽然更容易单独阅读，却可能与固定任务发生偏离。将最终指令记录进 replay evidence，
可以在不增加第二个权威输入的前提下保持可检查性。

# 非目标

本 RFC 不定义 benchmark leaderboard、通用 dataset registry、新 agent protocol 或 evaluation platform，也不替代任务原生
grader。对于用户无法观察的 runner 私有实现细节，本 RFC 不要求为其增加测试。

# 验收条件

满足以下条件时，本提案完成：

- 本地任务与 registry task 使用相同的 manifest、Harbor entrypoint、ACP agent、evaluator 和 artifact schema；
- 一个命令可以按一个或多个 ID 以及 category 选择 workload；
- LoCoMo 衍生 case 作为固定内置样例留在统一 catalog 中；
- replay evidence 可以确认 agent 收到的指令；
- 长程任务根据已采集、有来源依据且可以召回的 Memory 进行验收，不依赖任务原生 reward；
- 同一份 replay 无需重新运行任务即可在线或离线评分。
