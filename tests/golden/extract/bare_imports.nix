{ inputs }:

let
  overlays = import ./overlays.nix { inherit inputs; };
  lib = import ./lib;
  inherit (import ./qtile/theme.nix { inherit (pkgs) lib; })
    themeName;
in
{
  mkNixosConfiguration = { hostPath }:
    let
      extra = import ../extra.nix;
    in
    {
      imports = [ ./hardware.nix ];
    };
}