"""Unit tests for EvalCache (phase 8.2)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_navigator.graph.db import Database
from repo_navigator.models.option_value import OptionValue, ValueStatus
from repo_navigator.nix.eval_cache import EvalCache


def test_get_or_eval_caches(tmp_path: Path) -> None:
    db = Database(":memory:")
    db.init_db()
    cache = EvalCache(db, root=tmp_path)
    with patch("repo_navigator.nix.eval.subprocess.run") as mock_run, patch(
        "repo_navigator.nix.eval.shutil.which", return_value="/nix/bin/nix"
    ):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = json.dumps({"x": 1})
        mock_run.return_value.stderr = ""
        res1 = cache.get_or_eval("1+1")
        assert res1.value_json == {"x": 1}
        assert res1.cached is False
        assert res1.status == ValueStatus.ok
        # Second call should be cached
        res2 = cache.get_or_eval("1+1")
        assert res2.cached is True
        assert res2.value_json == {"x": 1}
        assert mock_run.call_count == 1


def test_get_stale_returns_none(tmp_path: Path) -> None:
    db = Database(":memory:")
    db.init_db()
    cache = EvalCache(db, root=tmp_path)
    # Insert stale entry
    from repo_navigator.nix.eval_cache import _expr_key

    key = _expr_key("1+1")
    db.upsert_option_value(OptionValue(key=key, expr="1+1", value_json={"v": 1}, status=ValueStatus.stale))
    assert cache.get("1+1") is None


def test_invalidate_for_files(tmp_path: Path) -> None:
    from repo_navigator.nix.eval_cache import _expr_key

    db = Database(":memory:")
    db.init_db()
    cache = EvalCache(db, root=tmp_path)
    # Insert two entries with different exprs
    with patch("repo_navigator.nix.eval.subprocess.run") as mock_run, patch(
        "repo_navigator.nix.eval.shutil.which", return_value="/nix/bin/nix"
    ):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = json.dumps({"v": 1})
        mock_run.return_value.stderr = ""
        cache.get_or_eval("config.services.foo")
        cache.get_or_eval("config.services.bar")
    # Invalidate for foo.nix (stem "foo" matches first expr only)
    cache.invalidate_for_files(["foo.nix"])
    ov1 = db.get_option_value(_expr_key("config.services.foo"))
    assert ov1 is not None and ov1.status == ValueStatus.stale
    ov2 = db.get_option_value(_expr_key("config.services.bar"))
    assert ov2 is not None and ov2.status == ValueStatus.ok


def test_source_rev_mismatch(tmp_path: Path) -> None:
    # Create flake.lock with rev abc
    lock = tmp_path / "flake.lock"
    lock.write_text(json.dumps({"nodes": {"root": {"locked": {"rev": "abc123"}}}}))
    db = Database(":memory:")
    db.init_db()
    cache = EvalCache(db, root=tmp_path)
    # Insert entry with old rev
    from repo_navigator.nix.eval_cache import _expr_key

    key = _expr_key("1+1")
    db.upsert_option_value(OptionValue(key=key, expr="1+1", value_json={"v": 1}, status=ValueStatus.ok, source_rev="oldrev"))
    # get should return None due to rev mismatch
    assert cache.get("1+1") is None
    # After get_or_eval with current rev, it should update
    with patch("repo_navigator.nix.eval.subprocess.run") as mock_run, patch(
        "repo_navigator.nix.eval.shutil.which", return_value="/nix/bin/nix"
    ):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = json.dumps({"v": 2})
        mock_run.return_value.stderr = ""
        res = cache.get_or_eval("1+1")
        assert res.value_json == {"v": 2}
        ov = db.get_option_value(key)
        assert ov is not None and ov.source_rev == "abc123"


def test_get_or_eval_no_nix(tmp_path: Path) -> None:
    db = Database(":memory:")
    db.init_db()
    cache = EvalCache(db, root=tmp_path)
    with patch("repo_navigator.nix.eval.shutil.which", return_value=None):
        res = cache.get_or_eval("1+1")
        assert res.status == ValueStatus.unresolved
        assert "nix not found" in (res.error or "")


@pytest.mark.asyncio
async def test_get_or_eval_async(tmp_path: Path) -> None:
    db = Database(":memory:")
    db.init_db()
    cache = EvalCache(db, root=tmp_path)
    mock_proc = type("P", (), {"communicate": staticmethod(lambda: (json.dumps(99).encode(), b"")), "returncode": 0, "kill": lambda: None})()
    # Use AsyncMock for create_subprocess_exec
    from unittest.mock import AsyncMock

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(json.dumps(99).encode(), b""))
    mock_proc.returncode = 0
    with patch("repo_navigator.nix.eval.shutil.which", return_value="/nix/bin/nix"), patch(
        "repo_navigator.nix.eval.asyncio.create_subprocess_exec", return_value=mock_proc
    ):
        res = await cache.get_or_eval_async("1+1")
        assert res.value_json == 99
        assert res.status == ValueStatus.ok
