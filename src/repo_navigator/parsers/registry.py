"""Parser registry: maps file extensions to parsers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from repo_navigator.parsers.base import BaseParser


@dataclass
class LanguageConfig:
    name: str
    extensions: list[str]
    tier: int = 0
    enabled: bool = True


_registry: dict[str, BaseParser] = {}


def register(parser: BaseParser) -> None:
    """Register a parser for its language extensions."""
    _registry[parser.language] = parser


def get_parser_for_file(path: str | Path) -> BaseParser | None:
    """Return the parser for *path*'s extension, or ``None``."""
    ext = Path(path).suffix
    for parser in _registry.values():
        if ext in parser.extensions:
            return parser
    return None


def get_parser_for_language(lang: str) -> BaseParser | None:
    """Return the parser registered for *lang*, or ``None``."""
    return _registry.get(lang)
