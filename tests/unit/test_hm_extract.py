"""Tests for HM extensions (phase 9.3)."""

from __future__ import annotations

from repo_navigator.parsers.nix.ast_extract import extract_source
from repo_navigator.parsers.nix.module_parser import parse_module
from pathlib import Path


def test_home_session_variables() -> None:
    src = '{ home.sessionVariables = { EDITOR = "vim"; FOO = "bar"; }; }'
    r = extract_source(src)
    # Should be in configs with attrpath home.sessionVariables.EDITOR etc.
    assert any(c.attrpath == "home.sessionVariables.EDITOR" for c in r.configs)
    assert any(c.attrpath == "home.sessionVariables.FOO" for c in r.configs)


def test_programs_enable() -> None:
    src = '{ programs.git.enable = true; programs.neovim.enable = false; }'
    r = extract_source(src)
    assert any(c.attrpath == "programs.git.enable" for c in r.configs)
    assert any(c.attrpath == "programs.neovim.enable" for c in r.configs)


def test_programs_enable_attrset() -> None:
    src = '{ programs.git = { enable = true; package = pkgs.git; }; }'
    r = extract_source(src)
    assert any(c.attrpath == "programs.git.enable" for c in r.configs)
    assert any(p.attribute == "pkgs.git" for p in r.packages)


def test_xdg_data_file() -> None:
    src = '{ xdg.dataFile."foo".source = ./foo; }'
    r = extract_source(src)
    assert any(f.target == "foo" for f in r.home_files)


def test_hm_module_parser() -> None:
    src = '''
    {
      home.sessionVariables.EDITOR = "vim";
      programs.git.enable = true;
      xdg.dataFile."bar".source = ./bar;
    }
    '''
    r = extract_source(src)
    pr = parse_module(Path("hm.nix"), r)
    # Should have sets edges for sessionVariables and programs.enable, and configures for dataFile
    assert any(e.target == "nix_option:home.sessionVariables.EDITOR" for e in pr.edges)
    assert any(e.target == "nix_option:programs.git.enable" for e in pr.edges)
    assert any(e.target == "file:bar" for e in pr.edges)


def test_package_via_let_bound_inputs_select() -> None:
    # BUG-002 F2: `home.packages = [ my-pkg ]` with
    # `my-pkg = inputs.comfyui-nix.packages.${system}.rocm` in let
    # must record a flake-input reference next to the package.
    src = (
        "{ pkgs, inputs, system }: let my-pkg = inputs.comfyui-nix.packages.${system}.rocm; in"
        " { home.packages = [ my-pkg ]; }"
    )
    r = extract_source(src)
    assert any(p.attribute == "my-pkg" for p in r.packages)
    assert any(m.name == "comfyui-nix" for m in r.modules)
    # Dynamic `${system}` segment collapses to `…`, no JSON blob leaks in.
    comfy_ref = next(m for m in r.modules if m.name == "comfyui-nix")
    assert comfy_ref.select == "packages.….rocm"
    pr = parse_module(Path("comfyui.nix"), r)
    assert any(
        e.target == "flake_input:comfyui-nix" and e.type.value == "references"
        for e in pr.edges
    )


def test_package_direct_inputs_select() -> None:
    r = extract_source("{ home.packages = [ inputs.foo.bar.baz ]; }")
    assert any(m.name == "foo" and m.select == "bar.baz" for m in r.modules)


def test_package_programs_inputs_select() -> None:
    src = (
        "{ inputs }: { programs.myprog = { enable = true; package = inputs.foo.bar; }; }"
    )
    r = extract_source(src)
    assert any(p.attribute == "inputs.foo.bar" for p in r.packages)
    assert any(m.name == "foo" for m in r.modules)


def test_package_ordinary_selects_have_no_input_ref() -> None:
    r = extract_source("{ pkgs }: { home.packages = [ pkgs.git vim ]; }")
    assert r.modules == []
