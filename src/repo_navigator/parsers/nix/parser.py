"""Nix parser — recursive descent over :func:`tokenize` output.

AST nodes are pydantic models with a ``type`` discriminator so they serialize
cleanly for golden tests and later phases. Deviations from the original plan
(all approved):
* ``AttrPath`` folded into :class:`Select` (``base`` may be ``None`` → a bare
  reference / attr-path expression).
* ``IMPL`` (``->``) is a binary operator at the lowest-but-one precedence.
* ``LAMBDA``/``COLON`` share the single ``COLON`` token: a function is
  ``arg : body``.
"""

from __future__ import annotations

from typing import Any, Union

from typing import Literal as Lit

from pydantic import BaseModel, ConfigDict, Field

from repo_navigator.parsers.nix.lexer import Token, TokenType, tokenize


class NixParseError(Exception):
    def __init__(self, message: str, line: int = 0, col: int = 0) -> None:
        self.line = line
        self.col = col
        super().__init__(f"{message} (line {line}, col {col})")


# ------------------------------------------------------------------- AST


class Expr(BaseModel):
    """Base class; concrete nodes set ``type`` to a literal tag."""

    model_config = ConfigDict(populate_by_name=True)

    type: str = Field(default="", exclude=True)


class Literal(Expr):
    type: Lit["Literal"] = "Literal"
    value: Any = None
    value_type: str = "string"  # "int" | "float" | "string" | "path" | "uri" | "bool" | "null"


class Select(Expr):
    type: Lit["Select"] = "Select"
    base: Union["Expr", None] = None
    path: list[str] = Field(default_factory=list)


class AttrSet(Expr):
    type: Lit["AttrSet"] = "AttrSet"
    recursive: bool = False
    attrs: list["AttrDef"] = Field(default_factory=list)


class Inherit(BaseModel):
    type: Lit["Inherit"] = "Inherit"
    from_: Union["Expr", None] = Field(default=None, alias="from")
    names: list[str] = Field(default_factory=list)


class AttrDef(BaseModel):
    name: Union[str, Inherit]
    value: Union["Expr", None] = None


class List(Expr):
    type: Lit["List"] = "List"
    items: list["Expr"] = Field(default_factory=list)


class FormalArg(BaseModel):
    name: str
    default: Union["Expr", None] = None


class Formals(BaseModel):
    ellipsis: bool = False
    fields: list[FormalArg] = Field(default_factory=list)


class Function(Expr):
    type: Lit["Function"] = "Function"
    arg: Union[str, Formals]
    body: "Expr"


class FunctionCall(Expr):
    type: Lit["FunctionCall"] = "FunctionCall"
    func: "Expr"
    arg: "Expr"


class LetIn(Expr):
    type: Lit["LetIn"] = "LetIn"
    bindings: list[AttrDef] = Field(default_factory=list)
    body: "Expr"


class IfThenElse(Expr):
    type: Lit["IfThenElse"] = "IfThenElse"
    cond: "Expr"
    then_: "Expr" = Field(alias="then")
    else_: "Expr" = Field(alias="else")


class With(Expr):
    type: Lit["With"] = "With"
    expr: "Expr"
    body: "Expr"


class Assert(Expr):
    type: Lit["Assert"] = "Assert"
    assertion: "Expr"
    body: "Expr"


class BinaryOp(Expr):
    type: Lit["BinaryOp"] = "BinaryOp"
    op: str
    left: "Expr"
    right: "Expr"


class UnaryOp(Expr):
    type: Lit["UnaryOp"] = "UnaryOp"
    op: str  # "-" | "!"
    expr: "Expr"


class Interpolation(Expr):
    type: Lit["Interpolation"] = "Interpolation"
    parts: list[Union["Expr", str]] = Field(default_factory=list)


class Pin(Expr):
    type: Lit["Pin"] = "Pin"
    name: str


class UnresolvedExpr(Expr):
    type: Lit["UnresolvedExpr"] = "UnresolvedExpr"
    source: str
    reason: str


Expr.model_rebuild()
AttrDef.model_rebuild()
Inherit.model_rebuild()
Select.model_rebuild()
AttrSet.model_rebuild()
List.model_rebuild()
Formals.model_rebuild()
Function.model_rebuild()
FunctionCall.model_rebuild()
LetIn.model_rebuild()
IfThenElse.model_rebuild()
With.model_rebuild()
Assert.model_rebuild()
BinaryOp.model_rebuild()
UnaryOp.model_rebuild()
Interpolation.model_rebuild()
Pin.model_rebuild()
UnresolvedExpr.model_rebuild()


# ----------------------------------------------------------------- parser


class _Parser:
    _PRIMARY_KEYWORDS = {
        TokenType.KW_REC,
        TokenType.KW_LET,
        TokenType.KW_IF,
        TokenType.KW_WITH,
        TokenType.KW_ASSERT,
    }
    _STOPS = {
        TokenType.EOF,
        TokenType.RPAREN,
        TokenType.RBRACK,
        TokenType.RBRACE,
        TokenType.SEMI,
        TokenType.COMMA,
        TokenType.COLON,
        TokenType.KW_IN,
        TokenType.KW_THEN,
        TokenType.KW_ELSE,
        TokenType.KW_INHERIT,
        TokenType.DOT,
    }
    _BINARY_OPS = {
        TokenType.UPDATE: "//",
        TokenType.IMPL: "->",
        TokenType.OR: "||",
        TokenType.AND: "&&",
        TokenType.EQEQ: "==",
        TokenType.NEQ: "!=",
        TokenType.LT: "<",
        TokenType.LE: "<=",
        TokenType.GT: ">",
        TokenType.GE: ">=",
        TokenType.CONCAT: "++",
        TokenType.PLUS: "+",
        TokenType.MINUS: "-",
        TokenType.STAR: "*",
        TokenType.SLASH: "/",
        TokenType.QUESTION: "?",
    }

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.i = 0
        # Set by _parse_func_arg when an @-pattern (x@{...} or {...}@x) is seen.
        self._last_at_pattern: Expr | None = None

    # -------------------------------------------------------------- scanner

    def _peek(self, offset: int = 0) -> Token | None:
        j = self.i + offset
        return self.tokens[j] if 0 <= j < len(self.tokens) else None

    def _next(self) -> Token:
        tok = self._peek()
        if tok is None:
            raise NixParseError("unexpected end of input")
        self.i += 1
        return tok

    def _expect(self, ttype: TokenType) -> Token:
        tok = self._peek()
        if tok is None or tok.type != ttype:
            got = tok.type if tok else "EOF"
            raise NixParseError(f"expected {ttype}, got {got}")
        return self._next()

    def _error(self, message: str) -> NixParseError:
        tok = self._peek()
        line = tok.line if tok else 0
        col = tok.col if tok else 0
        return NixParseError(message, line, col)

    def _can_start_primary(self) -> bool:
        tok = self._peek()
        if tok is None or tok.type in self._STOPS:
            return False
        if tok.type in self._PRIMARY_KEYWORDS:
            return True
        return tok.type in {
            TokenType.IDENT,
            TokenType.INT,
            TokenType.FLOAT,
            TokenType.STRING_DOUBLE,
            TokenType.STRING_HEREDOC,
            TokenType.PATH,
            TokenType.URI,
            TokenType.LPAREN,
            TokenType.LBRACK,
            TokenType.LBRACE,
            TokenType.INTERPOL_START,
        }

    # ----------------------------------------------------------------- run

    def parse(self) -> Expr:
        node = self._parse_lambda()
        rest = self._peek()
        if rest is not None and rest.type not in (TokenType.EOF,):
            raise self._error(f"trailing input after expression: {rest.type}")
        return node

    # ------------------------------------------------------------- lambdas

    def _parse_lambda(self) -> Expr:
        snapshot = self.i
        try:
            arg = self._parse_func_arg()
        except NixParseError:
            self.i = snapshot
            self._last_at_pattern = None
            return self._parse_update()
        if (tok := self._peek()) is not None and tok.type == TokenType.COLON:
            self._next()
            # Preserve @-pattern across the recursive body parse so a
            # non-lambda body (e.g. ``let …``) doesn't reset the flag.
            saved_at = self._last_at_pattern
            body = self._parse_lambda()
            fn: Expr = Function(arg=arg, body=body)
            if saved_at is not None:
                at_name = saved_at
                bound = AttrSet(
                    recursive=False,
                    attrs=[AttrDef(name=at_name.path[0], value=at_name)],
                )
                fn = LetIn(bindings=bound.attrs, body=fn)
            return fn
        # Not a lambda after all: rewind and parse as a normal expression.
        self.i = snapshot
        self._last_at_pattern = None
        return self._parse_update()

    def _parse_func_arg(self) -> Union[str, Formals]:
        """Function argument, including Nix @-patterns.

        Valid forms:
          * ``x:``            — bare name
          * ``{ a, b, ... }:``  — formals
          * ``x@{ a, b }:``   — name then @ then formals
          * ``{ a, b }@x:``   — formals then @ then name
        In both @-forms the leading name also becomes a ``Select`` binding
        for the whole argset; we return the *formals* as the primary arg and
        record the bound name in ``self._last_at_pattern`` (consumed by the
        lambda parser to synthesise the pattern binding).
        """
        tok = self._peek()
        if tok is not None and tok.type == TokenType.IDENT:
            first = self._next()
            if (nxt := self._peek()) is not None and nxt.type == TokenType.AT:
                self._next()
                formals = self._parse_formals()
                self._last_at_pattern = Select(base=None, path=[first.value])
                return formals
            return first.value
        if tok is not None and tok.type == TokenType.LBRACE:
            # Если после { первый IDENT затем DOT — это attrset, не formals.
            # Быстрая защита: сканируем без потребления.
            first_inside = self._peek(1)
            second_inside = self._peek(2)
            if (first_inside is not None and first_inside.type == TokenType.IDENT and
                    second_inside is not None and second_inside.type == TokenType.DOT):
                raise self._error("expected function argument")
            formals = self._parse_formals()
            if (nxt := self._peek()) is not None and nxt.type == TokenType.AT:
                self._next()
                tok2 = self._peek()
                if tok2 is not None and tok2.type == TokenType.IDENT:
                    self._next()
                    self._last_at_pattern = Select(base=None, path=[tok2.value])
                return formals
            return formals
        raise self._error("expected function argument")

    def _parse_formals(self) -> Formals:
        self._expect(TokenType.LBRACE)
        fields: list[FormalArg] = []
        ellipsis = False
        while True:
            tok = self._peek()
            if tok is None:
                raise self._error("unterminated formals")
            if tok.type == TokenType.RBRACE:
                self._next()
                break
            if tok.type == TokenType.ELLIPSIS:
                self._next()
                ellipsis = True
                if (nxt := self._peek()) is not None and nxt.type == TokenType.COMMA:
                    self._next()
                continue
            if tok.type != TokenType.IDENT:
                raise self._error("expected formal name")
            name = self._next().value
            default: Expr | None = None
            if (nxt := self._peek()) is not None and nxt.type == TokenType.QUESTION:
                self._next()
                default = self._parse_expr()
            fields.append(FormalArg(name=name, default=default))
            nxt = self._peek()
            if nxt is None:
                raise self._error("unterminated formals")
            if nxt.type == TokenType.COMMA:
                self._next()
                continue
            if nxt.type == TokenType.RBRACE:
                continue
            raise self._error("expected ',' or '}' in formals")
        return Formals(ellipsis=ellipsis, fields=fields)

    # -------------------------------------------------------- precedence tiers

    def _parse_expr(self) -> Expr:
        return self._parse_lambda()

    def _parse_update(self) -> Expr:
        return self._parse_left_assoc(TokenType.UPDATE, self._parse_impl)

    def _parse_impl(self) -> Expr:
        return self._parse_left_assoc(TokenType.IMPL, self._parse_or)

    def _parse_or(self) -> Expr:
        return self._parse_left_assoc(TokenType.OR, self._parse_and)

    def _parse_and(self) -> Expr:
        return self._parse_left_assoc(TokenType.AND, self._parse_comparison)

    def _parse_comparison(self) -> Expr:
        return self._parse_left_assoc(
            {TokenType.EQEQ, TokenType.NEQ, TokenType.LT, TokenType.LE,
             TokenType.GT, TokenType.GE},
            self._parse_hasattr,
        )

    def _parse_hasattr(self) -> Expr:
        left = self._parse_concat()
        while (tok := self._peek()) is not None and tok.type == TokenType.QUESTION:
            self._next()
            right = self._parse_attrpath(None)
            left = BinaryOp(op="?", left=left, right=right)
        return left

    def _parse_concat(self) -> Expr:
        return self._parse_left_assoc(TokenType.CONCAT, self._parse_additive)

    def _parse_additive(self) -> Expr:
        return self._parse_left_assoc(
            {TokenType.PLUS, TokenType.MINUS}, self._parse_multiplicative
        )

    def _parse_multiplicative(self) -> Expr:
        return self._parse_left_assoc(
            {TokenType.STAR, TokenType.SLASH}, self._parse_unary
        )

    def _parse_left_assoc(self, op_types, operand_fn):
        if isinstance(op_types, TokenType):
            op_types = {op_types}
        left = operand_fn()
        while (tok := self._peek()) is not None and tok.type in op_types:
            self._next()
            right = operand_fn()
            left = BinaryOp(op=self._BINARY_OPS[tok.type], left=left, right=right)
        return left

    def _parse_unary(self) -> Expr:
        tok = self._peek()
        if tok is not None and tok.type == TokenType.MINUS:
            self._next()
            return UnaryOp(op="-", expr=self._parse_unary())
        if tok is not None and tok.type == TokenType.NOT:
            self._next()
            return UnaryOp(op="!", expr=self._parse_unary())
        return self._parse_postfix()

    # ---------------------------------------------------------- postfix: app/select

    def _parse_postfix(self) -> Expr:
        e = self._parse_primary()
        while True:
            tok = self._peek()
            if tok is None:
                break
            if tok.type == TokenType.DOT:
                self._next()
                name = self._read_attr_segment()
                path = e.path + [name] if isinstance(e, Select) and e.base is None else [name]
                base = e if (isinstance(e, Select) and e.base is None) else e
                if isinstance(e, Select) and e.base is None:
                    e = Select(base=None, path=e.path + [name])
                else:
                    e = Select(base=e, path=[name])
                continue
            if self._can_start_primary():
                arg = self._parse_primary()
                e = FunctionCall(func=e, arg=arg)
                continue
            break
        return e

    def _parse_postfix_no_funcall(self) -> Expr:
        """Parse a primary followed by attribute access, but NOT function application.

        Used for list items where ``a b c`` should be separate items,
        not nested function calls.
        """
        e = self._parse_primary()
        while True:
            tok = self._peek()
            if tok is None or tok.type != TokenType.DOT:
                break
            self._next()
            name = self._read_attr_segment()
            if isinstance(e, Select) and e.base is None:
                e = Select(base=None, path=e.path + [name])
            else:
                e = Select(base=e, path=[name])
        return e

    # ---------------------------------------------------------------- primary

    def _parse_primary(self) -> Expr:
        tok = self._peek()
        if tok is None:
            raise self._error("unexpected end of input")

        if tok.type == TokenType.LPAREN:
            self._next()
            if (nxt := self._peek()) is not None and nxt.type == TokenType.RPAREN:
                self._next()
                return UnresolvedExpr(source="()", reason="empty_parentheses")
            node = self._parse_expr()
            self._expect(TokenType.RPAREN)
            return node

        if tok.type == TokenType.LBRACK:
            return self._parse_list()

        if tok.type == TokenType.LBRACE:
            return self._parse_attrset()

        if tok.type in (TokenType.STRING_DOUBLE, TokenType.STRING_HEREDOC):
            return self._parse_stringish()

        if tok.type in (TokenType.INT, TokenType.FLOAT, TokenType.PATH, TokenType.URI):
            self._next()
            return Literal(value=tok.value, value_type=tok.type.lower())

        if tok.type == TokenType.INTERPOL_START:
            # Bare interpolation at the top level (rare).
            return self._parse_stringish()

        if tok.type == TokenType.KW_REC:
            self._next()
            self._expect(TokenType.LBRACE)
            attrs = self._parse_attrbindings()
            self._expect(TokenType.RBRACE)
            return AttrSet(recursive=True, attrs=attrs)

        if tok.type == TokenType.KW_LET:
            return self._parse_let()

        if tok.type == TokenType.KW_IF:
            return self._parse_ifthenelse()

        if tok.type == TokenType.KW_WITH:
            return self._parse_with()

        if tok.type == TokenType.KW_ASSERT:
            return self._parse_assert()

        if tok.type == TokenType.IDENT:
            self._next()
            if tok.value == "true":
                return Literal(value=True, value_type="bool")
            if tok.value == "false":
                return Literal(value=False, value_type="bool")
            if tok.value == "null":
                return Literal(value=None, value_type="null")
            return Select(base=None, path=[tok.value])

        raise self._error(f"unexpected token {tok.type}")

    def _parse_stringish(self) -> Expr:
        parts: list[Union[Expr, str]] = []
        while True:
            tok = self._peek()
            if tok is None:
                break
            if tok.type in (TokenType.STRING_DOUBLE, TokenType.STRING_HEREDOC):
                self._next()
                parts.append(Literal(value=tok.value, value_type="string"))
            elif tok.type == TokenType.INTERPOL_START:
                self._next()
                inner = self._parse_expr()
                self._expect(TokenType.INTERPOL_END)
                parts.append(inner)
            else:
                break
        if len(parts) == 1 and isinstance(parts[0], Literal):
            return parts[0]
        return Interpolation(parts=parts)

    def _parse_list(self) -> Expr:
        self._expect(TokenType.LBRACK)
        items: list[Expr] = []
        while (tok := self._peek()) is not None and tok.type != TokenType.RBRACK:
            items.append(self._parse_postfix_no_funcall())
        self._expect(TokenType.RBRACK)
        return List(items=items)

    def _parse_attrset(self) -> Expr:
        self._expect(TokenType.LBRACE)
        attrs = self._parse_attrbindings()
        self._expect(TokenType.RBRACE)
        return AttrSet(recursive=False, attrs=attrs)

    def _parse_attrbindings(self) -> list[AttrDef]:
        attrs: list[AttrDef] = []
        while True:
            tok = self._peek()
            if tok is None:
                raise self._error("unterminated attrset")
            if tok.type == TokenType.RBRACE:
                break
            if tok.type == TokenType.KW_IN:
                break
            if tok.type == TokenType.EQ:
                # EQ не может начинать attrpath — это разделитель, пусть вызывающий обработает.
                break
            if tok.type == TokenType.KW_INHERIT:
                attrs.append(self._parse_inherit())
                continue
            segments = self._parse_attrpath_segments()
            self._expect(TokenType.EQ)
            value = self._parse_expr()
            self._expect(TokenType.SEMI)
            attrs.append(self._expand_attrpath(segments, value))
        return attrs

    def _parse_inherit(self) -> AttrDef:
        self._expect(TokenType.KW_INHERIT)
        from_expr: Expr | None = None
        if (tok := self._peek()) is not None and tok.type == TokenType.LPAREN:
            self._next()
            from_expr = self._parse_expr()
            self._expect(TokenType.RPAREN)
        names: list[str] = []
        while (tok := self._peek()) is not None and tok.type == TokenType.IDENT:
            names.append(self._next().value)
        self._expect(TokenType.SEMI)
        return AttrDef(name=Inherit(from_=from_expr, names=names), value=None)

    def _parse_attrpath_segments(self) -> list[tuple[str, str]]:
        """Return list of (kind, value) where kind in {'name','dyn'}."""
        segments: list[tuple[str, str]] = []
        # first segment
        segments.append(self._read_attrpath_segment())
        while (tok := self._peek()) is not None and tok.type == TokenType.DOT:
            self._next()
            segments.append(self._read_attrpath_segment())
        return segments

    def _read_attrpath_segment(self) -> tuple[str, str]:
        tok = self._peek()
        if tok is None:
            raise self._error("expected attribute name")
        if tok.type == TokenType.IDENT:
            self._next()
            return ("name", tok.value)
        if tok.type in (TokenType.STRING_DOUBLE, TokenType.STRING_HEREDOC):
            # Строка с интерполяцией ("a${b}") токенизируется как STRING + INTERPOL_START + ...,
            # поэтому если за строкой сразу идёт интерполяция — весь stringish это dyn-ключ.
            if self._peek(1) is not None and self._peek(1).type == TokenType.INTERPOL_START:
                expr = self._parse_stringish()
                return ("dyn", expr.model_dump_json())
            self._next()
            return ("name", tok.value)
        if tok.type == TokenType.INTERPOL_START:
            # Может быть несколько интерполяций и строковых фрагментов:
            #   "a${b}c" → STRING_DOUBLE("a") + INTERPOL_START + ... + STRING_DOUBLE("c"),
            #   "${b}c"  → INTERPOL_START + ... + STRING_DOUBLE("c") (пустой префикс не эмитится).
            # Используем _parse_stringish чтобы потребить всё.
            expr = self._parse_stringish()
            return ("dyn", expr.model_dump_json())
        if tok.type == TokenType.LBRACK:
            self._next()
            inner = self._parse_expr()
            self._expect(TokenType.RBRACK)
            return ("dyn", inner.model_dump_json())
        raise self._error(f"invalid attribute path segment: {tok.type}")

    def _read_attr_segment(self) -> str:
        """Single segment after a DOT in an expression selection."""
        kind, value = self._read_attrpath_segment()
        return value if kind == "name" else f"[{value}]"

    def _parse_attrpath(self, base: Expr | None) -> Expr:
        segments = self._parse_attrpath_segments()
        names = [v for k, v in segments if k == "name"]
        dyn = [v for k, v in segments if k == "dyn"]
        path = names + (["…"] if dyn else [])
        return Select(base=base, path=path)

    def _expand_attrpath(self, segments: list[tuple[str, str]], value: Expr) -> AttrDef:
        names = [v for k, v in segments if k == "name"]
        dynamic = any(k == "dyn" for k, _ in segments)
        if dynamic:
            joined = ".".join(names + ["…"])
            return AttrDef(name=joined, value=UnresolvedExpr(source=joined, reason="dynamic_key"))
        if len(names) == 1:
            return AttrDef(name=names[0], value=value)
        # Build nested AttrSet: a.b.c = v  ->  a = { b = { c = v; }; };
        inner: AttrDef = AttrDef(name=names[-1], value=value)
        for name in reversed(names[:-1]):
            inner = AttrDef(name=name, value=AttrSet(recursive=False, attrs=[inner]))
        return inner

    def _parse_let(self) -> Expr:
        self._expect(TokenType.KW_LET)
        bindings = self._parse_attrbindings()
        self._expect(TokenType.KW_IN)
        body = self._parse_expr()
        return LetIn(bindings=bindings, body=body)

    def _parse_ifthenelse(self) -> Expr:
        self._expect(TokenType.KW_IF)
        cond = self._parse_expr()
        self._expect(TokenType.KW_THEN)
        then = self._parse_expr()
        self._expect(TokenType.KW_ELSE)
        else_ = self._parse_expr()
        return IfThenElse(cond=cond, then_=then, else_=else_)

    def _parse_with(self) -> Expr:
        self._expect(TokenType.KW_WITH)
        expr = self._parse_expr()
        self._expect(TokenType.SEMI)
        body = self._parse_expr()
        return With(expr=expr, body=body)

    def _parse_assert(self) -> Expr:
        self._expect(TokenType.KW_ASSERT)
        assertion = self._parse_expr()
        self._expect(TokenType.SEMI)
        body = self._parse_expr()
        return Assert(assertion=assertion, body=body)


def parse(source: str) -> Expr:
    """Tokenize ``source`` and parse it into an :class:`Expr` AST root."""
    tokens = tokenize(source)
    return _Parser(tokens).parse()


def ast_to_dict(value: Any) -> Any:
    """Recursively serialize an AST node (or any value) to a plain dict.

    pydantic's ``model_dump`` collapses ``Union["Expr", None]`` to the base
    schema (losing subclass fields), so we serialize by the *actual* type.
    """
    if isinstance(value, BaseModel):
        out: dict[str, Any] = {}
        for name, fld in type(value).model_fields.items():
            out[fld.alias or name] = ast_to_dict(getattr(value, name))
        return out
    if isinstance(value, list):
        return [ast_to_dict(v) for v in value]
    if isinstance(value, dict):
        return {k: ast_to_dict(v) for k, v in value.items()}
    return value


def parse_to_dict(source: str) -> dict:
    """Convenience for golden tests: parse and dump to a plain dict."""
    return ast_to_dict(parse(source))
