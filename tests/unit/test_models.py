"""Unit tests for Pydantic models: validation, defaults, serialization."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from repo_navigator.models import (
    Edge,
    EdgeType,
    EvalResult,
    FileState,
    ImpactReport,
    ModuleSummary,
    Neighbor,
    Node,
    NodeType,
    Observation,
    OptionInfo,
    OptionValue,
    ParseResult,
    PathStep,
    RawEdge,
    RawNode,
    RiskLevel,
    StatusResponse,
    Subgraph,
    SyncMode,
    ValueStatus,
)


# ---------------------------------------------------------------- nodes


def make_node(**overrides) -> Node:
    defaults = dict(
        id="nix_option:services.nginx.enable",
        type=NodeType.nix_option,
        name="services.nginx.enable",
        path="modules/services/nginx.nix",
        lang="nix",
        metadata={"opt_type": "types.bool", "default": "false"},
    )
    defaults.update(overrides)
    return Node(**defaults)


class TestNodeType:
    def test_core_values(self) -> None:
        assert NodeType("nix_module") is NodeType.nix_module
        assert {t.value for t in NodeType} >= {
            "nix_module",
            "nix_option",
            "nix_function",
            "flake_input",
            "package_ref",
            "file",
        }

    def test_is_strenum(self) -> None:
        assert NodeType.nix_option.value == "nix_option"
        assert isinstance(NodeType.nix_option, str)


class TestNode:
    def test_validation_minimal(self) -> None:
        node = Node(id="file:README.md", type=NodeType.file, name="README.md")
        assert node.lang == "nix"
        assert node.metadata == {}
        assert node.content_hash is None
        assert node.created_at.tzinfo is not None

    def test_unknown_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Node(id="x", type="nope", name="x")

    def test_json_roundtrip(self) -> None:
        node = make_node()
        data = json.loads(node.model_dump_json())
        restored = Node.model_validate(data)
        assert restored == node

    def test_datetime_defaults_utc(self) -> None:
        node = make_node()
        now = datetime.now(timezone.utc)
        assert (now - node.created_at).total_seconds() < 5
        assert node.updated_at == node.created_at or (
            now - node.updated_at
        ).total_seconds() < 5


class TestRawNode:
    def test_defaults(self) -> None:
        raw = RawNode(id="nix:flake.nix", type=NodeType.nix_module, name="flake.nix")
        assert raw.path is None
        assert raw.metadata == {}

    def test_to_dict_matches_golden_shape(self) -> None:
        raw = RawNode(
            id="nix:modules/foo.nix",
            type=NodeType.nix_module,
            name="modules/foo.nix",
            path="modules/foo.nix",
            lang="nix",
        )
        data = raw.model_dump()
        assert data["id"] == "nix:modules/foo.nix"
        assert data["type"] == "nix_module"
        assert data["lang"] == "nix"


# ---------------------------------------------------------------- edges


class TestEdgeType:
    def test_core_relations_present(self) -> None:
        assert {e.value for e in EdgeType} >= {
            "imports",
            "declares",
            "sets",
            "specialises",
            "passes_args",
            "configures",
            "generates",
            "uses_package",
        }

    def test_plugin_relations_present(self) -> None:
        assert {"calls", "binds_key", "spawns", "requires"} <= {
            e.value for e in EdgeType
        }


class TestEdge:
    def test_edge_roundtrip(self) -> None:
        edge = Edge(
            id="imports:nix:a.nix->nix:b.nix",
            source="nix:a.nix",
            target="nix:b.nix",
            type=EdgeType.imports,
            metadata={"line": 3},
        )
        restored = Edge.model_validate(json.loads(edge.model_dump_json()))
        assert restored == edge
        assert restored.weight == 1.0

    def test_unknown_relation_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Edge(id="1", source="a", target="b", type="hugs")


class TestRawEdge:
    def test_no_id_required(self) -> None:
        raw = RawEdge(source="nix:a.nix", target="nix:b.nix", type=EdgeType.imports)
        assert raw.metadata == {}


# ---------------------------------------------------------------- file_state


class TestFileState:
    def test_defaults(self) -> None:
        fs = FileState(path="flake.nix", lang="nix", content_hash="abc123")
        assert fs.dirty is False
        assert fs.ast_hash is None
        assert fs.last_parsed is None

    def test_invalid_detail_level_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FileState(
                path="x", lang="nix", content_hash="h", detail_level="deeply"
            )


# ---------------------------------------------------------------- option_value


class TestOptionValue:
    def test_default_status_ok(self) -> None:
        ov = OptionValue(key="k1", expr="config.services.nginx.enable")
        assert ov.status is ValueStatus.ok
        assert ov.value_json is None

    def test_status_enum_roundtrip(self) -> None:
        ov = OptionValue(key="k", expr="e", status=ValueStatus.stale)
        data = json.loads(ov.model_dump_json())
        assert data["status"] == "stale"
        assert ValueStatus(data["status"]) is ValueStatus.stale


# ---------------------------------------------------------------- queries


def make_module_node(path: str) -> Node:
    return Node(id=f"nix:{path}", type=NodeType.nix_module, name=path, path=path)


class TestObservation:
    def test_neighbors_pairing(self) -> None:
        module = make_module_node("modules/a.nix")
        imported = make_module_node("modules/b.nix")
        edge = Edge(
            id="e1",
            source=module.id,
            target=imported.id,
            type=EdgeType.imports,
        )
        obs = Observation(
            node=module,
            neighbors=[Neighbor(edge=edge, node=imported)],
            generation_id=42,
        )
        data = json.loads(obs.model_dump_json())
        assert data["generation_id"] == 42
        assert data["neighbors"][0]["edge"]["type"] == "imports"

    def test_empty_neighbors_default(self) -> None:
        obs = Observation(node=make_module_node("a.nix"), generation_id=1)
        assert obs.neighbors == []


class TestSubgraphAndPath:
    def test_subgraph(self) -> None:
        sg = Subgraph(
            nodes=[make_module_node("a.nix"), make_module_node("b.nix")],
            generation_id=7,
        )
        assert len(sg.nodes) == 2
        assert sg.edges == []

    def test_path_step_first_has_no_edge_in(self) -> None:
        step = PathStep(node=make_module_node("a.nix"), depth=0)
        assert step.edge_in is None


class TestOptionInfo:
    def test_full_construction(self) -> None:
        info = OptionInfo(
            option_path="services.nginx.enable",
            opt_type="types.bool",
            default="false",
            description="Whether to enable nginx.",
            declared_in="modules/services/nginx.nix",
            defined_in=["hosts/desktop.nix"],
            conditional_sets=["hosts/laptop.nix"],
            value=True,
            value_status="ok",
            generation_id=3,
        )
        assert info.value is True

    def test_value_can_be_any_json(self) -> None:
        info = OptionInfo(option_path="x", value={"nested": [1, 2]}, generation_id=1)
        assert info.value == {"nested": [1, 2]}


class TestEvalResult:
    def test_defaults(self) -> None:
        res = EvalResult(expr="config.x", generation_id=1)
        assert res.status is ValueStatus.unresolved
        assert res.cached is False


class TestImpactReport:
    def test_risk_level_enum(self) -> None:
        report = ImpactReport(
            target="nix:base.nix",
            affected_modules=["nix:a.nix"],
            risk_level=RiskLevel.high,
            generation_id=9,
        )
        assert json.loads(report.model_dump_json())["risk_level"] == "high"


class TestModuleSummary:
    def test_lists_default_empty(self) -> None:
        summary = ModuleSummary(path="flake.nix", generation_id=2)
        assert summary.incoming_edges == []
        assert summary.key_symbols == []


class TestStatusResponse:
    def test_sync_progress_tuple(self) -> None:
        status = StatusResponse(
            mode=SyncMode.hybrid,
            total_nodes=100,
            total_edges=150,
            uptime=12.5,
            sync_progress=(30, 200),
            generation_id=4,
        )
        data = json.loads(status.model_dump_json())
        assert data["sync_progress"] == [30, 200]

    def test_mode_static(self) -> None:
        status = StatusResponse(
            mode=SyncMode.static, total_nodes=0, total_edges=0, uptime=0.0,
            generation_id=0,
        )
        assert status.mode == SyncMode.static


class TestParseResult:
    def test_holds_raw_structures(self) -> None:
        result = ParseResult(
            nodes=[RawNode(id="nix:a.nix", type=NodeType.nix_module, name="a.nix")],
            edges=[RawEdge(source="nix:a.nix", target="nix:b.nix", type=EdgeType.imports)],
        )
        assert result.nodes[0].type is NodeType.nix_module
        assert result.edges[0].type is EdgeType.imports

    def test_empty_result_valid(self) -> None:
        result = ParseResult()
        assert result.nodes == [] and result.edges == []
