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

        # Load dependencies (single source of truth) from pyproject.toml
        project = pyproject-nix.lib.project.loadPyproject {
          projectRoot = ./.;
        };

        # Override python with mcp 2.x (nixpkgs pins mcp 1.x) and its mcp-types dep.
        python' =
          let
            base = pkgs.python3;
          in
          base.override {
            packageOverrides =
              _self: super:
              let
                mcp-types = super.buildPythonPackage rec {
                  pname = "mcp-types";
                  version = "2.1.1";
                  src = pkgs.fetchPypi {
                    pname = "mcp_types";
                    inherit version;
                    hash = "sha256-d9y+SPunPMpxpnPyZGpfA3oBe3oKB6yJzsERMCiJDto=";
                  };
                  pyproject = true;
                  nativeBuildInputs = with super; [
                    hatchling
                    uv-dynamic-versioning
                  ];
                  propagatedBuildInputs = with super; [
                    pydantic
                    typing-extensions
                  ];
                };

                mcp = super.buildPythonPackage rec {
                  pname = "mcp";
                  version = "2.1.1";
                  src = pkgs.fetchPypi {
                    inherit pname version;
                    hash = "sha256-ULe6HrvhFwCOp73SiCNAQ+acILQD1oUdGWYebUMade8=";
                  };
                  pyproject = true;
                  nativeBuildInputs = with super; [
                    hatchling
                    uv-dynamic-versioning
                  ];
                  propagatedBuildInputs = with super; [
                    anyio
                    httpx2
                    jsonschema
                    mcp-types
                    opentelemetry-api
                    pydantic
                    pyjwt
                    cryptography
                    python-multipart
                    sse-starlette
                    starlette
                    typing-extensions
                    typing-inspection
                    uvicorn
                  ];
                  passthru.optional-dependencies = super.mcp.optional-dependencies or { };
                };
              in
              {
                inherit mcp mcp-types;
              };
          };

        # Build the package on top of the overridden python set, pulling
        # dependencies from pyproject.toml (single source of truth).
        # Note: optional plugins ([tool... "plugins"]) are kept in the dev
        # shell (via uv, from PyPI) — nixpkgs' tree-sitter-languages is broken.
        repo-navigator = python'.pkgs.buildPythonPackage (
          project.renderers.buildPythonPackage {
            python = python';
          }
          // {
            pythonImportsCheck = [
              "mcp.server.mcpserver"
              "repo_navigator.mcp_server"
            ];
            doCheck = false;
          }
        );
      in
      {
        packages.default = repo-navigator;

        apps.default = {
          type = "app";
          program = "${repo-navigator}/bin/nix-repo-navigator";
        };

        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            python'
            python'.pkgs.hatchling
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
