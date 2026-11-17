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

"""Durable rule versions and pinned-run readback.

Rows join the caller's transaction. A rule version is append-only; the head row
carries the mutable current version and status. A run keeps its whole record, so
a retry reads exactly what was pinned instead of resolving today's revisions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.codec import dump_model, load_model, stored_bytes
from powercontext.builtin.persistence.errors import InvalidStoredColumnError
from powercontext.builtin.persistence.tables import (
    SHARE_ACCEPTANCES_TABLE,
    SHARE_RULE_HEADS_TABLE,
    SHARE_RULE_VERSIONS_TABLE,
    SHARE_RUNS_TABLE,
)
from powercontext.builtin.sharing.models import (
    ShareAcceptance,
    ShareAcceptanceConflictError,
    ShareAcceptanceSide,
    ShareRule,
    ShareRuleStatus,
    ShareRuleVersionConflictError,
    ShareRun,
    ShareRunAlreadyBoundError,
    ShareRunNotFoundError,
    ShareRunStateError,
)

_SIDE = TypeAdapter(ShareAcceptanceSide)


def _side(stored: object, /) -> ShareAcceptanceSide:
    """Decode one stored acceptance side, rejecting anything this domain cannot accept."""

    try:
        return _SIDE.validate_python(stored, strict=True)
    except ValidationError:
        raise InvalidStoredColumnError("side", "'origin' or 'target'") from None


def ticks(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000)


def from_ticks(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1_000_000, tz=UTC)


class ShareRuleRepository:
    """Read and write rule head/version rows inside the caller's transaction."""

    async def head(
        self, connection: AsyncConnection, scope_id: str, rule_id: str, /, *, for_update: bool = False
    ) -> ShareRule | None:
        """Read the current version together with the head's own mutable state.

        Invalidation changes only the head, so the accepted version payload keeps
        what was agreed while readback reports the current status.
        """

        statement = (
            select(
                SHARE_RULE_VERSIONS_TABLE.c.payload,
                SHARE_RULE_HEADS_TABLE.c.status,
                SHARE_RULE_HEADS_TABLE.c.updated_at,
            )
            .join(
                SHARE_RULE_HEADS_TABLE,
                (SHARE_RULE_HEADS_TABLE.c.scope_id == SHARE_RULE_VERSIONS_TABLE.c.scope_id)
                & (SHARE_RULE_HEADS_TABLE.c.rule_id == SHARE_RULE_VERSIONS_TABLE.c.rule_id)
                & (SHARE_RULE_HEADS_TABLE.c.current_version == SHARE_RULE_VERSIONS_TABLE.c.version),
            )
            .where(
                SHARE_RULE_HEADS_TABLE.c.scope_id == scope_id,
                SHARE_RULE_HEADS_TABLE.c.rule_id == rule_id,
            )
        )
        if for_update:
            statement = statement.with_for_update(of=SHARE_RULE_HEADS_TABLE)
        row = (await connection.execute(statement)).one_or_none()
        if row is None:
            return None
        return _rule(row.payload).model_copy(
            update={"status": str(row.status), "updated_at": from_ticks(int(row.updated_at))}
        )

    async def version(
        self, connection: AsyncConnection, scope_id: str, rule_id: str, version: int, /
    ) -> ShareRule | None:
        stored = await connection.scalar(
            select(SHARE_RULE_VERSIONS_TABLE.c.payload).where(
                SHARE_RULE_VERSIONS_TABLE.c.scope_id == scope_id,
                SHARE_RULE_VERSIONS_TABLE.c.rule_id == rule_id,
                SHARE_RULE_VERSIONS_TABLE.c.version == version,
            )
        )
        return None if stored is None else _rule(stored)

    async def list(
        self, connection: AsyncConnection, scope_id: str, /, *, status: ShareRuleStatus | None = None
    ) -> tuple[ShareRule, ...]:
        statement = (
            select(
                SHARE_RULE_VERSIONS_TABLE.c.payload,
                SHARE_RULE_HEADS_TABLE.c.status,
                SHARE_RULE_HEADS_TABLE.c.updated_at,
            )
            .join(
                SHARE_RULE_HEADS_TABLE,
                (SHARE_RULE_HEADS_TABLE.c.scope_id == SHARE_RULE_VERSIONS_TABLE.c.scope_id)
                & (SHARE_RULE_HEADS_TABLE.c.rule_id == SHARE_RULE_VERSIONS_TABLE.c.rule_id)
                & (SHARE_RULE_HEADS_TABLE.c.current_version == SHARE_RULE_VERSIONS_TABLE.c.version),
            )
            .where(SHARE_RULE_HEADS_TABLE.c.scope_id == scope_id)
            .order_by(SHARE_RULE_HEADS_TABLE.c.rule_id)
        )
        if status is not None:
            statement = statement.where(SHARE_RULE_HEADS_TABLE.c.status == status)
        return tuple(
            _rule(row.payload).model_copy(
                update={"status": str(row.status), "updated_at": from_ticks(int(row.updated_at))}
            )
            for row in (await connection.execute(statement)).all()
        )

    async def create(self, connection: AsyncConnection, rule: ShareRule, /) -> ShareRule:
        await connection.execute(
            insert(SHARE_RULE_HEADS_TABLE).values(
                scope_id=rule.scope_id,
                rule_id=rule.rule_id,
                current_version=rule.version,
                status=rule.status,
                updated_at=ticks(rule.updated_at),
            )
        )
        await connection.execute(
            insert(SHARE_RULE_VERSIONS_TABLE).values(
                scope_id=rule.scope_id,
                rule_id=rule.rule_id,
                version=rule.version,
                payload=_rule_payload(rule),
                created_at=ticks(rule.updated_at),
            )
        )
        return rule

    async def append(self, connection: AsyncConnection, rule: ShareRule, expected_version: int, /) -> ShareRule:
        """Add one immutable version and advance the head with a compare-and-set."""

        changed = await connection.execute(
            update(SHARE_RULE_HEADS_TABLE)
            .where(
                SHARE_RULE_HEADS_TABLE.c.scope_id == rule.scope_id,
                SHARE_RULE_HEADS_TABLE.c.rule_id == rule.rule_id,
                SHARE_RULE_HEADS_TABLE.c.current_version == expected_version,
            )
            .values(current_version=rule.version, status=rule.status, updated_at=ticks(rule.updated_at))
        )
        if changed.rowcount != 1:
            current = await self.head(connection, rule.scope_id, rule.rule_id)
            raise ShareRuleVersionConflictError(
                rule.rule_id, expected_version, 0 if current is None else current.version
            )
        await connection.execute(
            insert(SHARE_RULE_VERSIONS_TABLE).values(
                scope_id=rule.scope_id,
                rule_id=rule.rule_id,
                version=rule.version,
                payload=_rule_payload(rule),
                created_at=ticks(rule.updated_at),
            )
        )
        return rule

    async def invalidate(
        self, connection: AsyncConnection, scope_id: str, rule_id: str, expected_version: int, at: datetime, /
    ) -> ShareRule | None:
        """Stop the rule governing future work while retaining its versions."""

        changed = await connection.execute(
            update(SHARE_RULE_HEADS_TABLE)
            .where(
                SHARE_RULE_HEADS_TABLE.c.scope_id == scope_id,
                SHARE_RULE_HEADS_TABLE.c.rule_id == rule_id,
                SHARE_RULE_HEADS_TABLE.c.current_version == expected_version,
            )
            .values(status="invalidated", updated_at=ticks(at))
        )
        if changed.rowcount != 1:
            return None
        return await self.head(connection, scope_id, rule_id)


class ShareRunRepository:
    """Read and write pinned evaluation records inside the caller's transaction."""

    async def notification(
        self, connection: AsyncConnection, scope_id: str, rule_id: str, notification_key: str, /
    ) -> ShareRun | None:
        stored = await connection.scalar(
            select(SHARE_RUNS_TABLE.c.payload).where(
                SHARE_RUNS_TABLE.c.scope_id == scope_id,
                SHARE_RUNS_TABLE.c.rule_id == rule_id,
                SHARE_RUNS_TABLE.c.notification_key == notification_key,
            )
        )
        return None if stored is None else _run(stored)

    async def get(self, connection: AsyncConnection, scope_id: str, run_id: str, /) -> ShareRun | None:
        stored = await connection.scalar(
            select(SHARE_RUNS_TABLE.c.payload).where(
                SHARE_RUNS_TABLE.c.scope_id == scope_id,
                SHARE_RUNS_TABLE.c.run_id == run_id,
            )
        )
        return None if stored is None else _run(stored)

    async def claim_binding(
        self,
        connection: AsyncConnection,
        scope_id: str,
        run_id: str,
        /,
        *,
        dream_run_id: str,
        candidate_id: str | None = None,
    ) -> ShareRun:
        """Bind one pinned run to one execution, first writer wins.

        The guard is a compare-and-swap on the stored payload bytes, so exclusivity does
        not rely on an in-memory lock or a read-then-write sequence: the first writer
        changes the payload, and a competing writer's predicate then matches no row. A
        repeat of the same binding is idempotent; a different one raises. It joins the
        caller's transaction and writes only the payload, so an admitted claim commits or
        rolls back with the caller's own admission and never orphans separately.
        """

        expected = await connection.scalar(
            select(SHARE_RUNS_TABLE.c.payload).where(
                SHARE_RUNS_TABLE.c.scope_id == scope_id,
                SHARE_RUNS_TABLE.c.run_id == run_id,
            )
        )
        if expected is None:
            raise ShareRunNotFoundError(scope_id, run_id)
        current = _run(expected)
        if current.dream_run_id is not None:
            if _same_binding(current, dream_run_id, candidate_id):
                return current
            raise ShareRunAlreadyBoundError(run_id)
        proposed = current.model_copy(
            update={
                "dream_run_id": dream_run_id,
                "candidate_id": candidate_id if candidate_id is not None else current.candidate_id,
            }
        )
        bound = await connection.execute(
            update(SHARE_RUNS_TABLE)
            .where(
                SHARE_RUNS_TABLE.c.scope_id == scope_id,
                SHARE_RUNS_TABLE.c.run_id == run_id,
                SHARE_RUNS_TABLE.c.payload == stored_bytes(expected, column="share_run"),
            )
            .values(payload=_run_payload(proposed), manifest_digest=proposed.digest())
        )
        stored = await self.get(connection, scope_id, run_id)
        if stored is None:
            raise ShareRunNotFoundError(scope_id, run_id)
        if bound.rowcount != 1:
            # A competing writer changed the payload between our read and this update.
            if _same_binding(stored, dream_run_id, candidate_id):
                return stored
            raise ShareRunAlreadyBoundError(run_id)
        return stored

    async def list(
        self,
        connection: AsyncConnection,
        scope_id: str,
        /,
        *,
        rule_id: str | None = None,
        rule_version: int | None = None,
    ) -> tuple[ShareRun, ...]:
        statement = (
            select(SHARE_RUNS_TABLE.c.payload)
            .where(SHARE_RUNS_TABLE.c.scope_id == scope_id)
            .order_by(SHARE_RUNS_TABLE.c.created_at, SHARE_RUNS_TABLE.c.run_id)
        )
        if rule_id is not None:
            statement = statement.where(SHARE_RUNS_TABLE.c.rule_id == rule_id)
        if rule_version is not None:
            # Qualification happens in SQL: a per-version reader never loads other
            # versions' payloads to discard them afterwards.
            statement = statement.where(SHARE_RUNS_TABLE.c.rule_version == rule_version)
        return tuple(_run(stored) for stored in await connection.scalars(statement))

    async def create(self, connection: AsyncConnection, run: ShareRun, /) -> ShareRun:
        """Insert one pinned evaluation, normalizing a duplicate notification key.

        A caller that loses the `(scope_id, rule_id, notification_key)` race returns the
        evaluation already recorded for that key, so duplicate delivery converges on one
        durable run instead of raising a raw constraint error. An unrelated integrity
        failure is re-raised unchanged.
        """

        try:
            await connection.execute(
                insert(SHARE_RUNS_TABLE).values(
                    scope_id=run.scope_id,
                    run_id=run.run_id,
                    rule_id=run.rule_id,
                    rule_version=run.rule_version,
                    state=run.state,
                    outcome=run.outcome,
                    notification_key=run.notification_key,
                    manifest_digest=run.digest(),
                    created_at=ticks(run.created_at),
                    payload=_run_payload(run),
                )
            )
        except IntegrityError:
            stored = (
                None
                if run.notification_key is None
                else await self.notification(connection, run.scope_id, run.rule_id, run.notification_key)
            )
            if stored is None:
                raise
            return stored
        return run

    async def replace(
        self, connection: AsyncConnection, run: ShareRun, /, *, expected_state: str | None = None
    ) -> ShareRun:
        """Rewrite one recorded evaluation without allowing a state regression."""

        statement = update(SHARE_RUNS_TABLE).where(
            SHARE_RUNS_TABLE.c.scope_id == run.scope_id,
            SHARE_RUNS_TABLE.c.run_id == run.run_id,
        )
        if expected_state is not None:
            statement = statement.where(SHARE_RUNS_TABLE.c.state == expected_state)
        changed = await connection.execute(
            statement.values(
                state=run.state,
                outcome=run.outcome,
                manifest_digest=run.digest(),
                payload=_run_payload(run),
            )
        )
        if changed.rowcount != 1:
            current = await self.get(connection, run.scope_id, run.run_id)
            if current is None:
                raise ShareRunNotFoundError(run.scope_id, run.run_id)
            raise ShareRunStateError(run.run_id, current.state)
        return run


def _rule(stored: object, /) -> ShareRule:
    return load_model(ShareRule, stored_bytes(stored, column="share_rule"), kind="share", name="rule")


def _rule_payload(rule: ShareRule) -> bytes:
    return dump_model(rule, kind="share", name="rule")


def _run(stored: object, /) -> ShareRun:
    return load_model(ShareRun, stored_bytes(stored, column="share_run"), kind="share", name="run")


def _same_binding(run: ShareRun, dream_run_id: str, candidate_id: str | None) -> bool:
    """Report whether a run is already bound to exactly this execution."""

    return run.dream_run_id == dream_run_id and (candidate_id is None or run.candidate_id == candidate_id)


def _run_payload(run: ShareRun) -> bytes:
    return dump_model(run, kind="share", name="run")


class ShareAcceptanceRepository:
    """Reserve one authenticated acceptance and read back the recorded sides.

    Identity is reserved at the moment the accepting actor ran the call, so a
    later request cannot attach a different principal to an existing acceptance.
    """

    async def reserve(self, connection: AsyncConnection, acceptance: ShareAcceptance, /) -> ShareAcceptance:
        """Return the durable acceptance for this actor/side, creating it once.

        An acceptance is immutable. Repeating the identical call returns the stored
        row and its original identity, so a caller never receives an id no reader can
        confirm; a different coverage for an already accepted actor/side is refused
        rather than silently discarded.
        """

        existing = await self.find(
            connection, acceptance.scope_id, acceptance.rule_id, acceptance.version, acceptance.actor, acceptance.side
        )
        if existing is not None:
            if existing.covered_scope_ids != acceptance.covered_scope_ids:
                raise ShareAcceptanceConflictError(
                    acceptance.rule_id, acceptance.version, acceptance.actor, acceptance.side
                )
            return existing
        try:
            await connection.execute(
                insert(SHARE_ACCEPTANCES_TABLE).values(
                    scope_id=acceptance.scope_id,
                    rule_id=acceptance.rule_id,
                    version=acceptance.version,
                    actor=acceptance.actor,
                    side=acceptance.side,
                    terms_digest=acceptance.terms_digest,
                    covered_scope_ids=json.dumps(list(acceptance.covered_scope_ids)).encode(),
                    acceptance_id=acceptance.acceptance_id,
                    accepted_at=ticks(acceptance.accepted_at),
                )
            )
        except IntegrityError:
            # A concurrent identical acceptance won the unique key. Read it back so the
            # caller still receives the durable record rather than a fabricated id.
            stored = await self.find(
                connection,
                acceptance.scope_id,
                acceptance.rule_id,
                acceptance.version,
                acceptance.actor,
                acceptance.side,
            )
            if stored is None:
                raise
            if stored.covered_scope_ids != acceptance.covered_scope_ids:
                raise ShareAcceptanceConflictError(
                    acceptance.rule_id, acceptance.version, acceptance.actor, acceptance.side
                ) from None
            return stored
        return acceptance

    async def find(
        self,
        connection: AsyncConnection,
        scope_id: str,
        rule_id: str,
        version: int,
        actor: str,
        side: str,
        /,
    ) -> ShareAcceptance | None:
        row = (
            (
                await connection.execute(
                    select(SHARE_ACCEPTANCES_TABLE).where(
                        SHARE_ACCEPTANCES_TABLE.c.scope_id == scope_id,
                        SHARE_ACCEPTANCES_TABLE.c.rule_id == rule_id,
                        SHARE_ACCEPTANCES_TABLE.c.version == version,
                        SHARE_ACCEPTANCES_TABLE.c.actor == actor,
                        SHARE_ACCEPTANCES_TABLE.c.side == side,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return ShareAcceptance(
            scope_id=str(row["scope_id"]),
            rule_id=str(row["rule_id"]),
            version=int(row["version"]),
            side=_side(row["side"]),
            actor=str(row["actor"]),
            terms_digest=str(row["terms_digest"]),
            covered_scope_ids=tuple(json.loads(bytes(row["covered_scope_ids"]))),
            acceptance_id=str(row["acceptance_id"]),
            accepted_at=from_ticks(int(row["accepted_at"])),
        )

    async def coverage(
        self, connection: AsyncConnection, scope_id: str, rule_id: str, version: int, terms_digest: str, /
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return the accepted origin and audience coverage for these exact terms.

        Coverage is the union over accepting actors. One run is admitted when that
        union covers the origins it reached and the audiences the version declares,
        so two contributors can compose one agreement without one actor approving
        everything.
        """

        rows = (
            await connection.execute(
                select(
                    SHARE_ACCEPTANCES_TABLE.c.side,
                    SHARE_ACCEPTANCES_TABLE.c.covered_scope_ids,
                ).where(
                    SHARE_ACCEPTANCES_TABLE.c.scope_id == scope_id,
                    SHARE_ACCEPTANCES_TABLE.c.rule_id == rule_id,
                    SHARE_ACCEPTANCES_TABLE.c.version == version,
                    SHARE_ACCEPTANCES_TABLE.c.terms_digest == terms_digest,
                )
            )
        ).all()
        origins: set[str] = set()
        audiences: set[str] = set()
        for side, stored in rows:
            covered = json.loads(bytes(stored)) if stored is not None else []
            (origins if side == "origin" else audiences).update(str(value) for value in covered)
        return tuple(sorted(origins)), tuple(sorted(audiences))

    async def for_terms(
        self, connection: AsyncConnection, scope_id: str, rule_id: str, version: int, terms_digest: str, /
    ) -> tuple[ShareAcceptance, ...]:
        """Return the authoritative rows accepted for these exact terms."""

        rows = await self.all(connection, scope_id, rule_id, version)
        return tuple(record for record in rows if record.terms_digest == terms_digest)

    async def get(self, connection: AsyncConnection, scope_id: str, acceptance_id: str, /) -> ShareAcceptance | None:
        """Read one authoritative acceptance row by its identity."""

        row = (
            (
                await connection.execute(
                    select(SHARE_ACCEPTANCES_TABLE).where(
                        SHARE_ACCEPTANCES_TABLE.c.scope_id == scope_id,
                        SHARE_ACCEPTANCES_TABLE.c.acceptance_id == acceptance_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return ShareAcceptance(
            scope_id=str(row["scope_id"]),
            rule_id=str(row["rule_id"]),
            version=int(row["version"]),
            side=_side(row["side"]),
            actor=str(row["actor"]),
            terms_digest=str(row["terms_digest"]),
            covered_scope_ids=tuple(json.loads(bytes(row["covered_scope_ids"]))),
            acceptance_id=str(row["acceptance_id"]),
            accepted_at=from_ticks(int(row["accepted_at"])),
        )

    async def all(
        self, connection: AsyncConnection, scope_id: str, rule_id: str, version: int, /
    ) -> tuple[ShareAcceptance, ...]:
        rows = (
            await connection.execute(
                select(SHARE_ACCEPTANCES_TABLE)
                .where(
                    SHARE_ACCEPTANCES_TABLE.c.scope_id == scope_id,
                    SHARE_ACCEPTANCES_TABLE.c.rule_id == rule_id,
                    SHARE_ACCEPTANCES_TABLE.c.version == version,
                )
                .order_by(SHARE_ACCEPTANCES_TABLE.c.accepted_at, SHARE_ACCEPTANCES_TABLE.c.actor)
            )
        ).mappings()
        return tuple(
            ShareAcceptance(
                scope_id=str(row["scope_id"]),
                rule_id=str(row["rule_id"]),
                version=int(row["version"]),
                side=_side(row["side"]),
                actor=str(row["actor"]),
                terms_digest=str(row["terms_digest"]),
                covered_scope_ids=tuple(json.loads(bytes(row["covered_scope_ids"]))),
                acceptance_id=str(row["acceptance_id"]),
                accepted_at=from_ticks(int(row["accepted_at"])),
            )
            for row in rows
        )
