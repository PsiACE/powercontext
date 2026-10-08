---
title: 插件资源生成与分发
description: 渲染完整的显式 Skill 投影，同时保留原生能力和来源所有权。
---

- 提案名称：`plugin_resource_distribution`
- 开始日期：2026-10-09
- 状态：提议
- RFC PR：尚未创建
- 关联 RFC：[RFC 1733](1733-usability-and-agent-workflows.md)

# Summary

提供仓库构建期的完整原生 Skill 资源渲染与检查。声明式 manifest 为 Agent Plugin、Claude Code、WorkBuddy 和 Codex 选择共享来源和显式宿主覆盖。生成资源保留当前原生指导和完整引用。工具、MCP 配置、Hook 注册、凭证及支持资格继续归现有组件所有。

# Motivation

多个入口文件逐字节相同，但原生引用和专用入口不同。统一复制可移植基线会改变 Claude、WorkBuddy 的指导，并移除 Codex 的发现元数据和 Experience/Skill 引用。每份副本分别编写又会使一次共同入口修改只作用于直接编辑的宿主。`experiments/usability/distribution/` 固定基线 `f28f8edf` 的资源字节，记录可执行选择比较。字节检查和纯投影不代表真实宿主加载资格。

# Guide-level explanation

贡献者在 Agent Plugin Skill 基线中编写共同指导，在 `integrations/distribution/skills/overrides/<target>/` 中编写原生差异。`skills/targets.json` 将每个生成文件映射到唯一来源。增加或移除工作流需要显式目标与资源决策，不能因为另一宿主支持某工具就扩大当前宿主的能力。

`make plugin-skills-check` 不写入文件，并纳入 `make check`。`make plugin-skills` 显式刷新仓库内声明的生成资源。可以独立构建一个完整 Skill：

```bash
uv run python scripts/generate_plugin_skills.py --target codex --output ./build-skills
```

输出位于 `build-skills/codex/powercontext-project-context/`。现有原生插件打包继续包含检入的 Skill 资源，不引入新的运行时、来源下载、安装记录或已安装宿主同步。

# Reference-level explanation

## 来源所有权

| 资源 | 权威所有者 | 输出 |
| --- | --- | --- |
| 契约相同的共同入口或引用 | Agent Plugin Skill 基线 | 显式选择的原生文件 |
| 不同的宿主路由、Scope 或引用指导 | 目标覆盖来源 | 仅该目标 |
| 工具与运行时语义 | 现有原生适配器和 API 契约 | 本项不生成 |
| MCP、认证与用户设置 | 现有宿主配置 | 不读取、不写入 |
| 支持能力与资格 | `integrations/capabilities.toml` 和维护中的证据 | 保持不变 |

目标 manifest 使用 schema 版本 1，包含声明的原生目标目录、文件到来源映射和可选显式退役路径。路径必须为受限相对路径。来源解析不能越出选定仓库。每个投影必须具有有效、可发现的入口元数据，并包含所有本地 Markdown 引用，包括递归引用的声明资源。Codex 的 `agents/openai.yaml` 等原生文件继续显式声明。

## 构建、检查与发布

渲染先在内存中解析完整资源集，再检查任何输出目录。检查模式报告缺失或不同的声明文件和仍存在的退役文件，遇到偏差返回非零且不写入。输出模式为每个选定目标生成独立完整 Skill 目录。固定来源字节与 manifest 产生相同文件字节。

仓库刷新先验证所有选定投影和路径，再将每个变化文件暂存于目标旁并逐个替换。只能替换声明文件，只能移除显式 `retired` 路径，其他文件保留。发布前拒绝符号链接目标。`--write` 是开发者主动更新生成仓库内容，不承诺保留已部署插件中对声明生成文件的修改。没有持久哈希账本，也没有整个目录事务；中断可能留下部分仓库刷新，可由重新检查、渲染发现并恢复。

私有 MCP 配置、插件 manifest、原生 Hook 和运行时依赖不属于所有权集合。来源是开发者选定的 Git checkout；渲染器不选择远程来源，也不执行远程构建规则。现有包与插件版本继续遵循独立发布策略。字节可复现不代表发布者认证或运行时兼容。

## Acceptance

聚焦测试将每个声明的原生 Skill 构建到两个隔离目录并比较完整文件摘要，验证当前检入资源一致性、缺失 Experience/Skill 引用的拒绝、偏差报告、显式陈旧文件退役、外来文件和私有 MCP 配置保留、路径限制以及符号链接拒绝。实际 Agent 发现生成 Skill 仍属于独立原生资格。本地文件测试不能推出生成器性能或运行时支持结论。

# Drawbacks

构建期所有权增加 manifest 和覆盖目录。引用改变时贡献者必须更新声明可达性。宿主差异仍需要原生编写；过度统一模板会隐藏这些差异。检入输出占用空间，但使现有直接源码加载和打包无需新的运行时依赖。

# Rationale and alternatives

显式文件投影共享已证明相同的契约并保留差异。统一基线或全局工具目录会改变公开能力，固定比较和 #1691 审查已展示这一风险。运行时部署账本或双向用户文件同步增加了仓库生成不需要的所有权和恢复语义。仅进行内容一致性检查能发现偏差，却仍需要重复编辑；渲染提供共同来源传播操作。

# Prior art

[#1410](https://github.com/oceanbase/powercontext/pull/1410) 定义确定性资源投影，不以能力一致为前提。已关闭、未合并的 [#1691](https://github.com/oceanbase/powercontext/pull/1691) 提供实现材料；实际生成器和审查暴露统一工具发射与未经支持的授权假设，本设计不引入这些行为。

[Magpie 的受管 Skill 副本](https://github.com/yetone/magpie/blob/23d9e5def8c5595c7a8a49df8f2a376084980927/internal/library/skills.go) 与[编辑保留测试](https://github.com/yetone/magpie/blob/23d9e5def8c5595c7a8a49df8f2a376084980927/internal/library/skill_edits_test.go) 说明已安装用户文件同步需要更强的所有权语义。[Lody 的目录注册与测试](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/packages/shared/tests/acp-skills.test.ts) 保留各提供方经验证的发现差异。本设计借鉴明确所有权和原生发现边界，不宣称这里执行了两者的真实宿主验证。

# Unresolved questions

这些 Skill 投影不需要新的运行时或发布格式决策。未来安装更新器需独立定义用户编辑冲突、回滚和经过认证的分发。新增宿主格式需要维护中的目标选择和原生证据。

# Future possibilities

在保留当前指导和资源契约后声明更多宿主。仅在部署和失败语义一致时共享小型隔离执行资源。工具 schema 或注册生成需要显式目标选择和独立验证的公开能力契约。
