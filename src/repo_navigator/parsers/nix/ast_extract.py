"""AST extraction: walks a parsed Nix AST and produces structured metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repo_navigator.parsers.nix.parser import (
    AttrSet,
    BinaryOp,
    Expr,
    Formals,
    Function,
    FunctionCall,
    Inherit,
    Interpolation,
    List,
    Literal,
    Select,
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
    root = _unwrap_root(expr)
    if root is not None:
        _process(root.attrs, result)
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


def _is_interpolation(expr: Expr) -> bool:
    if isinstance(expr, Interpolation):
        return True
    if isinstance(expr, AttrSet):
        return any(_is_interpolation(a.value) for a in expr.attrs if a.value is not None)
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
                return [
                    item
                    for item in arg.items
                    if isinstance(item, AttrSet)
                ]
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


def _process(attrs: list, result: ExtractedNix, conditional: bool = False) -> None:
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
            _process_imports(value, result, conditional)
            continue

        if name == "options":
            _process_options(value, result, conditional)
            continue

        if name == "config":
            _process_config(value, result, conditional)
            continue

        if name == "specialisation":
            _process_specialisation(value, result)
            continue

        if name == "_module":
            _process_module_args(value, result)
            continue

        if name == "home":
            _process_home(value, result, conditional)
            continue

        if name == "xdg":
            _process_xdg(value, result, conditional)
            continue

        if name == "programs":
            _process_programs(value, result, conditional)
            continue

        mkif_blocks = _detect_mkif_blocks(value)
        if mkif_blocks:
            for block in mkif_blocks:
                if isinstance(block, AttrSet):
                    _process(block.attrs, result, conditional=True)
            continue

        if isinstance(value, AttrSet):
            _process(value.attrs, result, conditional)
            continue

        if isinstance(value, Function):
            result.functions.append(
                FunctionDecl(
                    name=name,
                    args=_formal_names(value.arg),
                )
            )


# ----------------------------------------------------------- processing


def _process_imports(
    value: Expr, result: ExtractedNix, conditional: bool
) -> None:
    if not isinstance(value, List):
        if isinstance(value, Literal):
            result.imports.append(
                ImportDecl(path=str(value.value), conditional=conditional)
            )
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
            result.unresolved.append(
                UnresolvedRef(
                    location=str(item),
                    reason="interpolation in import",
                )
            )


def _process_options(
    value: Expr, result: ExtractedNix, conditional: bool
) -> None:
    if not isinstance(value, AttrSet):
        return

    for opt_attr in value.attrs:
        if isinstance(opt_attr.name, Inherit) or not isinstance(
            opt_attr.name, str
        ):
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
                desc = str(func_args[0].value) if hasattr(func_args[0], "value") else None
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
                desc = str(func_args[0].value) if hasattr(func_args[0], "value") else None
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
            _walk_options_recursive(
                value.attrs, prefix + [name], result, conditional
            )


def _process_config(
    value: Expr, result: ExtractedNix, conditional: bool
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
                _scan_special_attrs(block.attrs, result, conditional=True)
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
    attrs: list, result: ExtractedNix, conditional: bool
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
            _process_imports(value, result, conditional)
        elif name == "options":
            _process_options(value, result, conditional)
        elif name == "specialisation":
            _process_specialisation(value, result)
        elif name == "_module":
            _process_module_args(value, result)
        elif name == "home":
            _process_home(value, result, conditional)
        elif name == "xdg":
            _process_xdg(value, result, conditional)
        elif name == "programs":
            _process_programs(value, result, conditional)


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
            _walk_config_recursive(
                value.attrs, prefix + [name], result, conditional
            )
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
    value: Expr, result: ExtractedNix, conditional: bool
) -> None:
    if not isinstance(value, AttrSet):
        return
    for attr in value.attrs:
        if isinstance(attr.name, Inherit) or not isinstance(attr.name, str):
            continue
        if attr.name == "file" and isinstance(attr.value, AttrSet):
            _process_home_file(attr.value, result)
        elif attr.name == "packages":
            _process_home_packages(attr.value, result)
        elif attr.name == "sessionVariables" and isinstance(attr.value, AttrSet):
            for var_attr in attr.value.attrs:
                if isinstance(var_attr.name, str):
                    result.configs.append(
                        ConfigSet(
                            attrpath=f"home.sessionVariables.{var_attr.name}",
                            value_expr=ast_to_dict(var_attr.value) if var_attr.value else None,
                            conditional=conditional,
                        )
                    )
        elif attr.name == "activation" and isinstance(attr.value, AttrSet):
            for act_attr in attr.value.attrs:
                if isinstance(act_attr.name, str):
                    result.configs.append(
                        ConfigSet(
                            attrpath=f"home.activation.{act_attr.name}",
                            value_expr=ast_to_dict(act_attr.value) if act_attr.value else None,
                            conditional=conditional,
                        )
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


def _process_home_packages(value: Expr, result: ExtractedNix) -> None:
    if not isinstance(value, List):
        return
    for item in value.items:
        if isinstance(item, Select) and item.base is None and item.path:
            result.packages.append(
                PackageRef(attribute=".".join(item.path))
            )
        elif isinstance(item, Literal):
            result.packages.append(PackageRef(attribute=str(item.value)))


def _process_xdg(
    value: Expr, result: ExtractedNix, conditional: bool
) -> None:
    if not isinstance(value, AttrSet):
        return
    for attr in value.attrs:
        if isinstance(attr.name, Inherit) or not isinstance(attr.name, str):
            continue
        if attr.name in ("configFile", "dataFile") and isinstance(attr.value, AttrSet):
            _process_home_file(attr.value, result)


def _process_programs(
    value: Expr, result: ExtractedNix, conditional: bool
) -> None:
    if not isinstance(value, AttrSet):
        return
    for prog_attr in value.attrs:
        if isinstance(prog_attr.name, Inherit) or not isinstance(
            prog_attr.name, str
        ):
            continue
        if not isinstance(prog_attr.value, AttrSet):
            # Programs may be enabled via boolean: programs.git.enable = true;
            # In that case prog_attr.value is Literal (bool) - treat as enable
            if isinstance(prog_attr.value, Literal):
                result.configs.append(
                    ConfigSet(
                        attrpath=f"programs.{prog_attr.name}.enable",
                        value_expr=ast_to_dict(prog_attr.value),
                        conditional=conditional,
                    )
                )
            continue
        for field_attr in prog_attr.value.attrs:
            if isinstance(field_attr.name, str) and field_attr.name == "package":
                attr_path = f"{prog_attr.name}.package"
                if isinstance(field_attr.value, Select) and field_attr.value.base is None:
                    result.packages.append(
                        PackageRef(
                            attribute=".".join(field_attr.value.path)
                            if field_attr.value.path
                            else attr_path
                        )
                    )
                elif isinstance(field_attr.value, Literal):
                    result.packages.append(
                        PackageRef(attribute=str(field_attr.value.value))
                    )
            elif isinstance(field_attr.name, str) and field_attr.name in ("enable", "enableCompletion"):
                result.configs.append(
                    ConfigSet(
                        attrpath=f"programs.{prog_attr.name}.{field_attr.name}",
                        value_expr=ast_to_dict(field_attr.value) if field_attr.value else None,
                        conditional=conditional,
                    )
                )
            elif isinstance(field_attr.name, str) and field_attr.name == "extraConfig":
                result.home_files.append(
                    HomeFile(
                        target=f"programs.{prog_attr.name}",
                        source=_literal_value(field_attr.value)
                        if field_attr.value
                        else None,
                    )
                )
