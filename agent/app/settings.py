from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Python agent service.

    Values are read from environment variables with fallbacks documented
    in the field definitions. Secrets must come from the environment; defaults
    are only provided for development convenience.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # FastAPI
    api_host: str = Field(default="127.0.0.1")
    api_port: int = Field(default=8005)

    # tools/ service
    tools_base_url: str = Field(default="http://127.0.0.1:4320")
    internal_auth_secret: str = Field(...)

    # LLM (OpenAI-compatible local proxy)
    openai_base_url: str = Field(
        default="http://127.0.0.1:8123/v1",
        validation_alias="OPENAI_COMPAT_BASE_URL",
    )
    openai_api_key: str = Field(
        default="local-dev-key",
        validation_alias="OPENAI_COMPAT_API_KEY",
    )
    openai_model: str = Field(
        default="gpt-5.4",
        validation_alias="OPENAI_COMPAT_MODEL",
    )

    # Worker
    worker_prepare_concurrency: int = Field(default=2, validation_alias="WORKER_PREPARE_CONCURRENCY")

    # Persistence
    repo_root: Path = Field(default=Path(__file__).resolve().parents[2])
    sqlite_path: Path = Field(default=Path("../automation/agent.db"))
    profile_path: Path = Field(default=Path("profile/profile.json"))
    raw_profile_path: Path = Field(default=Path("profile/raw_profile.json"))
    profile_answers_path: Path = Field(default=Path("profile/profile_answers.json"))
    profile_upload_dir: Path = Field(default=Path("automation/profile_uploads"))

    @property
    def resolved_sqlite_path(self) -> Path:
        if str(self.sqlite_path) == ":memory:":
            return Path(":memory:")
        if self.sqlite_path.is_absolute():
            return self.sqlite_path
        return (self.repo_root / self.sqlite_path).resolve()

    @property
    def resolved_profile_path(self) -> Path:
        if self.profile_path.is_absolute():
            return self.profile_path
        return (self.repo_root / self.profile_path).resolve()

    @property
    def resolved_raw_profile_path(self) -> Path:
        if self.raw_profile_path.is_absolute():
            return self.raw_profile_path
        return (self.repo_root / self.raw_profile_path).resolve()

    @property
    def resolved_profile_upload_dir(self) -> Path:
        if self.profile_upload_dir.is_absolute():
            return self.profile_upload_dir
        return (self.repo_root / self.profile_upload_dir).resolve()

    @property
    def resolved_profile_answers_path(self) -> Path:
        if self.profile_answers_path.is_absolute():
            return self.profile_answers_path
        return (self.repo_root / self.profile_answers_path).resolve()

    @property
    def resolved_target_profile_path(self) -> Path:
        source = self.resolved_profile_path
        if source.suffix:
            return source.with_name(f"{source.stem}.canonical{source.suffix}")
        return source.with_name(f"{source.name}.canonical.json")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
