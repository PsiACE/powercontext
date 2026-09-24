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

"""Repository-owned target catalog shared by setup, doctor, and distribution."""

from __future__ import annotations

import tomllib
from pathlib import PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .resources import ASSETS


class SetupSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    module: str | None = None
    options: tuple[str, ...] = ()
    verifies: bool = False
    package: str | None = None
    executable: str | None = None
    settings: dict[str, str] = Field(default_factory=dict)


class Hook(BaseModel):
    """Bind an adapter handler to a native registration event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event: str = Field(min_length=1)
    handler: str = Field(min_length=1)
    matcher: str | None = None


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    target: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    kind: Literal["agent_host", "framework_adapter", "evaluation_harness", "connector", "portable"]
    language: Literal["python", "typescript", "none"]
    source: str
    label: str
    order: int
    setup: SetupSpec
    resource_dir: str = "."
    hook_file: str | None = None
    hook_manifest: str | None = None
    root_variable: str = Field(default="PLUGIN_ROOT", pattern=r"^[A-Z][A-Z0-9_]*$")
    command_style: Literal["shell", "argv"] = "shell"
    hooks: tuple[Hook, ...] = ()

    @model_validator(mode="after")
    def unique_hooks(self) -> Self:
        bindings = {(hook.event, hook.handler, hook.matcher) for hook in self.hooks}
        if len(bindings) != len(self.hooks):
            raise ValueError("duplicate hook registration")  # noqa: TRY003
        if self.language == "typescript":
            if len({hook.event for hook in self.hooks}) != len(self.hooks):
                raise ValueError("duplicate native hook event")  # noqa: TRY003
            if any(hook.matcher is not None or ":" not in hook.handler for hook in self.hooks):
                raise ValueError("native hooks require source:handler bindings without matchers")  # noqa: TRY003
        return self

    @field_validator("source", "resource_dir", "hook_file", "hook_manifest")
    @classmethod
    def relative_path(cls, value: str | None) -> str | None:
        if value is not None:
            path = PurePosixPath(value)
            if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
                raise ValueError("paths must be contained relative POSIX paths")  # noqa: TRY003
        return value


def load_targets() -> list[Target]:
    targets = []
    for path in sorted(ASSETS.joinpath("targets").glob("*.toml")):
        target = Target.model_validate(tomllib.loads(path.read_text()))
        if path.stem != target.target:
            message = f"invalid target source or identity: {path}"
            raise ValueError(message)
        targets.append(target)
    return sorted(targets, key=lambda target: target.order)
