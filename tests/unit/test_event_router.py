"""Unit tests for EventRouter (phase 5.3)."""

from __future__ import annotations

import asyncio

import pytest

from repo_navigator.indexer.event_router import EventRouter


@pytest.mark.asyncio
async def test_debounce_batches_rapid_events() -> None:
    router = EventRouter(debounce_ms=50)
    router.on_file_event("a.nix")
    router.on_file_event("b.nix")
    router.on_file_event("c.nix")
    # Wait for debounce
    await asyncio.sleep(0.1)
    batch = await asyncio.wait_for(router.queue.get(), timeout=1)
    assert set(batch) == {"a.nix", "b.nix", "c.nix"}
    assert router.queue.empty()


@pytest.mark.asyncio
async def test_debounce_resets_on_new_event() -> None:
    router = EventRouter(debounce_ms=80)
    router.on_file_event("a.nix")
    await asyncio.sleep(0.04)
    router.on_file_event("b.nix")
    # Timer should have reset, so after 40ms from second event, still not flushed
    await asyncio.sleep(0.04)
    assert router.queue.empty()
    # After total 80ms from second event, should flush
    await asyncio.sleep(0.05)
    batch = await asyncio.wait_for(router.queue.get(), timeout=1)
    assert set(batch) == {"a.nix", "b.nix"}


@pytest.mark.asyncio
async def test_git_hook_immediate() -> None:
    router = EventRouter(debounce_ms=500)
    await router.on_git_hook(["x.nix", "y.nix"])
    batch = await asyncio.wait_for(router.queue.get(), timeout=1)
    assert set(batch) == {"x.nix", "y.nix"}
    assert router.sync_in_progress is False


@pytest.mark.asyncio
async def test_git_hook_bulk_sets_sync_flag() -> None:
    router = EventRouter(debounce_ms=500)
    files = [f"f{i}.nix" for i in range(51)]
    await router.on_git_hook(files)
    assert router.sync_in_progress is True
    batch = await asyncio.wait_for(router.queue.get(), timeout=1)
    assert len(batch) == 51


@pytest.mark.asyncio
async def test_git_hook_cancels_pending_debounce() -> None:
    router = EventRouter(debounce_ms=200)
    router.on_file_event("a.nix")
    # Before debounce fires, git hook should cancel and include pending
    await asyncio.sleep(0.05)
    await router.on_git_hook(["b.nix"])
    batch = await asyncio.wait_for(router.queue.get(), timeout=1)
    # Should contain both a.nix (pending) and b.nix (git)
    assert set(batch) == {"a.nix", "b.nix"}
    # No second batch
    await asyncio.sleep(0.25)
    assert router.queue.empty()


@pytest.mark.asyncio
async def test_flush_forces_immediate() -> None:
    router = EventRouter(debounce_ms=500)
    router.on_file_event("a.nix")
    assert router.queue.empty()
    await router.flush()
    batch = await asyncio.wait_for(router.queue.get(), timeout=1)
    assert batch == ["a.nix"]
