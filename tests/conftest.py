"""Shared fixtures and golden-test machinery for repo-navigator tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

GOLDEN_DIR = Path(__file__).parent / "golden"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Overwrite *_expected.json files with current output.",
    )


def run_golden_test(
    component: str,
    test_name: str,
    runner_fn: Callable[[str], Any],
    *,
    update: bool = False,
) -> None:
    """Feed a .nix fixture through ``runner_fn`` and compare to ``*_expected.json``.

    ``component`` is one of ``lexer``, ``parser``, ``extract`` (a directory under
    ``tests/golden/``). ``runner_fn`` receives the raw source string and returns a
    JSON-serializable object.
    """
    golden_path = GOLDEN_DIR / component
    input_file = golden_path / f"{test_name}.nix"
    expected_file = golden_path / f"{test_name}_expected.json"

    source = input_file.read_text(encoding="utf-8")
    result = runner_fn(source)
    actual_json = json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"

    if update or not expected_file.exists():
        expected_file.parent.mkdir(parents=True, exist_ok=True)
        expected_file.write_text(actual_json, encoding="utf-8")
        if not update:
            pytest.skip(f"Created expected file for '{component}/{test_name}'")
        return

    expected = expected_file.read_text(encoding="utf-8")
    assert actual_json == expected, (
        f"Golden test '{component}/{test_name}' failed.\n"
        f"Run with --update-golden to overwrite the expected file."
    )


@pytest.fixture
def golden(request: pytest.FixtureRequest):
    """Return ``run_golden_test`` bound to the ``--update-golden`` flag."""

    update = request.config.getoption("--update-golden")

    def _run(component: str, test_name: str, runner_fn: Callable[[str], Any]) -> None:
        return run_golden_test(component, test_name, runner_fn, update=update)

    return _run
