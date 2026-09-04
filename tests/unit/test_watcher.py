"""Unit tests for RepoWatcher (phase 5.3)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from repo_navigator.config import Config
from repo_navigator.indexer.event_router import EventRouter
from repo_navigator.watcher.filesystem import RepoWatcher


def test_watcher_collects_nix_and_skips_git(tmp_path: Path) -> None:
    (tmp_path / "a.nix").write_text("{ a = 1; }")
    (tmp_path / "b.txt").write_text("hello")
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "c.nix").write_text("{ a = 1; }")
    (tmp_path / "result").mkdir()
    (tmp_path / "result" / "d.nix").write_text("{ a = 1; }")
    nav_dir = tmp_path / ".repo-navigator"
    nav_dir.mkdir()
    (nav_dir / "repo-navigator.db").write_text("")
    (nav_dir / "e.nix").write_text("{ a = 1; }")

    router = EventRouter(debounce_ms=50)
    watcher = RepoWatcher(tmp_path, router, config=Config(root=tmp_path))
    files = watcher._collect_nix_files()
    paths = {p.name for p in files}
    assert "a.nix" in paths
    assert "b.txt" not in paths
    assert "c.nix" not in paths  # inside .git, skipped
    assert "d.nix" not in paths  # inside result, skipped
    assert "e.nix" not in paths  # inside .repo-navigator, skipped


def test_watcher_start_polling_mode(tmp_path: Path) -> None:
    router = EventRouter(debounce_ms=50)
    cfg = Config(root=tmp_path, watcher_mode="polling")
    watcher = RepoWatcher(tmp_path, router, config=cfg)
    mode = watcher.start()
    assert mode == "polling"
    assert watcher.mode == "polling"
    watcher.stop()
    assert watcher._poll_task is None or watcher._poll_task.done()


@pytest.mark.asyncio
async def test_watcher_polling_detects_new_file(tmp_path: Path) -> None:
    router = EventRouter(debounce_ms=50)
    cfg = Config(root=tmp_path, watcher_mode="polling", timeouts={"polling_s": 0.2, "debounce_ms": 50})
    watcher = RepoWatcher(tmp_path, router, config=cfg)
    # Start polling loop
    await watcher.start_polling_async()
    await asyncio.sleep(0.1)
    # Create new file
    (tmp_path / "new.nix").write_text("{ a = 1; }")
    # Wait for polling + debounce
    await asyncio.sleep(0.5)
    # Should have received event
    found = False
    while not router.queue.empty():
        batch = await router.queue.get()
        if any("new.nix" in p for p in batch):
            found = True
    watcher.stop()
    assert found, "polling watcher should detect new .nix file"


def test_watcher_should_handle_only_nix(tmp_path: Path) -> None:
    from repo_navigator.watcher.filesystem import _WatchdogHandler

    router = EventRouter()
    cfg = Config(root=tmp_path)
    # Create a dummy loop for handler
    loop = asyncio.new_event_loop()
    handler = _WatchdogHandler(tmp_path, router, loop, cfg)
    assert handler._should_handle(str(tmp_path / "a.nix")) is True
    assert handler._should_handle(str(tmp_path / "a.txt")) is False
    assert handler._should_handle(str(tmp_path / "test.db")) is False
    assert handler._should_handle(str(tmp_path / ".repo-navigator" / "repo-navigator.db")) is False
    loop.close()
