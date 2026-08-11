---
title: Python Client SDK
description: 使用异步 Python Client 连接运行中的 PowerContext Server。
---

# Python Client SDK

由 Server 管理持久化时，使用 Client SDK：

```python
import asyncio

from powercontext.client import PowerContextClient
from powercontext.http import PrepareContextRequest, RememberMemoryRequest, SearchMemoryRequest


async def main() -> None:
    async with PowerContextClient("http://127.0.0.1:8000") as client:
        await client.remember_memory(
            RememberMemoryRequest(
                scope_id="project:example",
                kind="decision",
                text="保持公开 API 异步化。",
            )
        )
        result = await client.search_memory(
            SearchMemoryRequest(
                scope_id="project:example",
                query="公开 API",
            )
        )
        print([hit.text for hit in result.hits])
        prepared = await client.prepare_context(
            PrepareContextRequest(scope_id="project:example", query="公开 API")
        )
        print(prepared.content)


asyncio.run(main())
```

变更操作的响应包含精确 citation。修订、停用或读取不可变条目版本时，应把该 citation 传回 Server。

Client 还提供 `generate_experience`、`propose_experience`、`get_experience`、`generate_skill`、
`propose_skill`、`get_skill`、`scan_external_skills`、`list_external_skills`、
`resolve_external_skill`、`import_external_skill` 和 Candidate Review 方法。Review 写操作都要求
`expected_version`。批准响应返回精确的 Experience 或 managed Skill `result_artifact`；pending 和 rejected
Candidate 不是 Artifact Revision。

`generate_experience` 和 `generate_skill` 接收调用方显式选择的精确 Source 与 Artifact 引用，返回一个 pending
Candidate 或明确的 `no_op`。replacement 必须把精确 target 同时放入 `artifact_refs` 并设置 `target`。managed
Skill generation 需要声明一种 provenance 形态：

- `experience`：至少引用一个已批准的 Experience，也可以附带精确 Source；
- `source`：只引用精确 Source，包括官方资料或人工材料；
- `usage`：引用精确 target Skill 和有界 usage Source。

这些 generation operation 需要配置 `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL`。已经拥有完整类型化内容和
精确证据的人或 integration 仍可使用低阶 `propose_*` operation。两条路径都不能自行批准 Candidate。

召回、审核与导出行为见 [Artifact 生命周期](../explanation/artifact-lifecycle.md)。生成的
[Python API 参考](../../modules.md)列出所有公开 Client 类型和方法。
