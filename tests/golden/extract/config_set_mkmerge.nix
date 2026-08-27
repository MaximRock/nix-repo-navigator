{
  config = mkMerge [
    { services.foo.enable = true; }
    { services.bar.enable = false; }
  ];
}
