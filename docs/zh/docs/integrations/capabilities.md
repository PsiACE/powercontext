---
title: 集成能力
description: 从实际参与分发与注册的定义生成工具和 Hook 视图。
---

# 集成能力

下表从原生注册和 setup 使用的工具目录生成。扩展工具保留现有宿主能力，公共工具集仍以统一基准为准。MCP 工具由 Server 发现；SDK 集成提供应用 API，不代表固定的模型工具集。Hook 列来自命令清单、WorkBuddy setup 或 TypeScript 事件注册器实际消费的绑定。空白表示不由分发层注册事件：Bub、Hermes 和框架回调通过宿主原生类接口发现，不再另行维护一份方法名单。运行时是否可用仍由宿主策略决定。

<!-- integration-catalog:start -->

| Target | 语言 | 工具（基准 + 扩展） | Hook 注册 |
| --- | --- | --- | --- |
| Codex | python | Server MCP | `SessionStart`, `PreToolUse`, `UserPromptSubmit`, `Stop` |
| Claude Code | python | Server MCP | `UserPromptSubmit` |
| DeepSeek Harness | typescript | 14 + 9 | `agent/pre-step`, `tools/pre-execute` |
| OpenClaw | typescript | 14 + 0 | `before_prompt_build`, `agent_end`, `before_compaction`, `session_end` |
| OpenCode | typescript | 14 + 9 | `chat.message`, `experimental.chat.messages.transform`, `experimental.chat.system.transform`, `event` |
| Pi | typescript | 14 + 20 | `before_agent_start`, `agent_end`, `session_before_compact`, `session_before_switch`, `session_shutdown` |
| Hermes | python | 14 + 21 | — |
| WorkBuddy | python | Server MCP | `UserPromptSubmit` |
| MiniMax | python | Server MCP | `UserPromptSubmit` |
| Bub | python | SDK | — |
| LangChain | python | SDK | — |
| LangGraph | python | SDK | — |
| Pydantic AI | python | SDK | — |
| OpenDAL | python | SDK | — |
| Agent Plugin | none | Server MCP | — |

<!-- integration-catalog:end -->

使用 JSON 查看各工具名称及对应操作，或直接输出对照表：

```bash
uv run python scripts/build_agent_distributions.py --list
uv run python scripts/build_agent_distributions.py --list --format markdown
powercontext doctor integrations --json
```

所有目标通过 HostAdapter 使用 Python setup 和 doctor。表格描述分发内容，doctor 检查本机安装和配置；运行时工具是否可用，由宿主策略和会话状态决定。`make agent-resources` 会随插件资源刷新本表。架构与新增宿主的方法见[插件架构与分发](../../development/plugin-distribution.md)。
