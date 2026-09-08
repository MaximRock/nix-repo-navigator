"""Tests for Python plugin parser (Tier 1)."""

from __future__ import annotations

from pathlib import Path

from repo_navigator.models.edges import EdgeType
from repo_navigator.models.nodes import NodeType
from repo_navigator.parsers.plugins.python import PythonParser

SAMPLE = '''\
import os
from pathlib import Path as P
from collections import defaultdict

GLOBAL_CONST = 42


def greet(name: str, *args, **kwargs) -> str:
    """Return a greeting."""
    return f"Hello {name}"


async def fetch(url: str) -> str:
    ...


@decorator
def wrapped():
    pass


class Animal:
    """Base animal class."""

    def speak(self) -> str:
        return "..."


class Dog(Animal):
    def speak(self) -> str:
        return "Woof"


def main():
    d = Dog()
    greet("max")
    return d.speak()
'''


def test_python_parser_basic() -> None:
    parser = PythonParser()
    result = parser.parse(Path("scripts/main.py"), SAMPLE)

    # Root node
    ids = {n.id for n in result.nodes}
    assert "python:scripts/main.py" in ids

    # Functions (incl. async def)
    py_funcs = {n.name for n in result.nodes if n.type == NodeType.py_function}
    assert {"greet", "fetch", "wrapped", "main"} <= py_funcs

    # Classes
    py_classes = {n.name for n in result.nodes if n.type == NodeType.py_class}
    assert {"Animal", "Dog"} <= py_classes

    # Imports resolved to package_ref nodes + python_imports edges
    import_edges = [e for e in result.edges if e.type == EdgeType.python_imports]
    assert len(import_edges) == 3  # os, pathlib, collections
    import_targets = {e.target for e in import_edges}
    assert {
        "py_module:os",
        "py_module:pathlib",
        "py_module:collections",
    } <= import_targets


def test_python_parser_metadata() -> None:
    parser = PythonParser()
    result = parser.parse(Path("scripts/main.py"), SAMPLE)

    greet = next(
        n for n in result.nodes if n.type == NodeType.py_function and n.name == "greet"
    )
    assert greet.metadata.get("args") == ["name", "*args", "**kwargs"]
    assert greet.metadata.get("docstring") == "Return a greeting."
    assert greet.metadata.get("async") is False

    fetch = next(
        n for n in result.nodes if n.type == NodeType.py_function and n.name == "fetch"
    )
    assert fetch.metadata.get("async") is True

    wrapped = next(
        n
        for n in result.nodes
        if n.type == NodeType.py_function and n.name == "wrapped"
    )
    assert wrapped.metadata.get("decorators") == ["decorator"]

    dog = next(
        n for n in result.nodes if n.type == NodeType.py_class and n.name == "Dog"
    )
    assert dog.metadata.get("bases") == ["Animal"]
    animal = next(
        n for n in result.nodes if n.type == NodeType.py_class and n.name == "Animal"
    )
    assert animal.metadata.get("docstring") == "Base animal class."


def test_python_parser_calls_and_declares() -> None:
    parser = PythonParser()
    result = parser.parse(Path("scripts/main.py"), SAMPLE)

    # declares edges from module to functions/classes
    declares = [e for e in result.edges if e.type == EdgeType.declares]
    assert len(declares) == len(
        [n for n in result.nodes if n.type in (NodeType.py_function, NodeType.py_class)]
    )

    # calls edges: main() calls Dog(), greet() inside main
    calls = [e for e in result.edges if e.type == EdgeType.calls]
    call_targets = {e.target for e in calls}
    assert "py_func:scripts/main.py:greet" in call_targets
    assert "py_class:scripts/main.py:Dog" in call_targets


def test_python_parser_syntax_error() -> None:
    parser = PythonParser()
    result = parser.parse(Path("broken.py"), "def foo(:\n")
    # Should still return module heading node, no crash
    assert len(result.nodes) == 1
    assert result.nodes[0].type == NodeType.heading


def test_python_parser_registry() -> None:
    from repo_navigator.parsers.registry import (
        get_parser_for_file,
        get_parser_for_language,
    )

    parser = get_parser_for_language("python")
    assert parser is not None
    assert parser.language == "python"
    assert get_parser_for_file("foo.py") is not None
    assert (
        get_parser_for_file("foo.kdl") is None
        or get_parser_for_file("foo.kdl").language == "kdl"
    )


def test_python_should_parse_with_config(tmp_path: Path) -> None:
    from repo_navigator.config import Config
    from repo_navigator.parsers.registry import should_parse_file

    p = tmp_path / ".config" / "python" / "main.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("import os\n")
    cfg_no = Config(root=tmp_path, plugins=[])
    assert should_parse_file(p, config=cfg_no) is False
    cfg_yes = Config(root=tmp_path, plugins=["python"])
    assert should_parse_file(p, config=cfg_yes) is True
