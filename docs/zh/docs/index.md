---
title: PowerContext 文档
description: 了解 PowerContext、完成常见任务并查询接口行为。
---

# PowerContext 文档

PowerContext 为 Agent 保存项目级上下文。它以本地或远程 Server 的形式运行，并通过 Codex、Python、HTTP 和
MCP 提供同一份持久化 Memory。

## 快速开始

如果你要为自己安装 PowerContext，请从 [Codex 快速入门](tutorials/codex-quickstart.md)开始。该教程从 Git
安装讲起，最后使用第二个 Codex 会话恢复第一个会话的工作。

## 操作指南

- [安装和运行](how-to/install-and-run.md)：从 Git 安装、启动 Server 和更新版本。
- [配置 Codex](how-to/configure-codex.md)：安装插件，并控制项目 scope 和提示词采集。
- [排查问题](how-to/troubleshoot.md)：诊断凭据、插件、Server、数据库和 Hook。

## 概念

- [Artifact 生命周期](explanation/artifact-lifecycle.md)：理解生成、审核、批准、召回和导出。
- [Core 协议与组合](../development/core-protocol.md)：理解 Source、Artifact、Trigger 和职责边界。

## 参考

- [接口](reference/interfaces.md)：在 Codex、CLI、Client SDK、Core SDK、HTTP 和 MCP 之间选择。
- [CLI](reference/cli.md)：查询命令组及其运行规则。
- [Python Client SDK](reference/python-client.md)：从 Python 调用运行中的 Server。
- [HTTP 和 MCP](reference/http-and-mcp.md)：查询端点和传输层可用范围。
- [配置](reference/configuration.md)：查询默认值和环境变量。
- [Python API](../modules.md)：浏览公开模块和类型的生成参考。

实现相关内容见[开发指南](../development/index.md)。设计历史和提案见 [RFC 索引](../rfcs/README.md)。RFC 可能
描述尚未实现的行为。
