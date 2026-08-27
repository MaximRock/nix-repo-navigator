"""Parser registry: maps file extensions to parsers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, TypeVar

from repo_navigator.models.nodes import NodeType, RawNode
from repo_navigator.models.queries import ParseResult
from repo_navigator.parsers.base import BaseParser

if TYPE_CHECKING:
    from repo_navigator.config import Config
    from repo_navigator.graph.nx_graph import NxGraph

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseParser)


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


def register_language(config: LanguageConfig) -> Callable[[type[T]], type[T]]:
    """Decorator: register a parser class with *config*.

    Example::

        @register_language(LanguageConfig(name="python", extensions=[".py"], tier=1))
        class PythonParser(BaseParser):
            language = "python"
            extensions = [".py"]
            tier = 1
            ...
    """

    def decorator(cls: type[T]) -> type[T]:
        instance = cls()  # type: ignore[call-arg]
        # Override with config values to keep registry authoritative.
        instance.language = config.name  # type: ignore[attr-defined]
        instance.extensions = config.extensions  # type: ignore[attr-defined]
        instance.tier = config.tier  # type: ignore[attr-defined]
        instance.enabled = config.enabled  # type: ignore[attr-defined]
        _registry[config.name] = instance
        return cls

    return decorator


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


def get_all_parsers() -> list[BaseParser]:
    """Return all registered parsers."""
    return list(_registry.values())


def clear_registry() -> None:
    """Clear the registry (used in tests)."""
    _registry.clear()


def should_parse_file(
    path: str | Path,
    graph: NxGraph | None = None,
    config: Config | None = None,
) -> bool:
    """Nix-first rule: should *path* be parsed?

    - Tier 0 (``.nix``) is always parsed.
    - Tier 1-3 is parsed only if the plugin is enabled **and**
      the file is referenced in the graph (``configures``/``generates`` edge)
      **or** its path contains ``.config/``.
    """
    parser = get_parser_for_file(path)
    if parser is None:
        return False

    # Tier 0: always parse (if enabled)
    if getattr(parser, "tier", 0) == 0:
        if not getattr(parser, "enabled", True):
            return False
        return True

    # Tier 1-3: must be enabled
    if not getattr(parser, "enabled", True):
        return False
    if config is not None and hasattr(config, "plugins"):
        if parser.language not in config.plugins:
            return False

    # Nix-first: .config/ in path
    path_obj = Path(path)
    if ".config" in path_obj.parts:
        return True

    # Nix-first: referenced in graph
    if graph is not None:
        path_str = str(path)
        # Direct file node check
        if hasattr(graph, "has_node"):
            if graph.has_node(f"file:{path_str}"):
                return True
            # Also check basename match (module_parser may use short target)
            if graph.has_node(f"file:{path_obj.name}"):
                return True
        # Scan all file nodes for substring match
        try:
            g = graph.get_graph_readonly()
            for node_id in g.nodes:
                if node_id.startswith("file:") and (
                    path_str in node_id or path_obj.name == Path(node_id).name
                ):
                    return True
            # Also check edges of type configures/generates
            for _u, _v, data in g.edges(data=True):
                edges = data.get("edges", {})
                for edge in edges.values():
                    if edge.type.value in ("configures", "generates") and (
                        edge.target == f"file:{path_str}"
                        or edge.target.endswith(f"/{path_obj.name}")
                        or path_str in edge.target
                    ):
                        return True
        except Exception:
            log.debug("should_parse_file graph scan failed for %s", path, exc_info=True)

    return False


def safe_parse(path: Path, content: str) -> ParseResult:
    """Parse *path* via the registry, isolating parser exceptions.

    Every ``parser.parse()`` call is wrapped in ``try/except`` so a
    single bad file never crashes the indexing pipeline.  On failure
    returns a bare ``file`` node.
    """
    parser = get_parser_for_file(path)
    if parser is None:
        return ParseResult(nodes=[], edges=[])
    try:
        return parser.parse(path, content)
    except Exception:
        log.exception("Parser %s failed for %s", parser.language, path)
        fallback_id = f"file:{path}"
        return ParseResult(
            nodes=[
                RawNode(
                    id=fallback_id,
                    type=NodeType.file,
                    name=str(path),
                    path=str(path),
                    lang=parser.language,
                )
            ],
            edges=[],
        )


# Backwards-compat alias
parse_file = safe_parse
