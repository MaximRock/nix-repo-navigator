"""End-to-end builder flow (phase 4.3).

Tests the integration of parser -> module_parser -> GraphBuilder ->
Database/NxGraph via both direct builder API and the bulk indexer
(`index_repo`).  Uses a miniature home-manager repo.
"""

from __future__ import annotations

from pathlib import Path

from repo_navigator.config import Config
from repo_navigator.graph.builder import GraphBuilder
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.graph.queries import QueryEngine
from repo_navigator.indexer.scan import index_repo
from repo_navigator.models.edges import EdgeType
from repo_navigator.models.nodes import NodeType


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_e2e_imports_declares_sets(tmp_path: Path) -> None:
    # Repo layout:
    #   default.nix -> imports a.nix, b.nix
    #   a.nix -> declares option services.foo.enable
    #   b.nix -> sets option, home.file
    _write(
        tmp_path / "a.nix",
        """
        { lib, ... }:
        {
          options.services.foo.enable = lib.mkOption {
            type = lib.types.bool;
            default = false;
            description = "foo";
          };
        }
        """,
    )
    _write(
        tmp_path / "b.nix",
        """
        {
          config.services.foo.enable = true;
          home.file.".config/foo".source = ./foo.conf;
          home.packages = [ pkgs.ripgrep ];
        }
        """,
    )
    _write(
        tmp_path / "default.nix",
        """
        {
          imports = [ ./a.nix ./b.nix ];
        }
        """,
    )
    _write(tmp_path / "foo.conf", "hello")

    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    cfg = Config(root=tmp_path)
    stats = index_repo(tmp_path, db, g, config=cfg)

    assert stats["files"] == 3
    # Nodes: 3 modules + 1 option + 1 file + 1 package
    assert db.get_node("nix:a.nix") is not None
    assert db.get_node("nix:b.nix") is not None
    assert db.get_node("nix:default.nix") is not None
    assert db.get_node("nix_option:services.foo.enable") is not None
    assert db.get_node("file:.config/foo") is not None
    assert db.get_node("package:pkgs.ripgrep") is not None

    # Edges: default imports a,b ; a declares option ; b sets option ; b configures file ; b uses_package
    edges = db.get_all_edges()
    types = {(e.source, e.target, e.type) for e in edges}
    assert ("nix:default.nix", "nix:a.nix", EdgeType.imports) in types
    assert ("nix:default.nix", "nix:b.nix", EdgeType.imports) in types
    assert ("nix:a.nix", "nix_option:services.foo.enable", EdgeType.declares) in types
    assert ("nix:b.nix", "nix_option:services.foo.enable", EdgeType.sets) in types
    assert ("nix:b.nix", "file:.config/foo", EdgeType.configures) in types
    assert ("nix:b.nix", "package:pkgs.ripgrep", EdgeType.uses_package) in types

    # NxGraph mirrors DB
    assert g.number_of_nodes() == db.count_nodes()
    assert g.number_of_edges() == db.count_edges()
    assert g.has_node("nix:default.nix")
    # BFS from default should reach a, b, option, file, package
    reachable = {n.id for n in g.bfs("nix:default.nix", depth=3, width=10)}
    assert "nix:a.nix" in reachable
    assert "nix:b.nix" in reachable


def test_e2e_dynamic_update(tmp_path: Path) -> None:
    _write(tmp_path / "a.nix", "{ imports = [ ./b.nix ]; }")
    _write(tmp_path / "b.nix", "{ config.x = 1; }")

    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    cfg = Config(root=tmp_path)
    index_repo(tmp_path, db, g, config=cfg)
    # a imports b + b sets x => 2 edges
    assert db.count_edges() == 2
    assert any(e.target == "nix:b.nix" for e in db.get_all_edges())

    # Modify a.nix to import c.nix instead
    _write(tmp_path / "a.nix", "{ imports = [ ./c.nix ]; }")
    _write(tmp_path / "c.nix", "{ config.y = 2; }")
    # Keep b.nix on disk, but a no longer imports it
    stats = index_repo(tmp_path, db, g, config=cfg)
    assert stats["files"] == 3
    edges = db.get_all_edges()
    assert any(e.target == "nix:c.nix" for e in edges)
    assert not any(e.source == "nix:a.nix" and e.target == "nix:b.nix" for e in edges)
    # b.nix module still exists (file still on disk)
    assert db.get_node("nix:b.nix") is not None
    # BFS from a should now reach c, not b
    reachable = {n.id for n in g.bfs("nix:a.nix", depth=2, width=10)}
    assert "nix:c.nix" in reachable
    assert "nix:b.nix" not in reachable

    # Delete b.nix from filesystem and re-index -> its node should be purged
    (tmp_path / "b.nix").unlink()
    index_repo(tmp_path, db, g, config=cfg)
    assert db.get_node("nix:b.nix") is None
    # c still there
    assert db.get_node("nix:c.nix") is not None


def test_e2e_deleted_file_prunes_orphan_synthetic(tmp_path: Path) -> None:
    # BUG-004 D5: a synthetic option placeholder orphaned by file
    # deletion is GC'd from both DB and NxGraph on re-index.
    _write(tmp_path / "a.nix", "{ modules.home.comfyui.enable = false; }")

    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    cfg = Config(root=tmp_path)
    index_repo(tmp_path, db, g, config=cfg)
    assert db.get_node("nix_option:modules.home.comfyui.enable") is not None

    (tmp_path / "a.nix").unlink()
    index_repo(tmp_path, db, g, config=cfg)
    assert db.get_node("nix_option:modules.home.comfyui.enable") is None
    assert not g.has_node("nix_option:modules.home.comfyui.enable")


def test_e2e_nested_import_normalisation(tmp_path: Path) -> None:
    # Ensure imports are normalised relative to file's directory
    _write(tmp_path / "modules" / "a.nix", "{ imports = [ ./b.nix ]; }")
    _write(tmp_path / "modules" / "b.nix", "{ config.foo = 1; }")
    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    cfg = Config(root=tmp_path)
    index_repo(tmp_path, db, g, config=cfg)
    # a imports b -> edge target should be nix:modules/b.nix (normalised)
    assert db.get_node("nix:modules/a.nix") is not None
    assert db.get_node("nix:modules/b.nix") is not None
    edges = db.get_all_edges()
    assert any(
        e.source == "nix:modules/a.nix" and e.target == "nix:modules/b.nix"
        for e in edges
    )
    # No placeholder ./b.nix
    assert db.get_node("nix:modules/./b.nix") is None
    assert db.get_node("nix:./b.nix") is None


def test_e2e_bare_import_edges_and_queries(tmp_path: Path) -> None:
    # The bug-report scenario: a flake imports ./lib via a let-binding, and
    # lib/default.nix imports ./overlays.nix and a bare directory path.
    # Before the fix these produced no `imports` edges, so dependencies/
    # dependents/observe from a changed file returned nothing.
    _write(
        tmp_path / "flake.nix",
        """
        { inputs }:
        let
          lib = import ./lib;
        in
        {
          nixosConfigurations.host = lib.mkHost {};
        }
        """,
    )
    _write(
        tmp_path / "lib" / "default.nix",
        """
        { inputs, ... }:
        let
          overlays = import ./overlays.nix;
          nvf = import ./modules/home/editors/configs/nvf-config;
          inherit (import ./secrets.nix { inherit inputs; }) tokens;
        in
        {
          mkHost = { ... }: { imports = [ ./modules/nixos/base.nix ]; };
        }
        """,
    )
    _write(
        tmp_path / "lib" / "overlays.nix", "{ final, prev }: { mypkg = final.hello; }"
    )
    _write(tmp_path / "lib" / "secrets.nix", "{ inputs, ... }: { tokens = { }; }")
    _write(
        tmp_path
        / "lib"
        / "modules"
        / "home"
        / "editors"
        / "configs"
        / "nvf-config"
        / "default.nix",
        "{ config.vim.enable = true; }",
    )
    _write(
        tmp_path / "lib" / "modules" / "nixos" / "base.nix",
        "{ config.services.openssh.enable = true; }",
    )

    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    cfg = Config(root=tmp_path)
    index_repo(tmp_path, db, g, config=cfg)

    edges = db.get_all_edges()
    # Bare-import edges now exist for let-bindings, nested-call args and
    # inherit-from, with directory imports resolving to default.nix.
    assert ("nix:flake.nix", "nix:lib/default.nix", EdgeType.imports) in {
        (e.source, e.target, e.type) for e in edges
    }
    assert ("nix:lib/default.nix", "nix:lib/overlays.nix", EdgeType.imports) in {
        (e.source, e.target, e.type) for e in edges
    }
    assert (
        "nix:lib/modules/home/editors/configs/nvf-config/default.nix",
        EdgeType.imports,
    ) in {(e.target, e.type) for e in edges if e.source == "nix:lib/default.nix"}
    assert ("nix:lib/default.nix", "nix:lib/secrets.nix", EdgeType.imports) in {
        (e.source, e.target, e.type) for e in edges
    }

    # flake -> lib -> overlays : dependencies view of flake reaches lib.
    q = QueryEngine(db, g, config=cfg)
    deps = q.dependencies("nix:flake.nix")
    ids = {d.node.id for d in deps.depends_on}
    assert "nix:lib/default.nix" in ids
    assert "nix:lib/overlays.nix" in ids

    # dependents view of a leaf: overlays is reachable from flake chain.
    dents = q.dependents("nix:lib/overlays.nix")
    d_ids = {d.node.id for d in dents.dependents}
    assert "nix:flake.nix" in d_ids
    assert "nix:lib/default.nix" in d_ids

    # observe from lib/default.nix lists bare-import neighbors.
    obs = q.observe("nix:lib/default.nix")
    nbrs = {nb.node.id for nb in obs.neighbors}
    assert "nix:lib/overlays.nix" in nbrs
    assert "nix:lib/modules/home/editors/configs/nvf-config/default.nix" in nbrs

    # BFS (CLI blast/impact path) reaches the same closure.
    reachable = {n.id for n in g.bfs("nix:lib/default.nix", depth=6, width=20)}
    assert "nix:lib/overlays.nix" in reachable
    assert "nix:lib/modules/home/editors/configs/nvf-config/default.nix" in reachable


def _node_types(db: Database) -> set[NodeType]:
    return {n.type for n in db.get_all_nodes()}


def test_e2e_modules_list_edges_and_queries(tmp_path: Path) -> None:
    # Bug-report follow-up scenario: `lib/default.nix` wires the system via
    # `nixosSystem { modules = [ … ] }`. Path literals must become `imports`
    # edges, bare selects (`sops-nix.nixosModules.sops`) must become
    # `references` edges to `flake_input:*` nodes, and the dynamic
    # `(hostPath + /default.nix)` entry is skipped (unresolved).
    _write(
        tmp_path / "flake.nix",
        """
        { inputs }:
        {
          nixosConfigurations.host = (import ./lib).mkHost {};
        }
        """,
    )
    _write(
        tmp_path / "lib" / "default.nix",
        """
        { inputs }:
        let
          inherit (inputs) nixpkgs home-manager sops-nix;
        in
        {
          mkHost = { ... }:
            nixpkgs.lib.nixosSystem {
              inherit system;
              modules = [
                sops-nix.nixosModules.sops
                (hostPath + /default.nix)
                ../modules/nixos
                home-manager.nixosModules.home-manager
                ../modules/nixos/home-manager.nix
              ];
            };
        }
        """,
    )
    _write(
        tmp_path / "modules" / "nixos" / "default.nix",
        "{ config.services.openssh.enable = true; }",
    )
    _write(
        tmp_path / "modules" / "nixos" / "home-manager.nix",
        "{ config.services.foo.enable = true; }",
    )

    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    cfg = Config(root=tmp_path)
    index_repo(tmp_path, db, g, config=cfg)

    edge_set = {(e.source, e.target, e.type) for e in db.get_all_edges()}
    # Path literals from `modules` resolve like imports (dir -> default.nix).
    assert (
        "nix:lib/default.nix",
        "nix:modules/nixos/default.nix",
        EdgeType.imports,
    ) in edge_set
    assert (
        "nix:lib/default.nix",
        "nix:modules/nixos/home-manager.nix",
        EdgeType.imports,
    ) in edge_set
    # Bare selects reference flake inputs (synthetic placeholders here —
    # no flake.lock in this fixture).
    assert (
        "nix:lib/default.nix",
        "flake_input:sops-nix",
        EdgeType.references,
    ) in edge_set
    assert (
        "nix:lib/default.nix",
        "flake_input:home-manager",
        EdgeType.references,
    ) in edge_set

    node_by_id = {n.id: n for n in db.get_all_nodes()}
    assert node_by_id["flake_input:sops-nix"].type == NodeType.flake_input

    # Dependencies closure from the flake reaches the wired modules, and
    # (BUG-004 D1) `references`/`sets` are dependency edges too, so flake
    # inputs and set options are part of the forward chain.
    q = QueryEngine(db, g, config=cfg)
    dep_ids = {d.node.id for d in q.dependencies("nix:flake.nix").depends_on}
    assert "nix:modules/nixos/default.nix" in dep_ids
    assert "nix:modules/nixos/home-manager.nix" in dep_ids
    assert "flake_input:sops-nix" in dep_ids
    assert "flake_input:home-manager" in dep_ids

    # Dependents of a wired module walk back through lib to the flake.
    dent_ids = {
        d.node.id for d in q.dependents("nix:modules/nixos/default.nix").dependents
    }
    assert "nix:lib/default.nix" in dent_ids
    assert "nix:flake.nix" in dent_ids


def test_e2e_python_plugin_activation_qtile_scenario(tmp_path: Path) -> None:
    # Bug-report scenario: python files live under modules/home/wm/qtile/config/
    # (directory named `config`, not `.config`), so two gates block them:
    # 1) plugins must be enabled, 2) parse_unreferenced must be on because no
    # configures edge targets the individual .py file.
    _write(
        tmp_path / "modules" / "home" / "wm" / "qtile" / "config" / "config.py",
        "import libqtile\nimport logging\n"
        "def autostart_apps():\n"
        "    return ['picom']\n",
    )
    _write(
        tmp_path
        / "modules"
        / "home"
        / "wm"
        / "qtile"
        / "config"
        / "settings"
        / "groups.py",
        "from libqtile.config import Group\ngroups = [Group('a')]\n",
    )

    def _index(plugins: list[str], parse_unreferenced: bool) -> Database:
        db = Database(":memory:")
        db.init_db()
        g = NxGraph()
        cfg = Config(
            root=tmp_path,
            plugins=plugins,
            parse_unreferenced=parse_unreferenced,
        )
        index_repo(tmp_path, db, g, config=cfg)
        return db

    # Gate 1: plugin off -> no py nodes at all.
    db = _index(plugins=[], parse_unreferenced=False)
    assert not any(type.value.startswith("py_") for type in _node_types(db))

    # Gate 2: plugin on but no parse_unreferenced -> still nothing (outside
    # .config/ and not referenced by a configures edge).
    db = _index(plugins=["python"], parse_unreferenced=False)
    assert not any(type.value.startswith("py_") for type in _node_types(db))

    # Full activation: module + function + class nodes, python_imports edges,
    # and no package_ref pollution from stdlib/third-party imports.
    db = _index(plugins=["python"], parse_unreferenced=True)
    types = _node_types(db)
    assert NodeType.python_module in types
    assert NodeType.py_function in types
    assert db.get_node("py_func:modules/home/wm/qtile/config/config.py:autostart_apps")
    edges = db.get_all_edges()
    assert any(e.type == EdgeType.python_imports for e in edges)
    # Import targets (libqtile, logging, libqtile.config) are python modules.
    py_imports = {e.target for e in edges if e.type == EdgeType.python_imports}
    assert "py_module:libqtile" in py_imports
    assert "py_module:logging" in py_imports
    imported = [db.get_node(t) for t in py_imports]
    assert all(n.type == NodeType.python_module for n in imported if n is not None)
    assert all(n.type != NodeType.package_ref for n in imported if n is not None)


def test_e2e_twopass_discovers_referenced_python_on_first_run(tmp_path: Path) -> None:
    # Repo: a.nix references scripts/tool.py via home.file. The .py is NOT in
    # .config/, so the Nix-first rule only accepts it through the graph
    # (configures edge). Two-pass bulk index must discover it on run 1.
    _write(
        tmp_path / "b.nix",
        """
        {
          home.file."scripts/tool.py".source = ./scripts/tool.py;
        }
        """,
    )
    _write(
        tmp_path / "scripts" / "tool.py",
        "def run():\n    return 42\n",
    )

    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    cfg = Config(root=tmp_path, plugins=["python"])
    stats = index_repo(tmp_path, db, g, config=cfg)

    # First run already picks up the referenced python file.
    assert db.get_node("nix:b.nix") is not None
    assert db.get_node("file:scripts/tool.py") is not None
    assert db.get_node("py_func:scripts/tool.py:run") is not None
    # Exactly one generation bump (cache invalidation on both passes).
    assert stats["generation"] == 1


def test_e2e_conditional_and_priority(tmp_path: Path) -> None:
    _write(tmp_path / "a.nix", "{ config = lib.mkIf true { x = 1; }; }")
    _write(tmp_path / "b.nix", "{ config.foo = lib.mkForce 42; }")
    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    cfg = Config(root=tmp_path)
    index_repo(tmp_path, db, g, config=cfg)
    edges = db.get_all_edges()
    # mkIf -> conditional True
    assert any(e.metadata.get("conditional") is True for e in edges)
    # mkForce -> priority 'force' (normalised)
    assert any(e.metadata.get("priority") == "force" for e in edges)


def test_e2e_builder_direct_api(tmp_path: Path) -> None:
    # Direct builder usage without index_repo, to test build_file + build_all
    from repo_navigator.parsers.nix_parser import NixParser

    parser = NixParser()
    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    builder = GraphBuilder(db, g)

    content = "{ imports = [ ./b.nix ]; options.test.enable = lib.mkOption {}; }"
    pr = parser.parse(Path("a.nix"), content)
    builder.build_file("a.nix", pr)
    assert db.count_nodes() >= 2
    # Rebuild same file with different content -> old option edge removed
    content2 = "{ config.test.enable = true; }"
    pr2 = parser.parse(Path("a.nix"), content2)
    builder.build_file("a.nix", pr2)
    # Option declare edge should be gone, sets edge present
    edges = db.get_all_edges()
    assert not any(e.type == EdgeType.declares for e in edges)
    assert any(e.type == EdgeType.sets for e in edges)
