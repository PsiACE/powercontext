---
title: 开发指南
description: 扩展 PowerContext、组合 Runtime 并验证实现变更。
---

# 开发指南

这些指南面向需要深入 CLI 和 Client 公开接口之下工作的贡献者与应用开发者。仓库配置、测试、生成代码和
pull request 要求见 [CONTRIBUTING.md](https://github.com/oceanbase/powercontext/blob/master/CONTRIBUTING.md)。

## 架构和扩展点

- [Core 协议与组合](core-protocol.md)说明 Source、Artifact、Trigger、组合方式和 package 职责。
- [Builtin Memory layer](memory-layer.md)说明持久化、entry 演进、检索和支持的数据库。
- [Pydantic AI 推理](pydantic-ai-inference.md)说明可选的 generation 与 embedding integration。

## Server 实现

- [远程访问](remote-access-implementation.md)说明 Server 配置、HTTP、Client SDK、CLI 和 MCP。
- [Server Web UI](server-web-ui.md)说明 Server 托管页面及其安全边界。

生成的 [Python API 参考](../modules.md)记录公开模块。对于提案或历史设计，请查阅 [RFC 索引](../rfcs/README.md)，
并通过当前源码和测试确认实际行为。
