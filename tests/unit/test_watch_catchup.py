"""Tests for the watcher startup catch-up index."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from repo_navigator.cli import _catch_up_index, _run_watch
from repo_navigator.config import Config
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.indexer.scan import index_repo


def test_catch_up_indexes_and_bumps_generation(tmp_path: Path) -> None:
    (tmp_path / "a.nix").write_text("{ config.a = 1; }", encoding="utf-8")
    db_path = tmp_path / "watch.db"
    cfg = Config(root=tmp_path, db_path=db_path)
    db = Database(str(db_path))
    db.init_db()
    graph = NxGraph()
    try:
        first = _catch_up_index(tmp_path, db, graph, cfg)
        assert first["files"] == 1
        assert db.get_node("nix:a.nix") is not None
        generation = db.get_generation_id()

        _catch_up_index(tmp_path, db, graph, cfg)
        assert db.get_generation_id() == generation + 1
    finally:
        db.close()


class _StopQueue:
    async def get(self) -> list[str]:
        raise asyncio.CancelledError

    def task_done(self) -> None:
        pass


class _FakeEventRouter:
    def __init__(self, debounce_ms: float = 500) -> None:
        self.queue = _StopQueue()


class _FakeWatcher:
    def __init__(
        self,
        root: Path,
        event_router: object,
        config: Config | None = None,
    ) -> None:
        self.root = root
        self.event_router = event_router
        self.config = config

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> str:
        return "polling"

    def stop(self) -> None:
        pass


def test_run_watch_catches_up_when_db_already_populated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.nix").write_text("{ config.a = 1; }", encoding="utf-8")
    db_path = tmp_path / "watch.db"
    cfg = Config(root=tmp_path, db_path=db_path)
    db = Database(str(db_path))
    db.init_db()
    graph = NxGraph()
    try:
        index_repo(tmp_path, db, graph, config=cfg)
        generation = db.get_generation_id()
    finally:
        db.close()

    monkeypatch.setattr(
        "repo_navigator.indexer.event_router.EventRouter", _FakeEventRouter
    )
    monkeypatch.setattr("repo_navigator.watcher.filesystem.RepoWatcher", _FakeWatcher)

    asyncio.run(_run_watch(tmp_path, db_path))

    db = Database(str(db_path))
    db.init_db()
    try:
        assert db.get_generation_id() == generation + 1
        assert db.get_node("nix:a.nix") is not None
    finally:
        db.close()
