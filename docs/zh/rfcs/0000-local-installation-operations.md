---
title: 独立可用的本地运维入口
description: Runtime 导入失败时仍可检查环境、管理自有原生服务并修复 PowerContext 软件包。
---

- Proposal Name: `local_installation_operations`
- Start Date: 2026-10-09
- Status: Proposed
- RFC PR: Not opened
- Related RFCs: [RFC 1733](1733-usability-and-agent-workflows.md)、[RFC 1299](1299_local_server_availability_and_service_installation.md)

# Summary

在现有发布 wheel 中提供仅依赖标准库、独立导入的 `powercontext-ops`。它检查本地安装与原生服务状态，只控制已验证属于 PowerContext 的注册，并通过 uv 执行用户明确选择的精确版本修复。现有 `powercontext` 启动入口保持不变。不引入守护进程、新的软件包、安装流水账、接管数据库或通用命令分发器。

只要运维模块和 Python 解释器仍存在，Server 停止或 Runtime 导入、依赖损坏时仍可维护。整个 wheel、环境或解释器被删除时，应使用 shell 引导或软件包管理器恢复。这一有界独立性不承诺任意损坏后的自我修复。

# Motivation

RFC 1299 已规定精确的原生资源归属、先停止后删除、分别报告注册、管理器与存活状态，并保留业务数据。当前原生模型与适配器使用标准库，但其父包和普通 CLI 会导入 Runtime 依赖；Service 控制器在执行状态查询前也会导入 Server 配置。现有重试引导模块已展示独立命名空间可在 Runtime 缺失时运行。

[运维实验](../../../experiments/usability/operations/README.md) 执行 Server 停止与导入故障场景，比较同一环境中的独立兄弟入口，并检查自有、外部及缺失注册。消融证明小型导入边界可行。安装后的真实构建 wheel 在 Runtime 依赖缺失时仍可运行运维入口；另行标注的受控 wheel 验证具名 uv 修复及真实 Linux systemd 对夹具进程的归属、启动、停止和卸载。既有行为测试保护删除及部分失败。这些结果不证明 macOS 或 Windows 上真实 PowerContext Server 的完整生命周期；相关声明仍须匹配平台验收。

# Guide-level explanation

```text
powercontext-ops
+-- status
+-- doctor
+-- server
|   `-- start | stop | restart | logs | uninstall
`-- repair --target uv-tool --version EXACT --profile local|client [--index-url HTTPS]
```

`status` 检查本地软件是否存在、定义、注册、管理器归属与状态以及精确日志位置。`doctor` 使用已记录的服务解释器执行有界 Runtime CLI 启动检查；没有服务注册时使用当前解释器。输出为 JSON，各事实分别报告，包括管理器不支持及状态未知。检查不会启动 Server，二者均不修改配置或数据。Server 存活明确为未知，须通过现有 Runtime 健康接口另行检查；管理器不可用时，本地检查仍可执行。

Server 生命周期使用现有的每用户原生标识与记录的启动器、端点。变更前同时验证磁盘上的定义与已加载的管理器对象；归属外部或未知时拒绝操作。端口、显示名称或 PID 不足以证明归属。远程端点只进行连接诊断，不触发本地生命周期操作。日志只来自已验证注册的原生选择器或路径，不读取无关服务元数据或环境。

启动、重启分别报告管理器操作与 Server 存活、就绪结果。停止不删除注册或数据。卸载先停止，再仅移除已验证注册；保留软件环境、配置和业务数据。软件包移除与数据清理不在首批命令中。

# Reference-level explanation

## 修复与分发

默认修复针对 uv 管理的具名 `powercontext` 工具，由 uv 负责环境与软件包替换。必须明确精确发布版本和 Runtime 配置档；不使用 `latest`，不替换版本，不自动切换下载源。显式默认索引遵守 uv 的附加索引优先级与认证配置；软件包索引、uv 引导源和 Python 下载源保持独立。输出说明显式请求的索引或当前 uv 配置这一源选择；公开 uv API 无法证明原始安装来源。继续使用私有源时，操作者须提供相同的显式索引或配置；不解析内部收据，也不宣称保留未知的原始来源。

本次不提供手工环境修复，不从当前解释器推断目标归属。`--target uv-tool` 明确选择当前 uv 配置下的具名工具，它可以与启动运维入口的安装不同。通过公开的 `uv tool dir --bin` 解析目标可执行目录，安装后验证其中的 Ops 启动器与精确版本，分别报告 Runtime 启动器及启动结果；不声称修复了另一个调用方环境。

修复不自动启动、停止或重启服务。已安装的自有服务必须能够确认已停止，否则应先明确停止，再替换软件；已停止的服务保持停止。原生控制和修复使用既有注册锁串行化。uv 成功后报告实际安装版本及 CLI 启动检查。检查失败时保留可观察的软件与注册状态，不报告成功修复，也不承诺程序回滚。任何操作都不修改数据库格式或宣称完成数据恢复。

现有 wheel 增加一个小型兄弟命名空间和 console 入口。干净迁移共享的标准库原生资源所有者并更新调用方导入，不复制第二套归属实现，也不经 Runtime 父包导入。保留记录的 Server 启动路径和 RFC 1299 元数据兼容性。本次有界交付保留既有服务与普通 CLI 行为；后续统一命令迁移须有明确的调用方和分发方案，而非在此添加永久别名。

## 验收

构建并安装真实发布 wheel，在 Server 停止、Runtime 依赖被阻断及 Runtime 启动失败时运行 `powercontext-ops`。通过匹配平台的原生管理器验证本地状态、归属拒绝、日志、停止、启动、重启及卸载。受控管理器或文件夹具只证明本地协议处理。

验证精确 uv 修复、源失败、版本不可用、活动服务拒绝及修复失败后的状态保留；检查实际软件结果与保留的配置、数据。将整个环境丢失明确归类为引导恢复边界，不报告为独立运维入口成功。

# Drawbacks

独立可执行入口略增发现成本，但可作为 RFC 1733 独立本地运维的渐进交付。

# Rationale and alternatives

托管 Runtime 记录和通用分发器会增加目前导入故障证据尚未证明必要的新权威；同 wheel 入口保留简单的发布协调，并明确其恢复边界。

# Prior art

[Lody 的安装交接契约](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/specs/daemon-upgrade-installation.md) 分开检查软件包安装、精确启动器及版本、Worker 就绪；交接失败不等同于软件包回滚。[其守护进程控制](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/apps/cli/src/commands/daemon.ts) 验证实例身份，而非仅凭过期 PID 停止进程。PowerContext 复用原生管理器，不引入 Lody 的监督进程或认证控制协议。

# Unresolved questions

本设计不要求新守护进程或接管 Runtime 启动器。原生支持限定于通过匹配平台生命周期验收的平台；整个环境丢失使用引导恢复。

# Future possibilities

后续可在独立分发和调用方兼容方案下，将相同维护操作合并到统一入口。软件移除和数据恢复须分别定义明确契约。
