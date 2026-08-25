"""Per-file state used by the incremental update engine."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FileState(BaseModel):
    """Row of the SQLite ``file_state`` table (spec v3 §5.3)."""

    path: str
    lang: str
    content_hash: str
    ast_hash: str | None = None
    merkle_hash: str | None = None
    dirty: bool = False
    last_parsed: datetime | None = None
    detail_level: Literal["full", "summary", "stub"] | None = Field(
        default=None,
        description="How deeply this file was parsed (Tier-dependent).",
    )
