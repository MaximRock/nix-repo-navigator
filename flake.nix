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
        lib = pkgs.lib;

        # Single place to bump pinned fallback versions (used only when nixpkgs
        # ships something too old: mcp <2.0 or httpx2 <2.5.0).
        mcpVersion = "2.1.1";
        mcpHash = "sha256-ULe6HrvhFwCOp73SiCNAQ+acILQD1oUdGWYebUMade8=";
        mcpTypesVersion = "2.1.1";
        mcpTypesHash = "sha256-d9y+SPunPMpxpnPyZGpfA3oBe3oKB6yJzsERMCiJDto=";
        idnaVersion = "3.18";
        idnaHash = "sha256-/7OFp+A5ZUzvGrnvMsb6/ig8DARnu6HZApc4zkoUqEg=";
        httpx2Version = "2.9.1";
        httpx2Hash = "sha256-GTKnaHN+NmYpFYKDPadIzE5WPDN8+WcG/MwE+m5Ydko=";
        httpcore2Hash = "sha256-TYrL+LMG9IydYEZZH9W6QDfRsbEADRQPwsPqsemgwOI=";

        # Load dependencies (single source of truth) from pyproject.toml
        project = pyproject-nix.lib.project.loadPyproject {
          projectRoot = ./.;
        };

        # Detect what the un-overridden nixpkgs already ships (read from the
        # pristine set, NOT from `super` inside packageOverrides — reading
        # version of an overridden package there recurses).
        basePyPkgs = pkgs.python3.pkgs;
        # Has a usable mcp 2.x already? Then everything (httpx2 etc.) is
        # satisfiable and no overrides are needed at all.
        hasMcp2 = basePyPkgs ? mcp && lib.versionAtLeast basePyPkgs.mcp.version "2.0";
        # mcp 2.x requires httpx2 >=2.5.0; skip the httpx2 chain if the
        # shipped httpx2 is already new enough.
        hasHttpx2 = basePyPkgs ? httpx2 && lib.versionAtLeast basePyPkgs.httpx2.version "2.5.0";

        # Override python only when nixpkgs ships an mcp/httpx2 that is too old
        # for a working mcp 2.x (nixpkgs still pins mcp 1.x).
        python' =
          let
            base = pkgs.python3;
          in
          base.override {
            packageOverrides =
              _self: super:
              let
                mcp-types = if hasMcp2 then super.mcp-types else super.buildPythonPackage rec {
                  pname = "mcp-types";
                  version = mcpTypesVersion;
                  src = pkgs.fetchPypi {
                    pname = "mcp_types";
                    inherit version;
                    hash = mcpTypesHash;
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

                # Note: when mcp is pinned (hasMcp2 false) it must reference the
                # *selected* httpx2 below (its own pin OR nixpkgs' new enough
                # one), otherwise a stale httpx2 chain would smuggle in a
                # conflicting idna; when hasMcp2 is true we reuse super.mcp.
                mcp = if hasMcp2 then super.mcp else super.buildPythonPackage rec {
                  pname = "mcp";
                  version = mcpVersion;
                  src = pkgs.fetchPypi {
                    inherit pname version;
                    hash = mcpHash;
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

                # Pin idna >=3.18 (httpx2 requirement) — nixpkgs 26.05 ships 3.15.
                idna = if hasHttpx2 then super.idna else super.buildPythonPackage rec {
                  pname = "idna";
                  version = idnaVersion;
                  src = pkgs.fetchPypi {
                    inherit pname version;
                    hash = idnaHash;
                  };
                  pyproject = true;
                  nativeBuildInputs = with super; [
                    flit-core
                  ];
                };

                # Pin httpcore2 to httpx2's exact requirement (httpx2 pins ==2.9.1)
                # — nixpkgs 26.05 ships 2.3.0.
                httpcore2 = if hasHttpx2 then super.httpcore2 else super.buildPythonPackage rec {
                  pname = "httpcore2";
                  version = httpx2Version;
                  src = pkgs.fetchPypi {
                    inherit pname version;
                    hash = httpcore2Hash;
                  };
                  pyproject = true;
                  nativeBuildInputs = with super; [
                    hatchling
                    hatch-fancy-pypi-readme
                    uv-dynamic-versioning
                  ];
                  propagatedBuildInputs = with super; [
                    h11
                    truststore
                  ];
                };

                # Pin httpx2 >=2.5.0 (mcp 2.x requires it) — nixpkgs 26.05 ships 2.3.0.
                httpx2 = if hasHttpx2 then super.httpx2 else super.buildPythonPackage rec {
                  pname = "httpx2";
                  version = httpx2Version;
                  src = pkgs.fetchPypi {
                    inherit pname version;
                    hash = httpx2Hash;
                  };
                  pyproject = true;
                  nativeBuildInputs = with super; [
                    hatchling
                    hatch-fancy-pypi-readme
                    uv-dynamic-versioning
                  ];
                  propagatedBuildInputs = with super; [
                    anyio
                    httpcore2
                    idna
                    truststore
                    typing-extensions
                  ];
                };
              in
              {
                inherit idna httpcore2 httpx2 mcp mcp-types;
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
