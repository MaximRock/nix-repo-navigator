# План починки парсера (баги Фазы 2)

## Баг 1: `${}` динамические ключи в attrpath

**Файл:** `src/repo_navigator/parsers/nix/parser.py`
**Метод:** `_read_attrpath_segment()` (строки ~562-577)

**Симптом:** `invalid attribute path segment: INTERPOL_START`

**Причина:** `{ "a${b}" = 1; }` — Nix-attrpath с интерполяцией внутри. Лексер выдаёт INTERPOL_START, но `_read_attrpath_segment` не имеет обработчика для этого токена.

**Фикс:** добавить кейс для INTERPOL_START между STRING_HEREDOC и LBRACK:

```python
    if tok.type in (TokenType.STRING_DOUBLE, TokenType.STRING_HEREDOC):
        self._next()
        return ("name", tok.value)
    # === НОВЫЙ КЕЙС ===
    if tok.type == TokenType.INTERPOL_START:
        self._next()  # consume ${
        inner = self._parse_expr()
        self._expect(TokenType.INTERPOL_END)
        return ("dyn", inner.model_dump_json())
    # ==================
    if tok.type == TokenType.LBRACK:
```

**Golden-тест:** создать `tests/golden/parser/string_interp_key.nix`:

```nix
{ "a${b}" = 1; }
```

**Expected** (после первого прогона `--update-golden`): AST с AttrSet и dyn-сегментом.

---

## Баг 2: EQ в середине attrpath

**Файл:** `src/repo_navigator/parsers/nix/parser.py`
**Метод:** `_parse_attrbindings()` (строки ~585-603)

**Симптом:** `invalid attribute path segment: EQ` — парсер заходит в `_parse_attrpath_segments`, а там токен EQ.

**Причина:** После полного attrpath (например, `a.b.c = v`) токен EQ — разделитель. Но в некоторых сложных вложенных присваиваниях парсер не выходит из цикла `_parse_attrbindings` до EQ и снова вызывает `_parse_attrpath_segments`.

**Фикс:** добавить break на EQ в начале цикла _parse_attrbindings:

```python
def _parse_attrbindings(self) -> list[AttrDef]:
    attrs: list[AttrDef] = []
    while True:
        tok = self._peek()
        if tok is None:
            raise self._error("unterminated attrset")
        if tok.type in (TokenType.RBRACE, TokenType.KW_IN):
            break
        # === НОВОЕ УСЛОВИЕ ===
        if tok.type == TokenType.EQ:
            # EQ не может начинать attrpath — значит attrpath уже кончился,
            # и это разделитель. Выходим, пусть вызывающий обработает.
            break
        # =====================
        if tok.type == TokenType.KW_INHERIT:
            attrs.append(self._parse_inherit())
            continue
        segments = self._parse_attrpath_segments()
        self._expect(TokenType.EQ)
        value = self._parse_expr()
        self. _expect(TokenType.SEMI)
        attrs.append(self._expand_attrpath(segments, value))
    return attrs
```

**Golden-тест:** использовать существующий `tests/golden/parser/nested_attrs.nix` или создать новый тест с глубоко вложенными attrpath.

---

## Баг 3: Formals — дефолтные значения со сложными выраениями

**Файл:** src/repo_navigator/parsers/nix/parser.py`
**Метод:** `_parse_formals()` (строки ~3403-374)

**Симптом:** expected ',' or '}' in formals` на `{ lib ? pkgs.lib, ... }`

**Причина:** `self._parse_expr()` в default-значении потребит comma/}` — он не знает о контексте formals.

**Фикс:** Заменить `self._parse_expr()` на `self._parse_expr_until (stop_tokens)` внутри `_parse_formals`:

```python
# Вместо:
            default = self._parse_exпр(
# Нужно:
            default = self._parse_expr_until ({TokenType.COMMA, TokenType.RBRACE})
```

Но проше — ввести **ограниченный парсер выажения** для дефолтного значения:

```python
def _parse_expr_until(self, stop_types: set[TokenType]) -> Expr:
    """Parse an expression until one of *stop_types* is encountered at depth 0."""
    # Собираем под-список токенов до stop_type на глубине 0
    saved_i = self.i
    depth = 0
    while True:
        tok = self._peek()
        if tok is None:
            break
        if depth == 0 and tok.type in stop_types:
            break
        if tok.type in (TokenType.LBRACE, TokenType.LPAREN,
                        TokenType.LBRACK, TokenType.INTERPOL_START):
            depth += 1
        if tok.type in (TokenType.RBRACE, TokenType.RPAREN,
                        TokenType.RBRACK, TokenType.INTERPOL_END):
            depth -= 1
            if depth < 0:
                break  # неожиданный закрывающий — пусть основной парсер разбирается
        self._next()
    
    end_i = self.i
    self.i = saved_i  # отматываем
    sub_tokens = self.tokens[saved_i:end_i]
    from repo_navigator.parsers.nix.parser import _Parser
    sub_parser = _Parser(sub_tokens)
    return sub_parser._parse_expr()
```

Затем в `_parse_formals`:

```python
            if (nxt := self._peek()) is not None and nxt.type == TokenType.EQ:
                self._next()
                default = self._parse_expr_until({TokenType.COMMA, TokenType.RBRACE})
```

**Golden-тест:** создать `tests/golden/parser/formals_default.nix`:

```nix
{ pkgs ? import <nixpkgs> {}, lib ? pkgs.lib, ... }:
pkgs.stdenv.mkDerivation { name = "test"; }
```

Или проще (без path lookup):
```nix
{ config ? {}, lib ? {}, ... }:
config.test
```

## Баг 4: Проверка на реальных модулях

После всех фиксов проверить на:
1. `~/.dotfiles/flake.nix` — должен парситься без ошибок
2. `~/.dotfiles/modules/nixos/default.nix` — должен парситься
3. `~/.dotfiles/modules/home/*.nix` — несколько модулей home-manager

Добавить golden-тест `complex_flake_partial.nix` — вырезанный кусок из реального flake.nix с imports, options, @-pattern, mkIf, formals с default.

---

## Порядок действий

1. **Закрепить баги тестами** — создать golden-файлы (они упадут — это ок, мы потом фиксим)
   - `tests/golden/parser/string_interp_key.nix`
   - `tests/golden/parser/formals_default.nix`
2. **Починить Баг 1** — INTERPOL_START в `_read_attrpath_segment` (5 минут)
3. **Починить Баг 2** — EQ guard в `_parse_attrbindings` (2 минуты)
4. **Починить Баг 3** — `_parse_expr_until` для formals default (10 минут)
5. **Прогнать** `pytest tests/ -v` — должно быть зелёное (старые тесты не сломаны)
6. **Обновить golden-expected** для новых тестов: `pytest tests/golden/ --update-golden`
7. **Проверить на реальных файлах** из `~/.dotfiles`
8. **Финальный прогон** `pytest tests/ -v` — все зелёные

---

## После починки

Следующий шаг — **Фаза 4: Parser Registry + Builder** (`registry.py`, `base.py`, `builder.py`).