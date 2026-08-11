---
title: 接口
description: 在 Codex 插件、CLI、Python SDK、HTTP 和 MCP 之间选择。
---

# 接口

PowerContext 可以在本地进程中运行，也可以由 Server 提供服务。请根据代码运行位置和持久化数据的归属，选择
满足需求的最小接口。

| 接口 | 适用场景 | 安装或启用方式 |
| --- | --- | --- |
| Codex 插件 | 在 Codex 中跨会话恢复和显式维护 Memory | `powercontext setup codex` |
| CLI | 配置、诊断、Server 控制、能力检查和 Candidate 审核 | `powercontext[cli,server]` |
| Python Client SDK | 对运行中的 Server 发起类型化异步调用 | `powercontext[client]` |
| Core SDK | 进程内 Source、Artifact、Trigger 和组合契约 | 基础包 |
| HTTP | 从任意语言集成服务 | `powercontext[server]` |
| MCP | 面向 Agent 的 Memory 与 Candidate Review 工具 | 由 Server 启用 |

所有远程接口都操作同一个 Server 和同一份持久化 Artifact 存储。Core SDK 不同，应用需要自行管理组合与资源
生命周期。

## Codex 插件

project-context skill 指导 Codex 何时检索、记忆、修订或停用 Memory。Prompt Hook 会恢复相关条目，并把用户
输入采集为 Source 证据；MCP 工具执行显式操作。插件不会启动或内嵌 Server。

[配置 Codex](../how-to/configure-codex.md)说明了安装、scope 选择、提示词采集和鉴权配置。

## CLI

CLI 用于安装、诊断、Server 控制和人工审核。[CLI 参考](cli.md)列出了主要命令组，以及对应的证据和并发规则。

## Python SDK

由 Server 管理持久化时，使用 [Python Client SDK](python-client.md)。由应用管理组合根时，使用 Core SDK。
[Core 协议指南](../../development/core-protocol.md)说明了 Core 契约和 Builtin Runtime 边界。基础
`powercontext` package 不会替应用选择存储、调度、传输或推理。需要在同一进程使用随附的 SQLite 或
OceanBase 实现时，安装 `builtin`。生成的 [Python API 参考](../../modules.md)列出公开模块和类型。

## HTTP 和 MCP

HTTP 提供完整的应用契约。MCP 只提供一部分面向 Agent 的操作。端点和可用性边界见
[HTTP 和 MCP 参考](http-and-mcp.md)。

## Artifact 生成与审核

Experience 生成、managed Skill 和外部 Skill 导入都要求显式证据与人工审核。[Artifact 生命周期](../explanation/artifact-lifecycle.md)
说明模型可以提出什么、批准会创建什么，以及哪些内容不会进入召回或执行流程。
