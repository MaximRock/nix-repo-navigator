"""Fallback Nix parser backed by ``nix-instantiate`` (phase 2.3)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

NIX_INSTANTIATE_TIMEOUT = 10


def nix_instantiate_available() -> bool:
    """Return ``True`` if ``nix-instantiate`` is on ``PATH``."""
    return shutil.which("nix-instantiate") is not None


def parse_via_nix_instantiate(path: Path) -> dict | None:
    """Parse *path* with ``nix-instantiate --parse --json``.

    Returns the parsed JSON-AST as a ``dict``, or ``None`` when
    ``nix-instantiate`` is unavailable or the invocation fails.
    """
    if not nix_instantiate_available():
        return None
    try:
        proc = subprocess.run(
            ["nix-instantiate", "--parse", "--json", str(path)],
            capture_output=True,
            text=True,
            timeout=NIX_INSTANTIATE_TIMEOUT,
        )
    except (subprocess.SubprocessError, OSError, TimeoutError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
