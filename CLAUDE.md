# CLAUDE.md — personal-intelligence-kit (template repo)

This is a **copier template** repo. Generated vaults live elsewhere; this repo contains the template source.

## Structure

- `copier.yml` — template questions and config
- `template/` — all files that get rendered into a generated vault (`.jinja` suffix = templated)
- `template/.claude/skills/` — skill implementations (Python scripts + SKILL.md docs)
- `template/.claude/skills/_lib/` — shared helpers (`vault_config.py`, `SCHEMAS.md`)

## Versioning

Versions are git tags consumed by `copier update --vcs-ref=<tag>`. Use annotated tags with a summary and the included commits:

```bash
git tag -a v0.X.Y -m "$(echo 'v0.X.Y — Summary of changes'; echo; git log --oneline $(git describe --tags --abbrev=0)..HEAD)"
```

## Conventions

- Skill scripts use inline script metadata (`# /// script`) for dependencies — no top-level pyproject.toml
- Skills import shared config via `vault_config.py` (paths, source toggles, etc.)
- All ingest skills write to normalized SQLite databases in `data/` per the contracts in `_lib/SCHEMAS.md`
- Jinja templates (`.jinja` suffix) are rendered at generation time; plain files are copied as-is
- `copier.yml` `_skip_if_exists` protects user data directories and `.env` from overwrites on update
