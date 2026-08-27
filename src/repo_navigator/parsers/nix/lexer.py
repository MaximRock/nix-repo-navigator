"""Nix lexer — tokenizes source into a flat stream of tokens.

A small, dependency-free recursive-context scanner. It is intentionally
lenient: unknown characters are skipped rather than raising, so a malformed
file never crashes the indexer (errors surface later, in the parser).

Conventions (deviations from the original plan, all approved):
* Added tokens: ``COMMA``, ``AT``, ``LT/LE/GT/GE``, ``EQEQ``, ``AND``,
  ``PLUS/MINUS/STAR/SLASH``, ``CONCAT``, ``UPDATE``, ``NEQ``, ``NOT``, ``EOF``.
* Merged: ``LAMBDA`` → ``COLON`` (one ``':'`` char, parser disambiguates),
  ``ARROW`` → ``IMPL`` (Nix has a single ``->`` operator).
* Dropped ``DOLLAR`` (a bare ``$`` outside a string is invalid Nix).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TokenType(StrEnum):
    IDENT = "IDENT"
    INT = "INT"
    FLOAT = "FLOAT"
    PATH = "PATH"
    URI = "URI"
    STRING_DOUBLE = "STRING_DOUBLE"
    STRING_HEREDOC = "STRING_HEREDOC"
    INTERPOL_START = "INTERPOL_START"
    INTERPOL_END = "INTERPOL_END"
    LBRACE = "LBRACE"
    RBRACE = "RBRACE"
    LBRACK = "LBRACK"
    RBRACK = "RBRACK"
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    DOT = "DOT"
    SEMI = "SEMI"
    COLON = "COLON"
    COMMA = "COMMA"
    AT = "AT"
    ELLIPSIS = "ELLIPSIS"
    EQ = "EQ"
    EQEQ = "EQEQ"
    NEQ = "NEQ"
    OR = "OR"
    AND = "AND"
    IMPL = "IMPL"
    NOT = "NOT"
    QUESTION = "QUESTION"
    UPDATE = "UPDATE"
    CONCAT = "CONCAT"
    PLUS = "PLUS"
    MINUS = "MINUS"
    STAR = "STAR"
    SLASH = "SLASH"
    LT = "LT"
    LE = "LE"
    GT = "GT"
    GE = "GE"
    KW_LET = "KW_LET"
    KW_IN = "KW_IN"
    KW_IF = "KW_IF"
    KW_THEN = "KW_THEN"
    KW_ELSE = "KW_ELSE"
    KW_WITH = "KW_WITH"
    KW_ASSERT = "KW_ASSERT"
    KW_REC = "KW_REC"
    KW_INHERIT = "KW_INHERIT"
    EOF = "EOF"


KEYWORDS = {
    "let": TokenType.KW_LET,
    "in": TokenType.KW_IN,
    "if": TokenType.KW_IF,
    "then": TokenType.KW_THEN,
    "else": TokenType.KW_ELSE,
    "with": TokenType.KW_WITH,
    "assert": TokenType.KW_ASSERT,
    "rec": TokenType.KW_REC,
    "inherit": TokenType.KW_INHERIT,
    "or": TokenType.OR,
}


@dataclass
class Token:
    type: str
    value: str
    line: int
    col: int

    def to_dict(self) -> dict:
        return {"type": self.type, "value": self.value, "line": self.line, "col": self.col}


def _is_ident_start(c: str) -> bool:
    return c.isalpha() or c == "_"


def _is_ident_char(c: str) -> bool:
    return c.isalnum() or c in ("_", "'", "-")


def _unescape_double(e: str) -> str:
    return {
        "n": "\n",
        "t": "\t",
        "r": "\r",
        "\\": "\\",
        '"': '"',
        "$": "$",
        "/": "/",
    }.get(e, e)


_HEREDOC_ESCAPES = {
    "\\": "\\",
    "'": "'",
    '"': '"',
    "$": "$",
    "n": "\n",
    "t": "\t",
    "r": "\r",
}


def _unescape_heredoc(e: str) -> str:
    return _HEREDOC_ESCAPES.get(e, e)


class _Lexer:
    def __init__(self, src: str) -> None:
        self.src = src
        self.n = len(src)
        self.i = 0
        self.line = 1
        self.col = 1
        self.tokens: list[Token] = []
        self.mode = "root"  # "root" | "sstr" | "hstr"
        self.interp_depth = 0
        self.balance = 0
        self.str_stack: list[str] = []
        self._buf = ""

    # ---------------------------------------------------------------- helpers

    def _emit(self, ttype: TokenType, value: str, line: int, col: int) -> None:
        self.tokens.append(Token(ttype, value, line, col))

    def _advance(self, k: int = 1) -> None:
        for _ in range(k):
            if self.i >= self.n:
                break
            c = self.src[self.i]
            self.i += 1
            if c == "\n":
                self.line += 1
                self.col = 1
            else:
                self.col += 1

    def _peek(self, offset: int = 0) -> str:
        j = self.i + offset
        return self.src[j] if 0 <= j < self.n else ""

    # ------------------------------------------------------------------ main

    def run(self) -> list[Token]:
        while self.i < self.n:
            if self.mode == "root":
                self._step_root()
            elif self.mode == "sstr":
                self._step_sstr()
            else:
                self._step_hstr()
        if self.mode in ("sstr", "hstr") and self._buf:
            self._flush_string()
        self._emit(TokenType.EOF, "", self.line, self.col)
        return self.tokens

    # --------------------------------------------------------------- root mode

    def _step_root(self) -> None:
        c = self.src[self.i]
        n1 = self._peek(1)
        if c.isspace():
            self._advance()
            return
        if c == "#":
            while self.i < self.n and self.src[self.i] != "\n":
                self._advance()
            return
        if c == "/" and self._peek(1) == "*":
            self._advance(2)
            while self.i < self.n and not (
                self.src[self.i] == "*" and self._peek(1) == "/"
            ):
                self._advance()
            if self.i < self.n:
                self._advance(2)
            return

        if c.isdigit():
            self._read_number()
            return

        if c in (".", "~") and n1 == "/":
            self._read_path()
            return
        if c == "." and n1 == "." and self._peek(2) == "/":
            self._read_path()
            return
        if c == "/" and self._next_is_path_char():
            self._read_path()
            return

        if _is_ident_start(c):
            self._read_ident()
            return

        if c == '"':
            self._enter_sstr()
            return
        if c == "'" and self._peek(1) == "'":
            self._enter_hstr()
            return

        self._read_operator_or_punct(c)

    def _read_operator_or_punct(self, c: str) -> None:
        n1, n2 = self._peek(1), self._peek(2)
        sl, sc = self.line, self.col

        if c == "/":
            if n1 == "*":
                return  # comment, handled above
            if n1 == "/":
                self._emit(TokenType.UPDATE, "//", sl, sc)
                self._advance(2)
                return
            self._emit(TokenType.SLASH, "/", sl, sc)
            self._advance()
            return
        if c == "*":
            self._emit(TokenType.STAR, "*", sl, sc)
            self._advance()
            return
        if c == "+":
            if n1 == "+":
                self._emit(TokenType.CONCAT, "++", sl, sc)
                self._advance(2)
                return
            self._emit(TokenType.PLUS, "+", sl, sc)
            self._advance()
            return
        if c == "-":
            if n1 == ">":
                self._emit(TokenType.IMPL, "->", sl, sc)
                self._advance(2)
                return
            self._emit(TokenType.MINUS, "-", sl, sc)
            self._advance()
            return
        if c == "=":
            if n1 == "=":
                self._emit(TokenType.EQEQ, "==", sl, sc)
                self._advance(2)
                return
            self._emit(TokenType.EQ, "=", sl, sc)
            self._advance()
            return
        if c == "!":
            if n1 == "=":
                self._emit(TokenType.NEQ, "!=", sl, sc)
                self._advance(2)
                return
            self._emit(TokenType.NOT, "!", sl, sc)
            self._advance()
            return
        if c == "?":
            self._emit(TokenType.QUESTION, "?", sl, sc)
            self._advance()
            return
        if c == "<":
            if n1 == "=":
                self._emit(TokenType.LE, "<=", sl, sc)
                self._advance(2)
                return
            if (n1.isalpha() or n1 == "/") and self._scan_path_lookup():
                return
            self._emit(TokenType.LT, "<", sl, sc)
            self._advance()
            return
        if c == ">":
            if n1 == "=":
                self._emit(TokenType.GE, ">=", sl, sc)
                self._advance(2)
                return
            self._emit(TokenType.GT, ">", sl, sc)
            self._advance()
            return
        if c == "&" and n1 == "&":
            self._emit(TokenType.AND, "&&", sl, sc)
            self._advance(2)
            return
        if c == "|" and n1 == "|":
            self._emit(TokenType.OR, "||", sl, sc)
            self._advance(2)
            return
        if c == ".":
            if n1 == "." and n2 == ".":
                self._emit(TokenType.ELLIPSIS, "...", sl, sc)
                self._advance(3)
                return
            self._emit(TokenType.DOT, ".", sl, sc)
            self._advance()
            return
        if c == ",":
            self._emit(TokenType.COMMA, ",", sl, sc)
            self._advance()
            return
        if c == "@":
            self._emit(TokenType.AT, "@", sl, sc)
            self._advance()
            return
        if c == ";":
            self._emit(TokenType.SEMI, ";", sl, sc)
            self._advance()
            return
        if c == ":":
            self._emit(TokenType.COLON, ":", sl, sc)
            self._advance()
            return
        if c == "(":
            self.balance += 1
            self._emit(TokenType.LPAREN, "(", sl, sc)
            self._advance()
            return
        if c == ")":
            self.balance -= 1
            self._emit(TokenType.RPAREN, ")", sl, sc)
            self._advance()
            return
        if c == "{":
            self.balance += 1
            self._emit(TokenType.LBRACE, "{", sl, sc)
            self._advance()
            return
        if c == "}":
            if self.interp_depth > 0 and self.balance <= 0:
                self._close_interpolation(sl, sc)
                return
            self.balance -= 1
            self._emit(TokenType.RBRACE, "}", sl, sc)
            self._advance()
            return
        if c == "[":
            self._emit(TokenType.LBRACK, "[", sl, sc)
            self._advance()
            return
        if c == "]":
            self._emit(TokenType.RBRACK, "]", sl, sc)
            self._advance()
            return
        if c == "$" and n1 == "{":
            # Bare interpolation at top level (syntax error in Nix, but we
            # still tokenize to avoid losing the rest of the file).
            self._start_interpolation("root", sl, sc)
            return

        # Unknown character — skip to stay robust.
        self._advance()

    def _scan_path_lookup(self) -> bool:
        """If ``<ident...>`` forms a path lookup, emit PATH and return True."""
        j = self.i + 1
        start = self.i
        # skip leading '/' (e.g. </abs/path>)
        while j < self.n and self.src[j] == "/":
            j += 1
        body_start = j
        while j < self.n and (self.src[j].isalnum() or self.src[j] in "._-+~/"):
            j += 1
        if j < self.n and self.src[j] == ">" and j > body_start:
            value = self.src[start : j + 1]
            self._emit(TokenType.PATH, value, self.line, self.col)
            self._advance(j - self.i + 1)
            return True
        return False

    def _read_number(self) -> None:
        sl, sc = self.line, self.col
        start = self.i
        if self.src[self.i : self.i + 2] in ("0x", "0X"):
            self.i += 2
            while self.i < self.n and (
                self.src[self.i] in "0123456789abcdefABCDEF"
            ):
                self._advance()
            self._emit(TokenType.INT, self.src[start : self.i], sl, sc)
            return

        j = self.i
        is_float = False
        while j < self.n and self.src[j].isdigit():
            j += 1
        if j < self.n and self.src[j] == "." and j + 1 < self.n and self.src[j + 1].isdigit():
            is_float = True
            j += 1
            while j < self.n and self.src[j].isdigit():
                j += 1
        if j < self.n and self.src[j] in "eE" and (
            (j + 1 < self.n and self.src[j + 1].isdigit())
            or (
                j + 2 < self.n
                and self.src[j + 1] in "+-"
                and self.src[j + 2].isdigit()
            )
        ):
            is_float = True
            j += 1
            if j < self.n and self.src[j] in "+-":
                j += 1
            while j < self.n and self.src[j].isdigit():
                j += 1

        value = self.src[start:j]
        self._emit(TokenType.FLOAT if is_float else TokenType.INT, value, sl, sc)
        self._advance(j - self.i)

    def _next_is_path_char(self) -> bool:
        n1 = self._peek(1)
        return n1.isalnum() or n1 in ("_", ".", "-", "+", "~")

    def _read_path(self) -> None:
        sl, sc = self.line, self.col
        path_chars = set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
            "0123456789._+/-~"
        )
        j = self.i
        while j < self.n and self.src[j] in path_chars:
            j += 1
        value = self.src[self.i : j]
        if "/" not in value:
            # Not actually a path (e.g. stray chars) — emit as a slash run.
            self._emit(TokenType.SLASH, value, sl, sc)
        else:
            self._emit(TokenType.PATH, value, sl, sc)
        self._advance(j - self.i)

    def _read_ident(self) -> None:
        sl, sc = self.line, self.col
        start = self.i
        while self.i < self.n and _is_ident_char(self.src[self.i]):
            self._advance()
        word = self.src[start : self.i]

        # URI detection: ident followed by "://"
        if self._peek(0) == ":" and self._peek(1) == "/" and self._peek(2) == "/":
            j = self.i
            j += 3
            while j < self.n and self.src[j] not in ' \t\n\r"\'(){}[];':
                j += 1
            value = self.src[start:j]
            self._emit(TokenType.URI, value, sl, sc)
            self._advance(j - self.i)
            return

        if word in KEYWORDS:
            self._emit(KEYWORDS[word], word, sl, sc)
        else:
            self._emit(TokenType.IDENT, word, sl, sc)

    # --------------------------------------------------------------- strings

    def _enter_sstr(self) -> None:
        sl, sc = self.line, self.col
        self._advance()  # consume opening "
        self._buf = ""
        self._str_start_line, self._str_start_col = sl, sc
        self.mode = "sstr"

    def _flush_string(self) -> None:
        if self._buf:
            ttype = (
                TokenType.STRING_HEREDOC
                if self.mode == "hstr"
                else TokenType.STRING_DOUBLE
            )
            self._emit(ttype, self._buf, self._str_start_line, self._str_start_col)
            self._buf = ""

    def _step_sstr(self) -> None:
        c = self.src[self.i]
        if c == '"':
            self._flush_string()
            self._advance()
            self.mode = "root"
            return
        if c == "\\":
            sl, sc = self.line, self.col
            self._advance()
            e = self.src[self.i] if self.i < self.n else ""
            self._buf += _unescape_double(e)
            self._advance()
            return
        if c == "$" and self._peek(1) == "{":
            self._flush_string()
            self._start_interpolation("sstr", self.line, self.col)
            return
        self._buf += c
        self._advance()

    def _enter_hstr(self) -> None:
        sl, sc = self.line, self.col
        self._advance(2)  # consume opening ''
        self._buf = ""
        self._str_start_line, self._str_start_col = sl, sc
        self._h_line_start = True
        self.mode = "hstr"

    def _step_hstr(self) -> None:
        c = self.src[self.i]

        # Closing '' must be tested while _h_line_start is still true, i.e.
        # BEFORE the content flag gets flipped by the quote character below.
        if c == "'" and self._peek(1) == "'":
            nxt = self._peek(2)
            if self._h_line_start:
                # A '' at the start of a line closes the indented string.
                self._flush_string()
                self._advance(2)
                self.mode = "root"
                return
            if nxt == "$":
                self._buf += "$"
                self._advance(3)
                return
            if nxt in _HEREDOC_ESCAPES:
                self._buf += _unescape_heredoc(nxt)
                self._advance(3)
                return
            if nxt == "'":  # ''' -> single quote
                self._buf += "'"
                self._advance(3)
                return
            if nxt == "":  # EOF after '' -> close
                self._flush_string()
                self._advance(2)
                self.mode = "root"
                return
            # Lenient close: if `''` mid-line followed by a delimiter
            # (`;  ,  }  )  ]  \n`), close the heredoc per common expectation.
            if nxt in (";", ",", "}", ")", "]", "\n"):
                self._flush_string()
                self._advance(2)
                self.mode = "root"
                return
            # mid-line '' not an escape: consume and continue
            self._advance(2)
            return

        if c == "\n":
            self._h_line_start = True
            self._buf += c
            self._advance()
            return
        if c.strip():
            self._h_line_start = False

        if c == "$" and self._peek(1) == "{":
            self._flush_string()
            self._start_interpolation("hstr", self.line, self.col)
            return

        self._buf += c
        self._advance()

    # ---------------------------------------------------------- interpolation

    def _start_interpolation(self, enclosing: str, sl: int, sc: int) -> None:
        self._emit(TokenType.INTERPOL_START, "${", sl, sc)
        self._advance(2)
        self.str_stack.append(enclosing)
        self.interp_depth += 1
        self.balance = 0
        self.mode = "root"

    def _close_interpolation(self, sl: int, sc: int) -> None:
        self._emit(TokenType.INTERPOL_END, "}", sl, sc)
        self._advance()
        self.interp_depth -= 1
        self.mode = self.str_stack.pop() if self.str_stack else "root"


def tokenize(source: str) -> list[Token]:
    """Tokenize Nix ``source`` into a list of :class:`Token` (ends with EOF)."""
    return _Lexer(source).run()


def tokenize_to_dicts(source: str) -> list[dict]:
    """Convenience for golden tests: tokens as plain dicts."""
    return [t.to_dict() for t in tokenize(source)]
