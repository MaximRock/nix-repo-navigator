"""Tests for logging setup and parse-failure accounting (spec v3)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from repo_navigator.logging_setup import PARSE_LOG_NAME, configure_logging
from repo_navigator.parsers import registry
from repo_navigator.parsers.nix_parser import NixParser
from repo_navigator.parsers.registry import parse_failure_count, safe_parse


class _ExplodingParser:
    language = "boom"

    def parse(self, path: Path, content: str) -> Any:
        raise RuntimeError("kaboom in parser")


def test_parse_errors_are_logged_and_counted(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        registry, "get_parser_for_file", lambda path: _ExplodingParser()
    )
    configure_logging("INFO", tmp_path)

    before = parse_failure_count()
    result = safe_parse(Path("a.boom"), "x")

    assert parse_failure_count() == before + 1
    assert result.nodes[0].type.value == "file"
    assert result.nodes[0].id == "file:a.boom"

    log_file = tmp_path / PARSE_LOG_NAME
    assert log_file.is_file()
    text = log_file.read_text(encoding="utf-8")
    assert "kaboom in parser" in text
    assert "a.boom" in text


def test_configure_logging_replaces_previous_file_handler(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(
        registry, "get_parser_for_file", lambda path: _ExplodingParser()
    )
    first = tmp_path / "first"
    second = tmp_path / "second"
    configure_logging("INFO", first)
    configure_logging("INFO", second)

    logger = logging.getLogger("repo_navigator.parsers")
    marked = [
        h for h in logger.handlers if getattr(h, "_repo_navigator_parse_log", False)
    ]
    assert len(marked) == 1
    assert Path(marked[0].baseFilename).parent == second


def test_nix_parser_counts_its_own_failures(monkeypatch: Any) -> None:
    parser = NixParser()

    def boom(path: Path, content: str) -> Any:
        raise RuntimeError("inner boom")

    monkeypatch.setattr(parser, "_parse_inner", boom)
    before = parse_failure_count()
    result = parser.parse(Path("bad.nix"), "x = 1;")

    assert parse_failure_count() == before + 1
    assert result.nodes[0].id == "nix:bad.nix"
    assert result.nodes[0].type.value == "nix_module"


def test_configure_logging_reset_truncates(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        registry, "get_parser_for_file", lambda path: _ExplodingParser()
    )
    log_file = tmp_path / PARSE_LOG_NAME
    configure_logging("INFO", tmp_path, reset=True)
    safe_parse(Path("a.boom"), "x")
    assert log_file.read_text(encoding="utf-8").strip()

    configure_logging("INFO", tmp_path, reset=True)
    assert not log_file.exists() or not log_file.read_text(encoding="utf-8").strip()
