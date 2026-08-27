{
  config = mkIf cfg.enable {
    services.foo.enable = true;
  };
}
