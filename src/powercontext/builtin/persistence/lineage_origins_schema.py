# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Add the recorded-origin relation to storage created before it existed.

This is an additive startup convergence step, the same mechanism the Runtime already
uses for later nullable columns: nothing here rewrites a historical row, and a
database that already has the relation is left untouched. No lineage payload is read
to decide anything.
"""

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateTable

from powercontext.builtin.persistence.tables import ARTIFACT_LINEAGE_ORIGINS_TABLE


async def ensure_artifact_lineage_origins_schema(connection: AsyncConnection, /) -> None:
    """Create the relation only when absent; an existing shape is never altered."""

    name = ARTIFACT_LINEAGE_ORIGINS_TABLE.name
    tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    if name in tables:
        return
    declaration = str(CreateTable(ARTIFACT_LINEAGE_ORIGINS_TABLE).compile(dialect=connection.dialect))
    await connection.exec_driver_sql(declaration)


__all__ = ["ensure_artifact_lineage_origins_schema"]
