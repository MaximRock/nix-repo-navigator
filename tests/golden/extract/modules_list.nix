{ inputs }:

let
  inherit (inputs) nixpkgs home-manager sops-nix;
  lib = import ./lib;
in
{
  mkNixosConfiguration =
    { hostName, hostPath }:
    nixpkgs.lib.nixosSystem {
      inherit system;
      modules = [
        sops-nix.nixosModules.sops
        (hostPath + /default.nix)
        ../modules/nixos
        home-manager.nixosModules.home-manager
        ../modules/nixos/home-manager.nix
      ];
    };
}
