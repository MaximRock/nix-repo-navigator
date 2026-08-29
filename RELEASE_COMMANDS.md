# Команды для релиза 0.1.0

```bash
# 1. GitHub репозиторий
cd ~/projects/repo-navigator
gh repo create nix-repo-navigator --public --source=. --remote=origin --push

# 2. Собрать PyPI пакет
.venv/bin/hatch build

# 3. Проверить пакет
.venv/bin/twine check dist/*

# 4. Опубликовать на PyPI (нужен токен)
# .venv/bin/hatch publish

# 5. Git tag
git tag v0.1.0
git push origin v0.1.0

# 6. GitHub Release
gh release create v0.1.0 dist/* --notes-file CHANGELOG.md
```