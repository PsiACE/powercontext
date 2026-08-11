---
title: CLI
description: PowerContext CLI 命令及其运行边界。
---

# CLI

CLI 用于配置集成、诊断安装、控制本地 Server，以及通过已配置的 Server 操作内容。

## 配置和诊断

```text
powercontext setup codex
powercontext doctor
powercontext server run
powercontext ready
powercontext capabilities
```

可选的 `server` role 会增加 `powercontext server run`，但不会在 CLI 中创建第二套内容 profile。安装与进程
管理见[安装和运行](../how-to/install-and-run.md)。

## Candidate 审核

```text
powercontext candidate list --scope-id project:example
powercontext candidate list --scope-id project:example --family skill
powercontext candidate show --scope-id project:example CANDIDATE_ID
powercontext candidate approve --scope-id project:example --expected-version 1 CANDIDATE_ID
powercontext candidate reject --scope-id project:example --expected-version 1 --reason unsupported CANDIDATE_ID
powercontext candidate revise experience --scope-id project:example --expected-version 1 \
  --situation SITUATION --action ACTION --outcome OUTCOME --lesson LESSON CANDIDATE_ID
powercontext candidate revise skill --scope-id project:example --expected-version 1 \
  --name NAME --description DESCRIPTION --instructions-file instructions.md --validation CHECK CANDIDATE_ID
```

审核写操作要求当前 `expected_version`，避免过期的审核结果覆盖更新后的内容。

## Experience 和 Skill 命令

```text
powercontext experience generate --scope-id project:example --source-ref content/SOURCE_ID
powercontext skill generate --scope-id project:example --origin experience \
  --artifact-ref experience/EXPERIENCE_ID@REVISION
powercontext skill show --scope-id project:example --revision 1 SKILL_ID
powercontext skill export --target codex --scope-id project:example --revision 1 \
  --destination .agents/skills/example-skill SKILL_ID
```

Generation 和 revision 命令通过可重复的 `--source-ref TYPE/ID` 与
`--artifact-ref FAMILY/ID@REVISION` 接收精确引用，不读取序列化请求文件。
`--target FAMILY/ID@REVISION` 会自动把 target 纳入 Artifact 证据。修订 managed Skill 时，内联
`--instructions` 和 `--instructions-file` 必须且只能选择一个；`--validation` 可以重复提供。

生成、批准和导出的边界见 [Artifact 生命周期](../explanation/artifact-lifecycle.md)。

## 外部 Skill

```text
powercontext external-skill scan --scope-id project:example
powercontext external-skill list --scope-id project:example
powercontext external-skill resolve --scope-id project:example --fingerprint SHA256 EXTERNAL_SKILL_ID
powercontext external-skill import --scope-id project:example --fingerprint SHA256 \
  --mode import EXTERNAL_SKILL_ID
```

扫描和解析不会安装 package。导入会创建 managed Skill Candidate，并保留原来的 external registration。
