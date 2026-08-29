{
  description = "nix-repo-navigator — knowledge-graph assistant for NixOS and home-manager";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    pyproject-nix = {
      url = "github:nix-community/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      pyproject-nix,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python3;

        # Call package directly from pyproject.toml using pyproject-nix
        project = pyproject-nix.lib.project.loadPyproject {
          projectRoot = ./.;
        };

        # Build the package
        repo-navigator = project.renderers.buildPythonPackage {
          inherit python;
          # Use python's mkVirtualEnv or buildPythonPackage
          # For simplicity, let's use buildPythonPackage directly
        };

        # Alternative: manual buildPythonPackage
        repo-navigator' = python.pkgs.buildPythonPackage rec {
          pname = "nix-repo-navigator";
          version = "0.1.0";
          pyproject = true;
          src = ./.;
          nativeBuildInputs = with python.pkgs; [ hatchling ];
          propagatedBuildInputs = with python.pkgs; [
            networkx
            pydantic
            pydantic-settings
            typer
            watchdog
            xxhash
            gitpython
            mcp
          ];
          optional-dependencies = {
            dev = with python.pkgs; [
              pytest
              pytest-asyncio
              pytest-cov
            ];
            plugins = with python.pkgs; [
              tree-sitter
              tree-sitter-languages
            ];
          };
          doCheck = false;  # tests require local repo
          meta = {
            description = "Knowledge-graph assistant for NixOS and home-manager repositories";
            homepage = "https://github.com/MaximRock/nix-repo-navigator";
            license = pkgs.lib.licenses.mit;
            maintainers = [ ];
            platforms = pkgs.lib.platforms.linux;
          };
        };
      in
      {
        packages.default = repo-navigator';

        apps.default = {
          type = "app";
          program = "${repo-navigator'}/bin/nix-repo-navigator";
        };

        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            python
            python.pkgs.hatchling
            uv
            tree-sitter
          ];
          shellHook = ''
            if [ ! -d .venv ]; then
              uv venv
              uv pip install -e ".[dev,plugins]"
            fi
            source .venv/bin/activate
            echo "repo-navigator dev shell ready"
          '';
        };
      }
    );
}