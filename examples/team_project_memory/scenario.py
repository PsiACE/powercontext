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

"""Team and project memory on ordinary Scopes, over the public HTTP API.

Each failure names the workflow step and the observation that contradicted it, so the message belongs at the
step rather than in an exception class.

Run against a Server that enforces Access, with one authenticated identity per role:

    POWERCONTEXT_EXAMPLE_BASE_URL=https://pc.example \
    POWERCONTEXT_EXAMPLE_ADMIN_TOKEN=... \
    POWERCONTEXT_EXAMPLE_MAINTAINER_TOKEN=... \
    POWERCONTEXT_EXAMPLE_READER_TOKEN=... \
    POWERCONTEXT_EXAMPLE_OUTSIDER_TOKEN=... \
    python examples/team_project_memory/scenario.py

The script performs the workflow an application performs: resolve the Scope that owns a project's collective
record, let two contributors supply work records, produce and review one official conclusion, have a designated
member read it with its exact address, correct it when a later fact arrives, keep a project Skill separate from
personal memory, and confirm that a member without a grant reads nothing.

Every step uses only documented public routes. It is a demonstration, not an acceptance harness: rerunning it
creates new Scopes and Artifacts rather than reusing the previous run's.
"""

# ruff: noqa: TRY003

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Any

import httpx

PROJECT_REFERENCE = {"kind": "project", "value": "orders-example"}
PROFILE_PATH = "artifacts/profile/profile"


class ExampleFailure(RuntimeError):
    """Report one step that did not behave as this workflow documents.

    The message is assembled here so every raise site passes facts, not prose.
    """

    def __init__(self, step: str, detail: str) -> None:
        self.step = step
        self.detail = detail
        super().__init__(f"{step}: {detail}")


@dataclass(frozen=True)
class Caller:
    """One authenticated identity, never a credential derived from a request body."""

    name: str
    client: httpx.AsyncClient

    async def ok(self, response: httpx.Response, expected: int, step: str) -> httpx.Response:
        if response.status_code != expected:
            raise ExampleFailure(step, f"{self.name} received {response.status_code}: {response.text}")
        return response


def _client(base_url: str, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )


async def choose_project_scope(admin: Caller, project_title: str) -> str:
    """Create the Scope that owns the project's collective record and expose its business mapping."""

    created = await admin.ok(
        await admin.client.post(
            "/v1/scopes",
            json={
                "title": project_title,
                "summary": "Conclusion this project maintains for its members",
                "idempotency_key": f"example-{project_title}",
                "external_references": [PROJECT_REFERENCE],
            },
        ),
        201,
        "create project Scope",
    )
    scope_id = created.json()["scope_id"]

    # Any application that stored this mapping elsewhere can still find the Scope by business reference.
    found = await admin.ok(
        await admin.client.get(
            "/v1/scopes",
            params={
                "query": PROJECT_REFERENCE["value"],
                "query_field": "external_reference_value",
                "external_reference_kind": PROJECT_REFERENCE["kind"],
            },
        ),
        200,
        "find project Scope by reference",
    )
    if scope_id not in {item["scope_id"] for item in found.json()["items"]}:
        raise ExampleFailure("choose the project Scope", "the business reference resolves elsewhere")

    bound = await admin.ok(
        await admin.client.put(
            "/v1/scope-bindings",
            json={
                "key": {
                    "integration": "project",
                    "kind": PROJECT_REFERENCE["kind"],
                    "external_id": PROJECT_REFERENCE["value"],
                },
                "scope_id": scope_id,
            },
        ),
        200,
        "bind project identity",
    )
    if bound.json()["scope_id"] != scope_id:
        raise ExampleFailure("bind the project identity", "the binding names another Scope")
    return scope_id


async def grant(admin: Caller, scope_id: str, user_id: str, role: str) -> None:
    await admin.ok(
        await admin.client.post(
            "/v1/access/bindings/create",
            json={
                "subject": {"type": "user", "id": user_id},
                "resource": {"type": "scope", "scope_id": scope_id},
                "role": role,
                "idempotency_key": f"example-{user_id}-{role}-{scope_id}",
            },
        ),
        201,
        f"grant {role} to {user_id}",
    )


async def contribute(contributor: Caller, scope_id: str, record: str) -> None:
    """Supply one authorized observation to the Scope that owns the conclusion."""

    captured = await contributor.ok(
        await contributor.client.post(f"/v1/scopes/{scope_id}/sources", json={"content": record}),
        201,
        "contribute an observation",
    )
    body = captured.json()
    reread = await contributor.ok(
        await contributor.client.get(f"/v1/scopes/{scope_id}/sources/{body['source_type']}/{body['source_id']}"),
        200,
        "read the observation back",
    )
    if reread.json()["content_digest"] != body["content_digest"]:
        raise ExampleFailure("contribute an observation", "the readback digest does not match")


async def maintain_conclusion(maintainer: Caller, scope_id: str) -> int:
    """Produce and approve the official conclusion, then return its exact revision."""

    await maintainer.ok(
        await maintainer.client.put(
            f"/v1/scopes/{scope_id}/profile-policy",
            json={"generation_enabled": True, "activation_mode": "review_required", "expected_version": 0},
        ),
        200,
        "require review for the project conclusion",
    )
    pending = await maintainer.ok(
        await maintainer.client.post("/v1/profile/flush", json={"scope_id": scope_id}),
        200,
        "evaluate the project journal",
    )
    candidate_id = pending.json()["candidate_id"]
    if candidate_id is None:
        raise ExampleFailure("evaluate the project journal", "reviewed generation produced no Candidate")

    # Nothing is official until a reviewer decides.
    absent = await maintainer.client.get(f"/v1/scopes/{scope_id}/{PROFILE_PATH}")
    if absent.status_code != 404:
        raise ExampleFailure("evaluate the project journal", f"a pending Candidate published {absent.status_code}")

    approved = await maintainer.ok(
        await maintainer.client.post(
            "/v1/candidates/approve",
            json={"scope_id": scope_id, "candidate_id": candidate_id, "expected_version": 1},
        ),
        200,
        "approve the project conclusion",
    )
    result = approved.json()["result_artifact"]
    return int(result["revision"])


async def read_conclusion(reader: Caller, scope_id: str, revision: int) -> str:
    """Read the conclusion the way an Agent turn receives it, including its exact address."""

    prepared = await reader.ok(
        await reader.client.post(
            "/v1/context/prepare",
            json={
                "scope_id": scope_id,
                "query": "may we retry a callback after a timeout",
                "assembly": {"sections": [{"family": "profile", "limit": 1}]},
                "include_receipt": True,
            },
        ),
        200,
        "prepare the member's context",
    )
    body = prepared.json()
    if body["status"] != "ready":
        raise ExampleFailure("prepare the member context", "no conclusion was delivered")
    address = f'family="profile", id="profile", revision={revision}'
    if address not in body["content"]:
        raise ExampleFailure("prepare the member context", "the address does not name the revision")

    selected = {
        (item["artifact_address"]["scope_id"], item["artifact_address"]["artifact"]["revision"])
        for item in body["receipt"]["selected"]
    }
    if (scope_id, revision) not in selected:
        raise ExampleFailure("prepare the member context", "the receipt omits the delivered address")
    return body["content"]


async def correct_conclusion(maintainer: Caller, scope_id: str, later_fact: str) -> int:
    """Revisit the conclusion after a new fact, and refuse to approve a stale proposal.

    A reassessment may legitimately conclude that nothing should change, so ``no_change`` is reported rather
    than treated as a failure. The stale-proposal check only applies when a revision was actually proposed.
    """

    await contribute(maintainer, scope_id, later_fact)
    head = await maintainer.ok(
        await maintainer.client.get(f"/v1/scopes/{scope_id}/{PROFILE_PATH}"),
        200,
        "read the current conclusion",
    )
    etag = head.headers["ETag"]
    baseline = head.json()["revision"]

    stale = await maintainer.ok(
        await maintainer.client.post("/v1/profile/flush", json={"scope_id": scope_id}),
        200,
        "reassess after the new fact",
    )
    stale_candidate = stale.json()["candidate_id"]
    if stale_candidate is None:
        print(f"reassessment concluded no change against revision {baseline}")

    # A maintainer correction lands first, so any pending baseline is no longer the head.
    corrected = await maintainer.ok(
        await maintainer.client.put(
            f"/v1/scopes/{scope_id}/{PROFILE_PATH}",
            headers={"If-Match": etag},
            json={"content": {"content": f"# Orders project\n\n- {later_fact}\n"}},
        ),
        200,
        "correct the conclusion",
    )
    if corrected.json()["revision"] != baseline + 1:
        raise ExampleFailure("correct the conclusion", "the next revision was not created")

    if stale_candidate is None:
        return corrected.json()["revision"]

    refused = await maintainer.client.post(
        "/v1/candidates/approve",
        json={"scope_id": scope_id, "candidate_id": stale_candidate, "expected_version": 1},
    )
    if refused.status_code != 409:
        raise ExampleFailure("approve the stale proposal", f"it returned {refused.status_code}")

    # A stale proposal stays decidable: it can be rejected, and only a new run may succeed it.
    await maintainer.ok(
        await maintainer.client.post(
            "/v1/candidates/reject",
            json={
                "scope_id": scope_id,
                "candidate_id": stale_candidate,
                "expected_version": 1,
                "reason": "A maintainer corrected the conclusion first; reassess against the new baseline.",
            },
        ),
        200,
        "reject the stale proposal",
    )
    return corrected.json()["revision"]


async def publish_team_skill(maintainer: Caller, scope_id: str) -> dict[str, Any]:
    """Give members a capability that does not depend on anyone's personal Profile."""

    created = await maintainer.ok(
        await maintainer.client.post(
            f"/v1/scopes/{scope_id}/artifacts",
            json={
                "family": "skill",
                "content": {
                    "name": "incident-retry-triage",
                    "description": "Decide whether a failed callback may be retried.",
                    "instructions": (
                        "Check which channel failed. Retry with an idempotency key only on the new channel; "
                        "on the old channel, inspect transaction status first."
                    ),
                    "validation": ["Confirm the channel before retrying."],
                },
            },
        ),
        201,
        "publish the project Skill",
    )
    return created.json()


async def main() -> int:
    base_url = os.environ.get("POWERCONTEXT_EXAMPLE_BASE_URL")
    configured = {
        "admin": os.environ.get("POWERCONTEXT_EXAMPLE_ADMIN_TOKEN"),
        "maintainer": os.environ.get("POWERCONTEXT_EXAMPLE_MAINTAINER_TOKEN"),
        "reader": os.environ.get("POWERCONTEXT_EXAMPLE_READER_TOKEN"),
        "outsider": os.environ.get("POWERCONTEXT_EXAMPLE_OUTSIDER_TOKEN"),
    }
    if base_url is None or any(token is None for token in configured.values()):
        print(
            "Set POWERCONTEXT_EXAMPLE_BASE_URL and the ADMIN/MAINTAINER/READER/OUTSIDER tokens.",
            file=sys.stderr,
        )
        return 2
    tokens: dict[str, str] = {name: token for name, token in configured.items() if token is not None}

    clients = {name: _client(base_url, token) for name, token in tokens.items()}
    callers = {name: Caller(name, client) for name, client in clients.items()}
    try:
        project_id = await choose_project_scope(callers["admin"], "Orders project")
        print(f"project Scope: {project_id}")

        await grant(callers["admin"], project_id, "alice", "scope.contributor")
        await grant(callers["admin"], project_id, "bob", "scope.contributor")
        await grant(callers["admin"], project_id, "mara", "scope.contributor")
        # Configuring the Profile policy is Scope administration, separate from deciding a Candidate.
        await grant(callers["admin"], project_id, "mara", "scope.admin")
        await grant(callers["admin"], project_id, "mara", "scope.reviewer")
        await grant(callers["admin"], project_id, "dee", "scope.viewer")

        await contribute(
            callers["admin"], project_id, "A callback retried on the new channel succeeded with an idempotency key."
        )
        revision = await maintain_conclusion(callers["maintainer"], project_id)
        print(f"official conclusion: revision {revision}")

        context = await read_conclusion(callers["reader"], project_id, revision)
        print(f"a member read {len(context)} bytes of prepared context")

        final = await correct_conclusion(
            callers["maintainer"], project_id, "Check transaction status before retrying on the old channel."
        )
        print(f"corrected conclusion: revision {final}")

        skill = await publish_team_skill(callers["maintainer"], project_id)
        print(f"project Skill: {skill['family']}/{skill['artifact_id']}")

        denied = await callers["outsider"].client.get(f"/v1/scopes/{project_id}/{PROFILE_PATH}")
        if denied.status_code != 403:
            raise ExampleFailure("read without a grant", f"it returned {denied.status_code}")
        print("an ungranted member was refused, as intended")
        return 0
    finally:
        for client in clients.values():
            await client.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
