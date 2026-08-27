"""Unit tests for ast_extract (phase 3.1)."""

from __future__ import annotations

from repo_navigator.parsers.nix.ast_extract import extract_source


class TestImports:
    def test_simple_imports(self) -> None:
        r = extract_source("{ imports = [ ./a.nix ./b.nix ]; }")
        assert len(r.imports) == 2
        assert r.imports[0].path == "./a.nix"
        assert r.imports[1].path == "./b.nix"
        assert all(not i.conditional for i in r.imports)

    def test_conditional_import(self) -> None:
        r = extract_source("{ config = mkIf true { imports = [ ./x.nix ]; }; }")
        assert len(r.imports) == 1
        assert r.imports[0].conditional is True

    def test_dynamic_import_unresolved(self) -> None:
        r = extract_source("{ imports = [ (import ./auto.nix) ]; }")
        assert len(r.imports) == 0
        assert len(r.unresolved) == 1
        assert "dynamic import" in r.unresolved[0].reason


class TestOptions:
    def test_simple_option(self) -> None:
        r = extract_source("{ options.x = mkOption { type = types.bool; }; }")
        assert len(r.options) == 1
        assert r.options[0].attrpath == "x"

    def test_nested_option(self) -> None:
        r = extract_source(
            "{ options.services.foo.enable = mkOption { type = types.bool; }; }"
        )
        assert len(r.options) == 1
        assert r.options[0].attrpath == "services.foo.enable"

    def test_option_metadata(self) -> None:
        r = extract_source(
            '{ options.x = mkOption { type = types.bool; default = false; description = "test"; }; }'
        )
        assert r.options[0].description == "test"


class TestConfigs:
    def test_simple_config(self) -> None:
        r = extract_source("{ config.x = true; }")
        assert len(r.configs) == 1
        assert r.configs[0].attrpath == "x"
        assert r.configs[0].conditional is False

    def test_mkif_conditional(self) -> None:
        r = extract_source("{ config = mkIf cfg.enable { x = true; }; }")
        assert len(r.configs) == 1
        assert r.configs[0].conditional is True

    def test_mkforce_priority(self) -> None:
        r = extract_source("{ config.x = mkForce 42; }")
        assert len(r.configs) == 1
        assert r.configs[0].priority == "force"

    def test_mkdefault_priority(self) -> None:
        r = extract_source("{ config.x = mkDefault 42; }")
        assert len(r.configs) == 1
        assert r.configs[0].priority == "default"


class TestSpecialisation:
    def test_specialisation(self) -> None:
        r = extract_source(
            "{ specialisation.desktop.configuration = { x = 1; }; }"
        )
        assert len(r.specialisations) == 1
        assert r.specialisations[0].name == "desktop"


class TestModuleArgs:
    def test_module_args(self) -> None:
        r = extract_source("{ _module.args.myLib = null; }")
        assert len(r.module_args) == 1
        assert r.module_args[0].name == "myLib"


class TestHomeFiles:
    def test_home_file(self) -> None:
        r = extract_source('{ home.file.".config/foo".source = ./foo; }')
        assert len(r.home_files) == 1
        assert r.home_files[0].target == ".config/foo"

    def test_xdg_config(self) -> None:
        r = extract_source('{ xdg.configFile.".config/bar".source = ./bar; }')
        assert len(r.home_files) == 1
        assert r.home_files[0].target == ".config/bar"


class TestPackages:
    def test_home_packages(self) -> None:
        r = extract_source("{ home.packages = [ pkgs.ripgrep pkgs.fd ]; }")
        assert len(r.packages) == 2
        assert r.packages[0].attribute == "pkgs.ripgrep"
        assert r.packages[1].attribute == "pkgs.fd"

    def test_program_package(self) -> None:
        r = extract_source("{ programs.git.package = pkgs.git; }")
        assert len(r.packages) == 1
        assert r.packages[0].attribute == "pkgs.git"


class TestFunctions:
    def test_function_decl(self) -> None:
        r = extract_source("{ myFunc = x: x + 1; }")
        assert len(r.functions) == 1
        assert r.functions[0].name == "myFunc"
        assert r.functions[0].args == ["x"]
