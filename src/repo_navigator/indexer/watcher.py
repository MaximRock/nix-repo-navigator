"""Re-export for backwards compat: watcher lives in :mod:`repo_navigator.watcher`."""

from repo_navigator.watcher.filesystem import RepoWatcher

__all__ = ["RepoWatcher"]
