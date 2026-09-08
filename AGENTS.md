# AGENTS.md

Инструкции для AI-агентов и разработчиков, работающих в этом репозитории.

## Миссия продукта

**repo-navigator** — MCP-компаньон AI-агента, работающего в **NixOS/home-manager репозиториях**.

Приложение индексирует репозиторий в типизированный граф знаний (модули, опции, файлы, flake-входы) и отдаёт его агенту через MCP. Основная ценность: агент получает **точный, компактный контекст** и оценку влияния изменений **без повторного перечитывания исходников** — это экономит токены модели и ускоряет итерации.

Это **не** универсальный индексатор кода. Это помощник, который делает агента эффективным **внутри Nix-конфигураций**.

## Ключевые принципы

1. **Nix в корне.** Репозиторий всегда рассматривается как Nix-конфигурация. `.nix` (tier 0) парсится всегда. Остальные языки (Python, KDL, Shell, Lua, TOML…) — это **плагины** (tier 1+), которые участвуют постольку, поскольку связаны с Nix-конфигурацией или явно запрошены.
2. **Релевантность вместо объёма.** Tier 1-3 файлы парсятся, если: лежат в `.config/`, упомянуты в графе рёбрами `configures`/`generates`, либо флаг `Config.parse_unreferenced=True` (осознанная универсальность для питон-проектов с nix-flake), либо файл передан явно в однофайловом режиме.
3. **Агент = оркестратор, приложение = точный граф.** Агент формулирует ответ на естественном языке, опираясь на структурные ответы инструментов (`dependencies`, `dependents`, `impact`, `observe`…). **Не встраивать LLM внутрь MCP-сервера** — агент уже модель.
4. **Измеряемая польза.** Приложение считает и сообщает агенту, сколько запросов обслужено и сколько токенов сэкономлено (через `repo_navigator_report`). Экономия — оценка: «байты исходников, не перечитанных агентом, ÷ 4».
5. **Стабильность модели.** id нод стабильны по схеме `lang:path:symbol` (или `тип:символ`). Каждая нода несёт `lang`, `type`, `metadata`. Это требование будущего визуализатора графа и фильтров поиска.

## Архитектура (3 слоя)

```
Layer 3: MCP Server (инструменты)  →  QueryEngine
Layer 2: Query Engine + Graph       →  SQLite (источник истины) + NetworkX (производный)
Layer 1: Parsers & Indexer          →  registry (Nix-first), hash/merkle, cascade, watch
```

- **Nix-парсер:** lexer → recursive-descent parser → `ast_extract` → `module_parser` (импорты/опции/конфиги/`home.file`/пакеты). Наружные файлы → `file:*` ноды + `configures`/`generates` рёбра.
- **Плагины языков:** `parsers/plugins/<lang>.py` + импорт в `parsers/plugins/__init__.py` + `@register_language(LanguageConfig(...))`. Включаются через `Config.plugins`.
- **Индексация:** content_hash (xxhash) → ast_hash → merkle_hash (по импортам) → каскадная пометка dirty. Bulk-индекс двтороходный: сначала Nix, затем tier 1-3 по наполненному графу.
- **Граф:** DB — истина; NxGraph — производный для обхода. LRU-кэш запросов инвалидируется по `generation_id`.

## Рабочие команды

```bash
.venv/bin/python -m pytest tests/ -q        # тесты (375+)
.venv/bin/python -m pytest tests/unit/test_python_parser.py -v
ruff check . && ruff format --check .       # линтер/формат (системный ruff)
```

- Не коммитить мусорные артефакты: `repo.db`, `test-dotfiles.db`, `.repo-navigator/`, `__pycache__`.
- Перед завершением задачи — прогнать `pytest tests/ -q` и `ruff check .`.
- Следить за парадигмой «Nix в корне»: новые языки добавляются как плагины, не меняя правила релевантности без явного запроса.

## Как добавить парсер (паттерн)

См. `docs/development.md` — «Adding a Parser Plugin». Шаблон:

```python
@register_language(LanguageConfig(name="myLang", extensions=[".my"], tier=1))
class MyParser(BaseParser):
    ...
    def parse(self, path, content) -> ParseResult: ...
```

Затем: импорт в `parsers/plugins/__init__.py`, тест по образцу `tests/unit/test_python_parser.py`, включение в `REPO_NAVIGATOR_PLUGINS`.

## Roadmap (заложено в архитектуру)

- **Языки:** `sh`, `lua`, `vim`, `toml`, `json`, `qtile` (типы нод/рёбер уже в `models/nodes.py`, `models/edges.py`).
- **Визуализатор графа** (отдельная фаза): тонкий веб-слой поверх экспорта графа в JSON (`lang` + стабильные id дают фильтр по языку бесплатно). Не зависит от MCP.
- **Фильтры поиска:** `find_symbol` поддерживает `lang`/`node_type`/`path_contains`/`id_prefix`/`offset` — общий набор для поиска и визуализатора.
- **Отчёт о выгоде:** `repo_navigator_report` (MCP) + `nix-repo-navigator report` (CLI).