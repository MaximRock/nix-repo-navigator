{
  description = "KDL example for repo-navigator";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { self, nixpkgs }: {
    nixosConfigurations.example = nixpkgs.lib.nixosSystem {
      modules = [ ./configuration.nix ];
    };
  };
}
