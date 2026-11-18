# План: автообновление навигатора и отключение graft в `.dotfiles`

Дата: 2026-11-17  
Статус: в работе (навигатор готов, dotfiles впереди)  
Рабочая директория агента: `/home/max/projects/repo-navigator`  
Целевой репозиторий: `/home/max/.dotfiles`

## 1. Зачем это делается

Замер adoption за 2026-11-07 .. 2026-11-16:

- 23 сессии, 3830 tool calls;
- `nix-repo-navigator`: 2 вызова (0.05%), оба `impact`, оба с ошибкой;
- успешных органических вызовов: 0;
- fallback после ошибок: 2/2 (100%).

Оба вызова были в сессии «happ новый - настройка, создание пакета»
(2026-11-16, 19:58). Агент угадал несуществующие пути
`modules/nixos/networking/flclashx-helper.nix` и
`.../happ-legacy.nix`; реальный файл — `.../happ.nix`. Ошибка
`node not found` ничего не подсказала, и агент ушёл в `bash`/`read`.

Дополнительно граф был устаревшим:

- generation 19;
- `modules/nixos/networking/happ.nix` не проиндексирован (создан
  2026-11-16 18:58, после последнего refresh);
- `ansible/roles/vpn-server/files/xui_api.py` числится в графе, но
  отсутствует на диске;
- health: `REGRESSED` (unindexed 1, missing 1).

Вывод: проблема не в том, что граф плохой, а в том, что его никто не
обновлял, а ошибки не помогали агенту восстановиться. Поэтому даём
навигатору один ограниченный шанс: автоматическое обновление графа и
понятные ошибки. Graft в `.dotfiles` для Nix-файлов бесполезен — его
проектная интеграция отключается.

## 2. Принятые решения

1. **Graft отключаем только в проекте `.dotfiles`.**
   Пакет `graft`, модуль `modules/home/ai-agents/graft` и его
   обновления остаются: в других проектах он может быть полезен.
2. **Граф обновляет один фоновый процесс (systemd user service).**
   MCP-серверы только читают SQLite. Несколько сессий opencode не
   конкурируют за запись.
3. **MCP остаётся с шестью инструментами:** `find`, `module`,
   `impact`, `option`, `neighbors`, `status`. Новых инструментов не
   добавляем.
4. **Пробный период:** 5 дней или 3 реальные Nix-задачи. Если снова
   0 успешных органических использований — убрать MCP, оставить CLI и
   граф.

## 3. Архитектура автообновления

```
systemd user service
  └─ nix-repo-navigator watch --root /home/max/.dotfiles ...
       ├─ startup catch-up: index_repo (полный проход)
       ├─ watchdog / polling → EventRouter (debounce 500 ms)
       └─ UpdateEngine.process_file / process_deleted_file
            └─ generation_id++

MCP server (stdio, одна или несколько сессий opencode)
  └─ читает SQLite
       └─ QueryEngine._check_generation() перезагружает граф
```

Почему так:

- один писатель → нет `database is locked` и двойной работы;
- граф обновляется, даже когда opencode закрыт;
- все сессии видят один и тот же свежий граф;
- тяжёлый полный проход выполняется один раз при старте службы, а не в
  каждой сессии.

`watch` при старте должен **всегда** делать catch-up `index_repo`, а не
только при пустой БД. Это подхватывает новые, изменённые и удалённые
файлы, появившиеся пока служба не работала. После catch-up запускается
обычный цикл watcher.

Порядок в `_run_watch`:

1. открыть БД и загрузить существующий граф;
2. создать `UpdateEngine` и `EventRouter`;
3. запустить watcher (события за время catch-up попадут в очередь);
4. выполнить `index_repo(root, db, nx_graph, config=cfg)`;
5. выводить статистику catch-up в journal;
6. обрабатывать очередь событий до остановки.

Не добавлять запись из MCP: MCP — только читатель. Если файл новый,
служба проиндексирует его в течение debounce (обычно < 1 с).

## 4. Изменения в `/home/max/projects/repo-navigator`

### 4.1 `src/repo_navigator/cli.py`

- Переписать `_run_watch` по порядку из раздела 3.
- Убрать условие `if db.count_nodes() == 0` вокруг начального индекса.
- Добавить в лог/вывод режим watcher, число файлов/узлов и время
  catch-up.
- При необходимости вынести цикл обработки очереди в небольшую
  функцию, чтобы её можно было тестировать.

### 4.2 `src/repo_navigator/mcp_server.py`

- Убрать из `INSTRUCTIONS` и docstring `find` рекомендацию использовать
  graft. Заменить на: общий where-is/поиск кода — `rg`/`read`; этот
  сервер — Nix-граф, provenance и impact.
- Для `impact` и `module` при `KeyError` (и только при нём) возвращать
  `ToolError` с подсказкой:
  - generation;
  - похожие узлы из `engine.find_symbol(...)` (fuzzy, limit 5);
  - пример: `happ-legacy.nix` → `nix:modules/nixos/networking/happ.nix`;
  - «сначала `find` с коротким токеном»;
  - «если файл новый, проверь
    `systemctl --user status nix-repo-navigator-watch` или запусти
    `nix-repo-navigator refresh ...`».
- `ValueError` и остальные ошибки не маскировать.
- Не индексировать файлы из MCP: это нарушит схему «один писатель».

### 4.3 Тесты

- `tests/unit/test_mcp_cli.py`:
  - обновить/добавить тест: граф содержит `nix:happ.nix`, вызов
    `impact` с `happ-legacy.nix` возвращает `ToolError`, в тексте есть
    `happ.nix` и `find`;
  - существующий `test_mcp_error_impact_missing` не должен ломаться.
- `tests/unit/test_watcher.py` или новый тест:
  - проверить catch-up: временный репозиторий с `.nix`-файлом, вызов
    функции начального индекса создаёт узел в БД;
  - если выносится helper, тестировать его напрямую.
- Обновить `docs/query-verbs.md` и `README.md`, если там остаются
  утверждения «используй graft» (по смыслу заменить на `rg`/`read`).

### 4.4 Git

Перед коммитом проверить `git status`. В репозитории уже есть
пользовательские staged-файлы:

- `M .gitignore`
- `A .mcp.json`
- `A opencode.json`

**Их не добавлять и не коммитить.** Коммитить только исходники, тесты и
этот документ. Сообщение коммита, например:

```
feat: auto-refresh graph and actionable not-found hints
```

После тестов — push в `github` (это разрешено для навигатора).

## 5. Изменения в `/home/max/.dotfiles`

### 5.1 systemd user service

Файл: `modules/home/ai-agents/nix-repo-navigator/default.nix`.

Добавить в аргументы модуля `variables` и сервис:

```nix
systemd.user.services.nix-repo-navigator-watch = {
  Unit.Description = "nix-repo-navigator graph watcher";
  Service = {
    ExecStart = "${nix-repo-navigator.packages.${pkgs.stdenv.hostPlatform.system}.default}/bin/nix-repo-navigator watch --root ${variables.basePathFilesDir} --plugins python --parse-unreferenced --exclude 11";
    Environment = [ "PYTHONPATH=" ];
    Restart = "on-failure";
    RestartSec = "3s";
  };
  Install.WantedBy = [ "default.target" ];
};
```

Проверить, что `variables.basePathFilesDir` действительно указывает на
`/home/max/.dotfiles`. Если нет — использовать явный путь
`${config.home.homeDirectory}/.dotfiles`.

### 5.2 `opencode.json`

Файл: `modules/home/ai-agents/opencode/project/opencode.json`
(корневой `/home/max/.dotfiles/opencode.json` — симлинк на него).

- Удалить MCP-серверы `graft` и `graft-navigator`.
- Оставить `dotfiles-helper`, `rime`, `nix-repo-navigator`.
- `nix-repo-navigator` запускать **без** `--watch`: обновлением
  занимается служба.

### 5.3 `AGENTS.md`

- Удалить весь блок `<!-- graft:start --> ... <!-- graft:end -->`.
- В строке про роли убрать «graft — произвольный код»; написать, что
  graft в этом репозитории отключён, where-is — `rg`/`read`.
- В разделе Repo Navigator:
  - «Граф обновляется автоматически службой
    `nix-repo-navigator-watch`; MCP читает БД и перезагружает generation».
  - «Не угадывать путь: сначала `find`; `impact` принимает id или путь
    из результата `find`».
  - Ручной refresh — только если служба недоступна.
- Убедиться, что в `AGENTS.md` не осталось советов запускать `graft`.

### 5.4 Graft-артефакты проекта

- `git rm graft/INDEX.md graft/manifest.json`.
- Удалить локальную папку `graft/` целиком (там ещё `.cache`,
  `.graph` и сгенерированные markdown-файлы).
- Удалить из `.gitignore` оба блока с комментарием про graft (они
  дублируются).
- Не трогать `modules/home/ai-agents/graft`, `graft.enable` и
  `modules/home/ai-agents/updates`: пакет остаётся для других проектов.

### 5.5 Деплой

```sh
cd /home/max/.dotfiles
nix flake update nix-repo-navigator
sudo nixos-rebuild switch --flake .#nixos
systemctl --user daemon-reload
systemctl --user enable --now nix-repo-navigator-watch
```

Если `enable --now` не сработал после `nrs` — выполнить
`systemctl --user start nix-repo-navigator-watch` и проверить
`systemctl --user status`.

## 6. Проверка

### 6.1 Служба и граф

```sh
systemctl --user status nix-repo-navigator-watch --no-pager
journalctl --user -u nix-repo-navigator-watch -n 50 --no-pager
nix-repo-navigator status --root /home/max/.dotfiles
python /home/max/projects/repo-navigator/scripts/mcp_stats.py health \
  --root /home/max/.dotfiles --exclude 11
```

Ожидается:

- служба `active (running)`;
- `happ.nix` проиндексирован;
- missing `xui_api.py` удалён из графа;
- unindexed 0, missing 0, parse_errors 0;
- generation увеличился после catch-up.

### 6.2 MCP и graft

- Перезапустить opencode (старые процессы MCP и graft не обновятся
  сами).
- В списке инструментов должны быть только шесть
  `nix-repo-navigator_*`; `graft` и `graft-navigator` отсутствуют.
- `graft` CLI при этом остаётся доступен для других проектов:
  `graft --version`.

### 6.3 Функциональные проверки

Через MCP/openode:

- `find("happ")` находит
  `nix:modules/nixos/networking/happ.nix`;
- `impact("modules/nixos/networking/happ.nix")` возвращает отчёт без
  `node not found`;
- `impact("modules/nixos/networking/happ-legacy.nix")` возвращает
  ошибку с подсказкой и кандидатом `happ.nix`.

Проверка автообновления:

1. Создать временный
   `/home/max/.dotfiles/modules/nixos/networking/_watch-test.nix`
   с минимальным Nix-выражением.
2. Подождать 1–2 секунды.
3. `find("_watch-test")` должен найти файл без ручного refresh.
4. Удалить файл.
5. Подождать 1–2 секунды; `status`/`find` не должны показывать узел.

## 7. Замер после деплоя

Дата старта — день после проверки, чтобы не считать проверочные вызовы.
Через 5 дней:

```sh
cd /home/max/projects/repo-navigator
python scripts/mcp_stats.py usage --since <дата-старта>
```

Критерий успеха:

- хотя бы одно успешное органическое использование, которое дало
  агенту новую информацию (impact перед правкой, provenance опции,
  импортёры модуля);
- нет `node not found` на существующих файлах;
- fallback после вызовов навигатора не 100%;
- служба жива, health без регрессий.

Если снова 0 успешных использований — убрать MCP из `opencode.json`,
оставить CLI, граф и службу (или отключить службу).

## 8. Ограничения и подводные камни

- **Не делать watcher внутри каждого MCP.** Несколько сессий будут
  писать в одну SQLite; возможны блокировки и дублирование.
- **Не индексировать из MCP.** Единственный писатель — служба.
- Полный catch-up при старте службы занимает несколько секунд
  (для `.dotfiles` ~4.5 с). Это нормально.
- Если служба упала, граф стареет. `Restart=on-failure` возвращает её;
  ошибки MCP должны подсказывать проверить статус службы.
- Direnv в `.dotfiles` добавляет старый `PYTHONPATH`. Для тестов и
  MCP использовать `env -u PYTHONPATH`.
- В навигаторе есть 25 старых ошибок ruff. Новых быть не должно.
- Не коммитить staged-файлы пользователя в repo-navigator
  (`.gitignore`, `.mcp.json`, `opencode.json`).
- Для dotfiles действует правило: показать анализ, сообщение коммита и
  команду, затем получить явное согласие пользователя. Push dotfiles —
  пользователь.

## 9. Чек-лист для агента

- [ ] Прочитать этот документ и `AGENTS.md` в `.dotfiles`.
- [ ] Реализовать catch-up в `watch` и подсказки в MCP.
- [ ] Добавить/обновить тесты; `env -u PYTHONPATH .venv/bin/python -m pytest tests/ -q`.
- [ ] Проверить ruff (новых ошибок нет).
- [ ] Закоммитить и запушить навигатор (без пользовательских staged-файлов).
- [ ] Добавить systemd-службу, убрать graft из проекта, обновить AGENTS.
- [ ] `nix flake update nix-repo-navigator` и `nrs`.
- [ ] Запустить службу, проверить health и MCP-инструменты.
- [ ] Проверить `find`/`impact` на `happ.nix` и подсказку на `happ-legacy.nix`.
- [ ] Проверить автообновление временным файлом.
- [ ] Зафиксировать дату старта замера.
