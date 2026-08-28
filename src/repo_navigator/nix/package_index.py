"""Package index with mock resolution (phase 9.2)."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from repo_navigator.graph.db import Database

log = logging.getLogger(__name__)


def _mock_package_info(attribute: str) -> dict[str, Any]:
    """Return deterministic mock info for *attribute* (e.g. ``pkgs.ripgrep``)."""
    # Use hash to make version deterministic but varied per attribute
    h = hashlib.sha256(attribute.encode()).hexdigest()[:8]
    # Extract short name (last component after dot)
    short = attribute.split(".")[-1] if "." in attribute else attribute
    # Mock version: 1.<h[0:2]>.0
    version = f"1.{int(h[:2], 16) % 20}.0"
    store_path = f"/nix/store/{h}-{short}-{version}"
    return {
        "name": short,
        "version": version,
        "store_path": store_path,
        "meta": {"description": f"Mock package for {attribute}", "attribute": attribute, "mock": True},
    }


def resolve_package(attribute: str) -> dict[str, Any] | None:
    """Mock resolve: always returns deterministic info (no nix required)."""
    if not attribute:
        return None
    return _mock_package_info(attribute)


class PackageIndexBuilder:
    """Populates ``package_index`` from ``package_ref`` nodes."""

    def __init__(self, db: Database, root: Path | None = None) -> None:
        self.db = db
        self.root = Path(root) if root is not None else Path.cwd()

    def refresh(self) -> int:
        """Scan ``package_ref`` nodes and upsert mock package info.

        Returns number of packages indexed.
        """
        # Collect distinct package attributes from nodes
        package_attrs: set[str] = set()
        for node in self.db.get_all_nodes():
            if node.type.value == "package_ref":
                # id is package:<attr>
                attr = node.id.removeprefix("package:")
                package_attrs.add(attr)
                # Also try name
                if node.name:
                    package_attrs.add(node.name)

        # Also collect from package_ref via id prefix
        # Already done

        count = 0
        for attr in sorted(package_attrs):
            info = resolve_package(attr)
            if info is None:
                continue
            # Collect used_by: which modules use this package (via uses_package edge)
            used_by: list[str] = []
            for edge in self.db.get_all_edges():
                if edge.type.value == "uses_package" and edge.target == f"package:{attr}":
                    # source is nix:module
                    src_node = self.db.get_node(edge.source)
                    if src_node and src_node.path:
                        used_by.append(src_node.path)
                    else:
                        used_by.append(edge.source)
            # Deduplicate and sort
            used_by = sorted(set(used_by))
            # Upsert with used_by in meta? The DB's upsert_package stores used_by separately
            # but the current DB upsert_package expects used_by to be handled internally
            # via existing row's used_by. We need to pass used_by via meta? Actually
            # upsert_package's signature is (attribute, name, version, store_path, meta)
            # and it internally handles used_by. We will call it and then update used_by
            # via direct SQL if needed. For now, we store used_by in meta and also
            # try to update the used_by column.
            try:
                self.db.upsert_package(
                    attribute=attr,
                    name=info["name"],
                    version=info["version"],
                    store_path=info["store_path"],
                    meta=info["meta"],
                )
                # Update used_by column directly (since upsert_package doesn't take it)
                # The DB's upsert_package merges used_by internally, but we want to set it
                import json

                with self.db._lock, self.db.transaction():
                    self.db._conn.execute(
                        "UPDATE package_index SET used_by=? WHERE attribute=?",
                        (json.dumps(used_by), attr),
                    )
                count += 1
            except Exception as exc:
                log.debug("package_index refresh failed for %s: %s", attr, exc)

        # Purge stale packages (those in DB but no longer referenced)
        try:
            existing = {row["attribute"] for row in self.db._conn.execute("SELECT attribute FROM package_index").fetchall()}
            stale = existing - package_attrs
            for attr in stale:
                with self.db._lock, self.db.transaction():
                    self.db._conn.execute("DELETE FROM package_index WHERE attribute=?", (attr,))
        except Exception:
            pass

        return count

    def list_packages(self, limit: int = 50, query: str | None = None) -> list[dict[str, Any]]:
        """List packages, optionally filtered by *query* substring."""
        all_pkgs = self.db.get_packages()
        if query:
            q = query.lower()
            all_pkgs = [p for p in all_pkgs if q in p["attribute"].lower() or q in p["name"].lower()]
        return all_pkgs[:limit]

    def get_package(self, attribute: str) -> dict[str, Any] | None:
        for pkg in self.db.get_packages():
            if pkg["attribute"] == attribute:
                return pkg
        return None
