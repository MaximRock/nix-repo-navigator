"""Nix-specific parsing: lexer, parser, extractors, fallback."""

from repo_navigator.parsers.nix.nix_instantiate import (
    nix_instantiate_available,
    parse_via_nix_instantiate,
)

__all__ = ["nix_instantiate_available", "parse_via_nix_instantiate"]
