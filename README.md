# Personal Intelligence Kit

A [copier](https://copier.readthedocs.io) template that scaffolds a **personal intelligence system** for [Claude Code](https://claude.com/claude-code).

Your own back-office — ingesting email, browser, journal, tasks, and notes, then surfacing patterns and writing coaching across them. No central app. No cloud. Claude Code is the runtime; `vault.toml` is the config; skills do the work.

## What You Get

A generated vault with:

- **`CLAUDE.md`** — opinionated system prompt that turns Claude Code into a personal intelligence agent
- **`vault.toml`** — runtime config (data sources, paths)
- **`.claude/skills/`** — a curated set of skills that read `vault.toml`:
  - `cross-source-queries` — intention↔reality gaps, commitment accountability, topic convergence
  - `weekly-reflection` — qualitative narrative from all sources
  - `data-sync` — parallel sync orchestrator
  - `draft-coach`, `stale-drafts` — writing coaching (if you have a notes vault)
  - `email-ingest`, `email-analyzer` — mbsync + notmuch wrapper (if you enable email)
  - `browser-ingest`, `session-analyzer` — Chromium history & tab health (if you enable browser)
  - `journal-ingest` — Rosebud / Day One / markdown journals (if you enable journaling)
  - `tasks-import` — Todoist / Microsoft To-Do / Things 3 (if you enable tasks)
- **`config/`** — launchd / cron templates for scheduled syncs
- **`.mcp.json`** — MCP server config (e.g. Clay.earth for contacts)

Everything is **read-only against your real data**. Outputs go to the vault's `output/` directory.

## Prerequisites

- **[copier](https://copier.readthedocs.io):** `uv tool install copier` or `pipx install copier`
- **[uv](https://docs.astral.sh/uv/):** for the Python-based skills (`brew install uv`)
- **[Claude Code](https://claude.com/claude-code):** the runtime
- **Optional per source:** `mbsync` + `notmuch` for email, Full Disk Access for browser history, etc.

## Quickstart

```bash
copier copy gh:simonpodhajsky/personal-intelligence-kit ~/my-vault
cd ~/my-vault
claude
```

Then ask: `"What data sources do I have?"` — Claude will read `vault.toml` and answer.

## Philosophy

- **Infrastructure, not a chatbot.** This vault is the back-office of your brain. It doesn't chat; it indexes, correlates, and surfaces.
- **Narrow write zone.** The vault never writes to your notes, email, or any primary data. All output goes to `output/`.
- **Claude Code is the runtime.** No Python CLI to install, no FastAPI server, no cron daemon. Just skills that Claude runs.
- **Opinionated defaults.** The generated `CLAUDE.md` is long and specific on purpose. Delete what you don't like — it's your vault.
- **Skills read `vault.toml`.** You can edit it post-generation to enable/disable sources. Skills skip missing sources gracefully.

## What This Is Not

- A note-taking app (you already have one — this vault references it)
- A task manager (ditto)
- An autonomous agent (it runs when you invoke it, not in the background beyond scheduled syncs)
- A SaaS product (it's yours, on your machine)

## Customizing After Generation

The template is a starting point. After `copier copy`:

- **Add skills:** drop new directories into `.claude/skills/`
- **Change sources:** edit `vault.toml`, re-run skills
- **Tweak the brain:** edit `CLAUDE.md` freely
- **Re-run the template:** `copier update` in the generated vault to pull in template changes

## Verification After Generation

```bash
cd ~/my-vault
# 1. Structure
tree -L 2

# 2. Ask Claude
claude "Read vault.toml and list my enabled data sources."

# 3. First sync (once credentials are set up)
uv run .claude/skills/data-sync/sync_all.py --yes
```

## License

MIT — see LICENSE.

## Credits

Extracted from [Claude-Vault](https://github.com/simonpodhajsky/Claude-Vault), a personal intelligence system by [Šimon Podhajský](https://simon.podhajsky.net).
