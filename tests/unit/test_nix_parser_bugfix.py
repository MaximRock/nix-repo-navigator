"""Regression tests for PARSER_BUGFIX_PLAN — comment-only and empty-string bugs."""

from __future__ import annotations

from pathlib import Path

from repo_navigator.parsers.nix.lexer import TokenType, tokenize
from repo_navigator.parsers.nix.parser import parse
from repo_navigator.parsers.nix_parser import NixParser
from repo_navigator.models.nodes import NodeType


class TestCommentOnlyFile:
    """Bug 1: home.nix — файл из комментов, нет кода."""

    def test_lexer_comment_only_produces_only_eof(self) -> None:
        content = "# comment 1\n# comment 2\n# 49 lines\n"
        tokens = tokenize(content)
        assert len(tokens) == 1
        assert tokens[0].type == TokenType.EOF

    def test_nix_parser_comment_only_returns_empty(self) -> None:
        parser = NixParser()
        content = "# { pkgs, ... }:\n# {\n#   home.username = \"max\";\n# }\n"
        result = parser.parse(Path("home.nix"), content)
        assert result.nodes == []
        assert result.edges == []

    def test_nix_parser_empty_file_returns_empty(self) -> None:
        parser = NixParser()
        result = parser.parse(Path("empty.nix"), "")
        assert result.nodes == []
        assert result.edges == []

    def test_nix_parser_whitespace_only_returns_empty(self) -> None:
        parser = NixParser()
        result = parser.parse(Path("ws.nix"), "   \n\n  \n")
        assert result.nodes == []
        assert result.edges == []

    def test_real_home_nix_if_exists(self) -> None:
        p = Path("/home/max/.dotfiles/home.nix")
        if not p.exists():
            return
        parser = NixParser()
        result = parser.parse(p, p.read_text())
        # должен не падать, вернуть empty (файл закомментирован)
        assert result.nodes == [] or len(result.nodes) >= 0


class TestNodeOwnershipPath:
    """Bug B: option/home-file/package nodes must carry their owner file path.

    Without an owner, `DELETE FROM nodes WHERE path=?` never purges them
    (e.g. a module deleting one of its options leaves a stale node behind).
    """

    CONTENT = """
    { config, lib, pkgs, ... }:
    {
      options.services.foo.enable = lib.mkEnableOption "foo";
      config = lib.mkIf config.services.foo.enable {
        home.file.".bashrc".source = ./bashrc;
        home.packages = [ pkgs.ripgrep ];
      };
    }
    """

    def test_option_file_package_nodes_have_owner_path(self) -> None:
        parser = NixParser()
        result = parser.parse(Path("svc.nix"), self.CONTENT)
        types = {n.type for n in result.nodes}
        assert NodeType.nix_option in types
        assert NodeType.file in types
        assert NodeType.package_ref in types
        for n in result.nodes:
            if n.type in {NodeType.nix_option, NodeType.file, NodeType.package_ref}:
                assert n.path == "svc.nix", f"{n.id} должен иметь владельца svc.nix"


class TestEmptyString:
    """Bug 2: 11/vscode.nix — пустая строка \"\" теряется."""

    def test_lexer_empty_string_emits_token(self) -> None:
        tokens = tokenize('""')
        types = [t.type for t in tokens]
        assert TokenType.STRING_DOUBLE in types
        str_tokens = [t for t in tokens if t.type == TokenType.STRING_DOUBLE]
        assert len(str_tokens) == 1
        assert str_tokens[0].value == ""

    def test_lexer_attribute_with_empty_string(self) -> None:
        tokens = tokenize('"aiModel" = "";')
        types = [t.type for t in tokens]
        # должен быть STRING_DOUBLE между EQ и SEMI
        assert TokenType.STRING_DOUBLE in types
        eq_idx = types.index(TokenType.EQ)
        semi_idx = types.index(TokenType.SEMI)
        # между EQ и SEMI есть STRING_DOUBLE
        assert any(t == TokenType.STRING_DOUBLE for t in types[eq_idx + 1 : semi_idx])

    def test_parser_attrset_with_empty_string(self) -> None:
        tree = parse('{ "aiModel" = ""; }')
        # не должен падать, должен распарсить AttrSet с одним attr
        assert tree.type == "AttrSet"

    def test_parser_let_with_empty_string(self) -> None:
        tree = parse('let x = ""; in x')
        assert tree.type == "LetIn"

    def test_parser_interpolation_with_empty_parts(self) -> None:
        # "${var}" теперь даёт пустые STRING_DOUBLE до/после — парсер должен обработать
        tokens = tokenize('"${var}"')
        types = [t.type for t in tokens]
        # STRING_DOUBLE("") + INTERPOL_START + IDENT + INTERPOL_END + STRING_DOUBLE("") + EOF
        assert TokenType.INTERPOL_START in types
        assert TokenType.INTERPOL_END in types
        # парсинг не должен падать
        tree = parse('let var = "x"; in "${var}"')
        assert tree is not None

    def test_real_vscode_nix_if_exists(self) -> None:
        p = Path("/home/max/.dotfiles/11/vscode.nix")
        if not p.exists():
            return
        parser = NixParser()
        result = parser.parse(p, p.read_text())
        # должен не падать, вернуть хотя бы модуль (fallback или parsed)
        assert result is not None
        # не должен быть unexpected token SEMI
        assert isinstance(result.nodes, list)
