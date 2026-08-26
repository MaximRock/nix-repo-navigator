{ pkgs, lib, ... }@inputs:
let
  x = inputs.nixpkgs;
in
{ inherit x; }