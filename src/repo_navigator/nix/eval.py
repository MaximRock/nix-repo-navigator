"""Async/sync wrappers around ``nix eval``."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from typing import Any


def nix_available() -> bool:
    return shutil.which("nix") is not None


async def nix_eval(expr: str, timeout: int = 60) -> tuple[Any | None, str | None, str]:
    """Run ``nix eval --json --impure --expr <expr>`` asynchronously.

    Returns ``(value, error, status)`` where ``status`` is one of
    ``ok``/``unresolved``/``error`` and ``value`` is the parsed JSON on
    success (or ``None`` on failure).

    Timeout is enforced via ``asyncio.wait_for``.
    """
    if timeout > 120:
        raise ValueError("timeout must be <=120")
    if not nix_available():
        return None, "nix not found", "unresolved"

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "nix",
            "eval",
            "--json",
            "--impure",
            "--expr",
            expr,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        text_out = stdout.decode() if isinstance(stdout, (bytes, bytearray)) else str(stdout)
        text_err = stderr.decode() if isinstance(stderr, (bytes, bytearray)) else str(stderr)
        if proc.returncode == 0:
            try:
                value = json.loads(text_out) if text_out.strip() else None
            except json.JSONDecodeError:
                value = text_out.strip()
            return value, None, "ok"
        else:
            err = text_err.strip() or f"nix eval failed with code {proc.returncode}"
            status = "unresolved" if ("infinite recursion" in err.lower() or "attribute" in err.lower()) else "error"
            return None, err, status
    except asyncio.TimeoutError:
        # Try to kill proc if it exists
        try:
            proc.kill()  # type: ignore[possibly-undefined]
        except Exception:
            pass
        return None, f"nix eval timed out after {timeout}s", "error"
    except FileNotFoundError:
        return None, "nix not found", "unresolved"
    except ValueError:
        raise
    except Exception as exc:
        return None, str(exc), "error"


async def nix_eval_option(
    attrpath: str, flake_ref: str = ".", timeout: int = 60
) -> tuple[Any | None, str | None, str]:
    """Evaluate a NixOS option via flake: ``. #nixosConfigurations.<name>.config.<attrpath>``.

    This is a convenience wrapper that builds the expression
    ``(builtins.getFlake "<flake_ref>").nixosConfigurations.<...>.config.<attrpath>``
    but for now it simply delegates to ``nix_eval`` with a flake attribute
    expression.  Callers can pass a full flake attribute like
    ``. #nixosConfigurations.myhost.config.services.foo.enable``.
    """
    # If attrpath looks like a full flake reference (contains #), use it directly
    if "#" in attrpath:
        expr = attrpath
    else:
        # Generic: try to evaluate as config.<attrpath> via flake
        # We use impure flake eval
        expr = f'(builtins.getFlake "{flake_ref}").nixosConfigurations.*.config.{attrpath}'
        # The * is placeholder; real usage should provide full attrpath.
        # For now, just delegate to nix_eval with the attrpath as expr
        expr = attrpath
    return await nix_eval(expr, timeout=timeout)


def nix_eval_sync(expr: str, timeout: int = 60) -> tuple[Any | None, str | None, str]:
    """Synchronous wrapper around :func:`nix_eval`.

    Uses ``subprocess.run`` with timeout for simplicity and to avoid
    requiring an event loop in sync contexts (e.g. :class:`QueryEngine`).
    """
    if timeout > 120:
        raise ValueError("timeout must be <=120")
    if not nix_available():
        return None, "nix not found", "unresolved"
    try:
        proc = subprocess.run(
            ["nix", "eval", "--json", "--impure", "--expr", expr],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode == 0:
            try:
                value = json.loads(proc.stdout) if proc.stdout.strip() else None
            except json.JSONDecodeError:
                value = proc.stdout.strip()
            return value, None, "ok"
        else:
            err = proc.stderr.strip() or f"nix eval failed with code {proc.returncode}"
            status = "unresolved" if ("infinite recursion" in err.lower() or "attribute" in err.lower()) else "error"
            return None, err, status
    except subprocess.TimeoutExpired:
        return None, f"nix eval timed out after {timeout}s", "error"
    except FileNotFoundError:
        return None, "nix not found", "unresolved"
    except ValueError:
        raise
    except Exception as exc:
        return None, str(exc), "error"
