"""Indexer package: file discovery and repository indexing."""

from repo_navigator.indexer.scan import collect_files, index_repo

__all__ = ["collect_files", "index_repo"]
