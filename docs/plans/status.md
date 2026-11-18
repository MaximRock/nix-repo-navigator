# Статус: автообновление навигатора и отключение graft

Источник: `docs/plans/2026-11-17-navigator-autorefresh.md`
Дата старта: 2026-11-17
Легенда: `[ ]` — не сделано, `[x]` — готово

## 0. Подготовка

- [x] План согласован
- [x] Решения зафиксированы: systemd-служба; graft отключаем только в `.dotfiles`
- [ ] Прочитан план и `AGENTS.md` в `.dotfiles`
- [ ] Проверено текущее состояние: git, generation, health, служба

## 1. Навигатор: автообновление графа

- [x] `_run_watch`: всегда выполнять catch-up `index_repo`
- [x] Порядок: watcher → catch-up → обработка очереди
- [x] Логировать режим watcher и статистику catch-up
- [x] Тест catch-up на временном репозитории
- [x] Проверить, что generation увеличивается после catch-up

## 2. Навигатор: понятные ошибки

- [x] Убрать рекомендацию graft из `INSTRUCTIONS` и `find`
- [x] `impact`: при `node not found` предлагать похожие узлы
- [x] `module`: при `node not found` предлагать похожие узлы
- [x] В ошибке: generation, `find`, статус службы, ручной refresh
- [x] Не индексировать файлы из MCP (один писатель — служба)
- [x] Тест: `happ-legacy.nix` → подсказка `happ.nix`

## 3. Навигатор: качество

- [x] `env -u PYTHONPATH .venv/bin/python -m pytest tests/ -q`
- [x] Системный `ruff`: новых ошибок нет (25 старых не трогать)
- [x] Обновить `README.md` / `docs/query-verbs.md`, если там есть graft
- [x] Проверить `git status`: не добавлены `.gitignore`, `.mcp.json`, `opencode.json`

## 4. Навигатор: коммит

- [ ] Показать diff и сообщение коммита
- [ ] Закоммитить только исходники, тесты и docs
- [ ] Push в `github`
- [ ] Зафиксировать commit hash

## 5. Dotfiles: systemd-служба

- [ ] Добавить `variables` в модуль `nix-repo-navigator`
- [ ] Добавить `systemd.user.services.nix-repo-navigator-watch`
- [ ] `Restart=on-failure`, `RestartSec=3s`, `WantedBy=default.target`
- [ ] `Environment = [ "PYTHONPATH=" ]`
- [ ] Проверить путь `variables.basePathFilesDir`

## 6. Dotfiles: отключение graft

- [x] Удалить MCP `graft` из `opencode.json`
- [x] Удалить MCP `graft-navigator` из `opencode.json`
- [x] Оставить `nix-repo-navigator` без `--watch`
- [x] Удалить graft-блок из `AGENTS.md`
- [x] Обновить упоминания graft на `rg`/`read`
- [x] `git rm graft/INDEX.md graft/manifest.json`
- [x] Удалить локальную папку `graft/`
- [x] Убрать graft-строки из `.gitignore`
- [x] Не трогать пакет graft и `graft.enable`

Проверка после перезапуска opencode: в tools не должно быть
`graft`/`graft-navigator`; `graft --version` должен работать.

## 7. Dotfiles: документация

- [ ] Добавить в `AGENTS.md` правило «сначала `find`, потом `impact`»
- [ ] Описать auto-refresh через `nix-repo-navigator-watch`
- [ ] Описать ручной refresh только как fallback

## 8. Деплой

- [ ] `nix flake update nix-repo-navigator`
- [ ] `sudo nixos-rebuild switch --flake .#nixos`
- [ ] `systemctl --user daemon-reload`
- [ ] `systemctl --user enable --now nix-repo-navigator-watch`
- [ ] Перезапустить opencode
- [ ] Проверить, что старые graft/navigator процессы не остались

## 9. Проверка службы и графа

- [ ] `systemctl --user status` — active (running)
- [ ] `journalctl --user` — нет ошибок
- [ ] `nix-repo-navigator status --root /home/max/.dotfiles`
- [ ] `health`: unindexed 0, missing 0, parse_errors 0
- [ ] `happ.nix` проиндексирован
- [ ] `xui_api.py` удалён из графа

## 10. Проверка MCP и graft

- [ ] В opencode ровно 6 инструментов `nix-repo-navigator_*`
- [ ] `graft` и `graft-navigator` отсутствуют
- [ ] `graft --version` работает для других проектов
- [ ] `find("happ")` находит `happ.nix`
- [ ] `impact("modules/nixos/networking/happ.nix")` работает
- [ ] `impact(".../happ-legacy.nix")` предлагает `happ.nix`

## 11. Тестовая директория

- [ ] Создать `/home/max/.dotfiles/navigator-test/`
- [ ] Добавить `navigator-test/sample.nix`
- [ ] Дождаться индексации; `find("sample")` находит файл
- [ ] Изменить файл; `module(...)` видит изменения
- [ ] Переименовать файл; старый узел исчез
- [ ] Удалить файл; узел исчез
- [ ] `impact("navigator-test/alpha-legacy.nix")` предлагает `alpha.nix`
- [ ] Удалить `navigator-test/`
- [ ] `health` без unindexed/missing
- [ ] `git status` чистый

## 12. Замер

- [ ] Зафиксировать дату старта (день после проверки)
- [ ] Через 5 дней: `mcp_stats.py usage --since <дата>`
- [ ] Проверить: ≥1 успешное органическое использование
- [ ] Проверить: нет `node not found` на существующих файлах
- [ ] Проверить: fallback не 100%
- [ ] Принять решение: оставить MCP или убрать, оставив CLI

## Блокеры и заметки

- [x] Блокеров нет
- [x] `.gitignore`, `.mcp.json`, `opencode.json` остаются пользовательскими staged-файлами; не изменялись
