"""Golden tests for ast_extract + module_parser (phase 3.1/3.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_navigator.parsers.nix.ast_extract import extract
from repo_navigator.parsers.nix.module_parser import parse_module
from repo_navigator.parsers.nix.parser import parse


def _cases() -> list[str]:
    golden = Path(__file__).parent
    return sorted(p.stem for p in golden.glob("*.nix"))


def _run_extract_and_parse(source: str, name: str) -> dict:
    tree = parse(source)
    extracted = extract(tree)
    result = parse_module(Path(name + ".nix"), extracted)
    return result.model_dump()


@pytest.mark.parametrize("name", _cases())
def test_extract(name: str) -> None:
    from tests.conftest import run_golden_test

    run_golden_test("extract", name, lambda src: _run_extract_and_parse(src, name))
