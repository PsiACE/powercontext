"""Validated configuration for the fixed Bub end-to-end harness."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


class HarnessSettings(BaseSettings):
    """Typed environment contract shared by every executing harness mode."""

    model_config = SettingsConfigDict(
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    server_base_url: HttpUrl | None = Field(default=None, validation_alias="POWERCONTEXT_E2E_SERVER_URL")
    workspace_root: Path = Field(
        default=Path(".powercontext/e2e-workspace"),
        validation_alias="POWERCONTEXT_E2E_WORKSPACE",
    )
    database: str = Field(default="unknown", validation_alias="POWERCONTEXT_E2E_DATABASE")
    repository: Path = Field(default_factory=_repository_root, validation_alias="POWERCONTEXT_E2E_REPOSITORY")
    codex_home: Path = Field(default_factory=lambda: Path.home() / ".codex", validation_alias="CODEX_HOME")
    commit: str | None = Field(default=None, validation_alias="GITHUB_SHA")

    bub_model: str | None = Field(default=None, validation_alias="BUB_MODEL")
    bub_api_key: SecretStr | None = Field(default=None, validation_alias="BUB_API_KEY")
    bub_api_base: HttpUrl | None = Field(default=None, validation_alias="BUB_API_BASE")
    judge_model: str | None = Field(default=None, validation_alias="POWERCONTEXT_E2E_JUDGE_MODEL")
    generation_model: str | None = Field(
        default=None,
        validation_alias="POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL",
    )
    embedding_profile: str | None = Field(
        default=None,
        validation_alias="POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID",
    )

    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    deepseek_api_key: SecretStr | None = Field(default=None, validation_alias="DEEPSEEK_API_KEY")
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openrouter_api_key: SecretStr | None = Field(default=None, validation_alias="OPENROUTER_API_KEY")
    client_api_token: SecretStr | None = Field(
        default=None,
        validation_alias="POWERCONTEXT_CLIENT_API_TOKEN",
    )
    server_auth_token: SecretStr | None = Field(
        default=None,
        validation_alias="POWERCONTEXT_SERVER_AUTH_TOKEN",
    )
    server_database_url: SecretStr | None = Field(
        default=None,
        validation_alias="POWERCONTEXT_SERVER_DATABASE_URL",
    )
    agent_proxy_url: SecretStr | None = Field(
        default=None,
        validation_alias="POWERCONTEXT_E2E_AGENT_PROXY_URL",
    )

    def server_url(self, default: str) -> str:
        """Return the configured host URL or a task-specific default."""

        return str(self.server_base_url or default).rstrip("/")

    def workspace_for(self, run_id: str) -> Path:
        return self.workspace_root.expanduser().resolve() / run_id

    def repository_path(self) -> Path:
        return self.repository.expanduser().resolve()

    def codex_auth_path(self) -> Path:
        return self.codex_home.expanduser() / "auth.json"

    def commit_id(self) -> str:
        if self.commit:
            return self.commit
        git = shutil.which("git")
        if git is None:
            return "unknown"
        completed = subprocess.run(  # noqa: S603 - executable is resolved by shutil.which
            [git, "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return completed.stdout.strip() if completed.returncode == 0 else "unknown"

    def evidence_secrets(self) -> tuple[str, ...]:
        values = {
            secret.get_secret_value()
            for secret in (
                self.anthropic_api_key,
                self.bub_api_key,
                self.deepseek_api_key,
                self.openai_api_key,
                self.openrouter_api_key,
                self.client_api_token,
                self.server_auth_token,
                self.server_database_url,
                self.agent_proxy_url,
            )
            if secret is not None and secret.get_secret_value()
        }
        return tuple(sorted(values, key=lambda value: (-len(value), value)))
