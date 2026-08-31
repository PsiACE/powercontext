---
title: 安装和运行
description: 安装 PowerContext 和所选集成，并运行本地 Server。
---

# 安装和运行

## 安装应用和集成

在 macOS 或 Linux 上，显式选择 Runtime profile 和每个宿主：

```bash
curl -fsSL https://raw.githubusercontent.com/oceanbase/powercontext/master/install.sh | bash -s -- \
  --profile local \
  --host codex \
  --host claude-code \
  --yes
```

在 Windows PowerShell 上：

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/oceanbase/powercontext/master/install.ps1))) `
  --profile local --host codex --host claude-code --yes
```

安装器会在需要时获取 `uv`，创建独立的用户级虚拟环境，暴露 `powercontext` 可执行文件，通过宿主原生
marketplace 安装所选集成，并验证宿主可见状态。只安装 Runtime 时传入 `--no-hosts`。在交互终端中省略
`--yes`，可以先检查并确认安装计划。

## 运行本地 Server

```bash
powercontext server run
```

未设置环境变量时，Server 会：

- 监听 `127.0.0.1:8000`；
- 在 `/mcp` 启用 Streamable HTTP MCP；
- 在 `/` 启用 Dashboard；尚未配置 scope 时，页面会显示明确的空状态；
- 在操作系统的用户数据目录中创建持久化 SQLite 数据库；
- 无需推理服务即可支持显式 Memory 操作。

启动成功后，终端会输出 Dashboard 地址，例如 `http://127.0.0.1:8000/`。Dashboard 与 HTTP API、MCP 共用 Server
的监听地址和端口。Dashboard 初始化失败时，Server 会记录包含直接原因的 warning，并继续提供其他接口；可通过
`POWERCONTEXT_SERVER_DASHBOARD_ENABLED=false` 显式关闭 Dashboard。

按 `Ctrl-C` 可正常关闭。再次运行该命令会打开同一个数据库。

## 使用嵌入式 seekDB

在有兼容 `pylibseekdb` wheel 的 Linux 和 macOS 系统上可以使用嵌入式 seekDB；Windows 不支持该嵌入式
后端。使用 seekDB profile 安装或替换 Runtime：

```bash
curl -fsSL https://raw.githubusercontent.com/oceanbase/powercontext/master/install.sh | bash -s -- \
  --profile seekdb --no-hosts --yes
```

从 SQLite 切换时，需要从 `.env` 中删除 `POWERCONTEXT_SERVER_DATABASE_URL` 和
`POWERCONTEXT_SERVER_DATABASE_VEC1_EXTENSION`，或在 shell 中取消这两个变量；seekDB 不接受这些配置。
然后选择 seekDB 后端并启动 Server：

```bash
unset POWERCONTEXT_SERVER_DATABASE_URL
unset POWERCONTEXT_SERVER_DATABASE_VEC1_EXTENSION
export POWERCONTEXT_SERVER_DATABASE_KIND=seekdb
powercontext server run
```

PowerContext 固定使用 seekDB 内置的 `test` 数据库。未设置 `POWERCONTEXT_SERVER_DATABASE_PATH` 时，实例保存在
PowerContext 用户数据目录的 `seekdb` 子目录中；如果设置了 `POWERCONTEXT_HOME`，默认路径为
`$POWERCONTEXT_HOME/seekdb`。只有需要其他位置时才设置 `POWERCONTEXT_SERVER_DATABASE_PATH`。

在另一个终端确认 Server 和数据库已经就绪：

```bash
powercontext doctor
powercontext ready
powercontext capabilities
```

## 验证安装

```bash
powercontext doctor
powercontext doctor codex
powercontext doctor dsh
powercontext doctor pi
powercontext ready
powercontext capabilities
```

`doctor` 检查已安装的包、Server 存活状态和 Server 就绪状态，不要求安装集成。Server 就绪检查涵盖数据库和
每个已配置的推理服务。Runtime 或数据库故障返回 `not_ready`；推理服务故障返回 `degraded`，不会使数据库
操作退出流量。`doctor codex`、`doctor dsh` 和 `doctor pi` 分别检查对应的可选宿主 CLI 与 PowerContext 集成。内容命令会经过公开 HTTP SDK
路径。`ready` 和 `capabilities` 用于查看运行中服务的就绪状态和已启用能力。完整的状态解释和恢复步骤见[排查问题](troubleshoot.md)。

## 更新或替换安装

使用所需 profile 和宿主重复运行安装器即可更新。开发期间可以传入 `--ref`，让 Runtime 和 marketplace
安装自同一个 Git ref：

```bash
curl -fsSL https://raw.githubusercontent.com/oceanbase/powercontext/master/install.sh | bash -s -- \
  --ref <git-ref> --profile local --host codex --yes
```

更新后重启 Server，并开启新的宿主会话。只要没有修改 `POWERCONTEXT_HOME` 或数据库 URL，现有 SQLite
数据会继续保留。

## 为 Python 项目安装角色

如果应用需要导入异步 Client SDK，应把它加入该应用自己的环境：

```bash
uv add "powercontext[client] @ git+https://github.com/oceanbase/powercontext.git@master"
```

进程内 Python 组合使用 `builtin`，服务使用 `server`，Python SDK 使用 `client`，基于 Server 的命令行使用
`cli`。只安装在 `uv tool` 隔离环境中的 extra 不能被另一个 Python 项目直接导入。
