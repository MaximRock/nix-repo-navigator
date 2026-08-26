"""Golden tests for the Nix lexer.

Run with ``--update-golden`` to (re)generate the ``*_expected.json`` files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_navigator.parsers.nix.lexer import tokenize_to_dicts

GOLDEN_DIR = Path(__file__).parent


def _cases() -> list[str]:
    return sorted(p.stem for p in GOLDEN_DIR.glob("*.nix"))


@pytest.mark.parametrize("name", _cases())
def test_lexer(golden, name: str) -> None:
    golden("lexer", name, tokenize_to_dicts)
