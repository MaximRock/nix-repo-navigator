# repo-navigator v3 — Главная спецификация проекта

> **Ассистент графа знаний для NixOS и home-manager конфигураций.**
> Строит инкрементальный мультиуровневый граф с Nix в корне, предоставляет MCP-интерфейс для AI-агентов: навигация, безопасные правки, introspection опций.
>
> **Статус:** основная спецификация. Заменяет `repo-navigator-spec.md` (v1) и `repo-navigator-spec-v2.md` (v2).

---

## 0. Изменения относительно v2

| Область | v2 | v3 | Причина |
|---------|----|----|---------|
| Nix-парсер primary | `nix-instantiate --parse` (deprecated) | Собственный статический парсер на Python + `nix eval` для динамики; `nix-instantiate` fallback | `nix-instantiate` deprecated с Nix 2.20+, отсутствует в Determinate Systems installer |
| Извлечение `config.*` | «attrpath-резолюция, mkIf/mkMerge» | Двухпроходный алгоритм: сбор всех *возможных* attrpath'ов + пометка conditional/unconditional | Статический анализ не может разрешить `mkIf cfg.enable { ... }` — нужна честная пометка неопределённости |
| Concurrency | Не описана | `RWLock` на графе: параллельные чтения, эксклюзивная запись; `deepcopy(graph)` для долгих обходов | При активном агенте + watcher без блокировок будут гонки |
| SQLite схема — версионирование | Отсутствует | `PRAGMA user_version` + миграции в `db.py` при старте | Без миграций обновление ломает БД |
| Flake-parts | Не упомянут | Обнаружение `flake-parts.lib.mkFlake` → разворачивание `imports` | Современные Nix-репозитории используют flake-parts |
| AST-diff | «Отказ от точечного AST-diff как источника багов» | Окончательно удалён из мутационного пути; только для отчёта «что изменилось» | Упрощение, меньше багов |
| Плагины — модель ошибок | Отсутствует | `ParseError` + fallback до файлового узла; плагин не может уронить ядро | 13 парсеров = 13 источников крашей |
| `generation_id` графа | Отсутствует | Монотонно возрастающий `generation_id` в SQLite; MCP-ответы включают его | Агент кэширует ответы и видит их устаревание |
| Прогресс синхронизации | Отсутствует | `status()` возвращает `sync_progress` (N/M files) при bulk-обновлении | Git checkout 200 файлов — агент не должен гадать, жив ли граф |

---

## 1. Миссия и принципы

- **NixOS + home-manager — центр.** Ассистент понимает модульную систему NixOS: объявления опций (`mkOption`), присваивания в `config`, цепочки `imports`, `specialisation`, передачу аргументов через `_module.args`. Home-manager — первый класс: `home.file`, `xdg.configFile`, `programs.*`, `home.packages`.
- **Nix-first**: Nix — единственный язык с полным AST-парсингом всегда. Остальные языки парсятся только если Nix на них ссылается (`home.file`, `xdg.configFile`, `programs.*.extraConfig`) **и** соответствующий плагин включён в конфиге.
- **Гибридная introspection**: статический граф работает офлайн и всегда отвечает на «что объявлено / где задаётся / кто импортирует». Вычисленные значения опций запрашиваются ленивым `nix eval` по явному запросу агента, кэшируются и инвалидируются по `flake.lock` / изменению `.nix` файлов.
- **Единый источник истины**: персистентность только в SQLite. NetworkX-граф — производный in-memory вид, строго синхронизируемый слоем `db.py` (никаких параллельных записей).
- **Инкрементальность**: изменение одного файла перестраивает только его подграф + каскад по импортам (Merkle-tree dirty-flags). Полный rescan — только при первом запуске или явном `refresh()`.
- **Живой граф**: фоновый watcher (inotify + git hooks + flake.lock polling) обновляет граф в реальном времени.
- **Агенто-ориентированность**: интерфейс — навигационные глаголы (`observe`, `hop`, `path`, `blast_radius`), не SQL/Cypher. Ответы включают `generation_id` для client-side cache invalidation.
- **Деградация без nix CLI**: если `nix eval` недоступен/медленен — статический режим продолжает работать; динамические ответы помечаются `unresolved`.
- **Плагины изолированы**: ни один парсер плагина не может уронить ядро. Ошибка парсинга → fallback до файлового узла + запись в лог.

---

## 2. Архитектура по слоям

```
┌──────────────────────────────────────────────────────────────────┐
│  LAYER 4: MCP Server (интерфейс для агентов)                    │
│  observe │ hop │ path │ blast_radius │ find_symbol │             │
│  summarize_module │ introspect_option │ eval_expression │        │
│  impact_analysis │ status │ refresh                             │
│  → каждый ответ включает generation_id для cache invalidation   │
├──────────────────────────────────────────────────────────────────┤
│  LAYER 3: Query Engine (Python)                                  │
│  • BFS/DFS с бюджетом (width, depth)                            │
│  • Кратчайший путь (Dijkstra)                                   │
│  • Reverse dependencies (blast_radius / impact_analysis)         │
│  • Fuzzy search (FTS5 + trigram)                                │
│  • LRU-кэш частых запросов (ключ: query_hash + generation_id)   │
│  • RWLock: параллельные чтения, ожидание записи                 │
├──────────────────────────────────────────────────────────────────┤
│  LAYER 2: Graph Storage                                          │
│  • SQLite — ЕДИНСТВЕННЫЙ источник истины: nodes, edges,          │
│    file_state, flake_inputs, package_index, option_values        │
│  • NetworkX DiGraph — производный in-memory вид                  │
│    (перестраивается из SQLite при старте; delta-обновления       │
│     через db.py; прямая запись в NetworkX запрещена)             │
│  • FTS5: полнотекстовый поиск по символам                       │
│  • WAL-режим SQLite для конкурентности                          │
│  • PRAGMA user_version + миграции при старте                    │
│  • generation_id: монотонный счётчик, инкрементится при          │
│    каждом изменении графа                                       │
├──────────────────────────────────────────────────────────────────┤
│  LAYER 1: Incremental Update Engine                              │
│  • Event Router: asyncio Queue + debounce 500ms                 │
│  • Hash Engine: content (xxhash) + AST (structural)             │
│  • Diff Engine: ТОЛЬКО отчёт «что изменилось» (не мутатор)      │
│  • Cascade Engine: Merkle-tree dirty-flags +                    │
│    инвалидация option_values                                    │
│  • Граф обновляется полной заменой подграфа файла                │
│    (старые узлы/рёбра удаляются, новые добавляются —             │
│     не точечный AST-diff)                                       │
├──────────────────────────────────────────────────────────────────┤
│  LAYER 0: Parser Registry (плагиновая система)                   │
│  Tier 0: Nix (всегда, ядро)                                      │
│    • Статический парсер на Python:                                 │
│      - Лексер: токенизация комментариев, строк, interpolation    │
│      - Парсер: attrset, list, function, let-in, if-then-else,    │
│        with, assert, операторы (//, ->, ||, &&, ?)              │
│    • module_parser: семантика NixOS/HM поверх AST               │
│    • nix-instantiate --parse --json как fallback (если доступен) │
│  Tier 1 (плагины): Python, Shell, Lua, TOML, JSON, KDL          │
│  Tier 2 (плагины): Haskell, Vimscript, Hyprlang, YAML, CSS      │
│  Tier 3: Markdown, Org, остальные (headings / file only)        │
│  • Каждый плагин: try/catch на верхнем уровне; ошибка →          │
│    файловый узел + запись в parse_errors.log                    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Технологический стек

| Компонент | Библиотека | Обоснование |
|-----------|-----------|-------------|
| Граф in-memory (производный вид) | `networkx` | Стандарт для графовых алгоритмов на Python |
| Персистентность (источник истины) | `sqlite3` (stdlib) + `fts5` | Zero-dependency, WAL, полнотекстовый поиск |
| Статический Nix-парсер | Собственный на Python | `nix-instantiate` deprecated; `tree-sitter-nix` нестабилен |
| Nix-парсер fallback | `nix-instantiate --parse --json` (subprocess) | Если доступен — более точный для сложных файлов |
| Парсинг плагинов (Tier 1–2) | `tree-sitter` + `tree-sitter-languages` | Зрелые грамматики для Python/Shell/Lua/TOML/JSON/YAML/CSS |
| Динамическая introspection | `nix eval --json` (subprocess, lazy, timeout) | Единственный способ получить вычисленные значения |
| Парсинг KDL (плагин) | `kdl-py` | Единственная зрелая библиотека KDL для Python |
| Файловый watcher | `watchdog` | Кроссплатформенный inotify/FSEvents/ReadDirectoryChanges |
| Хеширование | `xxhash` + `hashlib` | xxhash — быстрое; hashlib — для Merkle-дерева |
| MCP | `mcp` (официальный Python SDK от Anthropic) | Стандарт для AI-агентов |
| CLI | `typer` | Богатый CLI из коробки |
| Модели | `pydantic` | Валидация, сериализация в JSON для MCP |
| Асинхронность | `asyncio` (stdlib) | Один event loop для watcher + MCP + eval |
| Git | `gitpython` | Чтение git-объектов, установка hooks |

**Требование среды:** полный функционал (динамическая introspection) требует `nix` в PATH. Без него — статический режим, динамические запросы возвращают `unresolved`.

---

## 4. Структура проекта с комментариями

```
repo-navigator/
├── pyproject.toml                  # PEP 621: зависимости, entry points, метаданные
├── README.md                       # Быстрый старт, примеры для агентов
├── repo-navigator-spec-v3.md       # ← эта спецификация (главная)
│
└── src/
    └── repo_navigator/
        ├── __init__.py             # Версия пакета, публичное API
        │
        ├── cli.py                  # Typer CLI: команды start/status/refresh/install-hooks
        │   # Точка входа для пользователя.
        │   # • start      — запуск MCP-сервера (основной режим)
        │   # • status     — вывод статуса графа в консоль
        │   # • refresh    — принудительный rescan из командной строки
        │   # • install-hooks — установка git post-checkout/post-merge
        │   # • dev parse FILE  — отладка: показать извлечённые узлы/рёбра файла
        │
        ├── mcp_server.py           # MCP сервер: регистрация 11 инструментов
        │   # Входная точка: python -m repo_navigator.mcp_server
        │   # Связывает MCP-инструменты с методами QueryEngine.
        │   # Каждый инструмент:
        │   #   1. Принимает параметры (Pydantic-модели)
        │   #   2. Вызывает соответствующий метод queries.py
        │   #   3. Возвращает результат с generation_id
        │   # Транспорт: stdio (JSON-RPC)
        │
        ├── config.py               # Pydantic-settings: все параметры конфигурации
        │   # • root: Path — корень отслеживаемого репозитория
        │   # • plugins: list[str] — включённые Tier 1–2 плагины (пусто = только Nix)
        │   # • budgets: dict — width/depth/limit для запросов
        │   # • timeouts: dict — nix_eval, debounce, polling
        │   # • watcher: dict — режим (inotify/polling), интервал
        │   # • db_path: Path — путь к SQLite-файлу (по умолчанию <root>/.repo-navigator.db)
        │   # • log_level: str
        │   # Загрузка: env vars (REPO_NAVIGATOR_*) + .env + аргументы CLI
        │
        ├── graph/                  # === Слой 2–3: хранение и запросы графа ===
        │   ├── __init__.py
        │   │
        │   ├── db.py               # SQLite: ЕДИНСТВЕННАЯ точка записи и чтения
        │   │   # Функции:
        │   │   # • init_db() — создание схемы + PRAGMA user_version + миграции
        │   │   # • get_generation_id() → int
        │   │   # • inc_generation_id() — атомарный инкремент
        │   │   # • upsert_node(node: Node) — вставка/обновление узла
        │   │   # • delete_file_nodes(path: str) — удаление всех узлов/рёбер файла
        │   │   # • upsert_edge(edge: Edge)
        │   │   # • get_node(id: str) → Node | None
        │   │   # • get_neighbors(node_id: str, depth: int) → list[(Edge, Node)]
        │   │   # • get_reverse_neighbors(node_id: str) → list[(Edge, Node)]
        │   │   # • get_all_nodes() → list[Node]  (для перестройки NetworkX)
        │   │   # • get_all_edges() → list[Edge]  (для перестройки NetworkX)
        │   │   # • upsert_file_state(...)
        │   │   # • get_file_state(path: str) → FileState | None
        │   │   # • get_dirty_files() → list[str]
        │   │   # • upsert_flake_input(...)
        │   │   # • upsert_option_value(...)
        │   │   # • invalidate_option_values(file_paths: list[str])
        │   │   # • invalidate_all_option_values()
        │   │   # • get_option_value(key: str) → OptionValue | None
        │   │   # • search_fts5(query: str, limit: int) → list[Node]
        │   │   # Миграции:
        │   │   #   PRAGMA user_version = N; миграция N→N+1 при старте
        │   │   # Синхронизация с NetworkX:
        │   │   #   После каждой мутации вызывает nx_graph.apply_delta() или
        │   │   #   публикует событие в asyncio.Queue
        │   │
        │   ├── nx_graph.py         # Производный NetworkX DiGraph
        │   │   # СТРОГО производный: запись только через db.py.
        │   │   # Методы:
        │   │   # • rebuild() — полная перестройка из SQLite (холодный старт)
        │   │   # • apply_delta(added_nodes, removed_ids, added_edges, removed_ids)
        │   │   # • get_graph() → nx.DiGraph (возвращает deepcopy для долгих обходов)
        │   │   # • bfs(source, depth, width) → Subgraph
        │   │   # • shortest_path(source, target) → list[PathStep]
        │   │   # • reverse_bfs(source, max_depth) → Subgraph
        │   │   # Concurrency:
        │   │   #   threading.RWLock: параллельные чтения, эксклюзивная запись.
        │   │   #   Долгие обходы работают на deepcopy, не держат read-lock.
        │   │
        │   ├── builder.py          # Построение графа из вывода парсеров
        │   │   # Принимает ParseResult (от парсера) → создаёт Node/Edge объекты.
        │   │   # Координирует:
        │   │   #   1. Получение ParseResult от parser_registry
        │   │   #   2. Преобразование в Node/Edge с правильными ID и типами
        │   │   #   3. Вызов db.py для сохранения
        │   │   #   4. Вызов nx_graph.apply_delta()
        │   │   #   5. Инкремент generation_id
        │   │   # Отвечает за ID-схему: {lang}:{path}:{symbol}
        │   │
        │   └── queries.py          # Query Engine: навигационные глаголы
        │       # Каждый метод = один навигационный глагол.
        │       # Принимает параметры → возвращает Pydantic-модель ответа.
        │       # Внутри: комбинация db.py (точечные запросы) + nx_graph.py (обходы).
        │       # LRU-кэш: ключ = (метод, хеш-параметров, generation_id).
        │       # При изменении generation_id кэш сбрасывается.
        │       # Методы:
        │       # • observe(node_id, depth)
        │       # • hop(node_id, relation, depth, width)
        │       # • path(source, target)
        │       # • blast_radius(node_id, max_depth)
        │       # • find_symbol(query, lang, fuzzy, limit)
        │       # • summarize_module(path)
        │       # • introspect_option(option_path, include_value)
        │       # • eval_expression(expr, timeout)
        │       # • impact_analysis(node_id, max_depth)
        │       # • status()
        │       # • refresh()
        │
        ├── parsers/                # === Слой 0: парсеры языков ===
        │   ├── __init__.py
        │   │
        │   ├── registry.py         # Плагиновая система: LanguageConfig + @register_language
        │   │   # LanguageConfig:
        │   │   #   name, extensions, tier (0|1|2|3), parser_class, enabled
        │   │   # @register_language декоратор регистрирует парсер.
        │   │   # get_parser_for_file(path) → BaseParser | None
        │   │   # get_all_parsers() → list[BaseParser]
        │   │   # Правило Nix-first:
        │   │   #   Tier 1–2 парсер вызывается только если:
        │   │   #   - файл уже упомянут в графе (есть ребро configures/generates)
        │   │   #   - ИЛИ путь содержит .config/
        │   │   #   - И плагин включён в config.py
        │   │
        │   ├── base.py             # BaseParser ABC
        │   │   # Абстрактный метод:
        │   │   #   parse(path: Path, content: str) → ParseResult
        │   │   # ParseResult = (nodes: list[RawNode], edges: list[RawEdge])
        │   │   # RawNode/RawEdge — промежуточные структуры, ещё не сохранённые в БД.
        │   │   # Вызов парсера ОБЯЗАН быть обёрнут в try/catch на уровне registry.
        │   │
        │   ├── nix/                # === Nix-подсистема (Tier 0, ядро) ===
        │   │   ├── __init__.py
        │   │   │
        │   │   ├── lexer.py        # Лексер Nix (собственный)
        │   │   │   # Токенизирует исходный текст в поток токенов.
        │   │   │   # Токены: IDENT, STRING, INT, FLOAT, PATH, URI,
        │   │   │   #   LBRACE, RBRACE, LBRACK, RBRACK, LPAREN, RPAREN,
        │   │   │   #   DOT, SEMI, COLON, EQ, ELLIPSIS, DOLLAR,
        │   │   │   #   LAMBDA, ARROW, OR, IMPL, question,
        │   │   │   #   KW_LET, KW_IN, KW_IF, KW_THEN, KW_ELSE,
        │   │   │   #   KW_WITH, KW_ASSERT, KW_REC, KW_INHERIT,
        │   │   │   #   COMMENT_SINGLE, COMMENT_MULTI (пропускаются,
        │   │   │   #   но позиции сохраняются для location)
        │   │   │   # Обрабатывает: escape-последовательности, интерполяцию
        │   │   │   #   ${...} в строках (возвращается как INTERPOL_START/END)
        │   │   │
        │   │   ├── parser.py       # Парсер Nix (собственный, ручной рекурсивный спуск)
        │   │   │   # Строит AST-дерево из потока токенов.
        │   │   │   # Узлы AST:
        │   │   │   #   Expr (базовый), Literal, AttrSet, AttrPath,
        │   │   │   #   List, Function, FunctionCall, LetIn, IfThenElse,
        │   │   │   #   With, Assert, BinaryOp, UnaryOp, Interpolation,
        │   │   │   #   Inherit, Pin
        │   │   │   # AttrSet: различает рекурсивные ({ a = 1; }) и
        │   │   │   #   нерекурсивные ({ inherit a; })
        │   │   │   # AttrPath: a.b.c = x разворачивается в
        │   │   │   #   a = { b = { c = x; }; };
        │   │   │   # Операторы: // (merge), -> (implies), &&, ||, !, ?,
        │   │   │   #   +, -, *, /, ++, <, >, <=, >=, ==, !=
        │   │   │   # Не парсим (возвращаем как UnresolvedExpr):
        │   │   │   #   - всё внутри ${...} в строках (требует eval)
        │   │   │   #   - вызовы builtins.* с неизвестной семантикой
        │   │   │   # Приоритет: свой парсер → nix-instantiate fallback
        │   │   │
        │   │   ├── ast_extract.py  # Обход AST: извлечение значимых конструкций
        │   │   │   # Извлекает из AST плоский список значимых конструкций:
        │   │   │   # • imports: list[str] — все пути из `imports = [...]`
        │   │   │   # • options: list[OptionDecl] — все `options.* = mkOption {...}`
        │   │   │   # • configs: list[ConfigSet] — все `config.* = <expr>`
        │   │   │   # • specialisations: list[Specialisation]
        │   │   │   # • module_args: list[ModuleArg]
        │   │   │   # • home_files: list[HomeFile]
        │   │   │   # • packages: list[PackageRef]
        │   │   │   # • functions: list[FunctionDecl]
        │   │   │   # Для config.* собирает ВСЕ возможные attrpath'ы, даже
        │   │   │   #   внутри mkIf/mkMerge: помечает их conditional=true.
        │   │   │   # Это честная стратегия: «может задаваться при условии».
        │   │   │
        │   │   ├── module_parser.py # Семантика NixOS/HM поверх ast_extract
        │   │   │   # Принимает вывод ast_extract → строит RawNode/RawEdge.
        │   │   │   # Понимает модульную систему:
        │   │   │   # • imports → ребро imports между nix_module
        │   │   │   # • options.* → узел nix_option + ребро declares
        │   │   │   # • config.* → ребро sets к nix_option
        │   │   │   # • specialisation → ребро specialises
        │   │   │   # • _module.args → ребро passes_args
        │   │   │   # • home.file / xdg.configFile → ребро configures к file
        │   │   │   # • home.packages → ребро uses_package к package_ref
        │   │   │   # Приоритеты mkForce/mkDefault → edge.metadata.priority
        │   │   │   # Условные присваивания (mkIf) → edge.metadata.conditional=True
        │   │   │   # Обнаруживает flake-parts.lib.mkFlake → рекурсивно
        │   │   │   #   разворачивает imports внутри flake-parts
        │   │   │
        │   │   └── nix_instantiate.py # Fallback: вызов nix-instantiate --parse --json
        │   │       # Используется если:
        │   │       #   1. Собственный парсер вернул UnresolvedExpr для >50% файла
        │   │       #   2. И nix-instantiate доступен в PATH
        │   │       # Результат → обрабатывается ast_extract как обычно
        │   │
        │   └── plugins/           # Tier 1–3 парсеры (опциональные)
        │       ├── __init__.py
        │       ├── python.py       # tree-sitter-python: py_function, py_class, qtile_key, qtile_hook
        │       ├── kdl.py          # kdl-py: kdl_bind, kdl_rule, kdl_spawn
        │       ├── shell.py        # tree-sitter-bash: sh_function, sh_command_call
        │       ├── lua.py          # tree-sitter-lua: lua_function, lua_require, vim_keymap
        │       ├── toml.py         # tree-sitter-toml: toml_section, toml_key
        │       ├── json.py         # stdlib json: json_key
        │       ├── hyprlang.py     # Собственный (простой синтаксис key=value)
        │       ├── haskell.py      # tree-sitter-haskell: hs_function, hs_module
        │       ├── vimscript.py    # tree-sitter-vim: vim_function, vim_command
        │       ├── yaml.py         # tree-sitter-yaml (или stdlib yaml): yaml_key
        │       ├── css.py          # tree-sitter-css: css_rule
        │       └── markdown.py     # Заголовки Markdown → секции как узлы
        │
        ├── models/                 # === Pydantic-модели данных ===
        │   ├── __init__.py
        │   ├── nodes.py            # Node, NodeType (StrEnum), RawNode
        │   ├── edges.py            # Edge, EdgeType (StrEnum), RawEdge
        │   ├── file_state.py       # FileState — состояние файла для инкрементальности
        │   ├── option_value.py     # OptionValue — кэш динамической introspection
        │   └── queries.py          # Модели ответов:
        │       # Observation, Subgraph, PathStep, ImpactReport,
        │       # OptionInfo, EvalResult, StatusResponse, ParseResult
        │
        ├── indexer/                # === Слой 1: инкрементальное обновление ===
        │   ├── __init__.py
        │   │
        │   ├── event_router.py     # Приём файловых событий, дебаунсинг, маршрутизация
        │   │   # Вход: FileSystemEvent от watchdog ИЛИ git hook event
        │   │   # Дебаунсинг: asyncio.Queue + таймер 500ms → batch
        │   │   # Batch: группировка событий по файлу (последнее побеждает)
        │   │   # Выход: кладёт list[path] в очередь update_engine
        │   │   # Типы событий: MODIFY, CREATE, DELETE, RENAME (old→new)
        │   │
        │   ├── update_engine.py    # Координатор инкрементального обновления
        │   │   # Основной цикл:
        │   │   #   1. Получает list[path] из очереди event_router
        │   │   #   2. Для каждого файла вызывает process_file(path)
        │   │   #   3. После обработки batch: cascade dirty-файлов
        │   │   #   4. Инкрементирует generation_id
        │   │   # process_file(path):
        │   │   #   a. content_hash = xxhash(content)
        │   │   #   b. Если совпадает с file_state.content_hash → skip
        │   │   #   c. Парсинг: registry.parse(path)
        │   │   #   d. ast_hash = structural_hash(parse_result)
        │   │   #   e. Если ast_hash совпадает: обновить content_hash только
        │   │   #   f. Иначе: delete_file_nodes(path) + builder.build(parse_result)
        │   │   #   g. Обновить file_state
        │   │   #   h. Вернуть список импортёров для cascade
        │   │
        │   ├── hash_engine.py      # Вычисление хешей
        │   │   # content_hash(path) → xxhash.xxh64(content).hexdigest()
        │   │   # ast_hash(parse_result) → structural:
        │   │   #   Сортирует узлы по id, удаляет content_hash поля,
        │   │   #   сериализует в JSON, xxhash.xxh64(json).hexdigest()
        │   │   # merkle_hash(file, dependency_hashes) → SHA256:
        │   │   #   hash(file.ast_hash + ''.join(sorted(dep_hashes)))
        │   │
        │   ├── diff_engine.py      # ТОЛЬКО отчёт «что изменилось»
        │   │   # Принимает старый ParseResult и новый → DiffReport
        │   │   # DiffReport: added_nodes, removed_nodes, changed_nodes,
        │   │   #   added_edges, removed_edges
        │   │   # НЕ мутирует граф. Используется для логирования и
        │   │   #   потенциально для MCP-инструмента diff (будущее).
        │   │
        │   └── cascade.py          # Merkle-tree dirty-flags + инвалидация кэша
        │       # После обновления файла:
        │       #   1. Пересчитывает merkle_hash файла
        │       #   2. Находит всех прямых импортёров (reverse edges import)
        │       #   3. Помечает их dirty=1 в file_state
        │       #   4. Для каждого dirty-импортёра: рекурсивно шаги 1–3
        │       #   5. Собирает список затронутых .nix файлов →
        │       #      invalidate option_values для выражений,
        │       #      затрагивающих эти файлы
        │       # Оптимизация: topological sort, снизу вверх.
        │
        ├── nix/                    # === Взаимодействие с Nix CLI ===
        │   ├── __init__.py
        │   │
        │   ├── eval.py             # Ленивые обёртки nix eval
        │   │   # eval_option(attrpath: str) → value_json | None
        │   │   # eval_expression(expr: str) → value_json | None
        │   │   # Таймаут 60s (конфигурируемо); превышение → None
        │   │   # Запуск: nix eval --json --impure --expr "..." или
        │   │   #   nix eval --json ".#nixosConfigurations.<name>.config.<path>"
        │   │   # Ошибки парсинга JSON-ответа → None
        │   │   # Проверка доступности nix: shutil.which("nix") при старте
        │   │
        │   ├── eval_cache.py       # Кэш option_values
        │   │   # cache_key(expr) = xxhash(expr + flake_rev).hexdigest()
        │   │   # get(expr) → OptionValue | None (проверяет status != stale)
        │   │   # put(expr, value, status) → сохраняет в option_values
        │   │   # invalidate_for_files(paths: list[str]) → помечает stale
        │   │   #   все записи, чей ключ содержит любой из путей
        │   │   # invalidate_all() → все записи stale
        │   │   # При старте: все записи с source_rev != flake.lock.rev → stale
        │   │
        │   ├── flake_tracker.py    # Отслеживание flake.lock
        │   │   # Фоновый поток: каждые 30s проверяет flake.lock
        │   │   # При изменении: обновляет flake_inputs, инвалидирует
        │   │   #   все option_values → eval_cache.invalidate_all()
        │   │   # Обнаружение flake-parts: парсит flake.nix, ищет
        │   │   #   flake-parts.lib.mkFlake → разворачивает imports
        │   │   #   для обнаружения всех модулей
        │   │
        │   └── package_index.py    # Индексация пакетов
        │       # Извлекает home.packages, environment.systemPackages
        │       # Сохраняет в package_index: attribute, name, version
        │       # Опционально: nix eval для получения meta
        │
        └── watcher/                # === Файловый watcher ===
            ├── __init__.py
            ├── filesystem.py       # watchdog Observer + mtime fallback
            │   # Основной режим: watchdog.observers.Observer
            │   #   - Рекурсивный watch на корень репозитория
            │   #   - Игнорирует: .git/, .repo-navigator.db, __pycache__
            │   # Fallback (WSL/NFS/Docker): mtime polling
            │   #   - Каждые 60s (конфигурируемо) обходит все файлы
            │   #   - Сравнивает mtime с file_state.last_parsed
            │   #   - Изменённые → очередь event_router
            │   # Режим автоопределяется: пробуем inotify, при ошибке → polling
            │   #   с предупреждением в лог
            │   └── git_hooks.py    # Установка и обработка git hooks
            │       # install(repo_path) → записывает скрипты в .git/hooks/
            │       #   post-checkout: вызывает event_router с diff
            │       #     (список изменённых файлов между старым и новым HEAD)
            │       #   post-merge: аналогично, файлы из merge diff
            │       # Bulk-обновление: при checkout/merge >50 файлов —
            │       #   status() возвращает sync_progress = (N, total)
```

---

## 5. Модель данных

### 5.1. Узлы (Node)

```python
class Node(BaseModel):
    id: str                    # "nix_option:services.nginx.enable"
    type: NodeType             # StrEnum
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
    type: EdgeType             # StrEnum
    metadata: dict             # строка в файле, priority (mkForce/mkDefault), conditional
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
-- Версионирование схемы
PRAGMA user_version = 1;  -- инкрементится при миграциях

-- Счётчик generation_id: монотонно возрастает при каждом изменении графа
CREATE TABLE IF NOT EXISTS generation (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    value INTEGER NOT NULL DEFAULT 0
);

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
    source TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
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
    key TEXT PRIMARY KEY,          -- xxhash(expr + flake_rev + affected_files_hash)
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
- **Tier 0 (Nix):** парсим ВСЕГДА, полный AST, включая семантику модульной системы. Это ядро.
- **Tier 1–2 (плагины, вкл/выкл в `config.py`):** парсим только если:
  1. Nix явно ссылается на файл через `generates` / `configures` / `home.file` / `xdg.configFile` — то есть файл уже упомянут в графе как цель Nix-перехода;
  2. ИЛИ файл лежит внутри `.config/**` (эвристика для home-manager);
  3. И плагин включён в списке `config.plugins`.
- **Tier 3:** только файловый узел, без AST. Заголовки Markdown/Org — опционально как секции.

**Изоляция ошибок:**
- Каждый вызов `parser.parse()` обёрнут в `try/catch` на уровне `registry`.
- При ошибке: создаётся один узел типа `file` для этого файла; ошибка пишется в `parse_errors.log` (рядом с БД).
- Ошибка парсера НИКОГДА не роняет ядро, не прерывает обновление других файлов.

### 6.2. Nix-подсистема (Tier 0) — переработано в v3

**6.2.1. Статический парсер на Python (primary) — новый в v3**

v3 вводит собственный парсер Nix на Python как основной путь. Причины:
- `nix-instantiate --parse` deprecated с Nix 2.20+ и отсутствует в некоторых установках;
- `tree-sitter-nix` нестабилен (неполная грамматика, ошибки на `''${...}''` и `//`);
- Для статического извлечения (imports, options, config, home.file) не нужен полный Nix- парсер — достаточно надмножества «структурного» синтаксиса.

Что парсим (достаточно для статического графа):
- attrset (рекурсивный и нерекурсивный), attrpath-присваивания
- строки (одинарные, двойные, `''`-heredoc), интерполяция `${...}` (как непрозрачный блок)
- числа, булевы, null
- списки
- функции (аргументы + тело)
- `let ... in ...`, `if ... then ... else ...`, `with ...; ...`, `assert ...; ...`
- операторы: `//`, `->`, `&&`, `||`, `!`, `?`, арифметика, сравнения
- `inherit`, `inherit (src)`
- путь к файлу (строки, начинающиеся с `./` или `/`)

Что НЕ парсим (возвращаем `UnresolvedExpr`):
- Всё внутри `${...}` в строках — требует eval
- Вызовы `builtins.*` с неизвестным результатом
- Динамические импорты: `imports = [ (import ./auto-imports.nix) ]`

**Алгоритм ast_extract для `config.*`:**
1. Рекурсивно обходим AST, собираем ВСЕ attrpath-присваивания (a.b.c = x).
2. Для каждого — определяем контекст: внутри `options.*` или `config.*`?
3. Для `config.*`: если присваивание внутри `mkIf <cond> { ... }` или `mkMerge [...]` — помечаем `conditional=true`.
4. Не пытаемся вычислить условие статически — возвращаем «может задаваться при условии».

**Fallback:** если собственный парсер не смог разобрать >50% файла (по объёму UnresolvedExpr / общему числу выражений), и `nix-instantiate --parse` доступен — вызывается fallback.

**6.2.2. Динамический режим (лениво, по запросу агента):**
`nix eval --json <expr>` — вычисленное значение опции или произвольного выражения.
- Таймаут по умолчанию 60s (конфигурируемо); превышение → `status=unresolved`.
- Результат кэшируется в `option_values`; ключ = xxhash(expr + flake_rev + affected_files_hash).
- Инвалидация: изменение `flake.lock` → все записи `stale`; изменение `.nix` → записи, чей expr затронут каскадом, `stale`.

### 6.3. Event Router

- `watchdog.observers.Observer` на корень репозитория; fallback `mtime` polling 60s (WSL/NFS/Docker). Автоопределение: пробуем inotify, при ошибке → polling с предупреждением.
- Дебаунсинг 500ms: batch изменений → одна транзакция. Группировка: MODIFY+MODIFY одного файла → один обработчик.
- Git hooks: `post-checkout`, `post-merge` → bulk-обновление diff-файлов; при >50 файлов status() показывает `sync_progress`.
- Flake lock tracker: отдельный поток, проверка `flake.lock` каждые 30s → инвалидация eval-кэша.

### 6.4. Incremental Update Engine

**Алгоритм (v3 — упрощённый и надёжный):**
1. Считаем `content_hash` (xxhash).
2. Если совпадает с `file_state.content_hash` → игнорируем (файл не менялся).
3. Парсим → получаем `ParseResult`.
4. Вычисляем `ast_hash` (структурный хеш ParseResult).
5. Если `ast_hash` совпадает с `file_state.ast_hash` → AST идентичен, обновляем только `content_hash` (изменились комментарии/форматирование).
6. Иначе: **полная замена подграфа файла**:
   a. `delete_file_nodes(path)` — удаляем все узлы и рёбра, связанные с этим файлом.
   b. `builder.build(parse_result)` — добавляем новые узлы и рёбра.
   c. Обновляем `file_state` (content_hash, ast_hash, dirty=0, last_parsed).
7. **Cascade**: находим всех импортёров этого файла, помечаем `dirty=1`; инвалидируем `option_values` для затронутых выражений.
8. Инкрементируем `generation_id`.

**Почему полная замена, а не AST-diff:**
- AST-diff — источник багов: сложно вычислить, какой именно узел изменился.
- Полная замена атомарна: удалили всё старое, добавили всё новое.
- Цена: SQLite-транзакция с ~100 операциями для одного файла — доли миллисекунды.
- `diff_engine` остаётся ТОЛЬКО для отчёта «что изменилось» (логирование, будущий MCP-инструмент `diff`).

### 6.5. Concurrency Model (новое в v3)

```
                    ┌──────────────┐
                    │  MCP Server   │  читает граф
                    │  (asyncio)    │  (держит read-lock кратко или
                    └──────┬───────┘   deepcopy для долгих обходов)
                           │
                    ┌──────▼───────┐
                    │  Query Engine │  RWLock на nx_graph:
                    │               │  • read-lock: observe, hop, path, blast_radius
                    │               │  • write-lock: update_engine (редко)
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
     ┌────────▼───┐ ┌─────▼──────┐ ┌──▼──────────┐
     │ Event Router│ │ Update     │ │ Eval Cache   │
     │ (asyncio)   │ │ Engine     │ │ (asyncio)    │
     └────────────┘ └─────┬──────┘ └──────────────┘
                          │
                   ┌──────▼───────┐
                   │  db.py       │  ЕДИНСТВЕННАЯ запись
                   │  (SQLite)    │  WAL: параллельные чтения
                   └──────────────┘
```

**Стратегия:**
- SQLite WAL-режим: параллельные чтения, одна запись за раз (SQLite сам сериализует).
- NetworkX защищён `threading.RWLock`:
  - `read-lock`: параллельные чтения (observe, hop, path, blast_radius).
  - `write-lock`: эксклюзивно для update_engine; новые читатели ждут.
- Долгие обходы (Dijkstra, blast_radius с depth=5) работают на `deepcopy(graph)`, захватывая read-lock только на время копирования.
- `generation_id` защищён: атомарный `UPDATE generation SET value = value + 1` через SQLite.
- Запросам не нужно ждать запись: вероятность коллизии низка (запись ~10ms, чтение ~1ms).

### 6.6. Query Engine (навигационные глаголы)

| Операция | Описание | Бюджет | Модель ответа |
|----------|----------|--------|---------------|
| `observe(node_id, depth=1)` | Соседи, типы рёбер, сводка | 20 соседей | `Observation` |
| `hop(node_id, relation, depth, width)` | BFS-обход с фильтром | width×depth ≤ 100 | `Subgraph` |
| `path(source, target)` | Кратчайший путь (Dijkstra) | — | `list[PathStep]` |
| `blast_radius(node_id, max_depth)` | Reverse BFS (обратные зависимости) | max_depth=5 | `Subgraph` |
| `find_symbol(query, lang, fuzzy, limit)` | FTS5 + trigram | limit=10 | `list[Node]` |
| `summarize_module(path)` | Входящие/исходящие, ключевые символы | — | `ModuleSummary` |
| `introspect_option(option_path, include_value=False)` | Статическая декларация + опционально ленивое значение | — | `OptionInfo` |
| `eval_expression(expr, timeout)` | Произвольный ленивый `nix eval` с кэшем | timeout ≤ 120s | `EvalResult` |
| `impact_analysis(node_id, max_depth)` | Затронутые модули/опции/файлы, оценка риска | max_depth=5 | `ImpactReport` |
| `status()` | Статус ассистента (режим, размер графа, sync_progress) | — | `StatusResponse` |
| `refresh()` | Принудительный полный rescan | — | `StatusResponse` |

**Все ответы включают `generation_id: int`** — агент может сравнить с предыдущим и понять, устарел ли его кэш.

### 6.7. MCP Server

Инструменты (11):
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

Конфигурация: путь к репозиторию передаётся через `REPO_NAVIGATOR_ROOT` env или аргумент `--root`.

### 6.8. SQLite Schema Migrations (новое в v3)

При старте `db.py.init_db()`:
1. Читает `PRAGMA user_version`.
2. Если 0 → создаёт все таблицы с нуля, ставит user_version = 1.
3. Если N → применяет миграции N→N+1 до текущей версии (хранится как константа в `db.py`).
4. Миграции — чистый SQL в строках, внутри `db.py` (без Alembic, без зависимостей).

Пример:
```python
MIGRATIONS = {
    1: "ALTER TABLE nodes ADD COLUMN metadata JSON;",  # пример
    2: "CREATE TABLE IF NOT EXISTS generation (...);",
}
CURRENT_SCHEMA_VERSION = 2
```

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
   ├── 4a. xxhash(content) != old → продолжаем
   ├── 4b. Собственный парсер: lexer → parser → AST
   ├── 4c. ast_extract: imports, options, configs, specialisations
   ├── 4d. module_parser: RawNode/RawEdge с семантикой NixOS/HM
   ├── 4e. delete_file_nodes(path) + builder.build(parse_result)
   ├── 4f. Обновляем file_state (content_hash, ast_hash, dirty=0)
   ├── 4g. Cascade: dirty всех импортёров (Merkle-tree)
   ├── 4h. Инвалидация option_values затронутых выражений → status=stale
   └── 4i. inc_generation_id()
           │
           ▼
5. Агент вызывает MCP: observe("nix:modules/services/nginx.nix")
   ├── 5a. QueryEngine читает NetworkX (read-lock)
   ├── 5b. Возвращает Observation с generation_id=42
   └── 5c. Агент кэширует ответ с generation_id=42
```

### 7.2. Вопрос агента (типовой сценарий)

```
Агент: «Что делает опция services.nginx.enable и где она задаётся?»
  1. introspect_option("services.nginx.enable")
     → статическая декларация: type=bool, default=false,
       declared_in=..., defined_in=[...], conditional_sets=[...]
  2. (опционально) include_value=True
     → eval_cache hit? значение : ленивый nix eval (timeout, кэширование)
  3. (опционально) impact_analysis → кто ещё sets эту опцию / что сломается
```

---

## 8. Дорожная карта (v3)

| Неделя | Этап | Результат | Ключевые риски |
|--------|------|-----------|----------------|
| 1 | **Фундамент** | pyproject.toml, структура пакета, Pydantic-модели, SQLite-схема с миграциями, `db.py` CRUD, `nx_graph.py` rebuild, CLI (`start`), `config.py` | — |
| 2 | **Nix Lexer + Parser** | `lexer.py` (все токены), `parser.py` (рекурсивный спуск), тесты на 50+ реальных Nix-файлах | Сложность Nix-синтаксиса; нужны golden-тесты |
| 3 | **ast_extract + module_parser** | Извлечение imports/options/config/specialisation из AST; семантика NixOS/HM; flake-parts; тесты на модулях nixpkgs | Извлечение config.* из mkIf/mkMerge — честная пометка conditional |
| 4 | **Инкрементальность** | `event_router.py`, `update_engine.py`, `hash_engine.py`, `cascade.py`, `diff_engine.py` (только отчёт) | Каскад на большом графе; дебаунсинг batch |
| 5 | **Query Engine** | `queries.py`: observe, hop, path, blast_radius, find_symbol, summarize_module; LRU-кэш; RWLock | Бюджеты BFS, deepcopy производительность |
| 6 | **MCP Server** | 11 инструментов, stdio транспорт, тесты с MCP Inspector | Корректность JSON-RPC, обработка ошибок |
| 7 | **Гибридная introspection** | `eval.py`, `eval_cache.py`, `flake_tracker.py`; introspect_option + eval_expression; graceful degradation без nix | nix eval медленный; кэш-инвалидация |
| 8 | **Home-manager слой** | home.file/xdg.configFile/programs.* → переходы в dotfiles; watcher на сгенерированные файлы | — |
| 9 | **impact_analysis** | Затронутые опции/файлы/модули, оценка риска правки | — |
| 10 | **Git + пакеты** | `git_hooks.py`, `package_index.py` | Корректность post-checkout diff |
| 11 | **Тестирование** | Интеграционные тесты на реальных NixOS+HM репо; golden-тесты ast_extract/module_parser; нагрузочное тестирование графа 10K+ узлов | — |
| 12 | **Оптимизация и релиз** | WAL-индексы, бенчмарки, LRU-профилирование, документация, примеры для агентов | — |

**MVP (навигация + introspection, пригодный для агентов):** конец недели 7.
**Плагины Tier 1–2 (KDL/Lua/qtile…):** после MVP, по потребности.

---

## 9. Критерии приёмки (Definition of Done)

- [ ] Собственный Nix-парсер корректно разбирает ≥95% файлов из реального NixOS+HM репозитория (проверено golden-тестами).
- [ ] Статическое извлечение находит `imports`, `options.*` (с метаданными mkOption), `config.*`, `specialisation`, `_module.args`, `home.file`, `home.packages`; `config.*` внутри mkIf/mkMerge помечается `conditional=true`.
- [ ] `nix-instantiate --parse` используется как fallback только при недоступности собственного парсера и работает идентично.
- [ ] Изменение одного `.nix` файла триггерит обновление графа за < 1 секунду (при графе ≤ 10K узлов).
- [ ] `blast_radius` возвращает всех импортёров (прямо и косвенно); Merkle-tree dirty-flags корректно каскадируются.
- [ ] `introspect_option` офлайн возвращает декларацию; `include_value=True` лениво достаёт значение и кэширует его.
- [ ] Изменение любого `.nix` → соответствующие `option_values` → `stale`; `nix flake update` → все записи инвалидируются.
- [ ] Без `nix` в PATH ассистент работает в статическом режиме; динамические запросы отвечают `unresolved`, не падают.
- [ ] `impact_analysis` перечисляет затронутые модули, опции (`sets`), генерируемые файлы с оценкой риска.
- [ ] MCP-сервер запускается через `python -m repo_navigator.mcp_server` и отвечает на все 11 инструментов; каждый ответ включает `generation_id`.
- [ ] `git checkout other-branch` → `post-checkout` hook → граф обновляется; `status()` показывает `sync_progress` при bulk-обновлении.
- [ ] Агент отвечает на вопрос «Где настроена опция X?» за ≤ 3 MCP-вызовов.
- [ ] Ошибка плагина Tier 1–2 не роняет ядро; файл получает fallback-узел `file`.
- [ ] Повторный запуск с существующей БД: миграции применяются, данные сохраняются, граф валиден.

---

## 10. Риски и mitigations (v3)

| Риск | Mitigation | Статус |
|------|------------|--------|
| Собственный Nix-парсер не покрывает весь синтаксис | Golden-тесты на реальных модулях nixpkgs (200+ файлов); fallback на `nix-instantiate`; неразобранные части → `UnresolvedExpr` | Активный |
| Извлечение `config.*` из mkIf/mkMerge неполно | Честная пометка `conditional=true`; не пытаемся вычислить условия статически; значения — через ленивый `nix eval` | Активный |
| `nix eval` медленный, требует сборки/сети | Только по явному запросу агента; таймаут 60s; кэш в `option_values`; инвалидация по flake.lock и изменению .nix; граф никогда не блокируется динамикой | Контролируемый |
| nix CLI отсутствует в PATH | Проверка `shutil.which("nix")` при старте; статический режим; динамические запросы → `unresolved`; явное сообщение агенту | Контролируемый |
| Рассинхронизация SQLite ↔ NetworkX | NetworkX строго производный; запись только через `db.py`; RWLock; generation_id для обнаружения гонок клиентом | Контролируемый |
| Граф >100k узлов, SQLite тормозит | WAL + индексы; NetworkX грузится из SQLite при старте, обновляется дельтами; LRU-кэш запросов | Контролируемый |
| WSL1/NFS/Docker — inotify не работает | Fallback: mtime polling 60s; автоопределение при старте; предупреждение в лог | Контролируемый |
| Динамические импорты `${...}` невидимы статически | Помечаем `unresolved`; раскрытие — через ленивый `nix eval` по запросу агента | Контролируемый |
| Git checkout 200+ файлов — долгое обновление | `status()` возвращает `sync_progress` (N/total); bulk-обновление пачками по 50 файлов с промежуточными generation_id | Контролируемый |
| Обновление схемы БД ломает существующие данные | `PRAGMA user_version` + миграции в `db.py`; тесты миграций на старых БД | Контролируемый |
| Flake-parts скрывает модули от статического анализа | Обнаружение `flake-parts.lib.mkFlake` → рекурсивное разворачивание `imports`; тесты на flake-parts репозиториях | Активный (низкий) |

---

## 11. Приложение: сравнение версий

| Аспект | v1 | v2 | v3 |
|--------|----|----|-----|
| Nix-парсер | `nix-instantiate --parse` primary, tree-sitter fallback | `nix-instantiate --parse` primary, tree-sitter fallback | **Собственный Python-парсер primary**, `nix-instantiate` fallback |
| Хранение | SQLite + NetworkX параллельно | SQLite — источник истины, NetworkX — производный | SQLite — источник истины, NetworkX — производный |
| Модульная система NixOS | Частично | Полноценно (options/config/specialisation/_module.args) | Полноценно + flake-parts |
| Concurrency | Не описана | Не описана | **RWLock + deepcopy + generation_id** |
| Миграции БД | Отсутствуют | Отсутствуют | **PRAGMA user_version + миграции** |
| Изоляция плагинов | Отсутствует | Отсутствует | **try/catch + fallback до file-узла** |
| AST-diff | Мутатор графа | Отказ от мутации | **Только отчёт (логирование)** |
| generation_id | Отсутствует | Отсутствует | **Монотонный, во всех ответах** |
| sync_progress | Отсутствует | Отсутствует | **В status() при bulk-обновлении** |
| Модель ошибок парсинга | Нет | Частично (option_values.status) | **Полная: ParseError, fallback, parse_errors.log** |

---

*Версия: 3.0*
*Заменяет: repo-navigator-spec.md (v1), repo-navigator-spec-v2.md (v2)*
*Формат: Спецификация для AI-агента*
*Язык: ru / en*