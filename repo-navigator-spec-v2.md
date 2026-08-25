# repo-navigator v2 — Спецификация проекта

> Ассистент графа знаний для **NixOS и home-manager** конфигураций. Строит инкрементальный мультиуровневый граф с Nix в корне, предоставляет MCP-интерфейс для AI-агентов: навигация, безопасные правки, introspection опций.

---

## 0. Изменения относительно v1

| Область | v1 | v2 |
|---------|----|----|
| Scope | Личный dotfiles-репозиторий | Универсальный ассистент: NixOS + home-manager |
| Introspection | Неявная (только `nix eval` списков) | Гибридная: статический граф + ленивый `nix eval` с кэшем |
| Storage | SQLite + NetworkX параллельно | SQLite — единственный источник истины; NetworkX — производный in-memory вид |
| Cross-language (KDL/Lua/qtile…) | Ядро | Опциональные плагины под home-manager |
| MCP-инструменты | 8 | 11 (+`introspect_option`, `eval_expression`, `impact_analysis`) |
| Модульная система NixOS | Частично (`options.*`, `imports`) | Полноценно: `mkOption`-метаданные, `config.*`, `specialisation`, `_module.args` |

---

## 1. Миссия и принципы

- **NixOS + home-manager — центр.** Ассистент понимает модульную систему NixOS: объявления опций (`mkOption`), присваивания в `config`, цепочки `imports`, `specialisation`, передачу аргументов через `_module.args`. Home-manager — первый класс: `home.file`, `xdg.configFile`, `programs.*`, `home.packages`.
- **Nix-first**: Nix — единственный язык с полным AST-парсингом всегда. Остальные языки парсятся только если Nix на них ссылается (`home.file`, `xdg.configFile`, `programs.*.extraConfig`) **и** соответствующий плагин включён в конфиге.
- **Гибридная introspection**: статический граф работает офлайн и всегда отвечает на «что объявлено / где задаётся / кто импортирует». Вычисленные значения опций запрашиваются ленивым `nix eval` по явному запросу агента, кэшируются и инвалидируются по `flake.lock` / изменению `.nix` файлов.
- **Единый источник истины**: персистентность только в SQLite. NetworkX-граф — производный in-memory вид, строго синхронизируемый слоем `db.py` (никаких параллельных записей).
- **Инкрементальность**: изменение одного файла перестраивает только его подграф + каскад по импортам (Merkle-tree dirty-flags).
- **Живой граф**: фоновый watcher (inotify + git hooks + flake.lock polling) обновляет граф в реальном времени.
- **Агенто-ориентированность**: интерфейс — навигационные глаголы (`observe`, `hop`, `path`, `blast_radius`), не SQL/Cypher.
- **Деградация без nix CLI**: если `nix eval` недоступен/медленен — статический режим продолжает работать; динамические ответы помечаются `unresolved`.

---

## 2. Архитектура по слоям

```
┌──────────────────────────────────────────────────────────────────┐
│  LAYER 4: MCP Server (интерфейс для агентов)                    │
│  observe │ hop │ path │ blast_radius │ find_symbol │             │
│  summarize_module │ introspect_option │ eval_expression │        │
│  impact_analysis │ status │ refresh                             │
├──────────────────────────────────────────────────────────────────┤
│  LAYER 3: Query Engine (Python)                                  │
│  • BFS/DFS с бюджетом (width, depth)                            │
│  • Кратчайший путь (Dijkstra)                                   │
│  • Reverse dependencies (blast_radius / impact_analysis)         │
│  • Fuzzy search (FTS5 + trigram)                                │
│  • LRU-кэш частых запросов                                      │
├──────────────────────────────────────────────────────────────────┤
│  LAYER 2: Graph Storage                                          │
│  • SQLite — ЕДИНСТВЕННЫЙ источник истины: nodes, edges,          │
│    file_state, flake_inputs, package_index, option_values       │
│  • NetworkX DiGraph — производный in-memory вид (rebuild/delta   │
│    из db.py; прямая запись запрещена)                           │
│  • FTS5: полнотекстовый поиск по символам                       │
│  • WAL-режим SQLite для конкурентности                          │
├──────────────────────────────────────────────────────────────────┤
│  LAYER 1: Incremental Update Engine                              │
│  • Event Router: asyncio Queue + debounce 500ms                 │
│  • Hash Engine: content (xxhash) + AST (structural)             │
│  • Diff Engine: AST-diff → add/remove/patch                     │
│  • Cascade Engine: Merkle-tree dirty-flags                      │
│  • Eval Cache Invalidation: flake.lock rev + content hash       │
├──────────────────────────────────────────────────────────────────┤
│  LAYER 0: Parser Registry (плагиновая система)                   │
│  Tier 0: Nix (full AST, всегда) — NixOS + home-manager семантика│
│  Tier 1 (плагины): Python, Shell, Lua, TOML, JSON, KDL          │
│  Tier 2 (плагины): Haskell, Vimscript, Hyprlang, YAML, CSS      │
│  Tier 3: Markdown, Org, остальные (headings / file only)        │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Технологический стек

| Компонент | Библиотека |
|-----------|-----------|
| Граф in-memory (производный вид) | `networkx` |
| Персистентность (источник истины) | `sqlite3` (stdlib) + `fts5` |
| Парсинг (плагины Tier 1–2) | `tree-sitter` + `tree-sitter-languages` |
| Парсинг Nix | `nix-instantiate --parse --json` (subprocess); fallback: `tree-sitter-nix` |
| Динамическая introspection | `nix eval --json` (subprocess, lazy, timeout) |
| Парсинг KDL (плагин) | `kdl-py` |
| Файловый watcher | `watchdog` |
| Хеширование | `xxhash` + `hashlib` |
| MCP | `mcp` (официальный Python SDK от Anthropic) |
| CLI | `typer` |
| Модели | `pydantic` |
| Асинхронность | `asyncio` (stdlib) |
| Git | `gitpython` |

**Требование среды:** полный функционал (динамическая introspection) требует `nix` в PATH. Без него ассистент работает в статическом режиме.

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
        ├── mcp_server.py             # MCP сервер: 11 инструментов
        ├── config.py                 # Pydantic-settings: root, plugins, budgets, timeouts
        ├── graph/
        │   ├── __init__.py
        │   ├── db.py                 # SQLite: единственная запись/чтение + уведомления nx_graph
        │   ├── nx_graph.py           # Производный NetworkX вид (rebuild/delta)
        │   ├── builder.py            # Построение графа из вывода парсеров
        │   └── queries.py            # Query Engine (навигационные глаголы)
        ├── parsers/
        │   ├── __init__.py
        │   ├── registry.py           # LanguageConfig + @register_language
        │   ├── base.py               # BaseParser ABC
        │   ├── nix.py                # Tier 0: вызов ast_extract/module_parser
        │   └── plugins/              # Tier 1–3, включаются в config
        │       ├── __init__.py
        │       ├── python.py         # qtile_key, py_function…
        │       ├── kdl.py            # kdl_bind, kdl_spawn…
        │       ├── shell.py
        │       ├── lua.py
        │       ├── toml.py
        │       ├── json.py
        │       ├── hyprlang.py
        │       ├── haskell.py
        │       ├── vimscript.py
        │       ├── yaml.py
        │       ├── css.py
        │       └── markdown.py
        ├── models/
        │   ├── __init__.py
        │   ├── nodes.py              # Pydantic: Node, NodeType
        │   ├── edges.py              # Pydantic: Edge, EdgeType
        │   └── queries.py            # Pydantic: Observation, Subgraph, PathStep, OptionInfo, ImpactReport
        ├── indexer/
        │   ├── __init__.py
        │   ├── event_router.py       # asyncio Queue + debounce 500ms
        │   ├── update_engine.py      # Инкрементальный движок
        │   ├── hash_engine.py        # content_hash + ast_hash
        │   ├── diff_engine.py        # AST-diff
        │   └── cascade.py            # Merkle-tree dirty-flags
        ├── nix/
        │   ├── __init__.py
        │   ├── ast_extract.py        # Обход JSON-AST: attrpaths, mkIf/mkMerge, //
        │   ├── module_parser.py      # Семантика NixOS/HM: options/config/imports/specialisation/_module.args
        │   ├── eval.py               # Ленивые обёртки nix eval + таймауты
        │   ├── eval_cache.py         # Таблица option_values, инвалидация
        │   ├── flake_tracker.py      # flake.lock watcher → инвалидация кэша
        │   └── package_index.py      # Таблица package_index
        └── watcher/
            ├── __init__.py
            ├── filesystem.py         # watchdog observer + mtime polling fallback
            └── git_hooks.py          # post-checkout/post-merge
```

---

## 5. Модель данных

### 5.1. Узлы (Node)

```python
class Node(BaseModel):
    id: str                    # "nix_option:services.nginx.enable"
    type: NodeType
    name: str                  # Человекочитаемое имя
    path: Path | None
    lang: str                  # "nix" | "python" | "kdl" | ...
    metadata: dict             # Языко-специфичные поля
    content_hash: str | None   # xxhash
    ast_hash: str | None       # Структурный хеш AST
    created_at: datetime
    updated_at: datetime
```

**Типы узлов Nix (Tier 0, ядро):**

| Тип узла | Описание | Пример ID |
|----------|----------|-----------|
| `nix_module` | Файл модуля (NixOS/HM/flake.nix) | `nix:modules/services/nginx.nix` |
| `nix_option` | Декларация опции (`mkOption`) | `nix_option:services.nginx.enable` |
| `nix_function` | Свободная функция/lambda | `nix_function:lib/mk-service.nix:mkService` |
| `flake_input` | Вход flake | `flake_input:nixpkgs` |
| `package_ref` | Ссылка на пакет | `package_ref:pkgs.ripgrep` |

**Метаданные `nix_option`** (заполняются статически):

```json
{
  "opt_type": "types.bool",
  "default": "false",
  "example": "true",
  "description": "Whether to enable nginx.",
  "declared_in": "modules/services/nginx.nix",
  "defined_in": ["hosts/desktop.nix", "profiles/web.nix"],
  "has_value_cached": false
}
```

**Типы узлов плагинов (Tier 1–3, опционально):**

| Язык | Типы узлов | Пример ID |
|------|-----------|-----------|
| **Python** | `py_function`, `py_class`, `qtile_key`, `qtile_hook` | `py_function:qtile/keys.py:setup_keys` |
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
    metadata: dict             # строка в файле, provenance, приоритет mkForce/mkDefault
    weight: float = 1.0
```

**Универсальные типы рёбер (ядро NixOS/HM):**

| Тип | От | До | Пример |
|-----|----|----|--------|
| `imports` | `nix_module` | `nix_module` | `imports = [ ./base.nix ]` |
| `declares` | `nix_module` | `nix_option` | `options.services.nginx.enable = mkOption ...` |
| `sets` | `nix_module` | `nix_option` | `config.services.nginx.enable = true` |
| `specialises` | `nix_module` | `nix_module` | `specialisation.desktop.configuration = { ... }` |
| `passes_args` | `nix_module` | `nix_module` | `_module.args.myLib = ...` |
| `configures` | `nix_option` | `file` | `home.file."...".source = ...` |
| `generates` | `nix_module` | `file` | `pkgs.writeText "config.py" ...` |
| `uses_package` | `nix_module` | `package_ref` | `home.packages = [ pkgs.ripgrep ]` |

**Типы рёбер плагинов:**

| Тип | От | До | Пример |
|-----|----|----|--------|
| `python_imports` | `py_file` | `py_file` | `from lib.keys import *` |
| `calls` | `py_function` | `py_function` | `lazy.spawn("...")` |
| `binds_key` | `qtile_key` / `kdl_bind` | command | `Key(mod, "r", lazy.spawn("rofi"))` |
| `spawns` | `kdl_bind` / `sh_function` | command | `spawn "alacritty"` |
| `requires` | `lua_file` | `lua_function` | `require("plugins.lsp")` |
| `sources` | `sh_script` | `sh_script` | `source ./lib/utils.sh` |
| `references` | `toml_key` | `package_ref` | `font = "JetBrainsMono Nerd Font"` |

### 5.3. SQLite Схема (единственный источник истины)

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
CREATE INDEX idx_edges_source ON edges(source);
CREATE INDEX idx_edges_target ON edges(target);
CREATE INDEX idx_edges_type   ON edges(type);

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

-- Кэш динамической introspection (гибрид)
CREATE TABLE option_values (
    key TEXT PRIMARY KEY,          -- hash(expr) + flake_rev + relevant_files_hash
    expr TEXT NOT NULL,            -- 'config.services.nginx.enable' | произвольное выражение
    value_json JSON,
    status TEXT NOT NULL,          -- ok | unresolved | error | stale
    error TEXT,
    computed_at TIMESTAMP,
    source_rev TEXT                -- rev flake.lock на момент вычисления
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
- Tier 0 (Nix): парсим всегда, полный AST, включая семантику модульной системы.
- Tier 1–2 (плагины, вкл/выкл в `config.py`): парсим только если:
  1. Nix явно ссылается на файл через `generates` / `configures` / `home.file` / `xdg.configFile`;
  2. ИЛИ файл лежит внутри `.config/**` (эвристика для home-manager).
- Tier 3: только файловый узел, без AST.

Плагины — строго опциональны: без них ядро (NixOS + HM) полностью функционально.

### 6.2. Nix-подсистема (Tier 0)

**6.2.1. Статический режим (всегда):**
`nix-instantiate --parse --json file.nix` → обход JSON-AST.

Извлечение (`ast_extract.py` + `module_parser.py`):
- `attrpath`-резолюция вложенных set'ов (`a.b.c = x` ≡ `a = { b = { c = x; }; };`);
- слияния: `//`, `mkMerge`, приоритеты `mkIf` / `mkForce` / `mkDefault` (сохраняются в `edge.metadata.priority`);
- `imports = [...]` (включая условные через `mkIf` — помечаем `conditional=true`);
- декларации `options.*` с метаданными `mkOption` (type/default/example/description);
- присваивания `config.*` → ребро `sets`;
- `specialisation.<name>.configuration`;
- `_module.args.<name>`;
- `home.file`, `xdg.configFile`, `programs.*`, `home.packages` (HM).

Динамические значения (`${...}` внутри путей/строк) → помечаются `unresolved`, не блокируют остальное.

**6.2.2. Динамический режим (лениво, по запросу агента):**
`nix eval --json <expr>` — вычисленное значение опции или произвольного выражения.
- Таймаут по умолчанию 60s (конфигурируемо); превышение → `status=unresolved`.
- Результат кэшируется в `option_values`; ключ = hash(expr) + rev flake.lock + hash затронутых `.nix`.
- Инвалидация: изменение `flake.lock` → все записи `stale`; изменение `.nix` → записи, чей expr затронут каскадом, `stale`.

### 6.3. Event Router

- `watchdog.observers.Observer` на корень репозитория; fallback `mtime` polling 60s (WSL/NFS/Docker).
- Дебаунсинг 500ms: batch изменений → одна транзакция.
- Git hooks: `post-checkout`, `post-merge` → bulk-обновление diff-файлов.
- Flake lock tracker: отдельный поток, проверка `flake.lock` каждые 30s → инвалидация eval-кэша.

### 6.4. Incremental Update Engine

**Алгоритм:**
1. Считаем `content_hash` (xxhash).
2. Если совпадает с `file_state` → игнорируем.
3. Парсим → `ast_hash` (структурный, без комментариев).
4. Если `ast_hash` совпадает → обновляем только `content_hash`.
5. Иначе: перестраиваем весь подграф файла (v2: отказ от точечного AST-diff как источника багов; diff_engine оставлен для отчёта «что изменилось», не для мутации).
6. Для Nix: `cascade` → помечаем `dirty=1` всех импортёров (Merkle-tree) + инвалидируем eval-кэш затронутых выражений.

### 6.5. Query Engine (навигационные глаголы)

| Операция | Описание | Бюджет |
|----------|----------|--------|
| `observe(node_id, depth=1)` | Соседи, типы рёбер, сводка | 20 соседей |
| `hop(node_id, relation, depth, width)` | BFS-обход с фильтром | width×depth ≤ 100 |
| `path(source, target)` | Кратчайший путь (Dijkstra) | — |
| `blast_radius(node_id, max_depth)` | Reverse BFS (обратные зависимости) | max_depth=5 |
| `find_symbol(query, lang, fuzzy, limit)` | FTS5 + trigram | limit=10 |
| `summarize_module(path)` | Входящие/исходящие, ключевые символы | — |
| `introspect_option(option_path, include_value=False)` | Статическая декларация (type/default/example/description/declared_in/defined_in) + опционально ленивое значение из eval-кэша/`nix eval` | — |
| `eval_expression(expr, timeout)` | Произвольный ленивый `nix eval` с кэшем | timeout ≤ 120s |
| `impact_analysis(node_id, max_depth)` | Расширенный blast_radius для безопасных правок: затронутые модули, опции (`sets`), генерируемые файлы (`configures`/`generates`), оценка риска | max_depth=5 |
| `status()` | Статус ассистента (режим static/hybrid, размер графа, свежесть кэша) | — |
| `refresh()` | Принудительный полный rescan | — |

### 6.6. MCP Server

Инструменты:
- `repo_navigator_observe`
- `repo_navigator_hop`
- `repo_navigator_path`
- `repo_navigator_blast_radius`
- `repo_navigator_find_symbol`
- `repo_navigator_summarize_module`
- `repo_navigator_introspect_option`
- `repo_navigator_eval_expression`
- `repo_navigator_impact_analysis`
- `repo_navigator_status`
- `repo_navigator_refresh`

Транспорт: `stdio` (JSON-RPC over stdio). Запуск: `python -m repo_navigator.mcp_server`.

---

## 7. Потоки данных

### 7.1. Полный цикл (изменение файла)

```
1. Пользователь меняет modules/services/nginx.nix
           │
           ▼
2. inotify → FileSystemEvent(MODIFY)
           │
           ▼
3. EventRouter.debounce(500ms) → batch → queue
           │
           ▼
4. UpdateEngine.process_file("modules/services/nginx.nix")
   ├── 4a. xxhash(content) != old → парсим
   ├── 4b. nix-instantiate --parse --json
   ├── 4c. module_parser: imports, options.*, config.*, specialisation
   ├── 4d. Удаляем старые узлы/рёбра файла из SQLite → delta в NetworkX
   ├── 4e. Добавляем новые
   ├── 4f. Обновляем file_state (content_hash, ast_hash, dirty=0)
   ├── 4g. Cascade: dirty всех импортёров (Merkle-tree)
   └── 4h. Инвалидация option_values затронутых выражений → status=stale
           │
           ▼
5. Агент вызывает MCP: observe("nix:modules/services/nginx.nix")
   ├── 5a. QueryEngine читает SQLite (+ NetworkX для обходов)
   └── 5b. Возвращает соседей, рёбра, сводку
```

### 7.2. Вопрос агента (типовой сценарий introspection)

```
Агент: «Что делает опция services.nginx.enable и где она задаётся?»
  1. introspect_option("services.nginx.enable")
     → статически: type=bool, default=false, declared_in=..., defined_in=[...]
  2. (опционально) include_value=True
     → eval_cache hit? значение : ленивый nix eval (timeout, кэширование)
  3. (опционально) impact_analysis → кто ещё sets эту опцию / что сломается при смене
```

---

## 8. Дорожная карта

| Неделя | Этап | Результат |
|--------|------|-----------|
| 1–2 | Фундамент | Структура, SQLite (источник истины), Pydantic-модели, NetworkX производный вид, CLI |
| 3 | NixOS module parser | `ast_extract` + `module_parser`: attrpaths, mkIf/mkMerge, imports, options (mkOption-метаданные), config, specialisation, _module.args |
| 4 | Инкрементальность | watchdog, asyncio Queue, debounce, file_state, cascade dirty-flags |
| 5 | Query Engine | observe, hop, path, blast_radius, find_symbol, summarize_module |
| 6 | MCP Server | 11 инструментов, stdio транспорт |
| 7 | Гибридная introspection | eval.py + eval_cache + flake_tracker; introspect_option, eval_expression; graceful degradation |
| 8 | Home-manager слой | home.file / xdg.configFile / programs.* → переходы в сгенерированные dotfiles |
| 9 | impact_analysis | Затронутые опции/файлы/модули, оценка риска правки |
| 10 | Git + пакеты | Hooks, package index |
| 11 | Тестирование | Интеграционные тесты на реальном NixOS + home-manager репозитории; golden-тесты извлечения config.* |
| 12 | Оптимизация и релиз | WAL, индексы, LRU-кэш, бенчмарки, документация |

**MVP (навигация + introspection, пригодный для агентов):** конец недели 7.
**Плагины Tier 1–2 (KDL/Lua/qtile…):** после MVP, по потребности.

---

## 9. Критерии приёмки (Definition of Done)

- [ ] Статическое извлечение корректно находит `imports`, `options.*` (с метаданными mkOption), `config.*`, `specialisation`, `_module.args`, `home.file`, `home.packages` в реальном NixOS+HM репозитории.
- [ ] Изменение `modules/services/nginx.nix` триггерит обновление графа за < 1 секунду.
- [ ] `blast_radius("nix:modules/services/nginx.nix")` возвращает всех импортёров (прямо и косвенно).
- [ ] `introspect_option("services.nginx.enable")` офлайн возвращает декларацию; `include_value=True` лениво достаёт значение и кэширует его.
- [ ] Изменение любого `.nix` → соответствующие записи `option_values` становятся `stale`; `nix flake update` → все записи инвалидируются.
- [ ] Без `nix` в PATH ассистент работает в статическом режиме; динамические запросы отвечают `unresolved`, не падают.
- [ ] `impact_analysis` перед правкой перечисляет затронутые модули, опции (`sets`) и генерируемые файлы.
- [ ] MCP-сервер запускается через `python -m repo_navigator.mcp_server` и отвечает на все 11 инструментов.
- [ ] `git checkout other-branch` → `post-checkout` hook → граф обновляется без ручного `refresh`.
- [ ] Агент отвечает на вопрос «Где настроена опция X?» за ≤ 3 MCP-вызовов.

---

## 10. Риски и mitigations

| Риск | Mitigation |
|------|------------|
| `nix-instantiate --parse` — legacy-утилита, может отсутствовать | Проверка наличия при старте; fallback: `tree-sitter-nix` |
| Извлечение `config.*` из AST сложна (mkIf/mkMerge, `//`, приоритеты) | Отдельный модуль `module_parser.py` + golden-тесты на реальных модулях NixOS/nixpkgs |
| `nix eval` медленный, требует сборки/сети | Только по явному запросу агента, таймаут 60s, кэш, статус `unresolved`, граф никогда не блокируется |
| Рассинхронизация SQLite ↔ NetworkX | NetworkX строго производный: запись только через db.py, который рассылает delta/rebuild |
| Граф >100k узлов, SQLite тормозит | WAL + индексы; NetworkX грузится из SQLite при старте, обновляется дельтами |
| WSL1/NFS/Docker — inotify не работает | Fallback: mtime polling каждые 60s |
| Динамические импорты `${...}` невидимы статически | Помечаем `unresolved`; раскрытие — через ленивый `nix eval` по запросу |
| Агент засыпает данными | Жёсткий бюджет: `width×depth` ≤ 100 узлов, `limit=10`, компактные Observation-модели |

---

*Версия: 2.0*
*Заменяет: repo-navigator-spec.md (v1)*
*Формат: Спецификация для AI-агента*
*Язык: ru / en*
