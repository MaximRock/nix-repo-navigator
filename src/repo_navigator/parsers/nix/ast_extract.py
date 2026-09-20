"""AST extraction: walks a parsed Nix AST and produces structured metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repo_navigator.parsers.nix.parser import (
    Assert,
    AttrSet,
    BinaryOp,
    Expr,
    Formals,
    Function,
    FunctionCall,
    IfThenElse,
    Inherit,
    Interpolation,
    LetIn,
    List,
    Literal,
    Select,
    UnaryOp,
    With,
    ast_to_dict,
    parse,
)


@dataclass
class ImportDecl:
    path: str
    line: int = 0
    conditional: bool = False


@dataclass
class ModuleRef:
    """A ``nixosSystem { modules = [ … ] }`` entry that is not a path.

    Bare selects like ``sops-nix.nixosModules.sops`` reference a module
    exported by a flake input: *name* is the input name (first path
    component), *select* the remainder (``None`` for a bare ``name``).

    *partial* marks provenance from a static ``inputs.<name>`` prefix whose
    tail is dynamic (e.g. ``${system}`` in the middle): the input is certain,
    the selected attribute is not.
    """

    name: str
    select: str | None = None
    line: int = 0
    partial: bool = False


@dataclass
class OptionDecl:
    attrpath: str
    type: Any = None
    default: Any = None
    example: Any = None
    description: str | None = None
    line: int = 0


@dataclass
class ConfigSet:
    attrpath: str
    value_expr: Any = None
    conditional: bool = False
    priority: str | None = None
    line: int = 0


@dataclass
class Specialisation:
    name: str
    config: Any = None
    line: int = 0


@dataclass
class ModuleArg:
    name: str
    line: int = 0


@dataclass
class HomeFile:
    target: str
    source: Any = None
    line: int = 0


@dataclass
class PackageRef:
    attribute: str
    line: int = 0


@dataclass
class FunctionDecl:
    name: str
    args: list[str] = field(default_factory=list)
    line: int = 0


@dataclass
class UnresolvedRef:
    location: str
    reason: str


@dataclass
class ExtractedNix:
    imports: list[ImportDecl] = field(default_factory=list)
    modules: list[ModuleRef] = field(default_factory=list)
    options: list[OptionDecl] = field(default_factory=list)
    configs: list[ConfigSet] = field(default_factory=list)
    specialisations: list[Specialisation] = field(default_factory=list)
    module_args: list[ModuleArg] = field(default_factory=list)
    home_files: list[HomeFile] = field(default_factory=list)
    packages: list[PackageRef] = field(default_factory=list)
    functions: list[FunctionDecl] = field(default_factory=list)
    unresolved: list[UnresolvedRef] = field(default_factory=list)


def extract(expr: Expr) -> ExtractedNix:
    """Walk *expr* and return structured extraction."""
    result = ExtractedNix()
    let_env = _collect_let_env(expr)
    root = _unwrap_root(expr)
    if root is not None:
        _process(root.attrs, result, let_env=let_env)
    _collect_bare_imports(expr, result)
    return result


def extract_source(source: str) -> ExtractedNix:
    """Parse *source* and extract structured metadata."""
    return extract(parse(source))


# ------------------------------------------------------------------ helpers


def _unwrap_root(expr: Expr) -> AttrSet | None:
    if isinstance(expr, AttrSet):
        return expr
    if isinstance(expr, Function):
        return _unwrap_root(expr.body)
    if isinstance(expr, With):
        return _unwrap_root(expr.body)
    if expr.type == "LetIn" and hasattr(expr, "body"):
        return _unwrap_root(expr.body)
    return None


def _collect_let_env(expr: Expr) -> dict[str, Expr]:
    """Collect same-file ``let`` bindings along the module wrapper chain.

    Walks ``Function → With → LetIn`` wrappers (the same chain
    :func:`_unwrap_root` follows) and maps binding names to their value
    expressions.  Inner bindings shadow outer ones.  Used to resolve
    single-level references such as ``"${modulesHome}/…"`` where
    ``modulesHome = toString ../../modules/home``.
    """
    env: dict[str, Expr] = {}
    node: Expr | None = expr
    while node is not None:
        if isinstance(node, Function):
            node = node.body
        elif isinstance(node, With):
            node = node.body
        elif isinstance(node, LetIn):
            for binding in node.bindings:
                if isinstance(binding.name, str) and binding.value is not None:
                    env[binding.name] = binding.value
            node = node.body
        else:
            break
    return env


def _resolve_static_string(expr: Expr, env: dict[str, Expr]) -> str | None:
    """Resolve *expr* to a static string via a single let-env hop.

    Handles path/string literals, ``toString <path>`` calls, and bare
    single-name selects bound in *env* (unwrapping one ``toString`` level).
    Anything else — including chained var→var references — returns ``None``.
    """
    if (
        isinstance(expr, Select)
        and expr.base is None
        and len(expr.path) == 1
        and expr.path[0] in env
    ):
        expr = env[expr.path[0]]
    if isinstance(expr, Literal):
        if expr.value_type in ("path", "string") and isinstance(expr.value, str):
            return expr.value
        return None
    if isinstance(expr, FunctionCall):
        name, args = _unwrap_curried(expr)
        short = name.rsplit(".", 1)[-1] if name else name
        if short == "toString" and len(args) == 1:
            arg = args[0]
            if (
                isinstance(arg, Literal)
                and arg.value_type in ("path", "string")
                and isinstance(arg.value, str)
            ):
                return arg.value
        return None
    return None


def _render_interpolation(item: Interpolation, env: dict[str, Expr]) -> str | None:
    """Render an interpolation to a static string, or ``None`` if dynamic.

    Every part must be a string fragment or resolvable via
    :func:`_resolve_static_string`; a single unresolvable part (e.g.
    ``${system}``) keeps the whole interpolation dynamic.
    """
    out: list[str] = []
    for part in item.parts:
        if isinstance(part, str):
            out.append(part)
        elif isinstance(part, Expr):
            rendered = _resolve_static_string(part, env)
            if rendered is None:
                return None
            out.append(rendered)
        else:
            return None
    return "".join(out)


def _is_interpolation(expr: Expr) -> bool:
    if isinstance(expr, Interpolation):
        return True
    if isinstance(expr, AttrSet):
        return any(
            _is_interpolation(a.value) for a in expr.attrs if a.value is not None
        )
    if isinstance(expr, List):
        return any(_is_interpolation(i) for i in expr.items)
    return False


def _detect_call(expr: Expr, name: str) -> bool:
    if isinstance(expr, FunctionCall):
        if isinstance(expr.func, Select):
            if expr.func.path and expr.func.path[-1] == name:
                return True
    return False


def _unwrap_call_args(expr: Expr) -> tuple[str, list[Expr]]:
    if isinstance(expr, FunctionCall) and isinstance(expr.func, Select):
        inner_name, inner_args = _unwrap_call_args(expr.func)
        return inner_name, inner_args + [expr.arg]
    if isinstance(expr, Select) and isinstance(expr.base, None):
        return (".".join(expr.path), []) if expr.path else ("", [])
    return ("", [])


def _unwrap_curried(expr: Expr) -> tuple[str, list[Expr]]:
    """Unwrap nested FunctionCall chain to get (name, all_args).

    Handles ``mkIf cfg.enable { ... }`` which parses as nested calls:
    ``((mkIf cfg) enable) body`` → name="mkIf", args=[cfg, enable, body].
    """
    if isinstance(expr, FunctionCall):
        name, args = _unwrap_curried(expr.func)
        return name, args + [expr.arg]
    if isinstance(expr, Select) and expr.base is None:
        return (".".join(expr.path), []) if expr.path else ("", [])
    if isinstance(expr, Select) and expr.base is not None:
        # Select(base=FunctionCall(...), path=['enable'])
        # The path is attached to the result of the inner call.
        name, args = _unwrap_curried(expr.base)
        return name, args
    return ("", [])


def _detect_mkif_blocks(expr: Expr) -> list[Expr] | None:
    """Return the conditional body(s) if expr wraps mkIf/mkMerge."""
    if isinstance(expr, FunctionCall):
        name, args = _unwrap_curried(expr)
        parts = name.rsplit(".", 1)
        short = parts[-1] if parts else name
        if short == "mkIf" and len(args) >= 2:
            return [args[1]]
        if short == "mkMerge" and args:
            arg = args[0]
            if isinstance(arg, List):
                return [item for item in arg.items if isinstance(item, AttrSet)]
    return None


def _is_select_str(expr: Expr) -> str | None:
    if isinstance(expr, Select) and expr.base is None and expr.path:
        return ".".join(expr.path)
    return None


def _literal_value(expr: Expr) -> Any:
    if isinstance(expr, Literal):
        return expr.value
    return ast_to_dict(expr)


def _detect_priority(expr: Expr) -> tuple[str | None, Expr]:
    """Detect mkForce/mkDefault; return (priority, inner_expr)."""
    if isinstance(expr, FunctionCall):
        name, args = _unwrap_curried(expr)
        parts = name.rsplit(".", 1)
        short = parts[-1] if parts else name
        if short == "mkForce" and args:
            return ("force", args[0])
        if short == "mkDefault" and args:
            return ("default", args[0])
    return (None, expr)


def _formal_names(arg: Any) -> list[str]:
    if isinstance(arg, str):
        return [arg]
    if isinstance(arg, Formals):
        return [f.name for f in arg.fields]
    return []


def _extract_option_value(arg: Expr) -> dict[str, Any]:
    """Extract type/default/example/description from an mkOption arg AttrSet."""
    meta: dict[str, Any] = {}
    if not isinstance(arg, AttrSet):
        return meta
    for attr in arg.attrs:
        if isinstance(attr.name, str) and attr.value is not None:
            meta[attr.name] = _literal_value(attr.value)
    return meta


def _parse_option_meta(arg: Expr) -> dict[str, Any]:
    """Parse mkOption arg to structured OptionDecl fields."""
    meta = _extract_option_value(arg)
    result: dict[str, Any] = {}
    for key in ("type", "default", "example", "description"):
        if key in meta:
            result[key] = meta[key]
    return result


# ----------------------------------------------------------------- walker


def _process(
    attrs: list,
    result: ExtractedNix,
    conditional: bool = False,
    let_env: dict[str, Expr] | None = None,
    _prefix: tuple[str, ...] = (),
) -> None:
    for attr in attrs:
        if isinstance(attr.name, Inherit):
            continue
        if not isinstance(attr.name, str):
            continue
        name = attr.name
        value = attr.value

        if value is None:
            continue

        if name == "imports":
            _process_imports(value, result, conditional, let_env)
            continue

        if name == "options":
            _process_options(value, result, conditional)
            continue

        if name == "config":
            _process_config(value, result, conditional, let_env)
            continue

        if name == "specialisation":
            _process_specialisation(value, result)
            continue

        if name == "_module":
            _process_module_args(value, result)
            continue

        if name == "home":
            _process_home(value, result, conditional, let_env, _prefix)
            continue

        if name == "xdg":
            _process_xdg(value, result, conditional, _prefix)
            continue

        if name == "programs":
            _process_programs(value, result, conditional, let_env, _prefix)
            continue

        _emit_generic_attr(name, value, result, conditional, _prefix, let_env)


def _emit_generic_attr(
    name: str,
    value: Expr,
    result: ExtractedNix,
    conditional: bool,
    prefix: tuple[str, ...] = (),
    let_env: dict[str, Expr] | None = None,
) -> None:
    """Record a plain assignment under an unhandled attrpath as an option set.

    Covers dotted assignments outside ``config`` blocks (BUG-004 D3), e.g.
    ``modules.home.comfyui.enable = false`` in a bare
    ``modules.home = { … }`` attrset, as well as unknown leaves inside the
    ``home``/``programs``/``xdg`` handlers (which only extract their known
    fields and would otherwise drop e.g. ``home.stateVersion`` or
    ``programs.git.userName``). Handled names never reach this helper —
    ``_process`` dispatches them first.
    """
    mkif_blocks = _detect_mkif_blocks(value)
    if mkif_blocks:
        for block in mkif_blocks:
            if isinstance(block, AttrSet):
                _process(
                    block.attrs,
                    result,
                    conditional=True,
                    let_env=let_env,
                    _prefix=prefix + (name,),
                )
        # Mirror the config walker: record the mkIf-wrapped assignment
        # itself as a conditional set.
        priority, inner = _detect_priority(value)
        result.configs.append(
            ConfigSet(
                attrpath=".".join([*prefix, name]),
                value_expr=ast_to_dict(inner),
                conditional=True,
                priority=priority,
                line=getattr(value, "line", 0) or 0,
            )
        )
        return

    if isinstance(value, AttrSet):
        _process(value.attrs, result, conditional, let_env, prefix + (name,))
        return

    if isinstance(value, Function):
        result.functions.append(
            FunctionDecl(
                name=name,
                args=_formal_names(value.arg),
            )
        )
        return

    priority, inner = _detect_priority(value)
    result.configs.append(
        ConfigSet(
            attrpath=".".join([*prefix, name]),
            value_expr=ast_to_dict(inner),
            conditional=conditional,
            priority=priority,
            line=getattr(value, "line", 0) or 0,
        )
    )


def _collect_bare_imports(expr: Expr, result: ExtractedNix) -> None:
    """Recursively find bare ``import ./x`` calls and record ``ImportDecl``.

    ``import`` is a builtin function in Nix; a bare call ``import <path>``
    (in a ``let`` binding, an attribute value, an ``inherit (import …)``
    source, or nested as a curried call like ``import ./x { … }``) makes the
    module depend on *that path* even though it is not listed in an
    ``imports = [ … ]`` attribute.  The ``imports`` attribute itself is
    handled by :func:`_process_imports` and skipped here so dynamic
    imports stay ``unresolved`` (matches the historical behaviour).
    """
    if isinstance(expr, FunctionCall):
        name, args = _unwrap_curried(expr)
        parts = name.rsplit(".", 1)
        short = parts[-1] if parts else name
        if short == "import" and args and isinstance(args[0], Literal):
            path_val = args[0].value
            if isinstance(path_val, str) and (
                path_val.startswith("./")
                or path_val.startswith("../")
                or path_val.startswith("/")
            ):
                result.imports.append(
                    ImportDecl(path=path_val, line=getattr(expr, "line", 0))
                )
                return
        _collect_bare_imports(expr.func, result)
        _collect_bare_imports(expr.arg, result)
    elif isinstance(expr, AttrSet):
        for attr in expr.attrs:
            if isinstance(attr.name, Inherit):
                if attr.name.from_ is not None:
                    _collect_bare_imports(attr.name.from_, result)
                continue
            if isinstance(attr.name, str) and attr.name == "imports":
                # Handled by _process_imports; dynamic imports stay unresolved.
                continue
            if (
                isinstance(attr.name, str)
                and attr.name == "modules"
                and isinstance(attr.value, List)
            ):
                # `nixosSystem { modules = [ … ] }`: treat path literals like
                # imports; map bare selects to ModuleRef. Still recurse
                # generically so nested `import` calls are found.
                _collect_modules_list(attr.value, result)
                _collect_bare_imports(attr.value, result)
                continue
            if attr.value is not None:
                _collect_bare_imports(attr.value, result)
    elif isinstance(expr, List):
        for item in expr.items:
            _collect_bare_imports(item, result)
    elif isinstance(expr, LetIn):
        for binding in expr.bindings:
            if isinstance(binding.name, Inherit) and binding.name.from_ is not None:
                _collect_bare_imports(binding.name.from_, result)
            if binding.value is not None:
                _collect_bare_imports(binding.value, result)
        _collect_bare_imports(expr.body, result)
    elif isinstance(expr, Function):
        _collect_bare_imports(expr.body, result)
    elif isinstance(expr, With):
        _collect_bare_imports(expr.expr, result)
        _collect_bare_imports(expr.body, result)
    elif isinstance(expr, Assert):
        _collect_bare_imports(expr.assertion, result)
        _collect_bare_imports(expr.body, result)
    elif isinstance(expr, IfThenElse):
        _collect_bare_imports(expr.cond, result)
        _collect_bare_imports(expr.then_, result)
        _collect_bare_imports(expr.else_, result)
    elif isinstance(expr, Select) and expr.base is not None:
        _collect_bare_imports(expr.base, result)
    elif isinstance(expr, BinaryOp):
        _collect_bare_imports(expr.left, result)
        _collect_bare_imports(expr.right, result)
    elif isinstance(expr, UnaryOp):
        _collect_bare_imports(expr.expr, result)
    elif isinstance(expr, Interpolation):
        for part in expr.parts:
            if isinstance(part, Expr):
                _collect_bare_imports(part, result)


# ----------------------------------------------------------- processing


def _collect_modules_list(value: List, result: ExtractedNix) -> None:
    """Collect ``modules = [ … ]`` entries (e.g. ``nixosSystem { modules }``).

    Path literals become :class:`ImportDecl` (same as in ``imports``); bare
    selects like ``sops-nix.nixosModules.sops`` become :class:`ModuleRef`
    (first component names the flake input); dynamic entries become
    :class:`UnresolvedRef`.  Nested ``import`` calls are left to the generic
    walker in :func:`_collect_bare_imports`.
    """
    for item in value.items:
        if isinstance(item, Literal) and item.value_type == "path":
            result.imports.append(
                ImportDecl(path=str(item.value), line=getattr(item, "line", 0))
            )
        elif isinstance(item, Select) and item.base is None and item.path:
            if _is_interpolation(item):
                result.unresolved.append(
                    UnresolvedRef(
                        location=str(_is_select_str(item) or item),
                        reason="unresolved interpolation in module",
                    )
                )
            else:
                result.modules.append(
                    ModuleRef(
                        name=item.path[0],
                        select=".".join(item.path[1:]) or None,
                        line=getattr(item, "line", 0),
                    )
                )
        elif isinstance(item, (Interpolation, BinaryOp, UnaryOp)):
            result.unresolved.append(
                UnresolvedRef(
                    location=str(item),
                    reason="dynamic module entry",
                )
            )
        elif _is_interpolation(item):
            result.unresolved.append(
                UnresolvedRef(
                    location=str(_is_select_str(item) or item),
                    reason="unresolved interpolation in module",
                )
            )


def _process_imports(
    value: Expr,
    result: ExtractedNix,
    conditional: bool,
    let_env: dict[str, Expr] | None = None,
) -> None:
    env = let_env or {}
    if not isinstance(value, List):
        if isinstance(value, Literal):
            result.imports.append(
                ImportDecl(path=str(value.value), conditional=conditional)
            )
        elif isinstance(value, LetIn):
            # `imports = let … in [ … ]`: merge local bindings, then recurse.
            inner_env = dict(env)
            for binding in value.bindings:
                if isinstance(binding.name, str) and binding.value is not None:
                    inner_env[binding.name] = binding.value
            _process_imports(value.body, result, conditional, inner_env)
        return
    for item in value.items:
        if isinstance(item, Literal) and item.value_type == "path":
            result.imports.append(
                ImportDecl(path=str(item.value), conditional=conditional)
            )
        elif isinstance(item, FunctionCall):
            name, args = _unwrap_curried(item)
            if name == "import" and args:
                result.unresolved.append(
                    UnresolvedRef(
                        location=f"(import ...)",
                        reason="dynamic import",
                    )
                )
            elif _is_interpolation(item):
                result.unresolved.append(
                    UnresolvedRef(
                        location=str(name),
                        reason="unresolved interpolation in import",
                    )
                )
        elif isinstance(item, Select) and _is_interpolation(item):
            result.unresolved.append(
                UnresolvedRef(
                    location=str(_is_select_str(item) or item),
                    reason="unresolved interpolation in import",
                )
            )
        elif isinstance(item, Interpolation):
            rendered = _render_interpolation(item, env)
            if rendered is not None and (
                rendered.startswith("./")
                or rendered.startswith("../")
                or rendered.startswith("/")
            ):
                result.imports.append(
                    ImportDecl(path=rendered, conditional=conditional)
                )
            else:
                result.unresolved.append(
                    UnresolvedRef(
                        location=str(item),
                        reason="interpolation in import",
                    )
                )


def _process_options(value: Expr, result: ExtractedNix, conditional: bool) -> None:
    if not isinstance(value, AttrSet):
        return

    for opt_attr in value.attrs:
        if isinstance(opt_attr.name, Inherit) or not isinstance(opt_attr.name, str):
            continue
        attr_value = opt_attr.value
        if attr_value is None:
            continue

        mkif_blocks = _detect_mkif_blocks(attr_value)
        if mkif_blocks:
            for block in mkif_blocks:
                if isinstance(block, AttrSet):
                    _walk_options_recursive(
                        block.attrs, [opt_attr.name], result, conditional=True
                    )
            continue

        if isinstance(attr_value, AttrSet):
            _walk_options_recursive(
                attr_value.attrs, [opt_attr.name], result, conditional=conditional
            )
        elif isinstance(attr_value, FunctionCall):
            func_name, func_args = _unwrap_curried(attr_value)
            parts = func_name.rsplit(".", 1)
            short = parts[-1] if parts else func_name
            if short == "mkOption" and func_args:
                opt_path = opt_attr.name
                meta = _parse_option_meta(func_args[0])
                result.options.append(
                    OptionDecl(
                        attrpath=opt_path,
                        type=meta.get("type"),
                        default=meta.get("default"),
                        example=meta.get("example"),
                        description=meta.get("description"),
                    )
                )
            elif short == "mkEnableOption" and func_args:
                desc = (
                    str(func_args[0].value) if hasattr(func_args[0], "value") else None
                )
                result.options.append(
                    OptionDecl(
                        attrpath=opt_attr.name,
                        type="types.bool",
                        default="false",
                        description=desc,
                    )
                )


def _walk_options_recursive(
    attrs: list,
    prefix: list[str],
    result: ExtractedNix,
    conditional: bool,
) -> None:
    for attr in attrs:
        if isinstance(attr.name, Inherit) or not isinstance(attr.name, str):
            continue
        name = attr.name
        value = attr.value
        if value is None:
            continue

        mkif_blocks = _detect_mkif_blocks(value)
        if mkif_blocks:
            for block in mkif_blocks:
                if isinstance(block, AttrSet):
                    _walk_options_recursive(
                        block.attrs, prefix + [name], result, conditional=True
                    )
            continue

        if isinstance(value, FunctionCall):
            func_name, func_args = _unwrap_curried(value)
            parts = func_name.rsplit(".", 1)
            short = parts[-1] if parts else func_name
            if short == "mkOption" and func_args:
                opt_path = ".".join(prefix + [name])
                meta = _parse_option_meta(func_args[0])
                result.options.append(
                    OptionDecl(
                        attrpath=opt_path,
                        type=meta.get("type"),
                        default=meta.get("default"),
                        example=meta.get("example"),
                        description=meta.get("description"),
                    )
                )
                continue
            elif short == "mkEnableOption" and func_args:
                desc = (
                    str(func_args[0].value) if hasattr(func_args[0], "value") else None
                )
                result.options.append(
                    OptionDecl(
                        attrpath=".".join(prefix + [name]),
                        type="types.bool",
                        default="false",
                        description=desc,
                    )
                )
                continue

        if isinstance(value, AttrSet):
            _walk_options_recursive(value.attrs, prefix + [name], result, conditional)


def _process_config(
    value: Expr,
    result: ExtractedNix,
    conditional: bool,
    let_env: dict[str, Expr] | None = None,
) -> None:
    if isinstance(value, AttrSet):
        _walk_config_recursive(value.attrs, [], result, conditional)
        return

    # Handle mkIf / mkMerge wrapping
    mkif_blocks = _detect_mkif_blocks(value)
    if mkif_blocks:
        for block in mkif_blocks:
            if isinstance(block, AttrSet):
                # Walk for configs (produces config entries for all attrs)
                _walk_config_recursive(block.attrs, [], result, conditional=True)
                # Also scan for imports, options, etc. inside the block
                _scan_special_attrs(
                    block.attrs, result, conditional=True, let_env=let_env
                )
        return

    priority, inner = _detect_priority(value)
    result.configs.append(
        ConfigSet(
            attrpath="",
            value_expr=ast_to_dict(inner),
            conditional=conditional,
            priority=priority,
        )
    )


def _scan_special_attrs(
    attrs: list,
    result: ExtractedNix,
    conditional: bool,
    let_env: dict[str, Expr] | None = None,
) -> None:
    """Scan *attrs* for imports, options, specialisations, etc.

    Used inside mkIf/mkMerge blocks where ``_walk_config_recursive``
    handles configs but misses the structured extraction that ``_process``
    does for known prefixes.
    """
    for attr in attrs:
        if isinstance(attr.name, Inherit) or not isinstance(attr.name, str):
            continue
        name = attr.name
        value = attr.value
        if value is None:
            continue

        if name == "imports":
            _process_imports(value, result, conditional, let_env)
        elif name == "options":
            _process_options(value, result, conditional)
        elif name == "specialisation":
            _process_specialisation(value, result)
        elif name == "_module":
            _process_module_args(value, result)
        elif name == "home":
            _process_home(value, result, conditional, let_env)
        elif name == "xdg":
            _process_xdg(value, result, conditional)
        elif name == "programs":
            _process_programs(value, result, conditional, let_env)


def _walk_config_recursive(
    attrs: list,
    prefix: list[str],
    result: ExtractedNix,
    conditional: bool,
) -> None:
    for attr in attrs:
        if isinstance(attr.name, Inherit) or not isinstance(attr.name, str):
            continue
        name = attr.name
        value = attr.value
        if value is None:
            continue

        mkif_blocks = _detect_mkif_blocks(value)
        if mkif_blocks:
            for block in mkif_blocks:
                if isinstance(block, AttrSet):
                    _walk_config_recursive(
                        block.attrs, prefix + [name], result, conditional=True
                    )
            # Also emit a config entry for the mkIf-wrapped value itself
            priority, inner = _detect_priority(value)
            result.configs.append(
                ConfigSet(
                    attrpath=".".join(prefix + [name]),
                    value_expr=ast_to_dict(inner),
                    conditional=True,
                    priority=priority,
                )
            )
            continue

        if isinstance(value, AttrSet):
            _walk_config_recursive(value.attrs, prefix + [name], result, conditional)
            continue

        priority, inner = _detect_priority(value)
        result.configs.append(
            ConfigSet(
                attrpath=".".join(prefix + [name]),
                value_expr=ast_to_dict(inner),
                conditional=conditional,
                priority=priority,
            )
        )


def _process_specialisation(value: Expr, result: ExtractedNix) -> None:
    if not isinstance(value, AttrSet):
        return
    for attr in value.attrs:
        if isinstance(attr.name, Inherit) or not isinstance(attr.name, str):
            continue
        if attr.value is not None:
            result.specialisations.append(
                Specialisation(name=attr.name, config=ast_to_dict(attr.value))
            )


def _process_module_args(value: Expr, result: ExtractedNix) -> None:
    if not isinstance(value, AttrSet):
        return
    for attr in value.attrs:
        if isinstance(attr.name, Inherit) or not isinstance(attr.name, str):
            continue
        if attr.name == "args" and isinstance(attr.value, AttrSet):
            for arg_attr in attr.value.attrs:
                if isinstance(arg_attr.name, str):
                    result.module_args.append(ModuleArg(name=arg_attr.name))
        elif attr.name != "args":
            result.module_args.append(ModuleArg(name=attr.name))


def _process_home(
    value: Expr,
    result: ExtractedNix,
    conditional: bool,
    let_env: dict[str, Expr] | None = None,
    _prefix: tuple[str, ...] = (),
) -> None:
    if not isinstance(value, AttrSet):
        # e.g. `home = mkIf cond { … }` — record generically.
        _emit_generic_attr("home", value, result, conditional, _prefix, let_env)
        return
    base = (*_prefix, "home")
    for attr in value.attrs:
        if isinstance(attr.name, Inherit) or not isinstance(attr.name, str):
            continue
        if attr.name == "file" and isinstance(attr.value, AttrSet):
            _process_home_file(attr.value, result)
        elif attr.name == "packages" and isinstance(attr.value, List):
            _process_home_packages(attr.value, result, let_env)
        elif attr.name == "sessionVariables" and isinstance(attr.value, AttrSet):
            for var_attr in attr.value.attrs:
                if isinstance(var_attr.name, str):
                    result.configs.append(
                        ConfigSet(
                            attrpath=".".join(
                                [*base, "sessionVariables", var_attr.name]
                            ),
                            value_expr=ast_to_dict(var_attr.value)
                            if var_attr.value
                            else None,
                            conditional=conditional,
                        )
                    )
        elif attr.name == "activation" and isinstance(attr.value, AttrSet):
            for act_attr in attr.value.attrs:
                if isinstance(act_attr.name, str):
                    result.configs.append(
                        ConfigSet(
                            attrpath=".".join([*base, "activation", act_attr.name]),
                            value_expr=ast_to_dict(act_attr.value)
                            if act_attr.value
                            else None,
                            conditional=conditional,
                        )
                    )
        else:
            # BUG-004 D3: unknown home.* leaves (home.stateVersion,
            # modules.home.comfyui.*, …) are option sets too.
            if attr.value is not None:
                _emit_generic_attr(
                    attr.name, attr.value, result, conditional, base, let_env
                )


def _process_home_file(attrs: AttrSet, result: ExtractedNix) -> None:
    for target_attr in attrs.attrs:
        if isinstance(target_attr.name, Inherit) or not isinstance(
            target_attr.name, str
        ):
            continue
        if isinstance(target_attr.value, AttrSet):
            for src_attr in target_attr.value.attrs:
                if isinstance(src_attr.name, str) and src_attr.name == "source":
                    result.home_files.append(
                        HomeFile(
                            target=target_attr.name,
                            source=_literal_value(src_attr.value)
                            if src_attr.value
                            else None,
                        )
                    )


def _inputs_ref_of_select(sel: Select) -> ModuleRef | None:
    """Map an ``inputs.<name>.…`` select to a flake-input reference.

    Returns ``None`` for anything else.  Reuses :class:`ModuleRef` so the
    existing ``references → flake_input:<name>`` edge machinery applies.
    """
    if sel.base is None and sel.path and sel.path[0] == "inputs" and len(sel.path) > 1:
        line = getattr(sel, "line", 0)
        # Dynamic segments arrive as `[{...json...}]` blobs (see
        # `_read_attr_segment`); collapse them to `…` like `_parse_attrpath`.
        rest = ["…" if seg.startswith("[") else seg for seg in sel.path[2:]]
        return ModuleRef(
            name=sel.path[1],
            select=".".join(rest) or None,
            line=line if isinstance(line, int) else 0,
            partial="…" in rest,
        )
    return None


def _collect_inputs_refs(
    expr: Expr,
    env: dict[str, Expr],
    depth: int = 3,
    _seen: tuple[str, ...] = (),
) -> list[ModuleRef]:
    """Collect ``inputs.<name>`` references under *expr*, transitively.

    Walks the value subtree (interpolations, call args, list items, attr
    values, …) and resolves bare single-name selects through *env* up to
    *depth* var-hops.  This catches the real-world wrapper pattern where the
    package var is built by ``writeShellScriptBin`` from a sibling binding
    holding the ``inputs.…`` chain::

        comfyui = inputs.comfyui-nix.packages.${system}.rocm;
        comfy-ui = pkgs.writeShellScriptBin "comfy-ui" ''exec ${comfyui}/…''

    *depth* bounds var-hops and *_seen* guards cyclic bindings
    (``a = b; b = a``).  Results are de-duplicated by ``(name, select)``.
    """
    found: dict[tuple[str, str | None], ModuleRef] = {}

    def _emit(ref: ModuleRef) -> None:
        key = (ref.name, ref.select)
        if key not in found:
            found[key] = ref

    def _walk(
        node: Expr, local_env: dict[str, Expr], budget: int, seen: tuple[str, ...]
    ) -> None:
        if isinstance(node, Select):
            if node.base is not None:
                _walk(node.base, local_env, budget, seen)
                return
            if not node.path:
                return
            if node.path[0] == "inputs":
                ref = _inputs_ref_of_select(node)
                if ref is not None:
                    _emit(ref)
                return
            if (
                len(node.path) == 1
                and budget > 0
                and node.path[0] in local_env
                and node.path[0] not in seen
            ):
                _walk(
                    local_env[node.path[0]],
                    local_env,
                    budget - 1,
                    seen + (node.path[0],),
                )
            return
        if isinstance(node, Interpolation):
            for part in node.parts:
                if isinstance(part, Expr):
                    _walk(part, local_env, budget, seen)
        elif isinstance(node, FunctionCall):
            _walk(node.func, local_env, budget, seen)
            _walk(node.arg, local_env, budget, seen)
        elif isinstance(node, List):
            for item in node.items:
                _walk(item, local_env, budget, seen)
        elif isinstance(node, AttrSet):
            for attr in node.attrs:
                if attr.value is not None:
                    _walk(attr.value, local_env, budget, seen)
        elif isinstance(node, LetIn):
            nested = dict(local_env)
            for binding in node.bindings:
                if isinstance(binding.name, str) and binding.value is not None:
                    nested[binding.name] = binding.value
                    _walk(binding.value, nested, budget, seen)
            _walk(node.body, nested, budget, seen)
        elif isinstance(node, Function):
            _walk(node.body, local_env, budget, seen)
        elif isinstance(node, With):
            _walk(node.expr, local_env, budget, seen)
            _walk(node.body, local_env, budget, seen)
        elif isinstance(node, Assert):
            _walk(node.assertion, local_env, budget, seen)
            _walk(node.body, local_env, budget, seen)
        elif isinstance(node, IfThenElse):
            _walk(node.cond, local_env, budget, seen)
            _walk(node.then_, local_env, budget, seen)
            _walk(node.else_, local_env, budget, seen)
        elif isinstance(node, BinaryOp):
            _walk(node.left, local_env, budget, seen)
            _walk(node.right, local_env, budget, seen)
        elif isinstance(node, UnaryOp):
            _walk(node.expr, local_env, budget, seen)

    _walk(expr, env, depth, _seen)
    return list(found.values())


def _process_home_packages(
    value: Expr, result: ExtractedNix, let_env: dict[str, Expr] | None = None
) -> None:
    env = let_env or {}
    if not isinstance(value, List):
        return
    for item in value.items:
        if isinstance(item, Select) and item.base is None and item.path:
            result.packages.append(PackageRef(attribute=".".join(item.path)))
            result.modules.extend(_collect_inputs_refs(item, env))
        elif isinstance(item, Literal):
            result.packages.append(PackageRef(attribute=str(item.value)))


def _process_xdg(
    value: Expr,
    result: ExtractedNix,
    conditional: bool,
    _prefix: tuple[str, ...] = (),
) -> None:
    if not isinstance(value, AttrSet):
        _emit_generic_attr("xdg", value, result, conditional, _prefix)
        return
    base = (*_prefix, "xdg")
    for attr in value.attrs:
        if isinstance(attr.name, Inherit) or not isinstance(attr.name, str):
            continue
        if attr.name in ("configFile", "dataFile") and isinstance(attr.value, AttrSet):
            _process_home_file(attr.value, result)
        elif attr.value is not None:
            # BUG-004 D3: unknown xdg.* leaves are option sets too.
            _emit_generic_attr(attr.name, attr.value, result, conditional, base)


def _process_programs(
    value: Expr,
    result: ExtractedNix,
    conditional: bool,
    let_env: dict[str, Expr] | None = None,
    _prefix: tuple[str, ...] = (),
) -> None:
    if not isinstance(value, AttrSet):
        _emit_generic_attr("programs", value, result, conditional, _prefix, let_env)
        return
    env = let_env or {}
    base = (*_prefix, "programs")
    for prog_attr in value.attrs:
        if isinstance(prog_attr.name, Inherit) or not isinstance(prog_attr.name, str):
            continue
        prog_base = (*base, prog_attr.name)
        if not isinstance(prog_attr.value, AttrSet):
            # Programs may be enabled via boolean: programs.git.enable = true;
            # In that case prog_attr.value is Literal (bool) - treat as enable
            if isinstance(prog_attr.value, Literal):
                result.configs.append(
                    ConfigSet(
                        attrpath=".".join([*prog_base, "enable"]),
                        value_expr=ast_to_dict(prog_attr.value),
                        conditional=conditional,
                    )
                )
            elif prog_attr.value is not None:
                # e.g. `programs.git = mkIf cond { … }` — record generically.
                _emit_generic_attr(
                    prog_attr.name,
                    prog_attr.value,
                    result,
                    conditional,
                    base,
                    let_env,
                )
            continue
        for field_attr in prog_attr.value.attrs:
            if isinstance(field_attr.name, str) and field_attr.name == "package":
                attr_path = f"{prog_attr.name}.package"
                if (
                    isinstance(field_attr.value, Select)
                    and field_attr.value.base is None
                ):
                    result.packages.append(
                        PackageRef(
                            attribute=".".join(field_attr.value.path)
                            if field_attr.value.path
                            else attr_path
                        )
                    )
                    refs = _collect_inputs_refs(field_attr.value, env)
                    result.modules.extend(refs)
                elif isinstance(field_attr.value, Literal):
                    result.packages.append(
                        PackageRef(attribute=str(field_attr.value.value))
                    )
            elif isinstance(field_attr.name, str) and field_attr.name in (
                "enable",
                "enableCompletion",
            ):
                result.configs.append(
                    ConfigSet(
                        attrpath=".".join([*prog_base, field_attr.name]),
                        value_expr=ast_to_dict(field_attr.value)
                        if field_attr.value
                        else None,
                        conditional=conditional,
                    )
                )
            elif isinstance(field_attr.name, str) and field_attr.name == "extraConfig":
                result.home_files.append(
                    HomeFile(
                        target=".".join(prog_base),
                        source=_literal_value(field_attr.value)
                        if field_attr.value
                        else None,
                    )
                )
            elif isinstance(field_attr.name, str) and field_attr.value is not None:
                # BUG-004 D3: other programs.* settings (userName,
                # settings.*, …) are option sets too.
                _emit_generic_attr(
                    field_attr.name,
                    field_attr.value,
                    result,
                    conditional,
                    prog_base,
                    let_env,
                )
