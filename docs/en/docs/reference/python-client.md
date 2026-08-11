---
title: Python Client SDK
description: Use the asynchronous Python client with a running PowerContext Server.
---

# Python Client SDK

Use the Client SDK when the Server owns persistence:

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
                text="Keep the public API asynchronous.",
            )
        )
        result = await client.search_memory(
            SearchMemoryRequest(
                scope_id="project:example",
                query="public API",
            )
        )
        print([hit.text for hit in result.hits])
        prepared = await client.prepare_context(
            PrepareContextRequest(scope_id="project:example", query="public API")
        )
        print(prepared.content)


asyncio.run(main())
```

Mutation responses include an exact citation. Pass that citation back when revising, retiring, or reading an
immutable entry version.

The Client also exposes `generate_experience`, `propose_experience`, `get_experience`, `generate_skill`,
`propose_skill`, `get_skill`, `scan_external_skills`, `list_external_skills`, `resolve_external_skill`,
`import_external_skill`, and the Candidate Review methods. Review writes require `expected_version`. Approval returns
the exact Experience or managed Skill `result_artifact`; pending and rejected Candidates are not Artifact revisions.

`generate_experience` and `generate_skill` accept caller-selected exact Source and Artifact references. They return
either one pending Candidate or an explicit `no_op`. A replacement includes its exact target in `artifact_refs` and
sets `target`. Managed Skill generation declares one provenance shape:

- `experience`: at least one approved Experience reference, with optional exact Sources;
- `source`: only exact Source references, including official or human-authored material;
- `usage`: the exact target Skill plus bounded usage Sources.

These generation operations require `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL`. The lower-level `propose_*`
operations remain available to a person or integration that already has complete typed content and exact evidence.
Neither path approves its own Candidate.

See [Artifact lifecycle](../explanation/artifact-lifecycle.md) for recall, review, and export behavior. The generated
[Python API reference](../../modules.md) lists every public Client type and method.
