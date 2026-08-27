"""Module parser: converts ``ExtractedNix`` into ``ParseResult`` (RawNode + RawEdge)."""

from __future__ import annotations

from pathlib import Path

from repo_navigator.models.edges import EdgeType, RawEdge
from repo_navigator.models.nodes import NodeType, RawNode
from repo_navigator.models.queries import ParseResult
from repo_navigator.parsers.nix.ast_extract import ExtractedNix


def parse_module(file_path: Path | str, extracted: ExtractedNix) -> ParseResult:
    """Convert *extracted* metadata into graph-ready nodes and edges."""
    path_str = str(file_path)
    module_id = f"nix:{path_str}"
    nodes: list[RawNode] = []
    edges: list[RawEdge] = []

    nodes.append(
        RawNode(
            id=module_id,
            type=NodeType.nix_module,
            name=path_str,
            path=path_str,
            lang="nix",
        )
    )

    # imports
    for imp in extracted.imports:
        target_id = f"nix:{_normalise_import(path_str, imp.path)}"
        edges.append(
            RawEdge(
                source=module_id,
                target=target_id,
                type=EdgeType.imports,
                metadata={"conditional": imp.conditional, "line": imp.line},
            )
        )

    # options
    for opt in extracted.options:
        opt_id = f"nix_option:{opt.attrpath}"
        nodes.append(
            RawNode(
                id=opt_id,
                type=NodeType.nix_option,
                name=opt.attrpath,
                lang="nix",
                metadata={
                    "opt_type": _meta_str(opt.type),
                    "default": _meta_str(opt.default),
                    "example": _meta_str(opt.example),
                    "description": opt.description or "",
                },
            )
        )
        edges.append(
            RawEdge(
                source=module_id,
                target=opt_id,
                type=EdgeType.declares,
            )
        )

    # configs
    for cfg in extracted.configs:
        if not cfg.attrpath:
            continue
        target_id = f"nix_option:{cfg.attrpath}"
        meta: dict = {"conditional": cfg.conditional}
        if cfg.priority:
            meta["priority"] = cfg.priority
        if cfg.line:
            meta["line"] = cfg.line
        edges.append(
            RawEdge(
                source=module_id,
                target=target_id,
                type=EdgeType.sets,
                metadata=meta,
            )
        )

    # specialisations
    for spec in extracted.specialisations:
        spec_id = f"nix_module:{path_str}::specialisation.{spec.name}"
        nodes.append(
            RawNode(
                id=spec_id,
                type=NodeType.nix_module,
                name=f"specialisation.{spec.name}",
                path=path_str,
                lang="nix",
                metadata={"specialisation": spec.name},
            )
        )
        edges.append(
            RawEdge(
                source=module_id,
                target=spec_id,
                type=EdgeType.specialises,
            )
        )

    # module_args
    for arg in extracted.module_args:
        func_id = f"nix_function:{path_str}:{arg.name}"
        nodes.append(
            RawNode(
                id=func_id,
                type=NodeType.nix_function,
                name=arg.name,
                path=path_str,
                lang="nix",
            )
        )
        edges.append(
            RawEdge(
                source=module_id,
                target=func_id,
                type=EdgeType.passes_args,
            )
        )

    # home_files
    for hf in extracted.home_files:
        file_id = f"file:{hf.target}"
        if not any(n.id == file_id for n in nodes):
            nodes.append(
                RawNode(
                    id=file_id,
                    type=NodeType.file,
                    name=hf.target,
                    lang="nix",
                )
            )
        edges.append(
            RawEdge(
                source=module_id,
                target=file_id,
                type=EdgeType.configures,
            )
        )

    # packages
    for pkg in extracted.packages:
        pkg_id = f"package:{pkg.attribute}"
        if not any(n.id == pkg_id for n in nodes):
            nodes.append(
                RawNode(
                    id=pkg_id,
                    type=NodeType.package_ref,
                    name=pkg.attribute,
                    lang="nix",
                )
            )
        edges.append(
            RawEdge(
                source=module_id,
                target=pkg_id,
                type=EdgeType.uses_package,
            )
        )

    # functions
    for func in extracted.functions:
        func_id = f"nix_function:{path_str}:{func.name}"
        if not any(n.id == func_id for n in nodes):
            nodes.append(
                RawNode(
                    id=func_id,
                    type=NodeType.nix_function,
                    name=func.name,
                    path=path_str,
                    lang="nix",
                    metadata={"args": func.args},
                )
            )
            edges.append(
                RawEdge(
                    source=module_id,
                    target=func_id,
                    type=EdgeType.declares,
                )
            )

    return ParseResult(nodes=nodes, edges=edges)


def _normalise_import(file_path: str, import_path: str) -> str:
    """Normalise an import path relative to the importing file."""
    if import_path.startswith("/") or import_path.startswith("./") or import_path.startswith("../"):
        parent = str(Path(file_path).parent)
        if parent == ".":
            return import_path
        return str(Path(parent) / import_path)
    return import_path


def _meta_str(val: object) -> str:
    """Convert an extract value to a metadata string."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        parts = val.get("path", [])
        if isinstance(parts, list) and parts:
            return ".".join(str(p) for p in parts)
        return str(val.get("value", val))
    return str(val)
