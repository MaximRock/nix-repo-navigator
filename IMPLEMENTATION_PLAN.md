# repo-navigator — План реализации

> Пошаговый план имплементации по спецификации `repo-navigator-spec-v3.md`.
> Каждый шаг = одна задача. Зависимости между шагами указаны явно.

---

## Фаза 0: Подготовка

### 0.0. Git-инициализация
**Выполнить:** `git init` в `~/projects/repo-navigator/`

- Создать `.gitignore`:
  ```
  # Python
  __pycache__/
  *.py[cod]
  *.egg-info/
  .venv/
  venv/
  .pytest_cache/
  .coverage
  htmlcov/
  .mypy_cache/
  .ruff_cache/

  # repo-navigator runtime
  .repo-navigator/
  *.db
  *.db-wal
  *.db-shm
  parse_errors.log

  # IDE / OS
  .vscode/
  .idea/
  .DS_Store
  ```
- Первый коммит: спеки (`repo-navigator-spec.md`, `repo-navigator-spec-v2.md`, `repo-navigator-spec-v3.md`, `IMPLEMENTATION_PLAN.md`, `.gitignore`).
- Конвенция коммитов далее: по одному коммиту на завершённую фазу/подзадачу, сообщение вида `feat(models): add Node/Edge pydantic models`.

**Проверка:** `git status` показывает чистый трекинг; `git log` содержит первый коммит.

### 0.1. Инициализация проекта
**Создать:** `pyproject.toml`, `src/repo_navigator/__init__.py`, `tests/`

### 0.2. Как тестировать в процессе разработки (написал → проверил)

Три уровня проверки, от быстрого к полному:

**Уровень 1: «Прямо в терминале» (пишем код → сразу проверяем)**

Каждый модуль обязан иметь проверяемый фрагмент в `__main__` или через `cli.py dev`:

```bash
# Фаза 1: модели
python -c "from repo_navigator.models.nodes import Node; \
  n = Node(id='test', type='nix_module', name='test', lang='nix'); \
  print(n.model_dump_json(indent=2))"

# Фаза 1: БД
python -c "
from repo_navigator.graph.db import Database
db = Database(':memory:')
db.init_db()
db.upsert_node(...)
print(db.get_node('test'))
"

# Фаза 2: лексер
python -m repo_navigator.cli dev lex ~/.dotfiles/flake.nix
# выводит таблицу токенов

# Фаза 2: парсер
python -m repo_navigator.cli dev parse ~/.dotfiles/flake.nix
# выводит AST-дерево

# Фаза 3: извлечение
python -m repo_navigator.cli dev extract ~/.dotfiles/flake.nix
# выводит найденные imports/options/config/home.file

# Фаза 3: полный цикл одного файла
python -m repo_navigator.cli dev index ~/.dotfiles/flake.nix
# парсит, извлекает, строит граф в :memory: БД, выводит узлы и рёбра

# Фаза 5: индексация всего репозитория
python -m repo_navigator.cli refresh --root ~/.dotfiles
# полный rescan, выводит прогресс и итоговый размер графа

# Фаза 6: запросы к графу
python -m repo_navigator.cli query observe "nix:flake.nix"
python -m repo_navigator.cli query blast_radius "nix:modules/services/nginx.nix"
python -m repo_navigator.cli query find_symbol "nginx" --fuzzy
```

**Уровень 2: Golden-тесты (при изменении парсера/извлечения)**

```bash
# Прогон всех golden-тестов
pytest tests/golden/ -v

# Прогон конкретного теста
pytest tests/golden/lexer/test_lexer_golden.py -k "simple_let" -v

# После осознанного изменения парсера — обновить эталоны
pytest tests/golden/ --update-golden -v

# Сравнить diff вручную перед обновлением:
diff tests/golden/parser/simple_expected.json /tmp/actual.json
```

**Уровень 3: Полный тестовый набор (перед коммитом)**

```bash
# Все тесты
pytest tests/ -v

# С coverage
pytest tests/ -v --cov=repo_navigator --cov-report=term-missing

# Интеграционные (требуют nix в PATH)
pytest tests/integration/ -v
```

### 0.3. pyproject.toml и структура пакета

```toml
# pyproject.toml
[project]
name = "repo-navigator"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "networkx>=3.2",
    "pydantic>=2.5",
    "pydantic-settings>=2.1",
    "typer>=0.9",
    "watchdog>=4.0",
    "xxhash>=3.4",
    "gitpython>=3.1",
    "mcp>=1.0",
]
[project.optional-dependencies]
plugins = [
    "tree-sitter>=0.21",
    "tree-sitter-languages>=1.10",
    "kdl-py>=1.0",
]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]
[project.scripts]
repo-navigator = "repo_navigator.cli:app"
```

**Структура `tests/`:**
```
tests/
├── __init__.py
├── conftest.py                  # фикстуры: тестовая БД, temp_dir, sample .nix файлы
├── golden/                      # golden-тесты парсера
│   ├── lexer/                   # тесты лексера
│   │   ├── simple_let.nix
│   │   ├── simple_let_expected.json
│   │   ├── strings.nix
│   │   ├── strings_expected.json
│   │   └── ...
│   ├── parser/                  # тесты парсера
│   │   ├── flat_attrs.nix
│   │   ├── flat_attrs_expected.json
│   │   ├── nested_imports.nix
│   │   ├── nested_imports_expected.json
│   │   └── ...
│   └── extract/                 # тесты ast_extract + module_parser (фаза 3)
│       ├── mkoption_simple.nix
│       ├── mkoption_simple_expected.json
│       ├── mkif_conditional.nix
│       ├── mkif_conditional_expected.json
│       └── ...
├── fixtures/                    # тестовые Nix-репозитории для интеграционных тестов
│   └── minimal-flake/
│       ├── flake.nix
│       ├── flake.lock
│       ├── modules/
│       │   └── test.nix
│       └── expected_graph.json  # ожидаемый граф после полного скана
├── unit/                        # обычные unit-тесты (не golden)
│   ├── test_models.py           # Pydantic-модели: валидация, сериализация
│   ├── test_db.py               # SQLite CRUD: upsert/get/delete/FTS5
│   ├── test_hash_engine.py      # content_hash, ast_hash, merkle_hash
│   └── test_builder.py          # построение графа из ParseResult
└── integration/                 # интеграционные тесты (фаза 10)
    ├── test_full_cycle.py       # изменение файла → обновление графа → query
    ├── test_mcp_tools.py        # все 11 MCP-инструментов
    └── test_git_hooks.py        # post-checkout обновление
```
**Проверка:** `python -c "import repo_navigator; print(repo_navigator.__version__)"`

---

## Фаза 1: Фундамент (Неделя 1)

### 1.1. Pydantic-модели
**Зависит от:** 0.1
**Создать:** `src/repo_navigator/models/`

Файлы:
- `models/__init__.py` — реэкспорт всех моделей
- `models/nodes.py` — `Node`, `NodeType` (StrEnum: nix_module, nix_option, nix_function, flake_input, package_ref, py_function, py_class, qtile_key, qtile_hook, kdl_bind, kdl_rule, kdl_spawn, sh_function, sh_command_call, lua_function, lua_require, vim_keymap, toml_section, toml_key, json_key, file, heading)
- `models/edges.py` — `Edge`, `EdgeType` (StrEnum: imports, declares, sets, specialises, passes_args, configures, generates, uses_package, python_imports, calls, binds_key, spawns, requires, sources, references)
- `models/file_state.py` — `FileState` (path, lang, content_hash, ast_hash, merkle_hash, dirty, last_parsed, detail_level)
- `models/option_value.py` — `OptionValue` (key, expr, value_json, status, error, computed_at, source_rev)
- `models/queries.py` — модели ответов:
  - `Observation` (node, neighbors: list[(Edge, Node)], generation_id)
  - `Subgraph` (nodes, edges, generation_id)
  - `PathStep` (node, edge_in, depth)
  - `OptionInfo` (option_path, opt_type, default, example, description, declared_in, defined_in, conditional_sets, value, value_status, generation_id)
  - `EvalResult` (expr, value_json, status, error, cached, generation_id)
  - `ImpactReport` (target, affected_modules, affected_options, affected_files, risk_level, generation_id)
  - `ModuleSummary` (path, incoming_edges, outgoing_edges, key_symbols, generation_id)
  - `StatusResponse` (mode, total_nodes, total_edges, uptime, sync_progress, generation_id)
  - `ParseResult` (nodes: list[RawNode], edges: list[RawEdge]) — для внутреннего использования
  - `RawNode`, `RawEdge` — промежуточные структуры от парсеров

**Проверка:** все модели импортируются без ошибок, `Node.model_validate(...)` работает

### 1.2. Конфигурация
**Зависит от:** 0.1
**Создать:** `src/repo_navigator/config.py`

```python
class Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REPO_NAVIGATOR_")
    
    root: Path = Path.cwd()
    plugins: list[str] = []          # [] = только Nix
    db_path: Path | None = None      # по умолчанию <root>/.repo-navigator/repo-navigator.db
    budgets: dict = {"width": 10, "depth": 5, "limit": 10}
    timeouts: dict = {"nix_eval": 60, "debounce_ms": 500, "polling_s": 60}
    watcher_mode: str = "auto"       # auto | inotify | polling
    log_level: str = "INFO"
```

Загрузка: env vars → `.env` файл → CLI-аргументы (через Typer callback).

### 1.3. SQLite схема + db.py CRUD
**Зависит от:** 1.1, 1.2
**Создать:** `src/repo_navigator/graph/__init__.py`, `src/repo_navigator/graph/db.py`

**`db.py` методы:**
```
init_db(db_path: Path) -> None
  — PRAGMA journal_mode=WAL;
  — PRAGMA foreign_keys=ON;
  — Читает PRAGMA user_version
  — Если 0: создаёт ВСЕ таблицы (nodes, edges, file_state, flake_inputs,
    package_index, option_values, generation, node_search FTS5)
  — Иначе: применяет миграции до CURRENT_SCHEMA_VERSION
  — Инициализирует generation: INSERT OR IGNORE ... value=0

# GENERATION
get_generation_id() -> int
inc_generation_id() -> int  # UPDATE generation SET value = value + 1 RETURNING value

# NODES
upsert_node(node: Node) -> None
get_node(id: str) -> Node | None
delete_file_nodes(path: str) -> None  # удаляет узлы И рёбра для файла
get_all_nodes() -> list[Node]

# EDGES
upsert_edge(edge: Edge) -> None
get_edges_for_node(node_id: str) -> list[Edge]
get_edges_for_file(path: str) -> list[Edge]
get_all_edges() -> list[Edge]

# FILE_STATE
upsert_file_state(fs: FileState) -> None
get_file_state(path: str) -> FileState | None
get_dirty_files() -> list[str]
mark_dirty(path: str) -> None
mark_clean(path: str) -> None

# FLAKE_INPUTS
upsert_flake_input(name, url, rev) -> None
get_flake_inputs() -> list[dict]

# PACKAGE_INDEX
upsert_package(attr, name, version, store_path, meta) -> None
get_packages() -> list[dict]

# OPTION_VALUES
upsert_option_value(ov: OptionValue) -> None
get_option_value(key: str) -> OptionValue | None
invalidate_option_values(file_paths: list[str]) -> None
invalidate_all_option_values() -> None

# SEARCH
search_fts5(query: str, limit: int = 10) -> list[Node]
```

**Миграции:**
```python
CURRENT_SCHEMA_VERSION = 1
MIGRATIONS: dict[int, str] = {
    # 1: "ALTER TABLE ...",  # пример будущей миграции
}
```

**Проверка:** `pytest tests/unit/test_db.py -v` — создание БД в `:memory:`, upsert/get/delete узлов и рёбер, поиск FTS5, миграции схемы

### 1.4. NetworkX обёртка (базовая)
**Зависит от:** 1.3
**Создать:** `src/repo_navigator/graph/nx_graph.py`

```python
class NxGraph:
    def __init__(self):
        self._graph: nx.DiGraph = nx.DiGraph()
        self._lock = threading.RWLock()  # или свой простой RWLock
    
    def rebuild(self, nodes: list[Node], edges: list[Edge]) -> None
        # Полная перестройка из SQLite (холодный старт)
        # Захватывает write-lock
    
    def apply_delta(self, added_nodes, removed_ids, added_edges, removed_ids) -> None
        # Дельта-обновление (write-lock)
    
    def get_graph_readonly(self) -> nx.DiGraph
        # deepcopy графа под read-lock
        # Для долгих обходов — не держит lock
    
    def bfs(self, source, depth, width) -> list[Node]
    def shortest_path(self, source, target) -> list[PathStep]
    def reverse_bfs(self, source, max_depth) -> list[Node]
```

### 1.5. CLI (базовый)
**Зависит от:** 1.2, 1.3
**Создать:** `src/repo_navigator/cli.py`

```python
app = typer.Typer()

@app.command()
def start(root: str | None = None):
    """Запуск MCP-сервера (заглушка)"""
    pass

@app.command()
def status(root: str | None = None):
    """Вывод статуса: размер графа, режим"""
    pass

@app.command()
def refresh(root: str | None = None):
    """Принудительный rescan (заглушка)"""
    pass
```

**Проверка:** `python -m repo_navigator.cli status` выводит заглушку

---

## Фаза 2: Nix Lexer + Parser (Неделя 2)

### 2.1. Лексер
**Зависит от:** 1.1
**Создать:** `src/repo_navigator/parsers/__init__.py`, `src/repo_navigator/parsers/nix/__init__.py`, `src/repo_navigator/parsers/nix/lexer.py`

Токены (перечислить как Enum):
```
IDENT, STRING_SINGLE, STRING_DOUBLE, STRING_HEREDOC,
INT, FLOAT, PATH, URI,
LBRACE, RBRACE, LBRACK, RBRACK, LPAREN, RPAREN,
DOT, SEMI, COLON, EQ, ELLIPSIS, DOLLAR,
LAMBDA, ARROW, OR, IMPL, QUESTION,
KW_LET, KW_IN, KW_IF, KW_THEN, KW_ELSE,
KW_WITH, KW_ASSERT, KW_REC, KW_INHERIT,
COMMENT_SINGLE, COMMENT_MULTI,
INTERPOL_START, INTERPOL_END,
```

Функции:
- `tokenize(source: str) -> list[Token]`
- `Token`: type, value, line, col

Алгоритм: один проход по символам, switch по первому символу.
Обрабатывать: escape-последовательности в строках, `${...}` как отдельные токены INTERPOL_START/END.

**Проверка:** golden-тесты: 15+ .nix файлов разной сложности → список токенов совпадает с `_expected.json`. Запуск: `pytest tests/golden/lexer/ -v`

### 2.2. Парсер
**Зависит от:** 2.1
**Создать:** `src/repo_navigator/parsers/nix/parser.py`

Рекурсивный спуск. Узлы AST:
```
Expr (базовый класс)
├── Literal (значение, тип)
├── AttrSet (recursive: bool, attrs: list[AttrDef])
│   └── AttrDef (name: str | Inherit, value: Expr | None)
├── AttrPath (path: list[str], value: Expr)
├── List (items: list[Expr])
├── Function (arg: str | AttrSet, body: Expr)
├── FunctionCall (func: Expr, arg: Expr)
├── LetIn (bindings: list[AttrDef], body: Expr)
├── IfThenElse (cond, then_, else_)
├── With (expr, body)
├── Assert (assertion, body)
├── BinaryOp (left, op, right)  # //, ->, &&, ||, ?, +, -, *, /, ++, сравнения
├── UnaryOp (op, expr)          # !, -
├── Interpolation (parts: list[Expr | str])
├── Inherit (from_: Expr | None, names: list[str])
├── Pin (name: str)             # <name>
└── UnresolvedExpr (source: str, reason: str)
```

Точка входа: `parse(source: str) -> Expr`

Приоритеты операторов (от низшего к высшему):
1. `//` (merge)
2. `->` (implies)
3. `||` (or)
4. `&&` (and)
5. `!` (not)
6. Сравнения: `<`, `>`, `<=`, `>=`, `==`, `!=`
7. `?` (has-attr)
8. `+`, `-`
9. `*`, `/`
10. `++` (concat)
11. Унарный `-`

Не парсим (возвращаем UnresolvedExpr): всё внутри `${...}`, вызовы `builtins.*`.

**Проверка:** golden-тесты: 20+ .nix файлов → AST соответствует `_expected.json`. Запуск: `pytest tests/golden/parser/ -v`

### 2.3. Fallback nix-instantiate
**Зависит от:** 2.2
**Создать:** `src/repo_navigator/parsers/nix/nix_instantiate.py`

```python
def parse_via_nix_instantiate(path: Path) -> dict | None:
    """Вызывает nix-instantiate --parse --json <path>, возвращает JSON-AST"""
    # Проверяет shutil.which("nix-instantiate")
    # subprocess.run с таймаутом 10s
    # При ошибке → None
```

---

## Фаза 3: ast_extract + module_parser (Неделя 3)

### 3.1. ast_extract
**Зависит от:** 2.2, 2.3
**Создать:** `src/repo_navigator/parsers/nix/ast_extract.py`

Обходит AST (свой или от nix-instantiate) и возвращает плоский список:

```python
@dataclass
class ExtractedNix:
    imports: list[ImportDecl]          # {path, line, conditional}
    options: list[OptionDecl]          # {attrpath, type, default, example, description, line}
    configs: list[ConfigSet]           # {attrpath, value_expr, conditional, priority, line}
    specialisations: list[Specialisation]  # {name, config_file, line}
    module_args: list[ModuleArg]       # {name, line}
    home_files: list[HomeFile]         # {target, source, line}
    packages: list[PackageRef]         # {attribute, line}
    functions: list[FunctionDecl]      # {name, args, line}
    unresolved: list[UnresolvedRef]    # {location, reason}
```

Алгоритм обхода:
1. Рекурсивно спускаемся по AttrSet/AttrPath
2. Ищем attrpath-префиксы: `options.*`, `config.*`, `home.file`, `xdg.configFile`, `home.packages`, `specialisation.*`, `_module.args.*`
3. Для `config.*` внутри mkIf/mkMerge: `conditional=True`
4. Собираем все `imports = [...]`, включая условные (внутри mkIf)
5. Обнаруживаем `flake-parts.lib.mkFlake` → собираем `imports` внутри него

**Проверка:** golden-тесты на 15+ реальных NixOS модулях → корректное извлечение всех категорий. Запуск: `pytest tests/golden/extract/ -v`

### 3.2. module_parser
**Зависит от:** 3.1
**Создать:** `src/repo_navigator/parsers/nix/module_parser.py`

Преобразует `ExtractedNix` → `ParseResult` (RawNode + RawEdge):

```python
def parse_module(file_path: Path, extracted: ExtractedNix) -> ParseResult:
    # Создаёт nix_module узел для самого файла
    # Для каждого import: ребро imports к другому nix_module
    # Для каждого option: узел nix_option + ребро declares
    # Для каждого config: ребро sets к nix_option
    # Для каждого specialisation: ребро specialises
    # Для каждого module_arg: ребро passes_args
    # Для каждого home_file: ребро configures к file-узлу
    # Для каждого package: узел package_ref + ребро uses_package
    # Для каждой функции: узел nix_function
    # Приоритеты mkForce/mkDefault → edge.metadata.priority
    # Условные присваивания → edge.metadata.conditional
```

**Проверка:** на том же наборе golden-тестов → корректные RawNode/RawEdge

### 3.3. NixParser (объединяющий)
**Зависит от:** 3.2
**Создать:** `src/repo_navigator/parsers/nix_parser.py` (на уровне parsers/, не nix/)

Реализует `BaseParser.parse(path, content) -> ParseResult`:
1. Пробует свой lexer + parser
2. Если UnresolvedExpr > 50% — пробует nix-instantiate fallback
3. Прогоняет через ast_extract → module_parser
4. Возвращает ParseResult
5. Ошибки НЕ роняет — файл получает хотя бы узел `nix_module` без рёбер

---

## Фаза 4: Parser Registry + Builder (Неделя 3, продолжение)

### 4.1. Parser Registry
**Зависит от:** 3.3
**Создать:** `src/repo_navigator/parsers/base.py`, `src/repo_navigator/parsers/registry.py`

```python
# base.py
class BaseParser(ABC):
    language: LanguageConfig
    
    @abstractmethod
    def parse(self, path: Path, content: str) -> ParseResult: ...

# registry.py
@dataclass
class LanguageConfig:
    name: str
    extensions: list[str]
    tier: int  # 0, 1, 2, 3
    enabled: bool = True

def register_language(config: LanguageConfig):
    """Декоратор для регистрации парсера"""
    
def get_parser_for_file(path: str) -> BaseParser | None: ...
def should_parse_file(path: str, graph: NxGraph) -> bool:
    """Nix-first правило: парсим если есть ребро configures/generates ИЛИ .config/"""
```

### 4.2. Builder
**Зависит от:** 1.3, 4.1
**Создать:** `src/repo_navigator/graph/builder.py`

```python
class GraphBuilder:
    def __init__(self, db: Database, nx_graph: NxGraph):
        ...
    
    def build_file(self, path: Path, parse_result: ParseResult) -> None:
        """Полная замена подграфа файла"""
        # 1. db.delete_file_nodes(path)
        # 2. Для каждого RawNode → Node → db.upsert_node()
        # 3. Для каждого RawEdge → Edge → db.upsert_edge()
        # 4. nx_graph.apply_delta(added, removed, ...)
        # 5. db.inc_generation_id()
    
    def build_all(self, paths: list[Path]) -> None:
        """Полный rescan репозитория"""
```

---

## Фаза 5: Инкрементальность (Неделя 4)

### 5.1. Hash Engine
**Зависит от:** 1.3
**Создать:** `src/repo_navigator/indexer/__init__.py`, `src/repo_navigator/indexer/hash_engine.py`

```python
def content_hash(content: str | bytes) -> str:
    return xxhash.xxh64(content).hexdigest()

def ast_hash(parse_result: ParseResult) -> str:
    # Сортирует узлы по id, удаляет content_hash,
    # сериализует в JSON (sorted keys), хеширует

def merkle_hash(file_ast_hash: str, dependency_hashes: list[str]) -> str:
    combined = file_ast_hash + "".join(sorted(dependency_hashes))
    return hashlib.sha256(combined.encode()).hexdigest()
```

### 5.2. Event Router
**Зависит от:** 1.2
**Создать:** `src/repo_navigator/indexer/event_router.py`

```python
class EventRouter:
    queue: asyncio.Queue[list[str]]  # batch из путей
    _pending: dict[str, float]        # path → debounce_timer
    
    async def on_file_event(self, event: FileSystemEvent):
        # Сбрасывает таймер для path
        # Через 500ms (config.timeouts.debounce_ms):
        #   добавляет path в batch
        #   кладёт batch в queue
    
    async def on_git_hook(self, changed_files: list[str]):
        # Bulk: сразу кладёт в queue (без debounce)
        # Если >50 файлов: ставит флаг sync_in_progress
```

### 5.3. Update Engine
**Зависит от:** 1.3, 4.2, 5.1, 5.2
**Создать:** `src/repo_navigator/indexer/update_engine.py`

```python
class UpdateEngine:
    async def run(self):
        while True:
            batch = await event_router.queue.get()
            for path in batch:
                await self.process_file(path)
            await self.cascade_dirty()
    
    async def process_file(self, path: str):
        # 1. content = read(path)
        # 2. new_hash = content_hash(content)
        # 3. old_state = db.get_file_state(path)
        # 4. if old_state and new_hash == old_state.content_hash: return
        # 5. parse_result = registry.parse(path)
        # 6. new_ast_hash = ast_hash(parse_result)
        # 7. if old_state and new_ast_hash == old_state.ast_hash:
        #       db.upsert_file_state(content_hash=new_hash)  # только форматирование
        #       return
        # 8. builder.build_file(path, parse_result)
        # 9. db.upsert_file_state(path, content_hash=new_hash, ast_hash=new_ast_hash,
        #                         merkle_hash=..., dirty=0)
        # 10. Возвращает список импортёров для cascade
```

### 5.4. Cascade Engine
**Зависит от:** 1.3, 5.3
**Создать:** `src/repo_navigator/indexer/cascade.py`

```python
async def cascade_dirty(db, update_engine, changed_file: str):
    # 1. Найти всех прямых импортёров changed_file (reverse edge imports)
    # 2. Для каждого: пометить dirty=1 в file_state
    # 3. Пересчитать merkle_hash (его собственный ast_hash + хеши зависимостей)
    # 4. Рекурсивно для каждого dirty-импортёра (топологическая сортировка)
    # 5. Собрать список затронутых .nix файлов
    # 6. Вызвать db.invalidate_option_values(affected_nix_files)
```

### 5.5. Diff Engine (только отчёт)
**Зависит от:** 4.2
**Создать:** `src/repo_navigator/indexer/diff_engine.py`

```python
def diff_graph(old: ParseResult, new: ParseResult) -> DiffReport:
    # Сравнивает множества RawNode/RawEdge по id
    # Возвращает: added_nodes, removed_nodes, changed_nodes,
    #   added_edges, removed_edges
    # ТОЛЬКО для логирования. Не мутирует граф.
```

### 5.6. File Watcher
**Зависит от:** 1.2, 5.2
**Создать:** `src/repo_navigator/watcher/__init__.py`, `src/repo_navigator/watcher/filesystem.py`

```python
class FileWatcher:
    def start(self, root: Path, event_router: EventRouter):
        # Автоопределение режима:
        #   пробуем watchdog.observers.Observer
        #   при ошибке → mtime polling
        # Игнорировать: .git/, .repo-navigator/, __pycache__, *.pyc
        # Все изменения → event_router.on_file_event()
```

---

## Фаза 6: Query Engine (Неделя 5)

### 6.1. Навигационные глаголы
**Зависит от:** 1.3, 1.4, 5.3
**Создать:** `src/repo_navigator/graph/queries.py`

```python
class QueryEngine:
    def __init__(self, db: Database, nx_graph: NxGraph):
        self._cache = {}  # LRU
    
    def observe(self, node_id: str, depth: int = 1) -> Observation:
        # Соседи на расстоянии depth (макс 20)
        # read-lock → nx_graph.neighbors(node_id)
    
    def hop(self, node_id: str, relation: str | None,
            depth: int, width: int) -> Subgraph:
        # BFS с фильтром по типу ребра
        # Бюджет: width*depth ≤ 100
    
    def path(self, source: str, target: str) -> list[PathStep]:
        # Dijkstra на deepcopy графа
        # Возвращает кратчайший путь
    
    def blast_radius(self, node_id: str, max_depth: int = 5) -> Subgraph:
        # Reverse BFS: кто зависит от этого узла
        # max_depth ограничивает глубину обратного обхода
    
    def find_symbol(self, query: str, lang: str | None = None,
                    fuzzy: bool = True, limit: int = 10) -> list[Node]:
        # FTS5 если не fuzzy; триграммы (LIKE) если fuzzy
    
    def summarize_module(self, path: str) -> ModuleSummary:
        # Все входящие и исходящие рёбра узла nix_module по этому пути
        # Ключевые символы (options → функции → keybindings)
```

### 6.2. Introspection + Eval
**Зависит от:** 6.1
**Добавить в `queries.py`:**

```python
    def introspect_option(self, option_path: str,
                          include_value: bool = False) -> OptionInfo:
        # Статическая декларация: ищем nix_option узел → metadata
        # Если include_value: проверяем eval_cache
        #   hit → значение из кэша
        #   miss → nix eval (лениво, таймаут)
    
    def eval_expression(self, expr: str, timeout: int = 60) -> EvalResult:
        # Проверка кэша
        # Если нет: nix eval --json --impure --expr "expr"
        # Сохраняем в option_values
    
    def impact_analysis(self, node_id: str,
                        max_depth: int = 5) -> ImpactReport:
        # blast_radius + для каждого затронутого модуля:
        #   - какие опции он sets
        #   - какие файлы он configures/generates
        #   - оценка риска: low/medium/high (по числу затронутых)
```

### 6.3. Status + Refresh
**Добавить в `queries.py`:**

```python
    def status(self) -> StatusResponse:
        # Режим: static/hybrid (проверка shutil.which("nix"))
        # Размер: db.count_nodes(), db.count_edges()
        # sync_progress: (обработано, всего) при bulk-обновлении
    
    def refresh(self) -> StatusResponse:
        # Полный rescan: обойти все файлы, builder.build_all()
        # Возвращает StatusResponse после завершения
```

---

## Фаза 7: MCP Server (Неделя 6)

### 7.1. MCP Server
**Зависит от:** 6.1, 6.2, 6.3
**Создать:** `src/repo_navigator/mcp_server.py`

```python
# 11 инструментов, каждый → метод QueryEngine
mcp = FastMCP("repo-navigator")

@mcp.tool()
async def repo_navigator_observe(node_id: str, depth: int = 1) -> Observation:
    return engine.observe(node_id, depth)

@mcp.tool()
async def repo_navigator_hop(node_id: str, relation: str | None,
                              depth: int, width: int) -> Subgraph:
    return engine.hop(node_id, relation, depth, width)

# ... остальные 9 инструментов аналогично

# Запуск: python -m repo_navigator.mcp_server
# Транспорт: stdio (JSON-RPC)
```

**Проверка:** MCP Inspector, тестовый клиент

---

## Фаза 8: Nix Eval + Кэш (Неделя 7)

### 8.1. nix eval обёртка
**Зависит от:** 1.2
**Создать:** `src/repo_navigator/nix/__init__.py`, `src/repo_navigator/nix/eval.py`

```python
def nix_available() -> bool:
    return shutil.which("nix") is not None

async def nix_eval(expr: str, timeout: int = 60) -> dict | None:
    """nix eval --json --impure --expr <expr>"""
    # asyncio.create_subprocess_exec
    # Таймаут через asyncio.wait_for
    # Парсит JSON-ответ
    # Ошибка/таймаут → None

async def nix_eval_option(attrpath: str, timeout: int = 60) -> dict | None:
    """Для NixOS: nix eval --json .#nixosConfigurations.<name>.config.<attrpath>"""
```

### 8.2. Eval Cache
**Зависит от:** 1.3, 8.1
**Создать:** `src/repo_navigator/nix/eval_cache.py`

```python
class EvalCache:
    def get(self, expr: str) -> OptionValue | None:
        # Проверяет: status != stale, source_rev совпадает с текущим flake.lock rev
    
    async def get_or_eval(self, expr: str, timeout: int = 60) -> EvalResult:
        # cache hit → возвращает сразу
        # cache miss → nix_eval → сохраняет → возвращает
    
    def invalidate_for_files(self, paths: list[str]):
        # Помечает stale все записи, где expr затрагивает любой из путей
    
    def invalidate_all(self):
        # Все записи → stale (после nix flake update)
```

### 8.3. Flake Tracker
**Зависит от:** 1.3, 8.2
**Создать:** `src/repo_navigator/nix/flake_tracker.py`

```python
class FlakeTracker:
    async def run(self):
        # Каждые 30s: проверяет flake.lock mtime
        # При изменении: парсит JSON, обновляет flake_inputs
        # Вызывает eval_cache.invalidate_all()
```

### 8.4. Package Index
**Зависит от:** 1.3
**Создать:** `src/repo_navigator/nix/package_index.py`

```python
async def update_package_index(db, packages: list[PackageRef]):
    # Для каждого package_ref:
    #   если nix доступен: nix eval --json nixpkgs#<attr>.meta
    #   upsert в package_index
```

---

## Фаза 9: Home-manager слой (Неделя 8)

### 9.1. Home-manager переходы
**Зависит от:** 3.2, 4.2
**Добавить в `module_parser.py`:**

- Обнаружение `home.file."<target>".source` → ребро `configures` к файловому узлу (даже если файл ещё не проиндексирован)
- Обнаружение `xdg.configFile."<target>".source` → аналогично
- Обнаружение `programs.<name>.extraConfig` → ребро `generates` к виртуальному файловому узлу
- Обнаружение `programs.<name>.package` → ребро `uses_package`

### 9.2. Отслеживание сгенерированных файлов
- Файлы из `home.file` и `xdg.configFile` → watcher добавляет их в индекс
- При изменении: стандартный цикл update_engine

---

## Фаза 10–12: Завершение

### 10. Git hooks (Неделя 10)
**Создать:** `src/repo_navigator/watcher/git_hooks.py`

```python
def install(repo_path: Path):
    # Записывает post-checkout и post-merge в .git/hooks/
    # post-checkout: сравнивает prev HEAD и new HEAD (git diff --name-only)
    #   → вызывает event_router.on_git_hook(changed_files)

def uninstall(repo_path: Path):
    # Удаляет установленные хуки
```

### 11. Тестирование (Неделя 11)

#### 11.1. Что такое golden-тест и как он работает

**Принцип:** берём реальный `.nix` файл → прогоняем через тестируемый компонент → сравниваем вывод с эталонным JSON'ом. Если вывод изменился (из-за правок в парсере) — тест падает, разработчик вручную проверяет разницу и либо чинит парсер, либо обновляет эталон.

**Формат:**
```
tests/golden/<компонент>/
├── <имя_теста>.nix              # входной файл (реальный Nix-код)
├── <имя_теста>_expected.json    # эталонный вывод
└── <имя_теста>_error.txt        # (опционально) ожидаемая ошибка парсинга
```

**Механика прогона (conftest.py):**

```python
# tests/conftest.py
import pytest
import json
from pathlib import Path

GOLDEN_DIR = Path(__file__).parent / "golden"

def run_golden_test(component: str, test_name: str, runner_fn):
    """
    Универсальная функция для golden-тестов.
    
    Аргументы:
        component: "lexer", "parser", "extract"
        test_name: имя теста (без расширения)
        runner_fn: функция, принимающая содержимое .nix файла
                   и возвращающая dict | list (сериализуемый результат)
    """
    golden_path = GOLDEN_DIR / component
    input_file = golden_path / f"{test_name}.nix"
    expected_file = golden_path / f"{test_name}_expected.json"
    
    source = input_file.read_text()
    result = runner_fn(source)
    actual_json = json.dumps(result, indent=2, sort_keys=True, default=str)
    
    if expected_file.exists():
        expected = expected_file.read_text()
        assert actual_json.strip() == expected.strip(), (
            f"Golden test '{test_name}' failed.\n"
            f"Diff actual vs expected. To update expected, run:\n"
            f"  pytest tests/ --update-golden -k {test_name}"
        )
    else:
        # Первый прогон: создаём expected
        expected_file.write_text(actual_json)
        pytest.skip(f"Created expected file for '{test_name}'")
```

**Обновление эталонов:**
```bash
# После осознанного изменения парсера — обновить эталоны:
pytest tests/golden/ --update-golden
# Эта опция перезаписывает _expected.json актуальным выводом.
```

#### 11.2. Golden-тесты лексера (фаза 2, ~15 тестов)

Проверяют: строка исходного кода → список токенов.

**Набор тестов:**

| Тест | Что проверяет |
|------|--------------|
| `simple_let` | `let x = 1; in x` — базовые токены (KW_LET, IDENT, EQ, INT, SEMI, KW_IN) |
| `strings` | Одинарные `'hello'`, двойные `"world"`, heredoc `''line1\nline2''` |
| `interpolation` | `"Hello ${name}!"` — STRING с INTERPOL_START/INTERPOL_END |
| `path` | `./modules/test.nix`, `/absolute/path` — PATH токены |
| `uri` | `https://github.com/nixos/nixpkgs` — URI токены |
| `comments` | `# single line`, `/* multi\nline */` — COMMENT токены пропускаются |
| `nested_braces` | `{ a = { b = 1; }; }` — LBRACE/RBRACE с правильной вложенностью |
| `list` | `[ 1 2 (3+4) ]` — LBRACK, INT, INT, LPAREN, INT, BINOP, INT, RPAREN, RBRACK |
| `function` | `x: y: x + y` — IDENT, COLON, IDENT, COLON, IDENT, BINOP, IDENT |
| `operators` | `//`, `->`, `&&`, `||`, `!`, `?`, `++` — все операторы |
| `keywords` | `if then else`, `with`, `assert`, `rec`, `inherit` — все ключевые слова |
| `float_int` | `3.14`, `1e10`, `0xff` — числа во всех форматах |
| `inherit` | `inherit (pkgs) ripgrep fd;` — INHERIT, LPAREN, IDENT, RPAREN, IDENT, IDENT, SEMI |
| `lambda_destructure` | `{ config, lib, ... }@args:` — деструктуризация с ELLIPSIS и PIN |
| `flake_parts_basic` | Реальный `flake.nix` из `~/.dotfiles` — интеграционный тест лексера |

**Формат expected.json для лексера:**
```json
[
  {"type": "KW_LET", "value": "let",    "line": 1, "col": 1},
  {"type": "IDENT",   "value": "x",     "line": 1, "col": 5},
  {"type": "EQ",      "value": "=",     "line": 1, "col": 7},
  {"type": "INT",     "value": "1",     "line": 1, "col": 9},
  {"type": "SEMI",    "value": ";",     "line": 1, "col": 10},
  {"type": "KW_IN",   "value": "in",    "line": 1, "col": 12},
  {"type": "IDENT",   "value": "x",     "line": 1, "col": 15},
  {"type": "EOF",     "value": "",      "line": 1, "col": 16}
]
```

#### 11.3. Golden-тесты парсера (фаза 2, ~20 тестов)

Проверяют: список токенов → AST-дерево.

**Набор тестов:**

| Тест | Что проверяет |
|------|--------------|
| `flat_attrs` | `{ a = 1; b = 2; }` → AttrSet с простыми значениями |
| `nested_attrs` | `{ a.b.c = 1; }` → AttrPath с вложенностью |
| `rec_attrs` | `rec { a = 1; b = a + 1; }` → рекурсивный AttrSet |
| `function_simple` | `x: x + 1` → Function с BinaryOp в теле |
| `function_multi` | `x: y: x + y` → Function(Function(...)) |
| `function_destructure` | `{ config, lib, ... }: config.a` — деструктуризация аргумента |
| `function_call` | `mkOption { type = types.bool; }` — FunctionCall |
| `let_in` | `let x = 1; y = 2; in x + y` — LetIn с bindings |
| `if_then_else` | `if enabled then "yes" else "no"` — IfThenElse |
| `with_expr` | `with pkgs; [ ripgrep fd ]` — With |
| `assert_expr` | `assert x > 0; x` — Assert |
| `list_concat` | `[ 1 2 ] ++ [ 3 4 ]` — BinaryOp с `++` |
| `merge` | `a // b` — BinaryOp с `//` |
| `implication` | `x: x -> x >= 0` — Function с `->` |
| `question` | `x ? a` — BinaryOp с `?` |
| `inherit_simple` | `{ inherit a b; }` — Inherit без from |
| `inherit_from` | `{ inherit (pkgs) ripgrep; }` — Inherit с from |
| `string_interp` | `"Hello ${name}!"` → Interpolation AST-узел |
| `heredoc_interp` | `''line1 ''${var}'' line2''` → Interpolation в heredoc |
| `imports_list` | `imports = [ ./a.nix ./b.nix ];` — репрезентативный тест для imports |

**Формат expected.json для парсера:**
```json
{
  "type": "AttrSet",
  "recursive": false,
  "attrs": [
    {
      "name": "imports",
      "value": {
        "type": "List",
        "items": [
          {"type": "Literal", "value_type": "path", "value": "./a.nix"},
          {"type": "Literal", "value_type": "path", "value": "./b.nix"}
        ]
      }
    }
  ]
}
```

#### 11.4. Golden-тесты ast_extract + module_parser (фаза 3, ~15 тестов)

Проверяют: реальный .nix файл → `ParseResult` (RawNode + RawEdge).

**Набор тестов (используем модули из `~/.dotfiles` и nixpkgs):**

| Тест | Что проверяет |
|------|--------------|
| `imports_chain` | `a.nix` импортирует `b.nix` → ребро `imports` |
| `mkoption_simple` | `options.services.foo.enable = mkOption { type = types.bool; }` → узел `nix_option` + ребро `declares` |
| `mkoption_meta` | mkOption с `default`, `example`, `description` → metadata заполнены |
| `config_set` | `config.services.foo.enable = true` → ребро `sets` |
| `config_set_unconditional` | Присваивание вне mkIf → `conditional=False` |
| `config_set_conditional` | Присваивание внутри `mkIf cfg.enable { ... }` → `conditional=True` |
| `config_set_mkmerge` | Присваивание внутри `mkMerge [ ... ]` → `conditional=True` |
| `mkforce_priority` | `mkForce true` → `edge.metadata.priority = "force"` |
| `mkdefault_priority` | `mkDefault 42` → `edge.metadata.priority = "default"` |
| `specialisation` | `specialisation.desktop.configuration = { ... }` → ребро `specialises` |
| `module_args` | `_module.args.myLib = ...` → ребро `passes_args` |
| `home_file` | `home.file.".config/foo".source = ./foo` → ребро `configures` |
| `home_packages` | `home.packages = [ pkgs.ripgrep ]` → узел `package_ref` + ребро `uses_package` |
| `flake_parts` | `flake-parts.lib.mkFlake { imports = [ ./modules ]; }` → обнаружение imports |
| `dynamic_import` | `imports = [ (import ./auto.nix) ]` → `UnresolvedRef` |
| `unresolved_interp` | `"${pkgs}/bin/foo"` → `UnresolvedRef` |

**Формат expected.json для ast_extract:**
```json
{
  "nodes": [
    {
      "id": "nix:modules/foo.nix",
      "type": "nix_module",
      "name": "modules/foo.nix",
      "path": "modules/foo.nix",
      "lang": "nix"
    },
    {
      "id": "nix_option:services.foo.enable",
      "type": "nix_option",
      "name": "services.foo.enable",
      "lang": "nix",
      "metadata": {
        "opt_type": "types.bool",
        "default": "false",
        "description": "Whether to enable foo."
      }
    }
  ],
  "edges": [
    {
      "source": "nix:modules/foo.nix",
      "target": "nix_option:services.foo.enable",
      "type": "declares",
      "metadata": {}
    }
  ]
}
```

#### 11.5. Unit-тесты (пишутся параллельно с кодом)

| Файл | Что тестирует | Фаза |
|------|--------------|------|
| `tests/unit/test_models.py` | Pydantic-валидация всех моделей, сериализация в JSON | 1.1 |
| `tests/unit/test_db.py` | CRUD: upsert_node, get_node, delete_file_nodes, FTS5-поиск, миграции схемы | 1.3 |
| `tests/unit/test_hash_engine.py` | content_hash одинаков для одинакового контента; ast_hash не меняется от перестановки полей; merkle_hash меняется при изменении зависимости | 5.1 |
| `tests/unit/test_builder.py` | Построение графа из ParseResult: узлы и рёбра создаются, старые удаляются | 4.2 |
| `tests/unit/test_cascade.py` | Изменение файла → dirty-флаг на импортёрах; инвалидация option_values | 5.4 |

#### 11.6. Интеграционные тесты (фаза 10, ~10 тестов)

| Тест | Сценарий | Проверка |
|------|----------|----------|
| `test_full_cycle` | Трогаем `modules/test.nix` в тестовом репо → ждём обновления → observe видит изменения | Полный pipeline |
| `test_incremental_skip` | Трогаем файл без изменений → update_engine пропускает (content_hash совпал) | Инкрементальность |
| `test_cascade_imports` | Меняем `base.nix` → dirty у всех импортёров | Merkle-tree каскад |
| `test_blast_radius` | Запрашиваем blast_radius файла → список всех импортёров | Reverse BFS |
| `test_path` | path от keybinding к nix_option → кратчайший путь | Dijkstra |
| `test_introspect_option_offline` | introspect_option без nix → static metadata | Статический режим |
| `test_introspect_option_online` | introspect_option с include_value=True → nix eval → cached значение | Динамический режим |
| `test_eval_cache_invalidation` | nix flake update → все option_values stale | Инвалидация |
| `test_status` | status() → режим, размер графа, generation_id | Статус |
| `test_mcp_all_tools` | MCP-клиент вызывает все 11 инструментов → валидные ответы с generation_id | MCP Server |

#### 11.7. Тестовые фикстуры

**Минимальный тестовый flake-репозиторий (`tests/fixtures/minimal-flake/`):**

```
minimal-flake/
├── flake.nix          # базовый flake с одним модулем
├── flake.lock         # залоченные зависимости (минимальный)
├── modules/
│   ├── default.nix    # imports = [ ./services/nginx.nix ]
│   └── services/
│       └── nginx.nix  # options.services.nginx.enable = mkOption ...
└── expected_graph.json  # ожидаемый граф после полного скана
```

Используется в интеграционных тестах как `--root`. Содержит достаточно Nix-кода, чтобы проверить: imports, options, config, mkIf, home.file, home.packages.

#### 11.8. Нагрузочное тестирование

- Синтетический граф 10K+ узлов (генерируется скриптом `tests/gen_large_graph.py`)
- Замеры: `observe` (< 5ms), `hop` (< 50ms), `blast_radius` depth=5 (< 200ms)
- Замер: обновление одного файла в графе 10K узлов (< 500ms)

### 12. Оптимизация (Неделя 12)
- WAL + индексы в SQLite
- LRU-кэш профилирование
- Бенчмарки: observe, hop, blast_radius на графе 10K узлов
- Документация: README, примеры запросов для агентов

---

## Сводка зависимостей фаз

```
0.1 (pyproject)
 │
 ├─► 1.1 (модели) ──► 1.3 (db.py) ──► 1.4 (nx_graph)
 │                                      │
 ├─► 1.2 (config) ─────────────────────┤
 │                                      │
 └─► 1.5 (CLI)                          │
                                        │
    2.1 (лексер) ──► 2.2 (парсер) ──► 2.3 (fallback)
                                        │
    3.1 (ast_extract) ◄─────────────────┘
     │
     └─► 3.2 (module_parser) ──► 3.3 (NixParser)
                                      │
    4.1 (registry) ◄──────────────────┘
     │
     └─► 4.2 (builder) ◄─── 1.3, 1.4
                                      │
    5.1 (hash) ───────────────────────┤
    5.2 (event_router) ───────────────┤
    5.3 (update_engine) ◄─────────────┘
     │
     ├─► 5.4 (cascade)
     ├─► 5.5 (diff) [отчёт]
     └─► 5.6 (watcher)
                                      │
    6.1 (query глаголы) ◄─── 1.3, 1.4 │
    6.2 (introspection) ◄─────────────┘
    6.3 (status/refresh)
                                      │
    7.1 (MCP server) ◄─── 6.1, 6.2, 6.3
                                      │
    8.1 (eval.py) ──► 8.2 (eval_cache) ──► 8.3 (flake_tracker)
    8.4 (package_index)
                                      │
    9 (home-manager) ◄─── 3.2, 4.2
                                      │
    10 (git hooks) ──► 11 (тесты) ──► 12 (оптимизация)
```

---

*Статус: готов к реализации. Ждёт утверждения.*