# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Keep the team/project memory example runnable against the current public API.

The example is what a reader copies, so it is exercised rather than described: its own functions run through an
Access-enforced Server over ASGI transport. It protects the example's routes, request bodies, and expected status
codes from drifting when the contract changes. It is not deployment acceptance; a real deployment, a real model,
and a real membership provider are outside this test.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType

import httpx

from powercontext.builtin.authz import PrincipalRef
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, McpConfig, ServerSettings
from tests.e2e.server_storage import prepare_server_database

ADMIN = PrincipalRef(type="service", id="admin")
MAINTAINER = PrincipalRef(type="user", id="mara")
READER = PrincipalRef(type="user", id="dee")
OUTSIDER = PrincipalRef(type="user", id="outsider")
DEPLOYMENT_ID = "team-project-memory-example"
PROJECT_TITLE = "Orders project"
EXAMPLE = Path(__file__).parents[2] / "examples" / "team_project_memory" / "scenario.py"


class _DeterministicProfileGenerator:
    """A local stand-in for the configured model, with no claim about real generation quality."""

    config_id = "team-project-memory-example"

    async def generate(self, value):
        assert value.sources, "the Server must supply the admitted Source window"
        return f"# {PROJECT_TITLE}\n\n- Retry with an idempotency key on the new channel.\n"


def _load_example() -> ModuleType:
    name = "team_project_memory_example"
    spec = importlib.util.spec_from_file_location(name, EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before execution: the example declares dataclasses, and their annotations resolve through
    # ``sys.modules[cls.__module__]`` during class creation.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def test_team_project_memory_example_stays_runnable(tmp_path: Path) -> None:
    example = _load_example()
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'example.db'}")

    async def scenario() -> None:
        await asyncio.to_thread(prepare_server_database, ServerSettings(database=database))
        async with open_builtin_access_control(
            database, bootstrap_administrators=(ADMIN,), deployment_id=DEPLOYMENT_ID
        ) as access:

            @asynccontextmanager
            async def caller(principal: PrincipalRef) -> AsyncIterator[ModuleType]:
                app = create_server_app(
                    settings=ServerSettings(
                        database=database,
                        access=AccessControlConfig(mode="enforced", deployment_id=DEPLOYMENT_ID),
                        mcp=McpConfig(enabled=False),
                    ),
                    access_control=access,
                    authentication_provider=StaticBearerAuthenticationProvider("example-token", principal),
                    profile_generator=_DeterministicProfileGenerator(),
                )
                async with (
                    app.router.lifespan_context(app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://testserver",
                        headers={"Authorization": "Bearer example-token"},
                    ) as client,
                ):
                    yield example.Caller(principal.id, client)

            async with (
                caller(ADMIN) as admin,
                caller(MAINTAINER) as maintainer,
                caller(READER) as reader,
                caller(OUTSIDER) as outsider,
            ):
                project_id = await example.choose_project_scope(admin, PROJECT_TITLE)

                for user_id, role in (
                    ("alice", "scope.contributor"),
                    ("bob", "scope.contributor"),
                    ("mara", "scope.contributor"),
                    ("mara", "scope.admin"),
                    ("mara", "scope.reviewer"),
                    ("dee", "scope.viewer"),
                ):
                    await example.grant(admin, project_id, user_id, role)

                await example.contribute(
                    admin,
                    project_id,
                    "A callback retried on the new channel succeeded with an idempotency key.",
                )
                revision = await example.maintain_conclusion(maintainer, project_id)
                assert revision == 1

                context = await example.read_conclusion(reader, project_id, revision)
                assert "idempotency key" in context

                corrected = await example.correct_conclusion(
                    maintainer, project_id, "Check transaction status before retrying on the old channel."
                )
                assert corrected == revision + 1

                skill = await example.publish_team_skill(maintainer, project_id)
                assert skill["family"] == "skill"

                refused = await outsider.client.get(f"/v1/scopes/{project_id}/artifacts/profile/profile")
                assert refused.status_code == 403

    asyncio.run(scenario())
