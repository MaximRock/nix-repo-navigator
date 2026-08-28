"""Unit tests for QueryEngine introspect/eval (phase 6.2)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_navigator.graph.builder import GraphBuilder
from repo_navigator.graph.db import Database
from repo_navigator.graph.nx_graph import NxGraph
from repo_navigator.graph.queries import QueryEngine
from repo_navigator.models.edges import EdgeType, RawEdge
from repo_navigator.models.nodes import NodeType, RawNode
from repo_navigator.models.queries import ParseResult


def _mod(path: str) -> RawNode:
    return RawNode(id=f"nix:{path}", type=NodeType.nix_module, name=path, path=path)


def _setup_option_graph() -> tuple[Database, NxGraph, QueryEngine]:
    db = Database(":memory:")
    db.init_db()
    g = NxGraph()
    builder = GraphBuilder(db, g)
    # b declares option, a sets it conditionally
    builder.build_file(
        "b.nix",
        ParseResult(
            nodes=[
                _mod("b.nix"),
                RawNode(id="nix_option:services.foo.enable", type=NodeType.nix_option, name="services.foo.enable", metadata={"opt_type": "bool", "default": "false", "description": "foo"}),
            ],
            edges=[RawEdge(source="nix:b.nix", target="nix_option:services.foo.enable", type=EdgeType.declares)],
        ),
    )
    builder.build_file(
        "a.nix",
        ParseResult(
            nodes=[_mod("a.nix")],
            edges=[
                RawEdge(source="nix:a.nix", target="nix_option:services.foo.enable", type=EdgeType.sets, metadata={"conditional": False}),
                RawEdge(source="nix:a.nix", target="nix_option:services.foo.enable", type=EdgeType.sets, metadata={"conditional": True}),
            ],
        ),
    )
    engine = QueryEngine(db, g)
    return db, g, engine


class TestIntrospectOption:
    def test_static_introspection(self) -> None:
        _, _, engine = _setup_option_graph()
        info = engine.introspect_option("services.foo.enable")
        assert info.option_path == "services.foo.enable"
        assert info.opt_type == "bool"
        assert info.default == "false"
        assert info.description == "foo"
        assert info.declared_in == "b.nix"
        assert "a.nix" in info.defined_in
        assert "a.nix" in info.conditional_sets
        assert info.generation_id >= 1

    def test_missing_option(self) -> None:
        _, _, engine = _setup_option_graph()
        info = engine.introspect_option("services.missing")
        assert info.option_path == "services.missing"
        assert info.declared_in is None
        assert info.defined_in == []
        assert info.value is None

    def test_include_value_with_mock(self) -> None:
        _, _, engine = _setup_option_graph()
        # Mock eval to return 42
        with patch("repo_navigator.nix.eval.subprocess.run") as mock_run, patch(
            "repo_navigator.nix.eval.shutil.which", return_value="/nix/bin/nix"
        ):
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = json.dumps(42)
            mock_run.return_value.stderr = ""
            info = engine.introspect_option("services.foo.enable", include_value=True)
            assert info.value == 42
            assert info.value_status == "ok"


class TestEvalExpression:
    def test_eval_caches(self) -> None:
        db = Database(":memory:")
        db.init_db()
        g = NxGraph()
        engine = QueryEngine(db, g)
        with patch("repo_navigator.nix.eval.subprocess.run") as mock_run, patch(
            "repo_navigator.nix.eval.shutil.which", return_value="/nix/bin/nix"
        ):
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = json.dumps({"a": 1})
            mock_run.return_value.stderr = ""
            res1 = engine.eval_expression("1+1")
            assert res1.value_json == {"a": 1}
            assert res1.cached is False
            assert res1.status.value == "ok"
            # Second call should be cached (DB hit)
            res2 = engine.eval_expression("1+1")
            assert res2.cached is True
            assert res2.value_json == {"a": 1}
            # subprocess should have been called only once (second is cache)
            assert mock_run.call_count == 1

    def test_eval_no_nix(self) -> None:
        db = Database(":memory:")
        db.init_db()
        g = NxGraph()
        engine = QueryEngine(db, g)
        with patch("repo_navigator.nix.eval.shutil.which", return_value=None):
            res = engine.eval_expression("1+1")
            assert res.status.value == "unresolved"
            assert "nix not found" in (res.error or "")

    def test_eval_error(self) -> None:
        db = Database(":memory:")
        db.init_db()
        g = NxGraph()
        engine = QueryEngine(db, g)
        with patch("repo_navigator.nix.eval.subprocess.run") as mock_run, patch(
            "repo_navigator.nix.eval.shutil.which", return_value="/nix/bin/nix"
        ):
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = "error: infinite recursion"
            res = engine.eval_expression("bad")
            assert res.status.value == "unresolved"
            assert "infinite recursion" in (res.error or "")

    def test_eval_timeout(self) -> None:
        db = Database(":memory:")
        db.init_db()
        g = NxGraph()
        engine = QueryEngine(db, g)
        with patch("repo_navigator.nix.eval.subprocess.run", side_effect=__import__("subprocess").TimeoutExpired(cmd="nix", timeout=1)), patch(
            "repo_navigator.nix.eval.shutil.which", return_value="/nix/bin/nix"
        ):
            res = engine.eval_expression("1+1", timeout=1)
            assert res.status.value == "error"
            assert "timed out" in (res.error or "")

    def test_eval_timeout_validation(self) -> None:
        db = Database(":memory:")
        db.init_db()
        g = NxGraph()
        engine = QueryEngine(db, g)
        with pytest.raises(ValueError, match="timeout must be <=120"):
            engine.eval_expression("1+1", timeout=200)
