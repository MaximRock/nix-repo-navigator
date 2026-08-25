"""Cached result of a dynamic ``nix eval`` (SQLite ``option_values``)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ValueStatus(StrEnum):
    """Lifecycle status of a cached eval result."""

    ok = "ok"
    unresolved = "unresolved"
    error = "error"
    stale = "stale"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class OptionValue(BaseModel):
    """Row of the SQLite ``option_values`` cache.

    ``key`` = xxhash(expr + flake_rev + affected_files_hash).
    """

    key: str
    expr: str
    value_json: Any | None = None
    status: ValueStatus = ValueStatus.ok
    error: str | None = None
    computed_at: datetime = Field(default_factory=_utcnow)
    source_rev: str | None = None
