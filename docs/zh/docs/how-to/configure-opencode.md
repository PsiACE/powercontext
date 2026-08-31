---
title: 配置 OpenCode
description: 安装 PowerContext OpenCode 插件并控制其本地行为。
---

# 配置 OpenCode

## 安装状态

分发安装器尚未提供 OpenCode。仓库中的插件仍可用于开发和打包，但在宿主 artifact 完成迁移前，不提供受支持的最终用户
安装命令。开发安装完成后，启动 Server，再打开新的 OpenCode 会话：

```bash
powercontext server run
opencode
```

## 理解插件行为

每个正常用户回合，插件通过 `POST /v1/context/prepare` 获取一个有界上下文，同时通过
`POST /v1/sources/content` 独立采集符合条件的提示词。召回内容会标记为不可信历史，并在模型分发前临时
注入，不会写入 OpenCode 会话记录。

具名 `pc_*` 工具提供精选的 Memory、Handoff、Experience、Skill 和只读 Candidate 操作。持久化变更前
OpenCode 会要求确认；Candidate 的批准和拒绝仍由用户通过 CLI 或 Dashboard 显式执行。

## 配置连接

启动 OpenCode 前设置环境变量：

```bash
export POWERCONTEXT_OPENCODE_BASE_URL=http://127.0.0.1:8000
export POWERCONTEXT_OPENCODE_SCOPE_ID=project:example
export POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS=true
opencode
```

启用可选 Bearer 鉴权时，将完整请求头写入 `POWERCONTEXT_OPENCODE_AUTHORIZATION`，不要把凭据写入 URL。
非 loopback 地址必须使用 HTTPS。不应采集当前提示词时，设置 `POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS=false`。

## 验证安装

```bash
powercontext doctor
powercontext doctor opencode
```

集成专用 doctor 会检查 OpenCode 版本、解析后的插件配置和 PowerContext 所有的 Skill。Server 状态由默认
doctor 单独检查；Server 不可用不会阻止 OpenCode 正常工作。
