---
title: Client 连接与项目绑定
description: 解释保存的 Client 端点，并显式管理现有 Codex 检出目录的 Scope 绑定。
---

- Proposal Name: `client_connections_and_project_bindings`
- Start Date: 2026-10-09
- Status: Implemented
- RFC PR: Not opened
- Related RFC: [RFC 1733](1733-usability-and-agent-workflows.md)

# Summary

通过可复用的公共操作和 CLI 暴露现有 Client 传输解析与 Codex 项目绑定。继续使用版本 1 `clients.json`、环境凭证和 Server 管理的 Scope 绑定。命名连接与其他宿主投影是独立扩展，不是前置条件。

# Motivation

现有解析器已经保证关键安全行为，但不能解释端点来自哪个输入。宿主安装可能拒绝单次显式请求能够安全覆盖的冲突。Codex 检出目录绑定已是 Server 中的持久记录；另建本地目录会增加身份同步问题。

# Guide-level explanation

`powercontext connection inspect` 无网络调用地报告所选宿主的端点、传输许可、来源名称、凭证引用状态和资源修订。`connection configure` 保存端点和许可，以及可选的 Client 令牌环境变量引用，拒绝会在运行时覆盖选择的环境冲突，并要求此前观察到的修订。它不修改原生 MCP，也不声称宿主已激活。

`powercontext project inspect`、`bind` 和 `unbind` 在所选 Server 上操作现有 Codex 检出目录绑定。绑定接受准确的现有 Scope ID，不按显示名称创建 Scope，也不迁移其他 Server 的 Scope。使用 `project bind --project /path/to/checkout --server-url https://server.example --scope-id SCOPE_ID`，之后以相同端点和项目执行 `project inspect` 或 `project unbind`。切换端点后查询对应 Server 的绑定。

# Reference-level explanation

保留构造参数、宿主环境、公共环境、保存值、默认值的优先级。保存的明文 HTTP 许可绑定端点，API 与 `/mcp` 后缀视为同一端点。URL 拒绝内嵌凭证、查询参数与片段。凭证引用初期仅为 Client CLI 的环境变量名；不保存或输出其值。缺失引用凭证时，在发出请求前失败。显式令牌和现有环境令牌保留优先级。

项目身份沿用现有 Codex 工作区算法：对准确的规范化检出根目录求散列。独立 Git worktree 保持独立；远程地址和显示名称不能隐式合并项目。绑定通过现有 Scope 服务写入，只确认 Server 返回的准确结果；检查禁用默认 Scope 回退。解绑报告现有服务结果。失败或结果不明的网络写入不自动重放。

连接写入在本地协作写入锁内比较字节修订，并发布完整 JSON 文档。未知版本在写入前拒绝。保留其他宿主及未知字段，失败保留旧文件。此锁不承诺协调任意外部编辑器或多文件宿主安装。

# 验收

验证来源优先级、同端点与换端点许可、环境冲突、缺失令牌引用、并发修订冲突、跨进程读回、worktree 身份，以及通过实际 API 执行绑定、解绑与端点切换。原生宿主激活不属于此契约。

# Drawbacks

文件锁只协调合作写入者。原生宿主可能需要显式重载，环境凭证引用依赖进程环境。项目操作目前仅针对 Codex 工作区键。

# Rationale and alternatives

[执行证据](https://github.com/PsiACE/powercontext/blob/cce34000/experiments/usability/connections/README.md) 支持扩展现有操作，无需新的命名连接库或本地 Scope 副本。CLI 与 HTTP Client 暴露这些操作；现有宿主适配器继续各自的原生契约。

# Prior art

证据报告中固定版本的 Magpie、Lody 和 Agenvo 源码支持显式身份和分别报告保存与原生生效结果。验收未执行这些应用。

# Unresolved questions

命名连接、原生凭证存储和其他宿主项目绑定需要独立的消费者与验收，不阻塞本实现。

# Future possibilities

有明确消费者及原生验收时，可扩展命名连接、其他宿主绑定键和原生秘密存储。

验收包括持久化、冲突、凭证与进程内实际 SQLite/HTTP API 绑定。独立构建的 `[cli]` wheel 使用 Typer 0.27.3，未安装 Click 或 SQLAlchemy，通过了原生并发配置及重启读回场景。未执行原生 Agent、Windows 或 macOS。

所有连接与项目命令成功时输出 JSON，诊断写入 stderr。
