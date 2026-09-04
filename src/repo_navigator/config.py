"""Runtime configuration (env vars → .env → CLI args)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """All tunables; overridable via ``REPO_NAVIGATOR_*`` env variables."""

    model_config = SettingsConfigDict(
        env_prefix="REPO_NAVIGATOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    root: Path = Field(
        default_factory=Path.cwd,
        description="Repository root to index.",
    )
    plugins: list[str] = Field(
        default_factory=list,
        description="Enabled plugin languages; [] = Nix only.",
    )
    db_path: Path | None = Field(
        default=None,
        description="SQLite path; defaults to <root>/.repo-navigator/repo-navigator.db.",
    )

    budgets: dict[str, int] = Field(
        default_factory=lambda: {"width": 10, "depth": 5, "limit": 10},
        description="Query budgets: width*depth <= 100 enforced by QueryEngine.",
    )
    timeouts: dict[str, float] = Field(
        default_factory=lambda: {"nix_eval": 60, "debounce_ms": 500, "polling_s": 60},
        description="Timeouts for nix eval, watcher debounce and polling fallback.",
    )

    watcher_mode: Literal["auto", "inotify", "polling"] = "auto"
    log_level: str = "INFO"

    @property
    def resolved_db_path(self) -> Path:
        if self.db_path is not None:
            return self.db_path
        return self.root / ".repo-navigator" / "repo-navigator.db"
