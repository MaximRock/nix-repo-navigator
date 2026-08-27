"""Parser subpackage: language parsers and registry."""

from repo_navigator.parsers import nix_parser  # noqa: F401 — registers NixParser
from repo_navigator.parsers.registry import (  # noqa: F401
    LanguageConfig,
    clear_registry,
    get_all_parsers,
    get_parser_for_file,
    get_parser_for_language,
    parse_file,
    register,
    register_language,
    safe_parse,
    should_parse_file,
)
