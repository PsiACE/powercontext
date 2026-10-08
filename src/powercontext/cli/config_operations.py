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


"""Typed, private, revision-checked partial changes to existing .env resources."""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, EnvSettingsSource
from typing_extensions import override

from powercontext.client.settings import ClientSettings, normalize_server_url
from powercontext.local_config import (
    ConfigurationConflictError,
    configuration_lock,
    publish_configuration,
    read_configuration,
)
from powercontext_operations.env_file import EnvironmentFileError, parse_environment


class ConfigurationInputError(ValueError):
    """A public request is invalid; messages contain field names, never input values."""


class ConfigurationChange(BaseModel):
    """A strict public intent document, independent of .env serialization."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    schema_version: Literal[1]
    target: Literal["server", "client"]
    resource: str = Field(min_length=1)
    base_revision: str = Field(min_length=1)
    set: dict[str, JsonValue] = Field(default_factory=dict)
    unset: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ConfigurationField:
    path: str
    environment: str
    info: FieldInfo

    @property
    def adapter(self) -> TypeAdapter[Any]:
        return TypeAdapter(self.info.rebuild_annotation())

    @property
    def sensitive(self) -> bool:
        schema = self.adapter.json_schema()
        return bool(schema.get("writeOnly")) or any(item.get("writeOnly") for item in schema.get("anyOf", []))


def configuration_fields(target: str) -> dict[str, ConfigurationField]:
    """Expose an explicit scalar subset, deriving its types from the owning models."""
    if target == "server":
        from powercontext.server.settings import ServerSettings

        owner = ServerSettings
        groups = ("http", "dashboard", "mcp", "auth", "access", "logging")
        paths = [name + "." + field for name in groups for field in owner.model_fields[name].annotation.model_fields]
        paths += ["public_url", "allow_insecure_http", "allow_unauthenticated_non_loopback"]
        prefix = "POWERCONTEXT_SERVER_"
    elif target == "client":
        owner = ClientSettings
        paths = ["server_url", "allow_insecure_http", "timeout", "api_token"]
        prefix = "POWERCONTEXT_CLIENT_"
    else:
        raise ConfigurationInputError("Target must be server or client")  # noqa: TRY003
    fields = {}
    for path in paths:
        parts = path.split(".")
        info = owner.model_fields[parts[0]]
        if len(parts) == 2:
            info = info.annotation.model_fields[parts[1]]
        fields[path] = ConfigurationField(path, prefix + path.upper().replace(".", "_"), info)
    return fields


def configuration_schema(target: str = "server") -> dict[str, Any]:
    """Describe supported writable fields without opening a resource or reading secrets."""
    return {
        "schema_version": 1,
        "target": target,
        "format": "env",
        "fields": {
            name: {
                "environment": field.environment,
                "schema": field.adapter.json_schema(),
                "default": field.info.default,
                "sensitive": field.sensitive,
                "change_input": "controlled_from_env_or_from_file" if field.sensitive else "typed_json",
                "activation": "restart_required" if target == "server" else "next_invocation",
            }
            for name, field in configuration_fields(target).items()
        },
    }


def inspect_configuration(resource: Path, target: str = "server") -> dict[str, Any]:
    """Inspect only public fields; unknown assignments and credentials never become output values."""
    path = resource.expanduser().resolve()
    content, revision = read_configuration(path)
    values = parse_environment(content.decode("utf-8")) if content is not None else {}
    return _view(path, target, values, revision)


def _view(path: Path, target: str, values: Mapping[str, str], revision: str) -> dict[str, Any]:
    fields = configuration_fields(target)
    view = {}
    for name, field in fields.items():
        value = values.get(field.environment)
        item: dict[str, Any] = {
            "source": "file" if value is not None else "default",
            "environment": field.environment,
            "runtime_override": field.environment in os.environ,
        }
        if field.sensitive:
            item["present"] = bool(value)
        elif value is None:
            item["value"] = field.info.default
        else:
            try:
                item["value"] = field.adapter.dump_python(field.adapter.validate_python(value), mode="json")
            except ValidationError:
                item["state"] = "invalid"
        view[name] = item
    known = {field.environment for field in fields.values()}
    return {
        "schema_version": 1,
        "target": target,
        "resource": str(path),
        "revision": revision,
        "fields": view,
        "hidden_assignment_count": len(values.keys() - known),
        "activation": "unknown",
    }


def read_change(content: str) -> ConfigurationChange:
    """Parse intent while excluding Pydantic input/context from all public errors."""
    try:
        document = ConfigurationChange.model_validate_json(content)
    except ValidationError as error:
        raise ConfigurationInputError("Invalid configuration change document") from error  # noqa: TRY003
    if set(document.set) & set(document.unset) or len(set(document.unset)) != len(document.unset):
        raise ConfigurationInputError("Set and unset fields must be distinct and unset names unique")  # noqa: TRY003
    unknown = (set(document.set) | set(document.unset)) - configuration_fields(document.target).keys()
    if unknown:
        raise ConfigurationInputError("Change contains unsupported fields; use config schema")  # noqa: TRY003
    return document


def plan_configuration(document: ConfigurationChange) -> dict[str, Any]:
    """Revalidate a request and return a private preview without writes or network calls."""
    document = read_change(document.model_dump_json())
    path = Path(document.resource).expanduser().resolve()
    content, revision = read_configuration(path)
    if revision != document.base_revision:
        raise ConfigurationConflictError("Configuration changed; inspect it again before planning")  # noqa: TRY003
    updated, before, after = _candidate(content, document)
    return {
        "schema_version": 1,
        "target": document.target,
        "resource": str(path),
        "base_revision": revision,
        "changes": sorted(set(document.set) | set(document.unset)),
        "before": _view(path, document.target, before, revision)["fields"],
        "after": _view(path, document.target, after, revision)["fields"],
        "activation": "restart_required" if document.target == "server" else "next_invocation",
        "checks": [{"kind": "static", "passed": True}],
        "would_change": updated != (content or b""),
    }


def apply_configuration(document: ConfigurationChange) -> dict[str, Any]:
    """Revalidate, compare under the OS lock, then publish one complete private resource."""
    document = read_change(document.model_dump_json())
    path = Path(document.resource).expanduser().resolve()
    with configuration_lock(path):
        content, revision = read_configuration(path)
        if revision != document.base_revision:
            raise ConfigurationConflictError("Configuration changed; inspect it again before applying")  # noqa: TRY003
        updated, _, values = _candidate(content, document)
        saved = publish_configuration(path, updated, expected_revision=revision)
        result = _view(path, document.target, values, saved)
        result.update(
            saved_revision=saved,
            activation="restart_required" if document.target == "server" else "next_invocation",
            checks=[{"kind": "static", "passed": True}],
            credential_storage="private_env_file",
        )
        return result


def _candidate(content: bytes | None, document: ConfigurationChange) -> tuple[bytes, dict[str, str], dict[str, str]]:
    text = content.decode("utf-8") if content is not None else ""
    before = parse_environment(text)
    fields = configuration_fields(document.target)
    additions = {}
    for name, value in document.set.items():
        field = fields[name]
        if field.sensitive:
            value = _credential_input(value)
        try:
            validated = field.adapter.validate_python(value, strict=True)
        except ValidationError as error:
            raise ConfigurationInputError("Invalid value for " + name) from error
        if validated is None:
            raise ConfigurationInputError("Use unset to remove an optional value")  # noqa: TRY003
        rendered = field.adapter.dump_python(validated, mode="json")
        additions[field.environment] = str(rendered).lower() if isinstance(rendered, bool) else str(rendered)
        if field.sensitive:
            additions[field.environment] = validated.get_secret_value()
    removed = {fields[name].environment for name in document.unset}
    after = {name: value for name, value in before.items() if name not in removed} | additions
    _validate_candidate(document.target, after)
    return _edit_environment(text, additions, removed).encode(), before, after


def _credential_input(value: JsonValue) -> str:
    if not isinstance(value, dict) or len(value) != 1:
        raise ConfigurationInputError("Sensitive fields require from_env or from_file controlled input")  # noqa: TRY003
    if isinstance(value.get("from_env"), str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value["from_env"]):
        secret = os.environ.get(value["from_env"])
    elif isinstance(value.get("from_file"), str):
        path = Path(value["from_file"]).expanduser().resolve(strict=True)
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise ConfigurationInputError("Credential input file must have private permissions")  # noqa: TRY003
        secret = path.read_text(encoding="utf-8").removesuffix("\n")
    else:
        raise ConfigurationInputError("Sensitive fields require from_env or from_file controlled input")  # noqa: TRY003
    if not secret or secret in {"<redacted>", "**********"} or "…" in secret or "\x00" in secret:
        raise ConfigurationInputError("Credential input is missing or masked")  # noqa: TRY003
    return secret


class _DocumentEnvironmentSource(EnvSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings], values: Mapping[str, str]):
        self._document_values = {name.casefold(): value for name, value in values.items()}
        super().__init__(settings_cls)

    @override
    def _load_env_vars(self) -> Mapping[str, str | None]:
        return self._document_values


def _validate_candidate(target: str, values: Mapping[str, str]) -> None:
    try:
        if target == "client":
            fields = configuration_fields(target)
            typed = {
                name: field.adapter.validate_python(values[field.environment])
                if field.environment in values
                else field.info.default
                for name, field in fields.items()
            }
            normalize_server_url(typed["server_url"], allow_insecure_http=typed["allow_insecure_http"])
            return
        from powercontext.server.settings import ServerSettings

        class DocumentSettings(ServerSettings):
            @classmethod
            @override
            def settings_customise_sources(
                cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
            ):
                return (_DocumentEnvironmentSource(settings_cls, values),)

        DocumentSettings()
    except (ValueError, ValidationError) as error:
        raise ConfigurationInputError("Candidate settings failed static validation") from error  # noqa: TRY003


def _edit_environment(text: str, additions: Mapping[str, str], removed: set[str]) -> str:
    """Replace only changed logical assignments, retaining every unrelated physical line."""
    pending = dict(additions)
    lines = iter(text.splitlines(keepends=True))
    result = []
    for line in lines:
        block = line
        while True:
            try:
                parsed = parse_environment(block)
                break
            except EnvironmentFileError as error:
                if str(error.__cause__) != "No closing quotation":
                    raise
                try:
                    block += next(lines)
                except StopIteration:
                    raise error from None
        if not parsed:
            result.append(block)
            continue
        name = next(iter(parsed))
        if name in pending:
            result.append(name + "=" + shlex.quote(pending.pop(name)) + "\n")
        elif name not in removed:
            result.append(block)
    retained = "".join(result)
    if pending and retained and not retained.endswith("\n"):
        retained += "\n"
    return retained + "".join(name + "=" + shlex.quote(value) + "\n" for name, value in pending.items())


def validate_resource(resource: Path, target: str = "server") -> dict[str, Any]:
    """Validate this file alone without network probes or process activation claims."""
    path = resource.expanduser().resolve()
    content, revision = read_configuration(path)
    values = parse_environment(content.decode("utf-8")) if content is not None else {}
    _validate_candidate(target, values)
    return {
        "schema_version": 1,
        "target": target,
        "resource": str(path),
        "revision": revision,
        "checks": [{"kind": "static", "passed": True}],
        "activation": "unknown",
    }
