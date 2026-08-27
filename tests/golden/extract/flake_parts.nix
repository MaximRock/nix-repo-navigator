{ inputs, ... }: flake-parts.lib.mkFlake {
  imports = [ ./modules ];
}
