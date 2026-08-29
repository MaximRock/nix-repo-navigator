# Команды для релиза 0.1.0

## Вариант 1: через gh CLI (автоматически)

```bash
cd ~/projects/repo-navigator
gh auth login  # если ещё не авторизован
gh repo create nix-repo-navigator --public --source=. --remote=origin --push
```

## Вариант 2: через веб (вручную)

1. Создать репозиторий на https://github.com/new
   - Owner: MaximRock
   - Repository name: nix-repo-navigator
   - Public, без инициализации
2. В терминале:

```bash
cd ~/projects/repo-navigator
git remote add origin git@github.com:MaximRock/nix-repo-navigator.git
git push -u origin main
```

## После пуша — тег и релиз

```bash
git tag v0.1.0
git push origin v0.1.0
gh release create v0.1.0 dist/* --notes-file CHANGELOG.md
```