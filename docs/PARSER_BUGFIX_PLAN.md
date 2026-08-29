# План починки парсера — 2 оставшихся файла

## Баг 1: `home.nix` — файл из комментов, нет кода

**Файл:** `src/repo_navigator/parsers/nix_parser.py`
**Метод:** `NixParser.parse()`

**Симптом:** `unexpected token EOF` — файл полностью закомментирован (49 строк `# ...`), лексер выдаёт только EOF, парсер падает.

**Фикс:** В `NixParser.parse()` добавить проверку в начале: протокенизировать контент, если токенов ≤ 1 (только EOF) — вернуть пустой `ParseResult(nodes=[], edges=[])` без вызова `_parse_inner()`.

**Код:**

```python
def parse(self, path: Path, content: str) -> ParseResult:
    from repo_navigator.parsers.nix.lexer import tokenize

    tokens = tokenize(content)
    if len(tokens) <= 1:  # только EOF
        return ParseResult(nodes=[], edges=[])
    try:
        return self._parse_inner(path, content)
    except Exception:
        log.exception("NixParser failed for %s", path)
        return self._fallback(path)
```

---

## Баг 2: `11/vscode.nix` — пустая строка `""` теряется

**Файл:** `src/repo_navigator/parsers/nix/lexer.py`
**Метод:** `_flush_string()`

**Симптом:** `unexpected token SEMI` в строке `"aiModel" = "";` — токенов после `=` сразу SEMI, потому что `""` (пустая строка) не эмитит токен.

**Причина:** `_flush_string()` проверяет `if self._buf:` и не эмитит токен если buf пуст. Для `""` buf пуст → токен не создаётся → парсер видит `"aiModel" = ;` → падает.

**Фикс:** Убрать проверку `if self._buf:` — всегда эмитить токен, даже для пустой строки.

**Изменение в `_flush_string()`:**

```python
def _flush_string(self) -> None:
    ttype = (
        TokenType.STRING_HEREDOC
        if self.mode == "hstr"
        else TokenType.STRING_DOUBLE
    )
    self._emit(ttype, self._buf, self._str_start_line, self._str_start_col)
    self._buf = ""
```

**Побочный эффект:** Для `"${var}"` теперь будут эмититься пустые STRING_DOUBLE("") до и после интерполяции. Парсер (`_parse_stringish`) уже обрабатывает это правильно — он собирает любые STRING_DOUBLE/INTERPOL_SEQUENCE подряд.

**Проверка после фикса:**
```bash
# 1. Тесты
.venv/bin/python -m pytest tests/ -v | tail -10

# 2. Проверка на 2 файлах
.venv/bin/python -c "
from repo_navigator.parsers.nix.parser import parse
from pathlib import Path
for f in ['/home/max/.dotfiles/home.nix', '/home/max/.dotfiles/11/vscode.nix']:
    try:
        parse(Path(f).read_text())
        print(f'{f}: OK')
    except Exception as e:
        print(f'{f}: FAIL — {e}')
"

# 3. Полная индексация — должно быть 141/141 файлов
.venv/bin/python -m repo_navigator.cli refresh --root ~/.dotfiles --db-path ./repo.db | tail -3
```