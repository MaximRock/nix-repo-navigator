"""Unit tests for nix eval wrapper (phase 8.1)."""

from __future__ import annotations

import asyncio
import json
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from repo_navigator.nix.eval import nix_available, nix_eval, nix_eval_sync


class TestNixAvailable:
    def test_available(self) -> None:
        with patch("repo_navigator.nix.eval.shutil.which", return_value="/nix/bin/nix"):
            assert nix_available() is True

    def test_not_available(self) -> None:
        with patch("repo_navigator.nix.eval.shutil.which", return_value=None):
            assert nix_available() is False


class TestNixEvalSync:
    def test_ok(self) -> None:
        with patch("repo_navigator.nix.eval.shutil.which", return_value="/nix/bin/nix"), patch(
            "repo_navigator.nix.eval.subprocess.run"
        ) as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = json.dumps({"a": 1})
            mock_run.return_value.stderr = ""
            value, err, status = nix_eval_sync("1+1")
            assert value == {"a": 1}
            assert err is None
            assert status == "ok"

    def test_error_infinite_recursion(self) -> None:
        with patch("repo_navigator.nix.eval.shutil.which", return_value="/nix/bin/nix"), patch(
            "repo_navigator.nix.eval.subprocess.run"
        ) as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = "error: infinite recursion encountered"
            value, err, status = nix_eval_sync("bad")
            assert value is None
            assert status == "unresolved"
            assert "infinite recursion" in err

    def test_timeout(self) -> None:
        with patch("repo_navigator.nix.eval.shutil.which", return_value="/nix/bin/nix"), patch(
            "repo_navigator.nix.eval.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="nix", timeout=1)
        ):
            value, err, status = nix_eval_sync("1+1", timeout=1)
            assert status == "error"
            assert "timed out" in err

    def test_no_nix(self) -> None:
        with patch("repo_navigator.nix.eval.shutil.which", return_value=None):
            value, err, status = nix_eval_sync("1+1")
            assert status == "unresolved"
            assert "nix not found" in err

    def test_timeout_validation(self) -> None:
        with pytest.raises(ValueError, match="timeout must be <=120"):
            nix_eval_sync("1+1", timeout=200)


class TestNixEvalAsync:
    @pytest.mark.asyncio
    async def test_ok(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(json.dumps(123).encode(), b""))
        mock_proc.returncode = 0
        with patch("repo_navigator.nix.eval.shutil.which", return_value="/nix/bin/nix"), patch(
            "repo_navigator.nix.eval.asyncio.create_subprocess_exec", return_value=mock_proc
        ):
            value, err, status = await nix_eval("1+1")
            assert value == 123
            assert status == "ok"

    @pytest.mark.asyncio
    async def test_no_nix(self) -> None:
        with patch("repo_navigator.nix.eval.shutil.which", return_value=None):
            value, err, status = await nix_eval("1+1")
            assert status == "unresolved"
            assert "nix not found" in err

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_proc.kill = MagicMock()
        mock_proc.returncode = None
        with patch("repo_navigator.nix.eval.shutil.which", return_value="/nix/bin/nix"), patch(
            "repo_navigator.nix.eval.asyncio.create_subprocess_exec", return_value=mock_proc
        ):
            value, err, status = await nix_eval("1+1", timeout=1)
            assert status == "error"
            assert "timed out" in err

    @pytest.mark.asyncio
    async def test_validation(self) -> None:
        with pytest.raises(ValueError, match="timeout must be <=120"):
            await nix_eval("1+1", timeout=200)
