---
title: Agent 可操作的配置
description: 发现、检查、预览并应用类型化的本地 .env 修改，保护私密输出并处理修订冲突。
---

- Proposal Name: `agent_operable_configuration`
- Start Date: 2026-10-09
- Status: Implemented
- RFC PR: Not opened
- Related RFC: [RFC 1733](1733-usability-and-agent-workflows.md)

# 概述

扩展现有 `powercontext config`，提供字段发现、结构化检查和静态验证，以及非交互的局部计划与应用。继续使用 `.env` 和现有不执行 Shell 的解析器。初期支持明确的字段目录，其类型来自拥有这些设置的模型，不引入竞争的配置语言。

# Motivation

Agent 需要确定的本地资源与结构化操作，而不是模拟问卷。仅原子替换仍会丢失并发修改。设置模型忽略未知环境变量，适合运行时扩展，却不足以验证修改请求。普通输出必须排除未知私密值。

# Guide-level explanation

`config schema --json` 发现可写类型和环境变量名。`show --json` 报告修订、资源、公共设置、来源与秘密是否存在，不输出凭证值。`validate --json` 报告静态检查，不声称网络可达。文本显示保留赋值名，但所有未知值均隐藏，包括以前可见的值。Server 文本验证保留既有运行时预检；JSON 验证仅执行静态检查。

`plan` 接受严格的版本 1 JSON 文档，包含资源、基准修订、`set` 和 `unset`。同一字段不能同时设置和删除。未指定的赋值保持不变。它验证字段类型和模型的跨字段约束，无写入、无网络地返回修改及重启要求。`apply` 再次验证，在本地锁内检查修订后原子保存权限为 0600 的文件。冲突不能覆盖当前文件。

Client 文件使用 `config show --target client --env-file .env --json` 取得字节修订。显式修改文档示例：

```json
{"schema_version":1,"target":"client","resource":"/absolute/project/.env","base_revision":"missing","set":{"timeout":12.0},"unset":[]}
```

`config plan --request-file change.json --json` 只预览；`config apply --request-file change.json --json` 授权本地写入。已有文件使用刚读取的修订。请求文件 `-` 表示标准输入。凭证采用 `"api_token":{"from_env":"POWERCONTEXT_NEW_TOKEN"}` 或 `{"from_file":"/private/token"}`，在每次 plan/apply 时重新解析受控输入，不回显凭证。它将值保存到现有私密 `.env`；连接文档的环境变量引用则始终只保存引用。

Client 操作仅需 `[cli]`；Server 操作需要 Server extras。Client 激活标记为 `next_invocation`，Server 为 `restart_required`。这些命令不启动进程。

# Reference-level explanation

资源路径显式指定并规范化，不向上搜索目录。解析现有 `.env` 语法而不执行 Shell。保留注释和未指定的值，包括提供方凭证。未知字段、版本、无效输入与掩码形式的凭证替换在副作用之前拒绝。初始公共字段覆盖监听器、Dashboard、访问与认证、MCP、日志和 Client 连接控制。敏感修改采用受控输入；普通计划、显示和错误仅报告存在状态及修改字段名。

读取修订标识完整文件字节，包括注释和私密赋值。缺失文件有独立修订，允许显式创建。协作写入锁覆盖读取、检查与替换；任意外部编辑器不参与此协议。只发布完整快照，且仅清理操作自身的临时文件。配置写入属于本地行为，不授予远程管理权。

应用分别报告持久化与激活：`saved_revision`、`activation: restart_required` 和静态检查结果。没有观察到运行实例时，不声称它已经激活。不得隐式启动服务、探测模型或写入业务记录。JSON 是可组合输出，不代表这些行为的许可。连接工作流可以使用相同概念，无需新建命名连接。

# Acceptance

验证类型发现、未知字段拒绝、预览无副作用、局部保留、私密值隐藏、无效组合、过期及并发冲突、0600 读回、多行语法和准确激活状态。通过公共 CLI 与跨进程写入验收；真实网络、模型和宿主需要独立验收。

# Drawbacks

可写范围是明确的公共标量字段。提供方与数据库编辑仍使用现有配置接口。Unix 写入权限为 0600；Windows 依赖文件系统 ACL，不声称执行 Unix 权限。锁仅协调使用此协议的写入方。修改的赋值会规范化，其余注释和多行赋值保持原样。

# Rationale and alternatives

[执行消融](https://github.com/PsiACE/powercontext/blob/cce34000/experiments/usability/configuration/README.md) 说明需要修订检查、严格修改字段和公共输出白名单，不支持替换 `.env`、加入 TOML 引擎、daemon、任意提供方请求透传或全流程事务。

# Unresolved questions

提供方专用编辑、原生秘密后端和绑定观察结果的在线检查需要具体消费者。它们可独立交付，不阻塞本地静态操作。

# Prior art

[Cida](https://github.com/Xuanwo/cida/blob/a48745e79632f93d6763605d5718ab4b7cea1122/Sources/Cida/ConfigurationFields.swift) 从拥有字段推导 schema、解析与显示；[Jiandao](https://github.com/Xuanwo/jiandao/blob/76a3fbc0501ac9c6f5cb5aa88a16d0923bff97b3/src/utils/setup-document.ts) 将严格意图与存储分离；[Agenvo](https://github.com/Xuanwo/agenvo/blob/235978f9fd9cf70fd75b03292619262d5e86a6e6/docs/installation.md) 区分保存、部署与可达性。这里只检查源码，不将它们描述为已执行的验收。

# Future possibilities

提供方拥有的编辑接口与独立授权的在线检查可在实际需求出现后扩展。原生秘密存储、运行实例激活观察仍属于独立工作。

Linux 验收执行真实跨进程并发及新安装 `[cli]` wheel，使用 Typer 0.27.3，不含 Click 或 SQLAlchemy。Windows 可移植性工作流已纳入配置测试；本地尚未观察 Windows/macOS 执行。
