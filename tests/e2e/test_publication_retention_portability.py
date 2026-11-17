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

"""A retained publication copy survives its origin's Scope and stays portable.

The consuming Scope keeps the copy it was authorized to retain plus the exact origin address and digest.
Deleting the origin through the real Scope deletion pipeline must therefore leave the survivor readable and
exportable, and the resulting archive must restore on its own -- while a genuinely malformed publication is
still refused.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.portability.bundle import BundleFormatError
from powercontext.builtin.publication import ArtifactPublicationRequest
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig
from powercontext.builtin.runtime.composition import open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft, ScopeSelection
from powercontext.server.settings import ServerSettings
from tests.e2e.server_storage import prepare_server_database

_LESSON: dict[str, Any] = {"situation": "s", "action": "a", "outcome": "o", "lesson": "Retry with one idempotency key."}


def _config(database: Path) -> BuiltinConfig:
    return BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"), runtime=RuntimeConfig())


async def _allow(_scope_ids: tuple[str, ...]) -> None:
    """The test's own authorization callback; production supplies a real decision."""


def test_retained_copy_survives_origin_scope_deletion_and_round_trips(tmp_path: Path) -> None:
    database = tmp_path / "retention.db"
    other = tmp_path / "restored.db"
    bundle = tmp_path / "history.pcbundle"

    async def produce() -> tuple[str, str]:
        await asyncio.to_thread(
            prepare_server_database, ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))
        )
        async with open_builtin_runtime(_config(database)) as runtime:
            assert runtime.scopes is not None and runtime.publications is not None
            work = await runtime.scopes.create(ScopeDraft(title="Alice work", summary="s", idempotency_key="w"))
            project = await runtime.scopes.create(ScopeDraft(title="Orders", summary="s", idempotency_key="p"))
            created = await runtime.records.for_scope(work.scope_id).create_artifact(
                "experience", ArtifactWrite(content=_LESSON)
            )
            published = await runtime.publications.publish(
                ArtifactPublicationRequest(
                    source=ArtifactAddress(
                        scope_id=work.scope_id,
                        artifact=ArtifactRef(family="experience", artifact_id=created.artifact_id, revision=1),
                    ),
                    target_scope_id=project.scope_id,
                    idempotency_key="orders-copy",
                )
            )

            # Delete the origin through the real pipeline, not a direct SQL purge.
            deletions = runtime.scope_deletions
            assert deletions is not None
            preview = await deletions.preview(ScopeSelection(mode="exact", scope_ids=(work.scope_id,)))
            receipt = await deletions.confirm(
                ScopeSelection(mode="exact", scope_ids=(work.scope_id,)),
                preview.membership_digest,
                single_scope=True,
            )
            finished = await deletions.cleanup(receipt.operation_id)
            assert finished.state == "complete"

            # The origin's content and its own metadata are gone; the survivor is not.
            assert await runtime.records.for_scope(project.scope_id).get_artifact(
                "experience", published.target.artifact.artifact_id
            )

            archive = runtime.archive
            assert archive is not None
            await archive.export((project.scope_id,), bundle, authorize=_allow)
            # Inspection raises on an invalid bundle, so a returned result is the validity statement.
            inspection = await archive.inspect(bundle)
            assert inspection.record_count > 0
            assert project.scope_id in inspection.scopes
            return project.scope_id, published.target.artifact.artifact_id

    async def consume(scope_id: str, artifact_id: str) -> None:
        await asyncio.to_thread(
            prepare_server_database, ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{other}"))
        )
        async with open_builtin_runtime(_config(other)) as runtime:
            archive = runtime.archive
            assert archive is not None
            restored = await archive.restore(bundle)
            assert restored.status == "ready"
            survivor = await runtime.records.for_scope(scope_id).get_artifact("experience", artifact_id)
            assert survivor.revision == 1

    scope_id, artifact_id = asyncio.run(produce())
    asyncio.run(consume(scope_id, artifact_id))


def test_retained_publication_requires_a_complete_address() -> None:
    """The relaxed checks must not become a blanket waiver of missing references."""

    from powercontext.builtin.portability.bundle import _publication_source_retained

    # The publication column is a fixed 64-character field and every publishing path writes a bare
    # sha256 hex, so that is the only accepted representation.
    digest = "0643ba9e191e2870036eec1d91f6e8d1052a8e1e9c4ea3b8c7a8c0baaacd40d0"
    complete = {
        "source_scope_id": "scp_a",
        "source_family": "experience",
        "source_artifact_id": "art_a",
        "source_revision": 2,
        "content_digest": digest,
    }
    assert _publication_source_retained(complete) is True

    for broken in (
        {**complete, "source_scope_id": ""},
        {**complete, "source_artifact_id": ""},
        {**complete, "source_family": ""},
        {**complete, "source_revision": 0},
        {**complete, "source_revision": True},
        {k: v for k, v in complete.items() if k != "content_digest"},
        {**complete, "content_digest": "not-a-digest"},
        {**complete, "content_digest": "abc"},
        {**complete, "content_digest": f"sha256:{digest}"},
        {**complete, "content_digest": f"sha256: {digest}"},
        {**complete, "content_digest": digest.upper()},
        {**complete, "content_digest": digest[:-1]},
    ):
        assert _publication_source_retained(broken) is False, broken


def _resign(bundle: Path, mutate: Callable[[dict[str, Any], dict[str, Any]], None]) -> Path:
    """Tamper one publication and its retained copy, then re-seal so only the claim is suspicious.

    Every container digest is recomputed, so the archive stays structurally valid and the only thing wrong
    is what it claims about its own provenance or bytes. A bundle checksum proves internal integrity; it can
    never prove that an external origin really had this address, so that is not what these cases test.
    """

    import hashlib
    import json
    import zipfile

    def canonical(value: object) -> bytes:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        records = [json.loads(line) for line in archive.read("records.ndjson").decode().splitlines() if line.strip()]

    publication = next(record for record in records if record.get("record_type") == "artifact_publication")
    target_key = (
        publication["identity"]["target_scope_id"],
        publication["identity"]["target_family"],
        publication["identity"]["target_artifact_id"],
        publication["identity"]["target_revision"],
    )
    retained = next(
        record
        for record in records
        if record.get("record_type") == "artifact_revision"
        and (
            record["identity"]["scope_id"],
            record["identity"]["family"],
            record["identity"]["artifact_id"],
            record["identity"]["revision"],
        )
        == target_key
    )
    mutate(publication, retained)
    for record in (publication, retained):
        # A record digest covers exactly its identity, payload, type and schema version -- not itself.
        sealed = {key: record[key] for key in ("record_type", "schema_version", "identity", "payload")}
        record["digest"] = "sha256:" + hashlib.sha256(canonical(sealed)).hexdigest()

    body = b"\n".join(canonical(record) for record in records) + b"\n"
    accumulator = hashlib.sha256()
    for index, record in enumerate(records):
        if index:
            accumulator.update(b"\n")
        accumulator.update(record["digest"].encode("ascii"))
    manifest["total_digest"] = "sha256:" + accumulator.hexdigest()

    target = bundle.with_name(bundle.stem + "-resigned.pcbundle")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json", json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        archive.writestr("records.ndjson", body)
    return target


def test_tampered_retained_addresses_are_refused(tmp_path: Path) -> None:
    """A re-sealed archive cannot smuggle a fabricated or mismatched origin past the validator."""

    database = tmp_path / "origin.db"
    bundle = tmp_path / "origin.pcbundle"

    async def build() -> None:
        await asyncio.to_thread(
            prepare_server_database, ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))
        )
        async with open_builtin_runtime(_config(database)) as runtime:
            assert runtime.scopes is not None and runtime.publications is not None
            archive = runtime.archive
            assert archive is not None
            work = await runtime.scopes.create(ScopeDraft(title="Alice work", summary="s", idempotency_key="w"))
            project = await runtime.scopes.create(ScopeDraft(title="Orders", summary="s", idempotency_key="p"))
            created = await runtime.records.for_scope(work.scope_id).create_artifact(
                "experience", ArtifactWrite(content=_LESSON)
            )
            await runtime.publications.publish(
                ArtifactPublicationRequest(
                    source=ArtifactAddress(
                        scope_id=work.scope_id,
                        artifact=ArtifactRef(family="experience", artifact_id=created.artifact_id, revision=1),
                    ),
                    target_scope_id=project.scope_id,
                    idempotency_key="orders-copy",
                )
            )
            # The origin Scope is still present in this archive, so its address must agree with it.
            await archive.export((work.scope_id, project.scope_id), bundle, authorize=_allow)

    asyncio.run(build())

    def fabricated(record: dict[str, Any], _retained: dict[str, Any]) -> None:
        record["payload"]["source_scope_id"] = "scp_0000000000000000000000phantom"
        record["payload"]["source_revision"] = 7

    def wrong_revision(record: dict[str, Any], _retained: dict[str, Any]) -> None:
        record["payload"]["source_revision"] = 5

    def live_wrong_digest(record: dict[str, Any], _retained: dict[str, Any]) -> None:
        # A valid-looking digest that matches neither the origin's bytes nor the copy's.
        record["payload"]["content_digest"] = "0" * 64

    def swapped_body(_record: dict[str, Any], retained: dict[str, Any]) -> None:
        # The row keeps a genuine digest, but the retained copy carries different bytes than it names.
        body = retained["payload"]["content"]
        raw = base64.b64decode(body["base64"])
        retained["payload"]["content"] = {"base64": base64.b64encode(raw + b" swapped").decode("ascii")}

    def bad_digest(record: dict[str, Any], _retained: dict[str, Any]) -> None:
        record["payload"]["content_digest"] = "not-a-digest"

    # A publication's digest must equal the bytes archived for the origin AND the retained copy, so each
    # tampered archive below breaks one specific guarantee and must be refused by both public entry points.
    for mutate in (wrong_revision, bad_digest, live_wrong_digest, swapped_body):
        # Each mutator breaks one guarantee: a revision the archive lacks, a non-digest, a digest
        # matching neither side, and a retained body that contradicts its digest.
        tampered = _resign(bundle, mutate)
        for entry in ("validate", "restore"):
            with pytest.raises(BundleFormatError):
                asyncio.run(_attempt(tmp_path, tampered, entry))


async def _attempt(root: Path, path: Path, step: str) -> None:
    """Run one public archive entry point against its own fresh database."""

    database = root / (step + ".db")
    await asyncio.to_thread(
        prepare_server_database, ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))
    )
    async with open_builtin_runtime(_config(database)) as runtime:
        archive = runtime.archive
        assert archive is not None
        if step == "validate":
            await archive.validate(path)
        else:
            await archive.restore(path)
