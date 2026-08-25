"""Unit tests for Config (pydantic-settings)."""

from __future__ import annotations

from pathlib import Path

from repo_navigator.config import Config


def test_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = Config(_env_file=None)
    assert cfg.root == Path.cwd()
    assert cfg.plugins == []
    assert cfg.db_path is None
    assert cfg.budgets == {"width": 10, "depth": 5, "limit": 10}
    assert cfg.timeouts["debounce_ms"] == 500
    assert cfg.watcher_mode == "auto"
    assert cfg.log_level == "INFO"


def test_resolved_db_path_default_under_root(tmp_path: Path) -> None:
    cfg = Config(root=tmp_path, _env_file=None)
    assert cfg.resolved_db_path == tmp_path / ".repo-navigator.db"


def test_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("REPO_NAVIGATOR_ROOT", "/tmp/some-repo")
    monkeypatch.setenv("REPO_NAVIGATOR_DB_PATH", "/tmp/custom.db")
    monkeypatch.setenv("REPO_NAVIGATOR_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("REPO_NAVIGATOR_PLUGINS", '["python", "kdl"]')
    cfg = Config(_env_file=None)
    assert cfg.root == Path("/tmp/some-repo")
    assert cfg.db_path == Path("/tmp/custom.db")
    assert cfg.log_level == "DEBUG"
    assert cfg.plugins == ["python", "kdl"]
    assert cfg.resolved_db_path == Path("/tmp/custom.db")


def test_env_budgets_json(monkeypatch) -> None:
    monkeypatch.setenv("REPO_NAVIGATOR_BUDGETS", '{"width": 20, "depth": 3, "limit": 5}')
    cfg = Config(_env_file=None)
    assert cfg.budgets["width"] == 20


def test_invalid_watcher_mode_rejected(monkeypatch) -> None:
    monkeypatch.setenv("REPO_NAVIGATOR_WATCHER_MODE", "esp")
    try:
        Config(_env_file=None)
    except Exception:
        pass
    else:
        raise AssertionError("expected validation error for watcher_mode=esp")
