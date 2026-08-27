"""Base parser interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from repo_navigator.models.queries import ParseResult


class BaseParser(ABC):
    """Abstract base for all language parsers."""

    language: str
    extensions: list[str]

    @abstractmethod
    def parse(self, path: Path, content: str) -> ParseResult:
        """Parse *content* at *path* and return graph-ready nodes/edges."""
        ...
