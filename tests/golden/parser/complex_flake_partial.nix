{ config, lib, pkgs, ... }@args:
{
  imports = [ ./module.nix ];
  options.test.enable = lib.mkEnableOption "test";
  config = lib.mkIf config.test.enable {
    "a${pkgs.system}" = 1;
    nested.a.b.c = 2;
  };
}
