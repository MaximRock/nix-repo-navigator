"""Parser subpackage: language parsers and registry."""

from repo_navigator.parsers import nix_parser  # noqa: F401 — registers NixParser
from repo_navigator.parsers.registry import get_parser_for_file, get_parser_for_language  # noqa: F401
