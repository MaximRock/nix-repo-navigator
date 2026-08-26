"""Golden tests for the Nix parser.

Run with ``--update-golden`` to (re)generate the ``*_expected.json`` files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_navigator.parsers.nix.parser import parse_to_dict

GOLDEN_DIR = Path(__file__).parent


def _cases() -> list[str]:
    return sorted(p.stem for p in GOLDEN_DIR.glob("*.nix"))


@pytest.mark.parametrize("name", _cases())
def test_parser(golden, name: str) -> None:
    golden("parser", name, parse_to_dict)
