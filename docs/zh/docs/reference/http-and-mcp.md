---
title: HTTP 和 MCP
description: PowerContext Server 端点和传输层可用范围。
---

# HTTP 和 MCP

Server 提供以下端点：

| 路径 | 用途 |
| --- | --- |
| `/openapi.json` | 完整 HTTP 契约的 OpenAPI 文档 |
| `/health/ready` | 就绪检查 |
| `/v1/capabilities` | 已启用的 Server 能力 |
| `/mcp` | Streamable HTTP MCP 端点 |

HTTP 是完整应用契约。MCP 是面向 Agent 的 Memory 与 Candidate Review operation 子集。五个 Candidate Review
operation 通过 HTTP 和 MCP 使用相同的 validation、`expected_version` 并发校验和 approval transaction。

Experience 和 Skill generation、exact read、external Registry operation 和低阶 proposal operation 仍只通过
HTTP 提供。

`POST /v1/context/prepare` 及对应的 Python Client method 通过 HTTP 提供最终的临时 `PreparedContext`。Runtime
召回 active Memory 与 approved Experience head，并统一负责选择和总输出预算。该 operation 不会投影为 MCP
tool。public schema 是 `powercontext.prepared-context.v1`；Experience item 在 prepared content 内携带精确
Artifact 引用。
