"""Unit tests for the nix-instantiate fallback (phase 2.3)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

from repo_navigator.parsers.nix import nix_instantiate


def test_available_true() -> None:
    with mock.patch("shutil.which", return_value="/usr/bin/nix-instantiate"):
        assert nix_instantiate.nix_instantiate_available() is True


def test_available_false() -> None:
    with mock.patch("shutil.which", return_value=None):
        assert nix_instantiate.nix_instantiate_available() is False


def test_not_available_returns_none() -> None:
    with mock.patch("shutil.which", return_value=None):
        assert nix_instantiate.parse_via_nix_instantiate(Path("x.nix")) is None


def test_success_returns_dict() -> None:
    payload = {"AST": [1, 2, 3]}
    proc = mock.Mock()
    proc.returncode = 0
    proc.stdout = json.dumps(payload)
    with mock.patch("shutil.which", return_value="/usr/bin/nix-instantiate"), mock.patch(
        "subprocess.run", return_value=proc
    ) as run:
        result = nix_instantiate.parse_via_nix_instantiate(Path("x.nix"))
    assert result == payload
    run.assert_called_once()
    assert run.call_args.args[0][:1] == ["nix-instantiate"]


def test_failure_returncode_none() -> None:
    proc = mock.Mock()
    proc.returncode = 1
    proc.stdout = ""
    with mock.patch("shutil.which", return_value="/usr/bin/nix-instantiate"), mock.patch(
        "subprocess.run", return_value=proc
    ):
        assert nix_instantiate.parse_via_nix_instantiate(Path("x.nix")) is None


def test_invalid_json_none() -> None:
    proc = mock.Mock()
    proc.returncode = 0
    proc.stdout = "{ a = 1; }"  # nix-instantiate without --json support
    with mock.patch("shutil.which", return_value="/usr/bin/nix-instantiate"), mock.patch(
        "subprocess.run", return_value=proc
    ):
        assert nix_instantiate.parse_via_nix_instantiate(Path("x.nix")) is None


def test_timeout_none() -> None:
    with mock.patch("shutil.which", return_value="/usr/bin/nix-instantiate"), mock.patch(
        "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=10)
    ):
        assert nix_instantiate.parse_via_nix_instantiate(Path("x.nix")) is None
