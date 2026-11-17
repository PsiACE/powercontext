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

"""Rebuild the publication table so a retained copy survives its origin.

A consuming Scope keeps a copy it was authorized to retain, plus the exact origin
address and digest. The source columns therefore stay a content-free address
rather than a foreign key: purging the origin's own content must neither fail on
this row nor cascade it away. The target-side key is kept, so the retained copy
is still not purgeable through the same path.

This is a forward, non-additive constraint change. It rewrites one table to drop
one foreign key; every row, address, digest and idempotency key is copied
unchanged, and nothing is renamed or reinterpreted.
"""

import json
from pathlib import Path

from alembic import op

revision = "v1_5_0_008"
down_revision = "v1_5_0_007"


def upgrade():
    resource = Path(__file__).parents[1] / "schemas/sqlite-v1_5_0_008.json"
    for statement in json.loads(resource.read_text(encoding="utf-8"))["statements"]:
        op.get_bind().exec_driver_sql(statement)
