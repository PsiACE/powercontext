---
title: Hook 治理
description: 验证精确 Source 回执，同时保留原生事件、授权、预算和输出契约。
---

- 提案名称：`hook_governance`
- 开始日期：2026-10-09
- 状态：提议
- RFC PR：尚未创建
- 关联 RFC：[RFC 1733](1733-usability-and-agent-workflows.md)

# Summary

通过共享的可观察行为案例和操作边界验证治理自动执行。Codex、Claude Code 和 WorkBuddy 在推进检查点前验证完整捕获回执。原生适配器继续拥有事件、Scope 绑定、捕获授权、凭证、截止时间和输出。这项修正不需要共享进程、额外安装 Client 或引入 daemon。

# Motivation

Server 的 `CaptureContentSourceResponse` 要求 `status: accepted`、`content` Source 引用和正整数位置。仅有正整数位置不能确认当前写入。在基线 `f28f8edf` 上，实际执行的 Codex 和 Claude 子进程会在收到只有位置的响应或指向其他 Source 的响应后推进检查点。仅修改回执验证即可阻止这两类检查点，同时保留有效确认和自动执行的非阻塞行为。`experiments/usability/hooks/` 提供固定提交的可复现实验和一手来源。模拟 HTTP 响应验证适配器处理，不代表真实宿主事件投递资格。

# Guide-level explanation

自动召回和捕获继续使用现有宿主入口。有效捕获回执允许可选刷新推进到已确认位置。无效或无法解码的回执阻止刷新，但不阻止用户继续 Agent 会话。写入可能已到达 Server，因此不能宣称数据确定未保存，也不能自动重放。

Codex 和 Claude Code 使用现有原生通道发送不包含内容的 `capture_source`、`invalid_response` 诊断。WorkBuddy 保留现有静默处理捕获错误和有效空上下文输出。关闭捕获后不写入 Source。安装共享指导不会启用新的事件，也不会赋予捕获权限。

# Reference-level explanation

## 确认契约

仅在以下条件全部满足时确认捕获：

1. 响应恰好包含 `status`、`source` 和 `position`。
2. `status` 等于 `accepted`。
3. `source` 恰好等于 `{"name": "content", "source_id": REQUEST_SOURCE_ID}`，不含额外字段。
4. `position` 是正整数；布尔值不被接受。

使用请求中的精确 Source 身份。同一次捕获的重复接受仍然有效。当前 API 没有独立的 `duplicate` 状态。该验证位于捕获响应边界，即使未开启可选刷新也会执行；它不改变 Server 授权或现有幂等坐标。

## 原生与共享职责

| 关注点 | 共享契约 | 原生所有者 |
| --- | --- | --- |
| 回执 | 精确确认 Source 身份与位置 | 请求构造与传输 |
| 召回 | 严格预备上下文结构；空结果有效 | 输入选择与注入格式 |
| 捕获 | 捕获授权和确认控制检查点 | 原生资格与会话、事件身份 |
| 预算 | 网络步骤消耗现有绝对截止时间 | 已验证的宿主超时与请求上限 |
| 失败 | 自动操作非阻塞；未知写入不自动重放 | 原生诊断通道与跨调用节流 |
| Scope 与认证 | 使用已解析绑定和现有认证连接 | 宿主配置与权限交互 |

共享 JSON 案例表描述公开回执输入和是否允许检查点，不固定私有调用顺序、不要求所有宿主提供相同事件，也不以 Client 调用替换原生传输。隔离插件继续在没有 PowerContext 包时加载。Codex 保留现有 Pydantic 运行时要求，WorkBuddy 保留现有类型兼容依赖。

存在原生事件 ID 时继续使用。没有原生事件身份时，现有内容哈希回退可能在同一会话内合并相同提示文本；明确保留这一限制。本修正不重新分配已有 Source 身份，也不虚构跨宿主重试协议。

## 兼容性与恢复

生成的 HTTP 契约保持不变。符合契约的 Server 响应保持现有行为。缺字段、多字段、类型错误或引用其他 Source 的响应在检查点前被拒绝。无需持久配置或用户数据迁移。Source 接受并不代表已生成 Memory 或完成合成。取消、响应丢失或验证失败使写入结果不确定，重放前需要现有显式检查。

## Acceptance

`tests/fixtures/hooks/capture_receipts.json` 通过三个宿主的实际适配器子进程和回环 HTTP 服务执行。案例覆盖有效和重复回执、缺状态或字段、拒绝状态、Source 身份或类型不匹配、布尔值或零位置、多余字段、格式错误的 JSON、响应丢失以及关闭捕获。断言保护退出行为、空注入、确认后的检查点、稳定重试身份和不含内容的原生诊断。Claude 还在不加载 site-packages 时执行；其他宿主已有依赖保持不变。

现有适配器行为测试继续保护来源与授权。真实 Codex、Claude Code 和 WorkBuddy 回调资格与直接子进程执行分开记录。平台或延迟结论需要明确原生版本和测量证据。

# Drawbacks

三个隔离适配器保留少量验证代码副本。共享案例能检测行为偏差，但不消除源码重复。严格验证按当前 API 契约拒绝额外字段；不兼容的 Server 变化需要显式更新契约。

# Rationale and alternatives

共享 Client 引擎会仅为小型协议边界验证扩大隔离插件的部署要求。JSONL worker 还增加协议、进程、取消和版本所有权；本修正没有测量到这样的需要。窄范围验证与共享可执行案例保留现有分发并检验同一规则。资源分发可在未来共享隔离辅助代码，而不改变宿主生命周期。

# Prior art

[#1691](https://github.com/oceanbase/powercontext/pull/1691) 是已关闭、未合并的实现材料，不是已建立的共享运行时契约。其审查说明描述性 Hook 列表不能成为授权，目标差异必须保持明确。[插件可见诊断](../development/plugin-contract.md) 继续约束已覆盖宿主。

# Unresolved questions

回执验证无需额外的跨宿主事件或协议决策。未来共享执行器需要在迁移前明确依赖、截止时间和取消兼容性。

# Future possibilities

仅在调用方失败语义和部署依赖相同后提取执行。任何 worker 先在一个真实宿主中取得资格，再考虑扩大迁移。其他适配器需要同一不变量时，按其原生契约和支持证据扩展确认案例。
