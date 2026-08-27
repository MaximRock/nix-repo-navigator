{
  options.services.foo = {
    enable = mkOption {
      type = types.bool;
      default = false;
      example = true;
      description = "Whether to enable foo.";
    };
    port = mkOption {
      type = types.port;
      default = 8080;
      description = "Port for foo.";
    };
  };
}
