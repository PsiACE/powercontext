---
title: Artifact 生命周期
description: 了解 Experience 和 Skill Candidate 如何成为已批准的 Artifact。
---

# Artifact 生命周期

PowerContext 将模型辅助生成、人工审核、已批准的 Artifact Revision、召回和导出分开。生成 Candidate 不等于
批准、安装、召回或执行。

## Experience 生成

Experience 经 Review 批准后，确定性的 `searchable_text` 会写入现有通用 Artifact head，并进入 backend
可重建 FTS 索引。此后，它可以在同一 scope 内被 `PreparedContext` 召回。pending/rejected Candidate、所有
managed Skill 和历史 Experience Revision 不会进入召回结果。

Integration 可以把已完成任务采集为 metadata 含 `"kind": "task-outcome"` 的 Content Source。启用
Experience schedule 后，APScheduler 会扫描有上限的 Source window，并让配置好的 schema-bound pipeline
生成 situation、action、outcome 和 lesson proposal。每条 proposal 都引用精确 Source，并以 pending
Experience Candidate 进入 Review Inbox。

Experience 孵化使用独立于 Memory extraction 的持久化 Source cursor。Candidate 写入和 cursor 推进会在同一
事务提交。generation 或写入失败时，该 window 会留给下次重试。普通 Prompt Source 不是 Task Outcome，
不会进入这个 job。

后台流程止于审核边界。它不会批准 Experience、把 pending 内容放入 `PreparedContext`、派生 managed Skill、
把 Skill 导出给 Codex，或执行 instructions。只有支撑它的 Experience 获批后，Skill authoring 和导出才作为
显式步骤继续。

## Managed Skill 批准与导出

配置好的生成器可通过 `generate_skill` 生成完整 managed Skill。已经拥有完整类型化内容的人或 integration
可通过 `propose_skill` 提交。proposal 包括名称、用于发现的描述、instructions、validation，以及精确的
Source 或 Artifact lineage。在 reviewer 批准精确 Candidate version 之前，它始终只是 Candidate。

批准会创建不可变的 Skill Revision，但不会安装 Skill，也不会授予执行权限。要让 Codex 使用某个已批准
Revision，必须通过 `skill export --target codex` 将它显式导出到新的代码库级或用户级 Skill 目录。该命令生成
`SKILL.md` 和 `powercontext.json`；manifest 会记录精确 Artifact 引用和渲染内容哈希。目标目录已存在时命令会
拒绝覆盖，更新需要另一次明确的导出。

Codex 可以发现 `.agents/skills/<name>/SKILL.md` 下的代码库级导出。Artifact Revision 始终是内容权威。目录
只是 host-local projection，可以从同一个 Revision 重建。

## 外部 Skill

外部 Skill 的原始本地 package 始终是内容权威。显式配置 Codex roots 后，Server 可以扫描 scope-local、
可重建的 Registry，并记录名称、描述、provider、Agent kind、host、installation scope、locator 和整个 package
的 fingerprint。只有同一 package 在已配置 host 上仍可读且 fingerprint 一致时，exact resolve 才成功。它不会
安装 package，也不会回退到其他版本。

Discovery 不进入 Review。显式调用 `import_external_skill` 并提供精确 identity 与 fingerprint 后，Runtime
才会把有界 `SKILL.md` 快照采集为 Source evidence，并让已配置模型提出新的 managed Skill Candidate。
`mode=import` 与 `mode=fork` 记录调用方意图。两者都必须经 Review 批准后才产生新的 managed identity，且不会
修改 external registration。package 中的脚本和 assets 不会复制进 managed Artifact。

## Authority 与门禁

| Surface | 内容权威 | 模型门禁 | Review 门禁 | 当前可用方式 |
| --- | --- | --- | --- | --- |
| 外部 Agent-native Skill | 原始 package | scan/list/resolve 不需要；import/fork 需要 | discovery 不需要；import/fork 后需要 | host-local Registry 和 exact resolve |
| Experience | 精确 approved Artifact Revision | generate/evolve 需要；类型化 `propose` 不需要 | 需要 | exact read 与 `PreparedContext` approved-head FTS recall |
| managed Skill | 精确 approved Artifact Revision | generate/evolve/import/fork 需要；类型化 `propose` 不需要 | 需要 | exact read 与显式 Codex projection |
| Codex projection | 对应的 managed Skill Revision | 不需要 | 不增加额外 Review | 可重建的 host-local copy |
