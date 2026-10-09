"""Application configuration, loaded from the environment and/or a .env file."""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every variable is prefixed ``LEMBAS_``."""

    model_config = SettingsConfigDict(
        env_prefix="LEMBAS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    secret_key: str = Field(default="")
    # Set when no LEMBAS_SECRET_KEY was supplied and one had to be invented.
    # main.py warns about it at startup; see the validator below.
    secret_key_is_ephemeral: bool = Field(default=False, exclude=True)

    data_dir: Path = Path("./data")

    host: str = "127.0.0.1"
    port: int = 8080
    reload: bool = False
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    allow_signup: bool = True
    default_theme: Literal["moria", "shire"] = "moria"
    session_ttl: int = 60 * 60 * 24 * 30
    request_timeout: float = 300.0

    # What `/admin/updates` compares against and the helper deploys.
    #
    # Deployment configuration and deliberately not instance settings: they
    # decide what code runs on this machine, and a value a web administrator
    # could edit would turn "you may deploy the channel" into "you may deploy
    # anything". `deploy/install.sh` writes both beside the rest.
    #
    # `stable` follows the newest release tag; `edge` follows the branch tip.
    # Stable is the default because a branch tip is not a release -- following
    # one means deploying whatever was pushed five minutes ago, which is right
    # for whoever is building this and wrong for whoever is running it.
    update_channel: Literal["stable", "edge"] = "stable"
    # Which branch is fetched, and which one `edge` follows. Stable needs it too:
    # a fetch has to name a branch, and tags come down with it.
    update_branch: str = "main"

    @model_validator(mode="after")
    def _generate_secret_if_absent(self) -> Settings:
        # A generated key lets `lembas-server serve` work with no configuration at all,
        # but it changes on every restart: sessions drop and stored API keys
        # become unreadable. Flagged so startup can warn. Never use in anger.
        if not self.secret_key:
            self.secret_key = secrets.token_urlsafe(48)
            self.secret_key_is_ephemeral = True
        return self

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lembas.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Cached singleton so config is parsed once per process."""
    return Settings()


settings = get_settings()
