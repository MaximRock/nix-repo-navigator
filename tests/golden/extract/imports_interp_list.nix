{ config, ... }:
let modulesHome = toString ../../modules/home; in
{
  imports = [
    "${modulesHome}/ai-agents/comfyui"
    "${modulesHome}/ai-agents/nix-repo-navigator"
    "${modulesHome}/ai-agents/opencode"
  ];
}
