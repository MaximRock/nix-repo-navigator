"""Python plugin parser (Tier 1).

Uses the stdlib ``ast`` module to extract:
- function definitions  → ``py_function`` nodes
- class definitions      → ``py_class`` nodes
- import statements     → ``python_imports`` edges
- function calls        → ``calls`` edges
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from repo_navigator.models.edges import EdgeType, RawEdge
from repo_navigator.models.nodes import NodeType, RawNode
from repo_navigator.models.queries import ParseResult
from repo_navigator.parsers.base import BaseParser
from repo_navigator.parsers.registry import LanguageConfig, register_language

log = logging.getLogger(__name__)


def _func_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Return a flat list of argument names (positional + keyword)."""
    args: list[str] = []
    for arg in node.args.args:
        args.append(arg.arg)
    for arg in node.args.posonlyargs:
        args.append(arg.arg)
    for arg in node.args.kwonlyargs:
        args.append(arg.arg)
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")
    return args


def _decorators(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> list[str]:
    """Return decorator names (best-effort, no source introspection)."""
    names: list[str] = []
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name):
            names.append(dec.id)
        elif isinstance(dec, ast.Attribute):
            names.append(dec.attr)
        elif isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Name):
                names.append(dec.func.id)
            elif isinstance(dec.func, ast.Attribute):
                names.append(dec.func.attr)
    return names


def _docstring(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> str | None:
    """Return the first line of the docstring, if present."""
    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
    ):
        text = getattr(body[0].value, "value", None)
        if isinstance(text, str):
            return text.split("\n", 1)[0]
    return None


class _ImportCollector(ast.NodeVisitor):
    """Walk the AST to collect ``from X import Y`` and ``import X`` targets."""

    def __init__(self) -> None:
        self.imports: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.append(node.module)
        self.generic_visit(node)


class _CallCollector(ast.NodeVisitor):
    """Walk the AST to collect simple function-call names (best-effort)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []  # (name, lineno)

    def visit_Call(self, node: ast.Call) -> None:
        name: str | None = None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name:
            self.calls.append((name, getattr(node, "lineno", 0)))
        self.generic_visit(node)


@register_language(
    LanguageConfig(name="python", extensions=[".py"], tier=1, enabled=True)
)
class PythonParser(BaseParser):
    language = "python"
    extensions = [".py"]
    tier = 1
    enabled = True

    def parse(self, path: Path, content: str) -> ParseResult:
        path_str = str(path)
        module_id = f"python:{path_str}"

        nodes: list[RawNode] = [
            RawNode(
                id=module_id,
                type=NodeType.heading,
                name=path_str,
                path=path_str,
                lang="python",
            )
        ]
        edges: list[RawEdge] = []

        try:
            tree = ast.parse(content, filename=path_str)
        except SyntaxError:
            log.warning("Python syntax error in %s, returning file node only", path)
            return ParseResult(nodes=nodes, edges=edges)

        # --- functions ---
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name
            func_id = f"py_func:{path_str}:{name}"
            meta: dict[str, object] = {"line": node.lineno}
            args = _func_args(node)
            if args:
                meta["args"] = args
            decs = _decorators(node)
            if decs:
                meta["decorators"] = decs
            doc = _docstring(node)
            if doc:
                meta["docstring"] = doc
            meta["async"] = isinstance(node, ast.AsyncFunctionDef)

            nodes.append(
                RawNode(
                    id=func_id,
                    type=NodeType.py_function,
                    name=name,
                    path=path_str,
                    lang="python",
                    metadata=meta,
                )
            )
            edges.append(
                RawEdge(source=module_id, target=func_id, type=EdgeType.declares)
            )

        # --- classes ---
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            name = node.name
            class_id = f"py_class:{path_str}:{name}"
            meta_class: dict[str, object] = {"line": node.lineno}
            bases: list[str] = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    bases.append(base.id)
                elif isinstance(base, ast.Attribute):
                    bases.append(f"{ast.dump(base)}")
            if bases:
                meta_class["bases"] = bases
            decs = _decorators(node)
            if decs:
                meta_class["decorators"] = decs
            doc = _docstring(node)
            if doc:
                meta_class["docstring"] = doc

            nodes.append(
                RawNode(
                    id=class_id,
                    type=NodeType.py_class,
                    name=name,
                    path=path_str,
                    lang="python",
                    metadata=meta_class,
                )
            )
            edges.append(
                RawEdge(source=module_id, target=class_id, type=EdgeType.declares)
            )

        # --- imports → python_imports edges ---
        collector = _ImportCollector()
        collector.visit(tree)
        for mod_name in collector.imports:
            target_id = f"py_module:{mod_name}"
            # Ensure target node exists (deduplicated)
            if not any(n.id == target_id for n in nodes):
                nodes.append(
                    RawNode(
                        id=target_id,
                        type=NodeType.package_ref,
                        name=mod_name,
                        path=path_str,
                        lang="python",
                    )
                )
            edges.append(
                RawEdge(
                    source=module_id,
                    target=target_id,
                    type=EdgeType.python_imports,
                )
            )

        # --- calls → calls edges (limited to functions defined in the same file) ---
        call_collector = _CallCollector()
        call_collector.visit(tree)
        known_symbols = {
            n.name: n.id
            for n in nodes
            if n.type in (NodeType.py_function, NodeType.py_class)
        }
        for call_name, lineno in call_collector.calls:
            if call_name in known_symbols:
                edges.append(
                    RawEdge(
                        source=module_id,
                        target=known_symbols[call_name],
                        type=EdgeType.calls,
                        metadata={"line": lineno},
                    )
                )

        return ParseResult(nodes=nodes, edges=edges)
