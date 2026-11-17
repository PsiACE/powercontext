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

"""A managed database is a real target for the accepted F and D storage.

Revision 009 creates the recorded-origin relation, D's standing-rule tables and the Scope parent index.
This checks that a freshly prepared database exposes them at runtime, and that a database which stopped
at the previous revision plans the upgrade rather than claiming to be complete.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from powercontext.builtin.persistence.migrations.deployment import deployment_runner
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig
from powercontext.builtin.runtime.composition import open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.settings import ServerSettings
from tests.e2e.server_storage import prepare_server_database

_NEW_OBJECTS = {
    "pc_artifact_lineage_origins",
    "pc_share_rule_heads",
    "pc_share_rule_versions",
    "pc_share_runs",
    "pc_share_acceptances",
    "ix_pc_scopes_parent_scope_id",
}


def _config(database: Path) -> BuiltinConfig:
    return BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"), runtime=RuntimeConfig())


def _names(database: Path) -> set[str]:
    connection = sqlite3.connect(database)
    try:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    finally:
        connection.close()


def test_fresh_managed_database_is_a_target_for_f_and_d_storage(tmp_path: Path) -> None:
    database = tmp_path / "fresh.db"

    async def scenario() -> None:
        await asyncio.to_thread(
            prepare_server_database, ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))
        )
        assert _names(database) >= _NEW_OBJECTS
        async with open_builtin_runtime(_config(database)) as runtime:
            # Standing rules are a working capability here, not an absent one.
            assert runtime.share_rules is not None
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Orders", summary="s", idempotency_key="o"))
            # An ordinary write still works alongside the new relations.
            created = await runtime.records.for_scope(scope.scope_id).create_artifact(
                "experience",
                ArtifactWrite(content={"situation": "s", "action": "a", "outcome": "o", "lesson": "l"}),
            )
            assert created.artifact_id

    asyncio.run(scenario())


def test_database_at_the_previous_revision_plans_the_upgrade(tmp_path: Path) -> None:
    """A file that stopped at 008 must report the pending revision, keep its rows, and then be a target."""

    database = tmp_path / "at008.db"

    async def scenario() -> None:
        await asyncio.to_thread(
            prepare_server_database, ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))
        )
        # Reconstruct the previous shape: drop what 009 adds and move the marker back.
        connection = sqlite3.connect(database)
        with connection:
            for name in _NEW_OBJECTS - {"ix_pc_scopes_parent_scope_id"}:
                connection.execute(f"DROP TABLE {name}")
            connection.execute("DROP INDEX IF EXISTS ix_pc_scopes_parent_scope_id")
            connection.execute("UPDATE pc_schema_revision SET version_num = 'v1_5_0_008'")
            connection.execute(
                "INSERT INTO pc_scopes (scope_id,title,summary,scope_id_search,title_search,summary_search,version) "
                "VALUES ('kept','t','s','s','t','s',1)"
            )
        connection.close()
        assert not (_names(database) >= _NEW_OBJECTS)

        runner = deployment_runner(ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}")))
        plan = runner.plan(backup_policy="skip")
        assert plan.state == "migration_required"
        assert plan.revisions == ("v1_5_0_009",)
        result = runner.apply(
            plan_id=plan.plan_id,
            accepted=True,
            maintenance_confirmed=True,
            backup_policy="skip",
            accept_no_backup=True,
        )
        assert result.state == "ready"

        # The existing row survived, the new relations exist, and nothing is inconsistent.
        assert _names(database) >= _NEW_OBJECTS
        connection = sqlite3.connect(database)
        try:
            assert connection.execute("SELECT scope_id FROM pc_scopes").fetchall() == [("kept",)]
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert connection.execute("SELECT version_num FROM pc_schema_revision").fetchone() == ("v1_5_0_009",)
        finally:
            connection.close()

        # Reapplying is a no-op, and the upgraded file is now a full target.
        assert (
            deployment_runner(ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}")))
            .plan(backup_policy="skip")
            .state
            == "ready"
        )
        async with open_builtin_runtime(_config(database)) as runtime:
            assert runtime.share_rules is not None

    asyncio.run(scenario())


def _build_unmanaged(database: Path) -> None:
    """Create a file the way an unmanaged deployment does: tables only, no revision marker."""

    async def build() -> None:
        async with (
            SQLiteProfile.open(SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"), tables=BUILTIN_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            await connection.exec_driver_sql(
                "INSERT INTO pc_scopes "
                "(scope_id,title,summary,scope_id_search,title_search,summary_search,version) "
                "VALUES ('legacy','old','s','s','t','s',1)"
            )

    asyncio.run(build())


_REFUSALS = {"data_conversion_required", "unknown_baseline", "server_migration_unavailable"}


def test_unmanaged_database_is_admitted_or_refused_never_silently_stamped(tmp_path: Path) -> None:
    """An unmanaged file is either provably admitted or clearly refused, and refusal writes nothing."""

    database = tmp_path / "unmanaged.db"
    _build_unmanaged(database)
    assert "pc_schema_revision" not in _names(database)

    runner = deployment_runner(ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}")))
    try:
        plan = runner.plan(backup_policy="skip")
        result = runner.apply(
            plan_id=plan.plan_id,
            accepted=True,
            maintenance_confirmed=True,
            backup_policy="skip",
            accept_no_backup=True,
        )
    except MigrationError as refusal:
        # A shape the registered chain cannot prove is refused instead of coerced, before any write:
        # no revision marker, no new relations, and the original rows untouched.
        assert refusal.code in _REFUSALS, refusal.code
        names = _names(database)
        assert "pc_schema_revision" not in names
        assert "pc_artifact_lineage_origins" not in names
        assert not any(name.startswith("pc_share") for name in names)
        connection = sqlite3.connect(database)
        try:
            assert connection.execute("SELECT scope_id FROM pc_scopes").fetchall() == [("legacy",)]
        finally:
            connection.close()
        return

    # A recognized released shape may proceed, but only after explicit acceptance.
    assert result.state == "ready"
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("SELECT scope_id FROM pc_scopes").fetchall() == [("legacy",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()
