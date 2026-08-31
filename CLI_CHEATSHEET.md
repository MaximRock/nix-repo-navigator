# nix-repo-navigator — CLI Cheatsheet

## Индексация

```bash
# Полная индексация репозитория
nix-repo-navigator index .
nix-repo-navigator refresh .

# Индексация одного файла
nix-repo-navigator index ./modules/home/git/default.nix

# Watch режим (фоновое отслеживание изменений)
nix-repo-navigator watch .
```

## Статус

```bash
nix-repo-navigator status
```

## Навигация по графу

```bash
# Наблюдение — соседи узла
nix-repo-navigator query observe nix:flake.nix
nix-repo-navigator query observe nix:modules/home/git/default.nix --depth 1

# Поиск — найти узлы по имени
nix-repo-navigator query find "git"
nix-repo-navigator query find "nginx" --fuzzy
nix-repo-navigator query find "enable" --lang nix

# BFS-обход — пройти по рёбрам imports
nix-repo-navigator query hop nix:a.nix --relation imports --depth 2 --width 10

# Кратчайший путь между двумя узлами
nix-repo-navigator query path nix:a.nix nix:d.nix
```

## Интроспекция опций

```bash
# Статическая информация об опции (тип, дефолт, описание)
nix-repo-navigator query option services.nginx.enable

# С ленивым nix eval (вычисленное значение)
nix-repo-navigator query option services.nginx.enable --eval

# Произвольное Nix выражение
nix-repo-navigator query eval "1+1"
nix-repo-navigator query eval "builtins.toString 42" --timeout 10
```

## Анализ влияния

```bash
# Кто зависит от этого модуля (обратные imports)
nix-repo-navigator query blast nix:modules/base.nix

# Что сломается при изменении (модули + опции + файлы + оценка риска)
nix-repo-navigator query impact nix:modules/base.nix
```

## Сводка модуля

```bash
nix-repo-navigator query summarize modules/home/git/default.nix
```

## Flake и пакеты

```bash
# Список flake input'ов из flake.lock
nix-repo-navigator query flake-inputs

# Список пакетов (mock, без nix eval)
nix-repo-navigator query packages
nix-repo-navigator query packages --query ripgrep

# Информация о пакете
nix-repo-navigator query package pkgs.ripgrep
```

## MCP сервер (для AI-агентов)

```bash
# Запуск MCP-сервера (stdio транспорт)
nix-repo-navigator start --root ~/.dotfiles

# С указанием БД
nix-repo-navigator start --root ~/.dotfiles --db-path ./repo.db

# Через python напрямую
python -m repo_navigator.mcp_server --root ~/.dotfiles
```

## Dev-команды (отладка парсера)

```bash
# Токенизация .nix файла
nix-repo-navigator dev lex ~/.dotfiles/flake.nix

# AST-дерево
nix-repo-navigator dev parse ~/.dotfiles/flake.nix

# Извлечение imports/options/config
nix-repo-navigator dev extract ~/.dotfiles/modules/home/git/default.nix

# Полная индексация одного файла
nix-repo-navigator dev index ~/.dotfiles/flake.nix

# Watch в dev-режиме
nix-repo-navigator dev watch ~/.dotfiles
```

## Nix run (без установки)

```bash
nix run github:MaximRock/nix-repo-navigator -- status
nix run github:MaximRock/nix-repo-navigator -- index ~/.dotfiles
nix run github:MaximRock/nix-repo-navigator -- start --root ~/.dotfiles
```
