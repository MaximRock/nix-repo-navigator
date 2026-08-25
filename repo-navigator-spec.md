# repo-navigator — Спецификация проекта

> Ассистент графа знаний для Nix-репозиториев. Строит инкрементальный мультиуровневый граф с Nix в корне, предоставляет MCP-интерфейс для AI-агентов.

---

## 1. Миссия и принципы

- **Nix-first**: Nix — единственный язык с полным AST-парсингом всегда. Остальные парсятся только если Nix на них ссылается через `home.file`, `xdg.configFile`, `programs.*.extraConfig` и т.д.
- **Инкрементальность**: изменение одного файла перестраивает только его подграф + каскад по импортам (Merkle-tree dirty-flags).
- **Живой граф**: фоновый watcher (inotify + git hooks + flake.lock polling) обновляет граф в реальном времени.
- **Агенто-ориентированность**: интерфейс — навигационные глаголы (`observe`, `hop`, `path`, `blast_radius`), не SQL/Cypher.

---

## 2. Архитектура по слоям

```
┌─────────────────────────────────────────────────────────────┐
│  LAYER 4: MCP Server (интерфейс для агентов)              │
│  observe │ hop │ path │ blast_radius │ find_symbol │       │
│  status  │ refresh                                      │
├─────────────────────────────────────────────────────────────┤
│  LAYER 3: Query Engine (Python)                           │
│  • BFS/DFS с бюджетом (width, depth)                      │
│  • Кратчайший путь (Dijkstra)                             │
│  • Reverse dependencies (blast_radius)                    │
│  • Fuzzy search (FTS5 + trigram)                          │
│  • LRU-кэш частых запросов                                │
├─────────────────────────────────────────────────────────────┤
│  LAYER 2: Graph Storage                                   │
│  • SQLite: узлы, рёбра, file_state, flake_inputs          │
│  • NetworkX: DiGraph в памяти для обходов                 │
│  • FTS5: полнотекстовый поиск по символам                 │
│  • WAL-режим SQLite для конкурентности                    │
├─────────────────────────────────────────────────────────────┤
│  LAYER 1: Incremental Update Engine                       │
│  • Event Router: asyncio Queue + debounce 500ms           │
│  • Hash Engine: content (xxhash) + AST (structural)       │
│  • Diff Engine: AST-diff → add/remove/patch               │
│  • Cascade Engine: Merkle-tree dirty-flags                │
│  • Nix Eval: flake.lock tracker, package index updater    │
├─────────────────────────────────────────────────────────────┤
│  LAYER 0: Parser Registry (плагиновая система)            │
│  Tier 0: Nix (full AST)                                   │
│  Tier 1: Python, Shell, KDL, Lua, TOML, JSON (full AST)  │
│  Tier 2: Haskell, Vimscript, Hyprlang, YAML, CSS (struct)│
│  Tier 3: Markdown, Org, остальные (headings / file only) │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Технологический стек

| Компонент | Библиотека |
|-----------|-----------|
| Граф в памяти | `networkx` |
| Персистентность | `sqlite3` (stdlib) + `fts5` |
| Парсинг (универсальный) | `tree-sitter` + `tree-sitter-languages` |
| Парсинг Nix | `nix-instantiate --parse --json` (subprocess) |
| Парсинг KDL | `kdl-py` |
| Файловый watcher | `watchdog` |
| Хеширование | `xxhash` + `hashlib` |
| MCP | `mcp` (официальный Python SDK от Anthropic) |
| CLI | `typer` |
| Модели | `pydantic` |
| Асинхронность | `asyncio` (stdlib) |
| Git | `gitpython` |

---

## 4. Структура проекта

```
repo-navigator/
├── pyproject.toml
├── README.md
└── src/
    └── repo_navigator/
        ├── __init__.py
        ├── cli.py                    # Typer CLI
        ├── mcp_server.py             # MCP сервер с инструментами
        ├── config.py                 # Pydantic-settings конфиг
        ├── graph/
        │   ├── __init__.py
        │   ├── db.py                 # SQLite persistence + FTS5
        │   ├── nx_graph.py           # NetworkX обёртка
        │   ├── builder.py            # Построение графа из парсеров
        │   └── queries.py            # Query Engine
        ├── parsers/
        │   ├── __init__.py
        │   ├── registry.py           # LanguageConfig + @register_language
        │   ├── base.py               # BaseParser ABC
        │   ├── nix.py                # NixParser (Tier 0)
        │   ├── python.py             # PythonParser (Tier 1)
        │   ├── kdl.py                # KdlParser (Tier 1)
        │   ├── shell.py              # ShellParser (Tier 1)
        │   ├── lua.py                # LuaParser (Tier 1)
        │   ├── toml.py               # TomlParser (Tier 1)
        │   ├── json.py               # JsonParser (Tier 1)
        │   ├── hyprlang.py           # HyprlangParser (Tier 2)
        │   ├── haskell.py            # HaskellParser (Tier 2)
        │   ├── vimscript.py          # VimscriptParser (Tier 2)
        │   ├── yaml.py               # YamlParser (Tier 2)
        │   ├── css.py                # CssParser (Tier 2)
        │   └── markdown.py           # MarkdownParser (Tier 3)
        ├── models/
        │   ├── __init__.py
        │   ├── nodes.py              # Pydantic: Node, NodeType
        │   ├── edges.py              # Pydantic: Edge, EdgeType
        │   └── queries.py            # Pydantic: Observation, Subgraph, PathStep
        ├── indexer/
        │   ├── __init__.py
        │   ├── event_router.py       # asyncio Queue + debounce
        │   ├── update_engine.py      # Инкрементальный движок
        │   ├── hash_engine.py        # content_hash + ast_hash
        │   ├── diff_engine.py        # AST-diff
        │   └── cascade.py            # Merkle-tree dirty-flags
        ├── nix/
        │   ├── __init__.py
        │   ├── eval.py               # nix eval / nix-instantiate обёртки
        │   ├── flake_tracker.py      # flake.lock watcher
        │   └── package_index.py      # Таблица package_index
        └── watcher/
            ├── __init__.py
            ├── filesystem.py         # watchdog observer
            └── git_hooks.py          # Установка post-checkout/post-merge
```

---

## 5. Модель данных

### 5.1. Узлы (Node)

```python
class Node(BaseModel):
    id: str                    # "nix:modules/desktop.nix"
    type: NodeType             # nix_module | nix_option | py_function | kdl_bind | ...
    name: str                  # Человекочитаемое имя
    path: Path | None
    lang: str                  # "nix" | "python" | "kdl" | ...
    metadata: dict             # Языко-специфичные поля
    content_hash: str | None   # xxhash
    ast_hash: str | None       # Структурный хеш AST
    created_at: datetime
    updated_at: datetime
```

**Языко-специфичные типы узлов:**

| Язык | Типы узлов | Пример ID |
|------|-----------|-----------|
| **Nix** | `nix_module`, `nix_option`, `nix_function`, `flake_input`, `package_ref` | `nix_option:programs.qtile.extraConfig` |
| **Python** | `py_function`, `py_class`, `py_import`, `qtile_key`, `qtile_hook` | `py_function:qtile/keys.py:setup_keys` |
| **KDL** | `kdl_bind`, `kdl_rule`, `kdl_spawn` | `kdl_bind:niri/config.kdl:Mod+Return` |
| **Shell** | `sh_function`, `sh_command_call` | `sh_function:scripts/lock.sh:lock_screen` |
| **Lua** | `lua_function`, `lua_require`, `vim_keymap` | `lua_require:nvim/init.lua:plugins.lsp` |
| **TOML** | `toml_section`, `toml_key` | `toml_key:alacritty.toml:font.normal` |
| **JSON** | `json_key` | `json_key:waybar/config.json:modules-left` |
| **File** | `file` (fallback Tier 3) | `file:README.md` |

### 5.2. Рёбра (Edge)

```python
class Edge(BaseModel):
    id: str
    source: str                # ID узла-источника
    target: str                # ID узла-назначения
    type: EdgeType
    metadata: dict             # Строка в файле, provenance
    weight: float = 1.0
```

**Универсальные типы рёбер:**

| Тип | От | До | Пример |
|-----|----|----|--------|
| `imports` | `nix_module` | `nix_module` | `imports = [ ./base.nix ]` |
| `defines` | `nix_module` | `nix_option` | `options.foo = mkOption ...` |
| `configures` | `nix_option` | `file` | `home.file."...".source = ...` |
| `generates` | `nix_module` | `file` | `pkgs.writeText "config.py" ...` |
| `uses_package` | `nix_module` | `package_ref` | `home.packages = [ pkgs.ripgrep ]` |
| `python_imports` | `py_file` | `py_file` | `from lib.keys import *` |
| `calls` | `py_function` | `py_function` | `lazy.spawn("...")` |
| `binds_key` | `qtile_key` / `kdl_bind` | `command` | `Key(mod, "r", lazy.spawn("rofi"))` |
| `spawns` | `kdl_bind` / `sh_function` | `command` | `spawn "alacritty"` |
| `requires` | `lua_file` | `lua_function` | `require("plugins.lsp")` |
| `sources` | `sh_script` | `sh_script` | `source ./lib/utils.sh` |
| `references` | `toml_key` | `package_ref` | `font = "JetBrainsMono Nerd Font"` |

### 5.3. SQLite Схема

```sql
-- Узлы
CREATE TABLE nodes (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    path TEXT,
    lang TEXT NOT NULL,
    metadata JSON,
    content_hash TEXT,
    ast_hash TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Рёбра
CREATE TABLE edges (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL REFERENCES nodes(id),
    target TEXT NOT NULL REFERENCES nodes(id),
    type TEXT NOT NULL,
    metadata JSON,
    weight REAL DEFAULT 1.0
);

-- Состояние файлов (инкрементальность)
CREATE TABLE file_state (
    path TEXT PRIMARY KEY,
    lang TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    ast_hash TEXT,
    merkle_hash TEXT,
    dirty BOOLEAN DEFAULT 0,
    last_parsed TIMESTAMP,
    detail_level TEXT
);

-- Flake inputs
CREATE TABLE flake_inputs (
    name TEXT PRIMARY KEY,
    url TEXT,
    rev TEXT,
    last_modified TIMESTAMP,
    updated_at TIMESTAMP
);

-- Пакетный индекс
CREATE TABLE package_index (
    attribute TEXT PRIMARY KEY,
    name TEXT,
    version TEXT,
    store_path TEXT,
    meta JSON,
    used_by JSON,
    first_seen TIMESTAMP,
    last_updated TIMESTAMP
);

-- FTS5
CREATE VIRTUAL TABLE node_search USING fts5(
    id, name, type, lang, content=nodes
);
```

---

## 6. Компоненты (детально)

### 6.1. Parser Registry (плагиновая система)

Каждый язык — плагин. Регистрация через декоратор `@register_language`.

**Правило условного парсинга (Nix-first):**
- Tier 0 (Nix): парсим всегда, полный AST.
- Tier 1–2: парсим полный/структурный AST только если:
  1. Nix явно ссылается через `generates` / `configures` / `home.file`.
  2. Файл лежит внутри `.config/**` (эвристика для home-manager).
- Tier 3: только файловый узел, без AST.

### 6.2. Nix Parser (Tier 0)

Два режима:
1. **Статический**: `nix-instantiate --parse --json file.nix` → обход JSON-AST.
2. **Динамический**: `nix eval --json '.#homeConfigurations.user.config.home.packages'` → список пакетов и сгенерированных файлов.

Извлекаем: `imports`, `options.*`, `config.*`, `home.file`, `xdg.configFile`, `pkgs.writeText`, `mkIf`, `mkMerge`, `home.packages`, `programs.*.package`.

### 6.3. Event Router

- `watchdog.observers.Observer` на корень репозитория.
- Дебаунсинг 500ms: batch изменений → одна транзакция.
- Git hooks: `post-checkout`, `post-merge` → bulk-обновление diff-файлов.
- Flake lock tracker: отдельный поток, проверка `flake.lock` каждые 30s.

### 6.4. Incremental Update Engine

**Алгоритм:**
1. Считаем `content_hash` (xxhash).
2. Если совпадает с `file_state` → игнорируем.
3. Парсим → `ast_hash` (структурный, без комментариев).
4. Если `ast_hash` совпадает → обновляем только `content_hash`.
5. Иначе: `diff_engine` вычисляет `add/remove/patch` → применяем к графу.
6. Для Nix: `cascade` → помечаем `dirty=1` всех импортёров (Merkle-tree).

### 6.5. Query Engine (навигационные глаголы)

| Операция | Описание | Бюджет |
|----------|----------|--------|
| `observe(node_id, depth=1)` | Соседи, типы рёбер, сводка | 20 соседей |
| `hop(node_id, relation, depth, width)` | BFS-обход с фильтром | width×depth ≤ 100 |
| `path(source, target)` | Кратчайший путь (Dijkstra) | — |
| `blast_radius(node_id, max_depth)` | Reverse BFS (обратные зависимости) | max_depth=5 |
| `find_symbol(query, lang, fuzzy, limit)` | FTS5 + trigram | limit=10 |
| `summarize_module(path)` | Входящие/исходящие, ключевые символы | — |
| `status()` | Статус ассистента | — |
| `refresh()` | Принудительный полный rescan | — |

### 6.6. MCP Server

Инструменты:
- `repo_navigator_observe`
- `repo_navigator_hop`
- `repo_navigator_path`
- `repo_navigator_blast_radius`
- `repo_navigator_find_symbol`
- `repo_navigator_summarize_module`
- `repo_navigator_status`
- `repo_navigator_refresh`

Транспорт: `stdio` (JSON-RPC over stdio).

---

## 7. Поток данных (полный цикл)

```
1. Пользователь меняет modules/qtile.nix
           │
           ▼
2. inotify → FileSystemEvent(MODIFY)
           │
           ▼
3. EventRouter.debounce(500ms) → batch → queue
           │
           ▼
4. UpdateEngine.process_file("modules/qtile.nix")
   ├── 4a. xxhash(content) != old → парсим
   ├── 4b. nix-instantiate --parse --json
   ├── 4c. Извлекаем: imports, options, home.file
   ├── 4d. Удаляем старые узлы/рёбра файла из графа
   ├── 4e. Добавляем новые
   ├── 4f. Обновляем file_state (content_hash, ast_hash, dirty=0)
   └── 4g. Cascade: помечаем dirty всех импортёров
           │
           ▼
5. Агент вызывает MCP: observe("nix:modules/qtile.nix")
   ├── 5a. QueryEngine читает NetworkX + SQLite
   └── 5b. Возвращает соседей, рёбра, сводку
```

---

## 8. Дорожная карта

| Неделя | Этап | Результат |
|--------|------|-----------|
| 1 | Фундамент | Структура, SQLite, Pydantic, CLI |
| 2 | Nix Parser | Парсинг `flake.nix`, модулей, импортов, опций |
| 3 | Event System | `watchdog`, `asyncio` Queue, debounce, `file_state` |
| 4 | Инкрементальность | AST-hash, diff-engine, каскад dirty-flags |
| 5 | Python + KDL | `tree-sitter-python`, `kdl-py`, keybindings, `spawns` |
| 6 | Query Engine | `observe`, `hop`, `path`, `blast_radius`, `find_symbol` |
| 7 | MCP Server | 8 инструментов, `stdio` транспорт |
| 8 | Git + Flake | Hooks, `flake.lock` watcher, package index |
| 9 | Tier 2 языки | Lua, TOML, JSON, Shell, Hyprlang, Haskell, Vimscript |
| 10 | Оптимизация | LRU-кэш, WAL, бенчмарки |
| 11 | Тестирование | Интеграционные тесты на реальном dotfiles-репо |
| 12 | Документация | README, примеры, релиз |

**MVP (пригодный для агентов):** конец недели 7.

---

## 9. Критерии приёмки (Definition of Done)

- [ ] `nix-instantiate --parse --json` корректно извлекает `imports`, `options`, `home.file`, `home.packages` из `flake.nix` и всех модулей.
- [ ] Изменение `modules/qtile.nix` триггерит обновление графа за < 1 секунду.
- [ ] `blast_radius("nix:modules/qtile.nix")` возвращает все файлы, которые импортируют этот модуль (прямо или косвенно).
- [ ] `path("kdl_bind:niri/config.kdl:Mod+Return", "nix:modules/niri.nix")` возвращает цепочку от keybinding до Nix-опции.
- [ ] MCP-сервер запускается через `python -m repo_navigator.mcp_server` и отвечает на все 8 инструментов.
- [ ] `git checkout other-branch` → `post-checkout` hook → граф обновляется без ручного `refresh`.
- [ ] `nix flake update` → изменение `flake.lock` → package index обновляется.
- [ ] Агент может задать вопрос «Где настроен Mod+Return?» и получить ответ за 3 MCP-вызова.

---

## 10. Риски и mitigations

| Риск | Mitigation |
|------|------------|
| Nix динамичен, статический парсинг не видит `${}` | Для `home.file` используем `nix eval`. Динамические импорты → `unresolved`. |
| Граф >100k узлов, SQLite тормозит | WAL + индексы. Fallback RocksDB для `file_state`. |
| tree-sitter-nix нестабилен | Primary: `nix-instantiate --parse --json`. tree-sitter — fallback. |
| WSL1/NFS/Docker — inotify не работает | Fallback: `mtime` polling каждые 60s. |
| Агент засыпает данными | Жёсткий бюджет: `width`×`depth` ≤ 100 узлов. |

---

*Версия: 1.0*  
*Формат: Спецификация для AI-агента*  
*Язык: ru / en*
