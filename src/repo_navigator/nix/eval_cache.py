"""Eval cache with flake rev awareness."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from repo_navigator.graph.db import Database
from repo_navigator.models.option_value import OptionValue, ValueStatus
from repo_navigator.models.queries import EvalResult
from repo_navigator.nix.eval import nix_available, nix_eval_sync


def _expr_key(expr: str) -> str:
    return hashlib.sha256(expr.encode()).hexdigest()


def _current_flake_rev(root: Path | None = None) -> str | None:
    """Read current flake.lock rev (if any)."""
    if root is None:
        # Try cwd and its parents
        root = Path.cwd()
    # Search for flake.lock in root and up to 3 parents
    for candidate in [root, root.parent, root.parent.parent]:
        lock = candidate / "flake.lock"
        if lock.is_file():
            root = candidate
            break
    else:
        # Try direct root/flake.lock
        lock = (root or Path.cwd()) / "flake.lock"
        if not lock.is_file():
            return None
        root = root or Path.cwd()

    # At this point, lock is Path
    # Find the actual lock file that exists
    for p in [root / "flake.lock", root.parent / "flake.lock", Path.cwd() / "flake.lock"]:
        if p.is_file():
            lock = p
            break
    else:
        return None

    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
        # Try common locations: nodes.root.locked.rev or nodes.flake.locked.rev
        nodes = data.get("nodes", {})
        for key in ("root", "flake", ""):
            if key in nodes:
                locked = nodes[key].get("locked", {})
                rev = locked.get("rev")
                if rev:
                    return str(rev)
        # Fallback: search any node with locked.rev
        for node in nodes.values():
            if isinstance(node, dict):
                locked = node.get("locked", {})
                if isinstance(locked, dict) and locked.get("rev"):
                    return str(locked["rev"])
        # Also check top-level version?
        return None
    except Exception:
        return None


class EvalCache:
    """Cache for ``nix eval`` results with flake rev awareness."""

    def __init__(self, db: Database, root: Path | None = None) -> None:
        self.db = db
        self.root = Path(root) if root is not None else Path.cwd()
        self._cached_rev: str | None = None

    def _current_rev(self) -> str | None:
        # Cache rev for this instance to avoid re-reading file each time
        # But we also want to detect changes, so we re-read each time?
        # For simplicity, read each time
        return _current_flake_rev(self.root)

    def get(self, expr: str) -> OptionValue | None:
        """Return cached value if present and not stale and rev matches."""
        key = _expr_key(expr)
        ov = self.db.get_option_value(key)
        if ov is None:
            return None
        if ov.status == ValueStatus.stale:
            return None
        # Check rev
        current_rev = self._current_rev()
        if ov.source_rev is not None and current_rev is not None and ov.source_rev != current_rev:
            return None
        if ov.source_rev is not None and current_rev is None:
            # Flake removed, consider stale
            return None
        return ov

    def get_or_eval(self, expr: str, timeout: int = 60) -> EvalResult:
        """Get from cache or run ``nix eval`` and cache the result."""
        key = _expr_key(expr)
        gen = self.db.get_generation_id()
        cached = self.get(expr)
        if cached is not None and cached.status == ValueStatus.ok:
            return EvalResult(
                expr=expr,
                value_json=cached.value_json,
                status=cached.status,
                error=cached.error,
                cached=True,
                generation_id=gen,
            )

        # Need to eval
        if timeout > 120:
            raise ValueError("timeout must be <=120")
        value, error, status_str = nix_eval_sync(expr, timeout=timeout)
        status = ValueStatus(status_str) if status_str in ("ok", "unresolved", "error", "stale") else ValueStatus.error
        # For unresolved/error, we still cache but with that status
        ov = OptionValue(
            key=key,
            expr=expr,
            value_json=value,
            status=status,
            error=error,
            computed_at=datetime.now(UTC),
            source_rev=self._current_rev(),
        )
        self.db.upsert_option_value(ov)
        return EvalResult(
            expr=expr,
            value_json=value,
            status=status,
            error=error,
            cached=False,
            generation_id=gen,
        )

    async def get_or_eval_async(self, expr: str, timeout: int = 60) -> EvalResult:
        """Async version using :func:`nix_eval`."""
        from repo_navigator.nix.eval import nix_eval

        key = _expr_key(expr)
        gen = self.db.get_generation_id()
        cached = self.get(expr)
        if cached is not None and cached.status == ValueStatus.ok:
            return EvalResult(
                expr=expr,
                value_json=cached.value_json,
                status=cached.status,
                error=cached.error,
                cached=True,
                generation_id=gen,
            )
        if timeout > 120:
            raise ValueError("timeout must be <=120")
        value, error, status_str = await nix_eval(expr, timeout=timeout)
        status = ValueStatus(status_str) if status_str in ("ok", "unresolved", "error", "stale") else ValueStatus.error
        ov = OptionValue(
            key=key,
            expr=expr,
            value_json=value,
            status=status,
            error=error,
            computed_at=datetime.now(UTC),
            source_rev=self._current_rev(),
        )
        self.db.upsert_option_value(ov)
        return EvalResult(
            expr=expr,
            value_json=value,
            status=status,
            error=error,
            cached=False,
            generation_id=gen,
        )

    def invalidate_for_files(self, paths: list[str]) -> None:
        self.db.invalidate_option_values(paths)

    def invalidate_all(self) -> None:
        self.db.invalidate_all_option_values()
