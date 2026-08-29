"""KDL plugin parser (Tier 1, mock without tree-sitter).

Parses a tiny subset of KDL used in examples:
  bind "mod+Return" spawn "kitty"
  rule "window" { spawn "xterm"; }
"""

from __future__ import annotations

import re
from pathlib import Path

from repo_navigator.models.edges import EdgeType, RawEdge
from repo_navigator.models.nodes import NodeType, RawNode
from repo_navigator.models.queries import ParseResult
from repo_navigator.parsers.base import BaseParser
from repo_navigator.parsers.registry import LanguageConfig, register_language

# Simple regexes for KDL
_RE_BIND = re.compile(r'^\s*bind\s+"([^"]+)"\s+spawn\s+"([^"]+)"', re.MULTILINE)
_RE_RULE = re.compile(r'^\s*rule\s+"([^"]+)"', re.MULTILINE)
_RE_SPAWN = re.compile(r'^\s*spawn\s+"([^"]+)"', re.MULTILINE)


@register_language(LanguageConfig(name="kdl", extensions=[".kdl"], tier=1, enabled=True))
class KDLParser(BaseParser):
    language = "kdl"
    extensions = [".kdl"]
    tier = 1
    enabled = True

    def parse(self, path: Path, content: str) -> ParseResult:
        path_str = str(path)
        module_id = f"kdl:{path_str}"
        nodes: list[RawNode] = [
            RawNode(id=module_id, type=NodeType.heading, name=path_str, path=path_str, lang="kdl")
        ]
        edges: list[RawEdge] = []

        # Find binds
        for m in _RE_BIND.finditer(content):
            key, cmd = m.group(1), m.group(2)
            bind_id = f"kdl_bind:{path_str}:{key}"
            nodes.append(RawNode(id=bind_id, type=NodeType.kdl_bind, name=key, path=path_str, lang="kdl", metadata={"command": cmd}))
            edges.append(RawEdge(source=module_id, target=bind_id, type=EdgeType.binds_key))
            # Also spawn edge for the command
            spawn_id = f"kdl_spawn:{path_str}:{cmd}"
            if not any(n.id == spawn_id for n in nodes):
                nodes.append(RawNode(id=spawn_id, type=NodeType.kdl_spawn, name=cmd, path=path_str, lang="kdl"))
            edges.append(RawEdge(source=bind_id, target=spawn_id, type=EdgeType.spawns))

        # Find rules (standalone)
        for m in _RE_RULE.finditer(content):
            rule = m.group(1)
            rule_id = f"kdl_rule:{path_str}:{rule}"
            if not any(n.id == rule_id for n in nodes):
                nodes.append(RawNode(id=rule_id, type=NodeType.kdl_rule, name=rule, path=path_str, lang="kdl"))
                edges.append(RawEdge(source=module_id, target=rule_id, type=EdgeType.binds_key))

        # Find generic spawns not already captured via bind
        for m in _RE_SPAWN.finditer(content):
            cmd = m.group(1)
            spawn_id = f"kdl_spawn:{path_str}:{cmd}"
            if not any(n.id == spawn_id for n in nodes):
                nodes.append(RawNode(id=spawn_id, type=NodeType.kdl_spawn, name=cmd, path=path_str, lang="kdl"))
                # Only add edge if not already via bind
                if not any(e.target == spawn_id and e.type == EdgeType.spawns for e in edges):
                    edges.append(RawEdge(source=module_id, target=spawn_id, type=EdgeType.spawns))

        return ParseResult(nodes=nodes, edges=edges)
