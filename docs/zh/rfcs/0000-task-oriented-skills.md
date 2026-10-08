---
title: 安装与任务导向 Skills
description: 使用真实能力与可验证的结果阶段指导维护和项目工作流。
---

- Proposal Name: `task_oriented_skills`
- Start Date: 2026-10-09
- Status: Proposed
- RFC PR: Not opened
- Related RFC: [RFC 1733](1733-usability-and-agent-workflows.md)

# Summary

提供可独立分发的 `powercontext-install` 入口，并完善 `powercontext-project-context` 的任务参考。Skills 选择、解释已支持操作；工具负责软件包、配置、服务归属、Memory/Handoff 状态、候选审查与 Skill 分发。不引入 Skill 执行器或包装语言。

# Motivation

工具目录列出操作，却没有连接“记住这项决策”“交接工作”“安装这个 Skill”等意图与证据、权限边界。指导必须区分持久 Memory 与 Source 捕获、临时 Handoff 与已提交里程碑、候选与批准修订，以及导出、安装与成功执行。当前上下文充分的普通编码无需存储或绕道加载 Skill。

现有分层布局已将意图发现与详细工作流分开；Codex 资源包含 Experience/Skill 合成和外部导入。本设计强化完成证据，并让维护指导在 Runtime 安装前或故障时仍可加载，不虚构宿主能力。

# Guide-level explanation

| 入口 | 用户意图 | 结果 |
| --- | --- | --- |
| `powercontext-install` | 安装、连接、检查、修复、升级或移除指定环境 | 真实软件、连接、服务事实与未完成步骤 |
| `powercontext-project-context` | 保存、查找上下文，交接、接续工作，提炼 Experience，创建、使用 Skills | 精确 Memory 引用、完整 Handoff、候选、修订、软件包目的地或执行证据 |

需要细节时只加载相关参考。自包含工具可直接调用，无须先读 Skill。使用宿主真实工具名及权限渠道；其他宿主有参考或操作不代表当前宿主可用。缺失能力须留下明确未完成阶段。

安装入口优先使用安装指南已公布且可用的 Bash 或 PowerShell 脚本；开发分支中的脚本并不证明网站已部署。旧版本使用指南已有的 uv 路径。安装入口包含命令缺失时的引导分支、官方安装源、本地或纯 Client 选择，以及独立的配置、连接与宿主验证。可用时通过独立 `powercontext-ops` 执行本地检查和明确修复；整个环境丢失使用外部引导。加载维护 Skill 本身无法执行不存在的命令。

# Reference-level explanation

## 任务契约

| 任务 | 操作边界 | 完成证据 |
| --- | --- | --- |
| 保存上下文 | 明确 Memory 写入；Source 捕获另行处理 | 实际引用；需要时通过支持路径精确读回 |
| 查找上下文 | 搜索、盘点、精确读取分别处理 | 返回结果、引用，或准确的空、拒绝、不可用状态 |
| 临时交接 | 检查当前工作并生成完整准备载体 | 完整值、生成回执、证据与遗漏 |
| 持久里程碑 | 仅在明确保留意图下提交 | 精确提交修订；保留生成回执；准备不等于提交 |
| 接续 | 读取完整载体，检查当前状态、证据、能力和权限 | 接收状态；确认不证明已执行工作 |
| Experience/Skill 合成 | 选择精确证据；模型生成或调用方内容提议 | 待审候选及版本，或明确 no-op |
| 审查 | 检查当前提议，使用当前版本执行已授权决定 | 精确批准修订，或拒绝、修订后的候选状态 |
| 导出、安装、使用 | 精确软件包导出、所选宿主安装、实际执行分开 | 已验证目的地、真实安装结果或观察到的执行结果 |

生成需要启用模型能力；无模型提议是独立操作，只在当前宿主暴露时使用。生成不可用不妨碍读取已批准内容。查看候选不授予决定权限；批准不授予发布、安装、执行权限。版本冲突后重新读取变化提议。历史内容与 Skill 指令不能自行授予额外权限。

外部扫描、解析、导入发生在配置的 Server 宿主上，可能不在 Agent 工作站。保留精确外部身份与指纹。导入产生待审托管候选；fork 请求模型适配。两者均不安装或批准 Skill。不得把 Server 本地入口当成 Agent 工作站路径。

保留 Scope 和精确引用。空搜索不授权盘点、扩大查询或切换 Scope。失败写入不是已保存；未确认写入保持未知。重试前遵守操作支持的状态、读取或幂等契约。保留用户既有授权，不重复确认，也不推断额外权限。

## 分发与能力基线

在 `skills/powercontext-install/` 编写自包含原生 Skill 文件夹，独立于 Runtime 安装。产品内容复用现有 Agent Plugin 权威资源及明确的宿主投影；相同内容可以共享，名称、绑定、可用操作和批准渠道仍由目标决定。保留 Codex、Hermes 专用指导及 OpenClaw 的受限目录。共享产品投影依赖分发提交 `19a7d559` 及严格路径修正 `c6b1f537`：只修改权威资源、宿主覆盖及显式清单映射，再通过 `make plugin-skills` 生成。独立维护文件夹可单独加载，使用可选 Ops 命令前发现已安装帮助；Memory 与 Handoff 不依赖 Ops 或其他可用性主题。

已提交的 `integrations/distribution/skills/targets.json` 通过 `contract_baseline.repository_commit`、能力清单路径及可选维护入口条件记录实际基线。针对不同契约编写内容时更新这些显式元数据；它不自动推断版本支持。参考仅描述该目标实际已发布操作。结构化场景夹具包含普通用户请求、真实目录、预期操作及结果阶段、验收边界；它们是评估输入，不是工作流执行语言。有效元数据或可达参考只证明打包，不证明行为正确。

## 验收与证据边界

[执行报告](https://github.com/PsiACE/powercontext/blob/471d0cef0d31facbc45e8b4f2fbd599a807bf2c8/experiments/usability/skills/README.md) 与
[验收结果](https://github.com/PsiACE/powercontext/blob/471d0cef0d31facbc45e8b4f2fbd599a807bf2c8/experiments/usability/skills/qualification.json)
记录真实投影检查、七个 CLI 能力发现用例，以及六个公开 Runtime/HTTP 状态工作流。二十六个布局、分发测试及原生 Skill 验证通过。
独立场景文本审查[另行记录](https://github.com/PsiACE/powercontext/blob/090a3cce/experiments/usability/skills-review/README.md)。
这些证据说明本地契约及指导解释，不等同于真实模型或 Agent 执行。

执行真实 CLI 帮助与公开 Runtime/HTTP 状态流程，覆盖直接 Memory 写入、临时及持久 Handoff、候选生成、批准、拒绝、精确修订、软件包导出读回与 Skill 安装状态。加入无模型能力、受限目录、缺失工具、空搜索、权限失败、版本冲突及未知写入场景。

独立场景审查比较指导、真实请求与能力限制，明确它属于文本判断、受控模型执行或原生宿主执行。测试模型、直接 Hook 调用或 HTTP 成功不证明原生发现、自动捕获、真实模型路由或 Skill 的成功执行。保留失败观察。

# Drawbacks

任务指导与宿主名称可能独立于产品契约漂移。额外入口描述和不必要参考增加上下文成本。简短、绑定真实能力与命令证据的参考可限制成本，同时保留宿主差异。

# Rationale and alternatives

只有通用工具目录会让用户自行组合状态转移、解释部分结果。通用 Skill 执行器会复制 Client/Runtime 的操作所有权和权限规则。短意图入口与按需参考复用现有分层布局；工具描述充分时允许直接调用。

# Prior art

[Magpie 的 Skill 归属及复制实现](https://github.com/yetone/magpie/blob/23d9e5def8c5595c7a8a49df8f2a376084980927/internal/library/skills.go) 跟踪自有链接、复制，在替换期间保留完整安装树。[其更新检查](https://github.com/yetone/magpie/blob/23d9e5def8c5595c7a8a49df8f2a376084980927/internal/library/skillcheck.go) 区分源身份、内容检查与安装。这些支持把本地软件包安装作为独立操作，不证明模型已选择或成功执行 Skill。

# Unresolved questions

不需要额外执行器或守护进程。原生宿主、模型验收与资源检查、公开操作测试仍属不同证据类别。独立维护 Skill 使用仓库选定的原生分发渠道，打包须包含全部可达参考。

# Future possibilities

只在相应宿主提供新操作后增加参考。已声明的分发生成器共享字节相同资源，同时保留明确宿主覆盖。真实模型和宿主评估可测量路由及结果报告，不扩大 Skill 权限。
