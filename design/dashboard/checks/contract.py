# /// script
# requires-python = ">=3.11"
# dependencies = ["jsonschema==4.25.1", "pyyaml==6.0.2"]
# ///
"""Validate the prototype's API projections against the checked-in contract."""

import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = yaml.safe_load((ROOT.parents[1] / "openapi/powercontext.yaml").read_text())
DATA = json.loads((ROOT / "content.json").read_text())


def validate(value: object, schema: dict[str, Any]) -> None:
    Draft202012Validator({**schema, "components": CONTRACT["components"]}).validate(value)


def model(name: str, value: object) -> None:
    validate(value, {"$ref": f"#/components/schemas/{name}"})


def request(path: str, body: dict[str, Any]) -> None:
    operation = CONTRACT["paths"][path]["post"]
    validate(body, operation["requestBody"]["content"]["application/json"]["schema"])


def check_dataset(data: dict[str, Any]) -> None:
    scope = {"scope_id": data["scope_id"]}
    selection = {"selection": {"mode": "exact", "scope_ids": [data["scope_id"]]}}
    request("/v1/scopes/selection/resolve", selection)
    request("/v1/handoff-reports/get", selection)
    request("/v1/memory/entries/list", scope)
    request("/v1/skill/library", scope)
    for period in ("today", "7d", "30d"):
        request("/v1/stats", {**selection, "period": period})
    experience = data["experience"]
    model("ExperienceProposal", {key: experience[key] for key in ("situation", "action", "outcome", "lesson")})
    skill = data["skill"]
    model(
        "SkillProposal",
        {
            "name": skill["name"],
            "description": skill["description"],
            "instructions": skill["instructions"],
            "validation": skill["validation"],
        },
    )
    for family, artifact_id in (("experience", experience["artifact_id"]), ("skill", skill["artifact_id"])):
        request(
            f"/v1/{family}/get", {**scope, "artifact": {"family": family, "artifact_id": artifact_id, "revision": 1}}
        )
    for position, source in enumerate(data["sources"], 1):
        model(
            "SourceRecord",
            {**scope, **source, "source_type": "content", "position": position, "content_digest": "sha256:" + "0" * 64},
        )
    citation = {"kind": "source", "source_ref": {"name": "content", "source_id": data["sources"][0]["source_id"]}}
    handoff = data["handoff"]
    model(
        "HandoffContent",
        {
            "schema": "powercontext.handoff.v1",
            "objective": handoff["objective"],
            "state": [{"text": text, "citations": [citation]} for text in handoff["state"]],
            "disposition": "continuable",
            "next_action": {"text": handoff["next_action"], "citations": [citation]},
            "omissions": [{"text": handoff["omissions"], "citation": citation}],
        },
    )


if __name__ == "__main__":
    for key in ("payments", "research"):
        check_dataset(DATA[key])
    for path in (
        "/v1/scopes/{scope_id}",
        "/v1/scopes/{scope_id}/artifacts/{family}",
        "/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}",
        "/v1/scopes/{scope_id}/sources/{source_type}/{source_id}",
    ):
        if "get" not in CONTRACT["paths"][path]:
            raise ValueError(path)
    print("Both story datasets and their request projections match the current OpenAPI contract.")
