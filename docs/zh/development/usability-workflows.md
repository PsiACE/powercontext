---
title: 独立的易用性工作流
description: RFC 1733 六个独立方向的实现、公开契约、验证证据与交付边界。
---

# 独立的易用性工作流

[RFC 1733](../rfcs/1733-usability-and-agent-workflows.md) 将安装、本地运维、Client 连接、Server 配置与任务指导交给各自的责任方。下面六个提案分别提供可独立使用的能力，无需先引入 daemon、新配置语言或通用宿主执行引擎。

| 主题与 RFC | 已实现的行为 | 功能分支 |
| --- | --- | --- |
| [Hooks 治理](../rfcs/0000-hook-governance.md) | Codex、Claude Code、WorkBuddy 在推进 checkpoint 前校验精确的 capture 回执；共享可执行用例保护同一约束 | [fix/hook-capture-receipts](https://github.com/PsiACE/powercontext/tree/fix/hook-capture-receipts) |
| [插件资源分发](../rfcs/0000-plugin-resource-distribution.md) | 声明式文件映射保留各宿主的 Skill 入口和引用；构建检查发现漂移并拒绝不安全路径 | [feat/plugin-skill-projections](https://github.com/PsiACE/powercontext/tree/feat/plugin-skill-projections) |
| [连接与项目绑定](../rfcs/0000-client-connections-and-project-bindings.md) | 解释 Client 端点来源，按 revision 保存连接，查看、绑定和解绑现有 Codex 工作副本 Scope | [feat/client-connections](https://github.com/PsiACE/powercontext/tree/feat/client-connections) |
| [Agent 可操作配置](../rfcs/0000-agent-operable-configuration.md) | 发现类型化字段，对 `.env` 执行保护私密值、校验 revision 的 plan/apply | [feat/agent-operable-configuration](https://github.com/PsiACE/powercontext/tree/feat/agent-operable-configuration) |
| [本地运维](../rfcs/0000-local-installation-operations.md) | 仅依赖标准库的 `powercontext-ops` 查看、控制有明确归属的原生服务，修复显式选定的 uv tool | [feat/independent-local-operations](https://github.com/PsiACE/powercontext/tree/feat/independent-local-operations) |
| [任务型 Skills](../rfcs/0000-task-oriented-skills.md) | 安装维护与产品任务指引选择实际可用工具、核验写入，并分别报告各类产物状态 | [feat/task-oriented-skills](https://github.com/PsiACE/powercontext/tree/feat/task-oriented-skills) |

[组合分支](https://github.com/PsiACE/powercontext/tree/feat/usability-workflows) 在上游 `f28f8edf` 基础上整合这些契约。任务型 Skills 使用资源生成来分发共享指导，其余功能分支可独立审阅。连接与配置共用一个职责明确的文件 revision／锁原语。本地运维迁移现有的标准库环境文件解析器和原生服务实现；组合时更新调用方，不另建一套归属判定逻辑。

## 查看、预览、应用、验证

安装脚本单独交付于 [#1892](https://github.com/oceanbase/powercontext/pull/1892)。发布后的安装指南提供脚本时优先使用脚本；未合并分支不代表下载地址已经上线。下列新命令要求安装包含对应功能的构建，操作旧版本前先发现实际命令帮助。

查看 Client 策略不会联系 Server：

```bash
powercontext connection inspect --host client
```

保存显式端点时使用返回的 revision。Client 凭据引用只保存环境变量名，不保存其值。查看结果描述 Client 传输选择，独立配置的宿主 MCP 是否已激活仍是未观测状态。

```bash
powercontext connection configure --host client \
  --server-url https://server.example \
  --expected-revision REVISION --api-token-env POWERCONTEXT_TOKEN
```

项目操作使用同一 Server 已签发的 Scope ID。不同 Git worktree 保持独立身份；未绑定工作副本不会被默认 Scope 替代。

```bash
powercontext project inspect --project /path/to/checkout --server-url https://server.example
powercontext project bind --project /path/to/checkout --server-url https://server.example --scope-id SCOPE_ID
```

配置操作显式选择文件与目标。Server 字段需要 Server extras；Client 字段只需 `[cli]`。

```bash
powercontext config schema --target client --json
powercontext config show --target client --env-file /path/to/client.env --json
powercontext config plan --request-file change.json --json
powercontext config apply --request-file change.json --json
```

变更文档包含资源、已观察到的字节 revision 和类型化变更。只有文件不存在时才使用 `missing`：

```json
{
  "schema_version": 1,
  "target": "client",
  "resource": "/path/to/client.env",
  "base_revision": "missing",
  "set": {"timeout": 12.0},
  "unset": []
}
```

未知字段在写入前失败，文件中已有的未知赋值保持私密且予以保留。秘密值通过受控环境变量或私有文件输入；与连接的凭据引用不同，配置 apply 会把解析后的值写入私有 `.env` 文件。保存结果说明需要重启或等待下次调用，不宣称运行中的进程已经采用新值。

只要 Ops 模块和 Python 解释器仍存在，Runtime 依赖损坏时仍可执行独立维护入口：

```bash
powercontext-ops status
powercontext-ops doctor
powercontext-ops server logs
```

生命周期操作先验证原生服务归属；`logs` 返回精确日志位置或选择器。修复必须提供 `--target uv-tool --profile local|client --version EXACT`，使用显式或当前 uv 源配置，并保持服务停止。它不修复任意手动环境，也不重建未知的原始安装源；整个环境丢失时需要 bootstrap 恢复。

## 编写资源与验证证据

`integrations/distribution/skills/targets.json` 选择公共 Skill 来源及显式宿主覆盖文件。修改这些来源后运行 `make plugin-skills`，通过 `make plugin-skills-check` 检查生成结果。生成器不修改用户宿主安装、MCP 凭据、Hook 注册或公开工具集合。

各 RFC 记录选定契约、替代方案、第一方资料和验收边界。`experiments/usability/` 提供可复现实验，固定基线的说明区分历史消融与当前实现检查。Magpie、Lody 为原生发现和归属边界提供参考；Cida、Jiandao、Agenvo 为字段所有权、显式变更文档以及已保存／已激活状态提供参考。源码阅读不等于执行这些应用。

证据包括真实适配器子进程访问回环 HTTP fixture、并发 CLI 进程、SQLite／HTTP Scope 工作流、移除 Runtime 依赖后的实际 wheel 执行，以及一次性 Linux systemd 服务的生命周期执行。模拟宿主事件不证明交互式 Agent 回调可用。Linux 原生生命周期使用受控 fixture 进程；macOS／Windows 运维、真实模型与宿主工作流仍需对应验收。#1892 的三平台原生安装 CI 仅证明该安装变更。

RFC 1733 的完整引导流程、命名连接目录、原生秘密存储、用户插件实时同步、更多宿主绑定方案与本地 daemon 仍是独立契约，不是这些已交付操作的前置条件。
