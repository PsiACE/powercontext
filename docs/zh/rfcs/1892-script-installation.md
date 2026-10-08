---
title: 脚本安装
description: 通过 Bash 和 PowerShell 安装最新或指定版本，独立管理包与运行时镜像，并显式选择 Agent。
---

- Proposal Name: `script_installation`
- Start Date: 2026-10-09
- RFC PR: [oceanbase/powercontext#1892](https://github.com/oceanbase/powercontext/pull/1892)
- Related RFCs: [RFC 1733](1733-usability-and-agent-workflows.md)、[RFC 1299](1299_local_server_availability_and_service_installation.md)

# Summary

提供由发行版维护的 Bash 和 PowerShell 入口，通过 uv 安装 PowerContext。默认版本是所选包索引中的最新稳定版，
`--version` 可以指定准确版本。脚本补齐缺少的 uv 和 Python，安装所选 Runtime profile，并按需调用现有 Agent 集成
适配器，使用与实际安装的 Runtime 对应的 tag。

安装、配置、诊断和服务运行各自承担独立职责。安装软件不会配置推理服务、启动 Server 或注册持久服务。
README 和快速开始将脚本及其安装指南作为首选路径。

# Motivation

正常安装不应要求用户自行协调 Python 发现、uv 安装、包 extras、包索引及集成 Git ref。Python 包、uv 二进制和
Python 发行版也来自不同渠道：只更换 PyPI 索引无法解决 Python 下载不可达的问题。

安装负责放置版本化组件并注册显式选择的集成；配置管理 Server URL、Scope、采集策略等可变值；诊断观察安装结果。
分清这些职责，安装体验就可以独立于配置存储、统一 Hooks、运维工具或服务生命周期改进。

预期结果是每种 shell 一个入口、复用已有依赖、明确的版本选择、显式的宿主选择、可恢复的部分失败，
以及通过实际安装产物进行验证。

# Guide-level explanation

## 安装 Runtime profile

macOS 或 Linux：

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --no-hosts
```

Windows，使用 PowerShell 5.1 或更新版本：

```powershell
powershell -ExecutionPolicy Bypass -c "& ([scriptblock]::Create((irm https://powercontext.oceanbase.io/install.ps1))) --no-hosts"
```

默认 `local` profile 安装 CLI、Client 和本地 Server 依赖。`--profile client` 只安装连接已有 Server 所需的 CLI 和
Client 依赖。Profile 与宿主选择相互独立：选择数据库角色不隐含选择 Agent，选择 Codex 也不会更改 Runtime profile。

安装器复用已有 uv，以及兼容的系统 Python 或 uv 管理的 Python；否则在用户目录补齐 uv 和 Python 3.12。
完成后打印实际 Runtime 版本、profile、可执行文件目录、PATH 命令及下一步配置命令，不修改持久 PATH 设置。
继续操作前，先执行打印的 PATH 命令。

## 选择发行版和集成

默认值为 `--version latest`，重复执行会检查已配置索引上的更新稳定版。需要可重复的包版本选择时，指定准确版本：

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --version 1.2.0 --host codex
```

`--host` 可以重复，`--no-hosts` 跳过集成安装。两者都不指定时，交互终端会打开 `powercontext setup select`；
没有交互终端则在安装依赖前报错。Bash 管道安装使用 `/dev/tty` 读取选择，将终端输入与脚本输入分开。
安装集成仍需 Git 和各宿主自己的前置条件。

已安装的 CLI 提供准确版本，形成 `powercontext-vVERSION`。宿主安装不再次解析 `latest`，也不默认使用 `master`。
宿主安装失败时，保留已安装的 Runtime，脚本返回非零状态，并提示按该 tag 重试 setup；各适配器提供其已有的宿主结果。

本地模式随后运行 `powercontext config init` 和 `powercontext server run --env-file .env`；Client-only 模式
则按远程连接指南设置 Server 地址和认证。持久服务仍由用户显式注册。

## 选择下载来源

`--region auto|cn|global` 优先于 `POWERCONTEXT_INSTALL_REGION`。自动模式依次参考本地命名时区、locale 地区，
最后采用全球源，不调用网络定位服务。

| 组件 | 全球默认源 | 中国区域自动源 | 显式控制 |
| --- | --- | --- | --- |
| PowerContext 及依赖 | PyPI | 清华 PyPI 镜像 | `--index-url`、uv 索引设置及配置文件 |
| uv 安装器及二进制 | Astral 渠道 | USTC release 镜像 | `POWERCONTEXT_UV_INSTALLER_URL`、`UV_DOWNLOAD_URL`、uv 安装器镜像变量 |
| Python | uv 默认渠道 | NJU python-build-standalone 镜像 | `UV_PYTHON_INSTALL_MIRROR`、uv Python 下载配置 |

已有 uv 配置时不自动选择包镜像。显式 `--index-url` 只更改默认索引，额外索引沿用 uv 的优先级。
认证和索引解析由 uv 负责。脚本 URL 参数必须使用 HTTPS，且不包含凭据；凭据通过 uv 配置管理。
pip 的索引环境变量不属于 uv 配置。Python 和 uv 下载覆盖项与包索引彼此独立。

# Reference-level explanation

## 安装职责

入口为 `website/public/install.sh` 和 `website/public/install.ps1`，作为网站静态资源发布。
两者支持相同的选项和失败语义。脚本负责引导与编排；uv 负责环境创建和包解析；现有 `powercontext setup` 适配器
负责集成安装。

安装操作由以下输入确定：

```text
包 requirement + Runtime profile + 显式宿主选择 = 安装操作
```

包管理器在替换工具环境前解析 requirement。安装完成后验证 CLI 的准确版本，再用于全部宿主安装。
这样一次操作具有统一的发行版坐标，无需新增包解析器，也不依赖另一个发行清单系统。

保留 `powercontext setup`、`config`、`doctor` 和服务命令。不把领域行为搬到 shell，不引入独立 Python 安装引擎，
也不修改 Runtime 或公共 HTTP API。

## 版本和 profile 语义

- `latest` 向 `uv tool install --upgrade --prerelease disallow` 传入未固定版本的 profile requirement。
  它表示所选解释器及已配置源兼容的最新稳定版，不一定是另一镜像上最近上传的版本。解析失败直接报告，不改换 requirement。
- 准确版本使用 `==VERSION`，格式为 `X.Y.Z`，可追加 `aN`、`bN` 或 `rcN`。支持显式预发布版本；
  `--version` 不接受源码 ref、版本范围或 URL，也不支持 0.1.0 之前的版本。
- `local` 使用 `powercontext[cli,server]`；`client` 使用 `powercontext[cli]`，后者已经包含 Client 依赖。
  使用不同 profile 重跑会替换该工具的依赖集合；自行添加的 extras 应通过文档中的手动安装路径维护。
- 安装后的可执行文件必须报告发行版本；显式请求必须与其一致。通过检查后才打印安装成功。
  后续宿主安装失败不会移除可用的 Runtime。

uv 使用安装器维护的固定引导版本，与 PowerContext 发行版本无关。已有 uv 直接复用，不悄悄升级。
Python 发现排除虚拟环境，避免项目 venv 意外成为安装前提。没有兼容的 Python 3.11+ 时，通过 uv 安装 Python 3.12。
平台、架构和 wheel 兼容性错误由 uv 报告。

## 镜像优先级与恢复

保留用户显式指定的 uv/Python 来源，不做自动回退。中国区域自动选择时，uv 镜像安装器不可用则回退 Astral；
uv 镜像文件下载失败时可以尝试官方源。Python 的准确构建 URL 由 uv 提供，安装前检查对应镜像文件；
自动 Python 镜像安装失败后，使用 uv 默认渠道重试。

自动包源选择检查 PowerContext 索引页；准确版本还检查索引是否列出该版本。中国区域索引不可达或尚未同步指定版本时，
回退 PyPI。索引选定后，依赖解析和文件下载错误交给 uv 报告，不再换源重试已经尝试的工具安装。
显式索引不经过 shell 探测，由 uv 正确处理认证和配置。

Shell 环境变化局限于安装器进程；PowerShell 在 `finally` 恢复临时来源变量。不用 shell 解析或改写已有配置文件，
只清理由安装器创建的临时下载目录。保留 `UV_INSTALL_DIR`、`UV_TOOL_DIR` 和 `UV_TOOL_BIN_DIR` 的位置控制。

## 持久化与兼容性

安装器不读取或改写 `.env`、凭据、数据目录或数据库 schema，也不停止或重启运行中的服务。
启动升级后的 Server 时，用户按现有升级和迁移指南操作。准确版本重试可以使用缓存；`latest` 则明确允许升级。

`UV_OFFLINE=1` 只支持已有 uv、兼容 Python 和全部依赖时的缓存重装，不承诺完整离线发行包。
引导依赖安装后发生失败，会保留这些依赖供重试；宿主安装失败保留 Runtime。不提供跨组件回滚，
也不修复无关宿主配置。

Windows 保持产品的试验性支持状态。原生验收在 Linux、macOS 和 Windows 执行；具体宿主支持范围仍由各集成的能力契约决定。

## 验收

`tests/native/test_installation.py` 执行真实 shell 安装器、uv、安装后的 CLI 和 HTTP Server。
从被测提交构建 wheel，使用发行版形式的元数据和带校验和的准确文件约束。验收包括：

- 全球源及中国区域源下缺少 uv/Python 的安装、已有工具与配置、包含空格和中文的路径；
- 通过受控包索引解析真实稳定版和预发布版 wheel、默认升级、指定版本，以及版本不存在时保留原安装；
- Client-only 安装及 profile 切换，安装过程不创建本地 Server 状态；
- `.env` 生成与验证、就绪检查、Memory 保存与搜索、离线缓存重装及重启后的回读；
- 显式索引失败、非交互模式缺少选择、Bash 管道终端选择，以及 PowerShell 下载脚本后执行 scriptblock 的入口。

独立 CI 矩阵在三个操作系统执行上述套件。发布引用检查保持脚本的 `latest` 默认值，同时更新显式版本示例。
网站验证检查文档链接和静态构建。语法检查本身不证明 Windows 或 macOS 安装可用；这些测试也不声称
真实 Agent 宿主已完成采集与召回工作流。

# Drawbacks

Bash 和 PowerShell 会重复部分编排和来源选择策略。自动镜像增加外部可用性依赖。
`latest` 会随时间变化，可重复部署需要指定版本。匹配 tag 能协调发行版，但不能单独证明宿主兼容性。

# Rationale and alternatives

保留直接运行 uv 命令的路径，供已有 Python 和 uv 的用户使用；它无法引导干净机器。
源码安装适合开发，不作为默认发行渠道。把公共脚本固定在某个 PowerContext 版本，会使新用户在网站重新发布前
一直安装过期版本。

独立安装引擎和不可变组件清单可以支持更丰富的安装计划，但不是包引导及可靠来源控制的前提。
复用现有包管理器和宿主适配器，使本项工作可以独立交付，同时保留可用的 CLI 契约。

# Prior art

[RFC 1408](https://github.com/oceanbase/powercontext/pull/1408) 定义了安装、配置和诊断的职责分离，
独立的 Runtime profile 与宿主选择、组件级恢复和显式服务注册。
[RFC 1299](1299_local_server_availability_and_service_installation.md) 定义个人服务生命周期。
[Bub](https://github.com/bubbuild/bub/tree/main/website/public) 提供复用 uv 并报告后续配置步骤的 Bash/PowerShell 引导入口。
[uv](https://docs.astral.sh/uv/guides/tools/) 提供独立工具环境、Python 安装、包索引和缓存。

# Unresolved questions

接受本契约无需额外的跨子系统决策。完整离线包、带密码学校验的安装清单、独立打包的宿主产物以及配置存储变化，
需要各自设计。推广前必须保持平台矩阵通过，Windows 产品支持仍为试验性。

# Future possibilities

当安装计划需要不可变组件清单、独立集成版本或持久组件修复记录时，脚本可以引导发行版维护的独立安装引擎。
离线包也可以显式携带全部所需产物。这些扩展都不应隐式启动 Server 或注册服务。
